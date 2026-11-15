"""HTML pages."""
import hashlib
import os
import re
import secrets
import shutil
import time

from flask import Blueprint, current_app, abort, flash, redirect, render_template, request, url_for

from . import RES, db, economy, licensing, updater
from .auth import PASSWORD_RULE, ROLES, game_scope, game_sql, can, create_user, login_required, role_level, user, valid_password
from .telemetry import layout
from .telemetry.reader import field_count
from .telemetry.service import HUB, local_user

bp = Blueprint("pages", __name__)

DAY = 86400


# ------------------------------------------------------------------ form helpers
def num(name, lo=0.0, hi=1e12, default=0.0, src=None):
    raw = (src or request.form).get(name, "")
    try:
        v = float(str(raw).replace(",", "").strip())
    except ValueError:
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, v))


def txt(name, maxlen=200, src=None):
    return str((src or request.form).get(name, "")).strip()[:maxlen]


KM_PER_MI, L_PER_GAL, KG_PER_LB = 1.609344, 3.785411784, 0.45359237


def unit_num(name, kind, lo=0.0, hi=1e12, default=0.0):
    """A number typed in the form's units (hidden field 'units': imperial / metric), returned in metric:
    kind 'dist' -> km, 'vol' -> litres, 'mass' -> kg. Bounds are in metric."""
    v = num(name, 0, 1e12, None)
    if v is None:
        return default
    if request.form.get("units") == "imperial":
        v *= {"dist": KM_PER_MI, "vol": L_PER_GAL, "mass": KG_PER_LB}[kind]
    elif kind == "mass":
        v *= 1000                                    # metric weights are typed in tonnes
    return max(lo, min(hi, v))


# standard garage prices: size -> (trucks, price, weekly upkeep)
GARAGE_SIZES = {"small": (1, 100000, 500), "medium": (3, 250000, 1500), "large": (5, 450000, 2500)}


def affordable(price, what):
    """Purchases can't take the company account below zero. Flashes why and returns False when they would."""
    bal = economy.balance()
    if price and price > bal + 0.005:
        cur = db.setting("currency", "$")
        flash(f"Not enough money for {what}: it costs {cur}{price:,.0f} and the company has {cur}{bal:,.0f}. "
              "Take a loan on Finance, sell something, or choose a cheaper option.", "error")
        return False
    return True


def opt_id(name, table):
    raw = request.form.get(name, "")
    if not str(raw).isdigit():
        return None
    return int(raw) if db.val(f"SELECT id FROM {table} WHERE id=?", (int(raw),)) else None  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def back(default="pages.dashboard"):
    ref = request.referrer or ""
    return redirect(ref if ref.startswith(request.host_url) else url_for(default))


def period_start(period):
    now = time.time()
    return {"week": now - 7 * DAY, "month": now - 30 * DAY, "year": now - 365 * DAY}.get(period, 0)


def truck_sql(column="game"):
    """Trucks seen in the other game are hidden; trucks added by hand (no game recorded) always show."""
    g_ = game_scope()
    return (f" AND ({column} IS NULL OR {column}=?)", (g_,)) if g_ else ("", ())


_CARGO = None
_COMPANIES = None
_CITIES = None
_DEPOTS = {}


