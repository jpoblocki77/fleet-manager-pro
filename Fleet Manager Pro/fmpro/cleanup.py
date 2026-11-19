"""Removing things for good: sold / returned trucks, sold trailers, and fired members.

Money history never changes: ledger lines keep their amounts and descriptions, deliveries stay in the logbook.
What goes is the row itself (and, for a fired member, everything personal), so the lists stay tidy.
"""
import secrets
import time

from flask import Blueprint, abort, flash, redirect, request, url_for
from werkzeug.security import generate_password_hash

from . import db
from .auth import login_required, user

bp = Blueprint("cleanup", __name__)
FORMER_USERNAME = "former-drivers"
GONE_TRUCK = ("sold", "returned")
# a fired member's own records - deleted with them
PERSONAL = [("messages", "from_id"), ("messages", "to_id"), ("notifications", "user_id"), ("user_achievements", "user_id"),
            ("event_rsvps", "user_id"), ("hos_logs", "user_id"), ("hos_violations", "user_id"), ("dvirs", "user_id"),
            ("inspections", "user_id"), ("fluid_samples", "user_id")]
# company records that must keep a driver: handed to the "Former driver" placeholder
KEEP_WITH_FORMER = [("jobs", "user_id"), ("job_events", "user_id"), ("ledger", "user_id"), ("ledger", "driver_id")]


