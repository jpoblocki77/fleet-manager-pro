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
  POST /v1/update      {product, build, version}   newest build for this copy -> Ed25519-signed manifest
                       (version, sha256, file) or {update: false}. Packages live in  updates/<BUILD TAG>/ .
  GET  /v1/update/file?build=<tag>&name=<zip>   download a package (its SHA-256 is pinned by the signed manifest)
Public pages (no key):
  GET/POST /apply/<code>   the company's application form (rate-limited, honeypot, length limits)
Admin API (Authorization: Bearer <admin_token>), used by License Manager:
  POST /v1/admin/purchase {name, email}   GET /v1/admin/list
  POST /v1/admin/release  {key, machine}  POST /v1/admin/revoke {key, revoked: true|false}
  POST /v1/admin/reset    {key}           free every PC on a purchase (buyer couldn't deactivate)
  POST /v1/admin/whose    {tag}           which purchase a copy's hidden build tag (FMPB-...) belongs to
  POST /v1/admin/delete   {key} | {all_revoked: true}   delete a purchase (active or revoked) for good, or every
                          revoked one; its PCs lock at their next check. A copy goes to deleted_purchases.jsonl first.

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
                CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, code TEXT NOT NULL, created TEXT,
                  data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_apps_code ON applications(code);
            """)
            for table, col, decl in (("purchases", "revoked", "TEXT"), ("activations", "last_seen", "TEXT"),
                                     ("activations", "last_ip", "TEXT"), ("purchases", "resets", "INTEGER DEFAULT 0"),
                                     ("purchases", "last_reset", "TEXT"), ("purchases", "product", "TEXT"),
                                     ("activations", "build", "TEXT")):
                cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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

    def recruit_sync(self, key, machine, product, company, intro, is_open, ack, ip=""):
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
            self.db.commit()
            pending = [dict(id=r[0], created=r[1], **json.loads(r[2])) for r in self.db.execute(
                "SELECT id, created, data FROM applications WHERE code=? ORDER BY id LIMIT 100", (code,))]
            return code, pending

    def recruit_page(self, code):
        if not re.fullmatch(r"[a-z2-9]{8}", code or ""):
            return None
        r = self.db.execute("SELECT r.company, r.intro, r.open, p.revoked FROM recruit r JOIN purchases p ON p.key=r.key "
                            "WHERE r.code=?", (code,)).fetchone()
        return dict(r) if r and not r["revoked"] else None

    def add_application(self, code, form):
        page = self.recruit_page(code)
        if not page or not page["open"]:
            raise LicenceError("closed", "This company isn't taking applications right now.", 404)
        data = {k: str(form.get(k, "")).strip()[:n] for k, n in self.APP_FIELDS.items()}
        if not data["name"] or not data["contact"]:
            raise LicenceError("missing", "Please fill in your name and how to contact you.")
        data["games"] = data["games"] if data["games"] in ("ats", "ets2", "both") else ""
        data["age"] = data["age"] if data["age"].isdigit() else ""
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
            self.db.execute("DELETE FROM purchases WHERE key=?", (key,))
            self.db.commit()
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
        if path == "/v1/recruit":
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

    def latest_update(self, build):
        """(version, filename, folder) of the newest package for a copy, or None."""
        folder = self._update_folder(build)
        if not folder:
            return None
        best = None
        try:
            names = os.listdir(folder)
        except OSError:
            return None
        for name in names:
            if not name.lower().endswith(".zip"):
                continue
            m = UPDATE_ZIP_RX.search(name)
            if not m:
                continue
            ver = m.group(1)
            if best is None or vtuple(ver) > vtuple(best[0]):
                best = (ver, name, folder)
        return best

    def update_manifest(self, build, client_version):
        """Signed offer of the newest build for this copy (or {'update': False} when it's current)."""
        best = self.latest_update(build)
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

    def apply_page(self, method, path, raw, ip):
        """(status, html) for the public application form."""
        code = path[len("/apply/"):].strip("/")
        page = self.store.recruit_page(code)
        if not page:
            return 404, _page("Not found", "<p>This application link isn't valid. Ask the company for its current link.</p>")
        name = page["company"] or "this trucking company"          # plain text (titles escape it themselves)
        company = html.escape(name)
        if method == "POST":
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), max_num_fields=20).items()}
            if form.get("website"):                                 # honeypot: people never see this field
                return 200, _page("Thank you", f"<p>Your application to <b>{company}</b> was sent.</p>")
            if not self.apply_rate_ok(ip):
                return 429, _page("Slow down", "<p>Too many applications from your connection. Try again in an hour.</p>")
            try:
                self.store.add_application(code, form)
            except LicenceError as e:
                return e.status, _page("Not sent", f"<p>{html.escape(e.msg)}</p><p><a href=\"\">Back</a></p>")
            return 200, _page("Thank you", f"<p>Your application to <b>{company}</b> was sent. The company will "
                                           "contact you using the details you gave.</p>")
        if not page["open"]:
            return 200, _page(name, f"<p><b>{company}</b> isn't taking applications right now.</p>", company)
        intro = html.escape(page["intro"] or "").replace("\n", "<br>")
        return 200, _page(f"Apply to {name}", f"""
<p>{intro or f"Want to drive for <b>{company}</b>? Tell us about yourself."}</p>
<form method="post" autocomplete="on">
  <label>Your name / driver name *<input name="name" maxlength="80" required></label>
  <label>How can we contact you? (Discord, email) *<input name="contact" maxlength="120" required></label>
  <div class="two"><label>Age<input name="age" type="number" min="10" max="99"></label>
  <label>Country<input name="country" maxlength="60"></label></div>
  <label>Game<select name="games"><option value="ats">American Truck Simulator</option>
    <option value="ets2">Euro Truck Simulator 2</option><option value="both">Both</option></select></label>
  <label>Experience<textarea name="experience" maxlength="400" rows="3" placeholder="Hours driven, other VTCs..."></textarea></label>
  <label>Message<textarea name="message" maxlength="1500" rows="4"></textarea></label>
  <label class="hp">Website<input name="website" tabindex="-1" autocomplete="off"></label>
  <button>Send application</button>
  <p class="small">Your details go only to {company}. They are deleted from this server as soon as the company
  has received them.</p>
</form>""", company)  # nosec B608  # HTML page, not SQL; company name is html-escaped

    def handle(self, method, path, body, headers, ip, peer=None):
        if not self.rate_ok(ip):
            raise LicenceError("rate_limited", "Too many requests, try again in a minute.", 429)
        if path == "/v1/health":
            return {"ok": True, "product": PRODUCT, "products": list(PRODUCTS), "public_key": self.public}
        if method != "POST" and not path.startswith("/v1/admin/list"):
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
                                                    body.get("ack") if isinstance(body.get("ack"), list) else [], ip)
            return {"ok": True, "code": code, "apply_url": f"{self.cfg['apply_base_url'].rstrip('/')}/apply/{code}",
                    "applications": pending}
        if path == "/v1/update":
            if body.get("product") != PRODUCT:
                raise LicenceError("bad_product", "This licence server only serves Fleet Manager Pro updates.")
            build = str(body.get("build") or "").strip().upper()
            build = build if BUILD_RX.match(build) else ""
            return self.update_manifest(build, str(body.get("version") or ""))
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
                    if n > 10_000:
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
                        status, page = srv.apply_page(method, self.path.split("?")[0], raw, ip)
                except ValueError:
                    status, page = 413, _page("Too long", "<p>That application is too long.</p>")
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
                self._go("GET")

            def do_POST(self):
                if self.path.startswith("/apply/"):
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
