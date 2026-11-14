"""Compliance pages: ELD logbook, DVIR, roadside inspections, violations."""
import json
import time

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from . import compliance, db, economy
from .auth import can, login_required, user
from .telemetry.service import HUB

bp = Blueprint("comp", __name__)


def _uid_arg():
    a = request.args.get("driver", "") or request.args.get("user", "")
    if a.isdigit() and db.val("SELECT id FROM users WHERE id=?", (int(a),)):
        return int(a)
    return user()["id"]


def _live_values(uid):
    e = HUB.get(uid)
    return e["frame"]["values"] if e else None


def _game(uid):
    """Which game's logbook: ?game=, else the game being driven, else the top-bar choice, else the default."""
    from .auth import game_scope
    a = request.args.get("game", "") if request else ""
    if a in ("ats", "ets2"):
        return a
    v = _live_values(uid)
    if v and v["game"] in ("ats", "ets2"):
        return v["game"]
    return game_scope() or db.setting("default_game", "ats")


def _now_min(uid, game=None):
    game = game or _game(uid)
    v = _live_values(uid)
    if v and v["game"] == game and v.get("game_time_abs"):
        return v["game_time_abs"]
    return compliance.last_game_min(uid, game)


@bp.route("/compliance")
@login_required
def page():
    uid = _uid_arg()
    who = db.one("SELECT id, display_name, color FROM users WHERE id=?", (uid,))
    tab = request.args.get("tab", "logbook")
    game = _game(uid)
    now_min = _now_min(uid, game)
    ctx = {"tab": tab, "who": who, "drivers": db.q("SELECT id, display_name FROM users WHERE status!='removed' ORDER BY display_name"),
           "game": game, "now_min": now_min, "STATUSES": compliance.STATUSES, "RULESETS": compliance.RULESETS,
           "live": bool(_live_values(uid))}
    if tab == "logbook":
        c = compliance.clocks(uid, now_min, game) if now_min else None
        day = request.args.get("day", "")
        day = int(day) if day.lstrip("-").isdigit() else ((now_min or 0) // 1440)
        start, end = day * 1440, day * 1440 + 1440
        segs = db.q("SELECT * FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')=? AND COALESCE(end_min,start_min) >= ? "
                    "AND start_min < ? ORDER BY start_min, id", (uid, game, start, end))
        bars, totals = [], {k: 0 for k in compliance.STATUSES}
        for s in segs:
            a, b = max(s["start_min"], start), min(s["end_min"] or s["start_min"], end, now_min or end)
            if b > a:
                bars.append({"status": s["status"], "a": a - start, "b": b - start, "note": s["note"], "auto": s["auto"]})
                totals[s["status"]] += b - a
        viol = db.q("SELECT * FROM hos_violations WHERE user_id=? AND COALESCE(game,'ats')=? AND game_min BETWEEN ? AND ? "
                    "ORDER BY game_min", (uid, game, start, end))
        ctx.update(clocks=c, day=day, bars=bars, totals=totals, segs=segs, day_viol=viol,
                   holds=compliance.holds(uid, game))
    elif tab == "dvir":
        truck = db.one("SELECT * FROM trucks WHERE assigned_user_id=? AND status NOT IN ('sold','returned') ORDER BY last_seen DESC", (uid,))
        live = _live_values(uid)
        if live:
            t2 = db.one("SELECT * FROM trucks WHERE game_truck_id=? AND plate=? AND status NOT IN ('sold','returned')",
                        (live["truck_id"], live["license_plate"]))
            truck = t2 or truck
        ctx.update(items=compliance.DVIR_ITEMS, truck=truck, hints=compliance.telemetry_hints(live),
                   trucks=db.q("SELECT * FROM trucks WHERE status NOT IN ('sold','returned') ORDER BY brand"),
                   rows=db.q("""SELECT d.*, u.display_name, t.brand, t.model, t.plate FROM dvirs d
                                JOIN users u ON u.id=d.user_id LEFT JOIN trucks t ON t.id=d.truck_id
                                ORDER BY d.created_at DESC LIMIT 60"""),
                   pretrip_ok=compliance.has_pretrip(uid, truck["id"] if truck else None, now_min, game))
        for r in ctx["rows"]:
            r["items"] = json.loads(r["items"])
    elif tab == "inspections":
        rows = db.q("""SELECT i.*, u.display_name, u.color, t.brand, t.model, t.plate FROM inspections i
                       JOIN users u ON u.id=i.user_id LEFT JOIN trucks t ON t.id=i.truck_id
                       ORDER BY i.created_at DESC LIMIT 100""")
        for r in rows:
            r["findings"] = json.loads(r["findings"])
        stats = db.one("""SELECT COUNT(*) n, SUM(result='passed') passed, SUM(result='oos') oos, COALESCE(SUM(fine),0) fines
                          FROM inspections""")
        ctx.update(rows=rows, stats=stats)
    elif tab == "violations":
        ctx.update(rows=db.q("""SELECT v.*, u.display_name, u.color FROM hos_violations v JOIN users u ON u.id=v.user_id
                                ORDER BY v.created_at DESC LIMIT 200"""))
    return render_template("compliance.html", **ctx)


@bp.route("/compliance/status", methods=["POST"])
@login_required
def set_status():
    st = request.form.get("status")
    if st not in ("off", "sleeper", "on"):
        abort(400)
    uid = user()["id"]
    v = _live_values(uid)
    if v and abs(v["speed"]) > 1.5:
        flash("You can't change duty status while the truck is moving.", "error")
    else:
        game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else _game(uid)
        gm = _now_min(uid, game)
        if gm is None:
            flash("No game time yet — drive with telemetry first so the logbook knows the in-game clock.", "error")
        else:
            compliance.set_status(uid, st, gm, request.form.get("note", "")[:120], game)
            HUB.tracker(uid).hos.status = None      # let the live tracker pick the new row up
            flash(f"Duty status: {compliance.STATUSES[st]}.", "success")
    return redirect(url_for("comp.page", tab="logbook", game=request.form.get("game") or None))


@bp.route("/compliance/dvir", methods=["POST"])
@login_required
def dvir_submit():
    uid = user()["id"]
    kind = request.form.get("kind") if request.form.get("kind") in ("pre", "post") else "pre"
    tid = request.form.get("truck_id", "")
    tid = int(tid) if tid.isdigit() and db.val("SELECT id FROM trucks WHERE id=?", (int(tid),)) else None
    items, defects = {}, []
    for key, label, _hint in compliance.DVIR_ITEMS:
        state = request.form.get(f"item_{key}")
        if state not in ("ok", "defect"):
            flash("Check every item: OK or defect.", "error")
            return redirect(url_for("comp.page", tab="dvir"))
        note = request.form.get(f"note_{key}", "").strip()[:200]
        items[key] = {"state": state, "note": note}
        if state == "defect":
            defects.append(f"{label}{': ' + note if note else ''}")
    if request.form.get("signed") != "1":
        flash("Sign the report to submit it.", "error")
        return redirect(url_for("comp.page", tab="dvir"))
    v = _live_values(uid)
    game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else _game(uid)
    gm = _now_min(uid, game)
    did = db.insert("dvirs", user_id=uid, truck_id=tid, kind=kind, game=game, items=json.dumps(items), defects="; ".join(defects),
                    status="defects" if defects else "ok", odometer=(v or {}).get("odometer"),
                    game_min=gm, location=(v or {}).get("source_city", "") if kind == "pre" else (v or {}).get("destination_city", ""),
                    created_at=time.time())
    if defects and tid:
        db.update("trucks", tid, status="maintenance")
        for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner')"):
            db.notify(m["id"], f"DVIR #{did}: {len(defects)} defect(s) reported — truck on maintenance hold.",
                      "/compliance?tab=dvir", "warning")
        flash(f"{'Pre' if kind == 'pre' else 'Post'}-trip report saved with {len(defects)} defect(s). "
              "The truck is on a maintenance hold until a manager marks it repaired.", "error")
    else:
        flash(f"{'Pre' if kind == 'pre' else 'Post'}-trip report saved — no defects. Drive safe!", "success")
    return redirect(url_for("comp.page", tab="dvir"))


@bp.route("/compliance/dvir/<int:did>/repair", methods=["POST"])
@login_required(role="admin")
def dvir_repair(did):
    d = db.one("SELECT * FROM dvirs WHERE id=? AND status='defects'", (did,))
    if not d:
        abort(404)
    cost = economy.dvir_repair_price(d["defects"])     # Settings > Economy; a cost sent with the form is never used
    db.update("dvirs", did, status="repaired", repaired_at=time.time(), repaired_by=user()["id"])
    if d["truck_id"]:
        db.insert("maintenance", truck_id=d["truck_id"], kind="repair", cost=cost,
                  odometer=db.val("SELECT odometer FROM trucks WHERE id=?", (d["truck_id"],)),
                  notes=f"DVIR #{did} defects repaired: {d['defects'][:200]}", user_id=user()["id"], created_at=time.time())
        if not db.val("SELECT COUNT(*) FROM dvirs WHERE truck_id=? AND status='defects'", (d["truck_id"],), 0):
            db.update("trucks", d["truck_id"], status="active")
        if cost:
            economy.post("maintenance", -cost, f"DVIR #{did} repair")
    db.notify(d["user_id"], f"Defects on DVIR #{did} were repaired — the truck is back in service.", "/compliance?tab=dvir", "success")
    flash("Marked repaired; the truck is back in service.", "success")
    return redirect(url_for("comp.page", tab="dvir"))


@bp.route("/compliance/inspect", methods=["POST"])
@login_required(role="dispatcher")
def inspect_now():
    uid = request.form.get("driver", "")
    uid = int(uid) if uid.isdigit() else user()["id"]
    v = _live_values(uid)
    if not v:
        flash("That driver has no live telemetry — the inspection needs the truck's current data.", "error")
        return redirect(url_for("comp.page", tab="inspections"))
    tr = HUB.tracker(uid)
    lvl = request.form.get("level", "")
    compliance.inspect(uid, v, tr.job_id, tr.truck_id, int(lvl) if lvl in ("1", "2", "3") else None)
    return redirect(url_for("comp.page", tab="inspections"))


@bp.route("/api/hos")
@login_required
def api_hos():
    a = request.args.get("user", "")
    uid = int(a) if a.isdigit() else user()["id"]
    game = _game(uid)
    c = compliance.clocks(uid, _now_min(uid, game), game)
    return jsonify(clocks=c, holds=compliance.holds(uid, game), statuses=compliance.STATUSES)
