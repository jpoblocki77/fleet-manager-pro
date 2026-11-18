"""Live telemetry hub: local sampler (DLL shared memory or built-in simulator),
remote agents, and the job tracker that turns frames into logbook entries."""
import json
import logging
import math
import threading
import time
import urllib.request

from .. import compliance, db, economy, tracelog
from . import fluids, reader

jlog = logging.getLogger("fmpro.jobs")
from .demo import Simulator

EVENT_KINDS = {  # flag -> (kind, amount key, ledger category, detail builder)
    "fined": ("fine", "fine_amount", "fines", lambda v: v["fine_offence"] or "offence"),
    "tollgate": ("toll", "toll_amount", "tolls", lambda v: "Toll gate"),
    "ferry": ("ferry", "ferry_amount", "ferry", lambda v: f"{v['ferry_source_name']} → {v['ferry_target_name']}"),
    "train": ("train", "train_amount", "train", lambda v: f"{v['train_source_name']} → {v['train_target_name']}"),
    "refuel_paid": ("refuel", "refuel_amount", "fuel", lambda v: f"{v['refuel_amount']:.0f} L"),
}
JOB_COL = {"fine": "fines", "toll": "tolls", "ferry": "ferry", "train": "train"}


def delivered_sig(v):
    """Fingerprint of the last delivery. The plugin leaves its event flags set after an event, so a
    delivery is recognised by its data (start/finish game time, revenue), not by the flag edge."""
    return (bool(v["job_delivered"]), v["job_starting_time"], v["job_finished_time"],
            v["job_delivered_revenue"], v["job_delivered_earned_xp"])


def cancelled_sig(v):
    return (bool(v["job_cancelled"]), v["job_starting_time"], v["job_finished_time"], v["job_cancelled_penalty"])


