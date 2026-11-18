"""Connect a tablet or phone (iPad, Android) by scanning a QR code.

  /tablet           shows a QR code for this PC's network address plus a one-time sign-in code
  /pair/<code>      opened by the tablet: signs it in as the member who made the code, then goes to the dashboard

A code works once, for 10 minutes, and only for the member who made it. Codes live in memory only (a restart
voids them) and are masked in every log. The tablet must be on the same network, and Fleet Manager Pro must be
running in network mode ("Start Fleet Manager Pro (LAN).bat"), which the launcher records in app.config["LAN"].
"""
import ipaddress
import logging
import secrets
import socket
import threading
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from markupsafe import Markup

from . import db, qr
from .auth import login_required, start_session, user

bp = Blueprint("tablet", __name__)
log = logging.getLogger("fmpro.tablet")

CODE_LIFE = 600              # seconds a code stays valid
KEEP_PER_USER = 3            # newest codes kept per member (older ones stop working)
_codes = {}                  # code -> (user id, expires at)
_lock = threading.Lock()


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
    lan = current_app.config.get("LAN")
    addrs = lan_addresses()
    ip = request.args.get("ip", "")
    ip = ip if ip in addrs else (addrs[0] if addrs else "")
    ctx = {"lan": lan, "addrs": addrs, "ip": ip, "life_min": CODE_LIFE // 60}
    if lan and ip:
        base = f"{lan['scheme']}://{ip}:{lan['port']}"
        code = new_code(user()["id"])
        link = f"{base}/pair/{code}"
        ctx.update(base=base, link=link, qr=Markup(qr.svg(link, border=4, px=7)))  # nosec B704  # SVG built by qr.py from our own URL
        log.info("tablet sign-in code made for member #%s (%s)", user()["id"], base)
    return render_template("tablet.html", **ctx)


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
