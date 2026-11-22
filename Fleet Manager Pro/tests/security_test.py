"""Security tests for Fleet Manager Pro. Throw-away data and licence server; never touches ./data.

    python tests/security_test.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from e2e_test import start_licence_server  # noqa: E402
from fmpro import create_app, db, licensing  # noqa: E402
from fmpro.auth import create_user  # noqa: E402
from fmpro.telemetry.service import HUB  # noqa: E402

FAILS = []
XSS = '<script>alert(1)</script>'


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class Browser:
    """A test client that behaves like one browser (own cookies, CSRF token from the session)."""

    def __init__(self, app):
        self.c = app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            return s.get("csrf")

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        d = dict(data or {})
        d["csrf"] = self.token()
        return self.c.post(url, data=d, **kw)

    def login(self, name, pw):
        self.c.get("/login")
        r = self.post("/login", {"username": name, "password": pw})
        self.c.get("/")
        return r


def main():
    srv, lic_folder = start_licence_server()
    key = srv.store.new_purchase("Sec Test", "", product="Fleet Manager Pro")
    tmp = tempfile.mkdtemp(prefix="fmp-sec-")
    app = create_app(tmp, start_telemetry=False, check_licence=False)
    app.testing = True
    lic = licensing.LICENCE

    anon = Browser(app)
    print("Locked until licensed")
    check(anon.get("/").headers.get("Location", "").endswith("/activate"), "unlicensed: pages redirect to /activate")
    check(anon.get("/api/live").status_code == 403, "unlicensed: API refused")
    check(anon.c.post("/api/agent/frame", json={"frame": None}, headers={"X-Agent-Token": "x"}).status_code == 403,
          "unlicensed: agent endpoint refused")
    anon.get("/activate")
    anon.post("/activate", {"key": key, "agree": "1"})
    check(lic.ok, "activated for the rest of the tests")

    anon.get("/setup")
    anon.post("/setup", {"company_name": "SecCo", "username": "boss", "password": "ownerpw1", "password2": "ownerpw1"})
    boss_id = db.val("SELECT id FROM users WHERE username='boss'")
    mgr_id = create_user("mgr", "Manager", "managerpw1", role="admin")
    drv_id = create_user("drv", "Driver", "driverpw1", role="driver")
    boss, mgr, drv = Browser(app), Browser(app), Browser(app)
    boss.login("boss", "ownerpw1"), mgr.login("mgr", "managerpw1"), drv.login("drv", "driverpw1")
    anon = Browser(app)

    print("Unauthenticated access")
    public = {"/login", "/apply", "/activate", "/setup", "/overlay", "/licence-agreement"}
    gets = sorted({str(r) for r in app.url_map.iter_rules() if "GET" in r.methods and "<" not in str(r)
                   and not str(r).startswith("/static")})
    leaks = []
    for url in gets:
        r = anon.get(url)
        ok = r.status_code in (401, 403) or (r.status_code == 302 and ("/login" in r.headers["Location"] or url in public))
        if url in public and r.status_code == 200:
            ok = True
        if not ok:
            leaks.append(f"{url}->{r.status_code}")
    check(not leaks, f"all {len(gets)} pages/APIs need a login ({leaks or 'none leak'})")
    for url in ("/jobs/1", "/drivers/1", "/fleet/truck/1", "/events/1", "/fleet/lease/1"):
        check(anon.get(url).status_code == 302, f"anonymous {url} redirected to login")
    posts = sorted({str(r) for r in app.url_map.iter_rules() if "POST" in r.methods and "<" not in str(r)})
    open_posts = []
    for url in posts:
        if url in ("/login", "/apply", "/setup", "/activate", "/api/agent/frame", "/logout"):
            continue
        anon.get("/login")
        r = anon.post(url, {})
        if not (r.status_code in (401, 403) or (r.status_code == 302 and "/login" in r.headers.get("Location", ""))):
            open_posts.append(f"{url}->{r.status_code}")
    check(not open_posts, f"all {len(posts)} form actions need a login ({open_posts or 'none open'})")

    print("Role checks (driver must not reach manager functions)")
    before = db.val("SELECT COUNT(*) FROM ledger"), db.val("SELECT COUNT(*) FROM users")
    admin_posts = ["/settings", "/fleet/maintenance", "/finance/entry", "/finance/loan", "/drivers/new", "/fleet/truck/save",
                   "/fleet/garage/save", "/fleet/trailer/save", "/dispatch/new", "/events/save", "/announcements",
                   "/settings/licence", "/settings/plugin-install", "/settings/webhook-test", "/jobs/1/review",
                   "/compliance/inspect", "/compliance/dvir/1/repair", "/divisions", "/recruitment/1",
                   f"/drivers/{boss_id}/edit", f"/drivers/{boss_id}/token", f"/drivers/{boss_id}/remove",
                   f"/drivers/{mgr_id}/edit", f"/drivers/{mgr_id}/remove", "/finance/loan/1/payoff",
                   "/fleet/truck/1/sell", "/events/1/manage", "/events/1/edit", "/leasing/new", "/leasing/plans/save",
                   "/leasing/plans/1/toggle", "/fleet/lease/1/end", f"/drivers/{mgr_id}/fire",
                   "/fleet/truck/1/delete", "/fleet/trailer/1/delete", "/fleet/clear-sold", "/drivers/purge-former",
                   "/settings/update"]
    bad = [u for u in admin_posts if drv.post(u, {"title": "x", "body": "y", "amount": "999", "plan": "standard",
                                                  "username": "evil", "password": "evilpw12", "brand": "a", "model": "b",
                                                  "city": "c", "name": "n", "destination_city": "d", "action": "delete",
                                                  "role": "owner"}).status_code not in (403, 404)]
    check(not bad, f"driver refused on {len(admin_posts)} manager actions ({bad or 'all 403'})")
    for u in ("/settings", "/recruitment", "/api/export/ledger.csv", "/api/telemetry/raw"):
        check(drv.get(u).status_code == 403, f"driver can't open {u}")
    check((db.val("SELECT COUNT(*) FROM ledger"), db.val("SELECT COUNT(*) FROM users")) == before, "nothing changed by refused requests")

    print("System & Backups")
    from fmpro import insights
    for u in ("/system", "/system/logs.zip"):
        check(drv.get(u).status_code == 403, f"driver can't open {u}")
    for u in ("/system/backup", "/system/auto-backup"):
        check(drv.post(u, {"on": "0"}).status_code == 403, f"driver can't post {u}")
    check(db.setting("auto_backup", "1") == "1", "driver couldn't turn automatic backups off")
    check(drv.post("/settings/update", {"action": "auto_on"}).status_code == 403 and db.setting("auto_update", "0") == "0",
          "driver can't turn on automatic updates")
    check(drv.get("/updates").status_code == 403, "driver can't open the Updates page")
    nb = len(insights.list_backups())
    boss.post("/system/backup", {})
    bk = insights.list_backups()
    check(len(bk) == nb + 1, "(owner made a backup)")
    name = bk[0]["name"]
    boss.post("/system/backup", {})
    check(len(insights.list_backups()) == nb + 1, "a second manual backup within a minute is refused (no disk filling)")
    r = boss.get("/system/logs.zip")
    check(r.status_code == 200 and r.data[:2] == b"PK", "logs download as a real zip (owner)")
    r.close()
    check(mgr.get(f"/system/backup/{name}").status_code == 403, "manager can't download a database backup")
    check(drv.get(f"/system/backup/{name}").status_code == 403, "driver can't download a database backup")
    check(mgr.post(f"/system/backup/{name}/delete").status_code == 403 and len(insights.list_backups()) == nb + 1,
          "manager can't delete a backup")
    r = boss.c.post(f"/system/backup/{name}/delete", data={})
    check(r.status_code == 400 and len(insights.list_backups()) == nb + 1, "deleting a backup needs the CSRF token")
    for evil in ("..%2Ffleet.db", "..%5Cfleet.db", "fleet.db", "secret.key", "fleet-x-20260101-000000.db%00.txt",
                 "..%2F..%2Fsecret.key"):
        check(boss.get(f"/system/backup/{evil}").status_code == 404, f"backup download refuses {evil}")
    for evil in ("../secret.key", "..\\fleet.db", "fleet.db", "C:\\Windows\\win.ini", "/etc/passwd"):
        page = boss.get("/system", query_string={"log": evil}).get_data(as_text=True)
        check("Fleet Manager" in page and "SQLite format" not in page, f"log viewer refuses {evil!r}")
    check(b"<script>alert(1)</script>" not in boss.get("/system", query_string={"q": "<script>alert(1)</script>"}).data,
          "log filter text is escaped")
    check(b"<script>alert(1)</script>" not in boss.get("/planner", query_string={"from": "<script>alert(1)</script>", "to": "x"}).data,
          "route planner escapes city names")

    print("Privilege escalation")
    drv.post(f"/drivers/{drv_id}/edit", {"display_name": "Driver", "role": "owner", "status": "active", "pay_per_km": "99"})
    d = db.one("SELECT role, pay_per_km FROM users WHERE id=?", (drv_id,))
    check(d["role"] == "driver" and d["pay_per_km"] is None, "driver can't change their own role or pay")
    mgr.post(f"/drivers/{mgr_id}/edit", {"display_name": "Manager", "role": "owner"})
    check(db.val("SELECT role FROM users WHERE id=?", (mgr_id,)) == "admin", "manager can't promote themself")
    check(mgr.post(f"/drivers/{boss_id}/edit", {"display_name": "pwned", "new_password": "hacked123"}).status_code == 403,
          "manager can't edit the owner")
    mgr.post("/drivers/new", {"username": "m2", "password": "secondpw1", "role": "admin"})
    check(db.val("SELECT role FROM users WHERE username='m2'") == "driver", "manager can't create another manager")
    mgr.post("/drivers/new", {"username": "o2", "password": "secondpw1", "role": "owner"})
    check(db.val("SELECT role FROM users WHERE username='o2'") == "driver", "nobody can create a second owner")
    tok = db.val("SELECT agent_token FROM users WHERE id=?", (boss_id,))
    check(mgr.post(f"/drivers/{boss_id}/token").status_code == 403 and
          db.val("SELECT agent_token FROM users WHERE id=?", (boss_id,)) == tok, "manager can't reset the owner's agent token")
    # the connection line (with the token) is only on the page while other PCs are let in: check it open, then closed
    drv_tok = db.val("SELECT agent_token FROM users WHERE id=?", (drv_id,))
    had_lan, had_addr = app.config.get("LAN"), db.setting("public_address", "") or ""
    app.config["LAN"] = {"scheme": "https", "port": 8760}
    db.set_setting("public_address", "vtc.example.com")
    line = b"https://vtc.example.com:8760 --token "
    check(tok.encode() not in mgr.get(f"/drivers/{boss_id}").data, "manager can't see the owner's agent token")
    check(tok.encode() not in drv.get(f"/drivers/{boss_id}").data, "driver can't see the owner's agent token")
    check(line + tok.encode() in boss.get(f"/drivers/{boss_id}").data, "owner sees their own token")
    check(line + drv_tok.encode() in mgr.get(f"/drivers/{drv_id}").data, "manager sees a driver's connection line")
    check(line + drv_tok.encode() in drv.get(f"/drivers/{drv_id}").data, "driver sees their own connection line")
    check(b"/drivers/connect" not in drv.get(f"/drivers/{drv_id}").data, "driver gets no Connect a Driver link (internet line)")
    db.set_setting("public_address", "")
    with mock.patch("fmpro.tablet.lan_addresses", return_value=["192.168.1.5"]):
        pg = drv.get(f"/drivers/{drv_id}").data
        check(b"https://192.168.1.5:8760 --token " + drv_tok.encode() in pg and b"/drivers/connect" not in pg,
              "driver gets no Connect a Driver link (home line)")
        check(b"/drivers/connect" in mgr.get(f"/drivers/{drv_id}").data, "manager gets the Connect a Driver link")
    app.config["LAN"] = None
    pg = drv.get(f"/drivers/{drv_id}").data
    check(drv_tok.encode() not in pg.replace(b"/overlay?key=" + drv_tok.encode(), b"") and b"--token" not in pg
          and b"/drivers/connect" not in pg, "closed: driver gets no line and no Connect a Driver link")
    check(b"/drivers/connect" in boss.get(f"/drivers/{drv_id}").data and drv.get("/drivers/connect").status_code == 403,
          "closed: owner gets the link, and drivers are refused at that page")
    app.config["LAN"] = had_lan
    db.set_setting("public_address", had_addr)
    check(mgr.post(f"/drivers/{boss_id}/remove").status_code == 403, "manager can't remove the owner")
    check(mgr.post(f"/drivers/{boss_id}/fire", {"confirm": "Boss"}).status_code == 403, "manager can't fire the owner")
    m3 = create_user("mgr3", "Manager Three", "managerpw3", role="admin")
    check(mgr.post(f"/drivers/{m3}/fire", {"confirm": "Manager Three"}).status_code == 403 and
          db.val("SELECT COUNT(*) FROM users WHERE id=?", (m3,)) == 1, "only the owner can fire a manager")
    check(mgr.post(f"/drivers/{mgr_id}/fire", {"confirm": "Manager"}).status_code == 403, "nobody can fire themself")
    check(mgr.post("/drivers/purge-former").status_code == 403, "only the owner can bulk-delete former members")
    fl = drv.get("/fleet").get_data(as_text=True)
    check("Trucks" in fl and 'id="maint-form"' not in fl and 'action="/fleet/maintenance"' not in fl, "drivers don't get the Record maintenance form")
    n_m = db.val("SELECT COUNT(*) FROM maintenance")
    check(drv.post("/fleet/maintenance", {"kind": "tyres", "cost": "0"}).status_code == 403 and
          db.val("SELECT COUNT(*) FROM maintenance") == n_m, "drivers can't record maintenance")
    before_p = db.setting("maint_price_tyres")
    check(drv.post("/settings", {"section": "economy", "maint_price_tyres": "0"}).status_code == 403 and
          db.setting("maint_price_tyres") == before_p, "drivers can't change maintenance prices")
    check(mgr.post("/fleet/maintenance", {"kind": "wash", "cost": "-999999"}).status_code == 302 and
          db.val("SELECT cost FROM maintenance ORDER BY id DESC LIMIT 1") == db.setting_f("maint_price_wash"),
          "a forged cost (even negative) can't change what a wash costs")
    mgr.post("/fleet/maintenance", {"kind": "repair", "cost": "-999999"})
    check(db.val("SELECT cost FROM maintenance ORDER BY id DESC LIMIT 1") >= 0, "a forged repair cost is ignored")
    before_r = db.setting("dvir_repair_per_defect")
    check(drv.post("/settings", {"section": "economy", "dvir_repair_per_defect": "0", "repair_cost_per_pct": "0"}).status_code == 403
          and db.setting("dvir_repair_per_defect") == before_r, "drivers can't change repair prices")
    check(mgr.post("/finance/loan", {"plan": "standard", "amount": "50000"}).status_code == 403, "only the owner takes loans")
    check(key.encode() not in mgr.get("/settings?tab=licence").data and key.encode() in boss.get("/settings?tab=licence").data,
          "purchase key is shown to the owner only")
    check(mgr.post("/settings/licence", {"action": "deactivate"}).status_code == 403 and lic.ok, "only the owner can deactivate the licence")

    print("Cross-site request forgery")
    n = db.val("SELECT COUNT(*) FROM announcements")
    check(boss.c.post("/announcements", data={"title": "x", "body": "y"}).status_code == 400, "form without a token refused")
    check(boss.c.post("/announcements", data={"title": "x", "body": "y", "csrf": "guess"}).status_code == 400, "wrong token refused")
    check(boss.c.post("/announcements", data={"title": "x", "body": "y", "csrf": drv.token()}).status_code == 400,
          "another user's token refused")
    check(db.val("SELECT COUNT(*) FROM announcements") == n, "no announcement created")
    check(boss.get("/logout").status_code == 405, "state-changing routes don't answer GET")

    print("Host header (DNS rebinding)")
    check(anon.get("/login", headers={"Host": "evil.example"}).status_code == 400, "foreign host name refused")
    check(anon.get("/login", headers={"Host": "evil.example:8760"}).status_code == 400, "foreign host name with port refused")
    for h in ("127.0.0.1:8760", "192.168.1.20:8760", "localhost", "[::1]:8760", socket.gethostname()):
        check(anon.get("/login", headers={"Host": h}).status_code == 200, f"own address {h} accepted")

    print("Open redirect")
    for nxt in ("//evil.example", "/\\evil.example", "https://evil.example", "/\tevil", "javascript:alert(1)"):
        b = Browser(app)
        b.c.get("/login")
        r = b.c.post("/login?next=" + __import__("urllib.parse").parse.quote(nxt, safe=""),
                     data={"username": "drv", "password": "driverpw1", "csrf": b.token()})
        loc = r.headers.get("Location", "")
        check(r.status_code == 302 and loc == "/", f"next={nxt!r} goes to the dashboard ({loc})")

    print("Injection")
    r = anon.c.get("/login")
    r = anon.post("/login", {"username": "' OR '1'='1' --", "password": "x"})
    check(b"Wrong username" in r.data, "SQL injection in login fails")
    for q in ("' OR 1=1 --", "%'; DROP TABLE jobs; --", '" UNION SELECT password_hash FROM users --'):
        r = boss.get("/jobs", query_string={"q": q, "driver": "1 OR 1=1", "status": "x' OR '1'='1", "page": "-5"})
        check(r.status_code == 200 and b"pbkdf2" not in r.data and b"scrypt" not in r.data, f"logbook search survives {q[:14]!r}")
    check(db.val("SELECT COUNT(*) FROM sqlite_master WHERE name='jobs'") == 1, "jobs table still exists")
    check(boss.get("/leaderboard?metric=1;DROP TABLE users&period=x&game=y").status_code == 200, "leaderboard ignores unknown metric")
    check(boss.get("/api/telemetry/raw?offset=abc&length=-9").status_code in (200, 404), "raw viewer survives bad numbers")
    check(boss.get("/static/../../data/fleet.db").status_code == 404 and boss.get("/static/..%2f..%2fdata%2ffleet.db").status_code == 404,
          "can't read the database through /static")

    print("Stored script injection (XSS)")
    a2 = Browser(app)
    a2.get("/apply")
    a2.post("/apply", {"name": XSS, "contact": XSS, "experience": XSS, "message": XSS, "discord": XSS})
    drv.post("/messages", {"body": XSS})
    drv.post(f"/drivers/{drv_id}/edit", {"display_name": XSS, "bio": XSS, "country": XSS, "color": '"><script>'})
    drv.post("/jobs/new", {"cargo": XSS, "source_city": XSS, "destination_city": XSS, "driven_km": "10", "revenue": "10", "notes": XSS})
    boss.post("/announcements", {"title": XSS, "body": XSS})
    boss.post("/dispatch/new", {"title": XSS, "destination_city": XSS, "notes": XSS})
    boss.post("/leasing/plans/save", {"name": XSS[:40], "weeks": "10", "allowed": "5000", "down_pct": "5", "apr": "5",
                                      "residual_pct": "50", "excess_rate": "0.2", "early_fee_pct": "20"})
    xplan = db.val("SELECT id FROM lease_plans WHERE name=?", (XSS[:40],))
    boss.post("/leasing/new", {"plan_id": str(xplan), "price": "50000", "source": "new", "brand": XSS, "model": XSS,
                               "plate": XSS, "game": "ats"})
    xlease = db.val("SELECT MAX(id) FROM leases")
    check(xplan and xlease, "lease with hostile names created for the XSS check")
    boss.post("/events/save", {"title": "Plain", "start_at": "2031-05-05T10:00"})
    xev = db.val("SELECT MAX(id) FROM events")
    boss.post(f"/events/{xev}/edit", {"title": XSS[:100], "start_at": "2031-05-05T10:00", "meetup": XSS[:100], "departure": XSS[:100],
                                      "destination": XSS[:100], "server": XSS[:64], "dlc": XSS, "description": XSS})
    check(db.val("SELECT title FROM events WHERE id=?", (xev,)) == XSS[:100], "event edited with hostile text for the XSS check")
    n_ev = db.val("SELECT title FROM events WHERE id=?", (xev,))
    check(drv.post(f"/events/{xev}/edit", {"title": "pwned", "start_at": "2031-05-05T10:00"}).status_code == 403
          and db.val("SELECT title FROM events WHERE id=?", (xev,)) == n_ev, "drivers can't edit events")
    boss.post("/events/save", {"title": "Closed", "start_at": "2031-06-06T10:00"})
    cev = db.val("SELECT MAX(id) FROM events")
    boss.post(f"/events/{cev}/manage", {"action": "cancel"})
    drv.post(f"/events/{cev}/rsvp", {"status": "going"})
    check(not db.val("SELECT 1 FROM event_rsvps WHERE event_id=?", (cev,)), "drivers can't sign up for a cancelled event by posting the form")
    check(drv.post(f"/events/{cev}/manage", {"action": "complete", "attended": str(drv_id)}).status_code == 403
          and db.val("SELECT status FROM events WHERE id=?", (cev,)) == "cancelled", "drivers can't complete events or award themselves XP")
    n_m = db.insert("notifications", user_id=mgr_id, text="mgr only", created_at=time.time())
    drv.post(f"/notifications/{n_m}/delete")
    drv.c.post(f"/notifications/{n_m}/delete", headers={"X-CSRF": drv.token()})
    drv.post("/notifications/clear")
    check(db.val("SELECT 1 FROM notifications WHERE id=?", (n_m,)) == 1, "drivers can't delete someone else's notifications")
    r_ = mgr.c.post(f"/notifications/{n_m}/delete")
    check(r_.status_code in (400, 403) and db.val("SELECT 1 FROM notifications WHERE id=?", (n_m,)) == 1,
          "deleting a notification needs the CSRF token")
    r_ = mgr.c.post("/notifications/clear", headers={"X-CSRF": "wrong"})
    check(r_.status_code in (400, 403) and db.val("SELECT 1 FROM notifications WHERE id=?", (n_m,)) == 1,
          "delete all refuses a wrong CSRF token")
    r_ = anon.c.post(f"/notifications/{n_m}/delete", data={"csrf": "x"})
    check(r_.status_code in (302, 400, 403) and db.val("SELECT 1 FROM notifications WHERE id=?", (n_m,)) == 1,
          "signed-out visitors can't delete notifications")
    check(mgr.c.get(f"/notifications/{n_m}/delete").status_code == 405, "deleting a notification can't be done with a link (GET)")
    db.insert("notifications", user_id=mgr_id, text=XSS, link="javascript:alert(1)", created_at=time.time())
    j_ = mgr.get("/api/notifications").get_json()
    check(any(n["text"] == XSS for n in j_["items"]), "notification text comes back as data (the dropdown escapes it)")
    jid = db.val("SELECT id FROM jobs WHERE source='manual'")
    pages = ["/leasing", f"/fleet/lease/{xlease}", "/fleet", "/", "/recruitment", "/messages", "/drivers", f"/drivers/{drv_id}", "/jobs", f"/jobs/{jid}", "/announcements",
             "/dispatch", "/leaderboard?period=all", "/compliance", "/finance", "/settings?tab=audit",
             "/events", f"/events/{xev}", f"/events/{xev}?edit=1"]
    raw = [u for u in pages if XSS.encode() in boss.get(u).data]
    check(not raw, f"script text is escaped on {len(pages)} pages ({raw or 'none raw'})")
    check(db.val("SELECT color FROM users WHERE id=?", (drv_id,)) == "#c8102e", "invalid colour value rejected")
    cur0 = db.setting("currency", "$")
    db.set_setting("currency", "');alert(1)//")
    lp = boss.get(f"/fleet/lease/{xlease}").get_data(as_text=True) + boss.get("/leasing").get_data(as_text=True)
    db.set_setting("currency", cur0)
    check("');alert(1)" not in lp and "confirm('" not in lp, "a hostile currency symbol can't break out of the leasing page scripts")

    print("Remote agent input")
    atok = db.val("SELECT agent_token FROM users WHERE id=?", (drv_id,))
    evil = {"frame": {"values": {"sdk_active": True, "game": XSS, "game_id": 2, "gear_label": XSS, "game_clock": XSS,
                                 "truck_brand": XSS + "A" * 500, "truck_wheel_count": XSS, "plugin_revision": XSS,
                                 "speed": float("nan"), "fuel": float("inf"), "odometer": 1e300, "x": "DROP",
                                 "wheel_powered": [XSS] * 40, "license_plate": {"a": 1}, "unknown_field": XSS,
                                 "job_delivered_revenue": 10 ** 30},
                      "trailers": [{"attached": True, "wheel_count": XSS, "name": XSS, "id": 5, "wear_body": "x",
                                    "slot": XSS, "extra": XSS}] * 30,
                      "substances": [XSS, 5, None] * 30}}
    r = anon.c.post("/api/agent/frame", data=json.dumps(evil), content_type="application/json", headers={"X-Agent-Token": atok})
    check(r.status_code == 200, "malformed agent frame handled without an error")
    live = boss.get(f"/api/live?user={drv_id}")
    text = live.get_data(as_text=True)
    try:
        d = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        strict = True
    except ValueError:
        d, strict = {}, False
    v = d.get("values", {})
    check(strict, "live API stays valid JSON (no NaN / Infinity)")
    check(v.get("game") == "ats" and v.get("gear_label") == "N" and XSS not in str(v.get("game_clock")),
          "derived text is recomputed by the server, not taken from the agent")
    check(v.get("truck_wheel_count") == 0 and v.get("plugin_revision") == 0 and v.get("speed") == 0 and v.get("fuel") == 0
          and v.get("x") == 0, "wrong-typed and non-finite numbers become 0")
    check(len(v.get("truck_brand", "")) <= 64 and "unknown_field" not in v and v.get("license_plate") == ""
          and v.get("wheel_powered") in ([], [False] * 16), "strings capped, unknown fields dropped")
    t = d.get("trailers", [])
    check(len(t) == 10 and all(isinstance(x["wheel_count"], int) and isinstance(x["slot"], int) and "extra" not in x for x in t),
          "trailers limited to 10 and typed")
    check(len(d.get("substances", [])) <= 25, "substances limited")
    check(anon.c.post("/api/agent/frame", json={"frame": {"values": {}}}, headers={"X-Agent-Token": "wrong"}).status_code == 401,
          "wrong agent token refused")
    check(anon.c.post("/api/agent/frame", data=b"x" * (3 * 1024 * 1024), content_type="application/json",
                      headers={"X-Agent-Token": atok}).status_code in (400, 413), "oversized upload refused")
    check(anon.get("/api/overlay?key=wrong").status_code == 401 and anon.get(f"/api/overlay?key={atok}").status_code == 200,
          "overlay needs a valid key")

    print("Spreadsheet formula injection")
    boss.post("/jobs/new", {"cargo": "=cmd|' /C calc'!A0", "source_city": "+SUM(1)", "destination_city": "@x",
                            "driven_km": "5", "revenue": "5"})
    csv_text = boss.get("/api/export/jobs.csv").get_data(as_text=True)
    check("'=cmd|" in csv_text and ",=cmd" not in csv_text and ",+SUM" not in csv_text and ",@x" not in csv_text,
          "formula cells are neutralised in the CSV export")

    print("Numbers")
    bal = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    for amt in ("-1000000", "nan", "inf", "1e999", "abc"):
        boss.post("/finance/entry", {"direction": "income", "amount": amt, "category": "test"})
    check(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") == bal, "negative / NaN / infinite amounts add nothing")
    boss.post("/finance/loan", {"plan": "standard", "amount": "nan"})
    boss.post("/finance/loan", {"plan": "../../etc", "amount": "50000"})
    check(db.val("SELECT COUNT(*) FROM loans") == 0, "bad loan requests refused")

    print("Private messages")
    boss.post("/messages", {"body": "secret-for-manager", "to": str(mgr_id)})
    check(b"secret-for-manager" in mgr.get(f"/messages?with={boss_id}").data, "recipient can read a private message")
    check(b"secret-for-manager" not in drv.get(f"/messages?with={boss_id}").data and
          b"secret-for-manager" not in drv.get(f"/messages?with={mgr_id}").data and
          b"secret-for-manager" not in drv.get("/messages").data, "a third driver can't read it")

    print("Sessions and passwords")
    drv2 = Browser(app)
    drv2.login("drv", "driverpw1")
    check(drv2.get("/jobs").status_code == 200, "second browser signed in")
    drv.post("/account/password", {"current": "driverpw1", "new": "newdriverpw2"})
    check(drv.get("/jobs").status_code == 200, "browser that changed the password stays signed in")
    check(drv2.get("/jobs").status_code == 302, "other browser is signed out after the password change")
    check(drv.post("/account/password", {"current": "wrong", "new": "x" * 10}).status_code == 302 and
          Browser(app).login("drv", "newdriverpw2").status_code == 302, "wrong current password can't change it")
    check(drv.post("/account/password", {"current": "newdriverpw2", "new": "123"}).status_code == 302 and
          Browser(app).login("drv", "123").status_code != 302, "short password refused")
    h = db.val("SELECT password_hash FROM users WHERE id=?", (drv_id,))
    check(h.startswith(("scrypt:", "pbkdf2:")) and "newdriverpw2" not in h, "passwords are stored hashed")
    for bad_pw, why in (("short12", "7 characters"), ("password123", "common password"), ("aaaaaaaaaa", "one repeated letter"),
                        ("boss2024", "the username plus digits")):
        boss.post("/account/password", {"current": "ownerpw1", "new": bad_pw})
        check(Browser(app).login("boss", bad_pw).status_code != 302, f"weak password refused ({why})")
    mgr.post("/drivers/new", {"username": "weak", "password": "1234567"})
    check(db.val("SELECT id FROM users WHERE username='weak'") is None, "new member with a 7-character password refused")
    db.ex("DELETE FROM login_failures")
    lock = Browser(app)
    lock.c.get("/login")
    for _ in range(5):
        lock.post("/login", {"username": "mgr", "password": "nope"})
    check(b"Too many" in lock.post("/login", {"username": "mgr", "password": "managerpw1"}).data,
          "5 wrong passwords block that PC for that account")
    other = Browser(app)
    other.c.get("/login", environ_base={"REMOTE_ADDR": "192.168.1.77"})
    r = other.post("/login", {"username": "mgr", "password": "managerpw1"}, environ_base={"REMOTE_ADDR": "192.168.1.77"})
    check(r.status_code == 302, "the real owner of the account still gets in from their own PC (no account lock-out)")
    check(lock.post("/login", {"username": "boss", "password": "nope"}).status_code == 200 and
          b"Too many" not in lock.post("/login", {"username": "boss", "password": "nope"}).data, "other accounts aren't blocked yet")
    spray = Browser(app)
    spray.c.get("/login", environ_base={"REMOTE_ADDR": "10.9.9.9"})
    for i in range(20):
        spray.post("/login", {"username": f"user{i}", "password": "guess"}, environ_base={"REMOTE_ADDR": "10.9.9.9"})
    check(b"Too many" in spray.post("/login", {"username": "boss", "password": "ownerpw1"}, environ_base={"REMOTE_ADDR": "10.9.9.9"}).data,
          "20 wrong guesses across names block that PC entirely")
    db.ex("DELETE FROM login_failures")
    boss.post(f"/drivers/{drv_id}/fire", {"confirm": db.val("SELECT display_name FROM users WHERE id=?", (drv_id,))})
    check(drv.get("/jobs").status_code == 302, "removed member is signed out at once")
    check(anon.c.post("/api/agent/frame", json={"frame": None}, headers={"X-Agent-Token": atok}).status_code == 401,
          "removed member's agent token stops working")
    r = boss.get("/")
    cookie = r.headers.get("Set-Cookie", "") or boss.c.get("/login").headers.get("Set-Cookie", "")
    sc = [x for x in boss.login("boss", "ownerpw1").headers.getlist("Set-Cookie") if x.startswith("fmp_session=")]
    check(sc and "HttpOnly" in sc[0] and "SameSite=Lax" in sc[0], "session cookie is HttpOnly and SameSite")
    with open(os.path.join(tmp, "secret.key")) as f:
        check(len(f.read().strip()) == 64, "session signing key is random 256-bit")
    forged = Browser(app)
    forged.c.set_cookie("fmp_session", "eyJ1aWQiOjF9.forged.signature")
    check(forged.get("/jobs").status_code == 302, "forged session cookie rejected")

    print("Response headers")
    hd = boss.get("/").headers
    check(hd.get("X-Frame-Options") == "SAMEORIGIN" and hd.get("X-Content-Type-Options") == "nosniff", "framing and sniffing headers set")
    csp = hd.get("Content-Security-Policy", "")
    check("default-src 'self'" in csp and "connect-src 'self'" in csp and "form-action 'self'" in csp and "object-src 'none'" in csp,
          "Content-Security-Policy limits scripts, connections and form targets to this site")
    check(b"Traceback" not in boss.get("/jobs/999999").data and boss.get("/jobs/999999").status_code == 404, "missing pages give a plain 404")

    print("Server-side file write (plugin installer)")
    fake = os.path.join(tmp, "Game")
    os.makedirs(os.path.join(fake, "bin", "win_x64"))
    r = boss.post("/settings/plugin-install", {"game_dir": fake}, environ_base={"REMOTE_ADDR": "192.168.1.50"})
    check(not os.path.exists(os.path.join(fake, "bin", "win_x64", "plugins")), "plugin install refused from another PC")
    check(boss.post("/settings/plugin-install", {"dir": r"C:\Windows\System32"}).status_code == 400, "arbitrary target folder refused")
    check(boss.post("/settings/plugin-install", {"game_dir": tmp}).status_code == 302 and
          not os.path.exists(os.path.join(tmp, "bin")), "folder that isn't a game refused")
    boss.post("/settings", {"section": "integrations", "discord_webhook": "http://127.0.0.1:22/"})
    boss.post("/settings", {"section": "integrations", "discord_webhook": "file:///etc/passwd"})
    check(db.setting("discord_webhook") == "", "webhook must be an https address")

    print("Anti-cheat holds")
    from fmpro.telemetry import reader, service
    cheat = {"sdk_active": True, "game_id": 2, "truck_id": "t", "license_plate": "CHEAT", "on_job": True, "cargo": "Gold",
             "cargo_id": "gold", "source_city": "A", "destination_city": "B", "job_income": 5000, "planned_distance_km": 900,
             "job_starting_time": 500, "game_time_abs": 500, "odometer": 1000.0, "fuel": 300.0, "fuel_capacity": 500.0}
    seq = [0]

    def cfeed(tr, vals, source="agent"):
        seq[0] += 1
        f = reader.parse(reader.pack(dict(vals, render_time=seq[0])))
        f["source"] = source
        tr.feed(f)

    tr = service.JobTracker(mgr_id)
    cfeed(tr, dict(cheat, on_job=False))
    cfeed(tr, cheat)
    cfeed(tr, dict(cheat, x=90000.0, odometer=1900.0))                      # jumps 90 km in one frame
    cfeed(tr, dict(cheat, on_job=False, job_delivered=True, job_delivered_revenue=999999, job_finished_time=520,
                   job_delivered_distance_km=900.0, x=90000.0, odometer=1900.0))
    cj = db.one("SELECT * FROM jobs WHERE cargo='Gold'")
    fl = set((cj["flags"] or "").split(","))
    check(cj["status"] == "pending_review", "faked delivery is held, not paid")
    check({"teleport", "odometer", "unobserved", "too-fast", "revenue"} <= fl, f"all five cheat checks fire ({sorted(fl)})")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE job_id=? AND category IN ('freight','driver pay','earnings','contract bonus')", (cj["id"],), 0) == 0 and
          db.val("SELECT xp FROM users WHERE id=?", (mgr_id,)) == 0, "no money or XP until a manager approves")
    check(drv.post(f"/jobs/{cj['id']}/review", {"action": "approve"}).status_code in (302, 403) and
          db.val("SELECT status FROM jobs WHERE id=?", (cj["id"],)) == "pending_review", "a driver can't approve it")
    boss.post(f"/jobs/{cj['id']}/review", {"action": "reject"})
    check(db.val("SELECT status FROM jobs WHERE id=?", (cj["id"],)) == "rejected" and
          db.val("SELECT COUNT(*) FROM ledger WHERE job_id=? AND category IN ('freight','driver pay','earnings','contract bonus')", (cj["id"],), 0) == 0,
          "manager rejects it; nothing is ever paid")
    db.set_setting("agent_jobs_need_approval", "1")
    class _Clock:                      # one second per frame, so the short drive counts as observed
        t = time.time()

        def time(self):
            return self.t
    clk = _Clock()
    service.time = clk
    honest = dict(cheat, cargo="Milk", cargo_id="milk", planned_distance_km=1, job_starting_time=900, license_plate="OK")
    tr2 = service.JobTracker(mgr_id)
    cfeed(tr2, dict(honest, on_job=False))
    for i in range(12):
        clk.t += 1
        cfeed(tr2, dict(honest, speed=25.0, x=i * 30.0, odometer=1000.0 + i * 0.1))
    cfeed(tr2, dict(honest, on_job=False, job_delivered=True, job_delivered_revenue=4000, job_finished_time=950,
                    job_delivered_distance_km=1.2, odometer=1001.2))
    service.time = time
    hj = db.one("SELECT * FROM jobs WHERE cargo='Milk'")
    check(hj["status"] == "pending_review" and not set(hj["flags"].split(",")) - {"", "joined-late"},
          f"with the setting on, a clean remote delivery is held too (flags {hj['flags']!r})")
    boss.post(f"/jobs/{hj['id']}/review", {"action": "approve"})
    check(db.val("SELECT status FROM jobs WHERE id=?", (hj["id"],)) == "delivered" and
          db.val("SELECT COUNT(*) FROM ledger WHERE job_id=? AND category='freight'", (hj["id"],), 0) == 1, "approval pays it out")
    db.set_setting("agent_jobs_need_approval", "0")

    print("Fleet Map learning (tampered telemetry)")
    db.ex("DELETE FROM road_points")
    db.ex("DELETE FROM map_places")
    clk.t = time.time()
    service.time = clk
    t3 = service.JobTracker(mgr_id)
    roam = dict(cheat, on_job=False, license_plate="MAP", speed=20.0)
    for i in range(30):                                      # honest driving: 20 m/s, one frame a second
        clk.t += 1
        cfeed(t3, dict(roam, x=1000.0 + i * 300.0, z=500.0))
    honest = db.val("SELECT COUNT(*) FROM road_points", (), 0)
    check(honest >= 20, f"real driving is learned ({honest} road points over 9 km)")
    for i in range(200):                                     # teleporting: 50 km between frames
        clk.t += 1
        cfeed(t3, dict(roam, x=float(100000 + i * 50000), z=float(-i * 40000)))
    check(db.val("SELECT COUNT(*) FROM road_points", (), 0) == honest, "teleport jumps add no fake roads")
    t3._road_today = t3.ROAD_DAILY_CAP
    t3._road_cap_t = clk.t
    for i in range(20):
        clk.t += 1
        cfeed(t3, dict(roam, x=1000.0 + i * 300.0, z=9000.0))
    check(db.val("SELECT COUNT(*) FROM road_points", (), 0) == honest, "daily per-driver cap stops a road flood")
    for i in range(5):
        cfeed(t3, dict(roam, x=float("1e9"), z=float("1e9")))
    check(db.val("SELECT COUNT(*) FROM road_points WHERE ABS(x) > 3000000", (), 0) == 0, "out-of-world positions ignored")
    t4 = service.JobTracker(mgr_id)
    fake = dict(cheat, cargo="Fake", cargo_id="fake", source_city="Denver", destination_city="Atlantis",
                job_starting_time=61000, license_plate="FAKE")
    clk.t += 1
    cfeed(t4, dict(fake, on_job=False))
    clk.t += 1
    cfeed(t4, fake)
    clk.t += 1
    cfeed(t4, dict(fake, on_job=False, job_delivered=True, job_delivered_revenue=999999, job_finished_time=61010,
                   x=5e5, z=5e5))
    check(db.val("SELECT COUNT(*) FROM map_places", (), 0) == 0, "a faked delivery can't plant or move a city on the map")
    service.time = time
    d3_id = create_user("drv3", "Driver Three", "driverpw33", role="driver")
    d3 = Browser(app)
    d3.login("drv3", "driverpw33")
    r = d3.get("/api/map?game=ats")
    check(r.status_code == 200 and Browser(app).get("/api/map").status_code == 401, "map data needs a login")
    check(d3.get("/api/map?game=../../etc").get_json()["game"] in ("ats", "ets2"), "map game parameter is whitelisted")

    print("Dispatch dropdowns")
    db.insert("jobs", user_id=d3_id, game="ats", source="telemetry", status="delivered", cargo=XSS, source_city=XSS,
              destination_city="Reno", source_company=XSS, destination_company="X", started_at=1, finished_at=1)
    page = boss.get("/dispatch").get_data(as_text=True)
    check(XSS not in page and "<\\/script>" not in page.replace("\\u003c", ""), "driver-reported names are escaped inside the page's data")
    before = db.val("SELECT COUNT(*) FROM contracts", (), 0)
    boss.post("/dispatch/new", {"destination_city_pick": "x'); DROP TABLE contracts;--|Reno", "game": "ats",
                                "cargo_pick": "a b|C", "source_city": "Modville", "source_company_pick": "Z" * 500})
    row = db.one("SELECT * FROM contracts ORDER BY id DESC LIMIT 1")
    check(db.val("SELECT COUNT(*) FROM contracts", (), 0) == before + 1 and row["destination_city_id"] in ("", "reno")
          and row["cargo_id"] == "" and len(row["source_company"]) <= 64, "dropdown values are validated and length-capped")
    check(d3.post("/dispatch/new", {"destination_city_pick": "|Reno", "game": "ats"}).status_code == 403,
          "drivers can't post contracts")

    print("HTTPS front door (LAN mode)")
    import hashlib
    import http.client
    import ssl
    import threading
    import waitress
    from fmpro import tlsfront
    cert, keyf, pin = tlsfront.ensure_cert(tmp)
    check(os.path.exists(cert) and len(pin) == 64, "self-signed certificate created")
    app.wsgi_app = tlsfront.Middleware(app.wsgi_app)
    back = waitress.create_server(app, host="127.0.0.1", port=0)
    threading.Thread(target=back.run, daemon=True).start()
    fs = socket.socket()
    fs.bind(("127.0.0.1", 0))
    fport = fs.getsockname()[1]
    fs.close()
    front = tlsfront.start("127.0.0.1", fport, back.effective_port, cert, keyf)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    hc = http.client.HTTPSConnection("127.0.0.1", fport, context=ctx, timeout=10)
    hc.connect()
    check(hashlib.sha256(hc.sock.getpeercert(binary_form=True)).hexdigest() == pin and hc.sock.version() in ("TLSv1.2", "TLSv1.3"),
          "TLS handshake works and the certificate matches the fingerprint")
    hc.request("GET", "/login")
    r = hc.getresponse()
    body = r.read()
    cookie = r.getheader("Set-Cookie") or ""
    check(r.status == 200 and b"Sign in" in body and "Secure" in cookie, "page served over HTTPS with a Secure cookie")
    hc.request("GET", "/static/css/app.css")
    r = hc.getresponse()
    check(r.status == 200 and len(r.read()) > 5000, "second request on the same encrypted connection works")
    hc.close()
    pc = http.client.HTTPConnection("127.0.0.1", fport, timeout=10)
    pc.request("GET", "/login")
    r = pc.getresponse()
    r.read()
    check(r.status == 200 and "Secure" not in (r.getheader("Set-Cookie") or ""), "plain HTTP still works from this PC itself")
    pc.close()
    a_sock, b_sock = socket.socketpair()
    b_sock.sendall(b"GET /jobs?x=1 HTTP/1.1\r\nHost: 192.168.1.20:8760\r\n\r\n")
    tlsfront._redirect_to_https(a_sock, 8760)
    reply = b_sock.recv(4096).decode()
    b_sock.close()
    check(reply.startswith("HTTP/1.1 308") and "Location: https://192.168.1.20:8760/jobs?x=1" in reply and "Sign in" not in reply,
          "plain HTTP from another PC gets only a redirect to https")
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
    try:
        old.maximum_version = ssl.TLSVersion.TLSv1_1
        s2 = old.wrap_socket(socket.create_connection(("127.0.0.1", fport), timeout=5))
        s2.close()
        weak = True
    except (ssl.SSLError, OSError, ValueError):
        weak = False
    check(not weak, "old TLS versions (1.0 / 1.1) are refused")
    with open(keyf) as f:
        check("PRIVATE KEY" in f.read(), "certificate key stays in the data folder")
    from fmpro.telemetry.service import _agent_connection
    try:
        _agent_connection(f"https://127.0.0.1:{fport}", "00" * 32)
        pinned = False
    except ConnectionError:
        pinned = True
    c2, _u = _agent_connection(f"https://127.0.0.1:{fport}", pin)
    c2.close()
    check(pinned, "agent refuses a server whose certificate doesn't match its pin")
    front.close()
    back.close()

    print("Licence")
    lease = dict(lic.lease)
    check(licensing.lease_problem(dict(lease, expires="2099-01-01T00:00:00Z"), lic.machine, key) is not None, "edited expiry date rejected")
    check(licensing.lease_problem(dict(lease, sig="00" * 64), lic.machine, key) is not None, "forged signature rejected")
    with open(os.path.join(tmp, "licence.json")) as f:
        saved = json.load(f)
    saved["lease"]["expires"] = "2099-01-01T00:00:00Z"
    p2 = os.path.join(tmp, "tampered.json")
    with open(p2, "w") as f:
        json.dump(saved, f)
    check(not licensing.Licence(p2).ok, "tampered licence file doesn't unlock")
    lic.lease = None
    lic._evaluate()
    page = anon.get("/activate").data
    check(key.encode() not in page, "locked activation screen doesn't reveal the purchase key")
    check(boss.get("/").status_code == 302 and boss.get("/api/live").status_code == 403, "a lost licence locks signed-in users too")
    lic.check()
    check(lic.ok, "licence restored")

    HUB.live.clear()
    db.close_all()
    srv.store.db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(lic_folder, ignore_errors=True)
    print()
    print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASSED")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
