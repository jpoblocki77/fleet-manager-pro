"""Accounts, sessions, roles, CSRF and first-run setup."""
import functools
import hmac
import ipaddress
import os
import secrets
import socket
import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, economy, licensing

bp = Blueprint("auth", __name__)

ROLES = ["trainee", "driver", "dispatcher", "admin", "owner"]
ROLE_LABELS = {"trainee": "Trainee", "driver": "Driver", "dispatcher": "Dispatcher", "admin": "Manager",
               "owner": "Owner"}


def role_level(role):
    return ROLES.index(role) if role in ROLES else 0


def user():
    if "user" not in g:
        uid = session.get("uid")
        u = db.one("SELECT * FROM users WHERE id=? AND status!='removed'", (uid,)) if uid else None
        # a session only counts while the password it signed in with is still the current one,
        # so changing or resetting a password signs every other browser out
        if u and session.get("pv") != u["password_hash"][-16:]:
            u = None
        g.user = u
    return g.user


def start_session(uid):
    session.clear()
    session["uid"] = uid
    session["pv"] = db.val("SELECT password_hash FROM users WHERE id=?", (uid,))[-16:]
    session.permanent = True


def safe_next(target, default):
    """Only follow redirects to a path on this site (no //host, no backslash tricks, no control characters)."""
    t = target or ""
    if t.startswith("/") and not t.startswith("//") and "\\" not in t and all(ord(c) >= 32 for c in t):
        return t
    return default


_LOCAL_NAMES = {"localhost", socket.gethostname().lower(), socket.getfqdn().lower()}


def host_allowed(host):
    """Refuse requests addressed to a host name that isn't this PC (DNS-rebinding protection).
    IP addresses, localhost and this PC's own name are fine; add others with FMP_ALLOWED_HOSTS or
    the allowed_hosts setting (comma separated), e.g. when publishing through a reverse proxy."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:h.index("]")] if "]" in h else h
    elif h.count(":") == 1:
        h = h.split(":")[0]
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    extra = (os.environ.get("FMP_ALLOWED_HOSTS", "") + "," + (db.setting("allowed_hosts", "") or "")).lower()
    return h in _LOCAL_NAMES or h in {x.strip() for x in extra.split(",") if x.strip()}


GAME_UNITS = {"ats": "imperial", "ets2": "metric"}
GAME_LABELS = {"ats": "American Truck Simulator", "ets2": "Euro Truck Simulator 2", "both": "Both games"}


def game_pref():
    u = user()
    p = (u or {}).get("game_pref") or "both"
    return p if p in GAME_LABELS else "both"


def game_scope():
    """'ats' or 'ets2' when the signed-in user chose one game (pages then show only that game's data),
    None for Both."""
    p = game_pref()
    return p if p in GAME_UNITS else None


def game_sql(column="game"):
    """SQL condition + arguments for the user's game filter, e.g.  f"... WHERE x {cond}", args + gargs."""
    g_ = game_scope()
    return (f" AND {column}=?", (g_,)) if g_ else ("", ())


def units_for(pref):
    """ATS -> imperial (mi, mph, lb, gal, °F); ETS2 -> metric; both -> the company's default units."""
    return GAME_UNITS.get(pref) or db.setting("units", "imperial")


def current_units():
    if "units_override" in g:
        return g.units_override
    return units_for(game_pref())


def can(min_role):
    u = user()
    return bool(u) and role_level(u["role"]) >= role_level(min_role)


def login_required(fn=None, role="trainee"):
    def deco(f):
        @functools.wraps(f)
        def inner(*a, **kw):
            if not user():
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("auth.login", next=request.path))
            if not can(role):
                abort(403)
            return f(*a, **kw)
        return inner
    return deco(fn) if fn else deco


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def install(app):
    app.jinja_env.globals.update(csrf_token=csrf_token, current_user=user, can=can, ROLE_LABELS=ROLE_LABELS,
                                 ROLES=ROLES, current_units=current_units, game_pref=game_pref, GAME_LABELS=GAME_LABELS,
                                 game_scope=game_scope)

    @app.before_request
    def _guard():
        ep = request.endpoint or ""
        if not host_allowed(request.host):
            abort(400, "This address isn't recognised as this Fleet Manager Pro server.")
        if ep == "static":
            return
        if ep == "auth.eula":                      # the licence agreement can always be read
            return
        if not licensing.licensed() and ep != "auth.activate":
            if request.path.startswith("/api/"):
                return {"ok": False, "error": "Fleet Manager Pro isn't activated on this PC."}, 403
            return redirect(url_for("auth.activate"))
        initialized = db.setting("initialized") == "1"
        if not initialized and ep not in ("auth.setup", "auth.activate", "static"):
            return redirect(url_for("auth.setup"))
        if request.method == "POST" and ep not in ("api.agent_frame",):
            sent = request.form.get("csrf") or request.headers.get("X-CSRF", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Security token missing or expired — reload the page and try again.")
        u = user()
        if u and (not u["last_seen"] or time.time() - u["last_seen"] > 60):
            db.update("users", u["id"], last_seen=time.time())

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'self'")
        return resp

    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403, msg="You don't have permission to do that."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, msg="That page doesn't exist."), 404

    @app.errorhandler(400)
    def _400(e):
        return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request")), 400


