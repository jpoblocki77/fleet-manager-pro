"""Insights, Route Planner and System pages.

  /insights   12-month profit trend, per-distance economics, best lanes, cargo, trucks and drivers
  /planner    what a lane has really paid the company, best lanes to run, an estimate for a new lane
  /system     automatic + manual database backups, log viewer / download, health of this copy
"""
import logging
import os
import re
import tempfile
import threading
import time
import zipfile

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from . import VERSION, db
from .auth import current_units, game_sql, login_required, user

bp = Blueprint("insights", __name__)
log = logging.getLogger("fmpro.system")
DAY = 86400
KM_PER_MI = 1.609344
PERIODS = {"30d": ("30 days", 30), "90d": ("90 days", 90), "year": ("12 months", 365), "all": ("All time", None)}
STARTED = time.time()


def _per_unit():
    """(label, km per unit) for 'per mile' / 'per km' figures in the viewer's units."""
    return ("mi", KM_PER_MI) if current_units() == "imperial" else ("km", 1.0)


def _since(period):
    days = PERIODS.get(period, PERIODS["90d"])[1]
    return time.time() - days * DAY if days else 0


# a delivered job's distance: what was driven, else what was planned
KM = "COALESCE(NULLIF(j.driven_km, 0), j.planned_km, 0)"
COST = "(j.driver_pay + j.fuel_cost + j.tolls + (j.fines - COALESCE(j.driver_fines, 0)) + j.ferry + j.train)"
ON_TIME = ("SUM(CASE WHEN j.game_time_deadline IS NOT NULL AND j.game_time_end IS NOT NULL "
           "AND j.game_time_end <= j.game_time_deadline THEN 1 ELSE 0 END)")
TIMED = "SUM(CASE WHEN j.game_time_deadline IS NOT NULL AND j.game_time_end IS NOT NULL THEN 1 ELSE 0 END)"


def _delivered(since=0, extra="", args=()):
    cond, gargs = game_sql("j.game")
    return f"FROM jobs j WHERE j.status='delivered' AND COALESCE(j.finished_at, j.started_at) >= ?{cond} {extra}", \
        (since,) + gargs + tuple(args)


def _rates(row, per_km):
    """Add per-distance, margin and on-time figures to an aggregate row."""
    km = row.get("km") or 0
    rev = row.get("revenue") or 0
    row["rev_per"] = rev / km * per_km if km else None
    row["cost_per"] = (row.get("cost") or 0) / km * per_km if km else None
    row["profit_per"] = (row.get("profit") or 0) / km * per_km if km else None
    row["margin"] = (row.get("profit") or 0) / rev * 100 if rev else None
    row["on_time_pct"] = (row.get("on_time") or 0) / row["timed"] * 100 if row.get("timed") else None
    return row


