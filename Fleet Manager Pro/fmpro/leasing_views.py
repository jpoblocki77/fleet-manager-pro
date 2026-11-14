"""Leasing pages: plans and calculator, leasing a truck, a lease's page, ending a lease, managing plans."""
import time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db, leasing
from .auth import can, current_units, game_scope, login_required, user

bp = Blueprint("lease", __name__)
KM_PER_MI = leasing.KM_PER_MI


def _f(name, lo, hi, default=None):
    try:
        v = float(str(request.form.get(name, "")).replace(",", "").strip())
    except ValueError:
        return default
    return default if v != v else max(lo, min(hi, v))


@bp.route("/leasing")
@login_required
def page():
    from .ops import vehicle_options
    leases = db.q("""SELECT l.*, t.brand, t.model, t.plate, t.odometer, u.display_name driver FROM leases l
                     LEFT JOIN trucks t ON t.id=l.truck_id LEFT JOIN users u ON u.id=t.assigned_user_id
                     ORDER BY l.status NOT IN ('active','matured'), l.started_at DESC""")
    for l in leases:
        l["st"] = leasing.status(l)
    active = [l for l in leases if l["status"] in ("active", "matured")]
    weekly = sum(l["payment"] for l in active if l["status"] == "active")
    spent = db.val("SELECT -SUM(amount) FROM ledger WHERE account='company' AND category LIKE 'lease%'", (), 0) or 0
    own = db.q("SELECT id, brand, model, plate, odometer, purchase_price FROM trucks WHERE status='active' "
               "AND COALESCE(leased,0)=0 ORDER BY brand, model")
    from . import economy
    return render_template("leasing.html", plans=leasing.plans(False), active=active, history=[l for l in leases if l not in active],
                           weekly=weekly, spent=spent, vehicles=vehicle_options(), own_trucks=own, balance=economy.balance(),
                           form_game=game_scope() or db.setting("default_game", "ats"), km_per_mi=KM_PER_MI)


@bp.route("/leasing/new", methods=["POST"])
@login_required(role="admin")
def new():
    from .pages import affordable
    plan = db.one("SELECT * FROM lease_plans WHERE id=? AND active=1", (request.form.get("plan_id", "0"),))
    if not plan:
        flash("Pick a lease plan.", "error")
        return redirect(url_for("lease.page"))
    price = _f("price", 0, 1e8)
    if not price or price < 1000:
        flash("Enter the truck's price (at least 1,000).", "error")
        return redirect(url_for("lease.page"))
    q = leasing.quote(price, plan)
    if request.form.get("source") == "existing":
        t = db.one("SELECT * FROM trucks WHERE id=? AND status='active' AND COALESCE(leased,0)=0", (request.form.get("truck_id", "0"),))
        if not t:
            flash("Pick one of your trucks that isn't already leased.", "error")
            return redirect(url_for("lease.page"))
        what = f"{t['brand']} {t['model']}"
    else:
        brand, model = request.form.get("brand", "").strip()[:64], request.form.get("model", "").strip()[:64]
        if not brand or not model:
            flash("Pick a truck from the dealer list (or type its brand and model).", "error")
            return redirect(url_for("lease.page"))
        what, t = f"{brand} {model}", None
    if not affordable(q["down"], f"the down payment on the {what}"):
        return redirect(url_for("lease.page"))
    if not t:
        game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else None
        tid = db.insert("trucks", brand=brand, model=model, plate=request.form.get("plate", "").strip()[:32], game=game,
                        purchase_price=0, odometer=0, created_at=time.time(), notes=f"Leased ({plan['name']})")
    else:
        tid = t["id"]
    lid = leasing.start(tid, plan, price, user()["id"])
    db.audit(user()["id"], "truck leased", f"lease #{lid} {what} {plan['name']} {price:,.0f}")
    cur = db.setting("currency", "$")
    flash(f"Leased the {what} on the {plan['name']} plan: {cur}{q['down']:,.2f} down, then {cur}{q['payment']:,.2f} a week "
          f"for {q['weeks']} weeks.", "success")
    return redirect(url_for("lease.detail", lid=lid))