def create_user(username, display_name, password, role="driver", **extra):
    return db.insert("users", username=username.strip(), display_name=display_name.strip() or username.strip(),
                     password_hash=generate_password_hash(password), role=role,
                     agent_token=secrets.token_urlsafe(24), created_at=time.time(), **extra)


_DUMMY_HASH = generate_password_hash(secrets.token_hex(8))


MIN_PASSWORD = 8
PASSWORD_RULE = f"Passwords need at least {MIN_PASSWORD} characters and can't be a common or easy-to-guess one."
_COMMON = {"password", "password1", "password12", "password123", "passw0rd", "12345678", "123456789", "1234567890",
           "87654321", "qwertyui", "qwerty123", "qwertyuiop", "1q2w3e4r", "abc12345", "abcd1234", "iloveyou",
           "letmein1", "welcome1", "welcome123", "admin123", "administrator", "trucking", "trucker1", "truckers",
           "fleetmanager", "fleetmanagerpro", "changeme", "football", "baseball", "sunshine", "princess", "superman"}


def valid_password(pw, username=""):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD or len(pw) > 200:
        return False
    low = pw.lower()
    if low in _COMMON or len(set(low)) < 3:                  # "aaaaaaaa", "abababab"
        return False
    name = (username or "").strip().lower()
    if name and (low == name or low == name + name or low.strip("0123456789!") == name):
        return False
    return True


LOCK_AFTER, LOCK_SECONDS, PER_PC_LIMIT = 5, 300, 20


def _throttled(ip, name):
    """Wrong passwords are counted per PC (IP address), never per account - so nobody can lock another
    person's account by guessing at it, while a guesser still gets 5 tries per name and 20 overall."""
    now = time.time()
    db.ex("DELETE FROM login_failures WHERE t < ?", (now - 900,))
    pair = db.val("SELECT COUNT(*) FROM login_failures WHERE ip=? AND username=? AND t>=?", (ip, name, now - LOCK_SECONDS), 0)
    total = db.val("SELECT COUNT(*) FROM login_failures WHERE ip=? AND t>=?", (ip, now - 600), 0)
    return pair >= LOCK_AFTER or total >= PER_PC_LIMIT


@bp.route("/activate", methods=["GET", "POST"])
def activate():
    lic = licensing.LICENCE
    error = None
    if request.method == "POST":
        if request.form.get("action") == "retry":
            lic.check()
        elif request.form.get("agree") != "1":
            error = "Tick the box to agree to the licence agreement before activating."
        else:
            error = lic.activate(request.form.get("key", ""))
            if lic.ok:
                db.set_setting("eula_accepted", f"{time.strftime('%Y-%m-%d %H:%M:%S')} key {lic.key or ''}")
                db.audit(session.get("uid"), "licence agreement accepted", lic.key or "")
        if lic.ok:
            db.audit(session.get("uid"), "licence activated", lic.key or "")
            return redirect(url_for("pages.dashboard"))
    elif lic.ok:
        return redirect(url_for("pages.dashboard"))
    return render_template("activate.html", error=error, st=lic.status())


def eula_text():
    from . import RES
    for p in (os.path.join(RES, "fmpro", "EULA.txt"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "EULA.txt")):
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read()
    return "The licence agreement file is missing from this copy. Contact the seller."


