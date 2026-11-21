"""Ask the home router (UPnP Internet Gateway Device) for this network's internet address, and open / close the
port remote drivers connect to - so the owner doesn't have to look up "what is my IP" or log into the router.

Nothing leaves the home network: discovery is a multicast question on the local network (SSDP), and every later
request goes to the router that answered. Only routers at a private or loopback address are believed, the answers
are size-capped and read with plain text matching (no XML entity processing), and only these three SOAP actions are
ever sent: GetExternalIPAddress, AddPortMapping, DeletePortMapping.

Many routers have UPnP switched off; then everything here returns a clear reason and the page falls back to the
address the owner types in.
"""
import html
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("fmpro.upnp")

SSDP_ADDR = ("239.255.255.250", 1900)
WAN_SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1")
MAX_BYTES = 256 * 1024                        # a router's description is a few KB; refuse anything silly
DESCRIPTION = "Fleet Manager Pro"
NOT_REACHABLE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16", "127.0.0.0/8", "0.0.0.0/8")
OFF = ("Your router didn't answer. Its UPnP setting is probably switched off (look for \"UPnP\" in the router's "
       "settings), or this PC isn't connected through a home router.")


class Gateway:
    def __init__(self, control_url, service, local_ip):
        self.control_url, self.service, self.local_ip = control_url, service, local_ip

    def __repr__(self):
        return f"<Gateway {urllib.parse.urlsplit(self.control_url).netloc} {self.service.rsplit(':', 2)[-2]}>"


class UPnPError(Exception):
    """Something the owner can read: why the router couldn't be asked or refused."""


# ------------------------------------------------------------------------------------------------ helpers
def _local_host(url):
    """True when a URL points at a router on this network (private or loopback IP over http), else False."""
    try:
        u = urllib.parse.urlsplit(url)
        ip = ipaddress.ip_address(u.hostname or "")
    except ValueError:
        return False
    return u.scheme == "http" and (ip.is_private or ip.is_loopback) and not ip.is_multicast


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could send us outside the home network after the address check: refuse every redirect."""
    def redirect_request(self, *a, **kw):
        return None


# no system proxy (a LAN router must be asked directly) and no redirects
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _get(url, data=None, headers=None, timeout=3):
    if not _local_host(url):
        raise UPnPError("The router gave an address outside your home network, so it was ignored.")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with _OPENER.open(req, timeout=timeout) as r:  # nosec B310  # http to a private/loopback router address only (checked above)
            return r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                          # a SOAP fault comes back as HTTP 500 with details
        body = e.read(MAX_BYTES).decode("utf-8", "replace")
        code = _tag(body, "errorCode")[:12]
        desc = _tag(body, "errorDescription")[:120]
        raise UPnPError(f"The router said no ({code or e.code}{': ' + desc if desc else ''}).") from None
    except OSError as e:
        raise UPnPError(f"Couldn't reach the router ({e}).") from None


def _tag(text, name):
    """The text inside the first <name>...</name> (any namespace prefix), unescaped, or ''."""
    m = re.search(rf"<(?:[\w-]+:)?{name}\b[^>]*>(.*?)</(?:[\w-]+:)?{name}>", text or "", re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def parse_description(xml, base_url):
    """(control URL, service type) of the router's WAN connection service from its description, or None."""
    base = _tag(xml, "URLBase") or base_url
    best = None
    for block in re.findall(r"<(?:[\w-]+:)?service\b[^>]*>(.*?)</(?:[\w-]+:)?service>", xml or "", re.S):
        st = _tag(block, "serviceType")
        if st in WAN_SERVICES:
            ctl = urllib.parse.urljoin(base, _tag(block, "controlURL"))
            rank = WAN_SERVICES.index(st)
            if best is None or rank < best[0]:
                best = (rank, ctl, st)
    return (best[1], best[2]) if best else None


def _local_ip_towards(host):
    """This PC's address on the network the router is on (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ discovery
def _ssdp_search(timeout):
    """LOCATION URLs of devices that answered an Internet Gateway search on this network."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: {st}\r\n\r\n")
    found = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        for st in ("urn:schemas-upnp-org:device:InternetGatewayDevice:1", "urn:schemas-upnp-org:device:InternetGatewayDevice:2"):
            s.sendto(msg.format(st=st).encode(), SSDP_ADDR)
        while True:
            try:
                data, _addr = s.recvfrom(4096)
            except OSError:                      # timeout: nobody else is answering
                break
            m = re.search(rb"^location:\s*(\S+)", data, re.I | re.M)
            if m:
                loc = m.group(1).decode("latin-1")
                if loc not in found:
                    found.append(loc)
    finally:
        s.close()
    return found