@bp.route("/fleet/lease/<int:lid>")
@login_required
def detail(lid):
    l = db.one("""SELECT l.*, t.brand, t.model, t.plate, t.odometer, t.game FROM leases l LEFT JOIN trucks t ON t.id=l.truck_id
                  WHERE l.id=?""", (lid,)) or abort(404)
    st = leasing.status(l)
    paid = db.q("SELECT category, amount, description, created_at FROM ledger WHERE account='company' AND "
                "description LIKE ? ORDER BY created_at, id", (f"Lease #{lid} %",))
    plan = {"down_pct": l["down"] / l["price"] * 100 if l["price"] else 0, "residual_pct": l["residual"] / l["price"] * 100
            if l["price"] else 0, "apr": l["apr"], "weeks": l["weeks"]}
    q = leasing.quote(l["price"], plan)
    return render_template("lease.html", l=l, st=st, q=q, paid=paid, paid_total=-sum(p["amount"] for p in paid),
                           plans=leasing.plans(), km_per_mi=KM_PER_MI)


@bp.route("/fleet/lease/<int:lid>/end", methods=["POST"])
@login_required(role="admin")
def end(lid):
    from .pages import affordable
    l = db.one("SELECT * FROM leases WHERE id=? AND status IN ('active','matured')", (lid,)) or abort(404)
    action = request.form.get("action")
    st = leasing.status(l)
    new_plan = None
    if action == "buyout":
        if not affordable(st["balance_left"] + l["residual"], "buying out this lease"):
            return redirect(url_for("lease.detail", lid=lid))
    elif action == "renew":
        new_plan = db.one("SELECT * FROM lease_plans WHERE id=? AND active=1", (request.form.get("plan_id", "0"),))
        if not new_plan or not st["matured"]:
            flash("A lease can be renewed once its last payment is made; pick the new plan.", "error")
            return redirect(url_for("lease.detail", lid=lid))
    elif action != "return":
        abort(400)
    cost, msg = leasing.finish(l, action, user()["id"], new_plan)
    db.audit(user()["id"], f"lease {action}", f"#{lid} cost {cost:,.2f}")
    flash(f"{msg} Cost: {db.setting('currency', '$')}{cost:,.2f}.", "success")
    return redirect(url_for("lease.page"))


@bp.route("/leasing/plans/save", methods=["POST"])
@login_required(role="admin")
def plan_save():
    imp = current_units() == "imperial"
    name = request.form.get("name", "").strip()[:40]
    weeks = _f("weeks", 1, 520)
    allowed = _f("allowed", 100, 10_000_000)
    rate = _f("excess_rate", 0, 100)
    vals = dict(down_pct=_f("down_pct", 0, 90), apr=_f("apr", 0, 60), residual_pct=_f("residual_pct", 0, 95),
                early_fee_pct=_f("early_fee_pct", 0, 100, 30))
    if not name or weeks is None or allowed is None or rate is None or None in vals.values():
        flash("Fill in every field of the lease plan.", "error")
        return redirect(url_for("lease.page") + "#plans")
    vals.update(name=name, weeks=int(weeks), km_allowed=allowed * (KM_PER_MI if imp else 1),
                excess_per_km=rate / (KM_PER_MI if imp else 1))
    pid = request.form.get("id", "")
    if pid.isdigit() and db.val("SELECT id FROM lease_plans WHERE id=?", (int(pid),)):
        db.update("lease_plans", int(pid), **vals)
        flash(f"Lease plan {name} updated. Leases already signed keep their own terms.", "success")
    else:
        db.insert("lease_plans", sort=99, created_at=time.time(), **vals)
        flash(f"Lease plan {name} added.", "success")
    db.audit(user()["id"], "lease plan saved", name)
    return redirect(url_for("lease.page") + "#plans")


@bp.route("/leasing/plans/<int:pid>/toggle", methods=["POST"])
@login_required(role="admin")
def plan_toggle(pid):
    p = db.one("SELECT * FROM lease_plans WHERE id=?", (pid,)) or abort(404)
    db.update("lease_plans", pid, active=0 if p["active"] else 1)
    flash(f"Lease plan {p['name']} {'hidden' if p['active'] else 'offered again'}.", "success")
    return redirect(url_for("lease.page") + "#plans")
