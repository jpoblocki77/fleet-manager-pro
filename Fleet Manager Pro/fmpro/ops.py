"""0.02 pages: Expenses, Driver performance, ELD & Compliance reset, garage edit / delete."""
import csv
import datetime as dt
import io
import json
import logging
import os
import time

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from . import RES, db, economy
from .auth import can, game_scope, game_sql, login_required, user

bp = Blueprint("ops", __name__)

DAY = 86400
PERIODS = [("week", "7 days"), ("month", "30 days"), ("year", "Year"), ("all", "All time")]
EXPENSE_CATEGORIES = ["fuel", "tolls", "fines", "repairs", "maintenance", "tyres", "cargo damage", "late delivery",
                      "insurance", "parking", "permits", "garage upkeep", "truck purchase", "trailer purchase",
                      "driver pay", "office", "other"]


def _since(period):
    now = time.time()
    return {"week": now - 7 * DAY, "month": now - 30 * DAY, "year": now - 365 * DAY}.get(period, 0)


def _num(name, lo=0.0, hi=1e9):
    try:
        v = float(str(request.form.get(name, "")).replace(",", "").strip())
    except ValueError:
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return max(lo, min(hi, v))


def _id(name, table):
    raw = str(request.values.get(name, ""))
    return int(raw) if raw.isdecimal() and db.val(f"SELECT id FROM {table} WHERE id=?", (int(raw),)) else None  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def _csv_cell(v):
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


# ------------------------------------------------------------------ expenses
def _expense_filter():
    a = {k: request.args.get(k, "") for k in ("period", "category", "truck", "driver", "q", "kind")}
    a["period"] = a["period"] if a["period"] in dict(PERIODS) else "month"
    where = ["l.account='company'", "l.amount < 0", "l.created_at >= ?"]
    args = [_since(a["period"])]
    if a["category"]:
        where.append("l.category = ?")
        args.append(a["category"][:40])
    if a["truck"].isdecimal():
        where.append("COALESCE(l.truck_id, j.truck_id) = ?")
        args.append(int(a["truck"]))
    if a["driver"].isdecimal():
        where.append("COALESCE(l.driver_id, j.user_id) = ?")
        args.append(int(a["driver"]))
    if a["kind"] == "manual":
        where.append("COALESCE(l.manual, 0) = 1")
    elif a["kind"] == "auto":
        where.append("COALESCE(l.manual, 0) = 0")
    if a["q"]:
        where.append("(l.description LIKE ? OR l.category LIKE ?)")
        args += [f"%{a['q'][:60]}%"] * 2
    return a, " AND ".join(where), args


_EXPENSE_SQL = """SELECT l.*, COALESCE(l.truck_id, j.truck_id) truck, COALESCE(l.driver_id, j.user_id) driver,
                  t.brand, t.model, t.plate, u.display_name, u.color, e.display_name entered_name
                  FROM ledger l LEFT JOIN jobs j ON j.id=l.job_id
                  LEFT JOIN trucks t ON t.id=COALESCE(l.truck_id, j.truck_id)
                  LEFT JOIN users u ON u.id=COALESCE(l.driver_id, j.user_id)
                  LEFT JOIN users e ON e.id=l.entered_by WHERE """


@bp.route("/expenses")
@login_required
def expenses():
    a, where, args = _expense_filter()
    rows = db.q(_EXPENSE_SQL + where + " ORDER BY l.created_at DESC, l.id DESC LIMIT 500", args)
    totals = db.q("""SELECT l.category, -SUM(l.amount) total, COUNT(*) n FROM ledger l LEFT JOIN jobs j ON j.id=l.job_id
                     WHERE """ + where + " GROUP BY l.category ORDER BY total DESC", args)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    total = sum(t["total"] for t in totals)
    count = sum(t["n"] for t in totals)
    days = max(1, (time.time() - _since(a["period"])) / DAY) if a["period"] != "all" else None
    cats = sorted(set(EXPENSE_CATEGORIES) | {r["category"] for r in db.q(
        "SELECT DISTINCT category FROM ledger WHERE account='company' AND amount<0")})
    return render_template("expenses.html", rows=rows, totals=totals, total=total, count=count, a=a, periods=PERIODS,
                           per_day=(total / days) if days else None, cats=cats,
                           trucks=db.q("SELECT id, brand, model, plate, status FROM trucks ORDER BY status='sold', brand"),
                           drivers=db.q("SELECT id, display_name FROM users WHERE status!='removed' ORDER BY display_name"),
                           today=dt.date.today().isoformat())


