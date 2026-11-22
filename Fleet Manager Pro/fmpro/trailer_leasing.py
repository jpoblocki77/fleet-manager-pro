"""Trailer leasing: the same deal as truck leasing (fmpro/leasing.py) - lease plans with a term, down payment,
interest and end value, billed weekly from the company account - but with no mileage allowance. The game doesn't
report how far a trailer has travelled, so trailer leases never charge for extra miles.

Weekly payment, priced exactly like a truck lease (leasing.quote):
    depreciation   = (price - down payment - end value) / weeks
    finance charge = (price - down payment + end value) x APR / 52 / 2
At the end: buy the trailer for its end value, return it, or renew on another plan.
Ending early: early-termination fee = fee % of the payments still to come.
"""
import time

from . import db, economy
from .leasing import WEEK, quote  # noqa: F401  (quote is re-exported for the views and tests)

DEFAULT_PLANS = [  # name, weeks, down %, APR %, end value %, early fee %
    ("Short Term", 12, 5, 7.9, 75, 40),
    ("Seasonal", 26, 10, 6.9, 60, 35),
    ("Annual", 52, 10, 5.9, 45, 30),
    ("Long Term", 104, 15, 4.9, 30, 25),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS trailer_lease_plans (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, weeks INTEGER NOT NULL, down_pct REAL NOT NULL, apr REAL NOT NULL,
    residual_pct REAL NOT NULL, early_fee_pct REAL NOT NULL DEFAULT 30, active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS trailer_leases (
    id INTEGER PRIMARY KEY, trailer_id INTEGER REFERENCES trailers(id), plan_id INTEGER, plan_name TEXT NOT NULL,
    price REAL NOT NULL, down REAL NOT NULL, residual REAL NOT NULL, apr REAL NOT NULL, weeks INTEGER NOT NULL,
    payment REAL NOT NULL, payments_made INTEGER NOT NULL DEFAULT 0, early_fee_pct REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', next_due REAL NOT NULL, started_at REAL NOT NULL, ended_at REAL,
    end_reason TEXT DEFAULT '', end_cost REAL DEFAULT 0, created_by INTEGER);
CREATE INDEX IF NOT EXISTS ix_trailer_leases_status ON trailer_leases(status, next_due);
"""
LIVE = ("active", "matured")


def init():
    c = db.conn()
    c.executescript(SCHEMA)
    if "leased" not in {r[1] for r in c.execute("PRAGMA table_info(trailers)")}:
        c.execute("ALTER TABLE trailers ADD COLUMN leased INTEGER DEFAULT 0")
    if not c.execute("SELECT COUNT(*) FROM trailer_lease_plans").fetchone()[0] \
            and db.setting("trailer_lease_plans_seeded") != "1":
        for i, p in enumerate(DEFAULT_PLANS):
            c.execute("INSERT INTO trailer_lease_plans(name, weeks, down_pct, apr, residual_pct, early_fee_pct, sort, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?)", p + (i, time.time()))
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('trailer_lease_plans_seeded', '1')")
    c.commit()


def plans(active_only=True):
    return db.q("SELECT * FROM trailer_lease_plans" + (" WHERE active=1" if active_only else "")  # nosec B608  # fixed SQL text, no user values
                + " ORDER BY sort, weeks, id")


def _label(trailer_id):
    t = db.one("SELECT name, brand, plate FROM trailers WHERE id=?", (trailer_id,))
    if not t:
        return "trailer"
    return f"{t['name']}" + (f" ({t['plate']})" if t["plate"] else "")


def status(lease, now=None):
    """Live figures for a trailer lease: payments left and what ending it now would cost."""
    now = now or time.time()
    remaining = max(0, lease["weeks"] - lease["payments_made"])
    return {"payments_left": remaining, "balance_left": round(remaining * lease["payment"], 2),
            "early_fee": round(remaining * lease["payment"] * lease["early_fee_pct"] / 100, 2),
            "ends_at": lease["started_at"] + lease["weeks"] * WEEK, "matured": lease["payments_made"] >= lease["weeks"],
            "pct_paid": min(100, lease["payments_made"] / lease["weeks"] * 100) if lease["weeks"] else 100}


def start(trailer_id, plan, price, user_id, now=None):
    """Open a lease on a trailer and take the down payment. Returns the lease id."""
    now = now or time.time()
    q = quote(price, plan)
    lid = db.insert("trailer_leases", trailer_id=trailer_id, plan_id=plan["id"], plan_name=plan["name"], price=q["price"],
                    down=q["down"], residual=q["residual"], apr=plan["apr"], weeks=q["weeks"], payment=q["payment"],
                    early_fee_pct=plan["early_fee_pct"], status="active", next_due=now + WEEK, started_at=now,
                    created_by=user_id)
    db.update("trailers", trailer_id, leased=1, status="active")
    if q["down"]:
        economy.post("trailer lease down payment", -q["down"],
                     f"Trailer lease #{lid} down payment - {_label(trailer_id)} ({plan['name']})")
    return lid


def process(now=None):
    """Weekly trailer lease payments (called from the same background loop as loans and truck leases)."""
    now = now or time.time()
    for l in db.q("SELECT * FROM trailer_leases WHERE status='active' AND next_due <= ? AND payments_made < weeks", (now,)):
        label = _label(l["trailer_id"])
        n = l["payments_made"] + 1
        economy.post("trailer lease payment", -l["payment"], f"Trailer lease #{l['id']} payment {n}/{l['weeks']} - {label}")
        db.update("trailer_leases", l["id"], payments_made=n, next_due=l["next_due"] + WEEK)
        if n >= l["weeks"]:
            db.update("trailer_leases", l["id"], status="matured")
            for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'"):
                db.notify(m["id"], f"Trailer lease #{l['id']} on {label} has ended: buy it, return it or renew.",
                          f"/fleet/trailer-lease/{l['id']}", "warning")


def finish(lease, action, user_id, new_plan=None, now=None):
    """End a trailer lease: 'buyout', 'return' (also early), or 'renew' onto new_plan. Returns (cost, message)."""
    now = now or time.time()
    st = status(lease, now)
    label = _label(lease["trailer_id"])
    early = not st["matured"]
    if action == "buyout":
        cost = round(st["balance_left"] + lease["residual"], 2)
        if early and st["balance_left"]:
            economy.post("trailer lease payoff", -st["balance_left"], f"Trailer lease #{lease['id']} remaining payments - {label}")
        economy.post("trailer lease buyout", -lease["residual"], f"Trailer lease #{lease['id']} buyout - {label}")
        db.update("trailers", lease["trailer_id"], leased=0, purchase_price=lease["price"])
        msg = f"You own {label} now."
    elif action == "return":
        cost = st["early_fee"] if early else 0
        if cost:
            economy.post("trailer lease early return", -cost, f"Trailer lease #{lease['id']} early-termination fee - {label}")
        db.update("trailers", lease["trailer_id"], leased=0, status="returned", garage_id=None)
        msg = f"{label} has gone back to the leasing company."
    elif action == "renew" and new_plan:
        db.update("trailer_leases", lease["id"], status="renewed", ended_at=now, end_reason="renewed", end_cost=0)
        new = start(lease["trailer_id"], new_plan, lease["residual"], user_id, now)   # leased again at its value now
        return quote(lease["residual"], new_plan)["down"], f"Renewed as trailer lease #{new} on the {new_plan['name']} plan."
    else:
        raise ValueError(action)
    db.update("trailer_leases", lease["id"], status="bought" if action == "buyout" else "returned", ended_at=now,
              end_reason=("early " if early else "") + action, end_cost=cost)
    return cost, msg
