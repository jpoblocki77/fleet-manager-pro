"""Fleet Manager Pro - in-app updater (notify, then one-click install).

How it works
* On start-up, and once a day after that, the app asks the licence server (over the same TLS-pinned channel
  licensing uses) whether a newer build exists for THIS copy's hidden build tag.
* The server answers with an Ed25519-signed manifest: the new version, the package's SHA-256 and its filename.
  Nothing is trusted unless the manifest is signed by the very key that signs licences (verified with the
  public key baked into this copy) - so a broken or hostile server can't push a tampered build.
* The buyer sees an "Update available" banner. When they click *Update now*, the app downloads the package
  over the pinned channel, checks it against the signed SHA-256, unpacks it beside the program and - on an
  installed buyer copy (both the single-program .exe and the no-exe build) - hands off to a tiny helper that
  waits for this program to close, swaps the new files in (keeping the data folder untouched) and relaunches.
  The server offers each copy the package built for its flavour, so a no-exe copy never gets an .exe package.

By default this is *notify + one click*: it never replaces the app behind the buyer's back. An Owner or
Manager can turn on "Install updates automatically" (setting auto_update); then a verified update installs by
itself, but only while nobody is connected to the game, so the app never restarts in the middle of a delivery.
"""
import hashlib
import logging
import os
import shutil
import subprocess  # nosec B404  # only launches our own update helper
import sys
import tempfile
import threading
import time
import zipfile

from . import licensing

log = logging.getLogger("fmpro.update")

EXE_NAME = "Fleet Manager Pro.exe"
LAUNCHER_BAT = "Start Fleet Manager Pro.bat"     # how the no-exe copy is started
CHECK_EVERY = 24 * 3600          # seconds between background checks
FIRST_CHECK_DELAY = 25           # let the app finish starting and the licence settle first
BUSY_RETRY = 600                 # auto-install: while someone is driving, look again every 10 minutes

_lock = threading.RLock()
_manifest = None                 # the verified manifest we're offering, if any
_started = False
STATE = {"available": False, "version": None, "notes": "", "size": 0,
         "status": "idle", "error": None, "checked": None, "staged": None}


# ---------------------------------------------------------------------------------------------- helpers
def _vtuple(s):
    out = []
    for part in str(s or "").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def current_version():
    from . import VERSION
    return VERSION


def _frozen():
    return ("__compiled__" in globals()             # Nuitka build (our .exe) - Nuitka never sets sys.frozen
            or bool(getattr(sys, "frozen", False))  # PyInstaller etc.
            or os.path.basename(sys.executable).lower() == EXE_NAME.lower())


def _install_dir():
    if "__compiled__" in globals():                     # Nuitka: sys.executable is a phantom python.exe (short path)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ...\fmpro -> the .exe's folder
    return os.path.dirname(os.path.abspath(sys.executable))


def _noexe_root():
    """For the no-exe copy, the install root (the folder with Start Fleet Manager Pro.bat and app\\run.pyc):
    the parent of the bundled runtime\\ that python.exe runs from. None when that layout isn't present."""
    exe = os.path.abspath(sys.executable)
    if os.path.basename(exe).lower() != "python.exe":
        return None
    runtime = os.path.dirname(exe)                      # ...\runtime
    if os.path.basename(runtime).lower() != "runtime":
        return None
    return os.path.dirname(runtime)                     # ...\ (install root)


def _flavor():
    """Which packaged buyer copy this is, and may replace itself: 'exe' (one Fleet Manager Pro.exe),
    'noexe' (python runtime + app\\run.pyc), or None for a source / developer copy that must not self-apply."""
    if _frozen():
        return "exe"
    root = _noexe_root()
    if root and os.path.isfile(os.path.join(root, LAUNCHER_BAT)) and os.path.isfile(os.path.join(root, "app", "run.pyc")):
        return "noexe"
    return None


def _install_root(flavor):
    """The folder the new files are copied over."""
    return _noexe_root() if flavor == "noexe" else _install_dir()


def _data_dir():
    from . import ROOT
    return os.path.join(ROOT, "data")


def state():
    """A thread-safe snapshot of the updater's state for the UI."""
    with _lock:
        return dict(STATE)


