"""VTC Finder: the company's public profile, shown by the licence server where drivers search for a company to join.

The Owner or a Manager writes the profile here (Company, VTC Profile). It is sent with the recruitment sync and is only
public once "List my company" is ticked. The name, tag, motto and colours come from Settings, Company."""
import json
import re
import unicodedata
import urllib.parse

from flask import Blueprint, flash, redirect, render_template, request, url_for

from . import db
from .auth import current_units, login_required, user

bp = Blueprint("vtc", __name__)

# The licence server keeps the same lists (VTC_* in license_server.py; the tests check they match).
GAMES = {"ats": "American Truck Simulator", "ets2": "Euro Truck Simulator 2", "both": "ATS and ETS2"}
REGIONS = {"world": "Worldwide", "na": "North America", "sa": "South America", "eu": "Europe", "as": "Asia",
           "oc": "Oceania", "af": "Africa"}
STYLES = {"realistic": "Realistic", "relaxed": "Relaxed", "competitive": "Competitive", "roleplay": "Roleplay"}
PLAYS = {"single": "Single-player", "convoy": "Convoy", "tmp": "TruckersMP"}
LANGS = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese", "it": "Italian",
         "nl": "Dutch", "pl": "Polish", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian",
         "bg": "Bulgarian", "hr": "Croatian", "sr": "Serbian", "el": "Greek", "tr": "Turkish", "ru": "Russian",
         "uk": "Ukrainian", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian", "fi": "Finnish",
         "sv": "Swedish", "no": "Norwegian", "da": "Danish", "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}
MAX_LANGS, MAX_OFFERS, MAX_QUESTIONS = 5, 6, 5
LIMITS = {"about": 1500, "dlc": 120, "trial": 120, "text": 400, "offer": 80, "question": 120, "link": 200}
LINKS = {"discord": "Discord invite", "website": "Website", "truckersmp": "TruckersMP page"}
LINK_HELP = {"discord": "The Discord invite must start with https://discord.gg/ or https://discord.com/invite/",
             "website": "The website must be a full address starting with https://",
             "truckersmp": "The TruckersMP page must start with https://truckersmp.com/"}
NEEDS_UPDATE = ("Your licence server needs updating before companies can be listed. Everything else keeps working; "
                "your profile is saved and will be sent once the server is up to date.")
_LINK_RX = re.compile(r"https://[A-Za-z0-9.\-]+(?:/[A-Za-z0-9\-._~!$&'()*+,;=:@%/]*)?"
                      r"(?:\?[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*)?(?:#[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*)?")


def clean_text(v, limit, lines=False):
    v = "".join(c for c in str(v or "").replace("\r\n", "\n").replace("\r", "\n")
                if c == "\n" or unicodedata.category(c) not in ("Cc", "Cs", "Co"))
    if lines:
        v = re.sub(r"\n{3,}", "\n\n", "\n".join(" ".join(x.split()) for x in v.split("\n"))).strip()
    else:
        v = " ".join(v.split())
    return v[:limit].strip()


def link_ok(kind, v):
    """The same rule as the licence server: https, and the right site for Discord and TruckersMP."""
    if len(v) > LIMITS["link"] or not _LINK_RX.fullmatch(v):
        return False
    u = urllib.parse.urlsplit(v)
    host = (u.hostname or "").lower()
    if "." not in host or host.startswith((".", "-")) or host.endswith((".", "-")) or ".." in host:
        return False
    if kind == "discord":
        return (host in ("discord.gg", "www.discord.gg") and len(u.path) > 1) or \
               (host in ("discord.com", "www.discord.com") and u.path.startswith("/invite/") and len(u.path) > 8)
    if kind == "truckersmp":
        return host in ("truckersmp.com", "www.truckersmp.com")
    return True


def blank():
    return {"about": "", "games": "both", "region": "world", "languages": [], "styles": [], "plays": [],
            "asks": {"age": 0, "km_week": 0, "mic": False, "dlc": "", "trial": "", "text": ""}, "offers": [],
            "links": {k: "" for k in LINKS}, "questions": []}


def load():
    """The saved profile (missing parts filled in, so an older or damaged setting still opens)."""
    out = blank()
    try:
        d = json.loads(db.setting("vtc_profile", "") or "{}")
    except ValueError:
        d = {}
    if isinstance(d, dict):
        for k, v in out.items():
            if isinstance(d.get(k), type(v)):
                out[k] = {**v, **d[k]} if isinstance(v, dict) else d[k]
    return out