@bp.route("/expenses.csv")
@login_required(role="admin")
def expenses_csv():
    _a, where, args = _expense_filter()
    rows = db.q(_EXPENSE_SQL + where + " ORDER BY l.created_at, l.id", args)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "date", "category", "amount", "description", "truck", "driver", "job", "entered by", "manual"])
    for r in rows:
        w.writerow([_csv_cell(x) for x in (
            r["id"], dt.datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M"), r["category"], -r["amount"],
            r["description"], f"{r['brand'] or ''} {r['model'] or ''} {r['plate'] or ''}".strip(), r["display_name"] or "",
            r["job_id"] or "", r["entered_name"] or "", "yes" if r["manual"] else "")])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fleet-expenses.csv"})


@bp.route("/expenses/save", methods=["POST"])
@login_required(role="admin")
def expense_save():
    amount = _num("amount", 0, 1e9)
    if not amount:
        flash("Enter an amount greater than zero.", "error")
        return redirect(url_for("ops.expenses"))
    cat = (request.form.get("category_other", "").strip() if request.form.get("category") == "__other"
           else request.form.get("category", "")).strip().lower()[:40] or "other"
    desc = request.form.get("description", "").strip()[:200] or cat.capitalize()
    when = request.form.get("date", "").strip()
    try:
        d = dt.datetime.strptime(when, "%Y-%m-%d")
        if d.year < 2000:
            raise ValueError("too old")
        created = d.replace(hour=12).timestamp() if d.date() != dt.date.today() else time.time()
    except (ValueError, OverflowError, OSError):
        if when:
            flash("Pick a date from the year 2000 on.", "error")
            return redirect(url_for("ops.expenses"))
        created = time.time()
    if created > time.time() + DAY:
        flash("The date can't be in the future.", "error")
        return redirect(url_for("ops.expenses"))
    cols = dict(category=cat, amount=-round(amount, 2), description=desc, truck_id=_id("truck_id", "trucks"),
                driver_id=_id("driver_id", "users"), job_id=_id("job_id", "jobs"), created_at=created)
    eid = request.form.get("id", "")
    u = user()
    if eid.isdecimal():
        row = db.one("SELECT * FROM ledger WHERE id=? AND account='company' AND manual=1 AND amount<0", (int(eid),))
        if not row:
            abort(404)
        db.update("ledger", row["id"], **cols)
        db.audit(u["id"], "expense edited", f"#{row['id']} {cat} {amount}")
        flash("Expense updated.", "success")
    else:
        new = db.insert("ledger", account="company", manual=1, entered_by=u["id"], **cols)
        db.audit(u["id"], "expense added", f"#{new} {cat} {amount}")
        flash(f"Expense recorded: {cat} {db.setting('currency', '$')}{amount:,.2f}.", "success")
    return redirect(url_for("ops.expenses", period=request.form.get("back_period") or "month"))


@bp.route("/expenses/<int:eid>/delete", methods=["POST"])
@login_required(role="admin")
def expense_delete(eid):
    row = db.one("SELECT * FROM ledger WHERE id=? AND account='company' AND manual=1 AND amount<0", (eid,))
    if not row:
        flash("Only expenses entered by hand can be deleted; expenses from the game stay in the books.", "error")
        return redirect(url_for("ops.expenses"))
    db.ex("DELETE FROM ledger WHERE id=?", (eid,))
    db.audit(user()["id"], "expense deleted", f"#{eid} {row['category']} {row['amount']} {row['description']}")
    flash("Expense deleted; the company balance is adjusted.", "success")
    return redirect(url_for("ops.expenses"))


