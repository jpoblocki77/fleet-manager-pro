"""Fleet Manager Pro - extensive troubleshooting log (logs/debug.log).

Everything the program does, in as much detail as is safe to write down:

  * a full system report at start-up (Windows, the .exe, folders, disk, memory, database, settings, licence state)
  * every web request: who, the page, the query, the form fields sent (passwords and tokens hidden), uploads,
    the result, its size and how long it took
  * every change to the database (the SQL and its values, secrets hidden) and any slow query
  * the game connection: connect / disconnect, game, pause, engine, job, truck and trailer changes, a snapshot of
    the main values every minute and a full dump of every telemetry value when a job starts or ends
  * a heartbeat every 5 minutes: uptime, memory, threads, database size, who is live
  * crashes: unhandled errors in any thread, Python warnings, and hard crashes (logs/crash.log)
  * messages from the web server and the other libraries the program uses
  * a line when the program shuts down

The file rolls over at 10 MB and keeps 10 old files (about 100 MB at most). Licence keys, passwords, tokens and
cookies are masked before anything is written (see logs.redact), so a buyer can safely send the logs folder.
"""
import atexit
import faulthandler
import logging
import os
import platform
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from . import logs

log = logs.get("trace")
T0 = time.time()
_ON = False
_crash_file = None

# form / JSON / query fields whose values are never written
SECRET_FIELD = re.compile(r"(?i)(pass|pwd|csrf|token|secret|pin|cookie|auth|session|signature|private)")
# database columns whose values are never written
SECRET_COL = re.compile(r"(?i)(password|hash|token|secret|key|pin|signature)")
SLOW_QUERY_MS = 100


def on():
    return _ON


def short(v, n=200):
    """A value made safe and short enough for one log line."""
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    s = str(v)
    return s if len(s) <= n else s[:n] + f"...(+{len(s) - n} chars)"


# ------------------------------------------------------------------------------------------------ setup
def setup(data_dir):
    """Add logs/debug.log (DEBUG level, everything) on top of the normal logs. Call after logs.setup()."""
    global _ON, _crash_file
    if _ON:
        return
    logdir = os.path.join(data_dir, "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
        h = RotatingFileHandler(os.path.join(logdir, "debug.log"), maxBytes=10_000_000, backupCount=10, encoding="utf-8")
    except OSError:
        return                                     # can't write here: run without the debug log
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d  %(levelname)-7s %(name)-18s [%(threadName)s] %(message)s",
                                     "%Y-%m-%d %H:%M:%S"))
    h.addFilter(logs._Redactor())
    h.addFilter(logs._NoAccess())                            # access.log has those; trace.web has more detail
    logging.getLogger("fmpro").setLevel(logging.DEBUG)       # the other files keep their own (INFO / WARNING) levels
    logging.getLogger("fmpro").addHandler(h)
    root = logging.getLogger()                               # web server, libraries, Python warnings
    root.addHandler(h)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    _ON = True

    try:                                                     # hard crashes (access violations etc.) -> crash.log
        _crash_file = open(os.path.join(logdir, "crash.log"), "a", encoding="utf-8")  # noqa: SIM115 - stays open for the process
        _crash_file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} process {os.getpid()} started ===\n")
        _crash_file.flush()
        faulthandler.enable(_crash_file, all_threads=True)
    except Exception:  # nosec B110  # crash logging is a nice-to-have
        pass

    prev_hook = sys.excepthook

    def _excepthook(tp, val, tb):
        log.critical("UNHANDLED %s: %s", tp.__name__, val, exc_info=(tp, val, tb))
        prev_hook(tp, val, tb)
    sys.excepthook = _excepthook

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.critical("UNHANDLED in thread %s: %s: %s", getattr(args.thread, "name", "?"), args.exc_type.__name__,
                     args.exc_value, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _thread_hook
    atexit.register(_shutdown)
    log.info("=" * 100)
    log.info("debug log started (logs/debug.log) - everything the program does is recorded here")


def _shutdown():
    log.info("shutting down after %s (pid %s)", _dur(time.time() - T0), os.getpid())
    try:
        if _crash_file:
            _crash_file.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} process {os.getpid()} ended normally ===\n")
            _crash_file.flush()
    except Exception:  # nosec B110
        pass


