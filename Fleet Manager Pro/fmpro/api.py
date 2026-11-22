"""JSON API: live telemetry, remote agents, overlay, notifications, exports."""
import csv
import logging
import io
import math
import time

from flask import Blueprint, Response, abort, jsonify, request

from . import db
from flask import g

from .auth import current_units, game_scope, login_required, units_for, user
from .telemetry import reader
from .telemetry.layout import FIELDS, GAMES, MMF_SIZE, TRAILER_FIELDS
from .telemetry.service import HUB, local_user

log = logging.getLogger("fmpro.api")

bp = Blueprint("api", __name__, url_prefix="/api")

_KINDS = {key: (kind, count) for key, _g, kind, _o, count in FIELDS}
_TRAILER_KINDS = {key: (kind, count) for key, kind, _o, count in TRAILER_FIELDS}


def _coerce(kind, v):
    """One telemetry value from an untrusted agent, forced to the type the DLL would give."""
    if kind == "bool":
        return v is True
    if kind == "str":
        return v[:64] if isinstance(v, str) else ""
    if kind in ("f32", "f64"):
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) < 1e15
        return float(v) if ok else 0.0
    ok = type(v) is int and -2 ** 63 <= v < 2 ** 64
    return v if ok else 0


def _clean_fields(src, kinds):
    out = {}
    for key, (kind, count) in kinds.items():
        v = src.get(key)
        if kind != "str" and count > 1:
            out[key] = [_coerce(kind, x) for x in v[:count]] if isinstance(v, list) else []
        else:
            out[key] = _coerce(kind, v)
    return out


def clean_agent_frame(frame):
    """Rebuild a frame from an agent using only known fields and types; derived values are recomputed
    here, so nothing an agent sends can reach a page as text it chose (or as NaN / oversized data)."""
    vals = _clean_fields(frame["values"], _KINDS)
    trailers = []
    for t in [t for t in frame.get("trailers", []) if isinstance(t, dict)][:10]:
        ct = _clean_fields(t, _TRAILER_KINDS)
        ct["slot"] = len(trailers)
        trailers.append(ct)
    vals["game"] = GAMES.get(vals["game_id"], "unknown")
    vals.update(reader.derive(vals, trailers))
    subs = [s[:64] for s in frame.get("substances", []) if isinstance(s, str)][:25]
    return {"values": vals, "trailers": trailers, "substances": subs}


