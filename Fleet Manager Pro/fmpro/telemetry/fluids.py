"""Fuel and AdBlue history for the Fuel & AdBlue page.

Every telemetry frame passes through record(); one sample per driver is kept every SAMPLE_S seconds for the
last WINDOW_S seconds, in memory only (a restart starts a fresh hour). A refuel or AdBlue top-up is recorded at
once, so the jump shows on the graph at the right moment.
"""
import threading
import time
from collections import deque

SAMPLE_S = 15                       # one bar every 15 seconds
WINDOW_S = 60 * 60                  # the graph shows the last hour
_KEEP = WINDOW_S // SAMPLE_S + 2
_hist = {}                          # user id -> deque of samples
_lock = threading.Lock()


def _num(v, key):
    x = v.get(key) or 0
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def record(uid, frame):
    if not uid or not frame:
        return
    v = frame.get("values") or {}
    cap, acap = _num(v, "fuel_capacity"), _num(v, "adblue_capacity")
    if cap <= 0 and acap <= 0:                       # no truck yet (menus, loading)
        return
    s = {"t": time.time(), "f": round(_num(v, "fuel"), 2), "fc": cap, "a": round(_num(v, "adblue"), 2), "ac": acap}
    with _lock:
        d = _hist.setdefault(uid, deque(maxlen=_KEEP))
        last = d[-1] if d else None
        if last and s["t"] < last["t"]:              # clock went backwards (tests, time change): start over
            d.clear()
            last = None
        jump = last and (s["f"] - last["f"] > max(1.0, cap * 0.01) or s["a"] - last["a"] > max(0.5, acap * 0.01))
        if last is None or s["t"] - last["t"] >= SAMPLE_S or jump:
            d.append(s)


def history(uid):
    cut = time.time() - WINDOW_S
    with _lock:
        return [dict(s) for s in _hist.get(uid, ()) if s["t"] >= cut]


def summary(samples):
    """Used fuel / AdBlue over the samples (drops only) and how many refuels / top-ups happened."""
    out = {"fuel_used": 0.0, "adblue_used": 0.0, "refuels": 0, "topups": 0}
    for a, b in zip(samples, samples[1:]):
        df, da = b["f"] - a["f"], b["a"] - a["a"]
        if df < 0:
            out["fuel_used"] += -df
        elif df > max(1.0, b["fc"] * 0.01):
            out["refuels"] += 1
        if da < 0:
            out["adblue_used"] += -da
        elif da > max(0.5, b["ac"] * 0.01):
            out["topups"] += 1
    out["fuel_used"], out["adblue_used"] = round(out["fuel_used"], 1), round(out["adblue_used"], 2)
    return out


def clear():
    with _lock:
        _hist.clear()
