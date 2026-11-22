"""In-game overlay: a small always-on-top panel drawn over ATS / ETS2.

It runs as its own process ("Fleet Manager Pro.exe overlay" / "run.py overlay") so the web server never waits on a
window. The window is see-through and click-through (mouse clicks go to the game), never takes the keyboard focus,
and a hotkey shows or hides it. Twice a second it asks the app on this PC for /api/overlay with the local driver's
key: the rows to draw are already formatted there, plus logbook alerts and Hours of Service warnings to pop up.

The game has to run in a window or borderless window for anything to show on top of it; true exclusive fullscreen
hides every other window, this one included.
"""
import json
import logging
import os
import subprocess  # nosec B404  # only ever starts this program itself
import sys
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("fmpro.ingame")

_proc = [None]
_lock = threading.Lock()
MUTEX = "FleetManagerPro.InGameOverlay"


# ------------------------------------------------------------------------------------------------ starting & stopping
def _command(port):
    """How to start this program in overlay mode: the .exe itself, or run.py from a source copy."""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.argv[0])
        if not exe.lower().endswith(".exe"):
            exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fleet Manager Pro.exe")
        return [exe, "overlay", "--port", str(port)]
    run_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    return [sys.executable, run_py, "overlay", "--port", str(port)]


def running():
    with _lock:
        p = _proc[0]
        return bool(p and p.poll() is None)


def local_port(app):
    """The plain-http port this PC's own copy answers on at 127.0.0.1: the normal port, or in LAN mode the
    private back-end port behind the HTTPS front door (LAN mode used to leave the overlay with no port at all)."""
    return app.config.get("OVERLAY_PORT") or app.config.get("PORT")


def start(port, key):
    """Open the overlay window (does nothing if it is already open). Returns None or a reason it can't."""
    if os.name != "nt":
        return "The in-game overlay works on Windows only."
    if not port or not key:
        return "The in-game overlay can't find Fleet Manager Pro on this PC. Restart Fleet Manager Pro and try again."
    with _lock:
        if _proc[0] and _proc[0].poll() is None:
            return None
        env = dict(os.environ, FMP_OVERLAY_KEY=key)       # the key goes in the environment, not on the command line
        try:
            _proc[0] = subprocess.Popen(_command(port), env=env, close_fds=True,  # nosec B603  # our own program
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            log.warning("in-game overlay didn't start: %s", e)
            return f"The overlay didn't start ({e})."
    log.info("in-game overlay started (pid %s)", _proc[0].pid)
    return None


def stop():
    with _lock:
        p, _proc[0] = _proc[0], None
    if p and p.poll() is None:
        p.terminate()
        log.info("in-game overlay stopped")


def autostart(app):
    """Open the overlay when Fleet Manager Pro starts, if the driver on this PC asked for that."""
    try:
        from . import db, overlay_fields
        from .telemetry.service import local_user
        with app.app_context():
            uid = local_user()
            if uid and overlay_fields.config(uid)["autostart"]:
                key = db.val("SELECT agent_token FROM users WHERE id=?", (uid,))
                start(local_port(app), key)
    except Exception:  # noqa: BLE001 - the overlay is optional; never stop the app starting
        log.exception("in-game overlay autostart failed")


# ------------------------------------------------------------------------------------------------ the window
KEYS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "o": 0x4F, "f": 0x46, "i": 0x49, "f10": 0x79, "f11": 0x7A, "f12": 0x7B}
COLORS = {"": "#f2f6fc", "warn": "#f0a92e", "bad": "#ff4d6a", "info": "#7fb2f0"}
TRANSPARENT = "#010203"                                  # painted pixels of this colour become see-through


def _hm(m):
    m = max(0, int(m))
    return f"{m // 60}:{m % 60:02d}"


STICKY = ("drive-", "break-", "cycle-")         # Hours of Service warnings that stay on screen while they're true


def pick_banner(warnings):
    """The one Hours of Service warning to keep on screen under the panel: the most urgent drive / break / cycle
    warning that is true right now, or None. A pop-up lasts seconds; 15 game minutes pass in under a minute of real
    time, so the warning has to stay up until the driver rests."""
    rank = {"bad": 0, "warn": 1}
    live = [w for w in warnings or [] if str(w.get("code", "")).startswith(STICKY) and w.get("level") in rank]
    return min(live, key=lambda w: rank[w["level"]]) if live else None


