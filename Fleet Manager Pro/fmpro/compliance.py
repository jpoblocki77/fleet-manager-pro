"""Compliance: ELD / Hours of Service, DVIR (pre/post-trip inspections) and roadside (DOT) inspections.

All clocks run on GAME time (telemetry `game_time_abs`, minutes), like a real ELD would inside the game world:
sleeping in-game fast-forwards the clock and counts as rest.
"""
import json
import secrets
import time

from . import bank, cdl, db, economy

_rng = secrets.SystemRandom()   # game dice; the OS generator, not the predictable random module

# ------------------------------------------------------------------------------------------------ rules
RULESETS = {
    "us70": {"label": "US FMCSA 70-hour / 8-day", "drive": 11 * 60, "window": 14 * 60, "break_after": 8 * 60,
             "break_len": 30, "reset": 10 * 60, "cycle": 70 * 60, "cycle_days": 8, "restart": 34 * 60},
    "us60": {"label": "US FMCSA 60-hour / 7-day", "drive": 11 * 60, "window": 14 * 60, "break_after": 8 * 60,
             "break_len": 30, "reset": 10 * 60, "cycle": 60 * 60, "cycle_days": 7, "restart": 34 * 60},
    "eu": {"label": "EU 561/2006 (simplified)", "drive": 9 * 60, "window": 13 * 60, "break_after": 270,
           "break_len": 45, "reset": 11 * 60, "reset_min": 9 * 60, "cycle": 56 * 60, "cycle_days": 7, "restart": 45 * 60},
}
STATUSES = {"off": "Off duty", "sleeper": "Sleeper berth", "driving": "Driving", "on": "On duty (not driving)"}
REST = ("off", "sleeper")
WORK = ("on", "driving")


def ruleset_for(game):
    key = db.setting("hos_rules_ets2" if game == "ets2" else "hos_rules_ats", "eu" if game == "ets2" else "us70")
    return key if key in RULESETS else "us70"


def _segments(uid, since, now, game="ats"):
    rows = db.q("SELECT status, start_min, COALESCE(end_min, start_min) end_min FROM hos_logs "
                "WHERE user_id=? AND COALESCE(game,'ats')=? AND COALESCE(end_min, start_min) >= ? AND start_min <= ? "
                "ORDER BY start_min, id", (uid, game, since, now))
    out = []
    for r in rows:
        s, e = max(r["start_min"], since), min(max(r["end_min"], r["start_min"]), now)
        if e > s:
            out.append((r["status"], s, e))
    return out


def _gaps_as_off(segs, since, now):
    """Time with no log at all (game closed, other profile) is treated as off duty."""
    out, t = [], since
    for st, s, e in segs:
        if s > t:
            out.append(("off", t, s))
        out.append((st, max(s, t), e))
        t = max(t, e)
    if now > t:
        out.append(("off", t, now))
    return [x for x in out if x[2] > x[1]]


def _last_rest_end(segs, length):
    """End of the most recent consecutive rest (off/sleeper) period lasting >= length minutes."""
    run_start = run_end = None
    best = None
    for st, s, e in segs:
        if st in REST:
            if run_end is not None and s <= run_end:
                run_end = e
            else:
                run_start, run_end = s, e
            if run_end - run_start >= length:
                best = run_end
        else:
            run_start = run_end = None
    return best


