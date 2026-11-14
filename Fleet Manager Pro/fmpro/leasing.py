"""Truck leasing: lease plans (term, mileage allotment, down payment, interest, residual value) and the leases
themselves, billed weekly from the company account like bank loans.

Weekly payment (the standard way leases are priced):
    capitalised cost  = price - down payment
    residual          = price x residual %            (what the truck is still worth at the end)
    depreciation      = (capitalised cost - residual) / weeks
    finance charge    = (capitalised cost + residual) x APR / 52 / 2      (the "money factor", per week)
    weekly payment    = depreciation + finance charge
At the end: buy the truck for the residual, return it (excess miles charged), or renew on another plan.
Ending early: early-termination fee = fee % of the payments still to come, plus excess miles so far.
All distances are stored in km, money in the company currency.
"""
import time

from . import db, economy

WEEK = 7 * 86400
KM_PER_MI = 1.609344

DEFAULT_PLANS = [  # name, weeks, km allowed, down %, APR %, residual %, excess $/km, early fee %
    ("Short Haul", 12, 25000 * KM_PER_MI, 5, 7.9, 75, 0.25 / KM_PER_MI, 40),
    ("Regional", 26, 60000 * KM_PER_MI, 10, 6.9, 60, 0.20 / KM_PER_MI, 35),
    ("Long Haul", 52, 125000 * KM_PER_MI, 10, 5.9, 45, 0.18 / KM_PER_MI, 30),
    ("Owner-Operator", 104, 250000 * KM_PER_MI, 15, 4.9, 30, 0.15 / KM_PER_MI, 25),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS lease_plans (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, weeks INTEGER NOT NULL, km_allowed REAL NOT NULL,
    down_pct REAL NOT NULL, apr REAL NOT NULL, residual_pct REAL NOT NULL, excess_per_km REAL NOT NULL,
    early_fee_pct REAL NOT NULL DEFAULT 30, active INTEGER NOT NULL DEFAULT 1, sort INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS leases (
    id INTEGER PRIMARY KEY, truck_id INTEGER REFERENCES trucks(id), plan_id INTEGER, plan_name TEXT NOT NULL,
    price REAL NOT NULL, down REAL NOT NULL, residual REAL NOT NULL, apr REAL NOT NULL, weeks INTEGER NOT NULL,
    payment REAL NOT NULL, payments_made INTEGER NOT NULL DEFAULT 0, km_allowed REAL NOT NULL,
    excess_per_km REAL NOT NULL, early_fee_pct REAL NOT NULL, odo_start REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', next_due REAL NOT NULL, started_at REAL NOT NULL, ended_at REAL,
    end_reason TEXT DEFAULT '', end_cost REAL DEFAULT 0, created_by INTEGER);
CREATE INDEX IF NOT EXISTS ix_leases_status ON leases(status, next_due);
"""


def init():
    c = db.conn()
    c.executescript(SCHEMA)
    if "leased" not in {r[1] for r in c.execute("PRAGMA table_info(trucks)")}:
        c.execute("ALTER TABLE trucks ADD COLUMN leased INTEGER DEFAULT 0")
    if not c.execute("SELECT COUNT(*) FROM lease_plans").fetchone()[0] and db.setting("lease_plans_seeded") != "1":
        for i, p in enumerate(DEFAULT_PLANS):
            c.execute("INSERT INTO lease_plans(name, weeks, km_allowed, down_pct, apr, residual_pct, excess_per_km, "
                      "early_fee_pct, sort, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", p + (i, time.time()))
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('lease_plans_seeded', '1')")
    c.commit()


def plans(active_only=True):
    return db.q("SELECT * FROM lease_plans" + (" WHERE active=1" if active_only else "") + " ORDER BY sort, weeks, id")  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def quote(price, plan):
    """Everything a lease on `price` under `plan` costs."""
    price = max(0.0, float(price))
    down = round(price * plan["down_pct"] / 100, 2)
    cap = price - down
    residual = round(price * plan["residual_pct"] / 100, 2)
    weeks = max(1, int(plan["weeks"]))
    depreciation = max(0.0, cap - residual) / weeks
    finance = (cap + residual) * plan["apr"] / 100 / 52 / 2
    payment = round(depreciation + finance, 2)
    total = round(down + payment * weeks, 2)
    return {"price": price, "down": down, "capitalised": round(cap, 2), "residual": residual, "weeks": weeks,
            "payment": payment, "depreciation": round(depreciation, 2), "finance": round(finance, 2),
            "total_payments": total, "interest": round(finance * weeks, 2), "buyout_total": round(total + residual, 2)}


def km_used(lease):
    odo = db.val("SELECT odometer FROM trucks WHERE id=?", (lease["truck_id"],), 0) or 0
    return max(0.0, odo - (lease["odo_start"] or 0))


def status(lease, now=None):
    """Live figures for a lease: miles used, projected miles, excess so far, what ending it now would cost."""
    now = now or time.time()
    used = km_used(lease)
    elapsed_weeks = max(0.0, (now - lease["started_at"]) / WEEK)
    projected = used / elapsed_weeks * lease["weeks"] if elapsed_weeks >= 0.5 else used
    excess_km = max(0.0, used - lease["km_allowed"])
    remaining = max(0, lease["weeks"] - lease["payments_made"])
    early_fee = round(remaining * lease["payment"] * lease["early_fee_pct"] / 100, 2)
    return {"km_used": used, "km_left": max(0.0, lease["km_allowed"] - used), "pct": min(100, used / lease["km_allowed"] * 100)
            if lease["km_allowed"] else 0, "projected_km": projected,
            "projected_excess_km": max(0.0, projected - lease["km_allowed"]),
            "excess_km": excess_km, "excess_cost": round(excess_km * lease["excess_per_km"], 2),
            "payments_left": remaining, "balance_left": round(remaining * lease["payment"], 2),
            "early_fee": early_fee, "ends_at": lease["started_at"] + lease["weeks"] * WEEK,
            "matured": lease["payments_made"] >= lease["weeks"]}


def start(truck_id, plan, price, user_id, now=None):
    """Open a lease on a truck and take the down payment. Returns the lease id."""
    now = now or time.time()
    q = quote(price, plan)
    odo = db.val("SELECT odometer FROM trucks WHERE id=?", (truck_id,), 0) or 0
    lid = db.insert("leases", truck_id=truck_id, plan_id=plan["id"], plan_name=plan["name"], price=q["price"], down=q["down"],
                    residual=q["residual"], apr=plan["apr"], weeks=q["weeks"], payment=q["payment"],
                    km_allowed=plan["km_allowed"], excess_per_km=plan["excess_per_km"], early_fee_pct=plan["early_fee_pct"],
                    odo_start=odo, status="active", next_due=now + WEEK, started_at=now, created_by=user_id)
    db.update("trucks", truck_id, leased=1, status="active")
    t = db.one("SELECT brand, model, plate FROM trucks WHERE id=?", (truck_id,))
    if q["down"]:
        economy.post("lease down payment", -q["down"], f"Lease #{lid} down payment - {t['brand']} {t['model']} ({plan['name']})")
    return lid


def process(now=None):
    """Weekly lease payments (called from the same background loop as loans)."""
    now = now or time.time()
    for l in db.q("SELECT * FROM leases WHERE status='active' AND next_due <= ? AND payments_made < weeks", (now,)):
        t = db.one("SELECT brand, model, plate FROM trucks WHERE id=?", (l["truck_id"],)) or {"brand": "truck", "model": "", "plate": ""}
        n = l["payments_made"] + 1
        economy.post("lease payment", -l["payment"], f"Lease #{l['id']} payment {n}/{l['weeks']} - {t['brand']} {t['model']}")
        db.update("leases", l["id"], payments_made=n, next_due=l["next_due"] + WEEK)
        if n >= l["weeks"]:
            db.update("leases", l["id"], status="matured")
            for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'"):
                db.notify(m["id"], f"Lease #{l['id']} on the {t['brand']} {t['model']} has ended: buy it, return it or renew.",
                          f"/fleet/lease/{l['id']}", "warning")


def finish(lease, action, user_id, new_plan=None, now=None):
    """End a lease: 'buyout', 'return' (also early), or 'renew' onto new_plan. Returns (cost, message)."""
    now = now or time.time()
    st = status(lease, now)
    t = db.one("SELECT * FROM trucks WHERE id=?", (lease["truck_id"],))
    label = f"{t['brand']} {t['model']} ({t['plate']})" if t else "truck"
    early = not st["matured"]
    if action == "buyout":
        cost = round(st["balance_left"] + lease["residual"], 2)
        if early:
            economy.post("lease payoff", -st["balance_left"], f"Lease #{lease['id']} remaining payments - {label}") if st["balance_left"] else None
        economy.post("lease buyout", -lease["residual"], f"Lease #{lease['id']} buyout - {label}")
        db.update("trucks", lease["truck_id"], leased=0, purchase_price=lease["price"])
        msg = f"You own the {label} now."
    elif action == "return":
        fee = st["early_fee"] if early else 0
        if fee:
            economy.post("lease early return", -fee, f"Lease #{lease['id']} early-termination fee - {label}")
        if st["excess_cost"]:
            economy.post("lease excess mileage", -st["excess_cost"],
                         f"Lease #{lease['id']} {st['excess_km']:,.0f} km over the allotment - {label}")
        cost = round(fee + st["excess_cost"], 2)
        db.update("trucks", lease["truck_id"], leased=0, status="returned", assigned_user_id=None, garage_id=None)
        msg = f"The {label} has gone back to the leasing company."
    elif action == "renew" and new_plan:
        if st["excess_cost"]:
            economy.post("lease excess mileage", -st["excess_cost"],
                         f"Lease #{lease['id']} {st['excess_km']:,.0f} km over the allotment - {label}")
        cost = st["excess_cost"]
        db.update("leases", lease["id"], status="renewed", ended_at=now, end_reason="renewed", end_cost=cost)
        new = start(lease["truck_id"], new_plan, lease["residual"], user_id, now)    # renewing leases the truck at its value now
        return cost + quote(lease["residual"], new_plan)["down"], f"Renewed as lease #{new} on the {new_plan['name']} plan."
    else:
        raise ValueError(action)
    db.update("leases", lease["id"], status="bought" if action == "buyout" else "returned", ended_at=now,
              end_reason=("early " if early else "") + action, end_cost=cost)
    return cost, msg