def _tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _columns(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _detach(c, column, ids, skip=()):
    """Set `column` to NULL wherever it points at one of `ids` (every table that has that column)."""
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    for t in _tables(c):
        if t not in skip and column in _columns(c, t):
            c.execute(f"UPDATE {t} SET {column}=NULL WHERE {column} IN ({marks})", tuple(ids))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def delete_trucks(ids):
    """Delete sold / returned trucks. Their jobs, maintenance, leases and ledger lines stay (unlinked)."""
    ids = [r["id"] for r in db.q("SELECT id FROM trucks WHERE status IN ('sold','returned') AND id IN (%s)"  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                                 % ",".join("?" * len(ids)), tuple(ids))] if ids else []
    if ids:
        c = db.conn()
        with c:
            _detach(c, "truck_id", ids)
            c.execute("DELETE FROM trucks WHERE id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    return len(ids)


def delete_trailers(ids):
    ids = [r["id"] for r in db.q("SELECT id FROM trailers WHERE status='sold' AND id IN (%s)" % ",".join("?" * len(ids)),  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                                 tuple(ids))] if ids else []
    if ids:
        c = db.conn()
        with c:
            _detach(c, "trailer_id", ids)
            c.execute("DELETE FROM trailers WHERE id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    return len(ids)


def former_user_id():
    """The placeholder that keeps fired members' deliveries and pay in the books. It can never sign in."""
    fid = db.val("SELECT id FROM users WHERE username=?", (FORMER_USERNAME,))
    if not fid:
        fid = db.insert("users", username=FORMER_USERNAME, display_name="Former driver", role="driver", status="removed",
                        password_hash=generate_password_hash(secrets.token_urlsafe(32)), color="#6b7a90",
                        created_at=time.time())
    return fid


def purge_member(uid):
    """Delete a member and everything personal; hand their deliveries and pay to 'Former driver'. Returns their name."""
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u or u["role"] == "owner" or u["username"] == FORMER_USERNAME:
        return None
    fid = former_user_id()
    c = db.conn()
    with c:
        for t, col in PERSONAL:
            if t in _tables(c) and col in _columns(c, t):
                c.execute(f"DELETE FROM {t} WHERE {col}=?", (uid,))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        for t, col in KEEP_WITH_FORMER:
            if t in _tables(c) and col in _columns(c, t):
                c.execute(f"UPDATE {t} SET {col}=? WHERE {col}=?", (fid, uid))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        # contracts they had claimed go back on the board
        c.execute("UPDATE contracts SET status='open', assigned_user_id=NULL WHERE assigned_user_id=? AND status='assigned'", (uid,))
        # any other reference to them (assigned truck, reviewer, author, creator...) is cleared
        for t in _tables(c):
            if t == "users":
                continue
            for r in c.execute(f"PRAGMA foreign_key_list({t})").fetchall():
                if r[2] == "users":
                    c.execute(f"UPDATE {t} SET {r[3]}=NULL WHERE {r[3]}=?", (uid,))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        if "user_id" in _columns(c, "road_points"):
            c.execute("UPDATE road_points SET user_id=NULL WHERE user_id=?", (uid,))
        c.execute("DELETE FROM login_failures WHERE username=? COLLATE NOCASE", (u["username"],))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return u["display_name"]


def former_members():
    return db.q("SELECT id, display_name FROM users WHERE status='removed' AND username!=? AND role!='owner'", (FORMER_USERNAME,))


# ------------------------------------------------------------------ routes
@bp.route("/fleet/truck/<int:tid>/delete", methods=["POST"])
@login_required(role="admin")
def truck_delete(tid):
    t = db.one("SELECT * FROM trucks WHERE id=?", (tid,)) or abort(404)
    if t["status"] not in GONE_TRUCK:
        flash("Only sold or returned trucks can be deleted from the list. Sell it first.", "error")
        return redirect(url_for("pages.truck_detail", tid=tid))
    delete_trucks([tid])
    db.audit(user()["id"], "truck deleted", f"#{tid} {t['brand']} {t['model']} ({t['status']})")
    flash(f"{t['brand']} {t['model']} deleted from the fleet list. Its jobs and ledger lines are kept.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/fleet/trailer/<int:tid>/delete", methods=["POST"])
@login_required(role="admin")
def trailer_delete(tid):
    t = db.one("SELECT * FROM trailers WHERE id=?", (tid,)) or abort(404)
    if t["status"] != "sold":
        flash("Only sold trailers can be deleted from the list. Sell it first.", "error")
        return redirect(url_for("pages.fleet"))
    delete_trailers([tid])
    db.audit(user()["id"], "trailer deleted", f"#{tid} {t['name']}")
    flash(f"Trailer {t['name']} deleted from the list.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/fleet/clear-sold", methods=["POST"])
@login_required(role="admin")
def clear_sold():
    trucks = delete_trucks([r["id"] for r in db.q("SELECT id FROM trucks WHERE status IN ('sold','returned')")])
    trailers = delete_trailers([r["id"] for r in db.q("SELECT id FROM trailers WHERE status='sold'")])
    db.audit(user()["id"], "sold items cleared", f"{trucks} truck(s), {trailers} trailer(s)")
    flash(f"Deleted {trucks} sold/returned truck(s) and {trailers} sold trailer(s) from the lists. "
          "Their jobs and ledger lines are kept.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/drivers/<int:uid>/fire", methods=["POST"])
@bp.route("/drivers/<int:uid>/remove", methods=["POST"])
@login_required(role="admin")
def fire(uid):
    me = user()
    p = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not p or p["role"] == "owner" or uid == me["id"] or p["username"] == FORMER_USERNAME:
        abort(403)
    if p["role"] == "admin" and me["role"] != "owner":
        abort(403)                                   # only the owner fires managers
    if request.form.get("confirm", "").strip().lower() != (p["display_name"] or "").strip().lower():
        flash(f"Type {p['display_name']} exactly to confirm firing them.", "error")
        return redirect(url_for("pages.driver_profile", uid=uid))
    purge_member(uid)
    db.audit(me["id"], "member fired", f"member #{uid} ({p['role']}) deleted")
    flash(f"{p['display_name']} has been fired and deleted. Their deliveries and pay stay in the books as \"Former driver\".",
          "success")
    return redirect(url_for("pages.drivers"))


@bp.route("/drivers/purge-former", methods=["POST"])
@login_required(role="admin")
def purge_former():
    if user()["role"] != "owner":
        abort(403)
    n = sum(1 for m in former_members() if purge_member(m["id"]))
    db.audit(user()["id"], "former members deleted", f"{n} member(s)")
    flash(f"Deleted {n} former member(s). Their deliveries and pay stay in the books as \"Former driver\".", "success")
    return redirect(url_for("pages.drivers"))
