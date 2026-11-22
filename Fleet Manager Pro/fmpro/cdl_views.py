"""CDL & Points page: the driver's own licence, points and tickets, the running drivers test, and for managers everyone's."""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from . import cdl, cdl_test, db
from .auth import can, login_required, role_level, user
from .telemetry.service import HUB

bp = Blueprint("cdl", __name__)


def _may_manage(target):
    me = user()
    return can("admin") and (me["role"] == "owner" or role_level(me["role"]) > role_level(target["role"]))


def _members():
    return db.q("SELECT id, display_name, color, role, status FROM users WHERE status!='removed' "
                "AND username!='former-drivers' ORDER BY display_name COLLATE NOCASE")


def _history(uid):
    rows = db.q("SELECT * FROM licence_points WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,))
    for r in rows:
        r["text"] = cdl.OFFENCES.get(r["code"], cdl.OFFENCES["other"])[2]
    return rows


def _mine(uid):
    info = cdl.info(uid)
    return info, (cdl_test.view(info["test"]) if info["test"] else None)


@bp.route("/cdl")
@login_required
def page():
    me = user()
    info, test = _mine(me["id"])
    team, shown = [], None
    if can("admin"):
        for m in _members():
            team.append(dict(m, lic=cdl.info(m["id"]), manage=_may_manage(m)))
        pick = request.args.get("driver", "")
        shown = next((m for m in team if pick.isdecimal() and m["id"] == int(pick[:9])), None)
    tests = db.q("SELECT status, score, reason, started_at, finished_at FROM licence_tests WHERE user_id=? AND "
                 "status!='active' ORDER BY id DESC LIMIT 10", (me["id"],))
    from . import economy
    return render_template(
        "cdl.html", on=cdl.enabled(), lic=info, test=test, history=_history(me["id"]), tests=tests, team=team, shown=shown,
        shown_history=_history(shown["id"]) if shown else [], offences=cdl.OFFENCES, bands=cdl.SPEED_BANDS,
        max_points=cdl.MAX_POINTS, fine=cdl.fine_cost(), speed_min=cdl.speed_minutes(), wallet=economy.balance("driver", me["id"]),
        fines=db.setting("drivers_pay_fines", "1") == "1", tickets=db.setting("licence_tickets", "1") == "1", live=bool(HUB.get(me["id"])), T=cdl_test)


@bp.route("/cdl/test/panel")
@login_required
def panel():
    _info, test = _mine(user()["id"])
    if not test:
        return jsonify(running=False)
    return jsonify(running=True, html=render_template("cdl_panel.html", test=test, live=bool(HUB.get(user()["id"]))))


def _done(ok, text):
    flash(text, "success" if ok else "error")
    return redirect(url_for("cdl.page"))


@bp.route("/cdl/test/start", methods=["POST"])
@login_required
def start():
    e = HUB.get(user()["id"])
    frame = e["frame"] if e else {}
    return _done(*cdl.start_test(user()["id"], frame.get("values"), bool(frame.get("demo"))))


@bp.route("/cdl/test/cancel", methods=["POST"])
@login_required
def cancel():
    ok = cdl.cancel_test(user()["id"])
    return _done(ok, "The drivers test was cancelled." if ok else "No drivers test is running.")


@bp.route("/cdl/pay", methods=["POST"])
@login_required
def pay():
    if not cdl.enabled():
        abort(403)
    return _done(*cdl.pay_fine(user()["id"]))


@bp.route("/cdl/issue-all", methods=["POST"])
@login_required(role="admin")
def issue_all():
    n = 0
    for m in _members():
        if (_may_manage(m) or m["id"] == user()["id"] and user()["role"] == "owner") and cdl.info(m["id"])["status"] == "none":
            if cdl.issue(m["id"], "Issued to current drivers"):
                n += 1
                if m["id"] != user()["id"]:
                    db.notify(m["id"], "You were given a driver's licence with all its points.", "/cdl", "success")
    db.audit(user()["id"], "licences issued", f"{n} current driver(s)")
    return _done(True, f"{n} licence{'s' if n != 1 else ''} issued.")


@bp.route("/cdl/driver/<int:uid>", methods=["POST"])
@login_required(role="admin")
def manage(uid):
    target = db.one("SELECT id, display_name, role FROM users WHERE id=? AND status!='removed'", (uid,)) or abort(404)
    if not _may_manage(target):
        abort(403)
    action, who, me = request.form.get("action"), target["display_name"], user()
    if action == "issue":
        ok = cdl.issue(uid, f"Issued by {me['display_name']}")
        text, news = f"{who} has a licence with all its points.", "You were given a driver's licence with all its points."
        cdl.cancel_test(uid)
    elif action == "suspend":
        ok = cdl.suspend(uid, f"had it taken away by {me['display_name']}")
        text, news = f"{who}'s licence is suspended.", ""
    elif action == "clear":
        ok = cdl.clear_points(uid)
        text, news = f"{who}'s points are cleared.", "A manager cleared the points on your licence."
    else:
        abort(400)
    if not ok:
        return _done(False, "Nothing to change.")
    db.audit(me["id"], f"licence {action}", f"#{uid} {who}")
    if news and uid != me["id"]:
        db.notify(uid, news, "/cdl", "success")
    return _done(True, text)