def _csv_cell(v):
    """Stop spreadsheet programs running a cell as a formula (text starting with = + - @)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _job_info(uid):
    j = db.one("""SELECT id, cargo, source_city, destination_city, driven_km, planned_km, fuel_used, tolls, fines,
                  max_speed, speeding_s, driving_s, income, contract_id, started_at FROM jobs
                  WHERE user_id=? AND status='in_progress' ORDER BY id DESC LIMIT 1""", (uid,))
    return j


def _frame_payload(uid, full=True):
    e = HUB.get(uid)
    out = {"user_id": uid, "online": bool(e), "status": HUB.local_status if uid == local_user() else
           ("streaming" if e else "offline"), "units": current_units(),
           "currency": db.setting("currency", "$"), "server_time": time.time()}
    if e:
        f = e["frame"]
        out.update(source=e["source"], age=round(time.time() - e["t"], 2), values=f["values"])
        if full:
            out.update(trailers=f.get("trailers", []), substances=f.get("substances", []))
        out["job"] = _job_info(uid)
    return out


def _uid_arg():
    a = request.args.get("user", "")
    return int(a) if a.isdecimal() else (local_user() or user()["id"])


@bp.route("/live")
@login_required
def live():
    return jsonify(_frame_payload(_uid_arg()))


@bp.route("/live/all")
@login_required
def live_all():
    out = []
    want = request.args.get("game") if request.args.get("game") in ("ats", "ets2") else game_scope()
    for uid, e in HUB.all_live().items():
        v = e["frame"]["values"]
        if want and v["game"] != want:
            continue
        u = db.one("SELECT id, display_name, color FROM users WHERE id=?", (uid,))
        if not u:
            continue
        job = _job_info(uid)
        trail = db.q("SELECT x, z FROM job_points WHERE job_id=? ORDER BY t", (job["id"],)) if job else []
        out.append({"user": u, "x": v["x"], "z": v["z"], "heading": v["heading"], "speed_kmh": v["speed_kmh"],
                    "truck": f"{v['truck_brand']} {v['truck_name']}".strip(), "game": v["game"],
                    "job": job, "trail": trail, "source": e["source"]})
    return jsonify({"drivers": out, "units": current_units()})


@bp.route("/map")
@login_required
def map_data():
    """Learned map layers for one game: roads that were driven and places that were visited."""
    game = request.args.get("game")
    if game not in ("ats", "ets2"):
        game = game_scope() or db.setting("default_game", "ats")
    pts = db.q("SELECT x, z FROM road_points WHERE game=? ORDER BY id DESC LIMIT 40000", (game,))
    pts.reverse()
    roads, cur, last = [], [], None
    for p in pts:                               # consecutive points close together form one stretch of road
        if last and math.hypot(p["x"] - last["x"], p["z"] - last["z"]) > 1500:
            if len(cur) > 2:
                roads.append(cur)
            cur = []
        cur += [p["x"], p["z"]]
        last = p
    if len(cur) > 2:
        roads.append(cur)
    places = db.q("SELECT name n, x, z, visits FROM map_places WHERE game=? ORDER BY visits DESC LIMIT 2000", (game,))
    return jsonify(game=game, roads=roads, places=places, road_points=len(pts))


@bp.route("/overlay")
def overlay():
    key = request.args.get("key", "")
    uid = None
    if user():
        uid = _uid_arg()
    elif key:
        uid = db.val("SELECT id FROM users WHERE agent_token=? AND status!='removed'", (key,))
        if uid:   # OBS overlay without a login: use that driver's game choice for units
            g.units_override = units_for(db.val("SELECT game_pref FROM users WHERE id=?", (uid,)) or "both")
    if not uid:
        abort(401)
    out = _frame_payload(uid, full=False)
    out["hud"] = _overlay_extras(uid, out.get("values"))
    from . import overlay_fields
    cfg = overlay_fields.config(uid)
    v = out.get("values") or {}
    out["config"] = cfg
    out["rows"] = overlay_fields.rows(cfg, v, out["hud"], out["units"] == "imperial", out["currency"]) if v else []
    out["warnings"] = [{"code": c_, "text": t_, "level": l_} for c_, t_, l_ in overlay_fields.hos_warnings(out["hud"], v)] \
        if v and cfg["hos_warnings"] else []
    out["alerts"] = _overlay_alerts(uid, request.args.get("since", "")) if cfg["alerts"] else []
    return jsonify(out)


def _overlay_alerts(uid, since):
    """Logbook (and low fuel) notifications made after `since` - the overlay passes back the server_time it got last
    time, so each alert pops up once. The first poll (no `since`) only gets alerts from the last 2 minutes."""
    try:
        t0 = float(since)
    except ValueError:
        t0 = time.time() - 120
    rows_ = db.q("SELECT id, text, kind, created_at FROM notifications WHERE user_id=? AND created_at > ? "
                 "AND (link LIKE '/compliance%' OR link LIKE '/fuel%') ORDER BY id DESC LIMIT 5", (uid, t0))
    return [{"id": r["id"], "text": r["text"], "level": {"error": "bad", "warning": "warn"}.get(r["kind"], "info"),
             "t": r["created_at"]} for r in reversed(rows_)]


def _overlay_extras(uid, v):
    """What the stream overlay shows besides the raw telemetry: who is driving and their Hours of Service."""
    u = db.one("SELECT display_name, color FROM users WHERE id=?", (uid,)) or {}
    hud = {"driver": u.get("display_name") or "", "driver_color": u.get("color") or "",
           "company": db.setting("company_name", ""), "tag": db.setting("company_tag", ""),
           "color": db.setting("company_color", "#c8102e"), "hos": None}
    if not v or db.setting("hos_enabled", "1") != "1":
        return hud
    try:
        from . import compliance
        game = v.get("game") if v.get("game") in ("ats", "ets2") else "ats"
        now_min = compliance.log_min(uid, game, v["game_time_abs"]) if v.get("game_time_abs") else None
        c = compliance.clocks(uid, now_min, game)
        if c:
            hud["hos"] = {"status": c["status"], "rules": c["rules_label"], "drive_left": c["drive"]["left"],
                          "window_left": c["window"]["left"], "break_left": c["break"]["left"],
                          "cycle_left": c["cycle"]["left"], "available": c["available"], "break_len": c["break_len"]}
    except Exception:  # noqa: BLE001 - the overlay must keep showing the truck even if the logbook can't be read
        log.exception("overlay: Hours of Service for driver #%s not available", uid)
    return hud


@bp.route("/agent/frame", methods=["POST"])
def agent_frame():
    token = request.headers.get("X-Agent-Token", "")
    uid = db.val("SELECT id FROM users WHERE agent_token=? AND status IN ('active','on_leave')", (token,)) if token else None
    if not uid:
        abort(401)
    data = request.get_json(silent=True) or {}
    frame = data.get("frame")
    if frame is None:
        HUB.ingest(uid, None, "agent")
        return jsonify(ok=True)
    if not isinstance(frame, dict) or not isinstance(frame.get("values"), dict):
        abort(400)
    HUB.ingest(uid, clean_agent_frame(frame), "agent")
    return jsonify(ok=True)


@bp.route("/telemetry/raw")
@login_required(role="admin")
def raw():
    s = HUB.sampler
    if not s or not s.raw:
        return jsonify(error="no local frame yet"), 404
    def num(name, default):
        raw = request.args.get(name, "")
        return int(raw) if raw.isdecimal() and len(raw) < 7 else default
    off = max(0, min(MMF_SIZE - 16, num("offset", 0)))
    n = max(16, min(1024, num("length", 256)))
    chunk = s.raw[off:off + n]
    rows = [{"offset": off + i, "hex": chunk[i:i + 16].hex(" "),
             "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in chunk[i:i + 16])} for i in range(0, len(chunk), 16)]
    return jsonify(rows=rows)


@bp.route("/notifications")
@login_required
def notifications():
    uid = user()["id"]
    rows = db.q("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 15", (uid,))
    unread = db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (uid,), 0)
    msgs = db.val("SELECT COUNT(*) FROM messages WHERE to_id=? AND read=0", (uid,), 0)
    from . import recruit
    return jsonify(items=rows, unread=unread, messages=msgs, applications=recruit.waiting(user()))


@bp.route("/export/jobs.csv")
@login_required
def export_jobs():
    rows = db.q("""SELECT j.id, u.display_name driver, j.game, j.status, j.source, j.cargo, j.cargo_mass,
                   j.source_city, j.source_company, j.destination_city, j.destination_company, j.planned_km, j.driven_km,
                   j.revenue, j.driver_pay, j.fuel_used, j.fuel_cost, j.tolls, j.fines, j.ferry, j.train, j.profit,
                   j.cargo_damage, j.max_speed, j.avg_speed, j.score, j.xp, j.flags,
                   datetime(j.started_at, 'unixepoch', 'localtime') started,
                   datetime(j.finished_at, 'unixepoch', 'localtime') finished
                   FROM jobs j JOIN users u ON u.id=j.user_id ORDER BY j.id""")
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([_csv_cell(x) for x in r.values()])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fleet-logbook.csv"})


@bp.route("/export/ledger.csv")
@login_required(role="admin")
def export_ledger():
    rows = db.q("""SELECT l.id, datetime(l.created_at, 'unixepoch', 'localtime') date, l.account, u.display_name,
                   l.category, l.amount, l.description, l.job_id FROM ledger l LEFT JOIN users u ON u.id=l.user_id
                   ORDER BY l.id""")
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([_csv_cell(x) for x in r.values()])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fleet-ledger.csv"})