def discover(timeout=2.5, search=None):
    """The router's WAN service, or raise UPnPError saying why not. `search` is for tests."""
    locations = (search or _ssdp_search)(timeout)
    if not locations:
        raise UPnPError(OFF)
    last = None
    for loc in locations:
        if not _local_host(loc):
            log.info("upnp: ignored a device outside the home network: %s", loc)
            continue
        try:
            found = parse_description(_get(loc), loc)
        except UPnPError as e:
            last = e
            continue
        if found:
            host = urllib.parse.urlsplit(found[0]).hostname
            if host != urllib.parse.urlsplit(loc).hostname:      # it may only send us to itself, not to another machine
                log.info("upnp: ignored a device pointing at another machine: %s -> %s", loc, found[0])
                continue
            gw = Gateway(found[0], found[1], _local_ip_towards(host))
            log.info("upnp: router found %r (this PC is %s)", gw, gw.local_ip)
            return gw
    raise last or UPnPError("A device answered, but it isn't a router that can report the internet address.")


# ------------------------------------------------------------------------------------------------ actions
def _soap(gw, action, args=()):
    body = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in args)
    envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{gw.service}">{body}</u:{action}></s:Body></s:Envelope>')
    return _get(gw.control_url, envelope.encode("utf-8"),
                {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{gw.service}#{action}"'})


def external_ip(gw):
    """The internet address the router has, as text. Raises UPnPError."""
    ip = _tag(_soap(gw, "GetExternalIPAddress"), "NewExternalIPAddress")
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise UPnPError("The router doesn't know its internet address right now (is it online?).") from None
    return ip


def behind_second_router(ip):
    """True when the 'internet' address is itself private / carrier-grade NAT: drivers outside can't reach it."""
    a = ipaddress.ip_address(ip)
    return any(a in ipaddress.ip_network(n) for n in NOT_REACHABLE) if a.version == 4 else not a.is_global


def open_port(gw, port, proto="TCP"):
    """Forward the router's `port` to the same port on this PC (permanent until removed)."""
    _soap(gw, "AddPortMapping", (("NewRemoteHost", ""), ("NewExternalPort", int(port)), ("NewProtocol", proto),
                                 ("NewInternalPort", int(port)), ("NewInternalClient", gw.local_ip),
                                 ("NewEnabled", 1), ("NewPortMappingDescription", DESCRIPTION),
                                 ("NewLeaseDuration", 0)))
    log.info("upnp: port %s/%s forwarded to %s", port, proto, gw.local_ip)


def close_port(gw, port, proto="TCP"):
    _soap(gw, "DeletePortMapping", (("NewRemoteHost", ""), ("NewExternalPort", int(port)), ("NewProtocol", proto)))
    log.info("upnp: port %s/%s forward removed", port, proto)


# ------------------------------------------------------------------------------------------------ app glue
def detect_and_save(port=None, open_it=False):
    """Find the router, save its internet address as the public address (when the owner hasn't typed a host name),
    and open the port if asked. Returns a dict for the page; raises UPnPError. Call inside an app context."""
    from . import db
    gw = discover()
    ip = external_ip(gw)
    out = {"ip": ip, "local": gw.local_ip, "nat2": behind_second_router(ip), "saved": False, "opened": False}
    current = db.setting("public_address", "") or ""
    is_name = bool(current) and not _is_ip(current)
    if not is_name:                                  # a typed host name (dynamic DNS) is better: keep it
        db.set_setting("public_address", ip)
        db.set_setting("public_address_auto", "1")
        out["saved"] = True
    if open_it and port:
        open_port(gw, port)
        db.set_setting("upnp_port", str(int(port)))
        out["opened"] = True
    return out


def _is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def refresh_on_start(app, port):
    """LAN-mode start: keep the found address current and re-open the port (routers forget it when they restart).
    Only does what the owner already chose on Connect a Driver. Runs in the background; never raises."""
    import threading

    def work():
        try:
            with app.app_context():
                from . import db
                want_ip = db.setting("public_address_auto", "0") == "1"
                want_port = (db.setting("upnp_port", "") or "") == str(int(port))
                if not (want_ip or want_port):
                    return
                gw = discover()
                current = db.setting("public_address", "") or ""
                if want_ip and (not current or _is_ip(current)):          # never replace a typed host name
                    ip = external_ip(gw)
                    if ip != db.setting("public_address", ""):
                        db.set_setting("public_address", ip)
                        log.info("upnp: internet address changed to %s", ip)
                if want_port:
                    open_port(gw, port)
        except UPnPError as e:
            log.info("upnp: start-up refresh skipped: %s", e)
        except Exception:  # noqa: BLE001 - optional helper; never disturb the app starting
            log.exception("upnp: start-up refresh failed")
    t = threading.Thread(target=work, daemon=True, name="upnp-refresh")
    t.start()
    return t
