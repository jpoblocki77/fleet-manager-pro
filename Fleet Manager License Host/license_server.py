"""Fleet Manager licence server - checked over the internet.

Run it on a machine buyers can reach (your PC with a forwarded port, or any VPS / cloud host with Python 3):

    python license_server.py                       # listens on 0.0.0.0:8750 (see server_config.json)
    python license_server.py --port 8750 --folder PATH

Data (all in this folder): host_key.json (private signing key), licenses.db (purchases + PCs), server_config.json
(admin token, port, lease length).

Public API (used by Fleet Manager and Fleet Manager Pro):
  POST /v1/activate    {key, machine, product?}   activate this PC (max 2 PCs per purchase) -> signed lease
                       product defaults to "Fleet Manager" (copies sold before Pro existed don't send it);
                       a key for the other product is refused before any PC slot is used
  POST /v1/check       {key, machine}   periodic check -> fresh signed lease, or an error that locks the app
  POST /v1/deactivate  {key, machine}   free this PC's slot (moving to a new PC)
  POST /v1/recruit     {key, machine, product, company, intro, open, ack: [ids]}   (Fleet Manager Pro)
                       publishes the company's Apply page and hands over new applications; acked ones are deleted
                       An optional  profile: {...}  is the company's VTC Finder profile (see vtc_clean)
  POST /v1/leaderboard {key, machine, product, join, company, currency, stats, drivers}   (Fleet Manager Pro)
                       posts this company's totals to the global leaderboard (join=false leaves it) and returns
                       every company on it, plus the best drivers of companies that share driver names
  POST /v1/update      {product, build, version}   newest build for this copy -> Ed25519-signed manifest
                       (version, sha256, file) or {update: false}. Packages live in  updates/<BUILD TAG>/ .
  GET  /v1/update/file?build=<tag>&name=<zip>   download a package (its SHA-256 is pinned by the signed manifest)
Public pages (no key):
  GET/POST /apply/<code>   the company's application form (rate-limited, honeypot, length limits)
  GET  /vtcs?q=&game=&open=&lang=&region=&style=&plays=&size=&sort=&p=   VTC Finder: search for a company
  GET  /vtc/<code>         a company's profile page   POST /vtc/<code>/report   report it (3 an hour per address)
  GET  /leaderboard?metric=km&period=month&game=all   the global leaderboard of every Fleet Manager Pro company
Admin API (Authorization: Bearer <admin_token>), used by License Manager:
  POST /v1/admin/purchase {name, email}   GET /v1/admin/list
  POST /v1/admin/release  {key, machine}  POST /v1/admin/revoke {key, revoked: true|false}
  POST /v1/admin/reset    {key}           free every PC on a purchase (buyer couldn't deactivate)
  POST /v1/admin/whose    {tag}           which purchase a copy's hidden build tag (FMPB-...) belongs to
  POST /v1/admin/delete   {key} | {all_revoked: true}   delete a purchase (active or revoked) for good, or every
                          revoked one; its PCs lock at their next check. A copy goes to deleted_purchases.jsonl first.

  GET  /v1/admin/vtc                      VTC Finder listings and the reports about them
  POST /v1/admin/vtc_hide {key, hidden: true|false}     POST /v1/admin/vtc_clear {key}   (forget its reports)

Every lease is signed with Ed25519; Fleet Manager only trusts leases signed by this server's key, so a fake
server can't unlock it.
"""
import argparse
import base64
import html
import urllib.parse
import collections
import datetime as dt
import hashlib
import http.server
import ipaddress
import json
import hmac
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import threading
import time
import unicodedata
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ed25519  # noqa: E402

# Purchase keys are masked in the logs (keep the first and last group so entries can be correlated, but never
# record a whole, usable key). Build tags (FMPB-...) don't unlock anything, so they're left readable.
_LOG_KEY_RX = re.compile(r"\bFMP-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})\b", re.I)