# ------------------------------------------------------------------ driver performance
def _timing(j, contract_deadline=None):
    """('on_time' | 'late' | 'none', minutes late in game time or None)."""
    if j.get("game_time_deadline") and j.get("game_time_end"):
        late = j["game_time_end"] - j["game_time_deadline"]
        return ("late", late) if late > 0 else ("on_time", late)
    if contract_deadline and j.get("finished_at"):
        return ("late", None) if j["finished_at"] > contract_deadline else ("on_time", None)
    return "none", None


@bp.route("/performance")
@login_required
def performance():
    period = request.args.get("period", "all")
    period = period if period in dict(PERIODS) else "all"
    gc, ga = game_sql("j.game")
    jobs = db.q("""SELECT j.*, c.deadline contract_deadline FROM jobs j LEFT JOIN contracts c ON c.id=j.contract_id
                   WHERE j.status='delivered' AND COALESCE(j.flags,'') NOT LIKE '%demo%' AND j.finished_at >= ?""" + gc  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                + " ORDER BY j.finished_at DESC", (_since(period),) + ga)
    drivers = {d["id"]: dict(d, loads=0, km=0.0, on_time=0, late=0, none=0, score=0, revenue=0.0, fines=0.0,
                             deduction=0.0, damage=0.0, late_min=0, last=None)
               for d in db.q("SELECT id, display_name, color, role FROM users WHERE status!='removed'")}
    for j in jobs:
        d = drivers.get(j["user_id"])
        if not d:
            continue
        j["timing"], j["late_min"] = _timing(j, j["contract_deadline"])
        d["loads"] += 1
        d["km"] += j["driven_km"] or j["planned_km"] or 0
        d[j["timing"]] += 1
        d["late_min"] += max(0, j["late_min"] or 0) if j["timing"] == "late" else 0
        d["score"] += j["score"] or 0
        d["revenue"] += j["revenue"] or 0
        d["fines"] += j["fines"] or 0
        d["deduction"] += j["deduction"] or 0
        d["damage"] += j["cargo_damage"] or 0
        d["last"] = max(d["last"] or 0, j["finished_at"] or 0)
    rows = []
    for d in drivers.values():
        timed = d["on_time"] + d["late"]
        d["on_time_pct"] = round(d["on_time"] / timed * 100) if timed else None
        d["avg_score"] = round(d["score"] / d["loads"]) if d["loads"] else None
        d["avg_damage"] = d["damage"] / d["loads"] if d["loads"] else 0
        if d["loads"] or request.args.get("all") == "1":
            rows.append(d)
    rows.sort(key=lambda d: (-d["km"], d["display_name"]))
    sel = request.args.get("driver", "")
    sel = int(sel) if sel.isdecimal() and int(sel) in drivers else None
    loads = [j for j in jobs if j["user_id"] == sel][:200] if sel else []
    fleet = {"loads": len(jobs), "km": sum(d["km"] for d in rows), "on_time": sum(d["on_time"] for d in rows),
             "late": sum(d["late"] for d in rows)}
    timed = fleet["on_time"] + fleet["late"]
    fleet["pct"] = round(fleet["on_time"] / timed * 100) if timed else None
    return render_template("performance.html", rows=rows, period=period, periods=PERIODS, sel=drivers.get(sel),
                           loads=loads, fleet=fleet)


# ------------------------------------------------------------------ ELD & Compliance reset
RESET_PARTS = {"logbook": "hos_logs", "violations": "hos_violations", "dvir": "dvirs", "inspections": "inspections"}


