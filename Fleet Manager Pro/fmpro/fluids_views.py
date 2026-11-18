"""Fuel & AdBlue: live tank levels and a bar graph of the last hour, per driver.

  /fuel              the page (drivers' picker like the Live Cockpit)
  /api/fluids        JSON the page polls every 2 seconds: current levels + the hour's samples
"""
import time

from flask import Blueprint, jsonify, render_template, request

from . import db
from .auth import current_units, login_required, user
from .telemetry import fluids
from .telemetry.service import HUB, local_user

bp = Blueprint("fluids", __name__)

_LIVE_KEYS = ("fuel", "fuel_capacity", "fuel_pct", "fuel_warning", "fuel_warning_factor", "fuel_range",
              "fuel_avg_consumption", "adblue", "adblue_capacity", "adblue_pct", "adblue_warning",
              "adblue_warning_factor", "speed_kmh", "game", "truck_brand", "truck_name", "paused", "engine_enabled")


def _uid():
    a = request.args.get("user", "")
    return int(a) if a.isdigit() else (local_user() or user()["id"])


@bp.route("/fuel")
@login_required
def page():
    drivers = db.q("SELECT id, display_name FROM users WHERE status!='removed' ORDER BY display_name")
    return render_template("fluids.html", drivers=drivers, me=local_user() or user()["id"],
                           window_min=fluids.WINDOW_S // 60, sample_s=fluids.SAMPLE_S)


@bp.route("/api/fluids")
@login_required
def data():
    uid = _uid()
    e = HUB.get(uid)
    hist = fluids.history(uid)
    out = {"user_id": uid, "online": bool(e), "units": current_units(), "server_time": time.time(),
           "window_s": fluids.WINDOW_S, "sample_s": fluids.SAMPLE_S, "history": hist,
           "summary": fluids.summary(hist)}
    if e:
        v = e["frame"]["values"]
        out["live"] = {k: v.get(k) for k in _LIVE_KEYS}
    return jsonify(out)
