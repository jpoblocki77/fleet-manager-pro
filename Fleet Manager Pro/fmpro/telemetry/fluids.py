"""Fuel and AdBlue history and low-level alerts for the Fuel & AdBlue page.

Every telemetry frame passes through record(). One reading per driver is saved to the database (fluid_samples)
every SAMPLE_S seconds, and a refuel or AdBlue top-up is saved at once so the jump shows at the right moment.
Readings are kept KEEP_DAYS days, so the graph survives restarts and updates and can show up to a week.

When a truck's fuel or AdBlue drops to the game's own warning level the driver and every Owner and Manager get
one notification (setting fluid_alerts); it re-arms once the tank is refilled above the warning level + REARM_PCT.
"""
import logging
import threading
import time

from .. import db

log = logging.getLogger("fmpro.fluids")

SAMPLE_S = 15                       # one reading every 15 seconds
WINDOW_S = 60 * 60                  # the page opens on the last hour
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 86400}
BARS = 240                          # longer ranges are grouped into this many bars
KEEP_DAYS = 30
REARM_PCT = 5                       # % above the warning level a tank must reach before it can alert again
_last = {}                          # user id -> last saved reading
_alert = {}                         # user id -> {"fuel": bool, "adblue": bool} (alert already sent)
_lock = threading.Lock()
_saved = [0]


def _num(v, key):
    x = v.get(key) or 0
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def record(uid, frame):
    if not uid or not frame:
        return
    v = frame.get("values") or {}
    cap, acap = _num(v, "fuel_capacity"), _num(v, "adblue_capacity")
    if (cap <= 0 and acap <= 0) or (_num(v, "fuel") <= 0.01 and _num(v, "adblue") <= 0.01):   # no truck yet (menus, loading)
        return
    s = {"t": time.time(), "f": round(_num(v, "fuel"), 2), "fc": cap, "a": round(_num(v, "adblue"), 2), "ac": acap}
    with _lock:
        last = _last.get(uid)
        if last and s["t"] < last["t"]:              # clock went backwards (tests, time change)
            last = None
        jump = last and (s["f"] - last["f"] > max(1.0, cap * 0.01) or s["a"] - last["a"] > max(0.5, acap * 0.01))
        save = last is None or s["t"] - last["t"] >= SAMPLE_S or jump
        if save:
            _last[uid] = s
            _saved[0] += 1
    try:
        if save:
            db.insert("fluid_samples", user_id=uid, t=s["t"], game=str(v.get("game") or "")[:8], fuel=s["f"],
                      fuel_cap=cap, adblue=s["a"], adblue_cap=acap)
            if _saved[0] % 500 == 0:
                prune()
        _check_alerts(uid, v, s)
    except Exception:  # noqa: BLE001 - a full disk or locked database must never stop the telemetry
        log.exception("fuel/AdBlue reading for driver #%s not saved", uid)


def prune():
    db.ex("DELETE FROM fluid_samples WHERE t < ?", (time.time() - KEEP_DAYS * 86400,))


def history(uid, seconds=WINDOW_S):
    rows = db.q("SELECT t, fuel, fuel_cap, adblue, adblue_cap FROM fluid_samples WHERE user_id=? AND t >= ? AND t <= ? "
                "ORDER BY t, id", (uid, time.time() - seconds, time.time() + 1))
    return [{"t": r["t"], "f": r["fuel"], "fc": r["fuel_cap"], "a": r["adblue"], "ac": r["adblue_cap"]} for r in rows]


def bars(samples, seconds, now=None):
    """Group samples into at most BARS bars for the graph (each bar = the level at the end of its slot).
    Returns (bars, seconds per bar)."""
    step = max(SAMPLE_S, seconds / BARS)
    if step <= SAMPLE_S:
        return samples, SAMPLE_S
    now = now or time.time()
    slots = {}
    for s in samples:
        slots[int((now - s["t"]) // step)] = s          # later samples overwrite: the slot keeps its last level
    out = [dict(s, t=now - k * step) for k, s in slots.items()]
    out.sort(key=lambda s: s["t"])
    return out, step


def summary(samples):
    """Used fuel / AdBlue over the samples (drops only). A leap down is another truck or save being loaded."""
    out = {"fuel_used": 0.0, "adblue_used": 0.0}
    for a, b in zip(samples, samples[1:]):
        if a["fc"] != b["fc"] or (a["f"] - b["f"] > 5 and b["t"] - a["t"] < 60):
            continue
        out["fuel_used"] += max(0.0, a["f"] - b["f"])
        out["adblue_used"] += max(0.0, a["a"] - b["a"])
    out["fuel_used"], out["adblue_used"] = round(out["fuel_used"], 1), round(out["adblue_used"], 2)
    return out


# ------------------------------------------------------------------------------------------------ alerts
def _check_alerts(uid, v, s):
    state = _alert.setdefault(uid, {})
    for fluid, amount, cap, warn_key, flag in (("fuel", s["f"], s["fc"], "fuel_warning_factor", "fuel_warning"),
                                               ("adblue", s["a"], s["ac"], "adblue_warning_factor", "adblue_warning")):
        if cap <= 0:
            continue
        warn_pct = (_num(v, warn_key) or 0.15) * 100
        pct = amount / cap * 100
        low = pct <= warn_pct or v.get(flag) is True
        first = fluid not in state
        if not low:
            if first or pct >= warn_pct + REARM_PCT:
                state[fluid] = False
            continue
        if state.get(fluid):
            continue
        state[fluid] = True
        if first and _recently_alerted(uid, fluid):      # app restarted while the tank was already low
            continue
        if db.setting("fluid_alerts", "1") == "1":
            _send(uid, fluid, pct, v)


def _label(fluid):
    return "fuel" if fluid == "fuel" else "AdBlue"


def _recently_alerted(uid, fluid):
    return bool(db.val("SELECT 1 FROM notifications WHERE link=? AND text LIKE ? AND created_at > ? LIMIT 1",
                       (f"/fuel?user={uid}", f"Low {_label(fluid)}:%", time.time() - 6 * 3600)))


def _send(uid, fluid, pct, v):
    u = db.one("SELECT id, display_name FROM users WHERE id=?", (uid,))
    if not u:
        return
    truck = f"{v.get('truck_brand') or ''} {v.get('truck_name') or ''}".strip()
    what = "Refuel soon." if fluid == "fuel" else "Top up AdBlue soon."
    text = f"Low {_label(fluid)}: {u['display_name']} is down to {pct:.0f}%" + (f" in the {truck}" if truck else "") \
        + f". {what}"
    who = {uid} | {m["id"] for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'")}
    for m in sorted(who):
        db.notify(m, text, f"/fuel?user={uid}", "warning")
    log.info("low %s alert for driver #%s (%.0f%%)", fluid, uid, pct)


def clear():
    """Forget the in-memory state (tests)."""
    with _lock:
        _last.clear()
        _alert.clear()