# ---------------------------------------------------------------------------------------------- checking
def _manifest_ok(m):
    """None when the manifest is genuine and for this exact copy, else why not."""
    if not isinstance(m, dict) or "sig" not in m:
        return "The update information wasn't a signed manifest."
    try:
        genuine = licensing.ed25519_verify(licensing.public_key(), licensing._canonical(m), bytes.fromhex(m["sig"]))
    except (ValueError, TypeError):
        genuine = False
    if not genuine:
        return "The update isn't signed by the official licence server, so it was ignored."
    if m.get("product") != licensing.PRODUCT:
        return "The update is for a different product."
    if (m.get("build") or "") != (licensing.BUILD_TAG or ""):
        return "The update is for a different copy of Fleet Manager Pro."
    if not str(m.get("sha256") or "") or not str(m.get("file") or ""):
        return "The update manifest is incomplete."
    return None


def check():
    """Ask the server for a newer, signed build. Returns a snapshot of STATE. Never raises."""
    global _manifest
    cur = current_version()
    body = {"product": licensing.PRODUCT, "version": cur, "machine": licensing.machine_id(),
            "flavor": _flavor() or "exe"}
    if licensing.BUILD_TAG:
        body["build"] = licensing.BUILD_TAG
    try:
        d = licensing._post("/v1/update", body, timeout=10)
    except licensing.ServerRefused:
        with _lock:
            STATE.update(checked=time.time())
        return state()
    except ConnectionError:
        with _lock:
            STATE.update(checked=time.time())
        return state()
    except Exception:  # nosec B110  # a check must never crash the app
        with _lock:
            STATE.update(checked=time.time())
        return state()

    if not isinstance(d, dict) or not d.get("update"):
        with _lock:
            _manifest = None
            if STATE["status"] != "staged":
                STATE.update(available=False, version=None, notes="", size=0, status="idle")
            STATE.update(error=None, checked=time.time())
        return state()

    m = d.get("manifest") or {}
    why = _manifest_ok(m)
    if why is None and _vtuple(m.get("version")) <= _vtuple(cur):
        why, m = None, None            # genuine, but not actually newer
    with _lock:
        if m is None:
            _manifest = None
            if STATE["status"] != "staged":
                STATE.update(available=False, status="idle")
            STATE.update(error=None, checked=time.time())
        elif why:
            _manifest = None
            STATE.update(available=False, status="error", error=why, checked=time.time())
        else:
            _manifest = m
            STATE.update(available=True, version=m["version"], notes=str(m.get("notes") or "")[:2000],
                         size=int(m.get("size") or 0), status="available", error=None, checked=time.time())
    if why:
        log.warning("update check rejected a manifest: %s", why)
    elif m is not None:
        log.info("update available: %s (installed %s)", m["version"], cur)
    return state()


# ---------------------------------------------------------------------------------------------- installing
def _safe_extract(zf, dest):
    """Unzip without letting any entry escape dest (zip-slip safe)."""
    dest_abs = os.path.abspath(dest)
    os.makedirs(dest_abs, exist_ok=True)
    for info in zf.infolist():
        if info.filename.endswith("/"):
            continue
        target = os.path.abspath(os.path.join(dest_abs, info.filename))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise ValueError("unsafe path in update package")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _find_app_root(staged, flavor):
    """The folder inside the unpacked package that holds the program: Fleet Manager Pro.exe for an exe copy,
    Start Fleet Manager Pro.bat for a no-exe copy, or either when flavor is None (a dev copy). Returns None if
    the package doesn't match - which stops a no-exe copy ever applying an .exe package, and vice versa."""
    markers = [LAUNCHER_BAT] if flavor == "noexe" else ([EXE_NAME] if flavor == "exe" else [EXE_NAME, LAUNCHER_BAT])
    if any(os.path.isfile(os.path.join(staged, mk)) for mk in markers):
        return staged
    for cur, _dirs, files in os.walk(staged):
        if any(mk in files for mk in markers):
            return cur
    return None