def clocks(uid, now_min=None, game="ats"):
    """Current HOS clocks for a driver, in minutes."""
    rules = RULESETS[ruleset_for(game)]
    if now_min is None:
        now_min = last_game_min(uid, game)
    if now_min is None:
        return None
    lookback = rules["cycle_days"] * 1440 + rules["restart"]
    segs = _gaps_as_off(_segments(uid, now_min - lookback, now_min, game), now_min - lookback, now_min)

    # simulated rests are real off-duty lines in the logbook now; only the zero-length markers made before 1.4.7
    # need counting here, as a rest of that length ending at that minute
    sims = db.q("SELECT start_min, sim_rest FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? AND sim_rest > 0 "
                "AND COALESCE(end_min, start_min) <= start_min AND start_min BETWEEN ? AND ?",
                (uid, game, now_min - lookback, now_min))

    def rest_end(segments, length):
        real = _last_rest_end(segments, length)
        sim = max((s["start_min"] for s in sims if s["sim_rest"] >= length), default=None)
        return max((x for x in (real, sim) if x is not None), default=None)

    # a daily rest resets the drive and window clocks; EU rules also accept a reduced daily rest (9 h)
    reset_end = rest_end(segs, rules.get("reset_min", rules["reset"])) or (now_min - lookback)
    after = [x for x in segs if x[2] > reset_end]
    drive = sum(min(e, now_min) - max(s, reset_end) for st, s, e in after if st == "driving")
    work_starts = [max(s, reset_end) for st, s, e in after if st in WORK]
    window_used = now_min - work_starts[0] if work_starts else 0

    brk = rest_end([("off" if st != "driving" else st, s, e) for st, s, e in segs], rules["break_len"])
    brk = max(brk or reset_end, reset_end)
    since_break = sum(e - max(s, brk) for st, s, e in segs if st == "driving" and e > brk)

    cyc_from = now_min - rules["cycle_days"] * 1440
    restart = rest_end(segs, rules["restart"])
    if restart and restart > cyc_from:
        cyc_from = restart
    if game == "ets2" or ruleset_for(game) == "eu":
        cycle_used = sum(e - max(s, cyc_from) for st, s, e in segs if st == "driving" and e > cyc_from)
    else:
        cycle_used = sum(e - max(s, cyc_from) for st, s, e in segs if st in WORK and e > cyc_from)

    def clock(used, limit):
        return {"used": int(used), "limit": limit, "left": int(limit - used), "pct": min(100, round(used / limit * 100, 1)),
                "over": used > limit}

    c = {"rules": ruleset_for(game), "rules_label": rules["label"], "now_min": now_min,
         "drive": clock(drive, rules["drive"]), "window": clock(window_used, rules["window"]),
         "break": clock(since_break, rules["break_after"]), "cycle": clock(cycle_used, rules["cycle"]),
         "reset_key": reset_end, "break_key": brk, "cycle_key": cyc_from, "break_len": rules["break_len"],
         "reset_len": rules["reset"], "restart_len": rules["restart"]}
    c["available"] = max(0, min(c["drive"]["left"], c["window"]["left"], c["cycle"]["left"]))
    c["out_of_hours"] = c["available"] <= 0
    cur = db.one("SELECT status, start_min FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? "
                 "AND (COALESCE(sim_rest,0)=0 OR COALESCE(end_min, start_min) > start_min) "   # skip pre-1.4.7 markers
                 "ORDER BY start_min DESC, id DESC LIMIT 1", (uid, game))
    c["status"] = cur["status"] if cur else "off"
    c["status_since"] = cur["start_min"] if cur else None
    return c


def last_game_min(uid, game="ats"):
    """The driver's latest in-game minute in that game's logbook (each game has its own clock)."""
    return db.val("SELECT MAX(COALESCE(end_min, start_min)) FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=?",
                  (uid, game))


# The logbook keeps its own clock: the in-game minute plus every simulated rest the driver has taken (the Simulate
# buttons write a real off-duty line of that length, so the time after it moves on by the same amount). A logbook
# reset clears those lines, and with them the extra time.
EPOCH = [0]          # bumped whenever simulated rests or the logbook change, so live trackers re-read the offset


def log_offset(uid, game="ats"):
    """Minutes of simulated rest in this driver's logbook for that game (added to the game clock)."""
    return int(db.val("SELECT COALESCE(SUM(sim_rest), 0) FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? "
                      "AND sim_rest > 0 AND COALESCE(end_min, start_min) > start_min", (uid, game), 0) or 0)


def log_min(uid, game, game_min):
    """An in-game minute (telemetry game_time_abs) on this driver's logbook clock."""
    return game_min + log_offset(uid, game) if game_min else game_min