def _dur(sec):
    sec = int(sec)
    return f"{sec // 86400}d {sec % 86400 // 3600}h {sec % 3600 // 60}m {sec % 60}s"


# ------------------------------------------------------------------------------------------------ system report
def _mem_status():
    """(total, free) physical memory in bytes on Windows, else (None, None)."""
    if os.name != "nt":
        return None, None
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong),
                    ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    m = MS()
    m.len = ctypes.sizeof(MS)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return m.total, m.avail
    return None, None


def process_memory():
    """This process's working set in bytes (Windows), else None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), ("peak", ctypes.c_size_t),
                        ("ws", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("pagefile", ctypes.c_size_t),
                        ("peakpage", ctypes.c_size_t)]
        p = PMC()
        p.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if fn(k32.GetCurrentProcess(), ctypes.byref(p), p.cb):
            return p.ws
    except Exception:  # nosec B110
        pass
    return None


def _mb(b):
    return "?" if b is None else f"{b / 1048576:,.0f} MB"


def system_report(data_dir):
    """Write everything about this PC and this copy that helps troubleshooting."""
    if not _ON:
        return
    from . import VERSION, FROZEN, ROOT
    r = log.info
    r("---- system report ----")
    r("Fleet Manager Pro %s  packaged(.exe)=%s  nuitka=%s  pid=%s", VERSION, FROZEN,
      "__compiled__" in globals() or getattr(sys.modules.get("fmpro"), "_NUITKA", False), os.getpid())
    r("program: %s", os.path.abspath(sys.argv[0]) if sys.argv else "?")
    r("python executable: %s  version: %s", sys.executable, sys.version.replace("\n", " "))
    r("arguments: %s", " ".join(short(a, 80) for a in sys.argv[1:]) or "(none)")
    r("working folder: %s", os.getcwd())
    r("program folder: %s", ROOT)
    r("data folder: %s", data_dir)
    r("windows: %s  (%s, %s)  machine: %s  processor: %s  cpus: %s", platform.platform(), platform.version(),
      platform.release(), platform.machine(), platform.processor() or "?", os.cpu_count())
    r("computer name: %s", platform.node())
    tot, free = _mem_status()
    r("memory: %s total, %s free; this program uses %s", _mb(tot), _mb(free), _mb(process_memory()))
    try:
        import shutil
        du = shutil.disk_usage(data_dir)
        r("disk (data folder drive): %s total, %s free (%.0f%% free)", _mb(du.total), _mb(du.free), du.free / du.total * 100)
    except Exception as e:
        r("disk: unknown (%s)", e)
    r("time zone: %s  (UTC%+.1f h)  local time: %s", time.tzname, -time.timezone / 3600, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        import locale
        r("language: %s  encoding: %s", locale.getlocale(), locale.getpreferredencoding(False))
    except Exception:  # nosec B110
        pass
    for k in ("USERPROFILE", "LOCALAPPDATA", "TEMP", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS",
              "PYTHONPATH", "FMP_PORT", "FMP_HOST"):
        if os.environ.get(k):
            r("env %s = %s", k, short(os.environ[k], 160))
    try:
        from importlib import metadata

        def ver(n):
            try:
                return metadata.version(n)
            except Exception:
                return getattr(sys.modules.get(n), "__version__", "?")
        mods = {n: ver(n) for n in ("flask", "werkzeug", "waitress", "jinja2", "cryptography", "pynacl")
                if n in sys.modules or n == "pynacl" and "nacl" in sys.modules}
        r("libraries: %s", ", ".join(f"{k} {v}" for k, v in mods.items()))
    except Exception:  # nosec B110
        pass
    _report_files(data_dir)
    _report_db()
    _report_licence()
    r("---- end of system report ----")


def _report_files(data_dir):
    try:
        for name in sorted(os.listdir(data_dir)):
            p = os.path.join(data_dir, name)
            if os.path.isfile(p):
                log.info("data file %-24s %10s bytes  modified %s", name, f"{os.path.getsize(p):,}",
                         time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))))
            else:
                n = sum(len(f) for _, _, f in os.walk(p))
                log.info("data folder %-22s %d files", name + "\\", n)
    except Exception as e:
        log.warning("couldn't list the data folder: %s", e)


def _report_db():
    from . import db
    try:
        log.info("database: %s (%s bytes)", db.DB_PATH, f"{os.path.getsize(db.DB_PATH):,}" if os.path.exists(db.DB_PATH) else "?")
        log.info("database check: %s", db.val("PRAGMA quick_check"))
        tables = [r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = []
        for t in tables:
            db.ident(t)
            counts.append(f"{t}={db.val(f'SELECT COUNT(*) FROM {t}')}")  # nosec B608  # names come from sqlite_master and pass ident()
        log.info("database rows: %s", ", ".join(counts))
        settings = db.all_settings()
        log.info("settings (%d): %s", len(settings), ", ".join(
            f"{k}={'****' if SECRET_COL.search(k) or SECRET_FIELD.search(k) else short(v, 60)}" for k, v in sorted(settings.items())))
        users = db.q("SELECT id, display_name, role, status, game_pref, last_seen FROM users ORDER BY id")
        for u in users:
            log.info("member #%s %s role=%s status=%s games=%s last seen %s", u["id"], u["display_name"], u["role"],
                     u["status"], u["game_pref"],
                     time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_seen"])) if u["last_seen"] else "never")
    except Exception as e:
        log.warning("couldn't read the database for the report: %s", e, exc_info=True)


def _report_licence():
    try:
        from . import licensing
        lic = licensing.LICENCE
        if not lic:
            log.info("licence: not loaded")
            return
        log.info("licence: ok=%s reason=%s key=%s build tag=%s lease=%s", getattr(lic, "ok", None),
                 getattr(lic, "reason", ""), logs.mask_key(getattr(lic, "key", "")), licensing.BUILD_TAG,
                 {k: v for k, v in (getattr(lic, "lease", None) or {}).items() if k in ("slot", "expires", "build", "product")}
                 if isinstance(getattr(lic, "lease", None), dict) else "-")
    except Exception as e:
        log.info("licence: couldn't read (%s)", e)


# ------------------------------------------------------------------------------------------------ heartbeat
def start_heartbeat(every=300):
    if not _ON:
        return

    def run():
        while True:
            time.sleep(every)
            try:
                heartbeat()
            except Exception:
                log.exception("heartbeat failed")
    threading.Thread(target=run, name="debug-heartbeat", daemon=True).start()


def heartbeat():
    from . import db
    from .telemetry.service import HUB
    s = HUB.sampler
    live = HUB.all_live()
    log.info("heartbeat: up %s, memory %s, threads %d (%s), database %s bytes", _dur(time.time() - T0),
             _mb(process_memory()), threading.active_count(), ", ".join(sorted(t.name for t in threading.enumerate())),
             f"{os.path.getsize(db.DB_PATH):,}" if os.path.exists(db.DB_PATH) else "?")
    log.info("heartbeat: telemetry '%s', %s frames read, live drivers: %s", HUB.local_status,
             getattr(s, "frames", 0), ", ".join(f"#{u} via {e['source']}" for u, e in live.items()) or "none")


# ------------------------------------------------------------------------------------------------ web requests
def fields(md):
    """Form / query fields for the log, with secrets hidden and long values cut."""
    out = []
    for k in md.keys():
        vals = md.getlist(k)
        shown = ["****" if SECRET_FIELD.search(k) else short(v, 120) for v in vals]
        out.append(f"{k}={shown[0] if len(shown) == 1 else shown}")
    return ", ".join(out)


_agent_frames = {"n": 0, "t": time.time()}


def request_line(request, resp, user, ms):
    """One detailed line per request (the agent's 4-per-second telemetry posts are counted, not listed)."""
    if not _ON:
        return
    ep = request.endpoint or "?"
    if ep == "static":
        log.debug("static %s -> %s", request.path, resp.status_code)
        return
    if ep == "api.agent_frame":
        _agent_frames["n"] += 1
        if time.time() - _agent_frames["t"] >= 60:
            logs.get("trace.web").debug("driver agent: %d telemetry frames received in the last %.0f s",
                                        _agent_frames["n"], time.time() - _agent_frames["t"])
            _agent_frames.update(n=0, t=time.time())
        return
    w = logs.get("trace.web")
    who = f"{user['display_name']} (#{user['id']}, {user['role']})" if user else "not signed in"
    w.debug("%s %s%s -> %s %s, %s bytes, %.1f ms | %s | ip %s | endpoint %s", request.method, request.path,
            ("?" + short(request.query_string.decode("utf-8", "replace"), 300)) if request.query_string else "",
            resp.status_code, resp.mimetype, resp.calculate_content_length() if not resp.is_streamed else "stream",
            ms, who, request.remote_addr, ep)
    if request.method != "GET":
        if request.form:
            w.debug("    form: %s", fields(request.form))
        if request.files:
            w.debug("    uploads: %s", ", ".join(f"{k}={f.filename!r}" for k, f in request.files.items()))
        if request.is_json:
            js = request.get_json(silent=True)
            if isinstance(js, dict):
                w.debug("    json: %s", ", ".join(f"{k}={'****' if SECRET_FIELD.search(k) else short(v, 80)}"
                                                 for k, v in js.items()))
    if resp.status_code >= 300 and resp.location:
        w.debug("    redirect to %s", resp.location)
    if resp.status_code >= 400:
        w.info("    request failed: %s %s -> %s (user agent %s, from %s)", request.method, request.path, resp.status_code,
               short(request.user_agent.string, 120), request.referrer or "-")
    try:
        from flask import session
        for cat, msg in session.get("_flashes", []) or []:
            w.debug("    message shown (%s): %s", cat, short(msg, 200))
    except Exception:  # nosec B110
        pass


# ------------------------------------------------------------------------------------------------ database
_db = logs.get("trace.db")


SECRET_SETTING = re.compile(r"(?i)(pass|pwd|token|secret|key|webhook|pin|signature)")


def _shown_args(sql, args, cols):
    flat = " ".join(sql.split())
    args = list(args)
    if cols:
        return [("****" if SECRET_COL.search(c) else short(a, 120)) for c, a in zip(cols, args)] +                [short(a, 120) for a in args[len(cols):]]
    if re.search(r"(?i)settings", flat) and args:                 # settings(key, value): hide secret settings
        return [short(a, 120) if i == 0 or not SECRET_SETTING.search(str(args[0])) else "****" for i, a in enumerate(args)]
    head = re.split(r"(?i)WHERE", flat)[0]
    if SECRET_COL.search(head):                                        # a statement that writes a secret column
        return ["****"] * len(args)
    return [short(a, 120) for a in args]


def db_write(sql, args, ms, cols=None, rowid=None):
    """A change to the database. cols = column names of the values (insert/update), to hide secret ones."""
    if not _ON:
        return
    flat = " ".join(sql.split())
    _db.debug("write %.1f ms%s: %s  %s", ms, f" (new id {rowid})" if rowid and flat.upper().startswith("INSERT") else "",
              short(flat, 400), _shown_args(sql, args, cols) or "")


def db_error(sql, args, err, cols=None):
    if _ON:      # IntegrityError is often expected (e.g. a violation already recorded for that period)
        lvl = logging.DEBUG if type(err).__name__ == "IntegrityError" else logging.WARNING
        _db.log(lvl, "database statement failed (%s: %s): %s  %s", type(err).__name__, err,
                    short(" ".join(sql.split()), 400), _shown_args(sql, args, cols))


def db_read(sql, args, ms, rows):
    if _ON and ms >= SLOW_QUERY_MS:
        _db.info("SLOW query %.0f ms, %s rows: %s  %s", ms, rows, short(" ".join(sql.split()), 400),
                 [short(a, 60) for a in args])


# ------------------------------------------------------------------------------------------------ telemetry
_tl = logs.get("trace.telemetry")
WATCH = ("game", "paused", "sdk_active", "engine_enabled", "electric_enabled", "parking_brake", "on_job",
         "truck_id", "license_plate", "truck_brand", "truck_name", "cargo", "source_city", "destination_city", "source_company",
         "destination_company", "job_market", "special_job", "trailers_attached", "cargo_loaded", "cruise_control",
         "game_version_major", "game_version_minor", "plugin_revision")
SNAP = ("game_time_abs", "speed", "cruise_control_speed", "engine_rpm", "gear", "fuel", "fuel_capacity", "adblue",
        "odometer", "x", "z", "navigation_distance", "navigation_time",
        "speed_limit", "cargo_mass", "cargo_damage", "wear_engine", "wear_transmission", "wear_cabin", "wear_chassis",
        "wear_wheels", "job_income", "job_delivery_time_abs", "oil_temperature", "water_temperature",
        "battery_voltage", "air_pressure", "brake_temperature")
FLAGS = ("job_delivered", "job_cancelled", "fined", "tollgate", "ferry", "train", "refuel", "refuel_paid")


class TelemetryTrace:
    """Per driver: logs connection, every change in the watched values, a snapshot each minute and full dumps."""

    def __init__(self):
        self.prev = {}        # uid -> {field: value}
        self.last_snap = {}
        self.connected = {}

    def frame(self, uid, frame, source):
        if not _ON:
            return
        try:
            if frame is None:
                if self.connected.get(uid):
                    _tl.info("driver #%s: telemetry stopped (%s) - game closed, paused at the menu or connection lost",
                             uid, source)
                self.connected[uid] = False
                self.prev.pop(uid, None)
                return
            v = frame.get("values") or {}
            if not self.connected.get(uid):
                self.connected[uid] = True
                _tl.info("driver #%s: telemetry connected via %s%s", uid, source, " (DEMO simulator)" if frame.get("demo") else "")
                self.dump(uid, v, frame.get("trailers"), "first frame after connecting")
            old = self.prev.get(uid, {})
            changes = [f"{k}: {short(old.get(k), 60)} -> {short(v.get(k), 60)}" for k in WATCH
                       if k in v and old and old.get(k) != v.get(k)]
            if changes:
                _tl.info("driver #%s: %s", uid, "; ".join(changes))
            for k in FLAGS if old else ():           # (on the first frame these are left over from before)
                if v.get(k) and not old.get(k):
                    _tl.info("driver #%s: game event %s", uid, k)
            if old and v.get("on_job") and not old.get("on_job"):
                self.dump(uid, v, frame.get("trailers"), "job started")
            if old and (v.get("job_delivered") and not old.get("job_delivered")
                        or v.get("job_cancelled") and not old.get("job_cancelled")):
                self.dump(uid, v, frame.get("trailers"), "job delivered" if v.get("job_delivered") else "job cancelled")
            now = time.time()
            if now - self.last_snap.get(uid, 0) >= 60:
                self.last_snap[uid] = now
                _tl.debug("driver #%s snapshot: %s", uid, ", ".join(f"{k}={short(v[k], 40)}" for k in SNAP if k in v))
            self.prev[uid] = {k: v.get(k) for k in WATCH + FLAGS}
        except Exception:
            _tl.exception("telemetry trace failed")

    @staticmethod
    def dump(uid, v, trailers, why):
        _tl.debug("driver #%s FULL telemetry dump (%s): %d values", uid, why, len(v))
        keys = sorted(v)
        for i in range(0, len(keys), 25):
            _tl.debug("    %s", ", ".join(f"{k}={short(v[k], 50)}" for k in keys[i:i + 25]))
        for n, t in enumerate(trailers or []):
            if isinstance(t, dict):
                tk = sorted(t)
                _tl.debug("    trailer %d: %s", n, ", ".join(f"{k}={short(t[k], 40)}" for k in tk[:60]))

    @staticmethod
    def status(old, new):
        if _ON and old != new:
            _tl.info("game connection status: %s -> %s", old, new)


TELEMETRY = TelemetryTrace()
