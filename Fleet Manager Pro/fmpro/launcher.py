"""Fleet Manager Pro launcher (see run.py for the command-line options)."""
import argparse
import os
import socket
import sys
import threading
import webbrowser


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    ap = argparse.ArgumentParser(description="Fleet Manager Pro")
    ap.add_argument("mode", nargs="?", default="serve", choices=["serve", "agent"])
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--insecure-http", action="store_true", help="with --lan: no HTTPS")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--data", default=None, help="data folder (default: ./data)")
    ap.add_argument("--server")
    ap.add_argument("--token")
    ap.add_argument("--pin", default="", help="agent: SHA-256 fingerprint of the server's certificate")
    ap.add_argument("--build-tag", action="store_true", help=argparse.SUPPRESS)   # seller: whose copy is this?
    a = ap.parse_args()

    if a.build_tag:
        from fmpro import licensing
        licensing.load_build()
        print(licensing.BUILD_TAG or "No build tag (developer or seller copy).")
        return

    if a.mode == "agent":
        if not a.server or not a.token:
            sys.exit("agent mode needs --server and --token (find them on your profile page)")
        from fmpro.telemetry.service import run_agent
        run_agent(a.server, a.token, a.pin)
        return

    # Windows lets two programs listen on one port; then a second copy silently answers instead of the
    # first. Refuse to start when something already answers on this port.
    try:
        socket.create_connection(("127.0.0.1", a.port), timeout=1).close()
        print(f"\n  Something is already running on port {a.port} - most likely Fleet Manager Pro itself.\n"
              f"  Open http://127.0.0.1:{a.port} in your browser, or close the other Fleet Manager Pro window first.\n"
              f"  (To run a second copy on purpose, start it with  --port {a.port + 10})\n")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{a.port}")
        sys.exit(1)
    except OSError:
        pass

    from waitress import serve
    from fmpro import APP_NAME, VERSION, create_app

    app = create_app(a.data)
    url = f"http://127.0.0.1:{a.port}"
    print(f"\n  {APP_NAME} {VERSION}\n  Open {url}")
    kw = dict(threads=12, ident=f"{APP_NAME}/{VERSION}")
    if not a.lan:
        print("  Press Ctrl+C to stop.\n")
        if not a.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        serve(app, host="127.0.0.1", port=a.port, **kw)
        return

    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "THIS-PC"
    if a.insecure_http:
        print(f"  Other PCs: http://{ip}:{a.port}   (NOT encrypted - passwords cross your network in the clear)")
        print("  Press Ctrl+C to stop.\n")
        if not a.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        serve(app, host="0.0.0.0", port=a.port, **kw)  # nosec B104  # listens on the network on purpose so other PCs can connect; admin is LAN-only + token
        return

    from fmpro import tlsfront
    try:
        cert, key, pin = tlsfront.ensure_cert(app.config["DATA_DIR"])
    except ImportError:
        sys.exit("\n  LAN mode encrypts with HTTPS and needs one more package:\n\n      python -m pip install cryptography\n\n"
                 "  (or add --insecure-http to share without encryption on a network you fully trust)")
    backend = _free_port()
    tlsfront.start("0.0.0.0", a.port, backend, cert, key)  # nosec B104  # listens on the network on purpose so other PCs can connect; admin is LAN-only + token
    app.wsgi_app = tlsfront.Middleware(app.wsgi_app)
    print(f"  Other PCs: https://{ip}:{a.port}   (encrypted; browsers show a one-time warning for the self-made certificate)")
    print(f"  Certificate fingerprint (for agents, --pin): {pin}")
    print("  Press Ctrl+C to stop.\n")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    serve(app, host="127.0.0.1", port=backend, **kw)

