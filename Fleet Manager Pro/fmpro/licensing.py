"""Fleet Manager Pro licensing — online activation against the seller's licence server.

How it works
* This PC has a Machine ID (a hash of Windows' MachineGuid; nothing personal is sent).
* Activate: purchase key (FMP-XXXX-XXXX-XXXX-XXXX) + Machine ID go to the licence server, which allows at most
  2 PCs per purchase and answers with a lease signed by the seller's Ed25519 key.
* The app re-checks at start-up and every 6 hours. A revoked purchase or released PC locks at the next check.
  If the server can't be reached the last lease keeps working until it expires (7-day offline grace).
* Only leases signed by PUBLIC_KEY_HEX and naming product "Fleet Manager Pro" are accepted, so neither a fake
  server nor a key for the original Fleet Manager can unlock Pro. Over https the server certificate must match
  TLS_PIN exactly.

License Manager -> 1) Set up rewrites the three constants below for a buyer copy.
"""
import base64
import datetime as dt
import hashlib
import hmac
import http.client
import json
import logging
import os
import ssl
import sys
import threading
import urllib.parse
import uuid

log = logging.getLogger("fmpro.licence")

PRODUCT = "Fleet Manager Pro"
PUBLIC_KEY_HEX = "49b187237dea8f82b9dc28d042b9c2b76f000380b22080241a30821fbaf83896"
SERVER_URL = "https://fleetmanager.servehttp.com:8750"
# Inside the seller's own home network the public address often can't be reached (routers rarely loop back),
# so the seller's copies also try the server's home-network address. Same server, same pinned certificate.
# Buyer copies are built without it (Build Pro Buyer Copy.py empties it).
LAN_FALLBACK = "https://192.168.1.29:8750"
TLS_PIN = "0c2a8aa6893098773aa0b75dd4074e8fae38ef28be9dd35426094fc7054c480d"
CHECK_EVERY = dt.timedelta(hours=6)
CLOCK_SLACK = dt.timedelta(hours=2)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Only the developer copy (it has the test suite) may be pointed at a throw-away server for testing.
_DEV = not getattr(sys, "frozen", False) and os.path.isfile(os.path.join(_ROOT, "tests", "e2e_test.py"))


def _dev(name, default):
    return (os.environ.get(name) or default) if _DEV else default


def server_url():
    return _dev("FMP_LICENSE_SERVER", SERVER_URL).rstrip("/")


def public_key():
    return bytes.fromhex(_dev("FMP_LICENSE_PUBKEY", PUBLIC_KEY_HEX))


def tls_pin():
    return _dev("FMP_LICENSE_TLS_PIN", TLS_PIN).strip().lower()


# ---------------------------------------------------------------------------------------------- Ed25519 (verify)
# RFC 8032 signature verification in plain Python (no third-party packages needed).
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_D2 = 2 * _D % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = t1 * _D2 * t2 % _P
    D = 2 * z1 * z2 % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(k, pt):
    out = (0, 1, 1, 0)
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def _x_from_y(y, sign):
    if y >= _P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - xx) % _P:
        return None
    if x == 0 and sign:
        return None
    return _P - x if (x & 1) != sign else x