class JobTracker:
    """Per-driver state machine fed with decoded frames."""

    def __init__(self, user_id):
        self.user_id = user_id
        self.job_id = None
        self.prev = None
        self.sig = {}
        self.last_t = None
        self.last_fuel = None
        self.last_pt = None
        self.last_pt_t = 0
        self.off_job_since = None
        self.last_wear = None
        self.pending_refuel = None
        self.fuel_hist = []          # (time, litres) over the last 2 minutes, same truck only
        self.fuel_truck = None
        self.truck_id = None
        self.lock = threading.Lock()
        self.hos = compliance.HosTracker(user_id)
        self.remote = False          # frames come from a remote agent (another PC)
        self._road = None            # last road point remembered for the map (x, z, time)
        self._road_today = 0         # road squares this driver added in the last 24 h (refreshed every 10 min)
        self._pickup = None          # (game, city, x, z) of the current job's pickup
        self.seen_idle = False       # saw the truck without a job since connecting (so a new job is seen from its start)
        self.jumps = 0               # position jumps without a ferry/train during the current job
        self._pos = None
        self.inspect_at = None       # fraction of the route where a planned roadside inspection happens

    # ------------------------------------------------------------ entry point
    def feed(self, frame):
        with self.lock:
            if frame is None:
                self.flush()
                self.prev, self.last_t = None, None
                return
            v, trailers = frame["values"], frame.get("trailers", [])
            self.remote = frame.get("source") == "agent"
            if not v["on_job"]:
                self.seen_idle = True
            self.demo = bool(frame.get("demo"))
            now = time.time()
            # Switched to the other game: that game's open job waits (it isn't abandoned); switching back resumes it.
            if self.job_id and v["game"] in ("ats", "ets2") and v["game"] != self._job_game():
                self.flush()
                self.job_id, self.off_job_since = None, None
                self._pos = None                 # the other game's map: positions aren't comparable
            first = self.prev is None
            if first:  # don't replay events that were already flagged before we connected
                for flag, (kind, amt, *_r) in EVENT_KINDS.items():
                    self.sig[kind] = (v[flag], v[amt])
                self.sig["delivered"] = delivered_sig(v)
                self.sig["cancelled"] = cancelled_sig(v)
                v = self._reconcile(v)
            if not v["paused"]:
                self._truck(v, now)
                self.hos.feed(v, self.truck_id, self.job_id)
                self._remember_road(v, now)
                if v["on_job"] and not self.job_id:
                    self._start(v, trailers, resume=True)
                if self.job_id and v["on_job"]:
                    self._track(v, now)
            truck = (v["truck_id"], v["license_plate"])
            if truck != self.fuel_truck:
                self.fuel_truck, self.fuel_hist = truck, []
            self.fuel_hist = [(t, f) for t, f in self.fuel_hist if now - t < 120] + [(now, v["fuel"])]
            if not first:
                self._events(v)
            self._confirm_refuel(v, now)
            self._finish(v, now)
            self.prev = v
            self.last_t = None if v["paused"] else now

    def _job_game(self):
        if getattr(self, "_jg", (None, None))[0] != self.job_id:
            self._jg = (self.job_id, db.val("SELECT COALESCE(game,'ats') FROM jobs WHERE id=?", (self.job_id,)))
        return self._jg[1]

    def _reconcile(self, v):
        """The job was delivered/cancelled while we weren't watching (app restarted, game closed on the
        results screen...): if the plugin's last result belongs to our open job, settle it now."""
        if v["on_job"] or not v["job_starting_time"]:
            return v
        row = db.one("SELECT id FROM jobs WHERE user_id=? AND status='in_progress' AND game_time_start=? "
                     "AND COALESCE(game,'ats')=? ORDER BY id DESC LIMIT 1", (self.user_id, v["job_starting_time"], v["game"]))
        if not row:
            return v
        self.job_id = row["id"]
        if not (v["job_delivered"] or v["job_cancelled"]) and (v["job_finished_time"] or 0) >= v["job_starting_time"]:
            # the event flag has already cleared (game paused on the results screen, flags reset...), but the
            # plugin still holds the job's result: a delivery has revenue, a cancellation a penalty
            if v["job_delivered_revenue"] > 0 and not v["job_cancelled_penalty"]:
                v = dict(v, job_delivered=True)
            elif v["job_cancelled_penalty"] > 0 and not v["job_delivered_revenue"]:
                v = dict(v, job_cancelled=True)
        if v["job_delivered"]:
            self.sig["delivered"] = None
        elif v["job_cancelled"]:
            self.sig["cancelled"] = None
        return v

    # ------------------------------------------------------------ pieces
    def _truck(self, v, now):
        if not v["truck_id"]:
            return
        plate = v["license_plate"]
        key = (v["truck_id"], plate)
        if key == getattr(self, "_truck_key", None) and self._truck_row:
            row = self._truck_row
        else:
            row = db.one("SELECT * FROM trucks WHERE game_truck_id=? AND plate=? AND status NOT IN ('sold','returned')", key)
        if not row and db.setting("auto_create_trucks") == "1":
            tid = db.insert("trucks", game_truck_id=v["truck_id"], brand=v["truck_brand"] or v["truck_brand_id"],
                            model=v["truck_name"] or v["truck_id"], plate=plate,
                            plate_country=v["license_plate_country"], odometer=v["odometer"],
                            fuel_capacity=v["fuel_capacity"], assigned_user_id=self.user_id, game=v["game"],
                            auto_detected=1, created_at=now, last_seen=now)
            row = db.one("SELECT * FROM trucks WHERE id=?", (tid,))
            jlog.info("driver #%s: new truck added to the fleet #%s %s %s (%s)", self.user_id, tid, v["truck_brand"],
                      v["truck_name"], plate)
            db.notify(self.user_id, f"New truck detected: {v['truck_brand']} {v['truck_name']} ({plate})", f"/fleet/truck/{tid}")
        if not row:
            return
        self._truck_key, self._truck_row = key, row
        self.truck_id = row["id"]
        wear = [v["wear_engine"], v["wear_transmission"], v["wear_cabin"], v["wear_chassis"], v["wear_wheels"]]
        if not hasattr(self, "_tw") or now - self._tw > 5:  # throttle DB writes
            self._tw = now
            db.update("trucks", row["id"], odometer=v["odometer"], wear_engine=wear[0], wear_transmission=wear[1],
                      wear_cabin=wear[2], wear_chassis=wear[3], wear_wheels=wear[4], last_seen=now)
        # in-game repair: wear drops noticeably
        if self.last_wear and sum(self.last_wear) - sum(wear) > 0.01:
            drop = sum(self.last_wear) - sum(wear)
            cost = round(drop * 100 * db.setting_f("repair_cost_per_pct", 40), 2)
            db.insert("maintenance", truck_id=row["id"], kind="repair", cost=cost, odometer=v["odometer"],
                      notes=f"In-game repair detected ({drop * 100:.1f}% wear removed)", user_id=self.user_id,
                      auto=1, created_at=now)
            if db.setting("auto_charge_repairs") == "1":
                economy.post("repairs", -cost, f"Repair {row['brand']} {row['model']} ({row['plate']})")
        self.last_wear = wear

    def _start(self, v, trailers, resume=False):
        now = time.time()
        if resume:
            row = db.one("SELECT * FROM jobs WHERE user_id=? AND status='in_progress' AND cargo_id=? "
                         "AND destination_city=? AND source_city=? AND (game_time_start=? OR ?=0) "
                         "AND COALESCE(game,'ats')=? ORDER BY id DESC LIMIT 1",
                         (self.user_id, v["cargo_id"], v["destination_city"], v["source_city"],
                          v["job_starting_time"], v["job_starting_time"], v["game"]))
            if row:
                self.job_id = row["id"]
                jlog.info("driver #%s: picked up open job #%s again (%s %s -> %s)", self.user_id, row["id"], v["cargo"],
                          v["source_city"], v["destination_city"])
                self.last_fuel = v["fuel"]
                self._pos = None                 # picked up again: don't measure a jump from where we were before
                return
        # an older unfinished job in THIS game is abandoned (a job open in the other game keeps waiting)
        db.ex("UPDATE jobs SET status='abandoned', finished_at=? WHERE user_id=? AND status='in_progress' "
              "AND COALESCE(game,'ats')=?", (now, self.user_id, v["game"]))
        trailer = next((t for t in trailers if t.get("attached")), None)
        contract = db.one("SELECT * FROM contracts WHERE status IN ('open','assigned') AND "
                          "(assigned_user_id IS NULL OR assigned_user_id=?) "
                          "AND (destination_city=? COLLATE NOCASE OR (COALESCE(destination_city_id,'')!='' AND destination_city_id=?)) "
                          "AND (source_city='' OR source_city=? COLLATE NOCASE "
                          "     OR (COALESCE(source_city_id,'')!='' AND source_city_id=?)) "
                          "AND (cargo='' OR cargo=? COLLATE NOCASE OR (cargo_id!='' AND cargo_id=?)) "
                          "AND game=? AND cargo_mass_ok(?, min_mass_t) "
                          "AND (COALESCE(source_company,'')='' OR source_company=? COLLATE NOCASE) "
                          "AND (COALESCE(destination_company,'')='' OR destination_company=? COLLATE NOCASE) "
                          "ORDER BY (COALESCE(source_company,'')!='') + (COALESCE(destination_company,'')!='') "
                          "+ (cargo!='') DESC, id LIMIT 1",
                          (self.user_id, v["destination_city"], v["destination_city_id"].split(".")[-1].lower(),
                           v["source_city"], v["source_city_id"].split(".")[-1].lower(), v["cargo"],
                           v["cargo_id"].split(".")[-1].lower(), v["game"], v["cargo_mass"],
                           v["source_company"], v["destination_company"])) \
            if _has_mass_fn() else None
        self.job_id = db.insert(
            "jobs", user_id=self.user_id, truck_id=self.truck_id, game=v["game"], source="telemetry",
            status="in_progress", source_city=v["source_city"], source_company=v["source_company"],
            destination_city=v["destination_city"], destination_company=v["destination_company"],
            cargo=v["cargo"], cargo_id=v["cargo_id"], cargo_mass=v["cargo_mass"], unit_count=v["unit_count"],
            market=v["job_market"], special=int(v["special_job"]),
            trailer_name=(trailer or {}).get("name", "") or (trailer or {}).get("body_type", ""),
            planned_km=v["planned_distance_km"], odo_start=v["odometer"], odo_end=v["odometer"],
            income=v["job_income"], truck_wear_start=max(v["wear_engine"], v["wear_transmission"], v["wear_cabin"],
                                                         v["wear_chassis"], v["wear_wheels"]),
            game_time_start=v["job_starting_time"] or v["game_time_abs"], game_time_deadline=v["job_delivery_time_abs"] or None,
            contract_id=contract["id"] if contract else None,
            flags="" if self.seen_idle else "joined-late",
            started_at=now)
        jlog.info("driver #%s: job #%s STARTED in %s: %s (%.1f t) %s, %s -> %s, %s; pays %s; %.0f km planned; truck #%s; "
                  "contract %s%s", self.user_id, self.job_id, v["game"].upper(), v["cargo"], v["cargo_mass"] / 1000,
                  v["source_company"], v["source_city"], v["destination_city"], v["destination_company"], v["job_income"],
                  v["planned_distance_km"], self.truck_id, contract["id"] if contract else "none",
                  "" if self.seen_idle else " (joined mid-job: flagged joined-late)")
        if contract:
            db.update("contracts", contract["id"], status="assigned", assigned_user_id=self.user_id, job_id=self.job_id)
        self.last_fuel = v["fuel"]
        self.last_pt = None
        self.jumps, self._pos = 0, None
        # picked up here (not joined mid-route): this is where the source city is. Kept until the delivery
        # passes the anti-cheat checks, so a faked trip can't move a city on the map.
        self._pickup = (v["game"], v["source_city"], v["x"], v["z"]) if self.seen_idle else None
        db.insert("job_events", job_id=self.job_id, user_id=self.user_id, kind="started",
                  detail=f"{v['cargo']} {v['source_city']} → {v['destination_city']}", x=v["x"], z=v["z"],
                  created_at=now)
        self._compliance_at_start(v)

    ROAD_CELL = 400      # one remembered point per 400 m square: driving the same road again adds nothing
    ROAD_DAILY_CAP = 4000   # new road squares one driver can add per day (a long day of real driving is ~1,500)

    def _remember_road(self, v, now):
        """Builds the Fleet Map's road layer from where trucks actually drive (on a job or not).
        Telemetry can come from a tampered agent, so only physically plausible movement is learned:
        no jumps (teleports) unless a ferry/train moved the truck, and a daily cap per driver."""
        if getattr(self, "demo", False) or v["game"] not in ("ats", "ets2") or abs(v["speed"]) < 2:
            return
        x, z = v["x"], v["z"]
        if not (abs(x) < 3e6 and abs(z) < 3e6):
            return
        if self._road:
            moved, dt = math.hypot(x - self._road[0], z - self._road[1]), now - self._road[2]
            if moved < 250 or dt < 1:
                return
            # trucks cover at most ~60 m of map per real second; anything far beyond that was not driven
            if moved > 3000 + 80 * dt and not (v["ferry"] or v["train"]):
                self._road = (x, z, now)
                return
        if now - getattr(self, "_road_cap_t", 0) > 600:          # re-count at most every 10 minutes
            self._road_cap_t = now
            self._road_today = db.val("SELECT COUNT(*) FROM road_points WHERE user_id=? AND t>?",
                                      (self.user_id, now - 86400), 0)
        if self._road_today >= self.ROAD_DAILY_CAP:
            return
        self._road = (x, z, now)
        self._road_today += 1
        c = self.ROAD_CELL
        db.ex("INSERT OR IGNORE INTO road_points(game, cx, cz, x, z, user_id, t) VALUES (?,?,?,?,?,?,?)",
              (v["game"], int(x // c), int(z // c), round(x, 1), round(z, 1), self.user_id, now))

    def _remember_place(self, game, name, x, z):
        """A pickup or delivery tells us where that city really is in the game world."""
        if getattr(self, "demo", False) or not name or game not in ("ats", "ets2") or (not x and not z):
            return
        row = db.one("SELECT id, x, z, visits FROM map_places WHERE game=? AND name=?", (game, name))
        if row:
            n = min(row["visits"], 50)
            db.update("map_places", row["id"], x=(row["x"] * n + x) / (n + 1), z=(row["z"] * n + z) / (n + 1),
                      visits=row["visits"] + 1, updated_at=time.time())
        else:
            db.insert("map_places", game=game, name=name[:64], x=x, z=z, updated_at=time.time())

    def _compliance_at_start(self, v):
        """Pre-trip DVIR and out-of-service defects are checked when a load is picked up."""
        g = v["game"] if v["game"] in ("ats", "ets2") else "ats"
        gm = compliance.log_min(self.user_id, g, v["game_time_abs"]) or None    # on the logbook clock
        if db.setting("dvir_required") == "1" and not compliance.has_pretrip(self.user_id, self.truck_id, gm, g):
            compliance.record_violation(self.user_id, "DVIR-PRE", f"job{self.job_id}", gm,
                                        compliance.RULE_TEXT["DVIR-PRE"], fine=db.setting_f("dvir_fine", 250),
                                        job_id=self.job_id, game=g)
        if compliance.open_defects(self.truck_id):
            compliance.record_violation(self.user_id, "DEFECT-OOS", f"job{self.job_id}", gm,
                                        compliance.RULE_TEXT["DEFECT-OOS"], fine=db.setting_f("oos_fine", 1500),
                                        job_id=self.job_id, game=g)
        self.inspect_at = compliance.plan_inspection()

    def _load_acc(self):
        j = db.one("SELECT odo_start, max_speed, speeding_s, driving_s, fuel_used, cargo_damage, driven_km "
                   "FROM jobs WHERE id=?", (self.job_id,))
        if not j:
            return None
        self.acc = {k: (j[k] or 0) for k in j}
        self.acc["odo_start"] = j["odo_start"]
        avg = db.val("SELECT avg_speed FROM jobs WHERE id=?", (self.job_id,), 0) or 0
        self.acc["moving_km"] = avg * self.acc["driving_s"] / 3600
        self.acc_job, self.acc_flush = self.job_id, 0
        return self.acc

    def flush(self, odo=None):
        if not self.job_id or getattr(self, "acc_job", None) != self.job_id:
            return
        a = self.acc
        upd = {k: a[k] for k in ("max_speed", "speeding_s", "driving_s", "fuel_used", "cargo_damage", "driven_km")}
        # from the speedometer: the game's odometer runs on map scale, so odometer/time overstates speed
        upd["avg_speed"] = round(a["moving_km"] / (a["driving_s"] / 3600), 1) if a["driving_s"] > 30 else 0
        if odo is not None:
            upd["odo_end"] = odo
        db.update("jobs", self.job_id, **upd)

    def _track(self, v, now):
        dt = min(2.0, now - self.last_t) if self.last_t else 0
        a = self.acc if getattr(self, "acc_job", None) == self.job_id else self._load_acc()
        if a is None:
            self.job_id = None
            return
        kmh = abs(v["speed_kmh"])
        # the truck can't move kilometres between two frames unless a ferry/train (or the game's own
        # teleport) moved it; count unexplained jumps for the anti-cheat review
        if self._pos and now - self._pos[2] < 5 and not (v["ferry"] or v["train"]):
            if math.hypot(v["x"] - self._pos[0], v["z"] - self._pos[1]) > 2500:
                self.jumps += 1
        self._pos = (v["x"], v["z"], now)
        a["max_speed"] = max(a["max_speed"], kmh)
        a["cargo_damage"] = max(a["cargo_damage"], v["cargo_damage"])
        if a["odo_start"] is not None and v["odometer"] >= a["odo_start"]:
            a["driven_km"] = round(v["odometer"] - a["odo_start"], 2)
        if kmh > 2:
            a["driving_s"] += dt
            a["moving_km"] += kmh * dt / 3600
            if now - getattr(self, "_tol_t", 0) > 10:  # re-read the setting at most every 10 s
                self._tol, self._tol_t = db.setting_f("speeding_tolerance_kmh", 5), now
            if v["speed_limit_kmh"] > 1 and kmh > v["speed_limit_kmh"] + self._tol:
                a["speeding_s"] += dt
        if self.last_fuel is not None and v["fuel"] < self.last_fuel:
            a["fuel_used"] = round(a["fuel_used"] + (self.last_fuel - v["fuel"]), 3)
        self.last_fuel = v["fuel"]
        if now - self.acc_flush >= 2:
            self.acc_flush = now
            self.flush(v["odometer"])
        # planned roadside inspection: at the first stop after that point of the route
        if (self.inspect_at is not None and v["planned_distance_km"] and abs(v["speed"]) < 0.5
                and a["driven_km"] >= self.inspect_at * v["planned_distance_km"]):
            self.inspect_at = None
            compliance.inspect(self.user_id, v, self.job_id, self.truck_id)
        # route trail point every ~500 m or 60 s
        x, z = v["x"], v["z"]
        if (self.last_pt is None or math.hypot(x - self.last_pt[0], z - self.last_pt[1]) > 500
                or now - self.last_pt_t > 60):
            db.ex("INSERT INTO job_points(job_id, t, x, z, speed) VALUES (?,?,?,?,?)",
                  (self.job_id, now, x, z, round(kmh, 1)))
            self.last_pt, self.last_pt_t = (x, z), now

    def _events(self, v):
        now = time.time()
        for flag, (kind, amt_key, category, detail) in EVENT_KINDS.items():
            sig = (v[flag], v[amt_key])
            if v[flag] and sig != self.sig.get(kind):
                amount = float(v[amt_key])
                if kind == "refuel":
                    # The game also raises this event when a job or truck is loaded, without any fuel
                    # being added. Only book it once the tank level actually rises.
                    base = min(f for _t, f in self.fuel_hist)
                    self.pending_refuel = {"t": now, "litres": amount, "base": base, "detail": detail(v),
                                           "truck": (v["truck_id"], v["license_plate"]), "x": v["x"], "z": v["z"],
                                           "job_id": self.job_id}
                    self.sig[kind] = sig
                    continue
                db.insert("job_events", job_id=self.job_id, user_id=self.user_id, kind=kind, amount=amount,
                          detail=detail(v), x=v["x"], z=v["z"], created_at=now)
                jlog.info("driver #%s: %s %.2f - %s (job #%s)", self.user_id, kind, amount, detail(v), self.job_id)
                economy.post(category, -amount, f"{kind.title()}: {detail(v)} ({economy.user_name(self.user_id)})",
                             job_id=self.job_id)
                if self.job_id and kind in JOB_COL:
                    col = JOB_COL[kind]
                    db.ex(f"UPDATE jobs SET {col} = {col} + ? WHERE id=?", (amount, self.job_id))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            self.sig[kind] = sig

    def _confirm_refuel(self, v, now):
        p = self.pending_refuel
        if not p:
            return
        if (v["truck_id"], v["license_plate"]) != p["truck"] or now - p["t"] > 120:
            self.pending_refuel = None      # truck swapped / nothing happened: phantom event
            jlog.debug("driver #%s: refuel event ignored (the tank didn't fill - the game raises it on load too)",
                       self.user_id)
            return
        p["base"] = min(p["base"], v["fuel"])
        if v["fuel"] - p["base"] >= max(1.0, 0.5 * p["litres"]):
            self.pending_refuel = None
            cost = round(p["litres"] * db.setting_f("fuel_price", 1.25), 2)
            db.insert("job_events", job_id=p["job_id"], user_id=self.user_id, kind="refuel", amount=cost,
                      detail=p["detail"], x=p["x"], z=p["z"], created_at=now)
            economy.post("fuel", -cost, f"Refuel: {p['detail']} ({economy.user_name(self.user_id)})", job_id=p["job_id"])
            jlog.info("driver #%s: refuel %.1f L booked as %.2f - %s", self.user_id, p["litres"], cost, p["detail"])

    def _finish(self, v, now):
        dsig = delivered_sig(v)
        if v["job_delivered"] and dsig != self.sig.get("delivered"):
            if self.job_id:
                self.flush(v["odometer"])
                db.update("jobs", self.job_id, status="delivered", revenue=v["job_delivered_revenue"],
                          game_xp=v["job_delivered_earned_xp"], cargo_damage=v["job_delivered_cargo_damage"],
                          driven_km=round(v["job_delivered_distance_km"], 2) or None,
                          autopark=int(v["job_delivered_autopark_used"]),
                          autoload=int(v["job_delivered_autoload_used"]),
                          truck_wear_end=max(v["wear_engine"], v["wear_transmission"], v["wear_cabin"],
                                             v["wear_chassis"], v["wear_wheels"]),
                          game_time_end=v["job_finished_time"] or v["game_time_abs"], finished_at=now)
                self._fix_km()
                db.insert("job_events", job_id=self.job_id, user_id=self.user_id, kind="delivered",
                          amount=v["job_delivered_revenue"], detail="Delivered", x=v["x"], z=v["z"], created_at=now)
                self._close_contract()
                dest = db.val("SELECT destination_city FROM jobs WHERE id=?", (self.job_id,))
                problems = self._flag_check(self.job_id)
                if not problems and not v["paused"]:
                    if getattr(self, "_pickup", None):
                        self._remember_place(*self._pickup)
                    self._remember_place(v["game"], dest, v["x"], v["z"])
                self._pickup = None
                hold_all = self.remote and db.setting("agent_jobs_need_approval") == "1"
                jlog.info("driver #%s: job #%s DELIVERED: revenue %s, xp %s, cargo damage %.1f%%, %.1f km, autopark %s, "
                          "autoload %s; anti-cheat: %s%s", self.user_id, self.job_id, v["job_delivered_revenue"],
                          v["job_delivered_earned_xp"], v["job_delivered_cargo_damage"] * 100,
                          v["job_delivered_distance_km"], v["job_delivered_autopark_used"],
                          v["job_delivered_autoload_used"], ", ".join(problems) if problems else "passed",
                          " - held for review" if problems or hold_all else " - paid")
                if problems or hold_all:
                    # nothing is paid until a manager has looked at it
                    db.update("jobs", self.job_id, status="pending_review")
                    why = ", ".join(problems) if problems else "remote driver"
                    for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'"):
                        db.notify(m["id"], f"Delivery #{self.job_id} by {economy.user_name(self.user_id)} is held for "
                                           f"review ({why}).", f"/jobs/{self.job_id}", "warning")
                    db.notify(self.user_id, f"Your delivery #{self.job_id} is waiting for a manager's approval.",
                              f"/jobs/{self.job_id}", "warning")
                else:
                    economy.settle_job(self.job_id)
                self.job_id = None
        self.sig["delivered"] = dsig

        csig = cancelled_sig(v)
        if v["job_cancelled"] and csig != self.sig.get("cancelled"):
            if self.job_id:
                self.flush(v["odometer"])
                pen = float(v["job_cancelled_penalty"])
                db.update("jobs", self.job_id, status="cancelled", penalty=pen, finished_at=now,
                          game_time_end=v["job_finished_time"] or v["game_time_abs"])
                db.insert("job_events", job_id=self.job_id, user_id=self.user_id, kind="cancelled", amount=pen,
                          detail="Job cancelled", created_at=now)
                economy.post("cancellation", -pen, f"Cancelled job #{self.job_id}", job_id=self.job_id)
                jlog.info("driver #%s: job #%s CANCELLED in the game, penalty %.2f", self.user_id, self.job_id, pen)
                db.ex("UPDATE contracts SET status='open', job_id=NULL WHERE job_id=?", (self.job_id,))
                self.job_id = None
        self.sig["cancelled"] = csig

        # job vanished without an event (e.g. profile change) - abandon after 15 s
        if self.job_id and not v["on_job"] and not v["paused"]:
            self.off_job_since = self.off_job_since or now
            if now - self.off_job_since > 15:
                self.flush()
                db.update("jobs", self.job_id, status="abandoned", finished_at=now)
                jlog.info("driver #%s: job #%s ABANDONED (gone from the game for 15 s without a delivery or cancel)",
                          self.user_id, self.job_id)
                self.job_id = None
        else:
            self.off_job_since = None

    def _fix_km(self):
        db.ex("UPDATE jobs SET driven_km = COALESCE(driven_km, MAX(0, odo_end - odo_start)) WHERE id=?", (self.job_id,))

    def _close_contract(self):
        j = db.one("SELECT contract_id FROM jobs WHERE id=?", (self.job_id,))
        if j and j["contract_id"]:
            db.update("contracts", j["contract_id"], status="completed", completed_at=time.time(), job_id=self.job_id)

    def _flag_check(self, job_id):
        """Anti-cheat. The server can't see the game, so it checks that what the driver's PC reported is
        physically plausible. Returns the problems found; a job with problems isn't paid until a manager
        approves it."""
        j = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        flags = [f for f in (j["flags"] or "").split(",") if f]
        if getattr(self, "demo", False):  # the simulator compresses time, so speed checks don't apply
            db.update("jobs", job_id, flags=",".join(sorted(set(flags + ["demo"]))))
            return []
        late = "joined-late" in flags
        a = self.acc if getattr(self, "acc_job", None) == job_id else {}
        moving, odo = a.get("moving_km", 0), a.get("driven_km", 0)
        if self.jumps:
            flags.append("teleport")
        # the odometer runs on map scale (about 20x the speedometer); far more than that was edited in
        if odo > moving * 30 + 5:
            flags.append("odometer")
        # delivered, but almost none of the drive was seen by the tracker
        if j["planned_km"] and moving < max(0.2, j["planned_km"] / 25 * 0.2):
            flags.append("unobserved")
        # faster than any truck could cover the route (130 km/h on a 1:20 map = 2,600 km/h of map distance)
        hours = ((j["finished_at"] or 0) - j["started_at"]) / 3600
        if not late and j["planned_km"] and hours > 0 and j["planned_km"] / hours > 3500:
            flags.append("too-fast")
        if j["avg_speed"] and j["avg_speed"] > 150:
            flags.append("avg-speed")
        if j["max_speed"] and j["max_speed"] > 200:
            flags.append("top-speed")
        if j["income"] and j["revenue"] > j["income"] * 2 + 1000:
            flags.append("revenue")
        if j["planned_km"] and j["driven_km"] is not None and j["driven_km"] < j["planned_km"] * 0.3 \
                and "joined-late" not in flags:
            flags.append("short-distance")
        if flags:
            db.update("jobs", job_id, flags=",".join(sorted(set(flags))))
        return sorted(set(flags) - {"joined-late", "demo"})


_mass_fn_ready = threading.local()


def _has_mass_fn():
    c = db.conn()
    if not getattr(_mass_fn_ready, "ok", False) or getattr(_mass_fn_ready, "c", None) is not c:
        c.create_function("cargo_mass_ok", 2, lambda kg, min_t: 1 if (kg or 0) / 1000 >= (min_t or 0) else 0)
        _mass_fn_ready.ok, _mass_fn_ready.c = True, c
    return True


class Hub:
    """Holds the latest frame for every driver and routes frames to trackers."""

    def __init__(self):
        self.live = {}        # user_id -> {"frame":..., "t":..., "source":...}
        self.trackers = {}
        self.lock = threading.Lock()
        self._local_status = "starting"
        self.sampler = None

    @property
    def local_status(self):
        return self._local_status

    @local_status.setter
    def local_status(self, new):          # every change of the game connection goes into the debug log
        tracelog.TELEMETRY.status(self._local_status, new)
        self._local_status = new

    def tracker(self, uid):
        with self.lock:
            if uid not in self.trackers:
                self.trackers[uid] = JobTracker(uid)
            return self.trackers[uid]

    def ingest(self, uid, frame, source):
        if not uid:
            return
        if frame is not None:
            frame["source"] = source
        tracelog.TELEMETRY.frame(uid, frame, source)
        self.tracker(uid).feed(frame)
        with self.lock:
            if frame is None:
                if uid in self.live and self.live[uid]["source"] == source:
                    self.live[uid]["frame"] = None
                return
            self.live[uid] = {"frame": frame, "t": time.time(), "source": source}
        fluids.record(uid, frame)

    def get(self, uid):
        e = self.live.get(uid)
        if not e or not e["frame"] or time.time() - e["t"] > 15:
            return None
        return e

    def all_live(self):
        now = time.time()
        return {u: e for u, e in list(self.live.items()) if e["frame"] and now - e["t"] < 15}

    def start_local(self):
        if self.sampler:
            return
        self.sampler = LocalSampler(self)
        self.sampler.start()


def local_user():
    uid = db.setting("telemetry_user_id", "")
    if uid and str(uid).isdigit():
        return int(uid)
    return db.val("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1")


class LocalSampler(threading.Thread):
    """Polls the DLL shared memory (or simulator) at 10 Hz."""

    def __init__(self, hub, hz=10):
        super().__init__(daemon=True, name="telemetry")
        self.hub, self.hz = hub, hz
        self.mem = reader.SharedMemory()
        self.sim = None
        self.halt = threading.Event()
        self.raw = None
        self.frames = 0

    def run(self):
        last_sim, stale_since, last_upkeep = None, None, 0
        while not self.halt.is_set():
            try:
                if time.time() - last_upkeep > 300:
                    last_upkeep = time.time()
                    economy.process_loans()
                    from .. import leasing
                    leasing.process()
                    economy.process_upkeep()
                from .. import licensing
                if not licensing.licensed():
                    self.hub.local_status = "locked — Fleet Manager Pro isn't activated"
                    self.halt.wait(3)
                    continue
                mode = db.setting("telemetry_mode", "auto")
                uid = local_user()
                if mode == "off" or not uid:
                    self.hub.local_status = "telemetry off" if mode == "off" else "waiting for setup"
                    self._idle(uid)
                    continue
                if mode == "demo":
                    self.sim = self.sim or Simulator()
                    buf = self.sim.step()
                    self.hub.local_status = "DEMO simulator running (no game needed)"
                else:
                    self.sim = None
                    if not self.mem.is_open and not self.mem.open():
                        self.hub.local_status = "waiting for game — scs-telemetry.dll not loaded"
                        self._idle(uid, 2)
                        continue
                    buf = self.mem.read()
                frame = reader.parse(buf)
                frame["demo"] = mode == "demo"
                v = frame["values"]
                now = time.time()
                # The mapping outlives the game while any other app keeps a handle open, so watch the
                # clocks: render time keeps ticking every frame (even paused) while the game is alive.
                clocks = (v["render_time"], v["simulated_time"], v["time"])
                if clocks == last_sim:
                    stale_since = stale_since or now
                else:
                    stale_since = None
                last_sim = clocks
                if not v["sdk_active"] or (stale_since and now - stale_since > 6):
                    self.hub.local_status = "game not running (no live data from scs-telemetry.dll)"
                    if stale_since and now - stale_since > 30:
                        self.mem.close()
                    self._idle(uid, 1)
                    continue
                if mode != "demo":
                    self.hub.local_status = (f"connected — {v['game'].upper()}{' (paused)' if v['paused'] else ''}, "
                                             f"SDK {v['game_version_major']}.{v['game_version_minor']}, "
                                             f"plugin rev {v['plugin_revision']}")
                self.raw = buf
                self.frames += 1
                self.hub.ingest(uid, frame, "local")
            except Exception as e:  # never let the sampler die
                self.hub.local_status = f"error: {e}"
                self.mem.close()
                self.halt.wait(2)
                continue
            self.halt.wait(1.0 / self.hz)

    def _idle(self, uid, wait=3):
        if uid:
            self.hub.ingest(uid, None, "local")
        self.halt.wait(wait)


HUB = Hub()


def _agent_connection(server, pin):
    """An HTTP(S) connection to the server. With a pin, the server's certificate must match it exactly
    (self-made LAN certificate); without one, normal certificate checks apply."""
    import hashlib
    import http.client
    import ssl
    import urllib.parse
    u = urllib.parse.urlsplit(server)
    if u.scheme != "https":
        return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=8), u
    if pin:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE      # trust comes from the pin
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    else:
        ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=8, context=ctx)
    conn.connect()
    if pin:
        seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if seen != pin.strip().lower().replace(":", ""):
            conn.close()
            raise ConnectionError("the server's certificate doesn't match --pin (wrong server, or it was "
                                  "re-created: copy the new fingerprint from your profile page)")
    return conn, u


def run_agent(server, token, pin=""):
    """Remote driver: read the local DLL and stream frames to a Fleet Manager Pro server."""
    server = server.rstrip("/")
    mem = reader.SharedMemory()
    if server.startswith("http://") and not any(h in server for h in ("//127.0.0.1", "//localhost")):
        print("[agent] WARNING: http:// is not encrypted - your token crosses the network in the clear. "
              "Use the https:// address and --pin from your profile page.")
    print(f"[agent] streaming telemetry to {server}  (Ctrl+C to stop)")
    last_sent, last_flags, conn = 0, None, None
    flag_keys = [k for k, g, *_ in reader.FIELDS if g == "flags"] + \
                ["fine_amount", "toll_amount", "ferry_amount", "train_amount", "refuel_amount"]
    while True:
        try:
            if not mem.is_open and not mem.open():
                print("[agent] waiting for game (scs-telemetry.dll)…")
                time.sleep(3)
                continue
            frame = reader.parse(mem.read())
            if not frame["values"]["sdk_active"]:
                time.sleep(1)
                continue
            flags = tuple(frame["values"][k] for k in flag_keys)
            if flags != last_flags or time.time() - last_sent >= 0.5:
                last_flags = flags
                body = json.dumps({"frame": {"values": frame["values"], "trailers": frame["trailers"],
                                             "substances": frame["substances"]}}).encode()
                if conn is None:
                    conn, _u = _agent_connection(server, pin)
                conn.request("POST", "/api/agent/frame", body=body,
                             headers={"Content-Type": "application/json", "X-Agent-Token": token})
                r = conn.getresponse()
                r.read()
                if r.status == 401:
                    print("[agent] the server refused the token - copy the current one from your profile page")
                    time.sleep(10)
                elif r.status >= 400:
                    print(f"[agent] server answered {r.status}")
                    time.sleep(3)
                last_sent = time.time()
            time.sleep(0.1)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"[agent] {e}")
            try:
                if conn:
                    conn.close()
            except Exception as e2:
                logging.getLogger(__name__).debug("agent connection close failed: %s", e2)
            conn = None
            time.sleep(3)
