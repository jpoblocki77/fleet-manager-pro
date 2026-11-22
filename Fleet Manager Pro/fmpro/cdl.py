"""Driver's licences (CDL): 12 points per driver, suspension at zero, and the drivers test that earns the licence.

Not to be confused with licensing.py, which is the purchase licence of the program itself.

  * A licence starts with 12 points. Every fine, accident and 5 minutes of speeding costs points (OFFENCES below)
    and a ticket the driver pays. Speeding counts in REAL minutes, not in-game ones.
  * Points come back one per real day (one clock per driver: the next point comes back a day after the last one).
  * At zero the licence is suspended. Once every point is back - 12 days, or straight away by paying the fine from the
    driver's own bank account - the driver retakes the drivers test (cdl_test.py) and is licensed again.
  * Without a licence a driver can't claim contracts, and loads they start are held for a manager (Settings).

Everything is off until the Owner or a Manager turns it on, so an update never locks a company's drivers out.
"""
import json
import logging
import threading
import time

from . import bank, cdl_test, db, economy

log = logging.getLogger("fmpro.cdl")
MAX_POINTS = 12
DAY = 86400
_lock = threading.RLock()            # one writer at a time: the telemetry threads and the pages both change licences

SCHEMA = """
CREATE TABLE IF NOT EXISTS licences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id), status TEXT NOT NULL DEFAULT 'none',
    used INTEGER NOT NULL DEFAULT 0, tick_at REAL, speed_s REAL NOT NULL DEFAULT 0,
    issued_at REAL, suspended_at REAL, how TEXT DEFAULT '', clear_told INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS licence_points (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), code TEXT NOT NULL, points INTEGER NOT NULL,
    detail TEXT DEFAULT '', ticket REAL NOT NULL DEFAULT 0, job_id INTEGER, game TEXT DEFAULT 'ats',
    created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_licence_points ON licence_points(user_id, created_at);
CREATE TABLE IF NOT EXISTS licence_tests (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'active',
    game TEXT DEFAULT 'ats', score INTEGER DEFAULT 100, reason TEXT DEFAULT '', state TEXT NOT NULL,
    started_at REAL NOT NULL, finished_at REAL);
CREATE INDEX IF NOT EXISTS ix_licence_tests ON licence_tests(user_id, status);
"""

# what an offence costs: code -> (points, ticket, text). The game's own fines use the game's offence names. Speeding is
# priced by how far over the limit it was (SPEED_BANDS); None means exactly that. 0 = points only, because the program
# has already fined that one (hours of service, inspections).
OFFENCES = {
    "serious": (4, 400, "Serious accident"),
    "accident": (2, 200, "Accident"),
    "crash": (2, 200, "Accident (fined by the game)"),
    "red_signal": (3, 200, "Running a red light"),
    "wrong_way": (3, 250, "Driving on the wrong side of the road"),
    "illegal_border_crossing": (3, 300, "Illegal border crossing"),
    "speeding": (None, None, "Speeding (caught by police)"),
    "speeding_camera": (None, None, "Speeding (caught by a camera)"),
    "speeding_time": (1, None, "Speeding for too long"),
    "hard_shoulder_violation": (2, 100, "Driving on the hard shoulder"),
    "avoid_sleeping": (2, 100, "Driving while tired"),
    "avoid_weighting": (2, 150, "Skipping a weigh station"),
    "avoid_inspection": (2, 150, "Skipping an inspection"),
    "illegal_trailer": (2, 150, "Pulling an illegal trailer"),
    "damaged_vehicle_usage": (2, 150, "Driving an unsafe vehicle"),
    "oos": (2, 0, "Put out of service"),
    "no_lights": (1, 60, "Driving without lights"),
    "compliance": (1, 0, "Hours of service or inspection fine"),
    "other": (1, 60, "Any other fine"),
}
SPEED_BANDS = [   # km/h over the limit, up to; points when the game catches it; ticket
    (10, 1, 50), (20, 1, 120), (30, 2, 200), (40, 2, 320), (60, 3, 550), (1e9, 3, 800)]