def logbook_changed():
    EPOCH[0] += 1


# ------------------------------------------------------------------------------------------------ violations
RULE_TEXT = {
    "HOS-DRIVE": "Drove beyond the {h} driving limit",
    "HOS-WINDOW": "Drove after the {h} on-duty window expired",
    "HOS-BREAK": "Drove more than {h} without a {b}-minute break",
    "HOS-CYCLE": "Drove over the {h} cycle limit",
    "DVIR-PRE": "Started a load without a pre-trip inspection (DVIR) in the last 24 hours",
    "DEFECT-OOS": "Drove a truck with unrepaired DVIR defects (out of service)",
}


def _h(m):
    return f"{m // 60} h" if m % 60 == 0 else f"{m / 60:.1f} h"


WARN_AHEAD = 15   # game-minutes before an HOS limit that the logbook warns the driver (shown on the overlay + bell)


def _warn_near_limit(uid, game, key, period, text):
    """One 'approaching a limit' alert for the in-game overlay (and the notifications bell). The period key is tied
    to the current rest / break / cycle, so each warning fires once per driving stint and re-arms after a rest."""
    link = f"/compliance?near={game}:{key}:{period}"
    if db.val("SELECT 1 FROM notifications WHERE user_id=? AND link=? LIMIT 1", (uid, link)):
        return
    db.notify(uid, text, link, "warning")


def record_violation(uid, rule, period_key, game_min, detail="", fine=None, job_id=None, game="ats"):
    """Store a violation once per period; charge the fine (driver or company, see bank.fine). Returns True if new."""
    fine = db.setting_f("hos_fine", 500) if fine is None else fine
    try:
        db.ex("INSERT INTO hos_violations(user_id, rule, detail, game_min, period_key, fine, created_at, game) "
              "VALUES (?,?,?,?,?,?,?,?)", (uid, rule, detail, game_min, f"{game}:{period_key}", fine, time.time(), game))
    except Exception:   # already recorded for this period
        return False
    if fine:
        bank.fine(uid, "compliance fine", fine, f"{rule}: {detail}", job_id)
    if job_id:
        db.insert("job_events", job_id=job_id, user_id=uid, kind="violation", amount=fine, detail=f"{rule}: {detail}",
                  created_at=time.time())
        db.ex("UPDATE jobs SET fines = fines + ? WHERE id=?", (fine, job_id))
    db.notify(uid, f"Compliance violation {rule}: {detail}", "/compliance?tab=violations", "error")
    if fine:
        cdl.company_fine(uid, "oos" if rule == "DEFECT-OOS" else "compliance", f"{rule}: {detail}", job_id, game, fine)
    return True


def check_hos(uid, game, now_min, job_id=None):
    rv = lambda *a, **k: record_violation(*a, game=game, **k)
    if db.setting("hos_enabled") != "1":
        return
    c = clocks(uid, now_min, game)
    if not c:
        return
    r = RULESETS[c["rules"]]
    if c["drive"]["over"]:
        rv(uid, "HOS-DRIVE", f"d{c['reset_key']}", now_min, RULE_TEXT["HOS-DRIVE"].format(h=_h(r["drive"])), job_id=job_id)
    if c["window"]["over"]:
        rv(uid, "HOS-WINDOW", f"w{c['reset_key']}", now_min, RULE_TEXT["HOS-WINDOW"].format(h=_h(r["window"])), job_id=job_id)
    if c["break"]["over"]:
        rv(uid, "HOS-BREAK", f"b{c['break_key']}", now_min,
                         RULE_TEXT["HOS-BREAK"].format(h=_h(r["break_after"]), b=r["break_len"]), job_id=job_id)
    if c["cycle"]["over"]:
        rv(uid, "HOS-CYCLE", f"c{c['cycle_key'] // 1440}", now_min, RULE_TEXT["HOS-CYCLE"].format(h=_h(r["cycle"])), job_id=job_id)

    # 15 minutes before a limit: one heads-up pushed to the overlay and the bell, once per driving stint per limit.
    near = (
        ("drive",  f"d{c['reset_key']}",          f"{c['drive']['left']} min of driving time left — find a rest stop."),
        ("window", f"w{c['reset_key']}",          f"{c['window']['left']} min left in your on-duty window — plan to park."),
        ("break",  f"b{c['break_key']}",          f"Break due in {c['break']['left']} min — a {r['break_len']}-minute break is coming up."),
        ("cycle",  f"c{c['cycle_key'] // 1440}",  f"{c['cycle']['left']} min left in your weekly cycle."),
    )
    for key, period, text in near:
        if 0 < c[key]["left"] <= WARN_AHEAD and not c[key]["over"]:
            _warn_near_limit(uid, game, key, period, text)