def install_now():
    """Download + verify + unpack the offered update, then (compiled copy) relaunch into it.
    Returns None when a restart has been scheduled, or a message string to show the user."""
    with _lock:
        m = dict(_manifest) if _manifest else None
        # one install at a time: a second click, or the automatic update at the same moment, must not delete the
        # files the first one is about to swap in
        going = STATE["status"] == "downloading" or (STATE["status"] == "staged" and _flavor() is not None)
        if m and not going:
            STATE.update(status="downloading", error=None)
    if going:
        return "The update is already being installed. Fleet Manager Pro will restart by itself in a moment."
    if not m:
        return "No update is ready. Check for updates first."
    build = licensing.BUILD_TAG or ""
    import urllib.parse
    path = "/v1/update/file?" + urllib.parse.urlencode({"build": build, "name": m["file"]})
    log.info("installing update %s: downloading %s (%d bytes)", m["version"], m["file"], int(m.get("size") or 0))
    try:
        data = licensing.fetch(path)
    except Exception as e:  # nosec B110  # report, don't crash
        with _lock:
            STATE.update(status="error", error="Download failed.")
        log.error("update download failed: %s", e)
        return f"Couldn't download the update: {e}"

    if hashlib.sha256(data).hexdigest() != str(m.get("sha256")).lower():
        with _lock:
            STATE.update(status="error", error="The download didn't match its signed checksum.")
        log.error("update refused: checksum mismatch for %s (possible tampering)", m["version"])
        return "The downloaded update was corrupt (checksum mismatch). Nothing was changed."

    ver_dir = os.path.join(_data_dir(), "updates", str(m["version"]))
    staged = os.path.join(ver_dir, "staged")
    try:
        if os.path.isdir(ver_dir):
            shutil.rmtree(ver_dir, ignore_errors=True)
        os.makedirs(ver_dir, exist_ok=True)
        zpath = os.path.join(ver_dir, "package.zip")
        with open(zpath, "wb") as fh:
            fh.write(data)
        with zipfile.ZipFile(zpath) as zf:
            _safe_extract(zf, staged)
    except Exception as e:
        with _lock:
            STATE.update(status="error", error="Couldn't unpack the update.")
        return f"Couldn't unpack the update: {e}"

    flavor = _flavor()
    root = _find_app_root(staged, flavor)
    if root is None:
        with _lock:
            STATE.update(status="error", error="The package didn't contain the program.")
        return "The update package didn't match this copy of Fleet Manager Pro. Nothing was changed."

    with _lock:
        STATE.update(status="staged", staged=root, error=None)
    log.info("update %s downloaded and verified, staged at %s", m["version"], root)

    if flavor is None:
        log.info("update %s staged; source copy won't self-apply", m["version"])
        return ("The update was downloaded to data\\updates, but a source / developer copy can't replace "
                "itself automatically - only installed buyer copies do. Nothing was changed.")

    # Give the browser ~2s to receive the redirect, then swap the files and relaunch.
    log.info("update %s (%s copy): closing to swap in the new files and relaunch", m["version"], flavor)
    threading.Thread(target=_apply_after, args=(root, ver_dir, flavor, 2.0), daemon=True).start()
    return None


def _same_exe_pids(exe):
    """Other running processes started from this very .exe (the in-game overlay window runs as one). Windows only."""
    import ctypes
    from ctypes import wintypes
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    pids = (wintypes.DWORD * 4096)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(got)):
        return []
    want, me, out = os.path.normcase(os.path.abspath(exe)), os.getpid(), []
    for pid in pids[:got.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid == me:
            continue
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) and os.path.normcase(buf.value) == want:
                out.append(pid)
        finally:
            k32.CloseHandle(h)
    return out


def _close_other_copies(flavor):
    """Close the in-game overlay (and any other process of this same .exe) so the swap can overwrite the program.
    A running overlay keeps Fleet Manager Pro.exe locked: the copy then fails and the app relaunches on the old
    version (seen on 1.5.9 -> 1.5.10). The overlay reopens with the app when it's set to autostart."""
    try:
        from . import ingame
        ingame.stop()
    except Exception:  # noqa: BLE001
        log.exception("couldn't close the in-game overlay before updating")
    if os.name != "nt" or flavor != "exe":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        for pid in _same_exe_pids(os.path.join(_install_root(flavor), EXE_NAME)):
            h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)   # PROCESS_TERMINATE | SYNCHRONIZE
            if h:
                k32.TerminateProcess(h, 0)
                k32.WaitForSingleObject(h, 5000)
                k32.CloseHandle(h)
                log.info("update: closed another copy of the program (pid %s) so it can be replaced", pid)
    except Exception:  # noqa: BLE001
        log.exception("couldn't close other copies of the program before updating")