# ------------------------------------------------------------------------------------------------ insights
@bp.route("/insights")
@login_required
def page():
    period = request.args.get("period", "90d")
    period = period if period in PERIODS else "90d"
    since = _since(period)
    unit, per_km = _per_unit()
    agg = (f"COUNT(*) loads, COALESCE(SUM(j.revenue),0) revenue, COALESCE(SUM({COST}),0) cost, "
           f"COALESCE(SUM(j.profit),0) profit, COALESCE(SUM({KM}),0) km, COALESCE(AVG(j.cargo_mass),0) mass, "
           f"COALESCE(AVG(NULLIF(j.score,0)),0) score, COALESCE(AVG(j.cargo_damage),0) damage, "
           f"{ON_TIME} on_time, {TIMED} timed")

    frm, args = _delivered(since)
    kpi = _rates(db.one(f"SELECT {agg} {frm}", args), per_km)  # nosec B608  # fixed SQL fragments; values are ? parameters

    # 12 months, oldest first, every month present even when empty
    frm12, args12 = _delivered(time.time() - 366 * DAY)
    rows = {r["m"]: r for r in db.q(
        f"SELECT strftime('%Y-%m', COALESCE(j.finished_at, j.started_at), 'unixepoch', 'localtime') m, {agg} "  # nosec B608
        f"{frm12} GROUP BY m", args12)}
    months = []
    t = time.localtime()
    y, mo = t.tm_year, t.tm_mon
    for i in range(11, -1, -1):
        yy, mm = y, mo - i
        while mm <= 0:
            mm += 12
            yy -= 1
        key = f"{yy:04d}-{mm:02d}"
        r = rows.get(key) or {"loads": 0, "revenue": 0, "cost": 0, "profit": 0, "km": 0}
        months.append({"m": key, "label": time.strftime("%b %y", time.strptime(key, "%Y-%m")), "loads": r["loads"],
                       "revenue": round(r["revenue"]), "cost": round(r["cost"]), "profit": round(r["profit"]),
                       "dist": round((r["km"] or 0) / per_km)})

    lanes = [_rates(r, per_km) for r in db.q(
        f"SELECT j.source_city src, j.destination_city dst, {agg} {frm} AND j.source_city!='' AND j.destination_city!='' "  # nosec B608
        f"GROUP BY j.source_city, j.destination_city HAVING COUNT(*) >= 1 ORDER BY SUM(j.profit) DESC LIMIT 12", args)]
    cargo = [_rates(r, per_km) for r in db.q(
        f"SELECT j.cargo name, {agg} {frm} AND j.cargo!='' GROUP BY j.cargo ORDER BY SUM(j.revenue) DESC LIMIT 10", args)]  # nosec B608
    trucks = [_rates(r, per_km) for r in db.q(
        f"SELECT t.id, t.brand, t.model, t.plate, {agg}, "  # nosec B608
        f"(SELECT COALESCE(SUM(m.cost),0) FROM maintenance m WHERE m.truck_id=t.id AND m.created_at >= ?) upkeep "
        f"FROM jobs j JOIN trucks t ON t.id=j.truck_id WHERE j.status='delivered' "
        f"AND COALESCE(j.finished_at, j.started_at) >= ?{game_sql('j.game')[0]} GROUP BY t.id ORDER BY SUM(j.profit) DESC LIMIT 10",
        (since, since) + game_sql("j.game")[1])]
    for tr in trucks:
        tr["net"] = (tr["profit"] or 0) - (tr["upkeep"] or 0)
    drivers = [_rates(r, per_km) for r in db.q(
        f"SELECT u.id, u.display_name, u.color, {agg} FROM jobs j JOIN users u ON u.id=j.user_id "  # nosec B608
        f"WHERE j.status='delivered' AND COALESCE(j.finished_at, j.started_at) >= ?{game_sql('j.game')[0]} "
        f"GROUP BY u.id ORDER BY SUM(j.profit) DESC LIMIT 15", (since,) + game_sql("j.game")[1])]
    best_profit = max((d["profit"] or 0 for d in drivers), default=0)
    return render_template("insights.html", period=period, periods=PERIODS, kpi=kpi, months=months, lanes=lanes,
                           cargo=cargo, trucks=trucks, drivers=drivers, unit=unit, best_profit=best_profit)


# ------------------------------------------------------------------------------------------------ route planner
@bp.route("/planner")
@login_required
def planner():
    unit, per_km = _per_unit()
    src = request.args.get("from", "").strip()[:80]
    dst = request.args.get("to", "").strip()[:80]
    cargo = request.args.get("cargo", "").strip()[:80]
    try:
        typed = float(request.args.get("distance", "") or 0)
    except ValueError:
        typed = 0
    typed_km = max(0.0, min(typed, 20000)) * per_km
    agg = (f"COUNT(*) loads, AVG(j.revenue) revenue, AVG({COST}) cost, AVG(j.profit) profit, AVG({KM}) km, "
           f"SUM(j.revenue) total_rev, SUM({KM}) total_km, SUM(j.profit) total_profit, "
           f"AVG(NULLIF(j.driving_s,0)) driving_s, AVG(j.cargo_damage) damage, {ON_TIME} on_time, {TIMED} timed")
    frm, args = _delivered()
    cities = [r["c"] for r in db.q(f"SELECT DISTINCT c FROM (SELECT j.source_city c {frm} UNION SELECT j.destination_city {frm}) "  # nosec B608
                                   f"WHERE c!='' ORDER BY c", args + args)]
    cargos = [r["cargo"] for r in db.q(f"SELECT DISTINCT j.cargo {frm} AND j.cargo!='' ORDER BY j.cargo", args)]  # nosec B608

    def lane(a, b, c=""):
        extra, ex = "AND j.source_city=? COLLATE NOCASE AND j.destination_city=? COLLATE NOCASE", [a, b]
        if c:
            extra += " AND j.cargo=? COLLATE NOCASE"
            ex.append(c)
        f, ar = _delivered(0, extra, ex)
        r = db.one(f"SELECT {agg} {f}", ar)  # nosec B608
        return _per_load(r, per_km) if r and r["loads"] else None

    company = _per_load(db.one(f"SELECT {agg} {frm}", args), per_km)  # nosec B608
    result = None
    if src and dst:
        exact = lane(src, dst, cargo) if cargo else None
        same = lane(src, dst)
        back = lane(dst, src)
        km = (same or exact or {}).get("km") or (back or {}).get("km") or typed_km
        est = None
        if km and company and company.get("rev_per_km") is not None:
            est = {"km": km, "revenue": company["rev_per_km"] * km, "cost": company["cost_per_km"] * km,
                   "profit": company["profit_per_km"] * km,
                   "hours": km / company["kmh"] if company.get("kmh") else None,
                   "source": "this lane" if same else ("the return lane" if back else "the distance you entered")}
        jobs = db.q(f"SELECT j.id, j.cargo, j.revenue, j.profit, {KM} km, j.finished_at, u.display_name "  # nosec B608
                    f"FROM jobs j JOIN users u ON u.id=j.user_id WHERE j.status='delivered' "
                    f"AND j.source_city=? COLLATE NOCASE AND j.destination_city=? COLLATE NOCASE{game_sql('j.game')[0]} "
                    f"ORDER BY j.finished_at DESC LIMIT 15", (src, dst) + game_sql("j.game")[1])
        f2, a2 = _delivered(0, "AND j.source_city=? COLLATE NOCASE AND j.destination_city!=''", [src])
        onward = [_per_load(r, per_km) for r in db.q(
            f"SELECT j.destination_city dst, {agg} {f2} GROUP BY j.destination_city "  # nosec B608
            f"ORDER BY SUM(j.profit) / MAX(SUM({KM}), 1) DESC LIMIT 8", a2)]
        result = {"exact": exact, "same": same, "back": back, "est": est, "jobs": jobs, "onward": onward}
    f3, a3 = _delivered(0, "AND j.source_city!='' AND j.destination_city!=''")
    best = [_per_load(r, per_km) for r in db.q(
        f"SELECT j.source_city src, j.destination_city dst, {agg} {f3} GROUP BY j.source_city, j.destination_city "  # nosec B608
        f"HAVING SUM({KM}) > 0 ORDER BY SUM(j.profit) / SUM({KM}) DESC LIMIT 15", a3)]
    return render_template("planner.html", src=src, dst=dst, cargo=cargo, distance=request.args.get("distance", ""),
                           cities=cities, cargos=cargos, result=result, best=best, company=company, unit=unit,
                           per_km=per_km)


