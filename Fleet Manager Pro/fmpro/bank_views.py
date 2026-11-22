"""Bank page: a driver's own account, next pay, pay slips and statement. Managers can look at drivers they outrank."""
from flask import Blueprint, abort, render_template, request

from . import bank, db
from .auth import can, login_required, role_level, user

bp = Blueprint("bank", __name__)


def may_see(target):
    """A bank account is private: its owner, and a Manager who outranks them (the Owner sees all)."""
    me = user()
    return me["id"] == target["id"] or (can("admin") and (me["role"] == "owner"
                                                          or role_level(me["role"]) > role_level(target["role"])))


@bp.route("/bank")
@login_required
def page():
    bank.process()
    me, pick = user(), request.args.get("driver", "")
    people = []
    if can("admin"):
        people = [m for m in db.q("SELECT id, display_name, color, role FROM users WHERE status!='removed' ORDER BY "
                                  "display_name COLLATE NOCASE") if may_see(m)]
    who = me
    if pick.isdecimal() and int(pick[:9]) != me["id"]:
        who = db.one("SELECT id, display_name, role FROM users WHERE id=? AND status!='removed'", (int(pick[:9]),)) or abort(404)
        if not may_see(who):
            abort(403)
    for p in people:
        p["balance"], p["pending"] = bank.balance(p["id"]), bank.pending(p["id"])
    uid = who["id"]
    return render_template("bank.html", who=who, mine=uid == me["id"], number=bank.number(uid), balance=bank.balance(uid),
                           pending=bank.pending(uid), waiting=bank.waiting(uid), payday=bank.next_payday(),
                           rows=bank.statement(uid), stubs=bank.stubs(uid), people=people)