class Overlay:
    def __init__(self, port, key):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.url = tk, f"http://127.0.0.1:{port}/api/overlay?key={urllib.parse.quote(key)}"
        self.root = tk.Tk()
        self.root.title("Fleet Manager Pro overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.fonts = {}
        self.tkfont = tkfont
        self.data, self.cfg = None, None
        self.since = ""
        self.visible = True
        self.toasts = []                                  # [text, level, until]
        self.pending = []                                 # alerts fetched but not shown yet
        self.seen_warn = set()
        self.banner = None                                # the HOS warning kept on screen (pick_banner)
        self.last_status = None
        self.fails = 0
        self.last_ok = time.time()                        # the overlay closes itself 30 s after the app goes away
        self.hotkey_down = False
        self.hwnd = None
        self._topmost_at = 0.0
        self.root.update_idletasks()
        self._click_through()
        threading.Thread(target=self._poll_loop, daemon=True, name="overlay-poll").start()
        self.toast("Fleet Manager Pro overlay is on.", "info", 6)

    # Windows: let clicks fall through to the game and never take the keyboard focus
    def _click_through(self):
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            self.hwnd = hwnd
            ex = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8)
        except Exception:  # noqa: BLE001 - still works as a normal top-most window
            self.hwnd = None
            log.exception("overlay: click-through not available")

    def _keep_on_top(self):
        """Put the window back on top without moving it or taking focus. tkinter sets -topmost once, but a game in
        borderless full screen repaints over everything, so on a single screen the overlay slips behind it. Calling
        SetWindowPos(HWND_TOPMOST) about once a second keeps the panel visible over the game."""
        if not self.hwnd:
            return
        try:
            import ctypes
            # HWND_TOPMOST=-1; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_NOOWNERZORDER so z-order is the only change
            ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200)
        except Exception as e:  # noqa: BLE001 - the -topmost attribute still applies
            log.debug("overlay: SetWindowPos topmost failed: %s", e)

    def font(self, size, bold=False):
        k = (size, bold)
        if k not in self.fonts:
            self.fonts[k] = self.tkfont.Font(family="Segoe UI", size=-size, weight="bold" if bold else "normal")
        return self.fonts[k]

    def toast(self, text, level="info", secs=9):
        self.toasts.append([text, level, time.time() + secs])
        self.toasts = self.toasts[-3:]

    # ---- data
    def _poll_loop(self):
        while True:
            try:
                url = self.url + (f"&since={self.since}" if self.since else "")
                with urllib.request.urlopen(url, timeout=3) as r:  # nosec B310  # always http://127.0.0.1 (this PC)
                    d = json.loads(r.read().decode("utf-8"))
                self.fails = 0
                self.last_ok = time.time()
                self.since = str(d.get("server_time") or "")
                self.pending.extend(d.get("alerts") or [])  # each logbook alert arrives once; queue it for drawing
                self.data = d
            except Exception as e:  # noqa: BLE001
                self.fails += 1
                if getattr(e, "code", None) == 401:
                    self.data = {"gone": True}
            time.sleep(0.5)

    # ---- hotkey (polled, so it works while the game has the keyboard)
    def _hotkey(self):
        try:
            import ctypes
            combo = (self.cfg or {}).get("hotkey", "ctrl+i").split("+")
            down = all(ctypes.windll.user32.GetAsyncKeyState(KEYS[k]) & 0x8000 for k in combo if k in KEYS)
        except Exception:  # noqa: BLE001
            down = False
        if down and not self.hotkey_down:
            self.visible = not self.visible
            if self.visible:
                self.toast("Overlay shown.", "info", 3)
        self.hotkey_down = down

    # ---- drawing
    def _round(self, x0, y0, x1, y1, r, **kw):
        c = self.canvas
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
               x0, y0 + r, x0, y0]
        return c.create_polygon(pts, smooth=True, **kw)

    def tick(self):
        self._hotkey()
        d = self.data or {}
        if d.get("gone") or time.time() - self.last_ok > 30:   # key revoked, or the app has been closed for 30 s
            self.root.destroy()
            return
        if d.get("config"):
            self.cfg = d["config"]
        cfg = self.cfg or {"opacity": 90, "scale": 100, "corner": "tr", "only_driving": True}
        hud = d.get("hud") or {}
        while self.pending:
            a = self.pending.pop(0)
            self.toast(a["text"], a.get("level", "info"), 20 if a.get("level") in ("warn", "bad") else 9)
        self.banner = pick_banner(d.get("warnings"))
        codes = set()
        for w in d.get("warnings") or []:
            codes.add(w["code"])
            if w["code"] not in self.seen_warn and not (self.banner and w["code"] == self.banner["code"]):
                self.toast(w["text"], w.get("level", "warn"), 12)
        self.seen_warn = codes                            # a warning shows again only after it has cleared
        status = (hud.get("hos") or {}).get("status")
        if status and self.last_status and status != self.last_status:
            self.toast("Duty status: " + {"driving": "Driving", "on": "On duty", "off": "Off duty",
                                          "sleeper": "Sleeper berth"}.get(status, status), "info", 6)
        self.last_status = status or self.last_status

        now = time.time()
        self.toasts = [t for t in self.toasts if t[2] > now]
        online = bool(d.get("online") and d.get("rows") is not None)
        show_panel = self.visible and (online or not cfg.get("only_driving"))
        self.draw(cfg, hud, d.get("rows") or [], online, show_panel)
        self.root.attributes("-alpha", max(0.3, min(1.0, cfg.get("opacity", 90) / 100)))
        if now - self._topmost_at >= 1.0:                 # stay above a borderless full-screen game
            self._topmost_at = now
            self._keep_on_top()
        self.root.after(150, self.tick)

    def draw(self, cfg, hud, rows, online, show_panel):
        c, s = self.canvas, cfg.get("scale", 100) / 100
        c.delete("all")
        pad, gap, w = int(12 * s), int(6 * s), int(250 * s)
        f_lbl, f_val, f_head, f_toast = self.font(int(13 * s)), self.font(int(14 * s), True), self.font(int(13 * s), True), self.font(int(13 * s))
        accent = hud.get("color") if str(hud.get("color", "")).startswith("#") and len(hud.get("color", "")) == 7 else "#c8102e"
        if show_panel:
            lw = max([f_lbl.measure(r[0]) for r in rows] + [0])
            vw = max([f_val.measure(r[1]) for r in rows] + [f_head.measure("Waiting for the game")])
            w = max(w, lw + vw + pad * 3)
        y = 0
        if show_panel:
            line = int(22 * s)
            h = pad * 2 + int(24 * s) + (len(rows) if online else 1) * line
            self._round(0, 0, w, h, int(12 * s), fill="#0b1c36", outline="#1d3a66")
            c.create_rectangle(int(10 * s), 1, w - int(10 * s), int(4 * s), fill=accent, outline=accent)
            tag = (hud.get("tag") or "FMP")[:5]
            c.create_text(pad, pad + int(10 * s), text=tag, anchor="w", fill=accent, font=f_head)
            c.create_text(pad + f_head.measure(tag) + gap, pad + int(10 * s), anchor="w", fill="#9fb3d1", font=f_lbl,
                          text=hud.get("driver") or "Fleet Manager Pro")
            yy = pad + int(28 * s)
            if not online:
                c.create_text(pad, yy + line // 2, anchor="w", text="Waiting for the game", fill="#9fb3d1", font=f_val)
            for label, val, state in rows if online else []:
                c.create_text(pad, yy + line // 2, anchor="w", text=label, fill="#9fb3d1", font=f_lbl)
                c.create_text(w - pad, yy + line // 2, anchor="e", text=val, fill=COLORS.get(state, COLORS[""]), font=f_val)
                yy += line
            y = h + gap
        stack = ([[self.banner["text"], self.banner["level"], 0]] if self.banner else []) + self.toasts
        for text, level, _until in stack if self.visible else []:
            tw = max(w, min(int(380 * s), f_toast.measure(text) + pad * 3))
            ty = int(9 * s)
            t_id = c.create_text(pad + int(4 * s), y + ty, anchor="nw", text=text, fill="#f2f6fc", font=f_toast,
                                 width=tw - pad * 2 - int(4 * s))          # long alerts wrap onto more lines
            th = (c.bbox(t_id)[3] - y) + ty
            bg = self._round(0, y, tw, y + th, int(10 * s), fill="#11284a", outline=COLORS.get(level, COLORS["info"]))
            bar = c.create_rectangle(int(4 * s), y + int(7 * s), int(8 * s), y + th - int(7 * s), fill=COLORS.get(level), outline="")
            c.tag_lower(bar, t_id)
            c.tag_lower(bg, bar)
            w = max(w, tw)
            y += th + gap
        self._place(cfg.get("corner", "tr"), w + 2, max(1, y + 2))

    def _place(self, corner, w, h):
        sw, sh, m = self.root.winfo_screenwidth(), self.root.winfo_screenheight(), 24
        x = {"l": m, "c": (sw - w) // 2, "r": sw - w - m}[corner[1] if corner[1] in "lcr" else "r"]
        y = {"t": m + 40, "m": (sh - h) // 2, "b": sh - h - m - 60}[corner[0] if corner[0] in "tmb" else "t"]
        self.canvas.configure(width=w, height=h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def run(self):
        self.root.after(200, self.tick)
        self.root.mainloop()


def main(port):
    """Entry point for "overlay" mode."""
    key = os.environ.get("FMP_OVERLAY_KEY", "")
    if not key:
        sys.exit("Start the overlay from Fleet Manager Pro (Stream Overlay page).")
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: an overlay is already open
            return
    except Exception as e:  # noqa: BLE001 - no single-instance guard; open the overlay anyway
        log.debug("overlay: single-instance check unavailable: %s", e)
    Overlay(port, key).run()