def _per_load(r, per_km):
    """Averages per load plus per-distance rates (per km internally; templates convert with per_km)."""
    if not r or not r.get("loads"):
        return None
    tk = r.get("total_km") or 0
    r["rev_per_km"] = (r["total_rev"] or 0) / tk if tk else None
    r["profit_per_km"] = (r["total_profit"] or 0) / tk if tk else None
    r["cost_per_km"] = (r["rev_per_km"] - r["profit_per_km"]) if tk else None
    r["rev_per"] = r["rev_per_km"] * per_km if tk else None
    r["profit_per"] = r["profit_per_km"] * per_km if tk else None
    r["kmh"] = (r["km"] / (r["driving_s"] / 3600)) if r.get("driving_s") and r.get("km") else None
    r["on_time_pct"] = (r["on_time"] or 0) / r["timed"] * 100 if r.get("timed") else None
    return r


# ------------------------------------------------------------------------------------------------ backups
BACKUP_KEEP = 14
MANUAL_GAP = 60                                     # seconds between manual backups (stops disk-filling)
_NAME = re.compile(r"^fleet-[A-Za-z0-9-]+-\d{8}-\d{6}\.db$")


def backup_dir():
    return os.path.join(os.path.dirname(db.DB_PATH), "backups")


def list_backups():
    d = backup_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if _NAME.match(f) and os.path.isfile(p):
            out.append({"name": f, "size": os.path.getsize(p), "t": os.path.getmtime(p), "auto": "-auto-" in f})
    return sorted(out, key=lambda b: b["t"], reverse=True)


def make_backup(tag):
    from .ops import backup_database
    path = backup_database(tag)
    log.info("database backup made: %s (%s bytes)", os.path.basename(path), f"{os.path.getsize(path):,}")
    return path


def prune_auto():
    """Keep the newest BACKUP_KEEP automatic backups (manual ones are never removed automatically)."""
    autos = [b for b in list_backups() if b["auto"]]
    for b in autos[BACKUP_KEEP:]:
        try:
            os.remove(os.path.join(backup_dir(), b["name"]))
            log.info("old automatic backup removed: %s", b["name"])
        except OSError as e:
            log.warning("couldn't remove old backup %s: %s", b["name"], e)


def auto_backup_due():
    if db.setting("auto_backup", "1") != "1":
        return False
    last = next((b for b in list_backups() if b["auto"]), None)
    return not last or time.time() - last["t"] >= DAY


def start_auto_backup(check_every=3600):
    """A daily automatic backup of the company database (checked hourly, first check a minute after start)."""
    def run():
        time.sleep(60)
        while True:
            try:
                if auto_backup_due():
                    make_backup("auto")
                    prune_auto()
            except Exception:
                log.exception("automatic backup failed")
            time.sleep(check_every)
    threading.Thread(target=run, name="auto-backup", daemon=True).start()


# ------------------------------------------------------------------------------------------------ system page
def _logs_dir():
    return os.path.join(os.path.dirname(db.DB_PATH), "logs")