@bp.route("/compliance/reset", methods=["POST"])
@login_required(role="admin")
def compliance_reset():
    from . import compliance
    from .telemetry.service import HUB
    parts = [p for p in RESET_PARTS if request.form.get(p) == "1"]
    who = request.form.get("driver", "")
    game = request.form.get("game", "")
    if not parts:
        flash("Tick at least one thing to reset.", "error")
        return redirect(url_for("comp.page"))
    if request.form.get("confirm", "").strip().upper() != "RESET":
        flash("Type RESET to confirm.", "error")
        return redirect(url_for("comp.page"))
    if who != "all" and not (who.isdecimal() and db.val("SELECT id FROM users WHERE id=?", (int(who),))):
        abort(400)
    if game not in ("ats", "ets2", "both"):
        abort(400)
    uids = [r["id"] for r in db.q("SELECT id FROM users")] if who == "all" else [int(who)]
    marks = ",".join("?" * len(uids))
    gcond, gargs = ("", ()) if game == "both" else (" AND COALESCE(game,'ats')=?", (game,))
    counts, released = {}, 0
    for p in parts:
        table = RESET_PARTS[p]
        if table == "inspections":                       # inspections carry no game column
            cond, args = f"user_id IN ({marks})", tuple(uids)
        else:
            cond, args = f"user_id IN ({marks})" + gcond, tuple(uids) + gargs
        if table in ("dvirs", "inspections"):
            held = {r["truck_id"] for r in db.q(f"SELECT truck_id FROM {table} WHERE {cond} AND truck_id IS NOT NULL", args)}  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        else:
            held = set()
        counts[p] = db.val(f"SELECT COUNT(*) FROM {table} WHERE {cond}", args, 0)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        db.ex(f"DELETE FROM {table} WHERE {cond}", args)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        for tid in held:                                  # holds that came from those reports are lifted
            if not db.val("SELECT COUNT(*) FROM dvirs WHERE truck_id=? AND status='defects'", (tid,), 0) \
                    and db.val("SELECT status FROM trucks WHERE id=?", (tid,)) == "maintenance":
                db.update("trucks", tid, status="active")
                released += 1
    if "logbook" in parts:
        compliance.logbook_changed()                      # simulated rest went with the lines: re-read the clock
        for uid in uids:                                  # the live tracker starts a fresh log line
            h = HUB.tracker(uid).hos
            h.row = h.status = h.last_min = None
            h.cand = None
            h.last_write = h.last_check = 0
    names = {"logbook": "duty-status lines", "violations": "violations", "dvir": "DVIRs", "inspections": "inspections"}
    done = ", ".join(f"{counts[p]} {names[p]}" for p in parts)
    target = "all drivers" if who == "all" else db.val("SELECT display_name FROM users WHERE id=?", (int(who),))
    db.audit(user()["id"], "compliance reset", f"{target} / {game}: {done}")
    flash(f"ELD & Compliance reset for {target} ({'both games' if game == 'both' else game.upper()}): removed {done}."
          + (f" {released} truck(s) released from maintenance hold." if released else "")
          + " Fines already charged stay in the books.", "success")
    return redirect(url_for("comp.page", driver=None if who == "all" else who,
                            game=None if game == "both" else game))


# ------------------------------------------------------------------ clear all data
CLEAR_CONFIRM = "DELETE ALL DATA"
# Everything the company has recorded, children before parents. Users, divisions, settings and the licence are
# handled separately (members can be kept; the owner doing the reset and the settings always stay).
CLEAR_TABLES = [("job_points", "route points"), ("job_events", "job events"), ("ledger", "ledger entries"),
                ("hos_violations", "HOS violations"), ("hos_logs", "logbook lines"), ("dvirs", "DVIRs"),
                ("inspections", "roadside inspections"), ("licence_points", "licence points"), ("licence_tests", "drivers tests"), ("personal_loans", "personal loans"),
                ("licences", "driver's licences"), ("maintenance", "maintenance records"),
                ("leases", "truck leases"), ("trailer_leases", "trailer leases"), ("jobs", "jobs"), ("contracts", "contracts"), ("loans", "loans"), ("trucks", "trucks"),
                ("trailers", "trailers"), ("garages", "garages"), ("event_rsvps", "event sign-ups"),
                ("events", "events"), ("announcements", "announcements"), ("messages", "messages"),
                ("user_achievements", "achievements"), ("notifications", "notifications"),
                ("applications", "applications"), ("road_points", "learned road points"),
                ("map_places", "learned places"), ("login_failures", "failed sign-ins"), ("fluid_samples", "fuel & AdBlue readings"),
                ("audit_log", "audit log lines")]


