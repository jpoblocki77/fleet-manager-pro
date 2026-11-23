"""Fuel & AdBlue: live tank levels and a bar graph of the last hour, per driver.

  /fuel              the page (drivers' picker like the Live Cockpit)
  /api/fluids        JSON the page polls every 2 seconds: current levels + the bars for the chosen range
                     (?range=1h | 6h | 24h | 7d, readings come from the database so they survive restarts)
"""
import time

from flask import Blueprint, jsonify, render_template, request

from . import db, fuel_prices
from .auth import current_units, login_required, user
from .telemetry import fluids
from .telemetry.service import HUB, local_user

bp = Blueprint("fluids", __name__)
RANGE_LABELS = {"1h": "Last hour", "6h": "Last 6 hours", "24h": "Last 24 hours", "7d": "Last 7 days"}

_LIVE_KEYS = ("fuel", "fuel_capacity", "fuel_pct", "fuel_warning", "fuel_warning_factor", "fuel_range",
              "fuel_avg_consumption", "adblue", "adblue_capacity", "adblue_pct", "adblue_warning",
              "adblue_warning_factor", "speed_kmh", "game", "truck_brand", "truck_name", "paused", "engine_enabled")


def _uid():
    a = request.args.get("user", "")
    return int(a) if a.isdecimal() else (local_user() or user()["id"])


@bp.route("/fuel")
@login_required
def page():
    drivers = db.q("SELECT id, display_name FROM users WHERE status!='removed' ORDER BY display_name")
    want = request.args.get("user", "")
    me = int(want) if want.isdecimal() and any(d["id"] == int(want) for d in drivers) else (local_user() or user()["id"])
    rng = request.args.get("range") if request.args.get("range") in fluids.RANGES else "1h"
    return render_template("fluids.html", drivers=drivers, me=me, ranges=RANGE_LABELS, rng=rng,
                           sample_s=fluids.SAMPLE_S)


@bp.route("/api/fluids")
@login_required
def data():
    uid = _uid()
    e = HUB.get(uid)
    rng = request.args.get("range") if request.args.get("range") in fluids.RANGES else "1h"
    seconds = fluids.RANGES[rng]
    now = time.time()
    hist = fluids.history(uid, seconds)
    shown, step = fluids.bars(hist, seconds, now)
    out = {"user_id": uid, "online": bool(e), "units": current_units(), "server_time": now, "range": rng,
           "window_s": seconds, "sample_s": step, "history": shown, "summary": {**fluids.summary(hist), **fuel_prices.totals(uid, seconds)},
           "refuel_log": fuel_prices.fills(uid, seconds)}
    if e:
        v = e["frame"]["values"]
        out["live"] = {k: v.get(k) for k in _LIVE_KEYS}
        price, place, source = fuel_prices.diesel(v.get("game"), v.get("x") or 0, v.get("z") or 0)
        out["prices"] = {"fuel": price, "place": place, "source": source, "adblue": fuel_prices.adblue()}
    return jsonify(out)
