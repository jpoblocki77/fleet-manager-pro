"""Online recruitment: the company's public Apply page lives on the licence server (always online), because this
app only listens on this PC. Every few minutes the app publishes its company name / intro / open-closed state there
and collects new applications, which then appear on the Recruitment page. The server deletes each application once
this app confirms it has saved it."""
import logging
import threading
import time

from . import db, economy, licensing

EVERY = 10 * 60                     # seconds between collections
_lock = threading.Lock()


def sync():
    """Publish the Apply page and collect new applications. Returns (new applications, error message or None)."""
    if not licensing.licensed():
        return 0, "Fleet Manager Pro isn't activated, so the online Apply page is off."
    with _lock:
        lic = licensing.LICENCE
        body = {"key": lic.key, "machine": lic.machine, "product": licensing.PRODUCT,
                "company": db.setting("company_name", "") or "", "intro": db.setting("recruitment_text", "") or "",
                "open": db.setting("recruitment_open") == "1", "ack": []}
        new = 0
        try:
            for _ in range(6):                               # up to 600 waiting applications per run
                d = licensing._post("/v1/recruit", body, timeout=10)
                saved = []
                for a in d.get("applications") or []:
                    rid = int(a.get("id") or 0)
                    if not rid:
                        continue
                    if not db.val("SELECT id FROM applications WHERE remote_id=?", (rid,)):
                        _store(a, rid)
                        new += 1
                    saved.append(rid)
                db.set_setting("recruit_url", str(d.get("apply_url") or "")[:200])
                db.set_setting("recruit_code", str(d.get("code") or "")[:16])
                if not saved:
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
            economy.webhook("event", f"📝 {new} new driver application{'s' if new > 1 else ''} received")
        return new, None


def _store(a, rid):
    age = str(a.get("age") or "")
    games = a.get("games") if a.get("games") in ("ats", "ets2") else ("ats,ets2" if a.get("games") == "both" else "")
    db.insert("applications", name=str(a.get("name") or "")[:48] or "(no name)", contact=str(a.get("contact") or "")[:100],
              age=int(age) if age.isdigit() and 0 < int(age) < 120 else None, country=str(a.get("country") or "")[:48],
              games=games, experience=str(a.get("experience") or "")[:1000], message=str(a.get("message") or "")[:2000],
              remote_id=rid, source="online", created_at=time.time())


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