def _log_files():
    d = _logs_dir()
    if not os.path.isdir(d):
        return []
    return sorted(({"name": f, "size": os.path.getsize(os.path.join(d, f)), "t": os.path.getmtime(os.path.join(d, f))}
                   for f in os.listdir(d) if f.endswith(".log") or re.search(r"\.log\.\d+$", f)),
                  key=lambda x: (not x["name"].endswith(".log"), x["name"]))


def _tail(path, lines=400):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        data = f.read().decode("utf-8", "replace").splitlines()
    return data[-lines:]


@bp.route("/system")
@login_required(role="admin")
def system():
    from . import licensing
    from .telemetry.service import HUB
    files = _log_files()
    sizes = {f["name"]: f["size"] for f in files}
    sel = request.args.get("log") or ("errors.log" if sizes.get("errors.log") else "debug.log")
    if sel not in sizes:
        sel = files[0]["name"] if files else ""
    q = request.args.get("q", "").strip()[:80]
    lines = _tail(os.path.join(_logs_dir(), sel)) if sel else []
    if q:
        lines = [ln for ln in lines if q.lower() in ln.lower()]
    lines = list(reversed(lines))[:400]
    lic = licensing.LICENCE
    health = {
        "version": VERSION, "uptime": time.time() - STARTED,
        "db_size": os.path.getsize(db.DB_PATH) if os.path.exists(db.DB_PATH) else 0,
        "db_check": db.val("PRAGMA quick_check"),
        "telemetry": HUB.local_status, "live": len(HUB.all_live()),
        "licence": bool(lic and lic.ok), "licence_reason": getattr(lic, "reason", "") if lic else "",
        "jobs": db.val("SELECT COUNT(*) FROM jobs", (), 0), "members": db.val("SELECT COUNT(*) FROM users WHERE status!='removed'", (), 0),
    }
    backups = list_backups()
    return render_template("system.html", files=files, sel=sel, q=q, lines=lines, health=health, backups=backups,
                           auto=db.setting("auto_backup", "1") == "1", keep=BACKUP_KEEP,
                           last_auto=next((b for b in backups if b["auto"]), None))


@bp.route("/system/backup", methods=["POST"])
@login_required(role="admin")
def backup_now():
    last = next((b for b in list_backups() if "-manual-" in b["name"]), None)
    if last and time.time() - last["t"] < MANUAL_GAP:
        flash("A backup was made less than a minute ago. Wait a moment before making another.", "error")
        return redirect(url_for("insights.system"))
    path = make_backup("manual")
    db.audit(user()["id"], "database backup", os.path.basename(path))
    flash(f"Backup saved: {os.path.basename(path)}", "success")
    return redirect(url_for("insights.system"))


@bp.route("/system/auto-backup", methods=["POST"])
@login_required(role="admin")
def auto_toggle():
    on = request.form.get("on") == "1"
    db.set_setting("auto_backup", "1" if on else "0")
    db.audit(user()["id"], "automatic backups", "on" if on else "off")
    flash(f"Automatic daily backups {'on' if on else 'off'}.", "success")
    return redirect(url_for("insights.system"))


@bp.route("/system/backup/<name>")
@login_required(role="owner")
def backup_download(name):
    if not _NAME.match(name) or not os.path.isfile(os.path.join(backup_dir(), name)):
        abort(404)
    db.audit(user()["id"], "backup downloaded", name)
    return send_file(os.path.join(backup_dir(), name), as_attachment=True, download_name=name)


@bp.route("/system/backup/<name>/delete", methods=["POST"])
@login_required(role="owner")
def backup_delete(name):
    p = os.path.join(backup_dir(), name)
    if not _NAME.match(name) or not os.path.isfile(p):
        abort(404)
    try:
        os.remove(p)
    except OSError as e:
        log.warning("couldn't delete backup %s: %s", name, e)
        flash(f"Couldn't delete {name}: it's in use (a download may still be running). Try again in a moment.", "error")
        return redirect(url_for("insights.system"))
    db.audit(user()["id"], "backup deleted", name)
    flash(f"Backup deleted: {name}", "success")
    return redirect(url_for("insights.system"))


@bp.route("/system/logs.zip")
@login_required(role="admin")
def logs_zip():
    """All log files in one zip, for sending to support. Secrets are already masked inside the logs."""
    tmp = tempfile.TemporaryFile()                     # logs can be 100+ MB: build the zip on disk, not in memory
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _log_files():
            z.write(os.path.join(_logs_dir(), f["name"]), f["name"])
        z.writestr("about.txt", f"Fleet Manager Pro {VERSION}\nlogs zipped {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    tmp.seek(0)
    db.audit(user()["id"], "logs downloaded", "")
    return send_file(tmp, mimetype="application/zip", as_attachment=True,
                     download_name=f"fleet-manager-logs-{time.strftime('%Y%m%d-%H%M')}.zip")
