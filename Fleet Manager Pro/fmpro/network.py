"""Let other PCs in: the switch that replaces starting with the (LAN) file.

A normal start serves this PC on http://127.0.0.1:PORT. When an Owner or Manager turns the switch on (Connect a
Driver), an encrypted (HTTPS) door opens for the network on the same PORT, at once and without a restart, and it opens
again by itself at every start (setting lan_access). Windows sends this PC's own 127.0.0.1 traffic to the app and
everything else to the door. It is the same port, certificate and address the (LAN) file uses, so connection files
drivers already have keep working.

Starting with --lan (the LAN file) still works and needs no switch: the launcher sets app.config["LAN"] itself.
app.config["LAN"] = {"scheme", "port"} is what the connect and tablet pages hand out; publish() keeps it right."""
import logging
import socket

from flask import Blueprint, current_app, flash, redirect, request, url_for

from . import db, tlsfront
from .auth import login_required, user

bp = Blueprint("network", __name__)
log = logging.getLogger("fmpro.network")

_door = {"srvs": []}                # the listening sockets while other PCs are let in


def is_open():
    return bool(_door["srvs"])


def can_switch(app):
    """The switch works at once in a normal start by the launcher; a --lan start is on the network already."""
    return bool(app.config.get("PORT"))


def publish(app):
    """Which door the connect and tablet pages point at: other PCs' door, else the tablet door, else none."""
    from . import tablet
    if is_open():
        app.config["LAN"] = {"scheme": "https", "port": app.config["PORT"]}
    elif tablet.door_open():
        app.config["LAN"] = {"scheme": "https", "port": tablet.tablet_port(app)}
    else:
        app.config.pop("LAN", None)


def use_middleware(app):
    if not app.config.get("_TLS_MW"):
        app.wsgi_app = tlsfront.Middleware(app.wsgi_app)   # the caller's real address + https for requests through a door
        app.config["_TLS_MW"] = True


def _answers(ip, port):
    try:
        socket.create_connection((ip, port), timeout=0.5).close()
        return True
    except OSError:
        return False


def start(app):
    """Open the door. Returns None, or a message saying why not."""
    if is_open():
        return None
    port = app.config.get("PORT")
    if not port:
        return "This copy wasn't started by the Fleet Manager Pro launcher."
    from .tablet import lan_addresses
    addrs = lan_addresses()
    if any(_answers(ip, port) for ip in addrs):
        return f"Another program on this PC already answers the network on port {port}. Close it and try again."
    try:
        cert, key, _pin = tlsfront.ensure_cert(app.config["DATA_DIR"])
    except ImportError:
        return "This copy is missing the encryption package (cryptography), so other PCs can't connect securely."
    use_middleware(app)
    try:
        _door["srvs"] = [tlsfront.start("0.0.0.0", port, port, cert, key)]  # nosec B104  # other PCs connect here; HTTPS only, plain HTTP is redirected
    except OSError:
        # this system doesn't share the port with 127.0.0.1: listen on each network address instead
        srvs = []
        for ip in addrs:
            try:
                srvs.append(tlsfront.start(ip, port, port, cert, key))
            except OSError as e:
                log.warning("couldn't listen on %s:%s: %s", ip, port, e)
        if not srvs:
            return f"Port {port} couldn't be opened for other PCs."
        _door["srvs"] = srvs
    publish(app)
    log.info("other PCs are let in: https on port %s", port)
    return None


def stop(app, linger=0.0):
    """Close the door and cut what is connected through it (after `linger` seconds, so the page that asked can answer)."""
    srvs, _door["srvs"] = _door["srvs"], []
    for s in srvs:
        tlsfront.stop(s, linger)
    if srvs:
        publish(app)
        log.info("other PCs are no longer let in")


def autostart(app):
    """Launcher, normal start: open the door again if it was left on. Returns a message when it couldn't."""
    with app.app_context():
        if db.setting("lan_access", "0") != "1":
            return None
        err = start(app)
    if err:
        log.warning("letting other PCs in: %s", err)
    else:
        from . import upnp
        upnp.refresh_on_start(app, app.config["PORT"])      # what the owner chose: current address, router port kept open
    return err


@bp.route("/network/access", methods=["POST"])
@login_required(role="admin")
def access():
    app = current_app._get_current_object()
    back = redirect(url_for("pages.drivers_connect"))
    on = request.form.get("action") == "on"
    if not on and request.form.get("action") != "off":
        return back
    if not can_switch(app):                                  # started with the LAN file: only remember it
        db.set_setting("lan_access", "1" if on else "0")
        db.audit(user()["id"], "let other PCs in", "on (from the next start)" if on else "off (from the next start)")
        flash("From now on other PCs can connect however you start Fleet Manager Pro." if on else
              "From the next normal start only this PC can connect. The LAN file still lets other PCs in.", "success")
        return back
    if on:
        err = start(app)
        if err:
            flash(err, "error")
            return back
        db.set_setting("lan_access", "1")
        db.audit(user()["id"], "let other PCs in", f"on, port {app.config['PORT']}")
        from . import upnp
        upnp.refresh_on_start(app, app.config["PORT"])
        flash("Other PCs can connect now, and every time you start Fleet Manager Pro. If Windows asks, choose Private networks "
              "and click Allow.", "success")
    else:
        db.set_setting("lan_access", "0")
        db.audit(user()["id"], "let other PCs in", "off")
        stop(app, linger=1.5)
        flash("Other PCs can no longer connect." + (" Tablet access is still on (Connect a Tablet)." if app.config.get("LAN") else ""),
              "success")
    return back
