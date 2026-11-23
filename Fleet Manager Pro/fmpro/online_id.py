"""The company's own ID on the Fleet Manager server, used by the online Apply page, the Global Leaderboard and the
VTC Finder instead of the licence key.

It is made once, on first use, and kept in the company's database, so it moves with the company's data to a new PC
or a new version. The secret proves to the server that a sync really comes from this company; it never leaves this
PC except to the server, and the debug log hides it (its setting name contains "secret").
"""
import base64
import re
import secrets
import threading

from . import db

ID_RX = re.compile(r"^FMC-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")
_lock = threading.Lock()


def _new_id():
    code = base64.b32encode(secrets.token_bytes(10)).decode()[:16]
    return "FMC-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))


def identity():
    """{"company_id", "company_secret"} for this company, made the first time it's needed."""
    with _lock:
        cid, secret = db.setting("online_company_id", "") or "", db.setting("online_secret", "") or ""
        if not ID_RX.match(cid) or not 32 <= len(secret) <= 128:
            cid, secret = _new_id(), secrets.token_urlsafe(32)
            db.set_setting("online_company_id", cid)
            db.set_setting("online_secret", secret)
        return {"company_id": cid, "company_secret": secret}


def company_id():
    """This company's ID, or "" before it has been made."""
    cid = db.setting("online_company_id", "") or ""
    return cid if ID_RX.match(cid) else ""