def eula_blocks(text):
    """Re-flow the hard-wrapped agreement into (kind, text) blocks for the web page: title, head, item or para."""
    import re
    blocks = []
    for para in re.split(r"\n[ \t]*\n", text.strip()):
        cur = None                                   # the block lines of this paragraph are joined onto
        for line in para.splitlines():
            t = line.strip()
            if not blocks:
                blocks.append(["title", t])
            elif re.match(r"^\d+\.\s+[A-Z][A-Z ]+$", t):
                blocks.append(["head", t])
                cur = None
            elif re.match(r"^[a-z]\)\s", t):
                cur = ["item", t]
                blocks.append(cur)
            elif cur is not None:
                cur[1] += " " + t
            else:
                cur = ["para", t]
                blocks.append(cur)
    return [tuple(b) for b in blocks]


@bp.route("/licence-agreement")
def eula():
    return render_template("eula.html", blocks=eula_blocks(eula_text()))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if db.setting("initialized") == "1":
        return redirect(url_for("pages.dashboard"))
    error = None
    if request.method == "POST":
        f = request.form
        sent = f.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400)
        name, user_n, pw = f.get("company_name", "").strip(), f.get("username", "").strip(), f.get("password", "")
        if not name or not user_n:
            error = "Company name and your username are required."
        elif not valid_password(pw, user_n):
            error = PASSWORD_RULE
        elif pw != f.get("password2"):
            error = "Passwords don't match."
        else:
            for k in ("company_name", "company_tag", "company_motto", "units", "currency", "default_game"):
                if f.get(k) is not None:
                    db.set_setting(k, f.get(k).strip())
            start = float(db.STARTING_CAPITAL)            # locked: the form can't change it
            uid = create_user(user_n, f.get("display_name", ""), pw, role="owner")
            db.insert("divisions", name="Freight", description="General freight division", color="#c8102e")
            db.insert("divisions", name="Heavy Haul", description="Oversize and special transport", color="#1f4e9c")
            economy.post("capital", start, "Starting capital")
            db.set_setting("starting_balance", str(db.STARTING_CAPITAL))
            db.set_setting("telemetry_user_id", uid)
            db.set_setting("initialized", "1")
            db.insert("announcements", title=f"Welcome to {name}!",
                      body="Your company is set up. Start ATS or ETS2 with the telemetry plugin installed and your jobs will be logged automatically.",
                      pinned=1, author_id=uid, created_at=time.time())
            start_session(uid)
            return redirect(url_for("pages.dashboard"))
    return render_template("setup.html", error=error)


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        name, pw = request.form.get("username", "").strip(), request.form.get("password", "")
        u = db.one("SELECT * FROM users WHERE username=? AND status!='removed'", (name,))
        ip, key = request.remote_addr or "?", name.lower()[:64]
        if _throttled(ip, key):
            error = "Too many wrong passwords from this PC. Try again in a few minutes."
        elif u and check_password_hash(u["password_hash"], pw):
            db.ex("DELETE FROM login_failures WHERE ip=? AND username=?", (ip, key))
            db.update("users", u["id"], last_seen=time.time())
            start_session(u["id"])
            db.audit(u["id"], "login")
            return redirect(safe_next(request.args.get("next", ""), url_for("pages.dashboard")))
        else:
            if not u:   # spend the same time as a real check, so timing doesn't reveal which names exist
                check_password_hash(_DUMMY_HASH, pw)
            db.ex("INSERT INTO login_failures(ip, username, t) VALUES (?,?,?)", (ip, key, time.time()))
            error = "Wrong username or password."
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account/game", methods=["POST"])
@login_required
def set_game():
    """Which game this driver plays - decides the units shown on every page."""
    pref = request.form.get("game", "")
    if pref not in GAME_LABELS:
        abort(400)
    db.update("users", user()["id"], game_pref=pref)
    if request.headers.get("X-CSRF"):
        return {"ok": True, "game": pref, "units": units_for(pref)}
    return redirect(safe_next(request.form.get("next", ""), url_for("pages.dashboard")))


@bp.route("/account/password", methods=["POST"])
@login_required
def change_password():
    u = user()
    if not check_password_hash(u["password_hash"], request.form.get("current", "")):
        flash("Current password is wrong.", "error")
    elif not valid_password(request.form.get("new", ""), u["username"]):
        flash(PASSWORD_RULE, "error")
    else:
        new_hash = generate_password_hash(request.form["new"])
        db.update("users", u["id"], password_hash=new_hash)
        session["pv"] = new_hash[-16:]          # keep this browser signed in; every other session ends
        db.audit(u["id"], "password changed")
        flash("Password changed. Other browsers signed in to your account have been signed out.", "success")
    return redirect(url_for("pages.profile"))