# ------------------------------------------------------------------------------------------------ duty status tracker
class HosTracker:
    """Turns telemetry into ELD duty-status segments for one driver."""

    def __init__(self, uid):
        self.uid = uid
        self.row = None          # open hos_logs row id
        self.status = None
        self.last_min = None
        self.cand, self.cand_since = None, None
        self.last_write = 0
        self.last_check = 0

    @staticmethod
    def derive(v):
        if abs(v["speed"]) > 1.5:
            return "driving"
        if v["engine_enabled"] or (v["on_job"] and not v["parking_brake"]):
            return "on"
        return "off"

    def _open(self, status, start, v, auto=1, truck_id=None):
        if self.row:
            db.update("hos_logs", self.row, end_min=start)
        loc = v.get("destination_city") if status == "driving" and v.get("on_job") else ""
        self.row = db.insert("hos_logs", user_id=self.uid, status=status, start_min=start, end_min=start,
                             truck_id=truck_id, location=loc or "", auto=auto, created_at=time.time(), game=self.game)
        self.status = status

    def feed(self, v, truck_id=None, job_id=None):
        if db.setting("hos_enabled") != "1":
            return
        gm = v["game_time_abs"]
        if not gm or v["paused"] or v.get("game") not in ("ats", "ets2"):
            return
        if v["game"] != getattr(self, "game", None) or getattr(self, "epoch", None) != EPOCH[0]:
            # switched game (that game's own logbook and clock), or a simulated rest / reset changed the logbook
            self.game = v["game"]
            self.epoch = EPOCH[0]
            self.offset = log_offset(self.uid, self.game)
            self.row = self.status = self.last_min = None
            self.cand = None
            self.last_write = self.last_check = 0
        gm += self.offset                                    # the logbook's own clock (see log_offset)
        if self.status is None:                              # (re)connect: continue the last segment if it fits
            last = db.one("SELECT * FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? "
                          "AND COALESCE(sim_rest,0)=0 ORDER BY start_min DESC, id DESC LIMIT 1", (self.uid, self.game))
            if last and 0 <= gm - (last["end_min"] or last["start_min"]) <= 10:
                self.row, self.status = last["id"], last["status"]
                self.last_min = last["end_min"] or last["start_min"]
        if self.last_min is not None and gm < self.last_min - 5:     # other profile / older save: new log line
            self.row, self.status = None, None
            self.last_min = None
        want = self.derive(v)
        if self.last_min is not None and gm - self.last_min >= 30 and want != "driving":
            # time jumped while stopped: the driver slept (ATS/ETS2 sleep skips hours)
            self._open("sleeper", self.last_min, v, truck_id=truck_id)
            db.update("hos_logs", self.row, end_min=gm)
            self._open(want, gm, v, truck_id=truck_id)
            self.cand = None
        elif self.status is None:
            self._open(want, gm, v, truck_id=truck_id)
        elif want != self.status:
            if self.cand != want:
                self.cand, self.cand_since = want, gm
            # debounce: driving starts after 1 game minute, stops being driving after 3
            need = 1 if want == "driving" else 3
            if gm - self.cand_since >= need:
                start = self.cand_since if want == "driving" else gm
                self._open(want, start, v, truck_id=truck_id)
                self.cand = None
        else:
            self.cand = None
        self.last_min = gm
        if self.row and (gm - self.last_write >= 2 or gm < self.last_write):
            db.update("hos_logs", self.row, end_min=gm)
            self.last_write = gm
        if self.status == "driving" and gm - self.last_check >= 1:
            self.last_check = gm
            check_hos(self.uid, v.get("game", "ats"), gm, job_id)