SERIOUS_TRUCK, SERIOUS_LOAD = 0.05, 0.15      # damage in one go that makes an accident a serious one
SIMULATED = set()       # drivers whose telemetry is the built-in simulator right now
CRASH_WINDOW = 15                   # seconds: the damage, the fine and a second bump in the same accident count once


def init():
    db.conn().executescript(SCHEMA)
    db.conn().commit()


def enabled():
    return db.setting("licence_enabled", "0") == "1"


def fine_cost():
    return max(0.0, db.setting_f("licence_fine", 5000))


def speed_minutes():
    return max(1.0, db.setting_f("licence_speed_min", 5))


# ------------------------------------------------------------------------------------------------ the licence
def _row(uid):
    r = db.one("SELECT * FROM licences WHERE user_id=?", (uid,))
    if not r:
        db.ex("INSERT OR IGNORE INTO licences(user_id) SELECT id FROM users WHERE id=?", (uid,))
        r = db.one("SELECT * FROM licences WHERE user_id=?", (uid,))
    return r


def _save(uid, **cols):
    db.ident(*cols)
    sets = ",".join(f"{k}=?" for k in cols)
    db.ex(f"UPDATE licences SET {sets} WHERE user_id=?", tuple(cols.values()) + (uid,), _cols=list(cols))  # nosec B608  # column names are fixed in the code; every value is a ? parameter