def _decode(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _x_from_y(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _same(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


_BY = 4 * pow(5, _P - 2, _P) % _P
_BASE = (_x_from_y(_BY, 0), _BY, 1, _x_from_y(_BY, 0) * _BY % _P)


def ed25519_verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = _decode(public), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _same(_mul(s, _BASE), _add(R, _mul(h, A)))


# ---------------------------------------------------------------------------------------------- helpers
def machine_id():
    raw = None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            raw = None
    raw = raw or f"node-{uuid.getnode():012x}"
    code = base64.b32encode(hashlib.sha256(f"fleet-manager-machine|{raw}".encode()).digest()).decode()[:16]
    return "FMID-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))


def _canonical(lease):
    return json.dumps({k: v for k, v in lease.items() if k != "sig"}, sort_keys=True, separators=(",", ":")).encode()


def _when(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------------- sealed pages
# A buyer copy carries a hidden build tag, and its pages are encrypted with a key for that tag which only the licence
# server can hand out (inside the signed lease). The developer copy and the seller's own copy have no sealed pages.
BUILD_TAG = PAGES_CHECK = None
SEALED_PAGES = {}
PAGES_LOCKED = ("This copy of Fleet Manager Pro still needs its pages unlocked by the licence server. Connect to the "
                "internet and restart Fleet Manager Pro (if it keeps happening, ask the seller).")


def load_build():
    """Pick up the sealed pages compiled into a buyer copy. Returns the module, or None for an unsealed copy."""
    global BUILD_TAG, PAGES_CHECK, SEALED_PAGES
    try:
        from . import _embedded_templates as emb
    except ImportError:
        BUILD_TAG = PAGES_CHECK = None
        SEALED_PAGES = {}
        return None
    BUILD_TAG, PAGES_CHECK, SEALED_PAGES = getattr(emb, "BUILD", None), getattr(emb, "CHECK", None), getattr(emb, "SEALED", {})
    return emb


def _stream(key, nonce, n):
    enc = hmac.new(key, b"enc", hashlib.sha256).digest()
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(enc + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return bytes(out[:n])


def seal(key, data):
    """Encrypt + authenticate (nonce | ciphertext | HMAC-SHA256)."""
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(key, nonce, len(data))))
    return nonce + ct + hmac.new(key, b"mac" + nonce + ct, hashlib.sha256).digest()


def unseal(key, blob):
    nonce, ct, mac = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(mac, hmac.new(key, b"mac" + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("wrong key or damaged page")
    return bytes(a ^ b for a, b in zip(ct, _stream(key, nonce, len(ct))))


def pages_check(key):
    return hmac.new(key, b"fmp-pages-check", hashlib.sha256).hexdigest()[:32]


def _wrap_pad(machine, tag):
    return hashlib.sha256(f"fmp-unlock|{machine}|{tag}".encode()).digest()


def lease_page_key(lease, machine):
    """The page key from a (verified) lease, or None if it has none for this build."""
    if not BUILD_TAG or not isinstance(lease, dict) or lease.get("build") != BUILD_TAG:
        return None
    try:
        wrapped = bytes.fromhex(lease.get("unlock") or "")
    except ValueError:
        return None
    if len(wrapped) != 32:
        return None
    key = bytes(a ^ b for a, b in zip(wrapped, _wrap_pad(machine, BUILD_TAG)))
    return key if PAGES_CHECK and hmac.compare_digest(pages_check(key), PAGES_CHECK) else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


CLOCK_BACK = ("This PC's clock is earlier than when the licence was last checked. Correct the date and time "
              "(Windows Settings > Time & language) and restart Fleet Manager Pro.")


def lease_problem(lease, machine, key=None):
    """None when the lease is genuine, for Pro, for this PC and still valid - otherwise why not."""
    if not isinstance(lease, dict) or "sig" not in lease:
        return "The licence server's answer wasn't a licence."
    if lease.get("product") != PRODUCT:
        return f"That licence is for {lease.get('product') or 'another product'}, not {PRODUCT}."
    try:
        genuine = ed25519_verify(public_key(), _canonical(lease), bytes.fromhex(lease["sig"]))
    except (ValueError, TypeError):
        genuine = False
    if not genuine:
        return "The licence isn't signed by the official licence server."
    if lease.get("machine") != machine:
        return "That licence belongs to another PC."
    if key and lease.get("purchase") != key:
        return "That licence belongs to a different purchase key."
    try:
        if _when(lease["expires"]) <= _now():
            return "The licence must be checked online again (the 7-day offline period has ended)."
        if _when(lease["issued"]) - CLOCK_SLACK > _now():
            return CLOCK_BACK
    except (KeyError, ValueError):
        return "The licence has no valid dates."
    return None


class ServerRefused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _post(path, body, timeout=8):
    """POST JSON to the licence server (public address first, then the home-network fallback if unreachable)."""
    urls = [server_url()]
    if LAN_FALLBACK and not (_DEV and os.environ.get("FMP_LICENSE_SERVER")) and LAN_FALLBACK.rstrip("/") != urls[0]:
        urls.append(LAN_FALLBACK.rstrip("/"))
    for i, base in enumerate(urls):
        try:
            return _post_to(base, path, body, timeout if i == 0 else 5)
        except ConnectionError:
            if i == len(urls) - 1:
                raise


def _post_to(base, path, body, timeout=8):
    """POST JSON to one licence server address. With a TLS pin, the certificate must match it byte for byte."""
    url = base + path
    u = urllib.parse.urlsplit(url)
    data = json.dumps(body).encode()
    try:
        if u.scheme == "https":
            if tls_pin():
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # trust comes from the pin
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            else:
                ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        try:
            conn.connect()
            if u.scheme == "https" and tls_pin():
                seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
                if seen != tls_pin():
                    raise ConnectionError("The licence server's certificate doesn't match this copy - the connection "
                                          "may be intercepted, so it was refused.")
            conn.request("POST", u.path or "/", body=data,
                         headers={"Content-Type": "application/json", "User-Agent": "FleetManagerPro"})
            r = conn.getresponse()
            raw = r.read()
        finally:
            conn.close()
    except ConnectionError:
        raise
    except (OSError, http.client.HTTPException) as e:
        raise ConnectionError(f"Can't reach the licence server ({server_url()}).") from e
    try:
        reply = json.loads(raw or b"{}")
    except ValueError:
        reply = {}
    if r.status >= 400:
        raise ServerRefused(reply.get("code", f"http_{r.status}"), reply.get("error") or f"Licence server error {r.status}")
    return reply


def fetch(path, timeout=300, max_bytes=500_000_000):
    """GET raw bytes from the licence server over the same pinned channel (public address first, then the
    seller's home-network fallback). Used to download a signed update package."""
    urls = [server_url()]
    if LAN_FALLBACK and not (_DEV and os.environ.get("FMP_LICENSE_SERVER")) and LAN_FALLBACK.rstrip("/") != urls[0]:
        urls.append(LAN_FALLBACK.rstrip("/"))
    for i, base in enumerate(urls):
        try:
            return _get_to(base, path, timeout, max_bytes)
        except ConnectionError:
            if i == len(urls) - 1:
                raise


def _get_to(base, path, timeout, max_bytes):
    """GET one URL with the certificate pinned exactly, returning the response body as bytes."""
    u = urllib.parse.urlsplit(base + path)
    try:
        if u.scheme == "https":
            if tls_pin():
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # trust comes from the pin
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            else:
                ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        try:
            conn.connect()
            if u.scheme == "https" and tls_pin():
                seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
                if seen != tls_pin():
                    raise ConnectionError("The licence server's certificate doesn't match this copy - the connection "
                                          "may be intercepted, so it was refused.")
            q = u.path + (("?" + u.query) if u.query else "")
            conn.request("GET", q or "/", headers={"User-Agent": "FleetManagerPro"})
            r = conn.getresponse()
            if r.status >= 400:
                raise ServerRefused(f"http_{r.status}", f"The update server answered {r.status}.")
            out = bytearray()
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out += chunk
                if len(out) > max_bytes:
                    raise ConnectionError("The update is larger than expected; the download was stopped.")
            return bytes(out)
        finally:
            conn.close()
    except (ConnectionError, ServerRefused):
        raise
    except (OSError, http.client.HTTPException) as e:
        raise ConnectionError(f"Can't reach the update server ({server_url()}).") from e


# ---------------------------------------------------------------------------------------------- licence state
class Licence:
    """This PC's Pro licence, kept in <data>/licence.json."""

    def __init__(self, path):
        self.path = path
        self.machine = machine_id()
        self.lock = threading.RLock()
        self.key = self.lease = self.last_check = self.seen = None
        self.online = None
        self.ok, self.reason = False, "Not activated yet."
        self._thread = None
        self._load()

    # storage
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self.reason = "The saved licence file is damaged. Activate again."
            return
        self.key, self.lease = d.get("key"), d.get("lease")
        self.last_check, self.seen = d.get("last_check"), d.get("seen")
        self._evaluate()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"product": PRODUCT, "key": self.key, "machine": self.machine, "lease": self.lease,
                       "last_check": self.last_check, "seen": self.seen}, f, indent=2)
        os.replace(tmp, self.path)

    def _evaluate(self):
        if not self.key:
            self.ok, self.reason = False, "Not activated yet."
            return
        if not self.lease:
            self.ok, self.reason = False, "This PC needs to check its licence online."
            return
        why = lease_problem(self.lease, self.machine, self.key)
        now = _now()
        if why is None and self.seen:
            try:
                if now + CLOCK_SLACK < _when(self.seen):
                    why = CLOCK_BACK               # the clock was wound back to stretch the offline period
            except ValueError:
                pass
        if why is None:
            try:
                self.seen = max(now, _when(self.seen)).isoformat(timespec="seconds") if self.seen \
                    else now.isoformat(timespec="seconds")
            except ValueError:
                self.seen = now.isoformat(timespec="seconds")
        if why is None and BUILD_TAG and not lease_page_key(self.lease, self.machine):
            why = PAGES_LOCKED                     # genuine licence, but no key for this copy's pages
        self.ok, self.reason = why is None, why

    def page_key(self):
        """Key for this copy's sealed pages - only while the licence is valid."""
        with self.lock:
            return lease_page_key(self.lease, self.machine) if self.ok else None

    def _body(self):
        b = {"key": self.key, "machine": self.machine, "product": PRODUCT}
        if BUILD_TAG:
            b["build"] = BUILD_TAG
        return b

    def _accept(self, key, lease):
        why = lease_problem(lease, self.machine, key)
        if why:
            raise ServerRefused("untrusted", why)
        self.key, self.lease = key, lease
        self.last_check = _now().isoformat(timespec="seconds")
        self.online = True
        self._evaluate()
        self._save()
        log.info("licence accepted: key=%s slot=%s/%s expires=%s build=%s ok=%s", key, lease.get("slot"),
                 lease.get("slots"), lease.get("expires"), lease.get("build"), self.ok)

    # actions
    def activate(self, key):
        """Returns None on success, else a message for the user."""
        key = (key or "").strip().upper().replace(" ", "")
        if not key:
            return "Type your purchase key."
        if key.startswith("FM-"):
            return "That's a key for the original Fleet Manager. Fleet Manager Pro keys start with FMP-."
        with self.lock:
            log.info("activation requested: key=%s machine=%s", key, self.machine)
            try:
                body = dict(self._body(), key=key)
                d = _post("/v1/activate", body)
                self._accept(key, d.get("lease"))
                return None if self.ok else self.reason
            except ServerRefused as e:
                log.warning("activation refused: %s (%s)", e.message, e.code)
                if e.code == "bad_product" or (e.code == "bad_key" and key.startswith("FMP-")):
                    return ("The licence server hasn't been updated for Fleet Manager Pro yet. "
                            "Ask the seller to update it.")
                return e.message
            except ConnectionError as e:
                self.online = False
                log.warning("activation failed - licence server unreachable: %s", e)
                return f"{e} Check your internet connection and try again."

    def check(self):
        """Renew the lease. A refusal from the server locks at once; network trouble keeps the lease (grace)."""
        with self.lock:
            if not self.key:
                return
            try:
                d = _post("/v1/check", self._body())
                self._accept(self.key, d.get("lease"))
            except ServerRefused as e:
                self.online = True
                if e.code in ("revoked", "not_active", "unknown_key", "wrong_product"):
                    log.warning("licence locked by server: %s (%s)", e.message, e.code)
                    self.lease = None
                    self.last_check = _now().isoformat(timespec="seconds")
                    self._save()
                    self._evaluate()
                    self.reason = e.message
                else:
                    log.info("licence check: server refused (%s), keeping current lease", e.code)
                    self._evaluate()
            except ConnectionError:
                self.online = False
                log.info("licence check: licence server unreachable, using saved lease (offline grace)")
                self._evaluate()
                try:
                    self._save()
                except OSError:
                    pass
                if not self.ok and self.lease and self.reason != CLOCK_BACK:
                    self.reason = ("Can't reach the licence server and the 7-day offline period has ended. "
                                   "Connect to the internet to keep using Fleet Manager Pro.")

    def deactivate(self):
        """Free this PC's slot (moving to another PC). Returns None or a message."""
        with self.lock:
            if not self.key:
                return "This PC isn't activated."
            try:
                _post("/v1/deactivate", self._body())
            except ServerRefused as e:
                if e.code != "not_active":
                    return e.message
            except ConnectionError as e:
                return str(e)
            log.info("deactivated this PC (key=%s)", self.key)
            self.key = self.lease = self.last_check = None
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self._evaluate()
            return None

    def status(self):
        with self.lock:
            self._evaluate()
            out = {"licensed": self.ok, "reason": None if self.ok else self.reason, "machine_id": self.machine,
                   "server": server_url(), "online": self.online, "last_check": self.last_check,
                   "has_key": bool(self.key), "key": self.key}
            if self.ok and self.lease:
                out.update({k: self.lease.get(k) for k in ("name", "email", "slot", "slots", "issued", "expires")})
            return out

    def start(self):
        """Check now and then every 6 hours in the background."""
        if self._thread:
            return

        def loop():
            stop = threading.Event()
            while True:
                self.check()
                stop.wait(CHECK_EVERY.total_seconds())

        self._thread = threading.Thread(target=loop, daemon=True, name="licence")
        self._thread.start()


LICENCE = None   # set by create_app()


def licensed():
    # Demo/developer copies (no sealed build tag) can be unlocked for showing the app off with test data by
    # setting FMP_DEMO=1. Buyer copies are always built with a sealed build tag, so they ignore this entirely.
    if not BUILD_TAG and os.environ.get("FMP_DEMO") == "1":
        return True
    return bool(LICENCE and LICENCE.ok)