def set_status(uid, status, game_min, note="", game="ats"):
    """Manual duty-status change from the logbook page (e.g. off duty at home)."""
    open_row = db.one("SELECT id FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? "
                      "AND COALESCE(sim_rest,0)=0 ORDER BY start_min DESC, id DESC LIMIT 1", (uid, game))
    if open_row:
        db.update("hos_logs", open_row["id"], end_min=game_min)
    db.insert("hos_logs", user_id=uid, status=status, start_min=game_min, end_min=game_min, auto=0, note=note,
              created_at=time.time(), game=game)


def simulate_rest(uid, kind, game_min, game="ats"):
    """Take a break (kind 'break'), a full sleeper-berth rest (kind 'sleeper', 10 h US / 11 h EU) or a cycle restart
    (kind 'restart', e.g. 34 h) right now without waiting it out in the game: an off-duty (sleeper-berth for 'sleeper')
    line of that length goes into the logbook (it shows on the graph like any rest) and
    the driver's logbook clock moves on by the same amount, so what they drive next comes after the rest.
    game_min is on the logbook clock. Returns the simulated length in minutes."""
    rules = RULESETS[ruleset_for(game)]
    length = {"break": rules["break_len"], "sleeper": rules["reset"]}.get(kind, rules["restart"])
    label = {"break": f"{length}-min break", "sleeper": f"{length // 60}-hour sleeper berth"}.get(kind, f"{length // 60}-hour restart")
    last = db.val("SELECT MAX(COALESCE(end_min, start_min)) FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=?",
                  (uid, game))
    if last is not None and last > game_min:
        game_min = last                                     # never overlap what's already logged
    open_row = db.one("SELECT id, end_min FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? "
                      "AND COALESCE(sim_rest,0)=0 ORDER BY start_min DESC, id DESC LIMIT 1", (uid, game))
    if open_row and (open_row["end_min"] or 0) < game_min:
        db.update("hos_logs", open_row["id"], end_min=game_min)  # the open line ends where the rest starts
    db.insert("hos_logs", user_id=uid, status="sleeper" if kind == "sleeper" else "off", start_min=game_min, end_min=game_min + length, auto=0,
              note=f"Simulated {label}", sim_rest=length, created_at=time.time(), game=game)
    logbook_changed()
    return length


# ------------------------------------------------------------------------------------------------ DVIR
DVIR_ITEMS = [
    ("brakes", "Service & parking brakes", "Air pressure builds, no leaks, parking brake holds"),
    ("tires", "Tires, wheels & lug nuts", "Tread depth, inflation, no cuts or loose lugs"),
    ("lights", "Lights & reflectors", "Headlights, marker, brake, turn signals, reflective tape"),
    ("coupling", "Coupling: fifth wheel, kingpin, locking jaws", "Jaws locked, no gap, release handle set"),
    ("air", "Air lines & glad hands", "Seals intact, lines not chafed or tangled"),
    ("steering", "Steering & suspension", "No excessive play, springs and mounts intact"),
    ("cab", "Mirrors, wipers, horn & windshield", "Clean and adjusted, no cracks in view"),
    ("cargo", "Cargo securement, doors & seal", "Straps/chains tight, doors latched, seal number matches"),
]