def refresh(uid, now=None):
    """The driver's licence row, with the points that came back since the last look taken off. None: no such driver."""
    now = now or time.time()
    with _lock:
        r = _row(uid)
        if not r:
            return None
        if r["used"] > 0 and r["tick_at"]:
            days = int((now - r["tick_at"]) // DAY)
            if days > 0:
                r["used"] = max(0, r["used"] - days)
                r["tick_at"] = r["tick_at"] + days * DAY if r["used"] else None
                _save(uid, used=r["used"], tick_at=r["tick_at"])
                if not r["used"] and r["status"] == "suspended" and not r["clear_told"]:
                    _save(uid, clear_told=1)
                    db.notify(uid, "All your licence points are back. Take the drivers test to get your licence back.",
                              "/cdl", "success")
        return r


def process(now=None):
    """Every few minutes: points that come back while nobody is looking (so the driver hears about it)."""
    for r in db.q("SELECT user_id FROM licences WHERE used > 0"):
        refresh(r["user_id"], now)


def info(uid, now=None):
    """Everything a page needs to know about one driver's licence."""
    now = now or time.time()
    r = refresh(uid, now) or {"status": "none", "used": 0, "tick_at": None, "issued_at": None, "suspended_at": None, "how": ""}
    test = active_test(uid)
    out = {"status": r["status"], "used": r["used"], "left": MAX_POINTS - r["used"] if r["status"] == "valid" else 0,
           "next_at": (r["tick_at"] + DAY) if r["used"] and r["tick_at"] else None,
           "clear_at": (r["tick_at"] + DAY * r["used"]) if r["used"] and r["tick_at"] else None,
           "issued_at": r["issued_at"], "suspended_at": r["suspended_at"], "how": r["how"] or "", "test": test,
           "number": f"FMP-{uid:05d}"}
    last = db.one("SELECT status, finished_at FROM licence_tests WHERE user_id=? AND status!='active' ORDER BY id DESC LIMIT 1",
                  (uid,))
    wait = db.setting_f("licence_retest_hours", 0) * 3600
    until = last["finished_at"] + wait if last and last["status"] == "failed" else 0
    out["retest_at"] = until if wait and until > now else None
    out["can_test"] = r["status"] != "valid" and not r["used"] and not test and not out["retest_at"]
    return out


def licensed(uid, now=None):
    r = refresh(uid, now)
    return bool(r and r["status"] == "valid")


def blocked(uid):
    """True when this driver may not be dispatched or paid without a manager: no valid licence and the company cares."""
    return enabled() and db.setting("licence_block", "1") == "1" and not licensed(uid)


def hold_text(uid):
    """One line for the dispatch holds, or None."""
    if not blocked(uid):
        return None
    r = refresh(uid)
    if r and r["status"] == "suspended":
        return "Licence suspended — " + ("retake the drivers test" if not r["used"] else "points still to clear, then a new test")
    return "No driver's licence — pass the drivers test"


def _managers():
    return [m["id"] for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'")]


def band(over_kmh):
    """(points, ticket) for speeding this far over the limit."""
    return next((p, t) for top, p, t in SPEED_BANDS if over_kmh <= top)


def price(code, over_kmh=0):
    """(points, ticket) an offence costs."""
    pts, ticket, _text = OFFENCES[code]
    b = band(over_kmh)
    return (b[0] if pts is None else pts), (b[1] if ticket is None else ticket)


def offence(uid, code, detail="", job_id=None, game="ats", over_kmh=0, now=None, fined=0):
    """Book an offence: points on the licence and a ticket the driver pays. Returns the points it cost (0 for a driver
    without a licence: there is nothing to take points from, but the ticket still comes). `fined`: the fine the driver
    has already paid for it, so there is no ticket on top."""
    if not enabled():
        return 0
    now = now or time.time()
    code = code if code in OFFENCES else "other"
    text = OFFENCES[code][2]
    pts, ticket = price(code, over_kmh)
    if fined or db.setting("licence_tickets", "1") != "1":
        ticket = 0
    cur = db.setting("currency", "$")
    with _lock:
        r = refresh(uid, now)
        if not r:
            return 0
        if r["status"] == "none":
            pts = 0
        if not pts and not ticket and not fined:
            return 0
        db.insert("licence_points", user_id=uid, code=code, points=pts, detail=(detail or "")[:160], ticket=ticket or fined,
                  job_id=job_id, game=game if game in ("ats", "ets2") else "ats", created_at=now)
        if ticket:
            economy.post("traffic ticket", -ticket, (f"{text} ({detail})" if detail else text)[:200], account="driver",
                         user_id=uid, job_id=job_id)
        paid = f" Ticket: {cur}{ticket:,.0f}." if ticket else f" Fine: {cur}{fined:,.0f}." if fined else ""
        used = min(MAX_POINTS, r["used"] + pts)
        if pts:
            _save(uid, used=used, tick_at=r["tick_at"] or now, clear_told=0)
        log.info("driver #%s: %s point(s), ticket %s for %s (%s) - %s left", uid, pts, ticket, code, detail, MAX_POINTS - used)
        what = f"{text}." + (f" {pts} licence point{'s' if pts != 1 else ''}." if pts else "") + paid
        if r["status"] == "valid":
            what += f" {MAX_POINTS - used} of {MAX_POINTS} points left."
        elif pts:
            what += " Your licence stays suspended for longer."
        db.notify(uid, what, "/cdl", "error")
        if r["status"] == "valid" and used >= MAX_POINTS:
            _suspend(uid, now, "ran out of points")
    return pts


def _suspend(uid, now, why):
    _save(uid, status="suspended", suspended_at=now, speed_s=0)
    name = economy.user_name(uid)
    db.notify(uid, f"Your licence is SUSPENDED: you {why}. Wait for your points or pay the fine, then retake the "
                   "drivers test.", "/cdl", "error")
    for m in _managers():
        if m != uid:
            db.notify(m, f"{name}'s licence is suspended ({why}).", "/cdl", "warning")
    economy.webhook("event", f"⛔ **{name}** lost their licence: {why}.")
    log.info("driver #%s: licence suspended (%s)", uid, why)


def issue(uid, how, now=None):
    """A fresh licence with all its points."""
    now = now or time.time()
    with _lock:
        if not refresh(uid, now):
            return False
        _save(uid, status="valid", used=0, tick_at=None, speed_s=0, issued_at=now, suspended_at=None, how=how[:80], clear_told=0)
    log.info("driver #%s: licence issued (%s)", uid, how)
    return True


def suspend(uid, why, now=None):
    """A manager takes the licence away: all points gone."""
    now = now or time.time()
    with _lock:
        if not licensed(uid, now):
            return False
        _save(uid, used=MAX_POINTS, tick_at=now, clear_told=0)
        _suspend(uid, now, why)
    return True


def clear_points(uid):
    with _lock:
        r = refresh(uid)
        if not r or not r["used"]:
            return False
        _save(uid, used=0, tick_at=None)
    return True


def pay_fine(uid):
    """The driver pays to get every point back now. Returns (ok, message). Once only, however often it is pressed."""
    cur = db.setting("currency", "$")
    with _lock:
        r = refresh(uid)
        if not r or not r["used"]:
            return False, "There are no points on your licence to clear."
        cost = fine_cost()
        if economy.balance("driver", uid) < cost:
            return False, f"You need {cur}{cost:,.0f} in your bank account to pay the fine; you have {cur}{economy.balance('driver', uid):,.0f}."
        economy.post("licence fine", -cost, f"Licence fine: {r['used']} point(s) cleared", account="driver", user_id=uid)
        _save(uid, used=0, tick_at=None)
        db.audit(uid, "licence fine paid", f"{cost:.0f}, {r['used']} point(s) cleared")
    if r["status"] == "suspended":
        return True, f"Fine paid: {cur}{cost:,.0f}. Your points are cleared. Now retake the drivers test to get your licence back."
    return True, f"Fine paid: {cur}{cost:,.0f}. You have all {MAX_POINTS} points again."


# ------------------------------------------------------------------------------------------------ the drivers test
def _test_row(uid):
    return db.one("SELECT * FROM licence_tests WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1", (uid,))


def active_test(uid):
    r = _test_row(uid)
    if not r:
        return None
    try:
        st = json.loads(r["state"])
    except ValueError:
        return None
    return dict(st, id=r["id"], started_at=r["started_at"])


def start_test(uid, v, demo=False):
    """Begin the drivers test with the live frame `v`. Returns (ok, message)."""
    if not enabled():
        return False, "Driver's licences are turned off in this company."
    if not v:
        return False, "Start the game and sit in your truck first: the test is marked from live telemetry."
    if demo:
        return False, "The drivers test needs the real game, not the simulator."
    if v.get("game") not in ("ats", "ets2") or not v.get("truck_id") or not v.get("game_time_abs"):
        return False, "No truck seen yet. Load your profile in the game, then try again."
    if abs(v.get("speed") or 0) > 0.5:
        return False, "Stop the truck first. The test starts parked."
    with _lock:
        lic = info(uid)
        if lic["status"] == "valid":
            return False, "You already have a licence."
        if lic["used"]:
            return False, "Your points have to clear first: wait for them or pay the fine."
        if lic["test"]:
            return False, "A drivers test is already running."
        if lic["retest_at"]:
            return False, "You failed the test a short while ago. You have to wait before you can try again."
        db.insert("licence_tests", user_id=uid, game=v["game"], state=json.dumps(cdl_test.new(v)), started_at=time.time())
    db.notify(uid, "Drivers test started: pre-trip check. Follow the steps on the CDL page.", "/cdl", "info")
    return True, "The drivers test has started. Begin with the pre-trip check."


def cancel_test(uid):
    r = _test_row(uid)
    if not r:
        return False
    db.ex("UPDATE licence_tests SET status='cancelled', reason='Cancelled', finished_at=? WHERE id=? AND status='active'",
          (time.time(), r["id"]))
    return True


def _store(tid, st):
    db.ex("UPDATE licence_tests SET state=?, score=? WHERE id=? AND status='active'", (json.dumps(st), st["score"], tid))


def _finish(uid, tid, st, now):
    passed = st["stage"] == "passed"
    with _lock:
        if not db.val("SELECT id FROM licence_tests WHERE id=? AND status='active'", (tid,)):
            return                                  # cancelled on the page a moment ago
        db.ex("UPDATE licence_tests SET status=?, score=?, reason=?, state=?, finished_at=? WHERE id=?",
              (st["stage"], st["score"], st["reason"], json.dumps(st), now, tid))
        if passed:
            issue(uid, f"Passed the drivers test ({st['score']}/100)", now)
    name = economy.user_name(uid)
    if passed:
        db.notify(uid, f"You PASSED the drivers test with {st['score']}/100. Your licence has {MAX_POINTS} points.", "/cdl", "success")
        economy.webhook("event", f"🪪 **{name}** passed the drivers test ({st['score']}/100).")
    elif st["stage"] == "cancelled":
        db.notify(uid, f"Drivers test stopped: {st['reason']} It doesn't count as a failure.", "/cdl", "warning")
    else:
        db.notify(uid, f"Drivers test failed: {st['reason']} You can try again.", "/cdl", "error")
    log.info("driver #%s: drivers test %s (%s/100) %s", uid, st["stage"], st["score"], st["reason"])


# ------------------------------------------------------------------------------------------------ telemetry
class Watcher:
    """Fed every frame by the driver's JobTracker: accidents, time spent speeding, and the running drivers test."""

    def __init__(self, uid):
        self.uid = uid
        self.prev = None
        self.prev_t = None
        self.on, self.on_t = False, 0
        self.tol = 5.0
        self.lic, self.lic_t = None, 0          # cached licence status
        self.speed_s, self.speed_t = None, 0
        self.refunded = None
        self.over_sum = self.over_t = 0.0       # how far over the limit, for the ticket
        self.crash_t = 0                        # last accident or crash fine
        self.test_id, self.test, self.test_t, self.saved_t = None, None, 0, 0

    def _refresh(self, now):
        if now - self.on_t >= 5 or now < self.on_t:
            self.on, self.on_t = enabled(), now
            self.tol = db.setting_f("speeding_tolerance_kmh", 5)
        if not self.on:
            return
        if now - self.lic_t >= 3 or now < self.lic_t:
            r = refresh(self.uid, now)
            self.lic, self.lic_t = (r or {}).get("status"), now
            if r and self.speed_s is None:
                self.speed_s = r["speed_s"] or 0
        if now - self.test_t >= 2 or now < self.test_t:
            self.test_t = now
            row = _test_row(self.uid)
            if not row:
                self.test_id = self.test = None
            elif row["id"] != self.test_id:
                self.test_id, self.test = row["id"], json.loads(row["state"])

    def reset(self):
        """The game closed or the connection dropped."""
        if self.test:
            _store(self.test_id, self.test)
        self.prev = self.prev_t = None

    def feed(self, v, job_id, now, demo=False):
        (SIMULATED.add if demo else SIMULATED.discard)(self.uid)
        self._refresh(now)
        if not self.on or demo:
            self.prev = None
            return
        prev, dt = self.prev, (now - self.prev_t) if self.prev_t else 0
        if v["paused"] or v["game"] not in ("ats", "ets2"):
            self.prev, self.prev_t = None, now
            return
        self.prev, self.prev_t = v, now
        hit = self._accident(prev, v, dt, now)
        if self.test:
            before = (self.test["stage"], self.test["score"])
            cdl_test.step(self.test, v, prev, dt, now, self.tol, accident=hit and hit[1])
            self._after_step(before, now)
            return
        if hit:
            offence(self.uid, *hit, job_id, v["game"], now=now)
        self._speeding(v, dt, job_id, now)

    def _after_step(self, before, now):
        st = self.test
        if st["stage"] in cdl_test.OVER:
            tid, self.test, self.test_id = self.test_id, None, None
            self.lic_t = 0
            _finish(self.uid, tid, st, now)
        elif st["stage"] != before[0]:
            _store(self.test_id, st)
            db.notify(self.uid, cdl_test.STAGE_NEWS[st["stage"]], "/cdl", "info")
        elif st["score"] != before[1] or now - self.saved_t >= 2:
            self.saved_t = now
            _store(self.test_id, st)

    def _accident(self, prev, v, dt, now):
        """(offence, what was damaged) when the truck hit something between these two frames, or None. A save that loads, a
        different truck, a teleport or a ferry also changes the damage, so those never count."""
        if not prev or dt <= 0 or dt > 3 or (prev["truck_id"], prev["license_plate"]) != (v["truck_id"], v["license_plate"]):
            return None
        if abs(prev["speed"]) < 1.0 or v["ferry"] or v["train"] or prev["game"] != v["game"]:
            return None
        if abs(v["x"] - prev["x"]) > 150 or abs(v["z"] - prev["z"]) > 150 or v["game_time_abs"] < prev["game_time_abs"]:
            return None
        worse = max(v[k] - prev[k] for k in ("wear_cabin", "wear_chassis", "wear_engine"))
        load = v["cargo_damage"] - prev["cargo_damage"] if v["on_job"] and prev["on_job"] and v["cargo_id"] == prev["cargo_id"] else 0
        if worse < 0.01 and load < 0.03:
            return None
        again, self.crash_t = now - self.crash_t < CRASH_WINDOW, now
        if again:
            return None
        bad = worse >= SERIOUS_TRUCK or load >= SERIOUS_LOAD
        return ("serious" if bad else "accident",
                f"truck damage +{worse * 100:.1f}%" if worse >= 0.01 else f"load damage +{load * 100:.1f}%")

    def _speeding(self, v, dt, job_id, now):
        if self.speed_s is None:
            return
        over = abs(v["speed_kmh"]) - v["speed_limit_kmh"]
        if v["speed_limit_kmh"] > 1 and over > self.tol:
            dt = min(2.0, max(0.0, dt))
            self.speed_s += dt
            self.over_sum += over * dt
            self.over_t += dt
            need = speed_minutes() * 60
            if self.speed_s >= need:
                self.speed_s -= need
                avg = self.over_sum / self.over_t if self.over_t else over
                self.over_sum = self.over_t = 0.0
                self.speed_t = 0
                offence(self.uid, "speeding_time", f"{need / 60:g} minutes", job_id, v["game"], avg, now)
        if now - self.speed_t >= 10 or now < self.speed_t:
            self.speed_t = now
            _save(self.uid, speed_s=round(self.speed_s, 1))

    def fine(self, v, job_id, now, demo=False):
        """The game fined the driver."""
        self._refresh(now)
        if not self.on or demo:
            return
        code = v["fine_offence"] if v["fine_offence"] in OFFENCES else "other"
        fined = float(v["fine_amount"]) if bank.driver_pays(self.uid) else 0
        if self.test:
            before = (self.test["stage"], self.test["score"])
            cdl_test.fined(self.test, code, OFFENCES[code][2])
            self._after_step(before, now)
            return
        if code == "crash":                       # the same accident may already be booked from the damage
            again, self.crash_t = now - self.crash_t < CRASH_WINDOW, now
            if again:
                if fined:
                    self._refund(now)
                return
        over = max(0.0, abs(v["speed_kmh"]) - v["speed_limit_kmh"]) if v["speed_limit_kmh"] > 1 else 0.0
        offence(self.uid, code, "", job_id, v["game"], over, now, fined)

    def _refund(self, now):
        """The game fined the accident the damage was already ticketed for: the driver pays the fine, not both."""
        r = db.one("SELECT id, ticket, job_id FROM licence_points WHERE user_id=? AND code IN ('accident','serious') AND ticket>0 "
                   "AND created_at>? ORDER BY id DESC LIMIT 1", (self.uid, now - CRASH_WINDOW * 4))
        if r and r["id"] != self.refunded and db.setting("licence_tickets", "1") == "1":
            self.refunded = r["id"]
            economy.post("traffic ticket", r["ticket"], "Ticket refunded: the game fined this accident", account="driver",
                         user_id=self.uid, job_id=r["job_id"])
            db.ex("UPDATE licence_points SET ticket=0 WHERE id=?", (r["id"],))


def company_fine(uid, code, detail, job_id, game, amount=0):
    """A fine from the logbook or a roadside inspection, already charged: points, no ticket. Never breaks the caller."""
    if uid in SIMULATED:
        return
    try:
        offence(uid, code, detail, job_id, game, fined=amount if bank.driver_pays(uid) else 0)
    except Exception:  # noqa: BLE001
        log.exception("driver #%s: points for %s could not be booked", uid, code)