def data_counts():
    out = [(label, db.val(f"SELECT COUNT(*) FROM {t}", (), 0)) for t, label in CLEAR_TABLES]  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    out.append(("other members", db.val("SELECT COUNT(*) FROM users WHERE role!='owner'", (), 0)))
    return out


def backup_database(tag):
    """Consistent copy of fleet.db (SQLite online backup) in data/backups; returns its path."""
    import sqlite3
    folder = os.path.join(os.path.dirname(db.DB_PATH), "backups")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"fleet-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.db")
    dst = sqlite3.connect(path)
    db.conn().backup(dst)
    dst.close()
    return path


def clear_all_data(owner_id, keep_members=True, restore_capital=True):
    """Wipe the company's records. Backs up first; returns (backup path, {label: rows removed})."""
    from .telemetry.service import HUB
    backup = backup_database("before-clear")
    c = db.conn()
    removed = {}
    c.execute("PRAGMA foreign_keys=OFF")
    try:
        with c:                                            # one transaction: all or nothing
            for table, label in CLEAR_TABLES:
                removed[label] = c.execute(f"DELETE FROM {table}").rowcount  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            if keep_members:
                c.execute("UPDATE users SET xp=0")
            else:
                removed["members"] = c.execute("DELETE FROM users WHERE id != ?", (owner_id,)).rowcount
                c.execute("DELETE FROM divisions")
                c.execute("UPDATE users SET xp=0, division_id=NULL WHERE id=?", (owner_id,))
                c.execute("UPDATE settings SET value=? WHERE key='telemetry_user_id'", (str(owner_id),))
            c.execute("DELETE FROM settings WHERE key='last_upkeep'")
            if restore_capital:
                start = float(db.STARTING_CAPITAL)
                if start:
                    c.execute("INSERT INTO ledger(account, category, amount, description, created_at) "
                              "VALUES ('company', 'capital', ?, 'Starting capital (after data reset)', ?)", (start, time.time()))
    finally:
        c.execute("PRAGMA foreign_keys=ON")
    try:
        c.execute("VACUUM")                                # the deleted rows don't linger in the file
    except Exception as e:                                 # busy (e.g. telemetry writing): compacts next time
        logging.getLogger(__name__).info("VACUUM skipped: %s", e)
    with HUB.lock:                                         # live trackers forget jobs that no longer exist
        HUB.trackers.clear()
    return backup, removed


@bp.route("/settings/clear-data", methods=["POST"])
@login_required(role="owner")
def clear_data():
    from werkzeug.security import check_password_hash
    u = user()
    back = redirect(url_for("pages.settings", tab="data"))
    if request.form.get("understand") != "1":
        flash("Tick the box to confirm you understand everything will be deleted.", "error")
        return back
    if request.form.get("confirm", "").strip() != CLEAR_CONFIRM:
        flash(f"Type {CLEAR_CONFIRM} exactly (capital letters) to confirm.", "error")
        return back
    recent = db.val("SELECT COUNT(*) FROM audit_log WHERE user_id=? AND action='clear data refused' AND created_at > ?",
                    (u["id"], time.time() - 900), 0)
    if recent >= 5:
        flash("Too many wrong passwords. Wait 15 minutes and try again.", "error")
        return back
    if not check_password_hash(u["password_hash"], request.form.get("password", "")):
        db.audit(u["id"], "clear data refused", "wrong password")
        flash("That password isn't right. Nothing was deleted.", "error")
        return back
    keep = request.form.get("keep_members") == "1"
    backup, removed = clear_all_data(u["id"], keep_members=keep, restore_capital=request.form.get("capital") == "1")
    total = sum(removed.values())
    db.audit(u["id"], "all data cleared", f"{total} rows removed; members {'kept' if keep else 'removed'}; backup {backup}")
    flash(f"All company data cleared ({total:,} records). A backup was saved first: {backup}", "success")
    return redirect(url_for("pages.dashboard"))


# ------------------------------------------------------------------ dissolve the company
DISSOLVE_CONFIRM = "DISSOLVE"