def telemetry_hints(v):
    """Suggest defects from live wear data."""
    hints = {}
    if not v:
        return hints
    if v.get("wear_wheels", 0) > 0.1:
        hints["tires"] = f"Wheel wear {v['wear_wheels'] * 100:.0f}%"
    if v.get("wear_chassis", 0) > 0.1:
        hints["steering"] = f"Chassis wear {v['wear_chassis'] * 100:.0f}%"
    if v.get("wear_cabin", 0) > 0.1:
        hints["cab"] = f"Cabin wear {v['wear_cabin'] * 100:.0f}%"
    if v.get("air_pressure_warning"):
        hints["brakes"] = "Low air pressure warning is on"
    if v.get("cargo_damage", 0) > 0.03:
        hints["cargo"] = f"Cargo damage {v['cargo_damage'] * 100:.1f}%"
    return hints


def open_defects(truck_id):
    if not truck_id:
        return []
    return db.q("SELECT * FROM dvirs WHERE truck_id=? AND status='defects' ORDER BY created_at DESC", (truck_id,))


def has_pretrip(uid, truck_id, game_min, game="ats"):
    if game_min is None:
        return bool(db.val("SELECT id FROM dvirs WHERE user_id=? AND kind='pre' AND created_at>?",
                           (uid, time.time() - 86400)))
    return bool(db.val("SELECT id FROM dvirs WHERE user_id=? AND kind='pre' AND (truck_id=? OR ? IS NULL) "
                       "AND COALESCE(game,'ats')=? AND game_min BETWEEN ? AND ?",
                       (uid, truck_id, truck_id, game, game_min - 1440, game_min + 5)))


# ------------------------------------------------------------------------------------------------ roadside inspection
def plan_inspection():
    """Called when a job starts: decide whether (and where along the route) a roadside inspection happens."""
    if db.setting("inspections_enabled") != "1":
        return None
    if _rng.random() * 100 >= db.setting_f("inspection_chance_pct", 15):
        return None
    return _rng.uniform(0.2, 0.8)


