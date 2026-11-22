"""Smoke test: start the real app the way the .bat files do, open its pages, then stop it.

Uses a throw-away data folder (no licence, no drivers), so it never touches ./data and never syncs
recruiting or telemetry with the licence server.

    python tests/smoke_test.py          normal start (this PC only)
    python tests/smoke_test.py --lan    also the LAN start (HTTPS front on the network; Windows may ask
                                        to allow Python through the firewall the first time)
"""
import os
import re
import shutil
import socket
import ssl
import subprocess  # nosec B404  # starts this project's own run.py
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def opener(base):
    ctx = ssl.create_default_context()
    if base.startswith("https"):        # the LAN front uses a self-made certificate
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))


def get(op, url):
    try:
        r = op.open(url, timeout=10)
        return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def wait_up(op, base, proc, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            get(op, base + "/")
            return True
        except OSError:
            time.sleep(0.5)
    return False


def run(label, extra):
    print(label)
    data = tempfile.mkdtemp(prefix="fmp_smoke_")
    port = free_port()
    scheme = "https" if "--lan" in extra else "http"
    base = f"{scheme}://127.0.0.1:{port}"
    out = open(os.path.join(data, "_stdout.txt"), "w+", encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if "lan_access" in label:                # what the Let other PCs in switch leaves behind
        sys.path.insert(0, ROOT)
        from fmpro import db
        db.init(os.path.join(data, "fleet.db"))
        db.set_setting("lan_access", "1")
        db.conn().close()
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port), "--data", data, *extra],  # nosec B603
                            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=env)
    op = opener(base)
    try:
        up = wait_up(op, base, proc)
        check(up, f"the app starts and answers on {base}")
        if not up:
            out.seek(0)
            print(out.read()[-3000:])
            return
        out.seek(0)
        banner = out.read()
        check("Fleet Manager Pro" in banner and "Open http://127.0.0.1" in banner, "the start-up banner shows")

        st, hdr, body = get(op, base + "/")
        check(st in (200, 302, 303), f"home page answers ({st})")
        seen, todo = set(), ["/"]
        if st in (302, 303) and hdr.get("Location"):
            todo.append(re.sub(r"^https?://[^/]+", "", hdr["Location"]))
        for p in ("/login", "/setup", "/register", "/activate", "/static/css/app.css"):
            todo.append(p)
        while todo and len(seen) < 25:
            p = todo.pop(0)
            if p in seen or not p.startswith("/") or p.startswith("//"):
                continue
            seen.add(p)
            st, hdr, body = get(op, base + p)
            check(st < 500, f"GET {p} -> {st}")
            if st in (302, 303) and hdr.get("Location"):
                todo.append(re.sub(r"^https?://[^/]+", "", hdr["Location"]))
            if st == 200 and "text/html" in (hdr.get("Content-Type") or ""):
                for href in re.findall(r'(?:href|src)="(/[^"#?]*)', body):
                    if not href.startswith(("/logout", "/account/leave", "/settings/dissolve")):
                        todo.append(href)
        check(len(seen) >= 3, f"opened {len(seen)} pages")
        st, h, _b = get(op, base + "/this-page-does-not-exist")
        check(st == 404 or (st in (302, 303) and "/activate" in (h.get("Location") or "")),
              f"an unknown page is a 404 (or, with no licence yet, sends you to activation) ({st})")
        check(proc.poll() is None, "the app is still running after the page tour")
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        out.seek(0)
        text = out.read()
        out.close()
        errs = ""
        for name in ("error.log", "fleetmanager.log"):
            f = os.path.join(data, "logs", name)
            if os.path.exists(f):
                errs += open(f, encoding="utf-8", errors="replace").read()
        bad = [ln for ln in (text + errs).splitlines() if "Traceback" in ln or "unhandled error" in ln]
        check(not bad, "no tracebacks or unhandled errors in the output and logs" + (f": {bad[:3]}" if bad else ""))
        shutil.rmtree(data, ignore_errors=True)


def main():
    run("Normal start (this PC only)", [])
    if "--lan" in sys.argv[1:]:
        run("LAN start (HTTPS front)", ["--lan"])
        run("Normal start with other PCs let in (lan_access)", [])
    print("\nALL PASSED" if not FAILS else f"\nFAILED: {len(FAILS)}\n  " + "\n  ".join(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