def dissolve_company(owner_id, erase=False):
    """End the VTC: every member except the Owner leaves (as if fired: accounts and personal records deleted, their
    agent tokens stop working), recruiting closes, and this PC stops being reachable from the internet (router port
    closed, public address and extra host names forgotten). With erase=True all company data goes too (backup first).
    Returns a dict for the summary."""
    from .cleanup import FORMER_USERNAME, purge_member
    out = {"members": 0, "backup": None, "port": None}
    if erase:
        out["members"] = db.val("SELECT COUNT(*) FROM users WHERE id!=? AND username!=?", (owner_id, FORMER_USERNAME), 0)
        out["backup"], _removed = clear_all_data(owner_id, keep_members=False, restore_capital=True)
    else:
        for m in db.q("SELECT id FROM users WHERE id!=? AND role!='owner' AND username!=?", (owner_id, FORMER_USERNAME)):
            if purge_member(m["id"]):
                out["members"] += 1
    port = db.setting("upnp_port", "") or ""
    if port:
        from . import upnp
        try:
            upnp.close_port(upnp.discover(), int(port))
            out["port"] = "closed"
        except (upnp.UPnPError, ValueError) as e:
            out["port"] = f"not closed ({e})"
    for key, value in (("upnp_port", ""), ("public_address", ""), ("public_address_auto", "0"), ("allowed_hosts", ""),
                       ("recruitment_open", "0"), ("global_board", "0"), ("global_board_drivers", "0"),
                       ("vtc_listed", "0"), ("vtc_profile", ""), ("lan_access", "0")):
        db.set_setting(key, value)
    try:
        from flask import current_app
        from . import network
        network.stop(current_app._get_current_object(), linger=3)    # other PCs can't connect any more
    except Exception as e:
        logging.getLogger(__name__).warning("closing the door for other PCs failed: %s", e)
    from . import global_board
    try:
        global_board.sync()             # take the company off the global leaderboard now (else at the next update)
    except Exception as e:              # dissolving must never fail because of the leaderboard
        logging.getLogger(__name__).warning("leaving the global leaderboard failed: %s", e)
    from . import recruit
    try:
        recruit.push_profile()          # and out of the VTC Finder
    except Exception as e:
        logging.getLogger(__name__).warning("leaving the VTC Finder failed: %s", e)
    return out


@bp.route("/settings/dissolve", methods=["POST"])
@login_required(role="owner")
def dissolve():
    from werkzeug.security import check_password_hash
    u = user()
    back = redirect(url_for("pages.settings", tab="data") + "#dissolve")
    if request.form.get("understand") != "1":
        flash("Tick the box to confirm you understand every member will be removed.", "error")
        return back
    if request.form.get("confirm", "").strip() != DISSOLVE_CONFIRM:
        flash(f"Type {DISSOLVE_CONFIRM} exactly (capital letters) to confirm.", "error")
        return back
    recent = db.val("SELECT COUNT(*) FROM audit_log WHERE user_id=? AND action='dissolve refused' AND created_at > ?",
                    (u["id"], time.time() - 900), 0)
    if recent >= 5:
        flash("Too many wrong passwords. Wait 15 minutes and try again.", "error")
        return back
    if not check_password_hash(u["password_hash"], request.form.get("password", "")):
        db.audit(u["id"], "dissolve refused", "wrong password")
        flash("That password isn't right. Nothing was changed.", "error")
        return back
    erase = request.form.get("erase") == "1"
    company = db.setting("company_name", "") or "The company"
    r = dissolve_company(u["id"], erase=erase)
    db.audit(u["id"], "company dissolved", f"{r['members']} member(s) removed; data {'erased, backup ' + r['backup'] if erase else 'kept'}; "
                                           f"router port {r['port'] or 'was not open'}")
    msg = f"{company} is dissolved. {r['members']} member(s) removed; their connections stop working and close by themselves."
    msg += (f" All company data was erased (backup: {r['backup']})." if erase else
            " Your books, fleet and logbook are kept; past deliveries show as \"Former driver\".")
    if r["port"] == "closed":
        msg += " The router no longer lets the internet in."
    elif r["port"]:
        msg += f" The router port is {r['port']}: remove the forward in your router's settings."
    from flask import current_app
    if current_app.config.get("LAN"):
        msg += " To stop other devices on your network connecting too, turn off tablet access, or start Fleet Manager Pro normally if you used the LAN file."
    flash(msg, "success")
    return redirect(url_for("pages.settings", tab="data"))


