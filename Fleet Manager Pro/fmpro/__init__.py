"""Fleet Manager Pro — virtual trucking company manager for ATS / ETS2."""
import datetime as _dt
import os
import secrets
import sys
import time

from flask import Flask, request, g
from flask.sessions import SecureCookieSessionInterface


class _Sessions(SecureCookieSessionInterface):
    """Marks the session cookie Secure whenever the request arrived over HTTPS (LAN mode)."""

    def get_cookie_secure(self, app):
        return request.is_secure

APP_NAME = "Fleet Manager Pro"
COPYRIGHT = "© 2026 Jeff Poblocki. All rights reserved."
# FROZEN: running as the buyer's Fleet Manager Pro.exe. Our .exe is built with Nuitka, which never sets sys.frozen
# and points sys.executable at a phantom python.exe (8.3 short path) in the .exe's folder - it marks compiled
# modules with __compiled__ instead. PyInstaller builds set sys.frozen.
_NUITKA = "__compiled__" in globals()
FROZEN = (_NUITKA or bool(getattr(sys, "frozen", False))
          or os.path.basename(sys.executable).lower() == "fleet manager pro.exe")
# ROOT: where the user's files live (data folder).  RES: where the program's own files are.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Nuitka: the .exe's folder (long path)
if FROZEN and not _NUITKA:
    RES = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
    ROOT = os.path.dirname(sys.executable)
else:
    RES = ROOT = _HERE
# how a driver starts the remote agent with this copy: the .exe, the no-exe buyer copy's .bat, or the source
if FROZEN:
    AGENT_CMD = '"Fleet Manager Pro.exe" agent'
elif not os.path.isfile(os.path.join(RES, "run.py")) and os.path.isfile(os.path.join(os.path.dirname(RES), "Driver Agent.bat")):
    AGENT_CMD = '"Driver Agent.bat"'
else:
    AGENT_CMD = "python run.py agent"
BUILT_VERSION = "1.3"          # used when the VERSION file is missing (e.g. lost when a copy was re-zipped)
try:
    with open(os.path.join(RES, "VERSION"), encoding="utf-8") as _f:
        VERSION = _f.read().strip() or BUILT_VERSION
except OSError:
    VERSION = BUILT_VERSION


class _PagesLoader:
    """Jinja loader for a buyer copy: the few pages needed before activation are plain; every other page is
    decrypted with the key the licence server put in this PC's signed licence (no valid licence, no pages)."""

    def __new__(cls, plain):
        from jinja2 import BaseLoader, TemplateNotFound
        from . import licensing

        class Loader(BaseLoader):
            def get_source(self, environment, name):
                if name in plain:
                    return plain[name], None, lambda: True
                blob = licensing.SEALED_PAGES.get(name)
                key = licensing.LICENCE.page_key() if licensing.LICENCE and blob else None
                if not key:
                    raise TemplateNotFound(name)
                return licensing.unseal(key, bytes.fromhex(blob)).decode("utf-8"), None, lambda: True

            def list_templates(self):
                return sorted(set(plain) | set(licensing.SEALED_PAGES))

        return Loader()


def create_app(data_dir=None, start_telemetry=True, check_licence=True):
    from . import db, auth, pages, api, licensing, compliance_views
    from .telemetry.service import HUB

    data_dir = data_dir or os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    from . import logs, tracelog
    logs.setup(data_dir)
    tracelog.setup(data_dir)                # logs/debug.log: everything, for troubleshooting
    db.init(os.path.join(data_dir, "fleet.db"))
    # The build tag must be known before the licence is loaded or checked: a check sent without it gets a lease
    # with no key for this copy's sealed pages, and the copy shows "not activated" until the next check.
    emb = licensing.load_build()            # buyer copies: pages compiled in, most of them sealed to the licence
    licensing.LICENCE = licensing.Licence(os.path.join(data_dir, "licence.json"))
    if check_licence:                       # the real program (the tests make many throwaway apps)
        tracelog.system_report(data_dir)
        tracelog.start_heartbeat()
        from . import insights
        insights.start_auto_backup()           # a daily copy of the company database in data/backups
    if check_licence:
        licensing.LICENCE.start()
        from . import recruit
        recruit.start()
        from . import global_board
        global_board.start()
        from . import updater
        updater.start()

    app = Flask(__name__)
    from . import howto
    howto.install(app)                      # the "How to use this page" panel at the top of each page
    if emb is not None:
        app.jinja_loader = _PagesLoader(getattr(emb, "TEMPLATES", {}))
    app.session_interface = _Sessions()
    key_file = os.path.join(data_dir, "secret.key")
    if not os.path.exists(key_file):
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(key_file, encoding="utf-8") as f:
        app.secret_key = f.read().strip()
    app.config.update(SESSION_COOKIE_NAME="fmp_session", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                      DATA_DIR=data_dir)

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(compliance_views.bp)
    from . import ops, leasing_views, trailer_leasing_views, cleanup, insights
    app.register_blueprint(ops.bp)
    app.register_blueprint(insights.bp)
    from . import tablet
    app.register_blueprint(tablet.bp)
    from . import fluids_views
    app.register_blueprint(fluids_views.bp)
    app.register_blueprint(cleanup.bp)
    app.register_blueprint(leasing_views.bp)
    app.register_blueprint(trailer_leasing_views.bp)
    auth.install(app)
    _filters(app)
    _logging_hooks(app)

    if start_telemetry:
        HUB.start_local()
    return app


