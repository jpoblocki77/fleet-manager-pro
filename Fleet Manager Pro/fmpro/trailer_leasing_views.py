"""Trailer leasing pages: plans and calculator, leasing a trailer, a lease's page, ending a lease, managing plans.
Works like the truck leasing pages (leasing_views.py) without the mileage allowance."""
import time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db, trailer_leasing as tl
from .auth import game_scope, login_required, user

bp = Blueprint("tlease", __name__)


def _f(name, lo, hi, default=None):
    try:
        v = float(str(request.form.get(name, "")).replace(",", "").strip())
    except ValueError:
        return default
    return default if v != v else max(lo, min(hi, v))


@bp.route("/trailer-leasing")
@login_required
def page():
    from . import economy
    from .ops import vehicle_options
    leases = db.q("""SELECT l.*, t.name, t.brand, t.plate, t.body_type FROM trailer_leases l
                     LEFT JOIN trailers t ON t.id=l.trailer_id
                     ORDER BY l.status NOT IN ('active','matured'), l.started_at DESC""")
    for l in leases:
        l["st"] = tl.status(l)
    active = [l for l in leases if l["status"] in tl.LIVE]
    weekly = sum(l["payment"] for l in active if l["status"] == "active")
    spent = db.val("SELECT -SUM(amount) FROM ledger WHERE account='company' AND category LIKE 'trailer lease%'", (), 0) or 0
    own = db.q("SELECT id, name, brand, plate, body_type, purchase_price FROM trailers WHERE status='active' "
               "AND COALESCE(leased,0)=0 ORDER BY name")
    return render_template("trailer_leasing.html", plans=tl.plans(False), active=active,
                           history=[l for l in leases if l not in active], weekly=weekly, spent=spent,
                           vehicles=vehicle_options(), own_trailers=own, balance=economy.balance(),
                           form_game=game_scope() or db.setting("default_game", "ats"))


@bp.route("/trailer-leasing/new", methods=["POST"])
@login_required(role="admin")
def new():
    from .ops import vehicle_options
    from .pages import affordable
    plan = db.one("SELECT * FROM trailer_lease_plans WHERE id=? AND active=1", (request.form.get("plan_id", "0"),))
    if not plan:
        flash("Pick a lease plan.", "error")
        return redirect(url_for("tlease.page"))
    price = _f("price", 0, 1e8)
    if not price or price < 1000:
        flash("Enter the trailer's price (at least 1,000).", "error")
        return redirect(url_for("tlease.page"))
    q = tl.quote(price, plan)
    if request.form.get("source") == "existing":
        t = db.one("SELECT * FROM trailers WHERE id=? AND status='active' AND COALESCE(leased,0)=0",
                   (request.form.get("trailer_id", "0"),))
        if not t:
            flash("Pick one of your trailers that isn't already leased.", "error")
            return redirect(url_for("tlease.page"))
        what = t["name"]
    else:
        brand, model = request.form.get("brand", "").strip()[:64], request.form.get("model", "").strip()[:64]
        if not brand or not model:
            flash("Pick a trailer from the dealer list (or type its brand and model).", "error")
            return redirect(url_for("tlease.page"))
        what, t = f"{brand} {model}", None
    if not affordable(q["down"], f"the down payment on the {what}"):
        return redirect(url_for("tlease.page"))
    if not t:
        types = {tid: label for g in vehicle_options().values() for tid, label in g.get("trailer_types", [])}
        trid = db.insert("trailers", name=what, brand=brand, body_type=types.get(request.form.get("body_type", ""), ""),
                         plate=request.form.get("plate", "").strip()[:32], purchase_price=0, created_at=time.time())
    else:
        trid = t["id"]
    lid = tl.start(trid, plan, price, user()["id"])
    db.audit(user()["id"], "trailer leased", f"trailer lease #{lid} {what} {plan['name']} {price:,.0f}")
    cur = db.setting("currency", "$")
    flash(f"Leased the {what} on the {plan['name']} plan: {cur}{q['down']:,.2f} down, then {cur}{q['payment']:,.2f} a week "
          f"for {q['weeks']} weeks.", "success")
    return redirect(url_for("tlease.detail", lid=lid))