class _KeyRedactor(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = _LOG_KEY_RX.sub(lambda m: f"FMP-{m.group(1)}-****-****-{m.group(4)}", msg)
        record.args = ()
        return True

PRODUCT = "Fleet Manager Pro"                   # this server only licenses Fleet Manager Pro
PRODUCTS = {"Fleet Manager Pro": "FMP"}         # product -> purchase key prefix
LEGACY = "Fleet Manager"                        # records made before Pro existed (no product stored)
SLOTS_PER_PURCHASE = 2
MIN_AGE = 16                                    # youngest applicant the Apply page takes
MACHINE_RX = re.compile(r"^FMID-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$")
KEY_RX = re.compile(r"^FMP-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$")


def product_of(p):
    return p.get("product") or LEGACY
# Only a reverse proxy on this same machine (Caddy) may tell us the buyer's real IP via X-Forwarded-For.
TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def client_ip(peer, forwarded):
    """The IP used for rate limiting and records. X-Forwarded-For is ignored unless the connection comes from
    Caddy on this machine; then we take its LAST entry (the address Caddy itself saw), never one a client wrote."""
    if peer in TRUSTED_PROXIES and forwarded:
        last = forwarded.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass
    return peer


def tls_fingerprint(cert_path):
    """SHA-256 of the certificate (DER). Fleet Manager copies are pinned to exactly this value."""
    with open(cert_path, encoding="ascii") as fh:
        der = ssl.PEM_cert_to_DER_cert(fh.read())
    return hashlib.sha256(der).hexdigest()


def admin_peer_ok(peer):
    """Admin requests must come from this PC, the home network, or Caddy on this server (which adds HTTPS).
    Uses the real connection address, never X-Forwarded-For, so it can't be faked."""
    try:
        a = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or a.is_private or a.is_link_local


BUILD_RX = re.compile(r"^FMPB-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")


def unlock_master(private_hex):
    """Secret for build tags and page keys, derived from the signing key (the build PC derives the same one)."""
    return hmac.new(bytes.fromhex(private_hex), b"fmp-unlock-master-v1", hashlib.sha256).digest()


def build_tag(master, purchase_key):
    raw = hmac.new(master, b"build|" + purchase_key.strip().upper().encode(), hashlib.sha256).digest()
    code = base64.b32encode(raw).decode()[:16]
    return "FMPB-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))


def wrapped_page_key(master, tag, machine):
    """The page key for a build, wrapped to one PC (the copy unwraps it with its own Machine ID)."""
    key = hmac.new(master, b"pages|" + tag.encode(), hashlib.sha256).digest()
    pad = hashlib.sha256(f"fmp-unlock|{machine}|{tag}".encode()).digest()
    return bytes(a ^ b for a, b in zip(key, pad)).hex()


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def canonical(payload):
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------------------------- updates
UPDATE_ZIP_RX = re.compile(r"(\d+(?:\.\d+)+)")       # a version like 1.4 or 1.4.2 inside a package filename
_SHA_CACHE = {}


HONEYPOT = "fmp_trap_q"                               # the Apply form's hidden anti-bot box (see apply_page)


# ---------------------------------------------------------------------------------------------- VTC Finder
# The app keeps the same lists in fmpro/vtc_profile.py (the tests check they match).
VTC_GAMES = {"ats": "American Truck Simulator", "ets2": "Euro Truck Simulator 2", "both": "ATS and ETS2"}
VTC_REGIONS = {"world": "Worldwide", "na": "North America", "sa": "South America", "eu": "Europe", "as": "Asia",
               "oc": "Oceania", "af": "Africa"}
VTC_STYLES = {"realistic": "Realistic", "relaxed": "Relaxed", "competitive": "Competitive", "roleplay": "Roleplay"}
VTC_PLAYS = {"single": "Single-player", "convoy": "Convoy", "tmp": "TruckersMP"}
VTC_LANGS = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese", "it": "Italian",
             "nl": "Dutch", "pl": "Polish", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian",
             "bg": "Bulgarian", "hr": "Croatian", "sr": "Serbian", "el": "Greek", "tr": "Turkish", "ru": "Russian",
             "uk": "Ukrainian", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian", "fi": "Finnish",
             "sv": "Swedish", "no": "Norwegian", "da": "Danish", "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}
VTC_SIZES = {"s": ("1-5 drivers", 1, 5), "m": ("6-20 drivers", 6, 20), "l": ("21+ drivers", 21, 10 ** 9)}
VTC_SORTS = {"match": "Best match", "active": "Most active this month", "new": "Newest", "name": "Name"}
VTC_REASONS = {"offensive": "Offensive or hateful", "pretend": "Pretends to be someone else", "scam": "Scam or bad link",
               "spam": "Spam", "other": "Something else"}
VTC_PER_PAGE = 20
VTC_LISTS = {"languages": (VTC_LANGS, 5), "styles": (VTC_STYLES, 4), "plays": (VTC_PLAYS, 3)}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def vtc_text(v, limit, lines=False):
    """Plain text: no control or direction-changing characters, spaces tidied, cut to the limit."""
    v = "".join(c for c in str(v if v is not None else "")
                if c == "\n" or (unicodedata.category(c) not in ("Cc", "Cs", "Co") and c not in "\u202a\u202b\u202c\u202d\u202e"
                                  "\u2066\u2067\u2068\u2069\u200e\u200f\u2028\u2029"))
    if lines:
        v = re.sub(r"\n{3,}", "\n\n", "\n".join(" ".join(x.split()) for x in v.split("\n"))).strip()
    else:
        v = " ".join(v.split())
    return v[:limit].strip()


def vtc_link(kind, v):
    """An https link, or '' . Discord must be an invite and TruckersMP must be truckersmp.com."""
    v = str(v or "").strip()
    if not v or len(v) > 200 or not re.fullmatch(r"https://[A-Za-z0-9.\-]+(?:/[A-Za-z0-9\-._~!$&'()*+,;=:@%/]*)?(?:\?[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*)?(?:#[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*)?", v):
        return ""
    u = urllib.parse.urlsplit(v)
    host = (u.hostname or "").lower()
    if "." not in host or host.startswith((".", "-")) or host.endswith((".", "-")) or ".." in host:
        return ""
    if kind == "discord":
        ok = (host in ("discord.gg", "www.discord.gg") and len(u.path) > 1) or \
             (host in ("discord.com", "www.discord.com") and u.path.startswith("/invite/") and len(u.path) > 8)
        return v if ok else ""
    if kind == "truckersmp":
        return v if host in ("truckersmp.com", "www.truckersmp.com") else ""
    return v


def _vtc_int(v, hi):
    return min(hi, max(0, int(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else 0


def vtc_clean(p):
    """Only the expected shape, choices and lengths are kept - nothing else a client sends is stored."""
    p = p if isinstance(p, dict) else {}
    asks = p.get("asks") if isinstance(p.get("asks"), dict) else {}
    links = p.get("links") if isinstance(p.get("links"), dict) else {}
    out = {"tag": vtc_text(p.get("tag"), 8), "motto": vtc_text(p.get("motto"), 120),
           "about": vtc_text(p.get("about"), 1500, True),
           "games": p.get("games") if isinstance(p.get("games"), str) and p["games"] in VTC_GAMES else "both",
           "region": p.get("region") if isinstance(p.get("region"), str) and p["region"] in VTC_REGIONS else "world",
           "drivers": _vtc_int(p.get("drivers"), 100_000)}
    for name, (known, most) in VTC_LISTS.items():
        vals = p.get(name) if isinstance(p.get(name), list) else []
        out[name] = [k for k in known if k in [v for v in vals if isinstance(v, str)]][:most]
    out["asks"] = {"age": _vtc_int(asks.get("age"), 99), "km_week": _vtc_int(asks.get("km_week"), 100_000),
                   "mic": asks.get("mic") is True, "dlc": vtc_text(asks.get("dlc"), 120),
                   "trial": vtc_text(asks.get("trial"), 120), "text": vtc_text(asks.get("text"), 400, True)}
    offers = p.get("offers") if isinstance(p.get("offers"), list) else []
    out["offers"] = [x for x in (vtc_text(o, 80) for o in offers[:20]) if x][:6]
    out["links"] = {k: vtc_link(k, links.get(k)) for k in ("discord", "website", "truckersmp")}
    col = p.get("colours") if isinstance(p.get("colours"), list) else []
    col = [c if isinstance(c, str) and _HEX.fullmatch(c) else "" for c in col[:2]]
    out["colours"] = [col[0] if col and col[0] else "#0a1a33", col[1] if len(col) > 1 and col[1] else "#c8102e"]
    qs, seen = [], set()
    for q in (p.get("questions") if isinstance(p.get("questions"), list) else [])[:20]:
        text = vtc_text(q.get("q"), 120) if isinstance(q, dict) else ""
        if text and text.lower() not in seen:
            seen.add(text.lower())
            qs.append({"q": text, "type": "yesno" if q.get("type") == "yesno" else "text"})
    out["questions"] = qs[:5]
    return out


def vtc_question_id(text):
    """The form field for a question. Made from its words, so an answer can't land on a question edited meanwhile."""
    return "q_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def vtuple(s):
    """Compare version strings like '1.3', '1.4', '1.4.2' as tuples of ints."""
    out = []
    for part in str(s or "").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def file_sha256(path):
    """SHA-256 of a file, cached by (path, mtime, size) so repeat checks don't re-hash a big zip."""
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _SHA_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _SHA_CACHE[path] = (key, digest)
    return digest


class LicenceError(Exception):
    def __init__(self, code, msg, status=400):
        super().__init__(msg)
        self.code, self.msg, self.status = code, msg, status


class Store:
    """Purchases and activations in licenses.db. Thread-safe."""

    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(folder, "licenses.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS purchases(key TEXT PRIMARY KEY, name TEXT, email TEXT, created TEXT,
                  slots INTEGER DEFAULT 2, note TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS activations(id INTEGER PRIMARY KEY, key TEXT, machine TEXT, slot INTEGER,
                  issued TEXT, released TEXT, file TEXT);
                CREATE TABLE IF NOT EXISTS recruit(key TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, company TEXT,
                  intro TEXT DEFAULT '', open INTEGER DEFAULT 1, updated TEXT);
                CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL,
                  created TEXT, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_apps_code ON applications(code);
                CREATE TABLE IF NOT EXISTS leaderboard(key TEXT PRIMARY KEY, pid TEXT UNIQUE NOT NULL, company TEXT,
                  currency TEXT DEFAULT '', stats TEXT NOT NULL, drivers TEXT DEFAULT '{}', updated TEXT);
                CREATE TABLE IF NOT EXISTS vtc_profiles(key TEXT PRIMARY KEY, listed INTEGER DEFAULT 0,
                  hidden INTEGER DEFAULT 0, data TEXT NOT NULL, created TEXT, updated TEXT);
                CREATE TABLE IF NOT EXISTS vtc_reports(id INTEGER PRIMARY KEY, code TEXT NOT NULL, reason TEXT,
                  note TEXT DEFAULT '', created TEXT);
                CREATE INDEX IF NOT EXISTS ix_vtc_reports_code ON vtc_reports(code);
            """)
            self._never_reuse_application_ids()
            for table, col, decl in (("purchases", "revoked", "TEXT"), ("activations", "last_seen", "TEXT"),
                                     ("activations", "last_ip", "TEXT"), ("purchases", "resets", "INTEGER DEFAULT 0"),
                                     ("purchases", "last_reset", "TEXT"), ("purchases", "product", "TEXT"),
                                     ("activations", "build", "TEXT")):
                cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self.db.commit()

    APP_ID_FLOOR = 1_000_000          # above every number the old server ever handed out

    def _never_reuse_application_ids(self):
        """Application numbers must never repeat: the app skips a number it has already saved and confirms it,
        which deletes it here. The old table (plain INTEGER PRIMARY KEY) reused 1 whenever it was empty, so every
        application after the first was thrown away. Rebuild it with AUTOINCREMENT and start well above old numbers."""
        sql = (self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()
               or [""])[0] or ""
        if "AUTOINCREMENT" not in sql.upper():
            self.db.executescript("""
                ALTER TABLE applications RENAME TO applications_old;
                CREATE TABLE applications(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, created TEXT,
                  data TEXT NOT NULL);
                INSERT INTO applications(id, code, created, data) SELECT id, code, created, data FROM applications_old;
                DROP TABLE applications_old;
                CREATE INDEX IF NOT EXISTS ix_apps_code ON applications(code);
            """)
        top = max(self.db.execute("SELECT COALESCE(MAX(id), 0) FROM applications").fetchone()[0], self.APP_ID_FLOOR)
        row = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='applications'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('applications', ?)", (top,))
        elif row[0] < top:
            self.db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='applications'", (top,))
        self.db.commit()

    def purchase(self, key):
        p = self.db.execute("SELECT * FROM purchases WHERE key=?", (key,)).fetchone()
        if not p:
            raise LicenceError("unknown_key", "That purchase key doesn't exist. Check it was typed correctly.", 404)
        return dict(p)

    def active(self, key):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM activations WHERE key=? AND released IS NULL ORDER BY slot", (key,))]

    def _valid(self, key, machine, product=None):
        key, machine = (key or "").strip().upper(), (machine or "").strip().upper()
        product = product or PRODUCT
        if product not in PRODUCTS:
            raise LicenceError("bad_product", "This licence server only licenses Fleet Manager Pro.")
        if not KEY_RX.match(key):
            raise LicenceError("bad_key", f"Purchase keys look like {PRODUCTS[product]}-XXXX-XXXX-XXXX-XXXX.")
        if not MACHINE_RX.match(machine):
            raise LicenceError("bad_machine", "Invalid Machine ID.")
        p = self.purchase(key)
        if product_of(p) != product:
            raise LicenceError("wrong_product", f"That purchase key is for {product_of(p)}, not {product}.", 403)
        if p.get("revoked"):
            raise LicenceError("revoked", "This purchase has been revoked. Contact the seller.", 403)
        return key, machine, p

    def note_build(self, act_id, build):
        if build:
            with self.lock:
                self.db.execute("UPDATE activations SET build=? WHERE id=?", (build, act_id))
                self.db.commit()

    def activate(self, key, machine, ip="", product=None):
        with self.lock:
            key, machine, p = self._valid(key, machine, product)
            act = self.active(key)
            mine = next((a for a in act if a["machine"] == machine), None)
            if not mine:
                if len(act) >= p["slots"]:
                    raise LicenceError("slots_full", f"This purchase is already active on {p['slots']} PCs. Deactivate one "
                                       "of them (Settings > Licence) or ask the seller to release it.", 403)
                used = {a["slot"] for a in act}
                slot = min(s for s in range(1, p["slots"] + 1) if s not in used)
                self.db.execute("INSERT INTO activations(key, machine, slot, issued, last_seen, last_ip) VALUES(?,?,?,?,?,?)",
                                (key, machine, slot, iso(now()), iso(now()), ip))
                self.db.commit()
                mine = next(a for a in self.active(key) if a["machine"] == machine)
            else:
                self._seen(mine["id"], ip)
            return p, mine

    def check(self, key, machine, ip="", product=None):
        with self.lock:
            key, machine, p = self._valid(key, machine, product)
            mine = next((a for a in self.active(key) if a["machine"] == machine), None)
            if not mine:
                raise LicenceError("not_active", "This PC is no longer activated on that purchase. Activate it again.", 403)
            self._seen(mine["id"], ip)
            return p, mine

    def deactivate(self, key, machine, product=None):
        with self.lock:
            key, machine, _ = self._valid(key, machine, product)
            n = self.db.execute("UPDATE activations SET released=? WHERE key=? AND machine=? AND released IS NULL",
                                (iso(now()), key, machine)).rowcount
            self.db.commit()
            if not n:
                raise LicenceError("not_active", "This PC isn't active on that purchase.", 404)

    def _seen(self, act_id, ip):
        self.db.execute("UPDATE activations SET last_seen=?, last_ip=? WHERE id=?", (iso(now()), ip, act_id))
        self.db.commit()

    # ---- recruitment (Fleet Manager Pro): public Apply page per company, applications held until collected
    APP_FIELDS = {"name": 80, "contact": 120, "age": 3, "country": 60, "games": 20, "experience": 400, "message": 1500}
    MAX_PENDING = 300

    def recruit_sync(self, key, machine, product, company, intro, is_open, ack, ip="", profile=None):
        with self.lock:
            p, _act = self.check(key, machine, ip, product)          # only an activated PC of the purchase
            row = self.db.execute("SELECT code FROM recruit WHERE key=?", (p["key"],)).fetchone()
            if row:
                code = row[0]
            else:
                while True:
                    code = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
                    if not self.db.execute("SELECT 1 FROM recruit WHERE code=?", (code,)).fetchone():
                        break
            self.db.execute("INSERT INTO recruit(key, code, company, intro, open, updated) VALUES(?,?,?,?,?,?) "
                            "ON CONFLICT(key) DO UPDATE SET company=excluded.company, intro=excluded.intro, "
                            "open=excluded.open, updated=excluded.updated",
                            (p["key"], code, str(company or "")[:80], str(intro or "")[:600], 1 if is_open else 0, iso(now())))
            ids = [int(i) for i in (ack or []) if isinstance(i, int) or str(i).isdigit()][:500]
            if ids:
                self.db.execute(f"DELETE FROM applications WHERE code=? AND id IN ({','.join('?' * len(ids))})", [code] + ids)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            if profile is not None:
                self._vtc_store(p["key"], profile)
            self.db.commit()
            self._vtc_cache = None
            pending = [dict(id=r[0], created=r[1], **json.loads(r[2])) for r in self.db.execute(
                "SELECT id, created, data FROM applications WHERE code=? ORDER BY id LIMIT 100", (code,))]
            return code, pending

    # ---- VTC Finder: the company's public profile (its address is /vtc/<the recruit code>)
    VTC_STALE_DAYS = 30               # a company that hasn't synced for this long isn't shown
    VTC_CACHE = 60                    # seconds the search list is kept (cleared at once by any change)
    VTC_REPORTS_PER_CODE, VTC_REPORTS_MAX = 50, 2000
    _vtc_cache = None

    def _vtc_store(self, key, profile):
        """Keep this company's profile. Not listed = only its Apply questions are kept. The hide switch survives edits."""
        listed = isinstance(profile, dict) and profile.get("listed") is True
        data = vtc_clean(profile)
        if not listed:
            data = vtc_clean({"questions": data["questions"]})
        row = self.db.execute("SELECT hidden FROM vtc_profiles WHERE key=?", (key,)).fetchone()
        if not listed and not data["questions"] and not (row and row["hidden"]):
            self.db.execute("DELETE FROM vtc_profiles WHERE key=?", (key,))
            return
        # 'created' is when it was (last) put in the finder, for the Newest order
        self.db.execute("INSERT INTO vtc_profiles(key, listed, data, created, updated) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET created=CASE WHEN excluded.listed AND NOT vtc_profiles.listed "
                        "THEN excluded.created ELSE vtc_profiles.created END, listed=excluded.listed, "
                        "data=excluded.data, updated=excluded.updated",
                        (key, 1 if listed else 0, json.dumps(data, separators=(",", ":")), iso(now()), iso(now())))

    def vtc_status(self, key):
        """What the app shows its owner: listed, hidden by the server's owner, and whether the page can be seen."""
        with self.lock:
            r = self.db.execute("SELECT v.listed, v.hidden, v.data, c.company FROM vtc_profiles v "
                                "JOIN recruit c ON c.key=v.key WHERE v.key=?", (key,)).fetchone()
        if not r:
            return {"stored": False, "listed": False, "hidden": False, "visible": False}
        full = bool((r["company"] or "").strip() and json.loads(r["data"])["about"])
        return {"stored": True, "listed": bool(r["listed"]), "hidden": bool(r["hidden"]),
                "visible": bool(r["listed"] and not r["hidden"] and full)}

    def vtc_all(self):
        """Every company that can be seen: listed, not hidden, not revoked, synced lately, with a name and a description."""
        t = time.monotonic()
        with self.lock:
            hit = self._vtc_cache
            if hit and t - hit[0] < self.VTC_CACHE:
                return hit[1]
            cutoff = iso(now() - dt.timedelta(days=self.VTC_STALE_DAYS))
            rows = self.db.execute(
                "SELECT c.code, c.company, c.open, v.data, v.created, c.updated, l.stats, l.updated lb_updated "
                "FROM vtc_profiles v JOIN recruit c ON c.key=v.key JOIN purchases p ON p.key=v.key "
                "LEFT JOIN leaderboard l ON l.key=v.key AND l.updated>=? "
                "WHERE v.listed=1 AND v.hidden=0 AND p.revoked IS NULL AND c.updated>=?", (cutoff, cutoff)).fetchall()
            out = []
            for r in rows:
                d = json.loads(r["data"])
                name = vtc_text(r["company"], 64)
                if not name or not d["about"]:
                    continue
                stats = json.loads(r["stats"]) if r["stats"] else None
                month = ((stats or {}).get("month") or {}).get("all")
                out.append(dict(d, code=r["code"], company=name, open=bool(r["open"]), created=r["created"] or "",
                                updated=r["updated"] or "", stats=stats, month_km=month[0] if month else 0,
                                _name=name.lower(), _tag=d["tag"].lower(),
                                _text=" ".join((name, d["tag"], d["motto"], d["about"])).lower()))
            self._vtc_cache = (t, out)
            return out

    def vtc_page(self, code):
        if not re.fullmatch(r"[a-z2-9]{8}", code or ""):
            return None
        return next((c for c in self.vtc_all() if c["code"] == code), None)

    def vtc_search(self, words="", game="", is_open=True, lang="", region="", style="", plays="", size="", sort="match"):
        """The companies that fit, in order. Unknown filter values are ignored."""
        words = vtc_text(words, 60).lower()
        parts = words.split()
        out = []
        for c in self.vtc_all():
            if is_open and not c["open"]:
                continue
            if game in VTC_GAMES and not (c["games"] in (game, "both") if game != "both" else c["games"] == "both"):
                continue
            if lang in VTC_LANGS and lang not in c["languages"]:
                continue
            if region in VTC_REGIONS and c["region"] not in (region, "world"):
                continue
            if style in VTC_STYLES and style not in c["styles"]:
                continue
            if plays in VTC_PLAYS and plays not in c["plays"]:
                continue
            if size in VTC_SIZES and not VTC_SIZES[size][1] <= c["drivers"] <= VTC_SIZES[size][2]:
                continue
            if not all(w in c["_text"] for w in parts):
                continue
            rank = 0 if not parts or words in c["_name"] else 1 if all(w in c["_name"] for w in parts) else \
                2 if any(w in c["_tag"] for w in parts) else 3
            out.append((rank, c))
        # each sort keeps the order of the one before for ties, so the least important goes first
        out.sort(key=lambda rc: rc[1]["_name"])
        if sort != "name":
            out.sort(key=lambda rc: rc[1]["updated"][:10], reverse=True)
            out.sort(key=lambda rc: not rc[1]["open"])
            if sort == "active":
                out.sort(key=lambda rc: -rc[1]["month_km"])
            elif sort == "new":
                out.sort(key=lambda rc: rc[1]["created"], reverse=True)
            else:
                out.sort(key=lambda rc: rc[0])
        return [c for _r, c in out]

    def vtc_report(self, code, reason, note):
        if not self.vtc_page(code):
            raise LicenceError("not_found", "That company isn't listed.", 404)
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM vtc_reports WHERE code=?", (code,)).fetchone()[0]
            total = self.db.execute("SELECT COUNT(*) FROM vtc_reports").fetchone()[0]
            if n < self.VTC_REPORTS_PER_CODE and total < self.VTC_REPORTS_MAX:
                self.db.execute("INSERT INTO vtc_reports(code, reason, note, created) VALUES(?,?,?,?)",
                                (code, reason if reason in VTC_REASONS else "other", vtc_text(note, 300), iso(now())))
                self.db.commit()

    def vtc_admin_list(self):
        """Listings (and hidden ones) with their reports, most reported first."""
        with self.lock:
            rows = self.db.execute("SELECT v.key, c.code, c.company, v.listed, v.hidden, v.updated FROM vtc_profiles v "
                                   "JOIN recruit c ON c.key=v.key WHERE v.listed=1 OR v.hidden=1").fetchall()
            reps = {}
            for r in self.db.execute("SELECT code, reason, note, created FROM vtc_reports ORDER BY id DESC"):
                reps.setdefault(r["code"], []).append({"reason": r["reason"], "note": r["note"], "created": r["created"]})
        out = [dict(dict(r), listed=bool(r["listed"]), hidden=bool(r["hidden"]), reports=reps.get(r["code"], [])) for r in rows]
        out.sort(key=lambda r: (-len(r["reports"]), (r["company"] or "").lower()))
        return out

    def vtc_hide(self, key, hidden):
        key = str(key or "").strip().upper()
        with self.lock:
            n = self.db.execute("UPDATE vtc_profiles SET hidden=? WHERE key=?", (1 if hidden else 0, key)).rowcount
            self.db.commit()
            self._vtc_cache = None
        if not n:
            raise LicenceError("not_found", "That company hasn't listed itself, so there is nothing to hide or show. Its "
                               "Owner or a Manager lists it in the app: Company > VTC Profile.", 404)

    def vtc_clear_reports(self, key):
        key = str(key or "").strip().upper()
        with self.lock:
            n = self.db.execute("DELETE FROM vtc_reports WHERE code IN (SELECT code FROM recruit WHERE key=?)", (key,)).rowcount
            self.db.commit()
            return n

    # ---- global leaderboard (Fleet Manager Pro): every company's totals in one ranking
    LB_PERIODS = ("week", "month", "year", "all")
    LB_GAMES = ("all", "ats", "ets2")
    # per period and game, a list of numbers in this order (drivers = how many drivers delivered)
    LB_METRICS = ("km", "revenue", "jobs", "mass", "score", "xp", "realism", "drivers")
    LB_DRIVERS = 10                   # best drivers per company and period (only when the company shares names)
    LB_STALE_DAYS = 30                # a company that hasn't synced for this long drops off the board
    LB_MAX = 500

    @classmethod
    def _lb_numbers(cls, vals):
        if not isinstance(vals, list):
            return None
        out = []
        for i, m in enumerate(cls.LB_METRICS):
            v = vals[i] if i < len(vals) else 0
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
                v = 0
            v = max(0.0, min(100.0 if m == "score" else 1e12, float(v)))
            out.append(round(v, 1) if m in ("score", "mass") else int(v))
        return out

    @classmethod
    def lb_clean(cls, stats, drivers):
        """Only the expected shape and plain, bounded numbers are kept - nothing else a client sends is stored."""
        clean = {}
        for per in cls.LB_PERIODS:
            row = stats.get(per) if isinstance(stats, dict) else None
            if isinstance(row, dict):
                clean[per] = {g: cls._lb_numbers(row.get(g)) or cls._lb_numbers([]) for g in cls.LB_GAMES}
        drv = {}
        for per in cls.LB_PERIODS:
            rows = drivers.get(per) if isinstance(drivers, dict) else None
            if not isinstance(rows, list):
                continue
            out = []
            for d in rows[:cls.LB_DRIVERS]:
                if isinstance(d, dict):
                    name = " ".join(str(d.get("name") or "").split())[:40]
                    nums = cls._lb_numbers(d.get("v"))
                    if name and nums:
                        out.append({"name": name, "v": nums})
            if out:
                drv[per] = out
        return clean, drv

    def leaderboard_sync(self, key, machine, product, join, company, currency, stats, drivers, ip=""):
        """Store (or, when leaving, delete) this company's totals. Returns its public board id, or None."""
        with self.lock:
            p, _act = self.check(key, machine, ip, product)          # only an activated PC of the purchase
            row = self.db.execute("SELECT pid FROM leaderboard WHERE key=?", (p["key"],)).fetchone()
            clean, drv = self.lb_clean(stats, drivers)
            company = " ".join(str(company or "").split())[:60]
            if not join or not company or not clean:
                self.db.execute("DELETE FROM leaderboard WHERE key=?", (p["key"],))
                self.db.commit()
                self._vtc_cache = None
                return None
            pid = row[0] if row else secrets.token_hex(6)
            self._vtc_cache = None
            self.db.execute("INSERT INTO leaderboard(key, pid, company, currency, stats, drivers, updated) "
                            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET company=excluded.company, "
                            "currency=excluded.currency, stats=excluded.stats, drivers=excluded.drivers, "
                            "updated=excluded.updated",
                            (p["key"], pid, company, str(currency or "")[:3], json.dumps(clean, separators=(",", ":")),
                             json.dumps(drv, separators=(",", ":")), iso(now())))
            self.db.commit()
            return pid

    def leaderboard(self):
        """Every company on the board (synced lately, not revoked) and the best shared drivers."""
        cutoff = iso(now() - dt.timedelta(days=self.LB_STALE_DAYS))
        with self.lock:
            rows = self.db.execute("SELECT l.pid, l.company, l.currency, l.stats, l.drivers, l.updated FROM leaderboard l "
                                   "JOIN purchases p ON p.key=l.key WHERE p.revoked IS NULL AND l.updated>=? "
                                   "ORDER BY l.updated DESC LIMIT ?", (cutoff, self.LB_MAX)).fetchall()
        companies, drivers = [], {}
        for r in rows:
            companies.append({"id": r["pid"], "company": r["company"], "currency": r["currency"] or "",
                              "stats": json.loads(r["stats"]), "updated": r["updated"]})
            for per, lst in json.loads(r["drivers"] or "{}").items():
                drivers.setdefault(per, []).extend(dict(d, company=r["company"], id=r["pid"], currency=r["currency"] or "")
                                                   for d in lst)
        top = {}                          # only the best 25 drivers for each measure go back, so the answer stays small
        for per, lst in drivers.items():
            keep = {}
            for i in range(len(self.LB_METRICS) - 1):
                for d in sorted(lst, key=lambda d, i=i: -d["v"][i])[:25]:
                    keep[(d["id"], d["name"])] = d
            top[per] = list(keep.values())
        return companies, top

    def board_rows(self, metric="km", period="month", game="all", limit=50):
        """Companies (and shared drivers) ranked by one measure, for the public page."""
        mi = self.LB_METRICS.index(metric) if metric in self.LB_METRICS else 0
        period = period if period in self.LB_PERIODS else "month"
        game = game if game in self.LB_GAMES else "all"
        companies, top = self.leaderboard()
        rows = []
        for c in companies:
            v = (c["stats"].get(period) or {}).get(game)
            if v and v[2]:                                   # at least one delivery
                rows.append(dict(c, v=v))
        rows.sort(key=lambda r: -r["v"][mi])
        drivers = sorted(top.get(period, []), key=lambda d: -d["v"][mi])[:limit] if game == "all" else []
        return rows[:limit], drivers

    def recruit_page(self, code):
        if not re.fullmatch(r"[a-z2-9]{8}", code or ""):
            return None
        r = self.db.execute("SELECT r.company, r.intro, r.open, p.revoked, v.data FROM recruit r JOIN purchases p ON p.key=r.key "
                            "LEFT JOIN vtc_profiles v ON v.key=r.key WHERE r.code=?", (code,)).fetchone()
        if not r or r["revoked"]:
            return None
        d = dict(r)
        d["questions"] = json.loads(d.pop("data") or "{}").get("questions") or []
        return d

    def add_application(self, code, form):
        page = self.recruit_page(code)
        if not page or not page["open"]:
            raise LicenceError("closed", "This company isn't taking applications right now.", 404)
        data = {k: str(form.get(k, "")).strip()[:n] for k, n in self.APP_FIELDS.items()}
        if not data["name"] or not data["contact"]:
            raise LicenceError("missing", "Please fill in your name and how to contact you.")
        data["games"] = data["games"] if data["games"] in ("ats", "ets2", "both") else ""
        if not data["age"].isdecimal() or not MIN_AGE <= int(data["age"]) <= 99:
            raise LicenceError("age", f"You must be at least {MIN_AGE} to apply. Please enter your age.")
        data["via"] = "finder" if form.get("via") == "finder" else "link"
        answers = []
        for q in page["questions"]:
            a = vtc_text(form.get(vtc_question_id(q["q"])), 300)
            if q["type"] == "yesno":
                a = {"yes": "Yes", "no": "No"}.get(a.lower(), "")
            if a:
                answers.append([q["q"], a])
        if answers:
            data["answers"] = answers
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM applications WHERE code=?", (code,)).fetchone()[0]
            if n >= self.MAX_PENDING:
                raise LicenceError("full", "This company has too many unread applications. Try again later.", 429)
            self.db.execute("INSERT INTO applications(code, created, data) VALUES(?,?,?)", (code, iso(now()), json.dumps(data)))
            self.db.commit()

    # ---- admin
    def new_purchase(self, name, email="", product=None):
        name = (name or "").strip()
        product = product or PRODUCT
        if not name:
            raise LicenceError("bad_request", "A buyer name is required")
        if product not in PRODUCTS:
            raise LicenceError("bad_product", "This licence server only issues Fleet Manager Pro keys.")
        with self.lock:
            while True:
                code = base64.b32encode(secrets.token_bytes(10)).decode()[:16]
                key = PRODUCTS[product] + "-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))
                if not self.db.execute("SELECT 1 FROM purchases WHERE key=?", (key,)).fetchone():
                    break
            self.db.execute("INSERT INTO purchases(key, name, email, created, slots, product) VALUES(?,?,?,?,?,?)",
                            (key, name, (email or "").strip(), iso(now()), SLOTS_PER_PURCHASE, product))
            self.db.commit()
            return key

    def release(self, key, machine):
        p = self.purchase((key or "").strip().upper())
        self.deactivate(key, machine, product_of(p))

    def reset_uses(self, key):
        """Free every PC slot on a purchase at once (e.g. the buyer's PC died and they couldn't deactivate).
        The old PCs lock at their next check; the buyer can activate again right away. Counted per purchase."""
        key = (key or "").strip().upper()
        with self.lock:
            self.purchase(key)
            freed = [a["machine"] for a in self.active(key)]
            self.db.execute("UPDATE activations SET released=? WHERE key=? AND released IS NULL", (iso(now()), key))
            self.db.execute("UPDATE purchases SET resets=COALESCE(resets,0)+1, last_reset=? WHERE key=?", (iso(now()), key))
            self.db.commit()
            return freed

    def revoke(self, key, revoked=True):
        with self.lock:
            self.purchase(key)
            self.db.execute("UPDATE purchases SET revoked=? WHERE key=?", (iso(now()) if revoked else None, key))
            self.db.commit()
            self._vtc_cache = None

    def delete_purchase(self, key):
        """Delete a purchase for good: its PCs, its recruitment page and waiting applications go too. Copies that use the
        key lock at their next check (the key no longer exists). A JSON copy of everything removed is appended to
        deleted_purchases.jsonl in the server folder first, so a mistake can be put right by hand."""
        key = (key or "").strip().upper()
        with self.lock:
            p = self.purchase(key)
            acts = [dict(r) for r in self.db.execute("SELECT * FROM activations WHERE key=?", (key,))]
            rec = self.db.execute("SELECT * FROM recruit WHERE key=?", (key,)).fetchone()
            rec = dict(rec) if rec else None
            n_apps = self.db.execute("SELECT COUNT(*) FROM applications WHERE code=?", (rec["code"],)).fetchone()[0] if rec else 0
            with open(os.path.join(self.folder, "deleted_purchases.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"deleted": iso(now()), "purchase": p, "activations": acts, "recruit": rec,
                                     "applications_dropped": n_apps}) + "\n")
            self.db.execute("DELETE FROM activations WHERE key=?", (key,))
            if rec:
                self.db.execute("DELETE FROM applications WHERE code=?", (rec["code"],))
                self.db.execute("DELETE FROM recruit WHERE key=?", (key,))
                self.db.execute("DELETE FROM vtc_reports WHERE code=?", (rec["code"],))
            self.db.execute("DELETE FROM vtc_profiles WHERE key=?", (key,))
            self.db.execute("DELETE FROM leaderboard WHERE key=?", (key,))
            self.db.execute("DELETE FROM purchases WHERE key=?", (key,))
            self.db.commit()
            self._vtc_cache = None
            return {"key": key, "name": p["name"], "pcs": len([a for a in acts if not a["released"]]),
                    "was_revoked": bool(p.get("revoked"))}

    def delete_revoked(self):
        """Delete every revoked purchase. Returns what was deleted."""
        with self.lock:
            keys = [r[0] for r in self.db.execute("SELECT key FROM purchases WHERE revoked IS NOT NULL")]
            return [self.delete_purchase(k) for k in keys]

    def listing(self):
        with self.lock:
            out = []
            for p in self.db.execute("SELECT * FROM purchases ORDER BY created DESC"):
                p = dict(p)
                p["product"] = product_of(p)
                p["pcs"] = self.active(p["key"])
                out.append(p)
            return out

    def whose(self, master, tag):
        """The purchase a build tag was made for: {"key", "name", ...}, "STOCK" for an unassigned copy, or None."""
        tag = (tag or "").strip().upper()
        if tag == build_tag(master, "STOCK"):
            return "STOCK"
        with self.lock:
            for p in self.db.execute("SELECT key, name, email, revoked FROM purchases"):
                if build_tag(master, p["key"]) == tag:
                    return dict(p)
        return None


def _page(title, body, company=""):
    """A small self-contained page in the Fleet Manager colours (no scripts, no outside files)."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#050e1c,#0f2546 60%,#3a1424);
font:15px/1.5 "Segoe UI",system-ui,sans-serif;color:#e8eef8;padding:20px}}
main{{width:min(560px,100%);background:#0d203d;border:1px solid #1d3a66;border-top:4px solid #c8102e;border-radius:16px;
padding:26px;box-shadow:0 30px 80px rgba(0,0,0,.5)}}
h1{{margin:0 0 8px;font-size:24px}} small,.small{{color:#93a7c6;font-size:12.5px}} a{{color:#e23552}}
label{{display:block;margin:12px 0 0;font-weight:600;font-size:13px;color:#c7d4ea}}
input,select,textarea{{display:block;width:100%;box-sizing:border-box;margin-top:5px;padding:9px 11px;border-radius:9px;
border:1px solid #1d3a66;background:#071327;color:#e8eef8;font:inherit}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .hp{{position:absolute;left:-9999px}}
main.wide{{width:min(860px,100%)}} h2{{font-size:17px;margin:22px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid #1d3a66}}
th{{color:#93a7c6;font-size:12px;text-transform:uppercase}} .n{{text-align:right}}
.seg{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}} .seg a{{padding:5px 10px;border-radius:8px;background:#071327;
color:#c7d4ea;text-decoration:none;font-size:13px;border:1px solid #1d3a66}}
.badge{{display:inline-grid;place-items:center;min-width:46px;height:46px;padding:0 6px;box-sizing:border-box;border-radius:11px;
font-weight:800;font-size:13px;flex:none}} .vtc{{display:flex;gap:13px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #1d3a66}}
.vtc>div{{min-width:0;overflow-wrap:anywhere}} .vtc b a{{color:#fff;text-decoration:none;font-size:16px}} .vtc b a:hover{{text-decoration:underline}}
.chip{{display:inline-block;padding:2px 8px;border-radius:999px;background:#071327;border:1px solid #1d3a66;font-size:12px;
color:#c7d4ea;margin:3px 4px 0 0}} .chip.on{{background:#14532d;border-color:#1f7a44;color:#d6f5e2}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 12px}} .nums b{{display:block;font-size:20px;color:#fff}}
.btn{{display:inline-block;padding:11px 18px;border-radius:10px;background:#c8102e;color:#fff;font-weight:700;text-decoration:none;margin-top:8px}}
.wrap{{overflow-wrap:anywhere}} summary{{cursor:pointer;color:#93a7c6;font-size:13px}} .err{{color:#ffb4c0}}
button{{margin-top:18px;width:100%;padding:11px;border:0;border-radius:10px;background:#c8102e;color:#fff;font-weight:700;font-size:15px}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}
<p class="small" style="margin-top:18px">Fleet Manager Pro{(' · ' + company) if company else ''}</p></main></body></html>"""


class Server:
    def __init__(self, folder, port=None):
        self.folder = folder
        self.store = Store(folder)
        with open(os.path.join(folder, "host_key.json"), encoding="utf-8") as fh:
            key = json.load(fh)
        self.secret = bytes.fromhex(key["private"])
        self.master = unlock_master(key["private"])
        self.public = key["public"]
        cfg_path = os.path.join(folder, "server_config.json")
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        cfg.setdefault("admin_token", secrets.token_urlsafe(24))
        cfg.setdefault("port", 8750)
        cfg.setdefault("lease_days", 7)
        cfg.setdefault("apply_base_url", "http://fleetmanager.servehttp.com:8750")   # what applicants open
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        self.cfg = cfg
        self.port = port or cfg["port"]
        self.hits = collections.defaultdict(collections.deque)
        self.hits_lock = threading.Lock()
        self.apply_hits = collections.defaultdict(collections.deque)
        # Encryption (optional): tls_cert.pem + tls_key.pem in this folder turn on HTTPS on the same port.
        self.tls_ctx, self.tls_pin = None, None
        cert, key_file = os.path.join(folder, "tls_cert.pem"), os.path.join(folder, "tls_key.pem")
        if os.path.exists(cert) and os.path.exists(key_file):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert, key_file)
            self.tls_ctx, self.tls_pin = ctx, tls_fingerprint(cert)
        self.log = logging.getLogger(f"licence.{id(self)}")
        self.log.propagate = False
        self._log_ready = False

    def setup_logging(self):
        """Write the server's activity to logs/ next to it (server.log + errors.log), plus the console."""
        if self._log_ready:
            return
        logdir = os.path.join(self.folder, "logs")
        try:
            os.makedirs(logdir, exist_ok=True)
        except OSError:
            self._log_ready = True
            return
        self.log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
        red = _KeyRedactor()
        for filename, lvl in (("server.log", logging.INFO), ("errors.log", logging.WARNING)):
            h = RotatingFileHandler(os.path.join(logdir, filename), maxBytes=2_000_000, backupCount=10, encoding="utf-8")
            h.setLevel(lvl)
            h.setFormatter(fmt)
            h.addFilter(red)
            self.log.addHandler(h)
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        console.addFilter(red)
        self.log.addHandler(console)
        self._log_ready = True

    def _req_detail(self, path, body, result):
        """A readable tail for the request log line (keys are masked by the log filter)."""
        if path in ("/v1/activate", "/v1/check", "/v1/deactivate"):
            return f"  key={body.get('key')} machine={body.get('machine')} build={body.get('build') or '-'}"
        if path == "/v1/update":
            m = (result or {}).get("manifest") or {}
            return f"  build={body.get('build') or '-'} has={body.get('version')} offer={m.get('version') or 'none'}"
        if path in ("/v1/recruit", "/v1/leaderboard"):
            return f"  key={body.get('key')}"
        if path.startswith("/v1/admin/"):
            return f"  {path.rsplit('/', 1)[-1]} key={body.get('key') or '-'}"
        return ""

    def lease(self, p, act, build=None):
        t = now()
        lease = {"v": 2, "product": product_of(p), "purchase": p["key"], "name": p["name"], "email": p["email"],
                 "machine": act["machine"], "slot": act["slot"], "slots": p["slots"], "issued": iso(t),
                 "expires": iso(t + dt.timedelta(days=self.cfg["lease_days"]))}
        if build:                                   # a sealed buyer copy: hand back the key to its pages
            lease["build"] = build
            lease["unlock"] = wrapped_page_key(self.master, build, act["machine"])
        lease["sig"] = ed25519.sign(self.secret, canonical(lease)).hex()
        return lease

    # ---- updates: per-copy signed manifests + downloads, from the updates/ folder next to this server
    def updates_dir(self):
        return os.path.join(self.folder, "updates")

    def _update_folder(self, build):
        """The folder that holds a copy's updates: updates/<BUILD TAG>, or updates/STOCK for a stock copy,
        or updates/GENERIC for an unsealed developer copy. None when there's nothing for this copy."""
        base = self.updates_dir()
        build = (build or "").strip().upper()
        if BUILD_RX.match(build):
            d = os.path.join(base, build)
            if os.path.isdir(d):
                return d
            if build == build_tag(self.master, "STOCK"):
                d = os.path.join(base, "STOCK")
                return d if os.path.isdir(d) else None
            return None
        d = os.path.join(base, "GENERIC")
        return d if os.path.isdir(d) else None

    def latest_update(self, build, flavor="exe"):
        """(version, filename, folder) of the newest package of this copy's flavour, or None. A package whose
        name contains 'no exe' is the no-exe build; any other is the single-program .exe build."""
        folder = self._update_folder(build)
        if not folder:
            return None
        want = "noexe" if "noexe" in str(flavor).lower().replace(" ", "").replace("-", "") else "exe"
        best = None
        try:
            names = os.listdir(folder)
        except OSError:
            return None
        for name in names:
            if not name.lower().endswith(".zip"):
                continue
            low = name.lower()
            is_noexe = "no exe" in low or "no-exe" in low or "noexe" in low
            if ("noexe" if is_noexe else "exe") != want:
                continue
            m = UPDATE_ZIP_RX.search(name)
            if not m:
                continue
            ver = m.group(1)
            if best is None or vtuple(ver) > vtuple(best[0]):
                best = (ver, name, folder)
        return best

    def update_manifest(self, build, client_version, flavor="exe"):
        """Signed offer of the newest build for this copy's flavour (or {'update': False} when it's current)."""
        best = self.latest_update(build, flavor)
        if not best:
            return {"ok": True, "update": False}
        ver, name, folder = best
        if vtuple(ver) <= vtuple(client_version or "0"):
            return {"ok": True, "update": False, "version": ver}
        path = os.path.join(folder, name)
        notes = ""
        npath = os.path.join(folder, "notes-%s.txt" % ver)
        if os.path.isfile(npath):
            with open(npath, encoding="utf-8", errors="replace") as fh:
                notes = fh.read()[:2000]
        m = {"product": PRODUCT, "build": (build or "").strip().upper(), "version": ver, "file": name,
             "size": os.path.getsize(path), "sha256": file_sha256(path), "notes": notes, "issued": iso(now())}
        m["sig"] = ed25519.sign(self.secret, canonical(m)).hex()
        return {"ok": True, "update": True, "manifest": m}

    def update_file(self, build, name):
        """Absolute path to a package a copy may download, or None (guards against path traversal)."""
        folder = self._update_folder(build)
        if not folder:
            return None
        name = os.path.basename(name or "")
        if not name.lower().endswith(".zip"):
            return None
        root = os.path.realpath(folder)
        path = os.path.realpath(os.path.join(folder, name))
        if path != root and not path.startswith(root + os.sep):
            return None
        return path if os.path.isfile(path) else None

    def rate_ok(self, ip, limit=60):
        t = time.time()
        with self.hits_lock:
            if len(self.hits) > 5000:   # forget addresses not seen for a minute, so memory can't grow forever
                for k in [k for k, v in self.hits.items() if not v or t - v[-1] > 60]:
                    del self.hits[k]
            q = self.hits[ip]
            while q and t - q[0] > 60:
                q.popleft()
            q.append(t)
            return len(q) <= limit

    def apply_rate_ok(self, ip, limit=5, window=3600):
        """At most 5 applications per address per hour."""
        t = time.time()
        with self.hits_lock:
            if len(self.apply_hits) > 5000:
                for k in [k for k, v in self.apply_hits.items() if not v or t - v[-1] > window]:
                    del self.apply_hits[k]
            q = self.apply_hits[ip]
            while q and t - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            return True

    # ---- VTC Finder pages: plain forms and links, no scripts, every word escaped
    def _base(self):
        return self.cfg["apply_base_url"].rstrip("/")

    @staticmethod
    def _badge(c):
        navy, accent = c["colours"]
        r, g, b = (int(navy[i:i + 2], 16) for i in (1, 3, 5))
        ink = "#fff" if 0.299 * r + 0.587 * g + 0.114 * b < 150 else "#111"
        text = html.escape((c["tag"] or "".join(w[0] for w in c["company"].split()[:3])).upper()[:8])
        return (f'<span class="badge" style="background:{navy};color:{ink};border:2px solid {accent}">{text}</span>')

    @staticmethod
    def _chips(c):
        names = ([VTC_GAMES[c["games"]], VTC_REGIONS[c["region"]]] + [VTC_LANGS[x] for x in c["languages"]]
                 + [VTC_STYLES[x] for x in c["styles"]] + [VTC_PLAYS[x] for x in c["plays"]])
        state = '<span class="chip on">Open to drivers</span>' if c["open"] else '<span class="chip">Closed</span>'
        return state + "".join(f'<span class="chip">{html.escape(n)}</span>' for n in names)

    @staticmethod
    def _seen(when):
        try:
            days = (now() - dt.datetime.fromisoformat(when.replace("Z", "+00:00"))).days
        except ValueError:
            return ""
        return "Active today" if days < 1 else "Active yesterday" if days < 2 else f"Active {days} days ago"

    def finder_page(self, query):
        """(status, html) for the VTC Finder search page."""
        q = {}
        for k, v in urllib.parse.parse_qs(query or "", max_num_fields=20).items():
            q[k] = v[0]
        known = {"game": VTC_GAMES, "lang": VTC_LANGS, "region": VTC_REGIONS, "style": VTC_STYLES, "plays": VTC_PLAYS,
                 "size": VTC_SIZES, "sort": VTC_SORTS}
        f = {k: (q.get(k) if q.get(k) in opts else "") for k, opts in known.items()}
        f["sort"] = f["sort"] or "match"
        f["q"] = vtc_text(q.get("q"), 60)
        everyone = q.get("open") == "all"
        rows = self.store.vtc_search(f["q"], f["game"], not everyone, f["lang"], f["region"], f["style"], f["plays"],
                                     f["size"], f["sort"])
        pages = max(1, (len(rows) + VTC_PER_PAGE - 1) // VTC_PER_PAGE)
        raw_p = q.get("p") or ""
        p = min(pages, max(1, int(raw_p))) if raw_p.isascii() and raw_p.isdecimal() and len(raw_p) < 6 else 1
        esc = html.escape

        def link(**change):
            args = {k: v for k, v in dict(f, open="all" if everyone else "", p="").items() if v}
            args.update(change)
            return "/vtcs?" + esc(urllib.parse.urlencode({k: v for k, v in args.items() if v and (k, v) != ("sort", "match")}))

        def pick(name, label, first, opts):
            sel = "".join(f'<option value="{k}"{" selected" if f[name] == k else ""}>{esc(t)}</option>' for k, t in opts.items())
            return f'<label>{label}<select name="{name}">' + (f'<option value="">{first}</option>' if first else "") + sel + "</select></label>"

        body = f"""<p>Find a virtual trucking company to drive for, read its profile and apply.</p>
<form method="get" action="/vtcs"><label>Search<input name="q" maxlength="60" value="{esc(f['q'])}" placeholder="Name, tag or a word from the description"></label>
<div class="grid">{pick("game", "Game", "Any", VTC_GAMES)}
<label>Recruiting<select name="open"><option value="">Open now</option><option value="all"{" selected" if everyone else ""}>Everyone</option></select></label>
{pick("lang", "Language", "Any", VTC_LANGS)}{pick("region", "Region", "Any", VTC_REGIONS)}{pick("style", "Style", "Any", VTC_STYLES)}
{pick("plays", "Plays on", "Any", VTC_PLAYS)}{pick("size", "Size", "Any", {k: v[0] for k, v in VTC_SIZES.items()})}{pick("sort", "Sort by", "", VTC_SORTS)}</div>
<button>Search</button></form>"""  # nosec B608  # HTML page, not SQL
        n = len(rows)
        filtered = any(v for k, v in f.items() if k != "sort")
        body += f'<h2>{n:,} compan{"y" if n == 1 else "ies"}</h2>'
        for c in rows[(p - 1) * VTC_PER_PAGE:p * VTC_PER_PAGE]:
            motto = f'<br>{esc(c["motto"])}' if c["motto"] else ""
            body += (f'<div class="vtc">{self._badge(c)}<div><b><a href="/vtc/{c["code"]}">{esc(c["company"])}</a></b>{motto}'
                     f'<br>{self._chips(c)}<br><small>{c["drivers"]:,} driver{"" if c["drivers"] == 1 else "s"} &middot; '
                     f'{self._seen(c["updated"])}</small></div></div>')
        if not n:
            body += "<p>No company matches that search.</p><p>"
            if not everyone:
                body += f'<a href="{link(open="all")}">Show companies that aren&#39;t recruiting</a><br>'
            if filtered:
                body += '<a href="/vtcs">Clear filters</a>'
            body += "</p>"
        if pages > 1:
            body += '<p class="seg">' + (f'<a href="{link(p=p - 1)}">Previous</a>' if p > 1 else "") + \
                f"<small>Page {p} of {pages}</small>" + (f'<a href="{link(p=p + 1)}">Next</a>' if p < pages else "") + "</p>"
        body += ('<p><small>Run a company in Fleet Manager Pro? List it in the app under Company, VTC Profile. '
                 '<a href="/leaderboard">Global Leaderboard</a></small></p>')
        return 200, _page("VTC Finder", body).replace("<main>", '<main class="wide">', 1)

    def vtc_profile_page(self, method, path, raw, ip):
        """(status, html) for a company's profile, and the report form on it."""
        rest = path[len("/vtc/"):].strip("/")
        code, _sep, action = rest.partition("/")
        c = self.store.vtc_page(code)
        back = '<p><a href="/vtcs">Back to the VTC Finder</a></p>'
        if not c or action not in ("", "report"):
            return 404, _page("Not found", "<p>That company isn't listed in the VTC Finder.</p>" + back)
        esc = html.escape
        if method == "POST":
            if action != "report":
                return 404, _page("Not found", back)
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), max_num_fields=10).items()}
            if not form.get(HONEYPOT):
                if form.get("reason") not in VTC_REASONS:
                    return 400, _page("Not sent", f'<p>Pick a reason first.</p><p><a href="/vtc/{code}">Back</a></p>')
                if not self.apply_rate_ok("report:" + ip, limit=3):
                    return 429, _page("Slow down", "<p>Too many reports from your connection. Try again in an hour.</p>" + back)
                self.store.vtc_report(code, form["reason"], form.get("note"))
                self.log.info("VTC report: %s about %s", form["reason"], code)
            return 200, _page("Thank you", "<p>Your report was sent. It will be looked at.</p>" + back)
        if action:
            return 404, _page("Not found", back)

        def para(t):
            return esc(t).replace("\n", "<br>")

        body = (f'<div class="vtc" style="border:0;padding:0">{self._badge(c)}<div>'
                + (f"<b>{esc(c['motto'])}</b><br>" if c["motto"] else "") + f"{self._chips(c)}</div></div>"
                f'<h2>About</h2><p class="wrap">{para(c["about"])}</p>')
        a = c["asks"]
        asks = ([f"At least {a['age']} years old"] if a["age"] else []) + \
            ([f"{a['km_week']:,} km ({a['km_week'] / 1.609344:,.0f} mi) a week"] if a["km_week"] else []) + \
            (["A microphone"] if a["mic"] else []) + ([f"DLC: {esc(a['dlc'])}"] if a["dlc"] else []) + \
            ([f"Trial period: {esc(a['trial'])}"] if a["trial"] else [])
        if asks or a["text"]:
            body += "<h2>What we ask for</h2>" + ("<ul>" + "".join(f"<li>{x}</li>" for x in asks) + "</ul>" if asks else "") + \
                (f'<p class="wrap">{para(a["text"])}</p>' if a["text"] else "")
        if c["offers"]:
            body += '<h2>What we offer</h2><ul class="wrap">' + "".join(f"<li>{esc(x)}</li>" for x in c["offers"]) + "</ul>"
        m = ((c["stats"] or {}).get("month") or {}).get("all")
        t = ((c["stats"] or {}).get("all") or {}).get("all")
        body += "<h2>Activity</h2>"
        if m and t:
            cells = ((f"{m[2]:,}", "deliveries this month"), (f"{m[0]:,} km", "this month"), (f"{t[2]:,}", "deliveries in all"),
                     (f"{t[0]:,} km", "in all"), (f"{t[4]:.0f} / 100", "average driving score"))
            body += f'<div class="grid nums"><div><b>{c["drivers"]:,}</b>drivers</div>' + \
                "".join(f"<div><b>{n}</b>{x}</div>" for n, x in cells) + "</div><p><small>Reported by the company.</small></p>"
        else:
            body += f'<p>{c["drivers"]:,} driver{"" if c["drivers"] == 1 else "s"}. <small>Reported by the company.</small></p>'
        names = {"discord": "Discord", "website": "Website", "truckersmp": "TruckersMP"}
        links = [f'<a href="{esc(u)}" target="_blank" rel="nofollow noopener noreferrer">{names[k]}</a>' for k, u in c["links"].items() if u]
        if links:
            body += "<h2>Links</h2><p>" + " &middot; ".join(links) + "<br><small>These go to other websites, chosen by the company.</small></p>"
        body += (f'<p><a class="btn" href="/apply/{code}?via=finder">Apply to {esc(c["company"])}</a></p>' if c["open"]
                 else "<p><b>Not taking applications right now.</b></p>")
        reasons = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in VTC_REASONS.items())
        body += f"""{back}<details><summary>Report this company</summary><form method="post" action="/vtc/{code}/report">
<label>What is wrong?<select name="reason" required><option value="">Choose...</option>{reasons}</select></label>
<label>Anything to add?<textarea name="note" maxlength="300" rows="3"></textarea></label>
<div class="hp" aria-hidden="true"><label>Leave this box empty<input name="{HONEYPOT}" tabindex="-1" autocomplete="off"
 data-lpignore="true" data-1p-ignore data-form-type="other"></label></div><button>Send report</button></form></details>"""
        return 200, _page(c["company"], body, esc(c["company"])).replace("<main>", '<main class="wide">', 1)

    def apply_page(self, method, path, raw, ip, query=""):
        """(status, html) for the public application form."""
        code = path[len("/apply/"):].strip("/")
        page = self.store.recruit_page(code)
        if not page:
            return 404, _page("Not found", "<p>This application link isn't valid. Ask the company for its current link.</p>")
        name = page["company"] or "this trucking company"          # plain text (titles escape it themselves)
        company = html.escape(name)
        if method == "POST":
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), max_num_fields=20).items()}
            # honeypot: people never see this box. Its name and label must not look like anything a browser
            # autofills (it used to be "website", which autofill filled in - real applications were dropped)
            if form.get(HONEYPOT):
                return 200, _page("Thank you", f"<p>Your application to <b>{company}</b> was sent.</p>")
            if not self.apply_rate_ok(ip):
                return 429, _page("Slow down", "<p>Too many applications from your connection. Try again in an hour.</p>")
            try:
                self.store.add_application(code, form)
            except LicenceError as e:
                return e.status, _page("Not sent", f"<p>{html.escape(e.msg)}</p><p><a href=\"\">Back</a></p>")
            return 200, _page("Thank you", f"<p>Your application to <b>{company}</b> was sent. The company will "
                                           "contact you using the details you gave.</p>"
                              + ('<p><a href="/vtcs">Back to the VTC Finder</a></p>' if form.get("via") == "finder" else ""))
        if not page["open"]:
            return 200, _page(name, f"<p><b>{company}</b> isn't taking applications right now.</p>", company)
        intro = html.escape(page["intro"] or "").replace("\n", "<br>")
        via = "finder" if urllib.parse.parse_qs(query or "", max_num_fields=10).get("via") == ["finder"] else "link"
        extra = ""
        for item in page["questions"]:
            fid, text = vtc_question_id(item["q"]), html.escape(item["q"])
            extra += f"<label>{text}" + (
                f'<select name="{fid}"><option value="">Choose...</option><option value="yes">Yes</option>'
                '<option value="no">No</option></select></label>' if item["type"] == "yesno"
                else f'<input name="{fid}" maxlength="300"></label>')
        return 200, _page(f"Apply to {name}", f"""
<p>{intro or f"Want to drive for <b>{company}</b>? Tell us about yourself."}</p>
<form method="post" autocomplete="on">
  <label>Your name / driver name *<input name="name" maxlength="80" required></label>
  <label>How can we contact you? (Discord, email) *<input name="contact" maxlength="120" required></label>
  <div class="two"><label>Age *<input name="age" type="number" min="{MIN_AGE}" max="99" required></label>
  <label>Country<input name="country" maxlength="60"></label></div>
  <label>Game<select name="games"><option value="ats">American Truck Simulator</option>
    <option value="ets2">Euro Truck Simulator 2</option><option value="both">Both</option></select></label>
  <label>Experience<textarea name="experience" maxlength="400" rows="3" placeholder="Hours driven, other VTCs..."></textarea></label>
  <label>Message<textarea name="message" maxlength="1500" rows="4"></textarea></label>
  {extra}<input type="hidden" name="via" value="{via}">
  <div class="hp" aria-hidden="true"><label>Leave this box empty<input name="{HONEYPOT}" tabindex="-1" autocomplete="off"
    data-lpignore="true" data-1p-ignore data-form-type="other"></label></div>
  <button>Send application</button>
  <p class="small">Your details go only to {company}. They are deleted from this server as soon as the company
  has received them.</p>
</form>""", company)  # nosec B608  # HTML page, not SQL; company name is html-escaped

    BOARD_LABELS = {"km": "Distance", "revenue": "Revenue", "jobs": "Deliveries", "mass": "Tonnage",
                    "score": "Avg. score", "xp": "XP earned", "realism": "Realism"}

    def board_page(self, query):
        """(status, html) for the public global leaderboard. Plain links only - no scripts, no forms."""
        q = urllib.parse.parse_qs(query or "", max_num_fields=5)
        metric = q.get("metric", ["km"])[0]
        metric = metric if metric in self.BOARD_LABELS else "km"
        period = q.get("period", ["month"])[0]
        period = period if period in Store.LB_PERIODS else "month"
        game = q.get("game", ["all"])[0]
        game = game if game in Store.LB_GAMES else "all"
        rows, drivers = self.store.board_rows(metric, period, game)
        mi = Store.LB_METRICS.index(metric)
        esc = html.escape

        def fmt(v, cur=""):
            if metric in ("km", "realism"):
                return f"{v[mi]:,.0f} km"
            if metric == "revenue":
                return f"{esc(cur)}{v[mi]:,.0f}"
            if metric == "mass":
                return f"{v[mi]:,.1f} t"
            if metric == "score":
                return f"{v[mi]:.1f}"
            return f"{v[mi]:,.0f}"

        def seg(name, opts, cur):
            links = []
            for val, label in opts:
                args = {"metric": metric, "period": period, "game": game, name: val}
                style = ' style="background:#c8102e;color:#fff"' if val == cur else ""
                links.append(f'<a href="?{esc(urllib.parse.urlencode(args))}"{style}>{esc(label)}</a>')
            return '<p class="seg">' + "".join(links) + "</p>"

        body = (seg("metric", list(self.BOARD_LABELS.items()), metric)
                + seg("period", [("week", "Week"), ("month", "Month"), ("year", "Year"), ("all", "All time")], period)
                + seg("game", [("all", "Both games"), ("ats", "ATS"), ("ets2", "ETS2")], game))
        trs = "".join(f"<tr><td>{i}</td><td><b>{esc(r['company'])}</b></td><td class=n>{r['v'][7]:,}</td>"
                      f"<td class=n>{r['v'][2]:,}</td><td class=n><b>{fmt(r['v'], r['currency'])}</b></td></tr>"
                      for i, r in enumerate(rows, 1))
        body += ("<h2>Companies</h2><table><tr><th>#</th><th>Company</th><th class=n>Drivers</th><th class=n>Jobs</th>"
                 f"<th class=n>{esc(self.BOARD_LABELS[metric])}</th></tr>"
                 + (trs or '<tr><td colspan="5"><small>No deliveries in this period yet.</small></td></tr>') + "</table>")
        if drivers:
            trs = "".join(f"<tr><td>{i}</td><td><b>{esc(d['name'])}</b><br><small>{esc(d['company'])}</small></td>"
                          f"<td class=n>{d['v'][2]:,}</td><td class=n><b>{fmt(d['v'], d.get('currency', ''))}</b></td></tr>"
                          for i, d in enumerate(drivers[:25], 1))
            body += ("<h2>Drivers</h2><table><tr><th>#</th><th>Driver</th><th class=n>Jobs</th>"
                     f"<th class=n>{esc(self.BOARD_LABELS[metric])}</th></tr>{trs}</table>"
                     "<p><small>Only companies that chose to share their drivers' names are listed here.</small></p>")
        body += ("<p><small>Every Fleet Manager Pro company that has joined, updated every 10 minutes. Revenue is "
                 "in each company's own in-game money.</small></p>")
        return 200, _page("Global Leaderboard", body).replace("<main>", '<main class="wide">', 1)

    def handle(self, method, path, body, headers, ip, peer=None):
        if not self.rate_ok(ip):
            raise LicenceError("rate_limited", "Too many requests, try again in a minute.", 429)
        if path == "/v1/health":
            return {"ok": True, "product": PRODUCT, "products": list(PRODUCTS), "public_key": self.public}
        if method != "POST" and not path.startswith("/v1/admin/list") and path != "/v1/admin/vtc":
            raise LicenceError("not_found", "Unknown endpoint", 404)
        if path in ("/v1/activate", "/v1/check", "/v1/deactivate") and body.get("product") != PRODUCT:
            raise LicenceError("bad_product", "This licence server only licenses Fleet Manager Pro.")
        if path in ("/v1/activate", "/v1/check"):
            fn = self.store.activate if path == "/v1/activate" else self.store.check
            p, act = fn(body.get("key"), body.get("machine"), ip, body.get("product"))
            build = str(body.get("build") or "").strip().upper()
            build = build if BUILD_RX.match(build) else None
            self.store.note_build(act["id"], build)
            return {"ok": True, "lease": self.lease(p, act, build)}
        if path == "/v1/deactivate":
            self.store.deactivate(body.get("key"), body.get("machine"), body.get("product"))
            return {"ok": True}
        if path == "/v1/recruit":
            if body.get("product") != "Fleet Manager Pro":
                raise LicenceError("bad_product", "Recruitment is a Fleet Manager Pro feature.")
            code, pending = self.store.recruit_sync(body.get("key"), body.get("machine"), body.get("product"),
                                                    body.get("company"), body.get("intro"), bool(body.get("open", True)),
                                                    body.get("ack") if isinstance(body.get("ack"), list) else [], ip,
                                                    body.get("profile") if isinstance(body.get("profile"), dict) else None)
            return {"ok": True, "code": code, "apply_url": f"{self._base()}/apply/{code}", "applications": pending,
                    "profile_url": f"{self._base()}/vtc/{code}", "finder_url": f"{self._base()}/vtcs",
                    "profile": self.store.vtc_status(str(body.get("key") or "").strip().upper())}
        if path == "/v1/leaderboard":
            if body.get("product") != "Fleet Manager Pro":
                raise LicenceError("bad_product", "The global leaderboard is a Fleet Manager Pro feature.")
            me = self.store.leaderboard_sync(body.get("key"), body.get("machine"), body.get("product"),
                                             body.get("join") is True, body.get("company"), body.get("currency"),
                                             body.get("stats"), body.get("drivers"), ip)
            companies, drivers = self.store.leaderboard()
            return {"ok": True, "you": me, "companies": companies, "drivers": drivers,
                    "page_url": f"{self.cfg['apply_base_url'].rstrip('/')}/leaderboard"}
        if path == "/v1/update":
            if body.get("product") != PRODUCT:
                raise LicenceError("bad_product", "This licence server only serves Fleet Manager Pro updates.")
            build = str(body.get("build") or "").strip().upper()
            build = build if BUILD_RX.match(build) else ""
            return self.update_manifest(build, str(body.get("version") or ""), str(body.get("flavor") or "exe"))
        if path.startswith("/v1/admin/"):
            if peer is not None and not admin_peer_ok(peer):
                # Straight from the internet (no HTTPS): the admin token must never travel that way.
                raise LicenceError("admin_local_only", "The admin functions only work from the licence server's own "
                                   "PC or home network (or over HTTPS through Caddy).", 403)
            auth = headers.get("Authorization", "")
            if not secrets.compare_digest(auth, f"Bearer {self.cfg['admin_token']}"):
                time.sleep(0.5)
                raise LicenceError("unauthorized", "Admin token required", 401)
            if path == "/v1/admin/purchase":
                return {"ok": True, "key": self.store.new_purchase(body.get("name"), body.get("email", ""),
                                                                   body.get("product"))}
            if path == "/v1/admin/release":
                self.store.release(body.get("key"), body.get("machine"))
                return {"ok": True}
            if path == "/v1/admin/reset":
                freed = self.store.reset_uses(body.get("key"))
                return {"ok": True, "freed": freed}
            if path == "/v1/admin/revoke":
                self.store.revoke((body.get("key") or "").strip().upper(), bool(body.get("revoked", True)))
                return {"ok": True}
            if path == "/v1/admin/whose":
                w = self.store.whose(self.master, body.get("tag"))
                return {"ok": True, "owner": w}
            if path == "/v1/admin/list":
                rows = self.store.listing()
                for p in rows:
                    p["build_tag"] = build_tag(self.master, p["key"])
                    for a in p["pcs"]:
                        if a.get("build") and a["build"] != p["build_tag"]:
                            o = self.store.whose(self.master, a["build"])
                            a["build_owner"] = o if o == "STOCK" else (f"{o['key']} ({o['name']})" if o else "an unknown copy")
                return {"ok": True, "purchases": rows}
            if path == "/v1/admin/vtc":
                return {"ok": True, "listings": self.store.vtc_admin_list(), "url": f"{self._base()}/vtcs"}
            if path == "/v1/admin/vtc_hide":
                self.store.vtc_hide(body.get("key"), body.get("hidden") is not False)
                return {"ok": True}
            if path == "/v1/admin/vtc_clear":
                return {"ok": True, "cleared": self.store.vtc_clear_reports(body.get("key"))}
            if path == "/v1/admin/delete":
                if body.get("all_revoked") is True:
                    return {"ok": True, "deleted": self.store.delete_revoked()}
                return {"ok": True, "deleted": [self.store.delete_purchase(body.get("key"))]}
        raise LicenceError("not_found", "Unknown endpoint", 404)

    def serve(self, host="0.0.0.0"):  # nosec B104  # listens on the network on purpose so other PCs can connect; admin is LAN-only + token
        srv = self
        self.setup_logging()

        class H(http.server.BaseHTTPRequestHandler):
            server_version = "FleetManagerLicence/2"
            timeout = 20   # close connections that send nothing (phone browsers open spare ones)

            def log_message(self, fmt, *args):
                pass

            def _reply(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _go(self, method):
                ip = client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))
                path = self.path.split("?")[0]
                try:
                    try:
                        n = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        n = -1
                    if n < 0:
                        raise LicenceError("bad_request", "Bad Content-Length")
                    if n > (64_000 if path == "/v1/recruit" else 10_000):     # a company profile in any alphabet fits
                        raise LicenceError("bad_request", "Request too large", 413)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    except ValueError:
                        raise LicenceError("bad_request", "Invalid JSON")
                    if not isinstance(body, dict):
                        raise LicenceError("bad_request", "Invalid JSON")
                    result = srv.handle(method, path, body, self.headers, ip, peer=self.client_address[0])
                    self._reply(200, result)
                    if path != "/v1/health":
                        srv.log.info("%s %s -> 200  ip=%s%s", method, path, ip, srv._req_detail(path, body, result))
                except LicenceError as e:
                    self._reply(e.status, {"ok": False, "code": e.code, "error": e.msg})
                    srv.log.warning("%s %s -> %s %s: %s  ip=%s", method, path, e.status, e.code, e.msg, ip)
                except Exception as e:  # never leak a traceback to clients
                    srv.log.error("%s %s -> 500  ip=%s: %r", method, path, ip, e)
                    self._reply(500, {"ok": False, "code": "server_error", "error": "Licence server error"})

            def _apply(self, method):
                ip = client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))
                status, page = 429, _page("Slow down", "<p>Too many requests. Try again in a minute.</p>")
                try:
                    if srv.rate_ok(ip):
                        raw = b""
                        if method == "POST":
                            n = int(self.headers.get("Content-Length") or 0)
                            if n < 0 or n > 10_000:
                                raise ValueError("size")
                            raw = self.rfile.read(n)
                        url = urllib.parse.urlsplit(self.path)
                        if url.path in ("/vtcs", "/vtcs/"):
                            status, page = srv.finder_page(url.query)
                        elif url.path.startswith("/vtc/"):
                            status, page = srv.vtc_profile_page(method, url.path, raw, ip)
                        else:
                            status, page = srv.apply_page(method, url.path, raw, ip, url.query)
                except ValueError:
                    status, page = 413, _page("Too long", "<p>That is too long to send.</p>")
                except Exception as e:  # never leak a traceback
                    srv.log.error("apply-form error: %r", e)
                    status, page = 500, _page("Error", "<p>Something went wrong. Try again later.</p>")
                data = page.encode("utf-8")
                self.send_response(status)
                for k, v in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                             ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                                         "frame-ancestors 'none'; base-uri 'none'"),
                             ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                             ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _board(self):
                ip = client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))
                status, page = 429, _page("Slow down", "<p>Too many requests. Try again in a minute.</p>")
                try:
                    if srv.rate_ok(ip):
                        status, page = srv.board_page(urllib.parse.urlsplit(self.path).query)
                except Exception as e:  # never leak a traceback
                    srv.log.error("leaderboard page error: %r", e)
                    status, page = 500, _page("Error", "<p>Something went wrong. Try again later.</p>")
                data = page.encode("utf-8")
                self.send_response(status)
                for k, v in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                             ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
                             ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                             ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _download(self):
                ip = client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))
                if not srv.rate_ok(ip, limit=30):
                    return self._reply(429, {"ok": False, "code": "rate_limited", "error": "Too many requests."})
                path = None
                try:
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    path = srv.update_file(q.get("build", [""])[0], q.get("name", [""])[0])
                except Exception:
                    path = None
                if not path:
                    srv.log.warning("update download refused (not found)  ip=%s", ip)
                    return self._reply(404, {"ok": False, "code": "not_found", "error": "No such update."})
                try:
                    size = os.path.getsize(path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(path))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with open(path, "rb") as fh:
                        shutil.copyfileobj(fh, self.wfile, 1 << 20)
                    srv.log.info("update download: %s (%d bytes)  ip=%s", os.path.basename(path), size, ip)
                except (ConnectionError, OSError, ssl.SSLError):
                    pass

            def do_GET(self):
                if self.path.startswith("/apply/"):
                    return self._apply("GET")
                if self.path.split("?")[0] == "/v1/update/file":
                    return self._download()
                if self.path.split("?")[0] in ("/leaderboard", "/leaderboard/"):
                    return self._board()
                if self.path.split("?")[0] in ("/vtcs", "/vtcs/") or self.path.startswith("/vtc/"):
                    return self._apply("GET")
                self._go("GET")

            def do_POST(self):
                if self.path.startswith("/apply/"):
                    return self._apply("POST")
                if self.path.startswith("/vtc/"):
                    return self._apply("POST")
                self._go("POST")

        class Quiet(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def finish_request(self, request, client_address):
                # One port, two languages: a TLS handshake starts with byte 0x16, plain HTTP with a letter.
                # Encrypted for new buyer copies; plain HTTP still answers copies sold earlier.
                if srv.tls_ctx is not None:
                    request.settimeout(20)
                    if request.recv(1, socket.MSG_PEEK) == b"\x16":
                        request = srv.tls_ctx.wrap_socket(request, server_side=True)
                self.RequestHandlerClass(request, client_address, self)

            def handle_error(self, request, client_address):
                # A client hanging up or going silent is normal on the internet (phones, flaky Wi-Fi) - not an error.
                exc = sys.exc_info()[1]
                if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError)):
                    return
                srv.log.error("request from %s failed: %r", client_address[0], exc)

        httpd = Quiet((host, self.port), H)
        self.log.info("Fleet Manager licence server on %s:%s (public key %s...)", host, self.port, self.public[:16])
        if self.tls_ctx:
            self.log.info("Encryption: ON - HTTPS and HTTP on one port (certificate %s...)", self.tls_pin[:16])
        else:
            self.log.info("Encryption: off (no tls_cert.pem / tls_key.pem in this folder)")
        self.log.info("Purchases: %s. Logs in logs/. Ctrl+C to stop.", len(self.store.listing()))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fleet Manager licence server")
    ap.add_argument("--folder", default=HERE)
    ap.add_argument("--port", type=int)
    ap.add_argument("--host", default="0.0.0.0")  # nosec B104  # listens on the network on purpose so other PCs can connect; admin is LAN-only + token
    a = ap.parse_args()
    if not os.path.exists(os.path.join(a.folder, "host_key.json")):
        sys.exit("No host_key.json yet. Run License Manager -> Set up first.")
    Server(a.folder, a.port).serve(a.host)