def _logging_hooks(app):
    """Log every web request (except static files and the high-frequency telemetry feed) and any unhandled error."""
    from . import logs, tracelog

    @app.before_request
    def _log_start():
        g._log_t0 = time.time()

    @app.after_request
    def _log_access(resp):
        try:
            ep = request.endpoint or ""
            dt = (time.time() - getattr(g, "_log_t0", time.time())) * 1000
            u = None
            if ep not in ("static", "api.agent_frame"):
                from .auth import user as _user
                u = _user()
                logs.get("access").info("%s %s -> %s  %.0fms  user=%s ip=%s", request.method, request.path,
                                        resp.status_code, dt, (u["display_name"] if u else "-"), request.remote_addr)
            tracelog.request_line(request, resp, u, dt)
        except Exception:  # nosec B110  # logging must never break a response
            pass
        return resp

    @app.teardown_request
    def _log_error(exc):
        if exc is not None:
            try:
                logs.get("error").error("unhandled error on %s %s", request.method, request.path, exc_info=exc)
            except Exception:  # nosec B110  # logging an error must never raise another
                pass


def _filters(app):
    from . import db, economy

    from .auth import current_units

    def units():
        return current_units()

    @app.template_filter("money")
    def money(v, decimals=0):
        v = float(v or 0)
        cur = db.setting("currency", "$")
        s = f"{abs(v):,.{decimals}f}"
        return f"-{cur}{s}" if v < 0 else f"{cur}{s}"

    @app.template_filter("dist")
    def dist(km, decimals=0):
        km = float(km or 0)
        if units() == "imperial":
            return f"{km * 0.621371:,.{decimals}f} mi"
        return f"{km:,.{decimals}f} km"

    @app.template_filter("speed")
    def speed(kmh):
        kmh = float(kmh or 0)
        return f"{kmh * 0.621371:.0f} mph" if units() == "imperial" else f"{kmh:.0f} km/h"

    @app.template_filter("mass")
    def mass(kg):
        kg = float(kg or 0)
        return f"{kg * 2.20462:,.0f} lb" if units() == "imperial" else f"{kg / 1000:,.1f} t"

    def ach_value(a, v):
        """An achievement number in the viewer's units: distance (mi / km), weight (lb / t), money or a count."""
        u = a.get("unit")
        if u == "dist":
            return dist(v)
        if u == "mass":
            return mass(v)
        if u == "money":
            return money(v)
        return f"{int(v or 0):,}"

    @app.template_filter("ach_desc")
    def ach_desc(a):
        return a["description"].replace("{need}", ach_value(a, a["need"]))

    @app.template_filter("ach_progress")
    def ach_progress(a):
        return f"{ach_value(a, a['current'])} / {ach_value(a, a['need'])}"

    @app.template_filter("liquid")
    def liquid(litres, decimals=0):
        litres = float(litres or 0)
        if units() == "imperial":
            return f"{litres * 0.264172:,.{decimals}f} gal"
        return f"{litres:,.{decimals}f} L"

    @app.template_filter("dt")
    def dt(ts, fmt="%b %d, %Y %H:%M"):
        if not ts:
            return "—"
        return _dt.datetime.fromtimestamp(float(ts)).strftime(fmt)

    @app.template_filter("ago")
    def ago(ts):
        if not ts:
            return "never"
        s = time.time() - float(ts)
        if s < 0:
            s = -s
            for n, u in ((86400, "day"), (3600, "hour"), (60, "minute")):
                if s >= n:
                    k = int(s // n)
                    return f"in {k} {u}{'s' if k > 1 else ''}"
            return "in moments"
        for n, u in ((86400 * 30, "month"), (86400, "day"), (3600, "hour"), (60, "minute")):
            if s >= n:
                k = int(s // n)
                return f"{k} {u}{'s' if k > 1 else ''} ago"
        return "just now"

    @app.template_filter("pct")
    def pct(v, d=0):
        return f"{float(v or 0) * 100:.{d}f}%"

    @app.template_filter("gametime")
    def gametime(minutes):
        if minutes is None:
            return "—"
        m = int(minutes)
        dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][(m // 1440) % 7]
        return f"{dow} {(m % 1440) // 60:02d}:{m % 60:02d}"

    @app.template_filter("dur")
    def dur(sec):
        sec = int(sec or 0)
        h, m = sec // 3600, (sec % 3600) // 60
        return f"{h}h {m:02d}m" if h else f"{m}m {sec % 60:02d}s"

    def _theme_css():
        from . import theme
        return theme.from_settings(db.all_settings())   # plain text, still auto-escaped: only #rrggbb values ever get in

    @app.context_processor
    def globals_():
        from . import tlsfront, updater
        return {"APP_NAME": APP_NAME, "COPYRIGHT": COPYRIGHT, "STARTING_CAPITAL": db.STARTING_CAPITAL, "VERSION": VERSION, "progress": economy.progress,
                "lan_pin": tlsfront.fingerprint(app.config["DATA_DIR"]),
                "S": db.all_settings(), "now": time.time(), "AGENT_CMD": AGENT_CMD,
                "UPDATE": updater.state(),
                "dvir_price": economy.dvir_repair_price, "THEME_CSS": _theme_css()}