@bp.route("/fleet/trailer-lease/<int:lid>")
@login_required
def detail(lid):
    l = db.one("""SELECT l.*, t.name, t.brand, t.plate, t.body_type FROM trailer_leases l
                  LEFT JOIN trailers t ON t.id=l.trailer_id WHERE l.id=?""", (lid,)) or abort(404)
    st = tl.status(l)
    paid = db.q("SELECT category, amount, description, created_at FROM ledger WHERE account='company' AND "
                "description LIKE ? ORDER BY created_at, id", (f"Trailer lease #{lid} %",))
    plan = {"down_pct": l["down"] / l["price"] * 100 if l["price"] else 0,
            "residual_pct": l["residual"] / l["price"] * 100 if l["price"] else 0, "apr": l["apr"], "weeks": l["weeks"]}
    return render_template("trailer_lease.html", l=l, st=st, q=tl.quote(l["price"], plan), paid=paid,
                           paid_total=-sum(p["amount"] for p in paid), plans=tl.plans())


@bp.route("/fleet/trailer-lease/<int:lid>/end", methods=["POST"])
@login_required(role="admin")
def end(lid):
    from .pages import affordable
    l = db.one("SELECT * FROM trailer_leases WHERE id=? AND status IN ('active','matured')", (lid,)) or abort(404)
    action = request.form.get("action")
    st = tl.status(l)
    new_plan = None
    if action == "buyout":
        if not affordable(st["balance_left"] + l["residual"], "buying out this trailer lease"):
            return redirect(url_for("tlease.detail", lid=lid))
    elif action == "renew":
        new_plan = db.one("SELECT * FROM trailer_lease_plans WHERE id=? AND active=1", (request.form.get("plan_id", "0"),))
        if not new_plan or not st["matured"]:
            flash("A lease can be renewed once its last payment is made; pick the new plan.", "error")
            return redirect(url_for("tlease.detail", lid=lid))
    elif action != "return":           # returning is always allowed, like trucks, so a lease can't trap the company
        abort(400)
    cost, msg = tl.finish(l, action, user()["id"], new_plan)
    db.audit(user()["id"], f"trailer lease {action}", f"#{lid} cost {cost:,.2f}")
    flash(f"{msg} Cost: {db.setting('currency', '$')}{cost:,.2f}.", "success")
    return redirect(url_for("tlease.page"))


@bp.route("/trailer-leasing/plans/save", methods=["POST"])
@login_required(role="admin")
def plan_save():
    name = request.form.get("name", "").strip()[:40]
    weeks = _f("weeks", 1, 520)
    vals = dict(down_pct=_f("down_pct", 0, 90), apr=_f("apr", 0, 60), residual_pct=_f("residual_pct", 0, 95),
                early_fee_pct=_f("early_fee_pct", 0, 100, 30))
    if not name or weeks is None or None in vals.values():
        flash("Fill in every field of the lease plan.", "error")
        return redirect(url_for("tlease.page") + "#plans")
    vals.update(name=name, weeks=int(weeks))
    pid = request.form.get("id", "")
    if pid.isdecimal() and db.val("SELECT id FROM trailer_lease_plans WHERE id=?", (int(pid),)):
        db.update("trailer_lease_plans", int(pid), **vals)
        flash(f"Trailer lease plan {name} updated. Leases already signed keep their own terms.", "success")
    else:
        db.insert("trailer_lease_plans", sort=99, created_at=time.time(), **vals)
        flash(f"Trailer lease plan {name} added.", "success")
    db.audit(user()["id"], "trailer lease plan saved", name)
    return redirect(url_for("tlease.page") + "#plans")


@bp.route("/trailer-leasing/plans/<int:pid>/toggle", methods=["POST"])
@login_required(role="admin")
def plan_toggle(pid):
    p = db.one("SELECT * FROM trailer_lease_plans WHERE id=?", (pid,)) or abort(404)
    db.update("trailer_lease_plans", pid, active=0 if p["active"] else 1)
    flash(f"Trailer lease plan {p['name']} {'hidden' if p['active'] else 'offered again'}.", "success")
    return redirect(url_for("tlease.page") + "#plans")