def _apply_after(app_root, ver_dir, flavor, delay):
    time.sleep(delay)
    try:
        _close_other_copies(flavor)
        _launch_swapper(app_root, ver_dir, flavor)
    except Exception:
        log.exception("couldn't start the update helper")
    finally:
        _hard_exit()                    # drop waitress immediately so the files are free to overwrite


def _hard_exit():
    """End this process now. TerminateProcess first: unlike os._exit it doesn't run DLL shutdown, which can hang
    while the web server's threads are still alive (seen on a buyer's .exe, which stayed open after Update now)."""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.TerminateProcess(k32.GetCurrentProcess(), 0)
        except Exception:  # nosec B110  # fall through to os._exit
            pass
    os._exit(0)


def _launch_swapper(app_root, ver_dir, flavor):
    """Write and launch a detached .cmd that waits for us to exit, copies the new files over the install
    (keeping data\\), relaunches the app and deletes itself."""
    install = _install_root(flavor)
    relaunch = os.path.join(install, LAUNCHER_BAT if flavor == "noexe" else EXE_NAME)
    pid = os.getpid()
    data = os.path.join(install, "data")
    helper = os.path.join(tempfile.gettempdir(), f"fmp-update-{pid}.cmd")
    script = (
        "@echo off\r\n"
        "setlocal\r\n"
        f'title Updating Fleet Manager Pro\r\n'
        ":waitloop\r\n"
        f'tasklist /fi "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"         # 1s pause; 'timeout' refuses to run without console input
        "  goto waitloop\r\n"
        ")\r\n"
        f'robocopy "{app_root}" "{install}" /E /IS /IT /XD "{data}" /R:3 /W:2 /NFL /NDL /NJH /NJS /NP >nul\r\n'
        f'start "" "{relaunch}"\r\n'
        f'rmdir /s /q "{ver_dir}" >nul 2>&1\r\n'
        'del "%~f0" >nul 2>&1\r\n'
    )
    with open(helper, "w", encoding="ascii", errors="replace", newline="") as fh:
        fh.write(script)
    # Its own hidden console, in its own process group, so it outlives us. NOT DETACHED_PROCESS: that leaves cmd
    # with no console at all (CREATE_NO_WINDOW is ignored alongside it), and then tasklist|find never returns and
    # the swap never happens - proven with a Nuitka-compiled probe.
    flags = 0x08000000 | 0x00000200     # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", helper], creationflags=flags, close_fds=True,  # nosec B603 B607
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=tempfile.gettempdir())


# ---------------------------------------------------------------------------------------------- background
def start():
    """Begin daily background update checks (once per process)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="updater").start()


def auto_enabled():
    from . import db
    return db.setting("auto_update", "0") == "1"


def busy():
    """Why an automatic install must wait (someone connected to the game), or None when it's safe now."""
    from .telemetry.service import HUB
    live = HUB.all_live()
    if live:
        return f"{len(live)} driver{'' if len(live) == 1 else 's'} connected to the game"
    return None


def auto_install():
    """Install the offered update now if automatic updates are on and nobody is driving.
    Returns 'installing', 'off', 'none', 'busy: ...', 'source copy' or an error message."""
    if not auto_enabled():
        return "off"
    with _lock:
        ready = bool(_manifest) and STATE["status"] == "available"
    if not ready:
        return "none"
    if _flavor() is None:
        return "source copy"            # a developer copy never replaces itself
    why = busy()
    if why:
        log.info("automatic update %s waiting: %s", STATE["version"], why)
        return "busy: " + why
    log.info("automatic update: installing %s", STATE["version"])
    msg = install_now()
    return "installing" if msg is None else msg


def _loop():
    time.sleep(FIRST_CHECK_DELAY)
    last_check = 0
    while True:
        try:
            if time.time() - last_check >= CHECK_EVERY:
                check()
                last_check = time.time()
            res = auto_install()
        except Exception:  # nosec B110  # background thread must never die
            res = "error"
        # while an update is waiting for drivers to stop, look again soon; otherwise until the next daily check
        time.sleep(BUSY_RETRY if str(res).startswith("busy") else max(60, CHECK_EVERY - (time.time() - last_check)))
