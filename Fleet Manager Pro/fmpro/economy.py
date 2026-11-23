"""Company economy, progression and gamification rules."""
import json
import logging
import math
import threading
import time
import urllib.request

from . import db

# ------------------------------------------------------------------ levels & ranks
RANKS = [  # (min level, name, colour)
    (1, "Rookie", "#8a94a6"), (5, "Driver", "#4f7cc4"), (10, "Senior Driver", "#2f9e6f"),
    (20, "Road Captain", "#d9a400"), (35, "Veteran", "#e0662b"), (50, "Elite", "#c8102e"),
    (75, "Legend", "#a020a0"),
]


def level_for(xp):
    return int(math.sqrt(max(0, xp) / 150)) + 1


def xp_for_level(level):
    return int(150 * (level - 1) ** 2)


def rank_for(level):
    r = RANKS[0]
    for row in RANKS:
        if level >= row[0]:
            r = row
    return {"name": r[1], "color": r[2]}


def progress(xp):
    lv = level_for(xp)
    lo, hi = xp_for_level(lv), xp_for_level(lv + 1)
    return {"level": lv, "xp": xp, "next": hi, "pct": round((xp - lo) / max(1, hi - lo) * 100, 1),
            "rank": rank_for(lv)}


def add_xp(user_id, amount, reason=""):
    if not amount:
        return
    before = db.val("SELECT xp FROM users WHERE id=?", (user_id,), 0)
    db.ex("UPDATE users SET xp = MAX(0, xp + ?) WHERE id=?", (int(amount), user_id))
    lb, la = level_for(before), level_for(before + amount)
    if la > lb:
        db.notify(user_id, f"Level up! You reached level {la} ({rank_for(la)['name']}).", "/profile", "success")