def cargo_options():
    """Cargo for the contract form, per game: what this company has hauled (exactly as the game reported it,
    mods included) and the game's full cargo list. Each item is [cargo id, name]; the id is '' when several
    cargo variants share one name."""
    global _CARGO, _COMPANIES, _CITIES, _DEPOTS
    if _CARGO is None:
        import json
        try:
            with open(os.path.join(RES, "fmpro", "cargo_list.json"), encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        _CARGO = {}
        for g in ("ats", "ets2"):
            by_name = {}
            for c in raw.get(g, []):
                by_name.setdefault(c["name"], set()).add(c["id"])
            _CARGO[g] = sorted(([next(iter(ids)) if len(ids) == 1 else "", n] for n, ids in by_name.items()),
                               key=lambda x: x[1].lower())
        _COMPANIES = {g: sorted({c["name"] for c in raw.get("companies", {}).get(g, [])}, key=str.lower)
                      for g in ("ats", "ets2")}
        # {city id: [company names with a depot there]}, from the game's own files
        _DEPOTS = {}
        for g in ("ats", "ets2"):
            names = {c["id"]: c["name"] for c in raw.get("companies", {}).get(g, [])}
            _DEPOTS[g] = {city: sorted({names[x] for x in ids if x in names}, key=str.lower)
                          for city, ids in raw.get("depots", {}).get(g, {}).items()}
        # [city id, name the game reports, label shown in the list]
        _CITIES = {g: [[c["id"], c["name"],
                        c["name"] + (f" ({c['native']})" if c.get("native") else "") + (f", {c['country']}" if c.get("country") else "")]
                       for c in raw.get("cities", {}).get(g, [])] for g in ("ats", "ets2")}
    out = {}
    for g in ("ats", "ets2"):
        hauled = db.q("""SELECT cargo, MAX(cargo_id) cargo_id, COUNT(*) n FROM jobs WHERE game=? AND source='telemetry'
                         AND cargo != '' AND COALESCE(flags,'') NOT LIKE '%demo%' GROUP BY cargo COLLATE NOCASE
                         ORDER BY cargo COLLATE NOCASE""", (g,))
        seen = db.q("""SELECT name FROM (SELECT source_company name FROM jobs WHERE game=? AND source='telemetry'
                       AND COALESCE(flags,'') NOT LIKE '%demo%' UNION SELECT destination_company FROM jobs
                       WHERE game=? AND source='telemetry' AND COALESCE(flags,'') NOT LIKE '%demo%')
                       WHERE name != '' ORDER BY name COLLATE NOCASE""", (g, g))
        been = db.q("""SELECT name FROM (SELECT source_city name FROM jobs WHERE game=? AND source='telemetry'
                       AND COALESCE(flags,'') NOT LIKE '%demo%' UNION SELECT destination_city FROM jobs
                       WHERE game=? AND source='telemetry' AND COALESCE(flags,'') NOT LIKE '%demo%')
                       WHERE name != '' ORDER BY name COLLATE NOCASE""", (g, g))
        out[g] = {"hauled": [[h["cargo_id"] or "", h["cargo"]] for h in hauled], "all": _CARGO[g],
                  "cities": _CITIES[g], "been": [r["name"] for r in been],
                  "visited": [r["name"] for r in seen], "companies": _COMPANIES[g], "depots": _DEPOTS[g]}
    return out


def users_list():
    return db.q("SELECT id, display_name, role, color FROM users WHERE status!='removed' ORDER BY display_name")


# ------------------------------------------------------------------ dashboard
@bp.route("/")
@login_required
def dashboard():
    u = user()
    month = time.time() - 30 * DAY
    gc, ga = game_sql()
    gcj, _ = game_sql("j.game")
    tc, ta = truck_sql()
    stats = db.one("""SELECT COUNT(*) jobs, COALESCE(SUM(driven_km),0) km, COALESCE(SUM(revenue),0) revenue,
                      COALESCE(SUM(profit),0) profit, COALESCE(AVG(score),0) score, COALESCE(SUM(cargo_mass),0) mass
                      FROM jobs WHERE status='delivered' AND finished_at>=?""" + gc, (month,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    mine = db.one("""SELECT COUNT(*) jobs, COALESCE(SUM(driven_km),0) km, COALESCE(SUM(revenue),0) revenue
                     FROM jobs WHERE status='delivered' AND user_id=?""" + gc, (u["id"],) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    daily = db.q(f"""SELECT strftime('%Y-%m-%d', finished_at, 'unixepoch', 'localtime') d, SUM(revenue) revenue,
                    SUM(driven_km) km, COUNT(*) n FROM jobs WHERE status='delivered' AND finished_at>=?{gc}
                    GROUP BY d ORDER BY d""", (time.time() - 14 * DAY,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    recent = db.q("""SELECT j.*, u.display_name, u.color FROM jobs j JOIN users u ON u.id=j.user_id
                     WHERE 1=1""" + gcj + """ ORDER BY j.started_at DESC LIMIT 8""", ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    top = db.q(f"""SELECT u.id, u.display_name, u.color, SUM(j.driven_km) km, COUNT(*) n FROM jobs j
                  JOIN users u ON u.id=j.user_id WHERE j.status='delivered' AND j.finished_at>=?{gcj}
                  GROUP BY u.id ORDER BY km DESC LIMIT 5""", (month,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    news = db.q("""SELECT a.*, u.display_name FROM announcements a LEFT JOIN users u ON u.id=a.author_id
                   ORDER BY a.pinned DESC, a.created_at DESC LIMIT 4""")
    upcoming = db.q("SELECT * FROM events WHERE start_at>=? AND status='scheduled'" + gc + " ORDER BY start_at LIMIT 3",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                    (time.time() - 3600,) + ga)
    contracts = db.q("""SELECT * FROM contracts WHERE status IN ('open','assigned') AND
                        (assigned_user_id IS NULL OR assigned_user_id=?)""" + gc + """ ORDER BY deadline IS NULL, deadline LIMIT 4""",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                     (u["id"],) + ga)
    counts = {
        "drivers": db.val("SELECT COUNT(*) FROM users WHERE status='active'", (), 0),
        "trucks": db.val("SELECT COUNT(*) FROM trucks WHERE status NOT IN ('sold','returned')" + tc, ta, 0),  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "pending": db.val("SELECT COUNT(*) FROM jobs WHERE status='pending_review'" + gc, ga, 0),  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        "apps": db.val("SELECT COUNT(*) FROM applications WHERE status='pending'", (), 0),
        "live": sum(1 for e in HUB.all_live().values() if not game_scope() or e["frame"]["values"]["game"] == game_scope()),
    }
    return render_template("dashboard.html", stats=stats, mine=mine, daily=daily, recent=recent, top=top,
                           news=news, upcoming=upcoming, contracts=contracts, counts=counts,
                           balance=economy.balance(), my_balance=economy.balance("driver", u["id"]),
                           prog=economy.progress(u["xp"]))


# ------------------------------------------------------------------ live telemetry
@bp.route("/live")
@login_required
def live():
    return render_template("live.html", drivers=users_list(), me=local_user())


@bp.route("/telemetry")
@login_required
def telemetry():
    groups = {}
    for key, group, kind, off, count in layout.FIELDS:
        groups.setdefault(group, []).append({"key": key, "kind": kind, "offset": off, "count": count,
                                             "unit": layout.UNITS.get(key, "")})
    return render_template("telemetry.html", groups=groups, order=layout.GROUP_ORDER,
                           trailer_fields=layout.TRAILER_FIELDS, total=field_count(), drivers=users_list())


@bp.route("/map")
@login_required
def live_map():
    scope = game_scope()
    game = scope or (request.args.get("game") if request.args.get("game") in ("ats", "ets2") else db.setting("default_game", "ats"))
    return render_template("map.html", drivers=users_list(), game=game, scope=scope)


@bp.route("/overlay")
def overlay():
    token = request.args.get("key", "")
    if not user():
        if not token or not db.val("SELECT id FROM users WHERE agent_token=?", (token,)):
            return redirect(url_for("auth.login", next=request.full_path))
    if not user() and token:
        from flask import g
        from .auth import units_for
        g.units_override = units_for(db.val("SELECT game_pref FROM users WHERE agent_token=?", (token,)) or "both")
    return render_template("overlay.html", solid="solid" in request.args, key=token)


# ------------------------------------------------------------------ jobs / logbook
@bp.route("/jobs")
@login_required
def jobs():
    a = request.args
    where, args = ["1=1"], []
    if a.get("driver", "").isdigit():
        where.append("j.user_id=?"); args.append(int(a["driver"]))
    if a.get("status"):
        where.append("j.status=?"); args.append(a["status"])
    if a.get("q"):
        where.append("(j.cargo LIKE ? OR j.source_city LIKE ? OR j.destination_city LIKE ?)")
        args += [f"%{a['q']}%"] * 3
    if game_scope() or a.get("game"):
        where.append("j.game=?"); args.append(game_scope() or a["game"])
    page = max(1, int(a.get("page", "1")) if a.get("page", "1").isdigit() else 1)
    total = db.val(f"SELECT COUNT(*) FROM jobs j WHERE {' AND '.join(where)}", tuple(args), 0)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    rows = db.q(f"""SELECT j.*, u.display_name, u.color FROM jobs j JOIN users u ON u.id=j.user_id
                    WHERE {' AND '.join(where)} ORDER BY j.started_at DESC LIMIT 25 OFFSET ?""",  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
                tuple(args) + ((page - 1) * 25,))
    from .ops import demo_counts
    return render_template("jobs.html", rows=rows, total=total, page=page, pages=max(1, (total + 24) // 25),
                           drivers=users_list(), a=a, demo=demo_counts() if can("admin") else (0, 0))


@bp.route("/jobs/<int:jid>")
@login_required
def job_detail(jid):
    j = db.one("""SELECT j.*, u.display_name, u.color, t.brand truck_brand, t.model truck_model, t.plate truck_plate
                  FROM jobs j JOIN users u ON u.id=j.user_id LEFT JOIN trucks t ON t.id=j.truck_id WHERE j.id=?""", (jid,))
    if not j:
        abort(404)
    events = db.q("SELECT * FROM job_events WHERE job_id=? ORDER BY created_at", (jid,))
    points = db.q("SELECT x, z, speed FROM job_points WHERE job_id=? ORDER BY t", (jid,))
    return render_template("job_detail.html", j=j, events=events, points=points)


@bp.route("/jobs/new", methods=["GET", "POST"])
@login_required
def job_new():
    if request.method == "POST":
        u = user()
        uid = opt_id("user_id", "users") if can("dispatcher") else u["id"]
        uid = uid or u["id"]
        km = num("driven_km", 0, 20000)
        if request.form.get("dist_unit") == "mi":
            km = min(20000, km * KM_PER_MI)
        now = time.time()
        needs_review = db.setting("require_manual_approval") == "1" and not can("admin")
        jid = db.insert("jobs", user_id=uid, truck_id=opt_id("truck_id", "trucks"), source="manual",
                        status="pending_review" if needs_review else "delivered", game=txt("game", 8) or "ats",
                        source_city=txt("source_city", 64), source_company=txt("source_company", 64),
                        destination_city=txt("destination_city", 64), destination_company=txt("destination_company", 64),
                        cargo=txt("cargo", 64), cargo_mass=unit_num("cargo_mass", "mass", 0, 200000) if "cargo_mass" in request.form
                        else num("cargo_mass_t", 0, 200) * 1000, planned_km=km,
                        driven_km=km, revenue=num("revenue", 0, 1e7), income=num("revenue", 0, 1e7),
                        fuel_used=unit_num("fuel_used", "vol", 0, 10000), tolls=num("tolls", 0, 1e5), fines=num("fines", 0, 1e6),
                        cargo_damage=num("cargo_damage_pct", 0, 100) / 100, notes=txt("notes", 1000),
                        started_at=now, finished_at=now)
        if not needs_review:
            economy.settle_job(jid)
            flash("Job logged and paid.", "success")
        else:
            for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner')"):
                db.notify(m["id"], f"Manual job #{jid} is waiting for approval.", f"/jobs/{jid}", "warning")
            flash("Job submitted — a manager will review it.", "success")
        return redirect(url_for("pages.job_detail", jid=jid))
    return render_template("job_new.html", drivers=users_list(),
                           trucks=db.q("SELECT * FROM trucks WHERE status NOT IN ('sold','returned') ORDER BY brand"))


@bp.route("/jobs/<int:jid>/review", methods=["POST"])
@login_required(role="admin")
def job_review(jid):
    j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j:
        abort(404)
    action = request.form.get("action")
    if action == "approve" and j["status"] == "pending_review":
        db.update("jobs", jid, status="delivered", reviewed_by=user()["id"])
        economy.settle_job(jid)
        db.notify(j["user_id"], f"Your job #{jid} was approved.", f"/jobs/{jid}", "success")
    elif action == "reject" and j["status"] == "pending_review":
        db.update("jobs", jid, status="rejected", reviewed_by=user()["id"])
        db.notify(j["user_id"], f"Your job #{jid} was rejected.", f"/jobs/{jid}", "error")
    elif action == "clear_flags":
        db.update("jobs", jid, flags="", reviewed_by=user()["id"])
    elif action == "delete":
        db.ex("DELETE FROM job_points WHERE job_id=?", (jid,))
        db.ex("DELETE FROM job_events WHERE job_id=?", (jid,))
        db.ex("UPDATE ledger SET job_id=NULL WHERE job_id=?", (jid,))
        db.ex("UPDATE contracts SET job_id=NULL WHERE job_id=?", (jid,))
        db.ex("DELETE FROM jobs WHERE id=?", (jid,))
        db.audit(user()["id"], "delete job", f"#{jid}")
        flash(f"Job #{jid} deleted (ledger entries kept).", "success")
        return redirect(url_for("pages.jobs"))
    db.audit(user()["id"], f"job {action}", f"#{jid}")
    return redirect(url_for("pages.job_detail", jid=jid))


# ------------------------------------------------------------------ dispatch
@bp.route("/dispatch")
@login_required
def dispatch():
    rows = db.q(f"""SELECT c.*, u.display_name assignee, u.color FROM contracts c
                   LEFT JOIN users u ON u.id=c.assigned_user_id
                   WHERE 1=1{game_sql("c.game")[0]}
                   ORDER BY CASE c.status WHEN 'assigned' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
                   c.deadline IS NULL, c.deadline, c.created_at DESC LIMIT 200""", game_sql()[1])  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    profiles = {}
    if _is_local():                                   # saves live on this PC: only offered to its own browser
        from . import savegame
        for g in ("ats", "ets2"):
            try:
                profiles[g] = savegame.list_profiles(g, current_app.config.get("SAVE_DIRS"))
            except OSError:
                profiles[g] = []
    return render_template("dispatch.html", rows=rows, drivers=users_list(), cargo=cargo_options(),
                           form_game=game_scope() or db.setting("default_game", "ats"), profiles=profiles)


def _is_local():
    import ipaddress
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


@bp.route("/dispatch/<int:cid>/to-game", methods=["POST"])
@login_required
def dispatch_to_game(cid):
    """Write the contract into the game as a new save slot (see fmpro/savegame.py)."""
    from . import savegame
    c = db.one("SELECT * FROM contracts WHERE id=?", (cid,)) or abort(404)
    u = user()
    if not (can("dispatcher") or c["assigned_user_id"] == u["id"]):
        abort(403)
    if not _is_local():
        flash("Sending a contract to the game only works from a browser on the PC where the game is installed.", "error")
        return redirect(url_for("pages.dispatch"))
    if c["status"] not in ("open", "assigned"):
        flash("Only open or assigned contracts can be sent to the game.", "error")
        return redirect(url_for("pages.dispatch"))
    wanted = request.form.get("save", "")
    choices = {s["dir"]: (p, s) for p in savegame.list_profiles(c["game"], current_app.config.get("SAVE_DIRS"))
               for s in p["saves"][:8]}
    if wanted not in choices:
        flash("Pick one of your saves from the list.", "error")
        return redirect(url_for("pages.dispatch"))
    profile, save = choices[wanted]
    lists, xy = savegame.load_static(RES)
    title = f"Fleet Manager Pro - Contract #{cid}"
    try:
        r = savegame.send_contract(c["game"], profile["dir"], save["dir"], c, lists, xy.get(c["game"], {}), title)
    except savegame.SaveError as e:
        flash(f"Couldn't put contract #{cid} into the game: {e}", "error")
        return redirect(url_for("pages.dispatch"))
    except (OSError, ValueError) as e:
        flash(f"Couldn't write the save: {e}", "error")
        return redirect(url_for("pages.dispatch"))
    names = {(x["id"]): x["name"] for x in lists.get("companies", {}).get(c["game"], [])}
    cities = {(x["id"]): x["name"] for x in lists.get("cities", {}).get(c["game"], [])}
    src, dst = r["source"], r["target"]
    db.update("contracts", cid, game_slot=title, sent_at=time.time(),
              source_city_id=src[1], destination_city_id=dst[1],
              source_company=c["source_company"] or "", destination_company=c["destination_company"] or "")
    if c["status"] == "open" and not can("dispatcher"):
        db.update("contracts", cid, status="assigned", assigned_user_id=u["id"])
    db.audit(u["id"], "contract sent to game", f"#{cid} -> {profile['name']}")
    flash(f"Contract #{cid} is in {c['game'].upper()} profile \"{profile['name']}\" as the save \"{title}\". "
          f"In the game: Load game, pick that save, then open the freight market at "
          f"{names.get(src[0], src[0])} in {cities.get(src[1], src[1])}: the job to "
          f"{names.get(dst[0], dst[0])}, {cities.get(dst[1], dst[1])} ({r['km']} km) is waiting there. "
          f"Take it within about {r['hours_to_take']} in-game hours"
          + (f" (it takes the place of the {r['replaces'].replace('_', ' ')} job there). " if r.get("replaces") else ". ")
          +
          f"Your other saves were not changed.", "success")
    return redirect(url_for("pages.dispatch"))


@bp.route("/dispatch/new", methods=["POST"])
@login_required(role="dispatcher")
def dispatch_new():
    def city(field):
        """(city id, city name) from the dropdown, or a typed name."""
        v = request.form.get(field + "_pick", "")
        if "|" in v:
            cid_, name = (x.strip()[:64] for x in v.split("|", 1))
            return (cid_ if re.fullmatch(r"[a-z0-9_]*", cid_) else ""), name
        return "", txt(field, 64)
    source_city_id, source_city = city("source_city")
    dest_id, dest = city("destination_city")
    if not dest:
        flash("Destination city is required.", "error")
        return redirect(url_for("pages.dispatch"))
    hours = num("deadline_hours", 0, 24 * 90)
    assignee = opt_id("assigned_user_id", "users")
    pick = request.form.get("cargo_pick", "")
    cargo_id, cargo_name = "", ""
    if pick == "__other":
        cargo_name = txt("cargo", 64)
    elif "|" in pick:
        cargo_id, cargo_name = (x.strip()[:64] for x in pick.split("|", 1))
        if not re.fullmatch(r"[a-z0-9_.]*", cargo_id):
            cargo_id = ""
    elif not pick:
        cargo_name = txt("cargo", 64)          # older form / API callers
    game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else "ats"

    def company(field):
        v = request.form.get(field + "_pick", "")
        return txt(field, 64) if v in ("__other", "") else v.strip()[:64]
    depots = cargo_options() and _DEPOTS.get(game, {})
    for which, label in (("source", "From"), ("destination", "To")):
        cid_, name_ = (source_city_id, source_city) if which == "source" else (dest_id, dest)
        if cid_ or not name_:
            continue
        same = [c for c in _CITIES.get(game, []) if c[1].lower() == name_.lower()]
        co = company(which + "_company").lower()
        if co and len(same) > 1:
            same = [c for c in same if co in {n.lower() for n in depots.get(c[0], [])}] or same
        if len(same) == 1:
            cid_ = same[0][0]
        elif len(same) > 1:
            flash(f"There is more than one {name_}: " + " / ".join(c[2] for c in same) +
                  f". Pick the right one in the {label} city list.", "error")
            return redirect(url_for("pages.dispatch"))
        if which == "source":
            source_city_id = cid_
        else:
            dest_id = cid_
    picked = {}
    for field, city_id, city_name, label in (("source_company", source_city_id, source_city, "From"),
                                             ("destination_company", dest_id, dest, "To")):
        name = picked[field] = company(field)
        if not name:
            continue
        if not city_name:
            flash(f"Pick the {label} city first: only companies in that city can be chosen.", "error")
            return redirect(url_for("pages.dispatch"))
        if city_id in depots and name.lower() not in {n.lower() for n in depots[city_id]}:
            flash(f"{name} has no depot in {city_name}. Pick one of the companies listed for that city.", "error")
            return redirect(url_for("pages.dispatch"))
    cid = db.insert("contracts", source_company=picked["source_company"],
                    destination_company=picked["destination_company"], title=txt("title", 100) or f"Load to {dest}", game=game,
                    source_city=source_city, destination_city=dest, source_city_id=source_city_id,
                    destination_city_id=dest_id, cargo=cargo_name, cargo_id=cargo_id,
                    min_mass_t=num("min_mass_t", 0, 200), bonus=num("bonus", 0, 1e7),
                    priority=request.form.get("priority") if request.form.get("priority") in ("low", "normal", "high", "urgent") else "normal",
                    deadline=time.time() + hours * 3600 if hours else None, assigned_user_id=assignee,
                    status="assigned" if assignee else "open", notes=txt("notes", 1000),
                    created_by=user()["id"], created_at=time.time())
    if assignee:
        db.notify(assignee, f"New dispatch: {dest} — check Dispatch.", "/dispatch", "info")
    economy.webhook("event", f"📋 New contract #{cid}: **{cargo_name or 'any cargo'}** → **{dest}** "
                             f"(bonus {db.setting('currency', '$')}{num('bonus', 0, 1e7):,.0f})")
    flash("Contract posted.", "success")
    return redirect(url_for("pages.dispatch"))


@bp.route("/dispatch/<int:cid>/<action>", methods=["POST"])
@login_required
def dispatch_action(cid, action):
    c = db.one("SELECT * FROM contracts WHERE id=?", (cid,))
    if not c:
        abort(404)
    u = user()
    if action == "claim" and c["status"] == "open":
        from . import compliance
        hold = compliance.holds(u["id"], c["game"])
        if hold:
            flash("Dispatch blocked: " + "; ".join(hold), "error")
            return redirect(url_for("pages.dispatch"))
        db.update("contracts", cid, assigned_user_id=u["id"], status="assigned")
    elif action == "release" and c["assigned_user_id"] == u["id"] and c["status"] == "assigned":
        db.update("contracts", cid, assigned_user_id=None, status="open")
    elif action in ("cancel", "reopen", "complete") and can("dispatcher"):
        st = {"cancel": "cancelled", "reopen": "open", "complete": "completed"}[action]
        db.update("contracts", cid, status=st, completed_at=time.time() if st == "completed" else None)
    else:
        abort(403)
    return redirect(url_for("pages.dispatch"))


# ------------------------------------------------------------------ fleet
MAINT_PRICES = {"service": 800, "tyres": 2400, "inspection": 150, "upgrade": 0, "wash": 40}   # defaults; Settings > Economy


@bp.route("/fleet")
@login_required
def fleet():
    trucks = db.q("""SELECT t.*, u.display_name driver, g.city garage,
                     (SELECT COUNT(*) FROM jobs WHERE truck_id=t.id AND status='delivered') jobs
                     FROM trucks t LEFT JOIN users u ON u.id=t.assigned_user_id LEFT JOIN garages g ON g.id=t.garage_id
                     WHERE 1=1""" + truck_sql("t.game")[0] + """ ORDER BY t.status IN ('sold','returned'), t.brand, t.model""", truck_sql()[1])  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    trailers = db.q("""SELECT tr.*, g.city garage FROM trailers tr LEFT JOIN garages g ON g.id=tr.garage_id
                       ORDER BY tr.status='sold', tr.name""")
    garages = db.q("""SELECT g.*, (SELECT COUNT(*) FROM trucks WHERE garage_id=g.id AND status NOT IN ('sold','returned')) used
                      FROM garages g WHERE 1=1""" + game_sql("g.game")[0] + """ ORDER BY city""", game_sql()[1])  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    maint = db.q("""SELECT m.*, t.brand, t.model, t.plate, u.display_name FROM maintenance m
                    LEFT JOIN trucks t ON t.id=m.truck_id LEFT JOIN users u ON u.id=m.user_id
                    ORDER BY m.created_at DESC LIMIT 15""")
    rate = db.setting_f("repair_cost_per_pct", 40)
    spent = {r["truck_id"]: r["s"] for r in db.q("SELECT truck_id, SUM(cost) s FROM maintenance GROUP BY truck_id")}
    for t in trucks:
        wear = [t["wear_engine"] or 0, t["wear_transmission"] or 0, t["wear_cabin"] or 0, t["wear_chassis"] or 0, t["wear_wheels"] or 0]
        t["since_service"] = max(0, (t["odometer"] or 0) - (t["last_service_odo"] or 0))
        t["service_due"] = t["since_service"] >= (t["service_interval_km"] or 50000)
        t["wear_max"] = max(wear)
        t["wear_total"] = sum(wear) * 100                    # same measure the in-game repair charge uses
        t["repair_est"] = economy.repair_price(t["id"])
        t["maint_spent"] = spent.get(t["id"]) or 0
    maint_prices = {k: db.setting_f("maint_price_" + k, d) for k, d in MAINT_PRICES.items()}
    from .ops import garage_options, vehicle_options
    return render_template("fleet.html", vehicles=vehicle_options(), trucks=trucks, trailers=trailers, garages=garages, maint=maint,
                           drivers=users_list(), garage_list=garage_options(), garage_sizes=GARAGE_SIZES,
                           balance=economy.balance(), maint_prices=maint_prices, repair_rate=rate,
                           form_game=game_scope() or db.setting("default_game", "ats"))


@bp.route("/fleet/truck/<int:tid>")
@login_required
def truck_detail(tid):
    t = db.one("""SELECT t.*, u.display_name driver, g.city garage FROM trucks t
                  LEFT JOIN users u ON u.id=t.assigned_user_id LEFT JOIN garages g ON g.id=t.garage_id WHERE t.id=?""", (tid,))
    if not t:
        abort(404)
    jobs_ = db.q("""SELECT j.*, u.display_name FROM jobs j JOIN users u ON u.id=j.user_id
                    WHERE truck_id=? ORDER BY started_at DESC LIMIT 30""", (tid,))
    maint = db.q("SELECT * FROM maintenance WHERE truck_id=? ORDER BY created_at DESC", (tid,))
    agg = db.one("""SELECT COUNT(*) n, COALESCE(SUM(driven_km),0) km, COALESCE(SUM(revenue),0) revenue,
                    COALESCE(SUM(fuel_used),0) fuel, COALESCE(SUM(tolls),0) tolls FROM jobs
                    WHERE truck_id=? AND status='delivered'""", (tid,))
    agg["maint"] = db.val("SELECT SUM(cost) FROM maintenance WHERE truck_id=?", (tid,), 0)
    from .ops import dealer_price
    lease = db.one("SELECT * FROM leases WHERE truck_id=? ORDER BY status NOT IN ('active','matured'), id DESC LIMIT 1", (tid,))
    return render_template("truck.html", new_price=dealer_price(t["brand"], t["model"]), t=t, lease=lease, jobs=jobs_, maint=maint, agg=agg, drivers=users_list(),
                           garages=db.q("SELECT * FROM garages ORDER BY city"))


@bp.route("/fleet/truck/save", methods=["POST"])
@login_required(role="admin")
def truck_save():
    tid = request.form.get("id", "")
    cols = dict(brand=txt("brand", 64), model=txt("model", 64), plate=txt("plate", 32),
                plate_country=txt("plate_country", 64), garage_id=opt_id("garage_id", "garages"),
                assigned_user_id=opt_id("assigned_user_id", "users"),
                service_interval_km=unit_num("service_interval_km", "dist", 1000, 1e6, 50000), notes=txt("notes", 1000))
    if request.form.get("fuel_capacity", "").strip():
        cols["fuel_capacity"] = unit_num("fuel_capacity", "vol", 0, 5000)
    if not cols["brand"] or not cols["model"]:
        flash("Brand and model are required.", "error")
        return back("pages.fleet")
    if tid.isdigit():
        db.update("trucks", int(tid), **cols)
        flash("Truck updated.", "success")
        return redirect(url_for("pages.truck_detail", tid=int(tid)))
    price = num("purchase_price", 0, 1e8)
    if not affordable(price, f"the {cols['brand']} {cols['model']}"):
        return back("pages.fleet")
    new = db.insert("trucks", purchase_price=price, odometer=unit_num("odometer", "dist", 0, 1e8), created_at=time.time(), **cols)
    if price:
        economy.post("truck purchase", -price, f"Bought {cols['brand']} {cols['model']} ({cols['plate']})")
    flash("Truck added to the fleet.", "success")
    return redirect(url_for("pages.truck_detail", tid=new))


@bp.route("/fleet/truck/<int:tid>/sell", methods=["POST"])
@login_required(role="admin")
def truck_sell(tid):
    t = db.one("SELECT * FROM trucks WHERE id=?", (tid,))
    if not t or t["status"] in ("sold", "returned"):
        abort(404)
    if t.get("leased"):
        flash("This truck is leased: buy it out or return it from its lease page instead of selling it.", "error")
        return redirect(url_for("pages.truck_detail", tid=tid))
    price = num("price", 0, 1e8)
    db.update("trucks", tid, status="sold", assigned_user_id=None)
    if price:
        economy.post("truck sale", price, f"Sold {t['brand']} {t['model']} ({t['plate']})")
    flash("Truck sold.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/fleet/trailer/save", methods=["POST"])
@login_required(role="admin")
def trailer_save():
    name = txt("name", 64)
    from .ops import vehicle_options
    pick = request.form.get("body_type_pick", "")
    types = {tid: label for g in vehicle_options().values() for tid, label in g.get("trailer_types", [])}
    body = types.get(pick) or (txt("body_type", 64) if pick in ("", "__other") else "")
    if not name:
        flash("Trailer name is required.", "error")
        return redirect(url_for("pages.fleet"))
    price = num("purchase_price", 0, 1e8)
    if not affordable(price, f"the trailer {name}"):
        return redirect(url_for("pages.fleet"))
    db.insert("trailers", name=name, body_type=body, brand=txt("brand", 64), plate=txt("plate", 32),
              garage_id=opt_id("garage_id", "garages"), purchase_price=price, created_at=time.time())
    if price:
        economy.post("trailer purchase", -price, f"Bought trailer {name}")
    flash("Trailer added.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/fleet/garage/save", methods=["POST"])
@login_required(role="admin")
def garage_save():
    from .ops import garage_options
    game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else "ats"
    pick = request.form.get("city_pick", "")
    city_id, city = "", ""
    if "|" in pick:                                    # from the game's list of garage locations
        city_id, city = (x.strip()[:64] for x in pick.split("|", 1))
        known = {r[0]: r[1] for r in garage_options().get(game, [])}
        if city_id not in known:
            flash("Pick a garage location from the list for that game.", "error")
            return redirect(url_for("pages.fleet"))
        city = known[city_id]
    else:                                              # "Other": a modded map or an older form
        city = txt("city", 64)
    if not city:
        flash("Pick the garage's city.", "error")
        return redirect(url_for("pages.fleet"))
    size = request.form.get("size") if request.form.get("size") in ("small", "medium", "large") else "small"
    slots = GARAGE_SIZES[size][0]
    gid = request.form.get("id", "")
    if gid.isdigit():                                  # edit
        g = db.one("SELECT * FROM garages WHERE id=?", (int(gid),)) or abort(404)
        used = db.val("SELECT COUNT(*) FROM trucks WHERE garage_id=? AND status NOT IN ('sold','returned')", (g["id"],), 0)
        if used > slots:
            flash(f"{used} trucks are based there; a {size} garage holds {slots}. Move trucks first.", "error")
            return redirect(url_for("pages.fleet"))
        dupe = db.val("SELECT id FROM garages WHERE game=? AND city_id=? AND city_id!='' AND id!=?", (game, city_id, g["id"]))
        if dupe:
            flash(f"You already have a garage in {city}.", "error")
            return redirect(url_for("pages.fleet"))
        db.update("garages", g["id"], city=city, city_id=city_id, game=game, size=size, slots=slots,
                  upkeep_per_week=num("upkeep_per_week", 0, 1e6, g["upkeep_per_week"]), notes=txt("notes", 300))
        db.audit(user()["id"], "garage edited", f"#{g['id']} {city} {size}")
        flash(f"Garage in {city} updated.", "success")
        return redirect(url_for("pages.fleet"))
    if city_id and db.val("SELECT id FROM garages WHERE game=? AND city_id=?", (game, city_id)):
        flash(f"You already have a garage in {city}. Edit it to change its size.", "error")
        return redirect(url_for("pages.fleet"))
    price = num("purchase_price", 0, 1e8, GARAGE_SIZES[size][1])
    if not affordable(price, f"a {size} garage in {city}"):
        return redirect(url_for("pages.fleet"))
    db.insert("garages", city=city, city_id=city_id, game=game, size=size, slots=slots, notes=txt("notes", 300),
              upkeep_per_week=num("upkeep_per_week", 0, 1e6, GARAGE_SIZES[size][2]), purchase_price=price, created_at=time.time())
    if price:
        economy.post("garage purchase", -price, f"{size.title()} garage in {city}")
    flash(f"Garage in {city} added.", "success")
    return redirect(url_for("pages.fleet"))


@bp.route("/fleet/maintenance", methods=["POST"])
@login_required(role="admin")
def maintenance_add():
    tid = opt_id("truck_id", "trucks")
    kind = request.form.get("kind") if request.form.get("kind") in ("service", "repair", "tyres", "inspection", "upgrade", "wash") else "service"
    # every price comes from Settings > Economy; a cost sent with the form is never used
    if kind in MAINT_PRICES:
        cost = round(db.setting_f("maint_price_" + kind, MAINT_PRICES[kind]), 2)
    else:                                          # repair: the truck's wear x the repair rate
        cost = economy.repair_price(tid)
    odo = db.val("SELECT odometer FROM trucks WHERE id=?", (tid,), 0) if tid else 0
    db.insert("maintenance", truck_id=tid, kind=kind, cost=cost, odometer=odo, notes=txt("notes", 500),
              user_id=user()["id"], created_at=time.time())
    if tid and kind == "service":
        db.ex("UPDATE trucks SET last_service_odo=odometer WHERE id=?", (tid,))
    if cost:
        economy.post("maintenance", -cost, f"{kind.title()} — truck #{tid}" if tid else kind.title())
    flash("Maintenance recorded.", "success")
    return back("pages.fleet")


# ------------------------------------------------------------------ finance
@bp.route("/finance")
@login_required
def finance():
    period = request.args.get("period", "month")
    since = period_start(period)
    cats = db.q("""SELECT category, SUM(amount) total, COUNT(*) n FROM ledger WHERE account='company' AND created_at>=?
                   GROUP BY category ORDER BY total DESC""", (since,))
    income = sum(c["total"] for c in cats if c["total"] > 0)
    expense = sum(c["total"] for c in cats if c["total"] < 0)
    entries = db.q("""SELECT l.*, u.display_name FROM ledger l LEFT JOIN users u ON u.id=l.user_id
                      WHERE l.account='company' ORDER BY l.created_at DESC, l.id DESC LIMIT 60""")
    trend = db.q("""SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') d,
                    SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) inc, SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END) exp
                    FROM ledger WHERE account='company' AND created_at>=? AND category NOT IN ('capital','loan')
                    GROUP BY d ORDER BY d""", (time.time() - 30 * DAY,))
    loans = db.q("SELECT * FROM loans ORDER BY status='paid', created_at DESC")
    payroll = db.q("""SELECT u.id, u.display_name, u.color, COALESCE(SUM(l.amount),0) earned FROM users u
                      LEFT JOIN ledger l ON l.user_id=u.id AND l.account='driver' AND l.created_at>=?
                      WHERE u.status!='removed' GROUP BY u.id ORDER BY earned DESC""", (since,))
    return render_template("finance.html", cats=cats, income=income, expense=expense, entries=entries,
                           trend=trend, loans=loans, plans=economy.LOAN_PLANS, balance=economy.balance(),
                           period=period, payroll=payroll)


@bp.route("/finance/entry", methods=["POST"])
@login_required(role="admin")
def finance_entry():
    amount = num("amount", 0, 1e9)
    if request.form.get("direction") == "expense":
        amount = -amount
    cat = txt("category", 40).lower() or "other"
    if amount:
        db.insert("ledger", account="company", category=cat, amount=round(amount, 2), manual=1, entered_by=user()["id"],
                  description=txt("description", 200) or "Manual entry", created_at=time.time())
    db.audit(user()["id"], "ledger entry", f"{cat} {amount}")
    flash("Entry recorded.", "success")
    return redirect(url_for("pages.finance"))


@bp.route("/finance/loan", methods=["POST"])
@login_required(role="owner")
def finance_loan():
    plan = request.form.get("plan")
    amount = num("amount", 0, 5_000_000)
    if plan not in economy.LOAN_PLANS or amount < 10_000:
        flash("Choose a plan and an amount of at least 10,000.", "error")
    elif db.val("SELECT COUNT(*) FROM loans WHERE status='active'", (), 0) >= 3:
        flash("You already have 3 active loans.", "error")
    else:
        economy.take_loan(plan, amount)
        flash("Loan approved and paid into the company account.", "success")
    return redirect(url_for("pages.finance"))


@bp.route("/finance/loan/<int:lid>/payoff", methods=["POST"])
@login_required(role="owner")
def loan_payoff(lid):
    loan = db.one("SELECT * FROM loans WHERE id=? AND status='active'", (lid,))
    if not loan:
        abort(404)
    if economy.balance() < loan["balance"]:
        flash("Not enough money in the company account.", "error")
    else:
        economy.post("loan repayment", -loan["balance"], f"Loan #{lid} paid off early")
        db.update("loans", lid, balance=0, status="paid")
        flash("Loan paid off.", "success")
    return redirect(url_for("pages.finance"))


# ------------------------------------------------------------------ drivers
@bp.route("/drivers")
@login_required
def drivers():
    rows = db.q(f"""SELECT u.*, d.name division, d.color division_color,
                   (SELECT COUNT(*) FROM jobs WHERE user_id=u.id AND status='delivered'{game_sql()[0]}) jobs,
                   (SELECT COALESCE(SUM(driven_km),0) FROM jobs WHERE user_id=u.id AND status='delivered'{game_sql()[0]}) km
                   FROM users u LEFT JOIN divisions d ON d.id=u.division_id WHERE u.status!='removed'
                   ORDER BY CASE u.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'dispatcher' THEN 2 ELSE 3 END,
                   u.display_name""", game_sql()[1] * 2)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    live = HUB.all_live()
    from . import compliance
    for r in rows:
        r["prog"] = economy.progress(r["xp"])
        r["online"] = r["id"] in live
        c = compliance.clocks(r["id"], None, db.setting("default_game", "ats")) if db.setting("hos_enabled") == "1" else None
        r["hours_left"] = c["available"] if c else None
        r["holds"] = compliance.holds(r["id"])
    former = db.val("SELECT COUNT(*) FROM users WHERE status='removed' AND role!='owner' AND username!='former-drivers'", default=0)
    return render_template("drivers.html", rows=rows, divisions=db.q("SELECT * FROM divisions ORDER BY name"), former=former)


@bp.route("/drivers/<int:uid>")
@login_required
def driver_profile(uid):
    p = db.one("""SELECT u.*, d.name division FROM users u LEFT JOIN divisions d ON d.id=u.division_id
                  WHERE u.id=? AND u.status!='removed'""", (uid,))
    if not p:
        abort(404)
    st = db.one("""SELECT COUNT(*) jobs, COALESCE(SUM(driven_km),0) km, COALESCE(SUM(revenue),0) revenue,
                   COALESCE(AVG(score),0) score, COALESCE(SUM(cargo_mass),0) mass, COALESCE(SUM(fuel_used),0) fuel,
                   COALESCE(MAX(driven_km),0) longest, COALESCE(SUM(fines),0) fines
                   FROM jobs WHERE user_id=? AND status='delivered'""" + game_sql()[0], (uid,) + game_sql()[1])  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    gc, ga = game_sql()
    fav = db.q(f"""SELECT cargo, COUNT(*) n FROM jobs WHERE user_id=? AND status='delivered' AND cargo!=''{gc}
                  GROUP BY cargo ORDER BY n DESC LIMIT 5""", (uid,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    cities = db.q(f"""SELECT destination_city c, COUNT(*) n FROM jobs WHERE user_id=? AND status='delivered'{gc}
                     GROUP BY c ORDER BY n DESC LIMIT 5""", (uid,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    recent = db.q("SELECT * FROM jobs WHERE user_id=?" + gc + " ORDER BY started_at DESC LIMIT 10", (uid,) + ga)  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    ach = [a for a in economy.achievement_progress(uid) if a["earned_at"]]
    return render_template("driver.html", p=p, st=st, fav=fav, cities=cities, recent=recent, ach=ach,
                           prog=economy.progress(p["xp"]), wallet=economy.balance("driver", uid), can_token=_manages(p),
                           divisions=db.q("SELECT * FROM divisions ORDER BY name"), live=HUB.get(uid))


@bp.route("/drivers/new", methods=["POST"])
@login_required(role="admin")
def driver_new():
    un, pw = txt("username", 32), request.form.get("password", "")
    role = request.form.get("role", "driver")
    if role not in ROLES or role == "owner" or (role == "admin" and not can("owner")):
        role = "driver"
    if not re.fullmatch(r"[A-Za-z0-9_.\- ]{2,32}", un or ""):
        flash("Username: 2-32 letters, numbers, spaces, _ . -", "error")
    elif not valid_password(pw, un):
        flash(PASSWORD_RULE, "error")
    elif db.val("SELECT id FROM users WHERE username=?", (un,)):
        flash("That username is taken.", "error")
    else:
        color = txt("color", 7)
        uid = create_user(un, txt("display_name", 48), pw, role=role, division_id=opt_id("division_id", "divisions"),
                          color=color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#c8102e")
        db.audit(user()["id"], "add member", un)
        flash(f"{un} added.", "success")
        return redirect(url_for("pages.driver_profile", uid=uid))
    return redirect(url_for("pages.drivers"))


@bp.route("/drivers/<int:uid>/edit", methods=["POST"])
@login_required
def driver_edit(uid):
    me = user()
    p = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not p:
        abort(404)
    is_self = me["id"] == uid
    manager = can("admin") and (role_level(me["role"]) > role_level(p["role"]) or me["role"] == "owner")
    if not (is_self or manager):
        abort(403)
    cols = dict(display_name=txt("display_name", 48) or p["display_name"], bio=txt("bio", 600),
                country=txt("country", 48), discord=txt("discord", 64), steam_id=txt("steam_id", 32),
                truckersmp_id=txt("truckersmp_id", 32))
    color = txt("color", 7)
    if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        cols["color"] = color
    if manager and not is_self:
        role = request.form.get("role", p["role"])
        if role in ROLES and role != "owner" and (role != "admin" or me["role"] == "owner"):
            cols["role"] = role
        st = request.form.get("status", p["status"])
        if st in ("active", "on_leave", "inactive"):
            cols["status"] = st
        cols["division_id"] = opt_id("division_id", "divisions")
        rate = request.form.get("pay_per_km", "").strip()
        cols["pay_per_km"] = num("pay_per_km", 0, 100) if rate else None
        if request.form.get("new_password"):
            if valid_password(request.form["new_password"], p["username"]):
                from werkzeug.security import generate_password_hash
                cols["password_hash"] = generate_password_hash(request.form["new_password"])
            else:
                flash("Password not changed. " + PASSWORD_RULE, "error")
    db.update("users", uid, **cols)
    flash("Profile saved.", "success")
    return redirect(url_for("pages.driver_profile", uid=uid))


def _manages(target):
    """True for the user themself, or a manager who outranks them (the owner outranks everyone)."""
    me = user()
    return me["id"] == target["id"] or (can("admin") and (me["role"] == "owner"
                                                         or role_level(me["role"]) > role_level(target["role"])))


@bp.route("/drivers/<int:uid>/token", methods=["POST"])
@login_required
def driver_token(uid):
    target = db.one("SELECT id, role FROM users WHERE id=?", (uid,)) or abort(404)
    if not _manages(target):
        abort(403)
    db.update("users", uid, agent_token=secrets.token_urlsafe(24))
    flash("New agent token generated — update it on that driver's PC.", "success")
    return redirect(url_for("pages.driver_profile", uid=uid))


# firing a member (/drivers/<id>/fire, old /remove) lives in cleanup.py


@bp.route("/profile")
@login_required
def profile():
    return driver_profile(user()["id"])


@bp.route("/divisions", methods=["POST"])
@login_required(role="admin")
def division_save():
    name = txt("name", 48)
    if request.form.get("delete", "").isdigit():
        did = int(request.form["delete"])
        db.ex("UPDATE users SET division_id=NULL WHERE division_id=?", (did,))
        db.ex("DELETE FROM divisions WHERE id=?", (did,))
    elif name and not db.val("SELECT id FROM divisions WHERE name=?", (name,)):
        color = txt("color", 7)
        db.insert("divisions", name=name, description=txt("description", 200),
                  color=color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#c8102e")
    return redirect(url_for("pages.drivers"))


# ------------------------------------------------------------------ recruitment
@bp.route("/apply", methods=["GET", "POST"])
def apply():
    if db.setting("recruitment_open") != "1":
        return render_template("apply.html", closed=True)
    if request.method == "POST":
        name, contact = txt("name", 48), txt("contact", 100)
        if request.form.get("website"):  # honeypot
            return render_template("apply.html", done=True)
        if not name or not contact:
            return render_template("apply.html", error="Name and contact are required.", f=request.form)
        recent = db.val("SELECT COUNT(*) FROM applications WHERE created_at>?", (time.time() - 3600,), 0)
        if recent > 30:
            return render_template("apply.html", error="Too many applications right now, try later.", f=request.form)
        age = int(num("age", 0, 120))
        db.insert("applications", name=name, contact=contact, discord=txt("discord", 64), age=age or None,
                  country=txt("country", 48), games=",".join(g for g in request.form.getlist("games") if g in ("ats", "ets2")),
                  experience=txt("experience", 1000), message=txt("message", 2000), created_at=time.time())
        for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner')"):
            db.notify(m["id"], f"New driver application from {name}.", "/recruitment", "info")
        economy.webhook("event", f"📝 New driver application from **{name}**")
        return render_template("apply.html", done=True)
    return render_template("apply.html")


@bp.route("/recruitment")
@login_required(role="admin")
def recruitment():
    rows = db.q("""SELECT a.*, u.display_name reviewer FROM applications a LEFT JOIN users u ON u.id=a.reviewed_by
                   ORDER BY a.status='pending' DESC, a.created_at DESC""")
    synced = float(db.setting("recruit_synced", "0") or 0)
    return render_template("recruitment.html", rows=rows, divisions=db.q("SELECT * FROM divisions ORDER BY name"),
                           apply_url=db.setting("recruit_url", ""), synced=synced or None,
                           sync_error=db.setting("recruit_error", ""))


@bp.route("/recruitment/sync", methods=["POST"])
@login_required(role="admin")
def recruitment_sync():
    from . import recruit
    new, err = recruit.sync()
    if err:
        flash(f"Couldn't reach the online Apply page: {err}", "error")
    else:
        flash(f"{new} new application{'s' if new != 1 else ''} collected." if new else "No new applications.", "success")
    return redirect(url_for("pages.recruitment"))


@bp.route("/recruitment/<int:aid>", methods=["POST"])
@login_required(role="admin")
def recruitment_review(aid):
    a = db.one("SELECT * FROM applications WHERE id=?", (aid,))
    if not a:
        abort(404)
    action = request.form.get("action")
    if action in ("accepted", "rejected", "interview", "pending"):
        db.update("applications", aid, status=action, notes=txt("notes", 1000), reviewed_by=user()["id"],
                  reviewed_at=time.time())
        if action == "accepted" and request.form.get("create_account") == "1":
            un = re.sub(r"[^A-Za-z0-9_.\-]", "", a["name"])[:24] or f"driver{aid}"
            base, n = un, 1
            while db.val("SELECT id FROM users WHERE username=?", (un,)):
                n += 1
                un = f"{base}{n}"
            pw = secrets.token_urlsafe(6)
            create_user(un, a["name"], pw, role="trainee", division_id=opt_id("division_id", "divisions"),
                        discord=a["discord"] or "", country=a["country"] or "")
            flash(f"Account created — username {un}, temporary password {pw} (shown once; send it to them).", "success")
        else:
            flash(f"Application marked {action}.", "success")
    return redirect(url_for("pages.recruitment"))


# ------------------------------------------------------------------ events / convoys
@bp.route("/events")
@login_required
def events():
    now = time.time()
    rows = db.q(f"""SELECT e.*, u.display_name host,
                   (SELECT COUNT(*) FROM event_rsvps WHERE event_id=e.id AND status='going') going
                   FROM events e LEFT JOIN users u ON u.id=e.created_by WHERE 1=1{game_sql("e.game")[0]}
                   ORDER BY e.start_at >= ? DESC,
                   CASE WHEN e.start_at >= ? THEN e.start_at END ASC, e.start_at DESC""", game_sql()[1] + (now - 3 * 3600, now - 3 * 3600))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    mine = {r["event_id"]: r["status"] for r in db.q("SELECT * FROM event_rsvps WHERE user_id=?", (user()["id"],))}
    return render_template("events.html", rows=rows, mine=mine)


@bp.route("/events/<int:eid>")
@login_required
def event_detail(eid):
    e = db.one("SELECT e.*, u.display_name host FROM events e LEFT JOIN users u ON u.id=e.created_by WHERE e.id=?", (eid,))
    if not e:
        abort(404)
    rsvps = db.q("""SELECT r.*, u.display_name, u.color, u.xp FROM event_rsvps r JOIN users u ON u.id=r.user_id
                    WHERE r.event_id=? ORDER BY r.status, u.display_name""", (eid,))
    mine = next((r["status"] for r in rsvps if r["user_id"] == user()["id"]), None)
    return render_template("event.html", e=e, rsvps=rsvps, mine=mine, open_edit=request.args.get("edit") == "1")


EVENT_KINDS = ("convoy", "meeting", "training", "public", "race")


def _event_form():
    """Read and check the event form. Returns (fields, None) or (None, error message)."""
    title = txt("title", 100)
    try:
        start = time.mktime(time.strptime(txt("start_at", 32), "%Y-%m-%dT%H:%M"))
    except ValueError:
        return None, "Pick a date and time."
    if not title:
        return None, "Title is required."
    kind = request.form.get("kind") if request.form.get("kind") in EVENT_KINDS else "convoy"
    game = request.form.get("game") if request.form.get("game") in ("ats", "ets2") else "ats"
    return dict(title=title, kind=kind, game=game, description=txt("description", 3000), start_at=start,
                meetup=txt("meetup", 100), departure=txt("departure", 100), destination=txt("destination", 100),
                server=txt("server", 64), dlc=txt("dlc", 200), min_level=int(num("min_level", 0, 1000)),
                max_slots=int(num("max_slots", 0, 1000)), xp_reward=int(num("xp_reward", 0, 100000))), None


@bp.route("/events/save", methods=["POST"])
@login_required(role="dispatcher")
def event_save():
    f, err = _event_form()
    if err:
        flash(err, "error")
        return redirect(url_for("pages.events"))
    title, start = f["title"], f["start_at"]
    eid = db.insert("events", created_by=user()["id"], created_at=time.time(), **f)
    economy.webhook("event", f"📅 **{title}** — {time.strftime('%a %b %d %H:%M', time.localtime(start))} "
                             f"{txt('departure', 100)} → {txt('destination', 100)}")
    for m in db.q("SELECT id FROM users WHERE status='active'"):
        db.notify(m["id"], f"New event: {title}", f"/events/{eid}", "info")
    flash("Event created.", "success")
    return redirect(url_for("pages.event_detail", eid=eid))


@bp.route("/events/<int:eid>/edit", methods=["POST"])
@login_required(role="dispatcher")
def event_edit(eid):
    e = db.one("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        abort(404)
    f, err = _event_form()
    if err:
        flash(err, "error")
        return redirect(url_for("pages.event_detail", eid=eid, edit=1))
    changed = [k for k, v in f.items() if e.get(k) != v]
    if not changed:
        flash("Nothing changed.", "info")
        return redirect(url_for("pages.event_detail", eid=eid))
    db.update("events", eid, **f)
    db.audit(user()["id"], "event edited", f"#{eid}: " + ", ".join(changed))
    if any(k in changed for k in ("start_at", "meetup", "departure", "destination", "server", "game", "title")):
        for r in db.q("SELECT user_id FROM event_rsvps WHERE event_id=? AND status IN ('going','maybe')", (eid,)):
            db.notify(r["user_id"], f"Event updated: {f['title']} - check the time and route", f"/events/{eid}", "info")
    msg = "Event updated."
    if "xp_reward" in changed and e["status"] == "completed":
        msg += " XP already awarded for attendance isn't changed."
    flash(msg, "success")
    return redirect(url_for("pages.event_detail", eid=eid))


@bp.route("/events/<int:eid>/rsvp", methods=["POST"])
@login_required
def event_rsvp(eid):
    e = db.one("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        abort(404)
    st = request.form.get("status")
    if st not in ("going", "maybe", "not_going"):
        abort(400)
    if e["status"] != "scheduled":
        flash("This event is no longer taking sign-ups.", "error")
        return redirect(url_for("pages.event_detail", eid=eid))
    if st != "not_going" and e["min_level"] and economy.level_for(user()["xp"] or 0) < e["min_level"]:
        flash(f"This event needs level {e['min_level']} or higher.", "error")
        return redirect(url_for("pages.event_detail", eid=eid))
    if st == "going" and e["max_slots"]:
        going = db.val("SELECT COUNT(*) FROM event_rsvps WHERE event_id=? AND status='going' AND user_id!=?",
                       (eid, user()["id"]), 0)
        if going >= e["max_slots"]:
            flash("This event is full.", "error")
            return redirect(url_for("pages.event_detail", eid=eid))
    db.ex("""INSERT INTO event_rsvps(event_id, user_id, status, created_at) VALUES (?,?,?,?)
             ON CONFLICT(event_id, user_id) DO UPDATE SET status=excluded.status""", (eid, user()["id"], st, time.time()))
    return redirect(url_for("pages.event_detail", eid=eid))


@bp.route("/events/<int:eid>/manage", methods=["POST"])
@login_required(role="dispatcher")
def event_manage(eid):
    e = db.one("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        abort(404)
    action = request.form.get("action")
    if action == "complete":
        if e["status"] == "cancelled":
            flash("A cancelled event can't be completed.", "error")
            return redirect(url_for("pages.event_detail", eid=eid))
        attended = {int(x) for x in request.form.getlist("attended") if x.isdigit()}
        for r in db.q("SELECT * FROM event_rsvps WHERE event_id=?", (eid,)):
            now_att = r["user_id"] in attended
            paid = r["xp_awarded"] or 0
            if now_att and not paid and e["xp_reward"]:          # pay once, whatever happens to the ticks later
                economy.add_xp(r["user_id"], e["xp_reward"], f"event {eid}")
                economy.check_achievements(r["user_id"])
                paid = e["xp_reward"]
            elif not now_att and paid:                          # ticked by mistake: take back exactly what was paid
                economy.add_xp(r["user_id"], -paid, f"event {eid} attendance removed")
                paid = 0
            db.ex("UPDATE event_rsvps SET attended=?, xp_awarded=? WHERE event_id=? AND user_id=?",
                  (int(now_att), paid, eid, r["user_id"]))
        db.update("events", eid, status="completed")
        db.audit(user()["id"], "event completed", f"#{eid}: {len(attended)} attended")
        flash("Attendance saved and XP awarded.", "success")
    elif action == "cancel":
        db.update("events", eid, status="cancelled")
        db.audit(user()["id"], "event cancelled", f"#{eid} {e['title']}")
    elif action == "delete":
        db.ex("DELETE FROM event_rsvps WHERE event_id=?", (eid,))
        db.ex("DELETE FROM events WHERE id=?", (eid,))
        db.audit(user()["id"], "event deleted", f"#{eid} {e['title']}")
        return redirect(url_for("pages.events"))
    return redirect(url_for("pages.event_detail", eid=eid))


# ------------------------------------------------------------------ leaderboard & achievements
METRICS = {
    "km": ("Distance", "SUM(j.driven_km)"), "revenue": ("Revenue", "SUM(j.revenue)"),
    "jobs": ("Deliveries", "COUNT(*)"), "mass": ("Tonnage", "SUM(j.cargo_mass)/1000.0"),
    "score": ("Avg. score", "AVG(j.score)"), "xp": ("XP earned", "SUM(j.xp)"),
    "realism": ("Realism", "SUM(j.driven_km * (j.score/100.0) * (j.score/100.0) * (1 - j.cargo_damage))"),
}


@bp.route("/leaderboard")
@login_required
def leaderboard():
    metric = request.args.get("metric", "km")
    metric = metric if metric in METRICS else "km"
    period = request.args.get("period", "month")
    game = game_scope() or request.args.get("game", "")
    args = [period_start(period)]
    gsql = ""
    if game in ("ats", "ets2"):
        gsql = "AND j.game=?"
        args.append(game)
    rows = db.q(f"""SELECT u.id, u.display_name, u.color, u.xp, {METRICS[metric][1]} value, COUNT(*) jobs,
                    SUM(j.driven_km) km FROM jobs j JOIN users u ON u.id=j.user_id
                    WHERE j.status='delivered' AND j.finished_at>=? {gsql} AND u.status!='removed'
                    GROUP BY u.id ORDER BY value DESC LIMIT 50""", tuple(args))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    for r in rows:
        r["prog"] = economy.progress(r["xp"])
    return render_template("leaderboard.html", rows=rows, metric=metric, metrics=METRICS, period=period, game=game)


@bp.route("/achievements")
@login_required
def achievements():
    uid = request.args.get("user", "")
    uid = int(uid) if uid.isdigit() else user()["id"]
    who = db.one("SELECT id, display_name FROM users WHERE id=?", (uid,)) or abort(404)
    holders = db.q("""SELECT code, COUNT(*) n FROM user_achievements GROUP BY code""")
    return render_template("achievements.html", items=economy.achievement_progress(uid), who=who,
                           holders={h["code"]: h["n"] for h in holders}, drivers=users_list(),
                           total=db.val("SELECT COUNT(*) FROM users WHERE status!='removed'", (), 1))


# ------------------------------------------------------------------ announcements / messages / notifications
@bp.route("/announcements", methods=["GET", "POST"])
@login_required
def announcements():
    if request.method == "POST":
        if not can("dispatcher"):
            abort(403)
        if request.form.get("delete", "").isdigit():
            db.ex("DELETE FROM announcements WHERE id=?", (int(request.form["delete"]),))
        else:
            title, body = txt("title", 120), txt("body", 5000)
            if title and body:
                level = request.form.get("level") if request.form.get("level") in ("info", "important", "event") else "info"
                db.insert("announcements", title=title, body=body, pinned=int(request.form.get("pinned") == "1"),
                          level=level, author_id=user()["id"], created_at=time.time())
                economy.webhook("event", f"📢 **{title}**\n{body[:500]}")
        return redirect(url_for("pages.announcements"))
    rows = db.q("""SELECT a.*, u.display_name FROM announcements a LEFT JOIN users u ON u.id=a.author_id
                   ORDER BY a.pinned DESC, a.created_at DESC LIMIT 100""")
    return render_template("announcements.html", rows=rows)


@bp.route("/messages", methods=["GET", "POST"])
@login_required
def messages():
    me = user()
    if request.method == "POST":
        body = txt("body", 2000)
        to = request.form.get("to", "")
        if body:
            to_id = int(to) if to.isdigit() and db.val("SELECT id FROM users WHERE id=? AND status!='removed'", (int(to),)) else None
            if to.isdigit() and not to_id:
                abort(404)                         # never turn a private message to a missing member into a public one
            db.insert("messages", from_id=me["id"], to_id=to_id, body=body, created_at=time.time())
            if to_id:
                db.notify(to_id, f"New message from {me['display_name']}", f"/messages?with={me['id']}", "info")
        return redirect(url_for("pages.messages", **({"with": to} if to else {})))
    other = request.args.get("with", "")
    if other.isdigit():
        other = int(other)
        rows = db.q("""SELECT m.*, u.display_name, u.color FROM messages m JOIN users u ON u.id=m.from_id
                       WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?) ORDER BY created_at DESC LIMIT 200""",
                    (me["id"], other, other, me["id"]))
        db.ex("UPDATE messages SET read=1 WHERE to_id=? AND from_id=?", (me["id"], other))
    else:
        other = None
        rows = db.q("""SELECT m.*, u.display_name, u.color FROM messages m JOIN users u ON u.id=m.from_id
                       WHERE to_id IS NULL ORDER BY created_at DESC LIMIT 200""")
    convs = db.q("""SELECT u.id, u.display_name, u.color,
                    (SELECT COUNT(*) FROM messages WHERE from_id=u.id AND to_id=? AND read=0) unread
                    FROM users u WHERE u.id!=? AND u.status!='removed' ORDER BY unread DESC, u.display_name""",
                 (me["id"], me["id"]))
    return render_template("messages.html", rows=list(reversed(rows)), convs=convs, other=other)


@bp.route("/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    db.ex("UPDATE notifications SET read=1 WHERE user_id=?", (user()["id"],))
    return back()


@bp.route("/notifications/<int:nid>/delete", methods=["POST"])
@login_required
def notification_delete(nid):
    """Delete one of your own notifications (someone else's id simply matches nothing)."""
    db.ex("DELETE FROM notifications WHERE id=? AND user_id=?", (nid, user()["id"]))
    if request.headers.get("X-CSRF"):
        return {"ok": True}
    return back()


@bp.route("/notifications/clear", methods=["POST"])
@login_required
def notifications_clear():
    """Delete all of your own notifications."""
    db.ex("DELETE FROM notifications WHERE user_id=?", (user()["id"],))
    if request.headers.get("X-CSRF"):
        return {"ok": True}
    flash("All notifications deleted.", "success")
    return back()


# ------------------------------------------------------------------ settings
# settings stored per metric unit but shown/typed in the viewer's units:
# key -> (imperial value per stored metric value, decimals shown, imperial label, metric label)
UNIT_SETTINGS = {
    "fuel_price": (L_PER_GAL, 2, "Fuel price per gallon", "Fuel price per litre"),
    "driver_pay_per_km": (KM_PER_MI, 2, "Driver pay per mile", "Driver pay per km"),
    "xp_per_km": (KM_PER_MI, 2, "XP per mile", "XP per km"),
    "xp_per_tonne": (0.90718474, 2, "XP per ton (2,000 lb)", "XP per tonne"),
    "speeding_tolerance_kmh": (1 / KM_PER_MI, 1, "Speeding tolerance (mph)", "Speeding tolerance (km/h)"),
}


def unit_settings_view(units):
    """{key: {label, value}} for the Economy tab, in the given units."""
    out = {}
    for key, (f, dec, imp_label, met_label) in UNIT_SETTINGS.items():
        v = db.setting_f(key)
        imp = units == "imperial"
        out[key] = {"label": imp_label if imp else met_label, "value": round(v * f if imp else v, dec)}
    return out


SETTING_FIELDS = {
    "company_name": ("str", 64), "company_tag": ("str", 8), "company_motto": ("str", 120),
    "company_color": ("color", 7), "currency": ("str", 4), "units": ("choice", ("imperial", "metric")),
    "default_game": ("choice", ("ats", "ets2")), "fuel_price": ("num", (0, 100)),
    "driver_pay_per_km": ("num", (0, 100)), "driver_commission_pct": ("num", (0, 100)),
    "xp_per_km": ("num", (0, 100)), "xp_per_tonne": ("num", (0, 100)), "speeding_tolerance_kmh": ("num", (0, 50)),
    "repair_cost_per_pct": ("num", (0, 100000)),
    "maint_price_service": ("num", (0, 1e6)), "maint_price_tyres": ("num", (0, 1e6)),
    "maint_price_inspection": ("num", (0, 1e6)), "maint_price_upgrade": ("num", (0, 1e6)), "maint_price_wash": ("num", (0, 1e6)),
    "dvir_repair_per_defect": ("num", (0, 1e6)),
    "auto_create_trucks": ("bool", None), "auto_charge_repairs": ("bool", None),
    "require_manual_approval": ("bool", None), "agent_jobs_need_approval": ("bool", None), "telemetry_mode": ("choice", ("auto", "demo", "off")),
    "discord_webhook": ("url", 300), "webhook_on_delivery": ("bool", None), "webhook_on_event": ("bool", None),
    "recruitment_open": ("bool", None), "recruitment_text": ("str", 1000),
    "hos_enabled": ("bool", None), "hos_rules_ats": ("choice", ("us70", "us60")), "hos_rules_ets2": ("choice", ("eu",)),
    "hos_fine": ("num", (0, 1e6)), "dvir_required": ("bool", None), "dvir_fine": ("num", (0, 1e6)),
    "inspections_enabled": ("bool", None), "inspection_chance_pct": ("num", (0, 100)), "oos_fine": ("num", (0, 1e6)),
}


@bp.route("/settings", methods=["GET", "POST"])
@login_required(role="admin")
def settings():
    if request.method == "POST":
        section = request.form.get("section", "")
        for key, (kind, arg) in SETTING_FIELDS.items():
            if f"has_{key}" not in request.form and key not in request.form:
                continue
            if kind == "bool":
                db.set_setting(key, "1" if request.form.get(key) == "1" else "0")
            elif kind == "num" and key in UNIT_SETTINGS and request.form.get("units") == "imperial":
                f, dec = UNIT_SETTINGS[key][:2]
                shown = round(db.setting_f(key) * f, dec)
                v = num(key, 0, 1e12, None)
                if v is not None and abs(v - shown) > 1e-9:   # unchanged fields keep their exact metric value
                    db.set_setting(key, max(arg[0], min(arg[1], v / f)))
            elif kind == "num":
                db.set_setting(key, num(key, arg[0], arg[1], db.setting_f(key)))
            elif kind == "choice":
                if request.form.get(key) in arg:
                    db.set_setting(key, request.form[key])
            elif kind == "color":
                if re.fullmatch(r"#[0-9a-fA-F]{6}", request.form.get(key, "")):
                    db.set_setting(key, request.form[key])
            elif kind == "url":
                v = txt(key, arg)
                if v == "" or v.startswith("https://"):
                    db.set_setting(key, v)
                else:
                    flash("Webhook URL must start with https://", "error")
            else:
                db.set_setting(key, txt(key, arg))
        if "telemetry_user_id" in request.form:
            tu = opt_id("telemetry_user_id", "users")
            if tu:
                db.set_setting("telemetry_user_id", tu)
        db.audit(user()["id"], "settings", section)
        flash("Settings saved.", "success")
        return redirect(url_for("pages.settings", tab=section or None))
    tab = request.args.get("tab", "company")
    if tab == "data" and not can("owner"):
        abort(403)
    from .ops import CLEAR_CONFIRM, data_counts
    from .auth import current_units
    return render_template("settings.html", tab=tab, drivers=users_list(), uf=unit_settings_view(current_units()),
                           data_counts=data_counts() if tab == "data" else None, clear_confirm=CLEAR_CONFIRM,
                           lic=licensing.LICENCE.status(),
                           status=HUB.local_status, plugin=plugin_status(),
                           frames=HUB.sampler.frames if HUB.sampler else 0,
                           audit=db.q("""SELECT a.*, u.display_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
                                         ORDER BY a.created_at DESC LIMIT 50"""))


@bp.route("/settings/licence", methods=["POST"])
@login_required(role="admin")
def licence_action():
    lic = licensing.LICENCE
    if request.form.get("action") == "check":
        lic.check()
        flash("Licence checked online." if lic.online else "Couldn't reach the licence server - the current "
              "licence keeps working until it expires.", "success" if lic.online else "error")
    elif request.form.get("action") == "deactivate":
        if not can("owner"):
            abort(403)
        err = lic.deactivate()
        if err:
            flash(f"Couldn't deactivate: {err}", "error")
        else:
            db.audit(user()["id"], "licence deactivated", lic.machine)
            return redirect(url_for("auth.activate"))
    return redirect(url_for("pages.settings", tab="licence"))


@bp.route("/settings/update", methods=["POST"])
@login_required(role="admin")
def update_action():
    action = request.form.get("action")
    if action == "check":
        st = updater.check()
        if st["available"]:
            flash(f"Update available: Fleet Manager Pro {st['version']}.", "success")
        elif st["error"]:
            flash(st["error"], "error")
        else:
            flash(f"You're on the latest version ({updater.current_version()}).", "success")
    elif action == "install":
        msg = updater.install_now()
        if msg is None:
            flash("Updating… Fleet Manager Pro will close and reopen in a few seconds.", "success")
        else:
            ok = msg.startswith("The update was downloaded")
            flash(msg, "success" if ok else "error")
    return redirect(url_for("pages.settings", tab="licence"))


@bp.route("/settings/webhook-test", methods=["POST"])
@login_required(role="admin")
def webhook_test():
    economy.webhook("test", f"✅ Test message from {db.setting('company_name') or 'Fleet Manager Pro'}")
    flash("Test message sent (if the webhook URL is valid it will appear in Discord).", "success")
    return redirect(url_for("pages.settings", tab="integrations"))


# ------------------------------------------------------------------ plugin installer
PLUGIN_DLL = os.path.join(RES, "plugin", "scs-telemetry.dll")
GAME_DIRS = {"ats": "American Truck Simulator", "ets2": "Euro Truck Simulator 2"}


def _steam_libraries():
    roots = []
    try:  # Steam's own install path from the registry
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0]))
    except (ImportError, OSError):
        pass
    for drive in "CDEFGH":
        roots += [f"{drive}:\\Program Files (x86)\\Steam", f"{drive}:\\Program Files\\Steam"]
    libs = []
    for root in roots:
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            libs.append(root)
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
            except OSError:
                pass
    for drive in "DEFGH":
        libs += [f"{drive}:\\SteamLibrary", f"{drive}:\\Steam"]
    seen, out = set(), []
    for lib in libs:
        k = os.path.normcase(lib)
        if k not in seen and os.path.isdir(os.path.join(lib, "steamapps", "common")):
            seen.add(k)
            out.append(lib)
    return out


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()


def _game_dirs():
    out = []
    for lib in _steam_libraries():
        for gid, folder in GAME_DIRS.items():
            out.append((gid, folder, os.path.join(lib, "steamapps", "common", folder)))
    for d in [x for x in (db.setting("extra_game_dirs", "") or "").split("|") if x]:
        name = "Euro Truck Simulator 2" if "euro" in d.lower() else "American Truck Simulator" if "american" in d.lower() else os.path.basename(d.rstrip("\\/"))
        out.append(("custom", name, d))
    seen, res = set(), []
    for gid, folder, gdir in out:
        k = os.path.normcase(os.path.abspath(gdir))
        if k not in seen and os.path.isdir(os.path.join(gdir, "bin", "win_x64")):
            seen.add(k)
            res.append((gid, folder, gdir))
    return res


def plugin_status():
    bundled = _md5(PLUGIN_DLL) if os.path.exists(PLUGIN_DLL) else None
    games = []
    for gid, folder, gdir in _game_dirs():
        pdir = os.path.join(gdir, "bin", "win_x64", "plugins")
        dll = os.path.join(pdir, "scs-telemetry.dll")
        state = "missing"
        if os.path.exists(dll):
            state = "installed" if _md5(dll) == bundled else "different version"
        games.append({"game": gid, "name": folder, "dir": pdir, "state": state})
    return {"bundled": bool(bundled), "games": games}


@bp.route("/settings/plugin-install", methods=["POST"])
@login_required(role="admin")
def plugin_install():
    # this writes a file on the server PC, so it only works from a browser on that PC itself
    try:
        local = __import__("ipaddress").ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        local = False
    if not local:
        flash("Installing the game plugin only works from a browser on the PC that runs Fleet Manager Pro.", "error")
        return redirect(url_for("pages.settings", tab="telemetry"))
    custom = txt("game_dir", 400)
    if custom:
        if not os.path.isdir(os.path.join(custom, "bin", "win_x64")):
            flash("That folder isn't a game folder — pick the one that contains bin\\win_x64 "
                  "(e.g. ...\\steamapps\\common\\American Truck Simulator).", "error")
            return redirect(url_for("pages.settings", tab="telemetry"))
        dirs = [x for x in (db.setting("extra_game_dirs", "") or "").split("|") if x]
        if custom not in dirs:
            db.set_setting("extra_game_dirs", "|".join(dirs + [custom]))
        target = os.path.join(custom, "bin", "win_x64", "plugins")
    else:
        target = request.form.get("dir", "")
    allowed = {g["dir"] for g in plugin_status()["games"]}
    if target not in allowed:
        abort(400)
    try:
        os.makedirs(target, exist_ok=True)
        shutil.copy2(PLUGIN_DLL, os.path.join(target, "scs-telemetry.dll"))
        flash("Telemetry plugin installed. Restart the game and accept the 'advanced SDK features' prompt.", "success")
    except PermissionError:
        flash("Windows blocked the copy. Run Fleet Manager Pro as administrator once, or copy "
              "plugin\\scs-telemetry.dll into that folder yourself.", "error")
    except OSError as e:
        flash(f"Could not install: {e}", "error")
    return redirect(url_for("pages.settings", tab="telemetry"))