# ------------------------------------------------------------------ clear demo jobs
DEMO_TRUCK = ("vehicle.kenworth.w900", "FMP 0001")          # the simulator's truck (telemetry/demo.py)


def demo_counts():
    ids = [r["id"] for r in db.q("SELECT id FROM jobs WHERE COALESCE(flags,'') LIKE '%demo%'")]
    return len(ids), (db.val(f"SELECT COUNT(*) FROM ledger WHERE job_id IN ({','.join('?' * len(ids))})", ids, 0) if ids else 0)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def clear_demo_jobs():
    """Remove everything the telemetry simulator created: its jobs, their money, events and route points,
    the XP they gave, and the simulator's own truck. Real jobs are never touched."""
    jobs = db.q("SELECT id, user_id, xp, contract_id FROM jobs WHERE COALESCE(flags,'') LIKE '%demo%'")
    ids = [j["id"] for j in jobs]
    out = {"jobs": len(ids), "ledger": 0, "trucks": 0}
    c = db.conn()
    with c:
        if ids:
            marks = ",".join("?" * len(ids))
            out["ledger"] = c.execute(f"DELETE FROM ledger WHERE job_id IN ({marks})", ids).rowcount  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            c.execute(f"DELETE FROM job_events WHERE job_id IN ({marks})", ids)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            c.execute(f"DELETE FROM job_points WHERE job_id IN ({marks})", ids)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            c.execute(f"UPDATE contracts SET job_id=NULL, status=CASE WHEN status='completed' THEN 'open' ELSE status END, "  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                      f"completed_at=NULL WHERE job_id IN ({marks})", ids)
            c.execute(f"UPDATE inspections SET job_id=NULL WHERE job_id IN ({marks})", ids)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            for j in jobs:
                if j["xp"]:
                    c.execute("UPDATE users SET xp = MAX(0, xp - ?) WHERE id=?", (j["xp"], j["user_id"]))
            c.execute(f"DELETE FROM jobs WHERE id IN ({marks})", ids)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        for t in c.execute("SELECT id, brand, model, plate FROM trucks WHERE game_truck_id=? AND plate=? AND auto_detected=1",
                           DEMO_TRUCK).fetchall():
            if c.execute("SELECT COUNT(*) FROM jobs WHERE truck_id=?", (t["id"],)).fetchone()[0]:
                continue                                   # a real job used it: keep
            out["ledger"] += c.execute("DELETE FROM ledger WHERE category='repairs' AND description=?",
                                       (f"Repair {t['brand']} {t['model']} ({t['plate']})",)).rowcount
            c.execute("DELETE FROM maintenance WHERE truck_id=?", (t["id"],))
            c.execute("UPDATE dvirs SET truck_id=NULL WHERE truck_id=?", (t["id"],))
            c.execute("UPDATE inspections SET truck_id=NULL WHERE truck_id=?", (t["id"],))
            c.execute("DELETE FROM trucks WHERE id=?", (t["id"],))
            out["trucks"] += 1
    return out


@bp.route("/jobs/clear-demo", methods=["POST"])
@login_required(role="admin")
def clear_demo():
    if request.form.get("confirm") != "1":
        abort(400)
    out = clear_demo_jobs()
    db.audit(user()["id"], "demo jobs cleared", f"{out['jobs']} jobs, {out['ledger']} ledger entries, {out['trucks']} demo truck(s)")
    flash(f"Cleared {out['jobs']} demo job(s) and {out['ledger']} ledger entries they made"
          + (f", plus the simulator's truck" if out["trucks"] else "") + ". Real jobs weren't touched.", "success")
    return redirect(request.form.get("back") if request.form.get("back") in ("/jobs", "/settings?tab=telemetry", "/finance")
                    else url_for("pages.jobs"))


# ------------------------------------------------------------------ truck / trailer dealer prices
_VEHICLES = None


