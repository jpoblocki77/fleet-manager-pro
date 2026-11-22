"""Drivers' own bank accounts, separate from the company account, with a payday every Friday at noon.

Three pots in the ledger, never mixed:
  company   the company account. Driver pay leaves it when a load is delivered, so the company always has the week's
            payroll set aside.
  wages     pay a driver has earned that waits for the next payday. Not the driver's to spend yet.
  driver    the driver's bank account: paydays in; fines, traffic tickets and licence fines out. May be overdrawn.

Noon is this PC's clock (the PC Fleet Manager Pro runs on). When the program was closed at noon the payday is made
up the next time it runs.
"""
import datetime as dt
import logging
import threading
import time

from . import db, economy

log = logging.getLogger("fmpro.bank")
PAY_WEEKDAY, PAY_HOUR = 4, 12           # Friday (Monday is 0), noon
_lock = threading.Lock()


def last_payday(now=None):
    """Timestamp of the latest Friday noon that has passed."""
    t = dt.datetime.fromtimestamp(now or time.time())
    d = t.date() - dt.timedelta(days=(t.weekday() - PAY_WEEKDAY) % 7)
    noon = dt.datetime.combine(d, dt.time(PAY_HOUR))
    if noon > t:
        noon -= dt.timedelta(days=7)
    return noon.timestamp()             # through the calendar, so summer time never moves noon


def next_payday(now=None):
    day = dt.datetime.fromtimestamp(last_payday(now)).date() + dt.timedelta(days=7)
    return dt.datetime.combine(day, dt.time(PAY_HOUR)).timestamp()


def balance(uid):
    return economy.balance("driver", uid)


def pending(uid):
    """Earned, waiting for payday."""
    return economy.balance("wages", uid)


def earn(uid, amount, description, job_id=None):
    """Pay for a load: held until payday."""
    return economy.post("earnings", amount, description, account="wages", user_id=uid, job_id=job_id)


def driver_pays(uid):
    """Do this driver's fines come out of their own account? (Never while the built-in simulator drives.)"""
    from . import cdl
    return db.setting("drivers_pay_fines", "1") == "1" and uid not in cdl.SIMULATED


def fine(uid, category, amount, description, job_id=None):
    """Charge a fine to the driver's bank account, or to the company when the settings say so. A bank account may go
    below zero; the next pay covers it. Returns True when the driver paid."""
    if not driver_pays(uid):
        economy.post(category, -amount, f"{description} ({economy.user_name(uid)})", job_id=job_id)
        return False
    economy.post(category, -amount, description, account="driver", user_id=uid, job_id=job_id)
    if job_id:
        db.ex("UPDATE jobs SET driver_fines = COALESCE(driver_fines, 0) + ? WHERE id=?", (amount, job_id))
    return True


def number(uid):
    n = f"{uid:08d}"
    return f"4400 {n[:4]} {n[4:]}"


def process(now=None):
    """Pay every driver what they had earned by the latest Friday noon. Safe to call as often as you like: what was
    paid is taken out of the wages pot in the same transaction, so nothing is ever paid twice. Returns the drivers paid."""
    cut = last_payday(now)
    paid = []
    with _lock:
        due = db.q("SELECT user_id, ROUND(SUM(amount), 2) due FROM ledger WHERE account='wages' AND user_id IS NOT NULL "
                   "AND created_at<=? GROUP BY user_id HAVING ROUND(SUM(amount), 2) >= 0.01", (cut,))
        when = dt.datetime.fromtimestamp(cut).strftime("%b %d, %Y")
        for r in due:
            c = db.conn()
            with c:
                for account, amount, text in (("wages", -r["due"], "Paid into the bank account"),
                                              ("driver", r["due"], f"Payday {when}")):
                    c.execute("INSERT INTO ledger(account, user_id, category, amount, description, created_at) "
                              "VALUES (?,?,?,?,?,?)", (account, r["user_id"], "payday", amount, text, cut))
            paid.append(r["user_id"])
    cur = db.setting("currency", "$")
    for r in due:
        db.notify(r["user_id"], f"Payday: {cur}{r['due']:,.2f} was deposited into your bank account.", "/bank", "success")
        log.info("payday %s: driver #%s paid %.2f", when, r["user_id"], r["due"])
    return paid


def statement(uid, limit=100):
    """Newest first, each with the balance after it."""
    rows = db.q("SELECT id, category, amount, description, job_id, created_at FROM ledger WHERE account='driver' AND user_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (uid, limit))
    left = balance(uid)
    for r in rows:
        r["after"] = left
        left = round(left - r["amount"], 2)
    return rows


def waiting(uid):
    """The loads in the next pay."""
    last = db.val("SELECT MAX(created_at) FROM ledger WHERE account='wages' AND category='payday' AND user_id=?", (uid,), 0) or 0
    return db.q("SELECT amount, description, job_id, created_at FROM ledger WHERE account='wages' AND category!='payday' "
                "AND user_id=? AND created_at>? ORDER BY created_at DESC, id DESC", (uid, last))


def stubs(uid, limit=8):
    """The last paydays with the loads in each."""
    days = db.q("SELECT created_at, -amount paid FROM ledger WHERE account='wages' AND category='payday' AND user_id=? "
                "ORDER BY created_at DESC LIMIT ?", (uid, limit + 1))
    for i, d in enumerate(days):
        since = days[i + 1]["created_at"] if i + 1 < len(days) else 0
        d["loads"] = db.q("SELECT amount, description, job_id, created_at FROM ledger WHERE account='wages' AND "
                          "category!='payday' AND user_id=? AND created_at>? AND created_at<=? ORDER BY created_at, id",
                          (uid, since, d["created_at"]))
    return days[:limit]


def payroll():
    """Everyone's pay waiting for the next payday (what the company has set aside)."""
    return round(db.val("SELECT SUM(amount) FROM ledger WHERE account='wages'", (), 0.0) or 0.0, 2)
