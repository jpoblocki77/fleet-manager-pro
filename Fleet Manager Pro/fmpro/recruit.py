"""Online recruitment: the company's public Apply page lives on the licence server (always online), because this
app only listens on this PC. Every few minutes the app publishes its company name / intro / open-closed state there
and collects new applications, which then appear on the Recruitment page. The server deletes each application once
this app confirms it has saved it."""
import json
import logging
import threading
import time

from . import db, economy, licensing, vtc_profile

EVERY = 10 * 60                     # seconds between collections
_lock = threading.Lock()


def _body():
    lic = licensing.LICENCE
    return {"key": lic.key, "machine": lic.machine, "product": licensing.PRODUCT,
            "company": db.setting("company_name", "") or "", "intro": (db.setting("recruitment_text", "") or "")[:600],
            "open": db.setting("recruitment_open") == "1", "ack": []}


def push_profile():
    """Send the VTC Finder profile now (applications are left for the normal collection). Returns an error or None.
    The first call carries no profile, so a licence server from before the VTC Finder is never sent one."""
    if not licensing.licensed():
        return "Fleet Manager Pro isn't activated."
    with _lock:
        body = _body()
        try:
            if not vtc_profile.note_status(licensing._post("/v1/recruit", body, timeout=10)):
                return vtc_profile.NEEDS_UPDATE
            body["profile"] = vtc_profile.payload()
            vtc_profile.note_status(licensing._post("/v1/recruit", body, timeout=10))
        except licensing.ServerRefused as e:
            return e.message
        except ConnectionError as e:
            return str(e)
    return None


def sync():
    """Publish the Apply page and collect new applications. Returns (new applications, error message or None)."""
    if not licensing.licensed():
        return 0, "Fleet Manager Pro isn't activated, so the online Apply page is off."
    with _lock:
        body = _body()
        new, profile_sent = 0, False
        try:
            for _ in range(6):                               # up to 600 waiting applications per run
                d = licensing._post("/v1/recruit", body, timeout=10)
                saved = []
                for a in d.get("applications") or []:
                    rid = int(a.get("id") or 0)
                    if not rid:
                        continue
                    # the same number AND the same person = already saved (a retry). Older servers reused numbers
                    # once their list was empty, so the number alone used to drop every new application.
                    if not db.val("SELECT id FROM applications WHERE remote_id=? AND name=? AND contact=?",
                                  (rid, str(a.get("name") or "")[:48] or "(no name)", str(a.get("contact") or "")[:100])):
                        _store(a, rid)
                        new += 1
                    saved.append(rid)
                db.set_setting("recruit_url", str(d.get("apply_url") or "")[:200])
                db.set_setting("recruit_code", str(d.get("code") or "")[:16])
                body.pop("profile", None)
                again = bool(saved)
                if vtc_profile.note_status(d) and not profile_sent:
                    profile_sent = True
                    # nothing written here and nothing kept there: nothing to send
                    if db.setting("vtc_profile", "") or (d.get("profile") or {}).get("stored"):
                        body["profile"] = vtc_profile.payload()
                        again = True
                if not again:
                    break
                body["ack"] = saved                          # the server deletes these on the next call
            db.set_setting("recruit_synced", str(time.time()))
            db.set_setting("recruit_error", "")
        except licensing.ServerRefused as e:
            db.set_setting("recruit_error", e.message[:300])
            return new, e.message
        except ConnectionError as e:
            db.set_setting("recruit_error", str(e)[:300])
            return new, str(e)
        if new:
            for m in db.q("SELECT id FROM users WHERE role IN ('admin','owner') AND status!='removed'"):
                db.notify(m["id"], f"{new} new driver application{'s' if new > 1 else ''} from your Apply page.",
                          "/recruitment", "info")
            economy.webhook("application", f"📝 {new} new driver application{'s' if new > 1 else ''} received")
        return new, None


def waiting(u):
    """New applications for the banner on every page: those still waiting that this manager hasn't hidden.
    Returns {"n", "name", "upto"} or None (nothing new, or not a manager)."""
    if not u or u["role"] not in ("admin", "owner"):
        return None
    rows = db.q("SELECT name, created_at FROM applications WHERE status='pending' AND created_at>? "
                "ORDER BY created_at DESC", (u["apps_seen"] or 0,))
    return {"n": len(rows), "name": rows[0]["name"], "upto": rows[0]["created_at"]} if rows else None


def _store(a, rid):
    age = str(a.get("age") or "")
    games = a.get("games") if a.get("games") in ("ats", "ets2") else ("ats,ets2" if a.get("games") == "both" else "")
    db.insert("applications", name=str(a.get("name") or "")[:48] or "(no name)", contact=str(a.get("contact") or "")[:100],
              age=int(age) if age.isdecimal() and 0 < int(age) < 120 else None, country=str(a.get("country") or "")[:48],
              games=games, experience=str(a.get("experience") or "")[:1000], message=str(a.get("message") or "")[:2000],
              remote_id=rid, source="online", created_at=time.time(), answers=_answers(a.get("answers")),
              found_via=a.get("via") if a.get("via") in ("finder", "link") else "")


def _answers(rows):
    """The answers to the company's own questions, as stored: a JSON list of [question, answer]."""
    out = [[r[0][:120], r[1][:300]] for r in (rows if isinstance(rows, list) else [])[:vtc_profile.MAX_QUESTIONS]
           if isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r)]
    return json.dumps(out) if out else ""


def answers_of(text):
    try:
        rows = json.loads(text or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, list) and len(r) == 2] if isinstance(rows, list) else []


def start():
    """Collect in the background: shortly after start, then every 10 minutes."""
    def loop():
        stop = threading.Event()
        stop.wait(20)
        while True:
            try:
                sync()
            except Exception as e:                           # never let a hiccup stop the loop
                logging.getLogger(__name__).warning("recruitment sync failed: %s", e)
            stop.wait(EVERY)
    threading.Thread(target=loop, daemon=True, name="recruit").start()