def vehicle_options():
    """{game: {"trucks": [{id, brand, model, price, from}], "trailers": [{id, brand, model, from}]}} (tools/build_vehicles.py)."""
    global _VEHICLES
    if _VEHICLES is None:
        try:
            with open(os.path.join(RES, "fmpro", "cargo_list.json"), encoding="utf-8") as f:
                _VEHICLES = json.load(f).get("vehicles", {})
        except (OSError, ValueError):
            _VEHICLES = {}
    return _VEHICLES


def dealer_price(brand, model, kind="trucks"):
    """The game's new price for a truck / trailer of this brand and model, if it's a stock one (else None)."""
    want = f"{brand} {model}".lower().replace("-", " ")
    best = None
    for g in vehicle_options().values():
        for v in g.get(kind, []):
            name = f"{v['brand']} {v['model']}".lower().replace("-", " ")
            if want == name or (want.startswith(name + " ") and (best is None or len(name) > best[0])):
                best = (len(name), v.get("price") or v["from"])
    return best[1] if best else None


@bp.route("/fleet/trailer/<int:tid>/sell", methods=["POST"])
@login_required(role="admin")
def trailer_sell(tid):
    t = db.one("SELECT * FROM trailers WHERE id=? AND status NOT IN ('sold','returned')", (tid,))
    if not t:
        abort(404)
    if t.get("leased"):
        flash("A leased trailer can't be sold - end its lease on Trailer Leasing first.", "error")
        return redirect(url_for("pages.fleet"))
    price = _num("price", 0, 1e8) or 0
    db.update("trailers", tid, status="sold", garage_id=None)
    if price:
        economy.post("trailer sale", price, f"Sold trailer {t['name']}")
    db.audit(user()["id"], "trailer sold", f"#{tid} {t['name']} for {price:,.0f}")
    flash(f"Trailer {t['name']} sold{' for ' + db.setting('currency', '$') + f'{price:,.0f}' if price else ''}.", "success")
    return redirect(url_for("pages.fleet"))


# ------------------------------------------------------------------ garages
_GARAGES = None


def garage_options():
    """{game: [[city id, city name, label]]} for every garage location in the game (from the games' saves)."""
    global _GARAGES
    if _GARAGES is None:
        try:
            with open(os.path.join(RES, "fmpro", "cargo_list.json"), encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        out = {}
        for g in ("ats", "ets2"):
            cities = {c["id"]: c for c in raw.get("cities", {}).get(g, [])}
            rows = []
            for cid in raw.get("garages", {}).get(g, []):
                c = cities.get(cid)
                if c:
                    rows.append([cid, c["name"], c["name"] + (f" ({c['native']})" if c.get("native") else "")
                                 + (f", {c['country']}" if c.get("country") else "")])
            out[g] = sorted(rows, key=lambda r: r[2].lower())
        _GARAGES = out
    return _GARAGES


@bp.route("/fleet/garage/<int:gid>/delete", methods=["POST"])
@login_required(role="admin")
def garage_delete(gid):
    g = db.one("SELECT * FROM garages WHERE id=?", (gid,))
    if not g:
        abort(404)
    trucks = db.val("SELECT COUNT(*) FROM trucks WHERE garage_id=?", (gid,), 0)
    trailers = db.val("SELECT COUNT(*) FROM trailers WHERE garage_id=?", (gid,), 0)
    db.ex("UPDATE trucks SET garage_id=NULL WHERE garage_id=?", (gid,))
    db.ex("UPDATE trailers SET garage_id=NULL WHERE garage_id=?", (gid,))
    db.ex("DELETE FROM garages WHERE id=?", (gid,))
    price = _num("sale_price", 0, 1e8) or 0
    if price:
        economy.post("garage sale", price, f"Sold the {g['size']} garage in {g['city']}")
    db.audit(user()["id"], "garage deleted", f"#{gid} {g['city']} ({g['game']})")
    moved = f" {trucks} truck(s) and {trailers} trailer(s) no longer have a home garage." if trucks or trailers else ""
    flash(f"Garage in {g['city']} removed{' and sold' if price else ''}.{moved}", "success")
    return redirect(url_for("pages.fleet"))
