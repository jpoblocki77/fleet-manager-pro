"""Connect a tablet or phone (iPad, Android) by scanning a QR code.

  /tablet           shows a QR code for this PC's network address plus a one-time sign-in code
  /pair/<code>      opened by the tablet: signs it in as the member who made the code, then goes to the dashboard

A code works once, for 10 minutes, and only for the member who made it. Codes live in memory only (a restart
voids them) and are masked in every log. The tablet must be on the same network.

Tablets need the app to answer on the network. Two ways:
  * Tablet access (Owner/Manager switch on this page, remembered in the tablet_access setting): the normal
    exe / Start Fleet Manager Pro.bat keeps the PC itself on http://127.0.0.1:PORT and opens an encrypted
    (HTTPS) door for tablets on PORT+1, at once, no restart. The launcher calls autostart() on start-up.
  * Other PCs are let in (Connect a Driver switch, network.py) or network mode (Start Fleet Manager Pro (LAN).bat):
    the whole app is on the network already.
Either way app.config["LAN"] holds the scheme and port the QR link uses.
"""
import ipaddress
import logging
import secrets
import socket
import threading
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from markupsafe import Markup

from . import db, network, qr, tlsfront
from .auth import can, login_required, start_session, user

bp = Blueprint("tablet", __name__)
log = logging.getLogger("fmpro.tablet")

CODE_LIFE = 600              # seconds a code stays valid
KEEP_PER_USER = 3            # newest codes kept per member (older ones stop working)
_codes = {}                  # code -> (user id, expires at)
_lock = threading.Lock()


_front = {"srv": None}      # the tablet door's listening socket while tablet access is on


def tablet_port(app):
    return app.config.get("TABLET_PORT") or (app.config["PORT"] + 1)


def door_open():
    return _front["srv"] is not None


def network_on(app):
    """True while tablets can reach the app (tablet access or network mode)."""
    return bool(app.config.get("LAN"))


def can_switch(app):
    """Tablet access can be switched here only in a normal (this-PC-only) start; network mode is always on."""
    return bool(app.config.get("PORT")) and (_front["srv"] is not None or not app.config.get("LAN"))


def start_network(app):
    """Open the encrypted tablet door (0.0.0.0:PORT+1 -> this app). Returns None, or a message saying why not."""
    if _front["srv"] is not None:
        return None
    if not app.config.get("PORT"):
        return "This copy wasn't started by the Fleet Manager Pro launcher."
    try:
        cert, key, _pin = tlsfront.ensure_cert(app.config["DATA_DIR"])
    except ImportError:
        return "This copy is missing the encryption package (cryptography), so tablets can't connect securely."
    port = tablet_port(app)
    try:
        srv = tlsfront.start("0.0.0.0", port, app.config["PORT"], cert, key)  # nosec B104  # tablets on the same network connect here; HTTPS only, plain HTTP is redirected
    except OSError as e:
        log.warning("tablet access: couldn't open port %s: %s", port, e)
        return f"Port {port} is already in use on this PC, so tablets can't connect. Close whatever uses it and try again."
    network.use_middleware(app)                            # real tablet address + https for requests through the door
    _front["srv"] = srv
    network.publish(app)
    log.info("tablet access on: https on port %s", port)
    return None


def stop_network(app):
    srv, _front["srv"] = _front["srv"], None
    if srv is not None:
        tlsfront.stop(srv, linger=1.5)
        network.publish(app)
        log.info("tablet access off")


def autostart(app):
    """Launcher, normal start: reopen the tablet door if an Owner or Manager left tablet access on."""
    with app.app_context():
        if db.setting("tablet_access", "0") == "1":
            err = start_network(app)
            if err:
                log.warning("tablet access: %s", err)


def new_code(uid):
    code = secrets.token_urlsafe(24)                     # 192 bits: can't be guessed
    now = time.time()
    with _lock:
        for k in [k for k, (_, exp) in _codes.items() if exp < now]:
            del _codes[k]
        mine = sorted((exp, k) for k, (u, exp) in _codes.items() if u == uid)
        for _, k in mine[:max(0, len(mine) - (KEEP_PER_USER - 1))]:
            del _codes[k]
        _codes[code] = (uid, now + CODE_LIFE)
    return code


def use_code(code):
    """The member id for a valid code (which is then spent), else None."""
    with _lock:
        hit = _codes.pop(code or "", None)
    if not hit or hit[1] < time.time():
        return None
    return hit[0]


def lan_addresses():
    """This PC's IPv4 addresses a tablet on the same network could reach, best guess first."""
    found = []
    try:                                                  # the address of the route to the internet (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for a in found:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or a in out:
            continue
        out.append(a)
    return sorted(out, key=lambda a: (not ipaddress.ip_address(a).is_private, out.index(a)))


@bp.route("/tablet")
@login_required
def page():
    app = current_app._get_current_object()
    lan = app.config.get("LAN")
    addrs = lan_addresses()
    ip = request.args.get("ip", "")
    ip = ip if ip in addrs else (addrs[0] if addrs else "")
    ctx = {"lan": lan, "addrs": addrs, "ip": ip, "life_min": CODE_LIFE // 60,
           "switchable": can_switch(app), "door_open": _front["srv"] is not None}
    if lan and ip:
        base = f"{lan['scheme']}://{ip}:{lan['port']}"
        code = new_code(user()["id"])
        link = f"{base}/pair/{code}"
        ctx.update(base=base, link=link, qr=Markup(qr.svg(link, border=4, px=7)))  # nosec B704  # SVG built by qr.py from our own URL
        log.info("tablet sign-in code made for member #%s (%s)", user()["id"], base)
    return render_template("tablet.html", **ctx)


@bp.route("/tablet/access", methods=["POST"])
@login_required(role="admin")
def access():
    app = current_app._get_current_object()
    if request.form.get("action") == "off":
        db.set_setting("tablet_access", "0")
        stop_network(app)
        db.audit(user()["id"], "tablet access off", "")
        flash("Tablet access is off. " + ("Tablets can still connect because other PCs are let in (Connect a Driver)." if network.is_open()
              else "Tablets already signed in can no longer reach Fleet Manager Pro."), "success")
    else:
        err = start_network(app)
        if err:
            flash(err, "error")
        else:
            db.set_setting("tablet_access", "1")
            db.audit(user()["id"], "tablet access on", f"port {tablet_port(app)}")
            flash("Tablet access is on. If Windows asks, allow Fleet Manager Pro on Private networks.", "success")
    return redirect(url_for("tablet.page"))


@bp.route("/pair/<code>")
def pair(code):
    uid = use_code(code)
    u = db.one("SELECT * FROM users WHERE id=? AND status!='removed'", (uid,)) if uid else None
    if not u:
        log.warning("tablet sign-in refused (code expired, used or unknown) from ip=%s", request.remote_addr)
        flash("That QR code has expired or was already used. On the PC, open Connect a Tablet for a new one.", "error")
        return redirect(url_for("auth.login"))
    start_session(u["id"])
    db.update("users", u["id"], last_seen=time.time())
    db.audit(u["id"], "tablet signed in", f"ip {request.remote_addr}")
    log.info("tablet signed in: member #%s from ip=%s", u["id"], request.remote_addr)
    flash(f"Signed in on this device as {u['display_name']}. Tip: add this page to your home screen.", "success")
    return redirect(url_for("pages.dashboard"))
