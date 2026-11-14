"""HTTPS for LAN mode.

Waitress itself speaks plain HTTP, so in LAN mode it listens only on 127.0.0.1 and this small front door
takes the public port:

* a TLS connection (first byte 0x16) is decrypted here and passed to Waitress;
* plain HTTP from this PC itself is passed through (no certificate warning on localhost);
* plain HTTP from any other PC is answered with a redirect to https:// - nothing is served unencrypted.

The certificate is self-signed (made on first use, kept in the data folder), so other PCs see a one-time
browser warning; remote agents pin its fingerprint instead (`--pin`).
"""
import datetime as dt
import hashlib
import ipaddress
import os
import socket
import ssl
import threading

PEERS = {}          # local port of our connection to Waitress -> (real client address, "http" | "https")
_lock = threading.Lock()


# ------------------------------------------------------------------------------------------ certificate
def _local_ips():
    ips = {"127.0.0.1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:   # the address used to reach the network, even when the host name doesn't resolve to it
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def fingerprint(data_dir):
    """SHA-256 of the LAN certificate (what agents pin), or None when there is none yet."""
    path = os.path.join(data_dir, "tls_cert.pem")
    if not os.path.exists(path):
        return None
    with open(path, encoding="ascii") as f:
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()


def ensure_cert(data_dir):
    """Create data/tls_cert.pem + tls_key.pem once. Needs the 'cryptography' package (ImportError if missing)."""
    cert_path, key_path = os.path.join(data_dir, "tls_cert.pem"), os.path.join(data_dir, "tls_key.pem")
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        host = socket.gethostname()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Fleet Manager Pro ({host})"[:64])])
        alt = [x509.DNSName("localhost"), x509.DNSName(host)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in _local_ips()]
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(alt), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, fingerprint(data_dir)


# ------------------------------------------------------------------------------------------ front door
def _is_loopback(addr):
    try:
        a = ipaddress.ip_address(addr)
        return a.is_loopback or bool(getattr(a, "ipv4_mapped", None) and a.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except (OSError, ssl.SSLError):
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _redirect_to_https(conn, port):
    """Plain HTTP from another PC: never serve it, point the browser at https instead."""
    try:
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = conn.recv(2048)
            if not chunk:
                break
            head += chunk
        lines = head.decode("latin-1", "replace").split("\r\n")
        path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
        host = next((ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("host:")), "")
        host = host.split(":")[0] if host.count(":") == 1 else host
        if not host or not path.startswith("/") or any(c in host + path for c in "\r\n "):
            host, path = host or "localhost", "/"
        body = b"Fleet Manager Pro only answers other PCs over https://"
        conn.sendall(f"HTTP/1.1 308 Permanent Redirect\r\nLocation: https://{host}:{port}{path}\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("latin-1") + body)
    except OSError:
        pass
    finally:
        conn.close()


def _handle(conn, addr, ctx, port, backend_port):
    backend = None
    try:
        conn.settimeout(20)
        first = conn.recv(1, socket.MSG_PEEK)
        if first == b"\x16":
            conn = ctx.wrap_socket(conn, server_side=True)
            scheme = "https"
        elif _is_loopback(addr[0]):
            scheme = "http"
        else:
            return _redirect_to_https(conn, port)
        conn.settimeout(None)
        backend = socket.create_connection(("127.0.0.1", backend_port), timeout=10)
        backend.settimeout(None)
        local = backend.getsockname()[1]
        with _lock:
            PEERS[local] = (addr[0], scheme)
        t = threading.Thread(target=_pipe, args=(backend, conn), daemon=True)
        t.start()
        _pipe(conn, backend)
        t.join(5)
        with _lock:
            PEERS.pop(local, None)
    except (OSError, ssl.SSLError):
        pass
    finally:
        for s in (conn, backend):
            try:
                if s:
                    s.close()
            except OSError:
                pass


def start(host, port, backend_port, cert_path, key_path):
    """Listen on host:port and forward to Waitress on 127.0.0.1:backend_port. Returns the listening socket."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(64)

    def loop():
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            threading.Thread(target=_handle, args=(conn, addr, ctx, port, backend_port), daemon=True).start()

    threading.Thread(target=loop, daemon=True, name="tls-front").start()
    return srv


class Middleware:
    """Gives the app the real client address and scheme for requests that came through the front door."""

    def __init__(self, wsgi_app):
        self.app = wsgi_app

    def __call__(self, environ, start_response):
        try:
            info = PEERS.get(int(environ.get("REMOTE_PORT") or 0)) if _is_loopback(environ.get("REMOTE_ADDR", "")) else None
        except ValueError:
            info = None
        if info:
            environ["REMOTE_ADDR"], environ["wsgi.url_scheme"] = info
        return self.app(environ, start_response)