# ------------------------------------------------------------------ money
def balance(account="company", user_id=None):
    if account == "company":
        return round(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'", (), 0.0), 2)
    return round(db.val("SELECT SUM(amount) FROM ledger WHERE account=? AND user_id=?",
                        (account, user_id), 0.0), 2)


def post(category, amount, description="", account="company", user_id=None, job_id=None):
    if not amount:
        return None
    return db.insert("ledger", account=account, user_id=user_id, category=category,
                     amount=round(float(amount), 2), description=description, job_id=job_id,
                     created_at=time.time())


def score_job(job):
    """0-100 driving score: damage, fines, speeding and lateness cost points."""
    s = 100.0
    s -= min(40, job.get("cargo_damage", 0) * 100 * 2)
    s -= min(25, max(0, job.get("truck_wear_end", 0) - job.get("truck_wear_start", 0)) * 100 * 3)
    fines = db.val("SELECT COUNT(*) FROM job_events WHERE job_id=? AND kind='fine'", (job["id"],), 0)
    s -= min(30, fines * 8)
    if job.get("driving_s"):
        s -= min(25, job.get("speeding_s", 0) / job["driving_s"] * 100)
    if job.get("game_time_deadline") and job.get("game_time_end") and job["game_time_end"] > job["game_time_deadline"]:
        s -= 15
    return int(max(0, round(s)))


def job_xp(job):
    km = job.get("driven_km") or job.get("planned_km") or 0
    tonnes = (job.get("cargo_mass") or 0) / 1000
    base = km * db.setting_f("xp_per_km", 1.0) + tonnes * db.setting_f("xp_per_tonne", 2.0)
    mult = 0.5 + (job.get("score", 100) / 100)
    if job.get("special"):
        mult += 0.25
    if job.get("contract_id"):
        mult += 0.2
    return int(base * mult)


def settle_job(job_id):
    """Pay out a delivered job: company income, costs, driver pay, XP, achievements."""
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        return
    cur = db.setting("currency", "$")
    route = f"{job['source_city']} → {job['destination_city']}"
    km = job["driven_km"] or job["planned_km"] or 0
    revenue = job["revenue"] or job["income"] or 0
    from . import bank, fuel_prices
    fuel_cost = round(job["fuel_used"] * fuel_prices.last_paid(job["user_id"]), 2)
    pay_rate = db.val("SELECT pay_per_km FROM users WHERE id=?", (job["user_id"],))
    if pay_rate is None:
        pay_rate = db.setting_f("driver_pay_per_km", 0.4)
    driver_pay = round(km * pay_rate + revenue * db.setting_f("driver_commission_pct", 0) / 100, 2)

    contract_bonus = 0
    if job["contract_id"]:
        contract_bonus = db.val("SELECT bonus FROM contracts WHERE id=?", (job["contract_id"],), 0)

    # The game pays the offer minus cargo damage (or lateness): book the offer as freight and the
    # deduction as its own expense, so the logbook and Expenses show the money lost to damage.
    offered = job["income"] or 0
    deduction = round(offered - revenue, 2) if job["source"] == "telemetry" and 0 < revenue < offered else 0
    if deduction:
        reason = db.deduction_reason(job)
        post("freight", offered, f"Delivery {route} ({job['cargo']})", job_id=job_id)
        db.insert("ledger", account="company", category=reason, amount=-deduction,
                  description=db._deduction_text(job, reason), job_id=job_id, truck_id=job["truck_id"],
                  driver_id=job["user_id"], created_at=time.time())
        db.update("jobs", job_id, deduction=deduction, deduction_reason=reason)
    else:
        post("freight", revenue, f"Delivery {route} ({job['cargo']})", job_id=job_id)
    if contract_bonus:
        post("contract bonus", contract_bonus, f"Contract #{job['contract_id']} bonus", job_id=job_id)
    # Fuel is charged to the ledger when the driver actually refuels; fuel_cost here is the
    # job's share of fuel burnt, used for per-job profit only.
    post("driver pay", -driver_pay, f"Driver pay job #{job_id}", job_id=job_id)
    bank.earn(job["user_id"], driver_pay, f"Pay for {route}", job_id)      # reaches the driver's bank account on payday

    profit = revenue + contract_bonus - fuel_cost - driver_pay - job["tolls"] - (job["fines"] - (job["driver_fines"] or 0)) - job["ferry"] - job["train"]
    score = score_job(job)
    job.update(score=score)
    xp = job_xp(job)
    db.update("jobs", job_id, fuel_cost=fuel_cost, driver_pay=driver_pay, profit=round(profit, 2),
              score=score, xp=xp)
    add_xp(job["user_id"], xp, f"job #{job_id}")
    check_achievements(job["user_id"])
    if job["truck_id"]:
        db.ex("UPDATE trucks SET odometer = MAX(odometer, ?) WHERE id=?", (job["odo_end"] or 0, job["truck_id"]))
    webhook("delivery", f"**{user_name(job['user_id'])}** delivered **{job['cargo']}** "
                        f"{route} — {km:.0f} km, {cur}{revenue:,.0f}, score {score}")


def _version():
    from . import VERSION
    return VERSION


def user_name(uid):
    return db.val("SELECT display_name FROM users WHERE id=?", (uid,), "Unknown")


# ------------------------------------------------------------------ loans
LOAN_PLANS = {
    "starter": {"label": "Starter (12 weeks, 6.9%)", "weeks": 12, "apr": 6.9},
    "standard": {"label": "Standard (26 weeks, 8.9%)", "weeks": 26, "apr": 8.9},
    "expansion": {"label": "Expansion (52 weeks, 11.5%)", "weeks": 52, "apr": 11.5},
}
WEEK = 7 * 86400


def take_loan(plan, principal):
    p = LOAN_PLANS[plan]
    r = p["apr"] / 100 / 52
    n = p["weeks"]
    inst = principal * r / (1 - (1 + r) ** -n)
    lid = db.insert("loans", principal=principal, apr=p["apr"], weeks=n, installment=round(inst, 2),
                    balance=round(inst * n, 2), next_due=time.time() + WEEK, created_at=time.time())
    post("loan", principal, f"Bank loan #{lid} ({p['label']})")
    return lid


def process_loans(now=None):
    now = now or time.time()
    for loan in db.q("SELECT * FROM loans WHERE status='active' AND next_due <= ?", (now,)):
        pay = min(loan["installment"], loan["balance"])
        post("loan repayment", -pay, f"Loan #{loan['id']} instalment {loan['payments_made'] + 1}/{loan['weeks']}")
        bal = round(loan["balance"] - pay, 2)
        db.update("loans", loan["id"], balance=bal, payments_made=loan["payments_made"] + 1,
                  next_due=loan["next_due"] + WEEK, status="paid" if bal <= 0.01 else "active")


def process_upkeep(now=None):
    """Weekly garage upkeep (Dispolotse-style running costs)."""
    now = now or time.time()
    last = float(db.setting("last_upkeep", "0") or 0)
    if not last:
        db.set_setting("last_upkeep", now)
        return
    weeks = int((now - last) // WEEK)
    if weeks <= 0:
        return
    total = db.val("SELECT SUM(upkeep_per_week) FROM garages", (), 0) * weeks
    if total:
        post("garage upkeep", -total, f"Garage upkeep ({weeks} week{'s' if weeks > 1 else ''})")
    db.set_setting("last_upkeep", last + weeks * WEEK)


# ------------------------------------------------------------------ achievements
ACHIEVEMENTS = [
    # code, name, description, icon, sql returning a number, threshold
    ("first_job", "First Delivery", "Deliver your first load.", "🚚", "jobs", 1),
    ("jobs_10", "Getting Started", "Deliver 10 loads.", "📦", "jobs", 10),
    ("jobs_50", "Workhorse", "Deliver 50 loads.", "🏗️", "jobs", 50),
    ("jobs_250", "Freight Machine", "Deliver 250 loads.", "🏭", "jobs", 250),
    # {need} is filled in with the threshold in the viewer's units (ATS: mi / lb, ETS2: km / t); thresholds are km / kg
    ("km_1k", "Road Tested", "Drive {need} on jobs.", "🛣️", "km", 1000),
    ("km_10k", "Long Hauler", "Drive {need} on jobs.", "🗺️", "km", 10000),
    ("km_100k", "Million Mile Mindset", "Drive {need} on jobs.", "🌎", "km", 100000),
    ("heavy", "Heavy Hauler", "Deliver a load over {need}.", "🏋️", "max_mass", 30000),
    ("long_haul", "Cross Country", "Complete a single job over {need}.", "🧭", "max_km", 1500),
    ("perfect", "Flawless", "Deliver with a perfect 100 score.", "💎", "perfect", 1),
    ("perfect_10", "Precision Driver", "Ten perfect deliveries.", "🎯", "perfect", 10),
    ("earner", "Big Earner", "Earn {need} in freight revenue.", "💰", "revenue", 1_000_000),
    ("ferry", "Sea Legs", "Take a ferry while on a job.", "⛴️", "ferries", 1),
    ("train", "Rail Rider", "Take a train while on a job.", "🚆", "trains", 1),
    ("special", "Special Transport", "Deliver a special transport job.", "⚠️", "special", 1),
    ("convoy", "Convoy Crew", "Attend a company event.", "🚛", "events", 1),
    ("contract", "Dispatcher's Favourite", "Complete 5 dispatch contracts.", "📋", "contracts", 5),
]


def _metric(uid, m):
    D = "status='delivered' AND user_id=?"
    sql = {
        "jobs": f"SELECT COUNT(*) FROM jobs WHERE {D}",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "km": f"SELECT SUM(driven_km) FROM jobs WHERE {D}",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "max_mass": f"SELECT MAX(cargo_mass) FROM jobs WHERE {D}",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "max_km": f"SELECT MAX(driven_km) FROM jobs WHERE {D}",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "perfect": f"SELECT COUNT(*) FROM jobs WHERE {D} AND score>=100",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "revenue": f"SELECT SUM(revenue) FROM jobs WHERE {D}",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "ferries": "SELECT COUNT(*) FROM job_events WHERE kind='ferry' AND user_id=?",
        "trains": "SELECT COUNT(*) FROM job_events WHERE kind='train' AND user_id=?",
        "special": f"SELECT COUNT(*) FROM jobs WHERE {D} AND special=1",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "events": "SELECT COUNT(*) FROM event_rsvps WHERE attended=1 AND user_id=?",
        "contracts": "SELECT COUNT(*) FROM contracts WHERE status='completed' AND assigned_user_id=?",
    }[m]
    return db.val(sql, (uid,), 0) or 0


def check_achievements(uid):
    have = {r["code"] for r in db.q("SELECT code FROM user_achievements WHERE user_id=?", (uid,))}
    for code, name, _desc, icon, metric, need in ACHIEVEMENTS:
        if code in have:
            continue
        if _metric(uid, metric) >= need:
            db.ex("INSERT OR IGNORE INTO user_achievements(user_id, code, earned_at) VALUES (?,?,?)",
                  (uid, code, time.time()))
            add_xp(uid, 250, f"achievement {code}")
            db.notify(uid, f"Achievement unlocked: {icon} {name} (+250 XP)", "/achievements", "success")


ACH_UNITS = {"km": "dist", "max_km": "dist", "max_mass": "mass", "revenue": "money"}   # how each metric is shown


def achievement_progress(uid):
    have = {r["code"]: r["earned_at"] for r in db.q("SELECT code, earned_at FROM user_achievements WHERE user_id=?", (uid,))}
    out = []
    for code, name, desc, icon, metric, need in ACHIEVEMENTS:
        cur = _metric(uid, metric)
        out.append({"code": code, "name": name, "description": desc, "icon": icon, "need": need,
                    "current": cur, "pct": min(100, round(cur / need * 100, 1)), "unit": ACH_UNITS.get(metric, ""),
                    "earned_at": have.get(code)})
    return out


# ------------------------------------------------------------------ Discord webhook
def webhook(kind, text):
    """Post to Discord. New driver applications ("application", "test_application") go to their own webhook when
    one is set (e.g. a staff-only channel), else to the company webhook."""
    url = db.setting("discord_webhook", "")
    if kind in ("application", "test_application"):
        url = db.setting("webhook_applications", "") or url
    if not url or not url.startswith("https://"):
        return
    if kind == "delivery" and db.setting("webhook_on_delivery") != "1":
        return
    if kind == "event" and db.setting("webhook_on_event") != "1":
        return
    if kind == "application" and db.setting("webhook_on_application", "1") != "1":
        return
    name = db.setting("company_name") or "Fleet Manager Pro"

    def send():
        try:
            # no pings: names typed on the public Apply page could otherwise say @everyone
            body = json.dumps({"username": name, "content": text[:1900], "allowed_mentions": {"parse": []}}).encode()
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": f"FleetManagerPro/{_version()}"})
            urllib.request.urlopen(req, timeout=8).read()  # nosec B310  # url checked above: https:// only
        except Exception as e:
            logging.getLogger(__name__).warning("Discord webhook failed: %s", e)
    threading.Thread(target=send, daemon=True).start()


# ------------------------------------------------------------------ repair prices (Settings > Economy only)
WEAR_COLS = ("wear_engine", "wear_transmission", "wear_cabin", "wear_chassis", "wear_wheels")


def repair_price(truck_id):
    """A repair costs the truck's wear (% summed over its five parts) x the repair rate in Settings."""
    t = db.one("SELECT " + ",".join(WEAR_COLS) + " FROM trucks WHERE id=?", (truck_id,)) if truck_id else None  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    if not t:
        return 0.0
    return round(sum(t[c] or 0 for c in WEAR_COLS) * 100 * db.setting_f("repair_cost_per_pct", 40), 2)


def dvir_repair_price(defects):
    """Fixing the defects on an inspection report: a set price per defect from Settings."""
    n = len([x for x in (defects or "").split(";") if x.strip()])
    return round(max(n, 1) * db.setting_f("dvir_repair_per_defect", 250), 2)