def inspect(uid, v, job_id=None, truck_id=None, level=None):
    """Roadside inspection at the current position. Returns the inspection row id."""
    game = v.get("game", "ats")
    level = level or _rng.choices([1, 2, 3], weights=[20, 30, 50])[0]
    findings = []

    def add(code, text, severity, fine):
        findings.append({"code": code, "text": text, "severity": severity, "fine": fine})

    gm = log_min(uid, game, v.get("game_time_abs")) or last_game_min(uid, game)
    hos_fine = db.setting_f("hos_fine", 500)
    # Level 1-3: driver credentials and hours
    c = clocks(uid, gm, game) if gm else None
    if c and c["out_of_hours"]:
        add("HOS-OOS", f"Over hours - placed out of service until a {_h(c['reset_len'])} rest", "oos",
            db.setting_f("oos_fine", 1500))
    elif c and c["break"]["over"]:
        add("HOS-BREAK", f"No {c['break_len']}-minute break after {_h(c['break']['limit'])} of driving", "violation", hos_fine)
    recent = db.val("SELECT COUNT(*) FROM hos_violations WHERE user_id=? AND rule LIKE 'HOS-%' AND COALESCE(game,'ats')=? "
                    "AND game_min>=?", (uid, game, (gm or 0) - 8 * 1440), 0) if gm else 0
    if recent:
        add("HOS-LOG", f"{recent} HOS violation(s) in the last 8 days of the log", "warning", 0)
    if db.setting("dvir_required") == "1" and not has_pretrip(uid, truck_id, gm, game):
        if job_id and db.val("SELECT id FROM hos_violations WHERE user_id=? AND rule='DVIR-PRE' AND period_key IN (?, ?)",
                             (uid, f"job{job_id}", f"{game}:job{job_id}")):
            add("DVIR", "No pre-trip inspection report for this truck (already fined when the load started)", "warning", 0)
        else:
            add("DVIR", "No pre-trip inspection report for this truck in the last 24 hours", "violation",
                db.setting_f("dvir_fine", 250))
    # Level 1-2: the vehicle
    if level <= 2:
        defects = open_defects(truck_id)
        if defects:
            add("DEFECT", f"Unrepaired defects reported on a DVIR ({defects[0]['defects'][:80]})", "oos",
                db.setting_f("oos_fine", 1500))
        worst = max(v.get("wear_engine", 0), v.get("wear_transmission", 0), v.get("wear_chassis", 0),
                    v.get("wear_wheels", 0), v.get("wear_cabin", 0))
        if worst > 0.25:
            add("EQUIP-OOS", f"Vehicle condition critical ({worst * 100:.0f}% wear)", "oos", db.setting_f("oos_fine", 1500))
        elif worst > 0.10:
            add("EQUIP", f"Vehicle needs repair ({worst * 100:.0f}% wear)", "warning", 0)
        if v.get("wear_wheels", 0) > 0.15:
            add("TIRES", "Worn tires", "violation", 250)
        if v.get("air_pressure_warning"):
            add("BRAKES", "Low air pressure", "oos", db.setting_f("oos_fine", 1500))
    # Level 1: the load
    if level == 1 and v.get("on_job"):
        limit = 25000 if game == "ets2" else 20400   # kg of cargo before a standard rig goes over 40 t / 80,000 lb
        if v.get("cargo_mass", 0) > limit and not v.get("special_job"):
            over = v["cargo_mass"] - limit
            add("WEIGHT", f"Overweight by about {over * 2.20462:,.0f} lb ({over / 1000:.1f} t) without a permit",
                "violation", round(500 + over * 0.1))
        if v.get("cargo_damage", 0) > 0.05:
            add("SECURE", f"Load securement: cargo damaged {v['cargo_damage'] * 100:.0f}%", "warning", 0)

    result = "oos" if any(f["severity"] == "oos" for f in findings) else \
        "violation" if any(f["severity"] == "violation" for f in findings) else \
        "warning" if findings else "passed"
    fine = sum(f["fine"] for f in findings)
    where = v.get("destination_city") and f"en route to {v['destination_city']}" or ""
    iid = db.insert("inspections", user_id=uid, truck_id=truck_id, job_id=job_id, level=level, result=result,
                    findings=json.dumps(findings), fine=fine, game_min=gm, location=where, created_at=time.time())
    if fine:
        bank.fine(uid, "inspection fine", fine, f"Roadside inspection #{iid}", job_id)
    if job_id:
        db.insert("job_events", job_id=job_id, user_id=uid, kind="inspection", amount=fine,
                  detail=f"Level {level} roadside inspection: {result.upper()}", x=v.get("x"), z=v.get("z"),
                  created_at=time.time())
        if fine:
            db.ex("UPDATE jobs SET fines = fines + ? WHERE id=?", (fine, job_id))
    if any(f["code"] in ("DEFECT", "EQUIP-OOS", "BRAKES") for f in findings) and truck_id:
        db.update("trucks", truck_id, status="maintenance")
    text = {"passed": "passed - clean inspection", "warning": "passed with warnings", "violation": "violations found",
            "oos": "OUT OF SERVICE"}[result]
    db.notify(uid, f"Level {level} roadside inspection: {text}", f"/compliance?tab=inspections",
              "success" if result == "passed" else "error")
    economy.webhook("event", f"🚔 {economy.user_name(uid)} — Level {level} roadside inspection: **{text}**"
                             + (f" (fines {db.setting('currency', '$')}{fine:,.0f})" if fine else ""))
    if fine:
        cdl.company_fine(uid, "oos" if result == "oos" else "compliance", f"Roadside inspection #{iid}", job_id, game, fine)
    return iid


def holds(uid, game="ats"):
    """Reasons a driver can't be dispatched right now."""
    out = [t for t in (cdl.hold_text(uid),) if t]
    if db.setting("hos_enabled") == "1":
        c = clocks(uid, None, game)
        if c and c["out_of_hours"]:
            out.append(f"Out of hours — needs a {_h(c['reset_len'])} rest")
    for t in db.q("SELECT * FROM trucks WHERE assigned_user_id=? AND status='maintenance'", (uid,)):
        out.append(f"{t['brand']} {t['model']} ({t['plate']}) is on a maintenance hold")
    return out
