"""Fleet Manager Pro's link to the Fleet Manager server (it used to check licences; Fleet Manager Pro is free now).

* Updates: the server offers a package signed with the seller's Ed25519 key; only packages signed by PUBLIC_KEY_HEX
  are installed (see updater.py). Over https the server certificate must match TLS_PIN exactly.
* Online features (Apply page, Global Leaderboard, VTC Finder) talk to the same server with the company's own ID
  (see online_id.py). Nothing is locked: the program works fully offline.
* A built copy carries a build tag (its update channel), compiled into _embedded_templates.py with its pages.
"""
import base64
import hashlib
import http.client
import json
import os
import ssl
import sys
import urllib.parse
import uuid

PRODUCT = "Fleet Manager Pro"
PUBLIC_KEY_HEX = "49b187237dea8f82b9dc28d042b9c2b76f000380b22080241a30821fbaf83896"
SERVER_URL = "https://fleetmanager.servehttp.com:8750"
# The Fleet Manager server is a cloud box reached at SERVER_URL from anywhere, so there is no separate home-network
# address to fall back to. Kept as an empty string (licensing.py still parses; Build Pro Buyer Copy.py empties it
# for buyer copies anyway) so the "also try the LAN address" step is simply skipped.
LAN_FALLBACK = ""
TLS_PIN = "0c2a8aa6893098773aa0b75dd4074e8fae38ef28be9dd35426094fc7054c480d"

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


# ---------------------------------------------------------------------------------------------- build tag
# A built copy carries a build tag (its update channel); the developer copy has none. Its pages are compiled in.
BUILD_TAG = None


def load_build():
    """Pick up the pages and build tag compiled into a built copy. Returns the module, or None for the developer copy."""
    global BUILD_TAG
    try:
        from . import _embedded_templates as emb
    except ImportError:
        BUILD_TAG = None
        return None
    BUILD_TAG = getattr(emb, "BUILD", None)
    return emb


class ServerRefused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _post(path, body, timeout=8):
    """POST JSON to the Fleet Manager server (public address first, then the home-network fallback if unreachable)."""
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
    """POST JSON to one Fleet Manager server address. With a TLS pin, the certificate must match it byte for byte."""
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
                    raise ConnectionError("The Fleet Manager server's certificate doesn't match this copy - the connection "
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
        raise ConnectionError(f"Can't reach the Fleet Manager server ({server_url()}).") from e
    try:
        reply = json.loads(raw or b"{}")
    except ValueError:
        reply = {}
    if r.status >= 400:
        raise ServerRefused(reply.get("code", f"http_{r.status}"), reply.get("error") or f"Fleet Manager server error {r.status}")
    return reply


def fetch(path, timeout=300, max_bytes=500_000_000):
    """GET raw bytes from the Fleet Manager server over the same pinned channel (public address first, then the
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
                    raise ConnectionError("The Fleet Manager server's certificate doesn't match this copy - the connection "
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
