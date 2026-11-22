"""Personal loans: a driver borrows from the bank into their own account. They work like the company's loans
(economy.LOAN_PLANS) but never touch the company's money: the total is fixed when the loan is taken, instalments are
taken automatically every week, and it can be paid off early at any time. Instalments go out on Fridays, right after
payday, from the driver's bank account.
"""
import logging
import time

from . import bank, db, economy

log = logging.getLogger("fmpro.bank")
PLANS = {
    "short": {"label": "Short (4 weeks, 4.9%)", "weeks": 4, "apr": 4.9},
    "starter": {"label": "Starter (12 weeks, 6.9%)", "weeks": 12, "apr": 6.9},
    "standard": {"label": "Standard (26 weeks, 8.9%)", "weeks": 26, "apr": 8.9},
    "extended": {"label": "Extended (52 weeks, 11.5%)", "weeks": 52, "apr": 11.5},
}
MIN_LOAN, MAX_LOAN, MAX_LOANS = 500, 25_000, 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_loans (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), principal REAL NOT NULL, apr REAL NOT NULL,
    weeks INTEGER NOT NULL, installment REAL NOT NULL, balance REAL NOT NULL, payments_made INTEGER NOT NULL DEFAULT 0,
    next_due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_personal_loans ON personal_loans(user_id, status);
"""


def init():
    db.conn().executescript(SCHEMA)
    db.conn().commit()


def enabled():
    return db.setting("personal_loans", "1") == "1"


def loans(uid):
    return db.q("SELECT * FROM personal_loans WHERE user_id=? ORDER BY status='paid', id DESC LIMIT 20", (uid,))


def installment(principal, plan):
    p = PLANS[plan]
    r = p["apr"] / 100 / 52
    return round(principal * r / (1 - (1 + r) ** -p["weeks"]), 2)


def take(uid, plan, amount):
    """Returns (ok, message). The money is in the driver's account straight away."""
    cur = db.setting("currency", "$")
    if not enabled():
        return False, "Personal loans are turned off in this company."
    amount = round(amount, 2)
    if plan not in PLANS or not MIN_LOAN <= amount <= MAX_LOAN:
        return False, f"Pick a plan and an amount from {cur}{MIN_LOAN:,} to {cur}{MAX_LOAN:,}."
    p = PLANS[plan]
    with bank.lock:
        if db.val("SELECT COUNT(*) FROM personal_loans WHERE user_id=? AND status='active'", (uid,), 0) >= MAX_LOANS:
            return False, f"You already have {MAX_LOANS} active loans."
        now = time.time()
        inst = installment(amount, plan)
        lid = db.insert("personal_loans", user_id=uid, principal=amount, apr=p["apr"], weeks=p["weeks"], installment=inst,
                        balance=round(inst * p["weeks"], 2), next_due=bank.next_payday(now + 3 * 86400), created_at=now)
        economy.post("personal loan", amount, f"Loan #{lid} ({p['label']})", account="driver", user_id=uid)
        db.audit(uid, "personal loan", f"#{lid} {amount:.2f} at {p['apr']:g}%")
    return True, f"Loan approved. {cur}{amount:,.2f} is in your bank account; you pay {cur}{inst:,.2f} every Friday."


def payoff(uid, lid):
    with bank.lock:
        loan = db.one("SELECT * FROM personal_loans WHERE id=? AND user_id=? AND status='active'", (lid, uid))
        if not loan:
            return False, "That loan is already paid off."
        if bank.balance(uid) < loan["balance"]:
            return False, "Not enough money in your bank account to pay it off."
        economy.post("loan repayment", -loan["balance"], f"Loan #{lid} paid off early", account="driver", user_id=uid)
        db.update("personal_loans", lid, balance=0, status="paid")
        db.audit(uid, "personal loan", f"#{lid} paid off early")
    return True, "Loan paid off."


def process(now=None):
    """Friday's instalments, taken right after payday. Fridays missed while the program was closed are made up."""
    now = now or time.time()
    with bank.lock:
        for loan in db.q("SELECT * FROM personal_loans WHERE status='active' AND next_due<=? ORDER BY id", (now,)):
            bal, made, due = loan["balance"], loan["payments_made"], loan["next_due"]
            while bal > 0.01 and due <= now:
                pay = min(loan["installment"], bal)
                economy.post("loan repayment", -pay, f"Loan #{loan['id']} instalment {made + 1}/{loan['weeks']}", account="driver",
                             user_id=loan["user_id"])
                bal, made, due = round(bal - pay, 2), made + 1, bank.next_payday(due)
                db.update("personal_loans", loan["id"], balance=max(0.0, bal), payments_made=made, next_due=due,
                          status="paid" if bal <= 0.01 else "active")
            if bal <= 0.01:
                db.notify(loan["user_id"], f"Loan #{loan['id']} is paid off.", "/bank#loans", "success")
                log.info("driver #%s: personal loan #%s paid off", loan["user_id"], loan["id"])