def from_form(f):
    """(profile, problems) from the VTC Profile form."""
    problems = []

    def whole(name, hi):
        raw = str(f.get(name, "")).replace(",", "").strip()
        return min(hi, int(raw)) if raw.isascii() and raw.isdecimal() and len(raw) < 9 else 0

    def many(name, known):
        chosen = f.getlist(name)
        return [k for k in known if k in chosen]

    p = blank()
    p["about"] = clean_text(f.get("about"), LIMITS["about"], True)
    p["games"] = f.get("games") if f.get("games") in GAMES else "both"
    p["region"] = f.get("region") if f.get("region") in REGIONS else "world"
    p["languages"], p["styles"], p["plays"] = many("languages", LANGS), many("styles", STYLES), many("plays", PLAYS)
    if len(p["languages"]) > MAX_LANGS:
        problems.append(f"Choose at most {MAX_LANGS} languages.")
    km = whole("week", 60_000)
    p["asks"] = {"age": whole("age", 99), "km_week": round(km * 1.609344) if f.get("units") == "imperial" else km,
                 "mic": f.get("mic") == "1", "dlc": clean_text(f.get("dlc"), LIMITS["dlc"]),
                 "trial": clean_text(f.get("trial"), LIMITS["trial"]), "text": clean_text(f.get("asks_text"), LIMITS["text"], True)}
    p["offers"] = [x for x in (clean_text(o, LIMITS["offer"]) for o in f.getlist("offer")) if x][:MAX_OFFERS]
    for k in LINKS:
        v = str(f.get(k, "")).strip()[:300]
        p["links"][k] = v
        if v and not link_ok(k, v):
            problems.append(LINK_HELP[k] + " (no spaces, at most 200 letters).")
    texts, kinds, seen = f.getlist("question"), f.getlist("question_type"), set()
    for i, t in enumerate(texts):
        t = clean_text(t, LIMITS["question"])
        if t and t.lower() not in seen:
            seen.add(t.lower())
            p["questions"].append({"q": t, "type": "yesno" if i < len(kinds) and kinds[i] == "yesno" else "text"})
    p["questions"] = p["questions"][:MAX_QUESTIONS]
    return p, problems


def listed():
    return db.setting("vtc_listed", "0") == "1"


def payload():
    """What is sent to the licence server. Not listed: only the questions for the Apply form leave this PC."""
    p = load()
    if not listed():
        return {"listed": False, "questions": p["questions"]}
    from . import theme
    drivers = db.val("SELECT COUNT(*) FROM users WHERE status='active'", (), 0)
    return dict(p, listed=True, tag=db.setting("company_tag", "") or "", motto=db.setting("company_motto", "") or "",
                colours=[theme.clean(db.setting("theme_navy", "")) or theme.DEFAULT_NAVY,
                         theme.clean(db.setting("theme_accent", "")) or theme.DEFAULT_ACCENT], drivers=drivers)


def status():
    """What the licence server last said about this company: {url, finder, listed, hidden, visible, stored, at}."""
    try:
        d = json.loads(db.setting("vtc_status", "") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def note_status(d):
    """Keep the server's answer. Returns False when the server is too old to know about the VTC Finder."""
    url, finder = str(d.get("profile_url") or ""), str(d.get("finder_url") or "")
    if not url.startswith(("http://", "https://")) or not finder.startswith(("http://", "https://")):
        db.set_setting("vtc_status", json.dumps({"old_server": True}))
        return False
    import time
    s = d.get("profile") if isinstance(d.get("profile"), dict) else {}
    db.set_setting("vtc_status", json.dumps({"url": url[:200], "finder": finder[:200], "at": time.time(),
                                             **{k: s.get(k) is True for k in ("listed", "hidden", "visible", "stored")}}))
    return True


def why_not():
    """The reasons the company can't be seen in the VTC Finder (an empty list = it can)."""
    out, s = [], status()
    if not listed():
        out.append("It isn't listed: tick “List my company in the VTC Finder” and save.")
    if not (db.setting("company_name", "") or "").strip():
        out.append("The company has no name (Settings, Company).")
    if not load()["about"]:
        out.append("“About the company” is empty.")
    if s.get("hidden"):
        out.append("It was hidden by the owner of the licence server. Contact the seller.")
    if s.get("old_server"):
        out.append(NEEDS_UPDATE)
    elif not out and not s.get("visible"):
        out.append("It hasn't reached the licence server yet. Save again, or wait up to 10 minutes.")
    return out


@bp.route("/vtc-profile", methods=["GET", "POST"])
@login_required(role="admin")
def page():
    p, want, shown = load(), listed(), False
    if request.method == "POST":
        p, problems = from_form(request.form)
        want = request.form.get("listed") == "1"
        shown = request.form.get("action") == "preview"
        if want and not p["about"]:
            problems.append("Write something in “About the company” before listing it.")
        if want and not (db.setting("company_name", "") or "").strip():
            problems.append("Give the company a name in Settings, Company before listing it.")
        for msg in problems:
            flash(msg, "error")
        if not problems and not shown:
            was = listed()
            db.set_setting("vtc_profile", json.dumps(p))
            db.set_setting("vtc_listed", "1" if want else "0")
            db.audit(user()["id"], "VTC profile", f"listed={want}")
            from . import recruit
            err = recruit.push_profile()
            if err:
                flash(f"Saved on this PC, but not sent yet: {err}", "error")
            else:
                flash("Saved. Your company is in the VTC Finder." if want and not why_not() else
                      "Your company is no longer in the VTC Finder." if was and not want else "Saved.", "success")
            return redirect(url_for("vtc.page"))
        if problems and not shown:
            flash("Nothing was saved. Fix the problem and press Save again.", "error")
    return render_template("vtc_profile.html", p=p, want=want, st=status(), why=why_not(), preview=shown or bool(p["about"]),
                           GAMES=GAMES, REGIONS=REGIONS, STYLES=STYLES, PLAYS=PLAYS, LANGS=LANGS, LIMITS=LIMITS,
                           LINKS=LINKS, MAX_OFFERS=MAX_OFFERS, MAX_QUESTIONS=MAX_QUESTIONS, MAX_LANGS=MAX_LANGS,
                           units=current_units(), drivers=db.val("SELECT COUNT(*) FROM users WHERE status='active'", (), 0))


@bp.route("/find-vtc")
@login_required
def find():
    return render_template("find_vtc.html", st=status(), needs_update=NEEDS_UPDATE, why=why_not(), listed=listed())
