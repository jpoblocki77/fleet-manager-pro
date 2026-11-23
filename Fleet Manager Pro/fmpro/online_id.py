"""The company's own ID on the Fleet Manager server, used by the online Apply page, the Global Leaderboard and the
VTC Finder instead of the licence key.

It is made once, on first use, and kept in the company's database, so it moves with the company's data to a new PC
or a new version. The secret proves to the server that a sync really comes from this company; it never leaves this
PC except to the server, and the debug log hides it (its setting name contains "secret").
"""
import base64
import json
import os
import re
import secrets
import threading

from . import db

ID_RX = re.compile(r"^FMC-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")
KEY_RX = re.compile(r"^FMP-[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")
MACHINE_RX = re.compile(r"^FMID-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")
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


def legacy():
    """The licence key and PC this company was activated with before Fleet Manager Pro went free (from the old
    data/licence.json), sent along with the company ID until the server confirms it has moved the company's Apply
    page, leaderboard entry and VTC profile over. {} when there's nothing (left) to move."""
    if db.setting("online_claimed") == "1" or not db.DB_PATH:
        return {}
    try:
        with open(os.path.join(os.path.dirname(db.DB_PATH), "licence.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    key, machine = str(d.get("key") or "").strip().upper(), str(d.get("machine") or "").strip().upper()
    return {"key": key, "machine": machine} if KEY_RX.match(key) and MACHINE_RX.match(machine) else {}


def body(**fields):
    """A request body for the server: the company ID, the old key while it's still needed, and the fields."""
    return dict(identity(), **legacy(), **fields)


def note_reply(sent, reply):
    """After a sync: once a server that knows company IDs has answered one that carried the old key, it has moved
    whatever there was to move, so the key isn't sent again."""
    if sent.get("key") and isinstance(reply, dict) and reply.get("company_id") == sent.get("company_id"):
        db.set_setting("online_claimed", "1")
