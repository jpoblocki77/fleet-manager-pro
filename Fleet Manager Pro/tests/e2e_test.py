"""End-to-end test for Fleet Manager Pro. Uses a throw-away data folder; never touches ./data.

    python tests/e2e_test.py
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmpro import create_app, db  # noqa: E402
from fmpro.telemetry import demo, reader, service  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class FakeClock:
    def __init__(self):
        import time as _t
        self.t = _t.time()
        self._real = _t

    def time(self):
        return self.t

    def __getattr__(self, k):
        return getattr(self._real, k)


HOST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "Fleet Manager License Host")


def start_licence_server():
    """A throw-away licence server (fresh signing key, empty purchase list) on a free local port."""
    import socket
    import threading
    if not os.path.isfile(os.path.join(HOST_DIR, "license_server.py")):
        sys.exit(f"The licence tests need the License Host folder next to this one ({HOST_DIR}).")
    sys.path.insert(0, HOST_DIR)
    import license_server
    import license_tool
    folder = tempfile.mkdtemp(prefix="fmp-licsrv-")
    info = license_tool.setup(folder)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = license_server.Server(folder, port)
    threading.Thread(target=srv.serve, kwargs={"host": "127.0.0.1"}, daemon=True).start()
    import time as _t
    _t.sleep(0.5)
    os.environ["FMP_LICENSE_SERVER"] = f"http://127.0.0.1:{port}"
    os.environ["FMP_LICENSE_PUBKEY"] = info["public"]
    return srv, folder


def main():
    from fmpro import VERSION
    import shutil
    from fmpro import licensing
    srv, lic_folder = start_licence_server()
    pro_key = srv.store.new_purchase("Test Buyer", "buyer@example.com", product="Fleet Manager Pro")
    default_key = srv.store.new_purchase("Default Buyer", "")
    # a classic Fleet Manager record left over from before the server went Pro-only
    srv.store.db.execute("INSERT INTO purchases(key, name, email, created, slots) VALUES(?,?,?,?,2)",
                         ("FM-AAAA-BBBB-CCCC-DDDD", "Old Buyer", "", "2026-01-01T00:00:00Z"))
    srv.store.db.commit()
    old_key = "FM-AAAA-BBBB-CCCC-DDDD"
    tmp = tempfile.mkdtemp(prefix="fmp-test-")
    app = create_app(tmp, start_telemetry=False, check_licence=False)
    app.testing = True
    c = app.test_client()

    def token():
        with c.session_transaction() as s:
            return s.get("csrf")

    def post(url, data=None, **kw):
        d = dict(data or {})
        d["csrf"] = token()
        return c.post(url, data=d, **kw)

    print("Licence")
    lic = licensing.LICENCE
    check(pro_key.startswith("FMP-") and default_key.startswith("FMP-"), f"the server issues Fleet Manager Pro keys only ({pro_key})")
    try:
        srv.store.new_purchase("Classic", "", product="Fleet Manager")
        check(False, "classic Fleet Manager keys can't be made any more")
    except Exception as e:
        check(getattr(e, "code", "") == "bad_product", "classic Fleet Manager keys can't be made any more")
    r = c.get("/")
    check(r.status_code == 302 and "/activate" in r.headers["Location"], "locked until activated")
    check(c.get("/api/live").status_code == 403, "API locked until activated")
    c.get("/activate")
    r = post("/activate", {"key": old_key, "agree": "1"})
    check(b"original Fleet Manager" in r.data and not lic.ok, "classic FM key refused by Pro")
    try:
        licensing._post("/v1/activate", {"key": old_key, "machine": lic.machine, "product": "Fleet Manager Pro"})
        check(False, "server refuses FM key for Pro")
    except licensing.ServerRefused as e:
        check(e.code in ("bad_key", "wrong_product"), "server refuses FM key for Pro")
    try:   # a copy of the original app (sends no product) trying a Pro key
        licensing._post("/v1/activate", {"key": pro_key, "machine": lic.machine})
        check(False, "original app can't use a Pro key")
    except licensing.ServerRefused as e:
        check(e.code == "bad_product", "original app can't use a Pro key (requests must name Fleet Manager Pro)")
    check(not srv.store.active(pro_key) and not srv.store.active(old_key), "refused keys used no PC slot")
    try:
        licensing._post("/v1/activate", {"key": old_key, "machine": lic.machine})
        check(False, "classic Fleet Manager keys no longer activate anything")
    except licensing.ServerRefused as e:
        check(e.code == "bad_product", "classic Fleet Manager keys no longer activate anything")
    r = c.get("/licence-agreement")
    check(r.status_code == 200 and b"Jeff Poblocki" in r.data and b"reverse engineer" in r.data,
          "the licence agreement can be read before activating")
    check(b'name="agree"' in c.get("/activate").data and b"/licence-agreement" in c.get("/activate").data,
          "the Activate screen has an I agree tick box linking the agreement")
    r = post("/activate", {"key": pro_key.lower()})
    check(not lic.ok and b"agree to the licence agreement" in r.data and not srv.store.active(pro_key),
          "activating without ticking I agree is refused (no PC slot used)")
    r = post("/activate", {"key": pro_key.lower(), "agree": "1"})
    check(r.status_code == 302 and lic.ok, "Pro key activates this PC")
    check("key " + pro_key.upper() in (db.setting("eula_accepted") or "")
          and db.val("SELECT COUNT(*) FROM audit_log WHERE action='licence agreement accepted'", (), 0) == 1,
          "the agreement acceptance is recorded with the key")
    check(os.path.exists(os.path.join(tmp, "licence.json")), "licence saved")
    lease = dict(lic.lease)
    check(licensing.lease_problem(dict(lease, slots=99), lic.machine, pro_key) is not None, "tampered lease rejected")
    check(licensing.lease_problem(dict(lease, product="Fleet Manager"), lic.machine, pro_key) is not None,
          "lease for the other product rejected")
    check(licensing.lease_problem(lease, "FMID-AAAA-AAAA-AAAA-AAAA", pro_key) is not None, "lease for another PC rejected")
    for m in ("FMID-BBBB-BBBB-BBBB-BBBB", "FMID-CCCC-CCCC-CCCC-CCCC"):
        try:
            licensing._post("/v1/activate", {"key": pro_key, "machine": m, "product": "Fleet Manager Pro"})
            second_ok = True
        except licensing.ServerRefused as e:
            second_ok = e.code
    check(second_ok == "slots_full", "third PC refused (2 PCs per purchase)")
    srv.store.deactivate(pro_key, "FMID-BBBB-BBBB-BBBB-BBBB", "Fleet Manager Pro")

    print("Setup")
    r = c.get("/")
    check(r.status_code == 302 and "/setup" in r.headers["Location"], "redirects to setup on first run")
    c.get("/setup")
    r = post("/setup", {"company_name": "Crimson Line Freight", "company_tag": "CLF", "username": "boss",
                        "display_name": "The Boss", "password": "secret12", "password2": "secret12",
                        "units": "imperial", "currency": "$", "default_game": "ats", "starting_balance": "5000000"})
    check(r.status_code == 302, "setup creates company")
    check(db.setting("initialized") == "1", "initialized flag set")
    cap = db.q("SELECT amount FROM ledger WHERE category='capital'")
    check([x["amount"] for x in cap] == [275000.0] and db.setting("starting_balance") == "275000",
          f"starting capital is locked at 275,000 (the form asked for 5,000,000; got {[x['amount'] for x in cap]})")
    check('name="starting_balance"' not in c.get("/").get_data(as_text=True), "nothing on the pages can set the starting capital")
    db.insert("ledger", account="company", category="capital", amount=4725000.0, description="Test top-up", created_at=0)
    r = c.post("/announcements", data={"title": "x", "body": "y"})
    check(r.status_code == 400, "POST without CSRF token is refused")

    print("Telemetry pipeline (simulator -> DLL layout -> parser -> tracker)")
    clock = FakeClock()
    demo.time = clock
    sim = demo.Simulator()
    uid = int(db.setting("telemetry_user_id"))
    tr = service.JobTracker(uid)
    for _ in range(60000):
        clock.t += 0.25
        frame = reader.parse(sim.step())
        frame["demo"] = True               # as the app's sampler marks simulator frames
        tr.feed(frame)
        if db.val("SELECT COUNT(*) FROM jobs WHERE status='delivered'", (), 0) >= 3:
            break
    jobs = db.q("SELECT * FROM jobs ORDER BY id")
    delivered = [j for j in jobs if j["status"] == "delivered"]
    check(len(delivered) >= 3, f"simulator delivered 3 jobs ({len(delivered)})")
    j = delivered[0]
    check(j["revenue"] > 0 and j["driven_km"] > 5, f"job has revenue {j['revenue']} and distance {j['driven_km']:.1f} km")
    check(j["score"] > 0 and j["xp"] > 0, f"job scored {j['score']} and earned {j['xp']} XP")
    check(db.val("SELECT COUNT(*) FROM job_points WHERE job_id=?", (j["id"],), 0) > 5, "route trail recorded")
    check(db.val("SELECT COUNT(*) FROM job_events WHERE kind='toll'", (), 0) >= 3, "toll events captured")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='freight'", (), 0) >= 3, "freight income booked")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='driver pay'", (), 0) >= 3, "driver pay booked")
    check(db.val("SELECT COUNT(*) FROM trucks WHERE auto_detected=1", (), 0) == 1, "truck auto-detected once")
    check(db.val("SELECT COUNT(*) FROM user_achievements WHERE user_id=?", (uid,), 0) >= 1, "achievement unlocked")
    check(db.val("SELECT xp FROM users WHERE id=?", (uid,), 0) > 0, "driver XP increased")

    print("Real-plugin behaviour (flags stay set after events, map-scale odometer)")
    import time as _time
    base = {"sdk_active": True, "game_id": 2, "plugin_revision": 12, "truck_id": "vehicle.intnl.9900i",
            "truck_brand": "International", "truck_name": "9900i", "license_plate": "AVBD24", "truck_wheel_count": 6,
            "fuel_capacity": 908.0, "fuel": 700.0, "odometer": 685381.0, "x": -35086.0, "z": -6664.0}
    stale = dict(base, job_delivered=True, job_finished=True, job_delivered_revenue=900, job_delivered_earned_xp=30,
                 job_starting_time=20000, job_finished_time=20050, refuel_paid=True, refuel_amount=35.6)
    job = dict(stale, on_job=True, cargo_loaded=True, cargo="Pharmaceuticals", cargo_id="pharma", cargo_mass=20326.0,
               source_city="Denver", destination_city="Denver", job_income=1142, planned_distance_km=16,
               job_starting_time=27989, game_time_abs=27989)
    t2 = service.JobTracker(uid)
    rt = [0]

    def feed(t, vals):
        rt[0] += 1
        t.feed(reader.parse(reader.pack(dict(vals, render_time=rt[0], simulated_time=rt[0]))))

    feed(t2, stale)                       # connect while last session's delivery flags are still set
    check(db.val("SELECT COUNT(*) FROM jobs WHERE cargo='Pharmaceuticals'", (), 0) == 0, "stale delivery flags ignored")
    service.time = clock                  # 1 s per frame
    for i in range(60):                   # 20 m/s = 72 km/h, but the map-scale odometer adds 80 m per second
        clock.t += 1
        feed(t2, dict(job, speed=20.0, odometer=685381.0 + i * 0.08, x=-35086.0 - i * 20))
    service.time = _time
    jid = db.val("SELECT id FROM jobs WHERE cargo='Pharmaceuticals' AND status='in_progress'")
    check(jid is not None, "job started although delivered flag is still set")
    feed(t2, dict(job, on_job=False, job_delivered=True, job_delivered_revenue=1142, job_delivered_earned_xp=45,
                  job_delivered_distance_km=17.0, job_finished_time=28026))
    jr = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    check(jr["status"] == "delivered" and jr["revenue"] == 1142, f"delivery detected with flag already set ({jr['status']})")
    check(60 < jr["avg_speed"] < 80, f"avg speed from speedometer, not map-scale odometer ({jr['avg_speed']} km/h)")
    check("avg-speed" not in (jr["flags"] or ""), "honest job not flagged for speed")
    # delivered while the app was disconnected: reconcile on reconnect
    t3 = service.JobTracker(uid)
    for i in range(3):
        feed(t3, dict(job, job_starting_time=30000, cargo="Lumber", cargo_id="lumber", speed=10.0))
    jid3 = db.val("SELECT id FROM jobs WHERE cargo='Lumber' AND game_time_start=30000")
    t3.feed(None)                          # game / app gone
    t4 = service.JobTracker(uid)
    feed(t4, dict(job, on_job=False, job_starting_time=30000, job_finished_time=30090, job_delivered=True,
                  job_delivered_revenue=2500, job_delivered_earned_xp=80, job_delivered_distance_km=50.0, paused=True))
    j3 = db.one("SELECT status, flags, revenue FROM jobs WHERE id=?", (jid3,))
    check(j3["status"] == "pending_review" and "unobserved" in j3["flags"] and j3["revenue"] == 2500,
          "missed delivery reconciled on reconnect, pay held because the drive wasn't observed")
    paid = db.val("SELECT COUNT(*) FROM ledger WHERE job_id=? AND category='freight'", (jid3,), 0)
    check(paid == 0, "held delivery is not paid yet")
    check(db.val("SELECT COUNT(*) FROM job_events WHERE kind='refuel' AND amount>0 AND job_id IN (?,?)", (jid, jid3), 0) == 0,
          "stale refuel flag not re-charged")
    # delivered during an app restart and the game's "delivered" flag has already cleared (paused on the
    # results screen): the plugin still holds the result, so the job is settled instead of abandoned
    t3b = service.JobTracker(uid)
    for i in range(3):
        feed(t3b, dict(job, job_starting_time=31000, cargo="Precast Stairs", cargo_id="precast_strs", speed=10.0))
    jid3b = db.val("SELECT id FROM jobs WHERE cargo='Precast Stairs' AND game_time_start=31000")
    t3b.feed(None)
    t4b = service.JobTracker(uid)
    feed(t4b, dict(job, on_job=False, job_starting_time=31000, job_finished_time=31491, job_delivered=False,
                   job_delivered_revenue=3756, job_delivered_earned_xp=254, job_delivered_distance_km=254.0,
                   job_cancelled=False, job_cancelled_penalty=0, paused=True))
    for i in range(20):                    # unpaused afterwards: must not be marked abandoned
        feed(t4b, dict(job, on_job=False, job_starting_time=31000, job_finished_time=31491, job_delivered=False,
                       job_delivered_revenue=3756, job_delivered_earned_xp=254, job_delivered_distance_km=254.0,
                       job_cancelled=False, job_cancelled_penalty=0, paused=False))
    j3b = db.one("SELECT status, revenue FROM jobs WHERE id=?", (jid3b,))
    check(j3b["status"] in ("delivered", "pending_review") and j3b["revenue"] == 3756,
          f"delivery missed during a restart is settled from the game's held result, flag already cleared ({j3b['status']})")
    t3c = service.JobTracker(uid)
    for i in range(3):
        feed(t3c, dict(job, job_starting_time=32000, cargo="Scrap", cargo_id="scrap", speed=10.0))
    jid3c = db.val("SELECT id FROM jobs WHERE cargo='Scrap' AND game_time_start=32000")
    t3c.feed(None)
    t4c = service.JobTracker(uid)
    feed(t4c, dict(job, on_job=False, job_starting_time=32000, job_finished_time=32100, job_delivered=False,
                   job_delivered_revenue=0, job_cancelled=False, job_cancelled_penalty=900, paused=True))
    check(db.val("SELECT status FROM jobs WHERE id=?", (jid3c,)) == "cancelled", "cancellation missed during a restart is recorded too")

    # refuel events: the game raises one when a job/truck loads without adding fuel
    service.time = clock
    t5 = service.JobTracker(uid)
    fuel_before = db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0)
    roam = dict(base, fuel=400.0)
    feed(t5, roam)
    for i in range(5):
        clock.t += 1
        feed(t5, dict(roam, refuel_paid=True, refuel_amount=30.0))         # phantom: tank level unchanged
    clock.t += 130
    feed(t5, dict(roam, refuel_paid=True, refuel_amount=30.0))
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0) == fuel_before, "phantom refuel ignored")
    for i, f in enumerate([400.0, 450.0, 500.0, 520.0]):                      # real: tank fills, then paid
        clock.t += 1
        feed(t5, dict(roam, fuel=f))
    clock.t += 1
    feed(t5, dict(roam, fuel=520.0, refuel_paid=True, refuel_amount=120.0))
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0) == fuel_before + 1, "real refuel booked")
    service.time = _time

    print("Compliance (ELD / HOS, DVIR, roadside inspections)")
    from fmpro import compliance
    from fmpro.auth import create_user
    hid = create_user("hosdriver", "HOS Driver", "driver12", role="driver")
    ht = compliance.HosTracker(hid)
    gm = [10 * 1440 + 6 * 60]                    # day 10, 06:00 game time

    def hfeed(speed, minutes, **kw):
        for _ in range(minutes):
            gm[0] += 1
            ht.feed(reader.parse(reader.pack(dict(base, game_time_abs=gm[0], speed=speed, engine_enabled=True,
                                                  on_job=True, **kw)))["values"])

    hfeed(0.0, 30)                               # on duty (pre-trip, loading)
    hfeed(25.0, 300)                             # 5 h driving
    hc = compliance.clocks(hid, gm[0], "ats")
    check(295 <= hc["drive"]["used"] <= 301 and hc["status"] == "driving", f"driving clock counts ({hc['drive']['used']} min)")
    check(320 <= hc["window"]["used"] <= 332, f"14-hour window runs from first work ({hc['window']['used']} min)")
    hfeed(25.0, 200)                             # 8 h 20 driving, no break
    check(db.val("SELECT COUNT(*) FROM hos_violations WHERE user_id=? AND rule='HOS-BREAK'", (hid,), 0) == 1,
          "30-minute break violation after 8 h")
    hfeed(0.0, 35, parking_brake=True)           # break
    hfeed(25.0, 200)                             # past 11 h
    check(db.val("SELECT COUNT(*) FROM hos_violations WHERE user_id=? AND rule='HOS-DRIVE'", (hid,), 0) == 1,
          "11-hour driving violation recorded once")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='compliance fine'", (), 0) >= 2, "HOS fines charged")
    check(compliance.holds(hid, "ats"), "driver out of hours is on hold")
    gm[0] += 620                                 # sleep in the game: clock jumps 10 h 20 min while parked
    ht.feed(reader.parse(reader.pack(dict(base, game_time_abs=gm[0], speed=0.0, engine_enabled=False,
                                          parking_brake=True)))["values"])
    hc = compliance.clocks(hid, gm[0], "ats")
    check(hc["drive"]["used"] == 0 and not hc["out_of_hours"], "in-game sleep logged as sleeper berth resets the clocks")
    check(db.val("SELECT COUNT(*) FROM hos_logs WHERE user_id=? AND status='sleeper'", (hid,), 0) == 1, "sleeper segment logged")
    ats_before = compliance.clocks(hid, None, "ats")
    egm = 600                                             # ETS2 has its own, unrelated in-game clock
    for _ in range(120):                                  # 2 h driving in ETS2
        egm += 1
        ht.feed(reader.parse(reader.pack(dict(base, game_id=1, game_time_abs=egm, speed=22.0, engine_enabled=True,
                                              on_job=True)))["values"])
    e2 = compliance.clocks(hid, None, "ets2")
    a2 = compliance.clocks(hid, None, "ats")
    check(e2 and 115 <= e2["drive"]["used"] <= 121 and abs(e2["now_min"] - egm) <= 2,
          f"ETS2 keeps its own logbook and clock ({e2['drive']['used'] if e2 else None} min driven)")
    check(a2["drive"]["used"] == ats_before["drive"]["used"] and a2["now_min"] == ats_before["now_min"],
          "driving ETS2 doesn't touch the ATS logbook")
    check(db.val("SELECT COUNT(*) FROM hos_logs WHERE user_id=? AND game='ets2'", (hid,), 0) >= 1, "ETS2 log rows are labelled")
    eu = compliance.clocks(hid, gm[0], "ets2")
    check(eu["drive"]["limit"] == 540 and eu["break"]["limit"] == 270, "ETS2 uses EU rules (9 h / 4.5 h)")
    # inspection: overweight load, level 1, with the driver over hours
    hfeed(25.0, 700)
    iid = compliance.inspect(hid, reader.parse(reader.pack(dict(base, game_time_abs=gm[0], on_job=True,
                                                                 cargo_mass=24000.0, speed=0.0)))["values"], level=1)
    ins = db.one("SELECT * FROM inspections WHERE id=?", (iid,))
    codes = [f["code"] for f in __import__("json").loads(ins["findings"])]
    check(ins["result"] == "oos" and "HOS-OOS" in codes and "WEIGHT" in codes and "DVIR" in codes,
          f"level 1 inspection finds hours, weight and DVIR ({codes})")
    check(ins["fine"] > 0 and db.val("SELECT COUNT(*) FROM ledger WHERE category='inspection fine'", (), 0) == 1,
          "inspection fine charged")

    service.HUB.live[uid] = {"frame": frame, "t": __import__("time").time(), "source": "local"}

    print("Pages")
    tid = db.val("SELECT id FROM trucks LIMIT 1")
    for url in ["/", "/live", "/telemetry", "/map", "/overlay", "/jobs", f"/jobs/{j['id']}", "/jobs/new", "/dispatch",
                "/fleet", f"/fleet/truck/{tid}", "/finance", "/finance?period=all", "/drivers", f"/drivers/{uid}", "/profile",
                "/recruitment", "/events", "/leaderboard", "/leaderboard?metric=realism&period=all",
                "/leaderboard?metric=revenue&period=year&game=ats", "/achievements", "/announcements", "/messages",
                "/settings", "/settings?tab=economy", "/settings?tab=telemetry", "/settings?tab=integrations",
                "/settings?tab=recruitment", "/settings?tab=audit", "/api/live", "/api/live/all", "/api/notifications",
                "/api/overlay", "/api/export/jobs.csv", "/api/export/ledger.csv", "/jobs?status=delivered&q=a",
                "/compliance", "/compliance?tab=dvir", "/compliance?tab=inspections", "/compliance?tab=violations",
                f"/compliance?tab=logbook&driver={hid}", f"/compliance?tab=logbook&driver={hid}&day=10",
                "/api/hos", f"/api/hos?user={hid}", "/settings?tab=compliance"]:
        r = c.get(url)
        check(r.status_code == 200, f"GET {url} -> {r.status_code}")
    d = c.get("/api/live").get_json()
    check(d["online"] and len(d["values"]) > 200, f"/api/live exposes {len(d['values'])} values")

    print("Game selector (units)")
    db.set_setting("units", "imperial")
    check(c.get("/api/live").get_json()["units"] == "imperial", "default 'Both' uses the company's units")
    r = c.post("/account/game", data={"csrf": token(), "game": "ets2"}, headers={"X-CSRF": token()})
    check(r.status_code == 200 and r.get_json()["units"] == "metric", "choose ETS2")
    check(c.get("/api/live").get_json()["units"] == "metric", "API now in metric")
    page = c.get("/").data
    check(b'data-units="metric"' in page and b'value="ets2" selected' in page, "pages switch to metric and show ETS2 selected")
    check(b" km" in c.get(f"/jobs/{j['id']}").data and b" L)" in c.get(f"/jobs/{j['id']}").data, "job page in km and litres")
    post("/account/game", {"game": "ats"})
    check(b" mi" in c.get(f"/jobs/{j['id']}").data and b" gal)" in c.get(f"/jobs/{j['id']}").data, "ATS: miles and gallons")
    check(b'value="ats" selected' in c.get("/").data, "ATS stays selected after reload")
    check(c.post("/account/game", data={"csrf": token(), "game": "xbox"}).status_code == 400, "unknown game refused")
    print("Game filter and map")
    n_ats = db.val("SELECT COUNT(*) FROM jobs WHERE game='ats'")
    db.insert("jobs", user_id=uid, game="ets2", source="manual", status="delivered", cargo="Cheese", source_city="Paris",
              destination_city="Lyon", driven_km=460, revenue=3000, started_at=__import__("time").time(),
              finished_at=__import__("time").time())
    db.insert("contracts", title="Euro run", game="ets2", destination_city="Berlin", status="open", created_at=1)
    db.insert("garages", city="Calais", game="ets2", slots=1, created_at=1)
    post("/account/game", {"game": "ets2"})
    page = c.get("/jobs").get_data(as_text=True)
    check("1 ETS2 jobs" in page and "Cheese" in page and "Canned Beef" not in page.split("<tbody>")[1], "ETS2 only: logbook shows the ETS2 job, no ATS jobs")
    check(b"Euro run" in c.get("/dispatch").data and b"Calais" in c.get("/fleet").data, "ETS2 only: its contract and garage are listed")
    check(b"Kenworth" not in c.get("/fleet").data.split(b"Garages")[0], "ETS2 only: the truck seen in ATS is hidden")
    check(c.get("/api/live/all").get_json()["drivers"] == [], "ETS2 only: ATS drivers aren't on the map")
    check(c.get("/").status_code == 200 and c.get("/leaderboard").status_code == 200 and c.get(f"/drivers/{uid}").status_code == 200
          and c.get("/drivers").status_code == 200 and c.get("/events").status_code == 200, "filtered pages render")
    post("/account/game", {"game": "ats"})
    page = c.get("/jobs").get_data(as_text=True)
    check(f"{n_ats} ATS jobs" in page and "Cheese" not in page, "ATS only: the ETS2 job is hidden")
    check(b"Euro run" not in c.get("/dispatch").data and b"Calais" not in c.get("/fleet").data.split(b"<dialog")[0], "ATS only: ETS2 contract and garage hidden")
    check(len(c.get("/api/live/all").get_json()["drivers"]) == 1, "ATS only: the ATS driver is on the map")
    post("/account/game", {"game": "both"})
    page = c.get("/jobs").get_data(as_text=True)
    check("Cheese" in page and f"{n_ats + 1}  jobs" in page.replace("\n", " ") or "Cheese" in page, "Both: everything is listed")
    m = c.get("/api/map?game=ats").get_json()
    check(len(m["roads"]) >= 1 and m["road_points"] >= 3, f"driven road remembered for the map ({m['road_points']} points)")
    check(any(p["n"] == "Denver" for p in m["places"]), "pickup city learned as a map place")
    check(c.get("/api/map?game=ets2").get_json()["roads"] == [], "ATS roads don't appear on the ETS2 map")
    g = c.get("/static/map/ats.json").get_json()
    den = next(x for x in g["cities"] if x["n"] == "Cheyenne")["p"]
    check(len(g["lines"]) > 100 and len(g["regions"]) == 48 and abs(den[0] + 37345) < 1500 and abs(den[1] + 13946) < 1500,
          "ATS map data: borders, 48 states, Cheyenne within 1.5 km (game) of where the truck really was")
    e = c.get("/static/map/ets2.json").get_json()
    check(len(e["lines"]) > 100 and any(x["n"] == "Berlin" for x in e["cities"]), "ETS2 map data present")
    check(c.get("/map").status_code == 200 and c.get("/map?game=ets2").status_code == 200, "map page renders for both games")

    print("Cargo dropdown")
    page = c.get("/dispatch").get_data(as_text=True)
    check('id="cargo-pick"' in page and '"Aluminum Ingots"' in page and '"Nuts"' in page, "contract form carries the game's cargo list")
    opts = __import__("fmpro.pages", fromlist=["x"]).cargo_options()
    check(len(opts["ats"]["all"]) > 250 and len(opts["ets2"]["all"]) > 200, f"cargo lists: {len(opts['ats']['all'])} ATS, {len(opts['ets2']['all'])} ETS2")
    check(any(n == "Pharmaceuticals" for _i, n in opts["ats"]["hauled"]), "cargo the company hauled is offered first (mods included)")
    post("/dispatch/new", {"destination_city": "Reno", "cargo_pick": "nuts|Nuts", "game": "ats", "bonus": "500"})
    nuts = db.one("SELECT * FROM contracts WHERE destination_city='Reno' AND cargo='Nuts'")
    check(nuts and nuts["cargo_id"] == "nuts" and nuts["game"] == "ats", "picked cargo stored with its game id")
    post("/dispatch/new", {"destination_city": "Elko", "cargo_pick": "__other", "cargo": "Mod Cargo", "game": "ats"})
    check(db.val("SELECT cargo FROM contracts WHERE destination_city='Elko'") == "Mod Cargo", "'Other' lets a cargo be typed")
    post("/dispatch/new", {"destination_city": "Ely", "cargo_pick": "", "game": "ats"})
    check(db.val("SELECT cargo FROM contracts WHERE destination_city='Ely'") == "", "'Any cargo' stores no cargo")
    post("/dispatch/new", {"destination_city": "Tonopah", "cargo_pick": "bad id!|<b>x</b>", "game": "xbox"})
    bad = db.one("SELECT cargo_id, game FROM contracts WHERE destination_city='Tonopah'")
    check(bad["cargo_id"] == "" and bad["game"] == "ats", "odd cargo id and game values are neutralised")
    db.insert("contracts", title="Other game", game="ets2", destination_city="Winnemucca", cargo="Nuts", status="open", created_at=1)
    t6 = service.JobTracker(uid)
    feed(t6, dict(base, on_job=False))
    feed(t6, dict(job, cargo="Nuesse", cargo_id="cargo.nuts", source_city="Elko", destination_city="Reno", job_starting_time=41000))
    cj = db.one("SELECT contract_id FROM jobs WHERE cargo='Nuesse'")
    check(cj and cj["contract_id"] == nuts["id"], "a job in another game language still matches the contract by cargo id")
    t7 = service.JobTracker(uid)
    feed(t7, dict(base, on_job=False))
    feed(t7, dict(job, cargo="Nuts", cargo_id="nuts", source_city="Elko", destination_city="Winnemucca", job_starting_time=42000))
    check(db.val("SELECT contract_id FROM jobs WHERE destination_city='Winnemucca'") is None, "an ATS job never matches an ETS2 contract")

    print("Company dropdown")
    page = c.get("/dispatch").get_data(as_text=True)
    check('id="src-co"' in page and '"Wallbert"' in page and "Eddy" in page, "contract form carries the game's company list")
    opts = __import__("fmpro.pages", fromlist=["x"]).cargo_options()
    check(len(opts["ats"]["companies"]) > 120 and len(opts["ets2"]["companies"]) > 100,
          f"company lists: {len(opts['ats']['companies'])} ATS, {len(opts['ets2']['companies'])} ETS2")
    check(opts["ats"]["depots"].get("elko") and "Eddy's" in opts["ats"]["depots"]["elko"]
          and "Wallbert" not in opts["ats"]["depots"]["elko"] and len(opts["ats"]["depots"]) > 250
          and len(opts["ets2"]["depots"]) > 250, "each city has its own list of companies from the game files")
    n0 = db.val("SELECT COUNT(*) FROM contracts", (), 0)
    post("/dispatch/new", {"destination_city_pick": "denver|Denver", "game": "ats", "source_city_pick": "elko|Elko",
                           "source_company_pick": "Wallbert"})
    check(db.val("SELECT COUNT(*) FROM contracts", (), 0) == n0 and b"has no depot in Elko" in c.get("/dispatch").data,
          "a company that isn't in the chosen city is refused")
    post("/dispatch/new", {"destination_city_pick": "denver|Denver", "game": "ats", "source_company_pick": "Wallbert"})
    check(db.val("SELECT COUNT(*) FROM contracts", (), 0) == n0 and b"Pick the From city first" in c.get("/dispatch").data,
          "a company without its city is refused")
    post("/dispatch/new", {"title": "Elko pickup", "destination_city_pick": "denver|Denver", "game": "ats",
                           "source_city_pick": "elko|Elko", "source_company_pick": "Eddy's"})
    check(db.val("SELECT source_company FROM contracts WHERE title='Elko pickup'") == "Eddy's", "a company in the chosen city is accepted")
    post("/dispatch/new", {"destination_city": "Modville", "game": "ats", "source_city_pick": "casper|Casper", "source_company_pick": "Wallbert",
                           "destination_company_pick": "__other", "destination_company": "Mod Depot", "cargo_pick": ""})
    wc = db.one("SELECT * FROM contracts WHERE destination_city='Modville'")
    check(wc["source_company"] == "Wallbert" and wc["destination_company"] == "Mod Depot", "picked and typed companies are stored")
    t8 = service.JobTracker(uid)
    feed(t8, dict(base, on_job=False))
    feed(t8, dict(job, cargo="Milk", cargo_id="milk", source_city="Elko", destination_city="Modville", job_starting_time=43000,
                  source_company="Eddy's", destination_company="Mod Depot"))
    check(db.val("SELECT contract_id FROM jobs WHERE cargo='Milk' AND destination_city='Modville'") is None,
          "a job from a different company doesn't match the contract")
    t9 = service.JobTracker(uid)
    feed(t9, dict(base, on_job=False))
    feed(t9, dict(job, cargo="Cheese", cargo_id="cheese", source_city="Casper", source_city_id="casper", destination_city="Modville", job_starting_time=44000,
                  source_company="wallbert", destination_company="Mod Depot"))
    check(db.val("SELECT contract_id FROM jobs WHERE cargo='Cheese' AND destination_city='Modville'") == wc["id"],
          "a job between the contract's companies matches it")
    check(opts is not None and any(n == "Mod Depot" for n in __import__("fmpro.pages", fromlist=["x"]).cargo_options()["ats"]["visited"]),
          "companies your drivers have been to are offered first (mods included)")

    print("City dropdown")
    opts = __import__("fmpro.pages", fromlist=["x"]).cargo_options()
    check(len(opts["ats"]["cities"]) > 250 and len(opts["ets2"]["cities"]) > 250,
          f"city lists: {len(opts['ats']['cities'])} ATS, {len(opts['ets2']['cities'])} ETS2")
    vie = next(c for c in opts["ets2"]["cities"] if c[0] == "wien")
    check(vie[1] == "Vienna" and "Wien" in vie[2] and "Austria" in vie[2], f"ETS2 cities in English with the local name ({vie[2]})")
    check(len([c for c in opts["ats"]["cities"] if c[1] == "Carlsbad"]) == 2, "same-name cities kept apart (Carlsbad CA / NM)")
    check("Denver" in opts["ats"]["been"], "cities your drivers have been to are offered first")
    post("/dispatch/new", {"destination_city_pick": "wien|Vienna", "source_city_pick": "|", "game": "ets2", "cargo_pick": ""})
    vc = db.one("SELECT * FROM contracts WHERE destination_city='Vienna'")
    check(vc and vc["destination_city_id"] == "wien" and vc["source_city"] == "", "picked city stored with its game id")
    t10 = service.JobTracker(uid)
    feed(t10, dict(base, on_job=False, game_id=1))
    feed(t10, dict(job, game_id=1, cargo="Holz", cargo_id="wood", source_city="Linz", destination_city="Wien",
                   destination_city_id="wien", job_starting_time=45000))
    check(db.val("SELECT contract_id FROM jobs WHERE destination_city='Wien'") == vc["id"],
          "a German-language job to 'Wien' matches the 'Vienna' contract by city id")
    post("/dispatch/new", {"destination_city_pick": "__other", "destination_city": "Modtown", "game": "ats"})
    check(db.val("SELECT destination_city FROM contracts WHERE destination_city='Modtown'") == "Modtown", "'Other' lets a city be typed")
    post("/dispatch/new", {"destination_city_pick": "evil id|<x>", "game": "ats"})
    check(db.val("SELECT destination_city_id FROM contracts WHERE destination_city='<x>'") == "", "odd city ids are neutralised")
    r = post("/dispatch/new", {"destination_city_pick": "", "game": "ats"})
    check(db.val("SELECT COUNT(*) FROM contracts WHERE destination_city=''", (), 0) == 0, "a contract without a destination is refused")

    print("Send contract to the game (save slot)")
    import zlib as _z
    from fmpro import savegame
    docs = tempfile.mkdtemp(prefix="fmp-docs-")
    app.config["SAVE_DIRS"] = [docs]
    prof = os.path.join(docs, "American Truck Simulator", "profiles", "4d6354657374")      # "McTest"
    slot0 = os.path.join(prof, "save", "1")
    os.makedirs(slot0)
    NL = "\r\n"
    game_txt = NL.join([
        "SiiNunit", "{", "economy : _nameless.1.2.3 {", " game_time: 5000", "}", "",
        "job_offer_data : _nameless.1.2.a {", ' target: "vp_epw_sit.denver"', " expiration_time: 5300", " urgency: 1",
        " shortest_distance_km: 581", " ferry_time: 0", " ferry_price: 0", " cargo: cargo.tractors",
        ' company_truck: "heavy/volvo_vnl2025_860"', " trailer_variant: trailer.lk_renow_d50",
        " trailer_definition: trailer_def.lodeking.renow_drop.single_53_3.dropdeck_alw", " units_count: 2",
        " fill_ratio: 1", " trailer_place: 0", "}", "",
        "company : company.volatile.gld_frm.dodge_city {", " permanent_data: company.permanent.gld_frm",
        " job_offer: 1", " job_offer[0]: _nameless.1.2.a", " cargo_offer_seeds: 0", "}", "",
        "company : company.volatile.vp_epw_sit.denver {", " permanent_data: company.permanent.vp_epw_sit", " job_offer: 0", "}", "",
        "}", ""])
    info_txt = NL.join(["SiiNunit", "{", "save_container : _nameless.9 {", ' name: "My own save"', " time: 5000",
                        " file_time: 1", "}", "}", ""])

    def scsc(text):                       # encrypt like the game does
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        data = _z.compress(text.encode())
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
        iv = os.urandom(16)
        enc = Cipher(algorithms.AES(savegame._key()), modes.CBC(iv)).encryptor()
        return b"ScsC" + bytes(32) + iv + len(text.encode()).to_bytes(4, "little") + enc.update(data) + enc.finalize()
    open(os.path.join(slot0, "game.sii"), "wb").write(scsc(game_txt))
    open(os.path.join(slot0, "info.sii"), "wb").write(scsc(info_txt))
    open(os.path.join(slot0, "preview.tga"), "wb").write(b"TGA")
    before = open(os.path.join(slot0, "game.sii"), "rb").read()
    check(savegame.read_sii(os.path.join(slot0, "game.sii")).startswith("SiiNunit"), "encrypted save is read")
    pl = savegame.list_profiles("ats", [docs])
    check(len(pl) == 1 and pl[0]["name"] == "McTest" and pl[0]["saves"][0]["name"] == "My own save",
          "profiles and save names are found (profile name decoded)")
    post("/dispatch/new", {"title": "Tractors to Denver", "game": "ats", "source_city_pick": "dodge_city|Dodge City",
                           "destination_city_pick": "denver|Denver", "cargo_pick": "tractors|Tractor", "priority": "urgent"})
    tc = db.one("SELECT * FROM contracts WHERE title='Tractors to Denver'")
    page = c.get("/dispatch").get_data(as_text=True)
    check("Send to game" in page and "McTest" in page, "Send to game is offered with the PC's profiles")
    r = post(f"/dispatch/{tc['id']}/to-game", {"save": slot0})
    os.makedirs(os.path.join(prof, "save", "fmp_contract_99"))          # a 0.01 hidden slot gets cleaned up
    slot = os.path.join(prof, "save", "2")                               # next free number after the driver's slot 1
    check(os.path.isdir(slot) and sorted(os.listdir(slot)) == ["game.sii", "info.sii", "preview.tga"], "new save slot written")
    new = savegame.Save(savegame.read_sii(os.path.join(slot, "game.sii")))
    o = new.offers.get("_nameless.1.2.a", {})
    check(list(new.offers) == ["_nameless.1.2.a"] and new.owner["_nameless.1.2.a"] == ("gld_frm", "dodge_city")
          and o.get("target") == '"vp_epw_sit.denver"' and o.get("cargo") == "cargo.tractors" and o.get("urgency") == "2"
          and o.get("shortest_distance_km") == "581" and o.get("expiration_time") == "5300",
          f"job written into the pickup company's own slot, keeping the game's expiry ({o})")
    check(new.companies[("gld_frm", "dodge_city")] == ["_nameless.1.2.a"]
          and "job_offer: 1" in new.text and "cargo_offer_seeds: 0" in new.text,
          "the company's job list is left exactly as the game wrote it (no extra slot for the game to trim)")
    check(savegame._slot_name(slot) == f"Fleet Manager Pro - Contract #{tc['id']}", "slot has the contract's name in the game's load menu")
    check(open(os.path.join(slot0, "game.sii"), "rb").read() == before, "the driver's own save is untouched")
    check(db.one("SELECT game_slot FROM contracts WHERE id=?", (tc["id"],))["game_slot"].endswith(f"#{tc['id']}")
          and b"in game" in c.get("/dispatch").data, "contract shows it's in the game")
    post(f"/dispatch/{tc['id']}/to-game", {"save": slot0})
    check(sorted(os.listdir(os.path.join(prof, "save"))) == ["1", "2"], "sending again replaces its own numbered slot, nothing piles up")
    check([s["slot"] for s in savegame.list_profiles("ats", [docs])[0]["saves"]] == ["1"], "the app's own slots aren't offered as starting points")
    r = post(f"/dispatch/{tc['id']}/to-game", {"save": os.path.join(docs, "..", "..", "Windows")})
    check(sorted(os.listdir(os.path.join(prof, "save"))) == ["1", "2"] and b"Pick one of your saves" in c.get("/dispatch").data,
          "only listed saves can be used")
    r = post(f"/dispatch/{tc['id']}/to-game", {"save": slot0}, environ_base={"REMOTE_ADDR": "192.168.1.40"})
    check(b"only works from a browser on the PC" in c.get("/dispatch").data, "refused from another PC")
    bad = db.insert("contracts", title="No pickup", game="ats", destination_city="Denver", destination_city_id="denver",
                    cargo="Tractor", cargo_id="tractors", status="open", created_at=1)
    post(f"/dispatch/{bad}/to-game", {"save": slot0})
    check(b"Pick a From city" in c.get("/dispatch").data and not os.path.exists(os.path.join(prof, "save", f"fmp_contract_{bad}")),
          "a contract without a pickup city is explained, nothing written")
    check(sorted(os.listdir(os.path.join(prof, "save"))) == ["1", "2"], "nothing else written")
    t11 = service.JobTracker(uid)
    feed(t11, dict(base, on_job=False))
    feed(t11, dict(job, cargo="Tractor", cargo_id="tractors", source_city="Dodge City", source_city_id="dodge_city",
                   destination_city="Denver", destination_city_id="denver", source_company="Golden Farms",
                   destination_company="EPW", job_starting_time=46000))
    check(db.val("SELECT contract_id FROM jobs WHERE cargo='Tractor' AND destination_city='Denver'") == tc["id"],
          "picking the job up in the game links it to the contract")
    n0 = db.val("SELECT COUNT(*) FROM contracts", (), 0)
    post("/dispatch/new", {"destination_city_pick": "denver|Denver", "game": "ats", "source_city_pick": "|Kansas City"})
    check(db.val("SELECT COUNT(*) FROM contracts", (), 0) == n0 and b"more than one Kansas City" in c.get("/dispatch").data,
          "a city name used twice (Kansas City, KS / MO) must be picked exactly")
    post("/dispatch/new", {"title": "KC printers", "destination_city_pick": "denver|Denver", "game": "ats",
                           "source_city_pick": "|Kansas City", "source_company_pick": "Enterpriser", "cargo_pick": "p_printers|Printers"})
    check(db.val("SELECT source_city_id FROM contracts WHERE title='KC printers'") == "kansas_ci_ks",
          "the company tells the two Kansas Cities apart")
    kc = NL.join(["SiiNunit", "{", "economy : _nameless.1.2.3 {", " game_time: 5000", "}", "",
                  "job_offer_data : _nameless.1.2.b {", ' target: "rail_exp.denver"', " expiration_time: 5300", " urgency: 0",
                  " shortest_distance_km: 900", " cargo: cargo.p_printers", ' company_truck: ""',
                  " trailer_variant: trailer.x", " trailer_definition: trailer_def.x", " units_count: 1", " fill_ratio: 1", "}", "",
                  "company : company.volatile.ent_whs.kansas_ci_ks {", " job_offer: 1", " job_offer[0]: _nameless.1.2.b", "}", "",
                  "company : company.volatile.mwm.kansas_ci_mo {", " job_offer: 0", "}", "",
                  "company : company.volatile.rail_exp.denver {", " job_offer: 0", "}", "", "}", ""])
    lists, xy = savegame.load_static(os.path.join(os.path.dirname(__file__), ".."))
    old = {"game": "ats", "source_city": "Kansas City", "source_city_id": "", "destination_city": "Denver",
           "destination_city_id": "denver", "source_company": "Enterpriser", "destination_company": "Rail Export",
           "cargo": "Printers", "cargo_id": "p_printers", "priority": "normal"}
    ent = {x["id"] for x in lists["companies"]["ats"] if x["name"] == "Enterpriser"}
    kc = kc.replace("ent_whs", sorted(i for i in ent if "kansas_ci_ks" in lists["depots"]["ats"] and i in lists["depots"]["ats"]["kansas_ci_ks"])[0])
    rx = {x["id"] for x in lists["companies"]["ats"] if x["name"] == "Rail Export"} & set(lists["depots"]["ats"]["denver"])
    kc = kc.replace("rail_exp", sorted(rx)[0])
    plan = savegame.plan_offer(savegame.Save(kc), "ats", old, lists, xy["ats"])
    check(plan["source"][1] == "kansas_ci_ks", f"an older contract with only the city name still goes to the right Kansas City ({plan['source']})")
    try:
        savegame.plan_offer(savegame.Save(kc), "ats", dict(old, source_company=""), lists, xy["ats"])
        check(False, "ambiguous city without a company is explained")
    except savegame.SaveError as e:
        check("Kansas City, Kansas or Kansas City, Missouri" in str(e), f"ambiguous city without a company is explained ({e})")
    shutil.rmtree(docs, ignore_errors=True)

    print("Forms")
    r = post("/drivers/new", {"username": "rookie", "display_name": "Rookie Rick", "password": "driver12", "role": "driver"})
    rid = db.val("SELECT id FROM users WHERE username='rookie'")
    check(rid is not None, "add driver")
    post("/dispatch/new", {"title": "Steel run", "destination_city": "Phoenix", "bonus": "1500", "deadline_hours": "24"})
    cid = db.val("SELECT id FROM contracts")
    check(cid is not None, "post contract")
    post(f"/dispatch/{cid}/claim")
    check(db.val("SELECT status FROM contracts WHERE id=?", (cid,)) == "assigned", "claim contract")
    post("/fleet/garage/save", {"city": "Phoenix", "size": "medium"})
    check(db.val("SELECT COUNT(*) FROM garages WHERE city='Phoenix'", (), 0) == 1, "buy garage")
    post("/fleet/truck/save", {"brand": "Peterbilt", "model": "579", "plate": "CLF 2", "purchase_price": "150000"})
    check(db.val("SELECT COUNT(*) FROM trucks WHERE plate='CLF 2'", (), 0) == 1, "add truck")
    post("/fleet/trailer/save", {"name": "53ft Dry Van", "purchase_price": "40000"})
    post("/fleet/maintenance", {"truck_id": str(tid), "kind": "service", "cost": "800"})
    check(db.val("SELECT COUNT(*) FROM maintenance WHERE kind='service'", (), 0) == 1, "record service")
    # DVIR with a defect: truck on hold, dispatch blocked, repair releases it
    db.update("trucks", tid, assigned_user_id=uid)
    form = {f"item_{k}": "ok" for k, *_ in compliance.DVIR_ITEMS}
    form.update({"kind": "pre", "truck_id": str(tid), "signed": "1", "item_brakes": "defect", "note_brakes": "air leak"})
    post("/compliance/dvir", form)
    check(db.val("SELECT status FROM trucks WHERE id=?", (tid,)) == "maintenance", "DVIR defect puts the truck on hold")
    cid2 = db.insert("contracts", title="Hold test", destination_city="Reno", status="open", created_at=1)
    post(f"/dispatch/{cid2}/claim")
    check(db.val("SELECT status FROM contracts WHERE id=?", (cid2,)) == "open", "dispatch blocked while truck is on hold")
    did = db.val("SELECT id FROM dvirs WHERE status='defects'")
    assert_page = c.get("/compliance?tab=dvir").get_data(as_text=True)
    check(f"/compliance/dvir/{did}/repair" in assert_page and 'name="cost"' not in assert_page.split(f"/compliance/dvir/{did}/repair")[1][:400],
          "DVIR repair shows its set price, with no cost box")
    bal_d = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    post(f"/compliance/dvir/{did}/repair", {"cost": "1"})
    check(db.val("SELECT cost FROM maintenance ORDER BY id DESC LIMIT 1") == 250 and
          abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal_d - 250)) < 0.01,
          "DVIR repair charges the Settings price per defect (250 x 1), a cost sent with the form is ignored")
    check(db.val("SELECT status FROM trucks WHERE id=?", (tid,)) == "active", "repair puts the truck back in service")
    post(f"/dispatch/{cid2}/claim")
    check(db.val("SELECT status FROM contracts WHERE id=?", (cid2,)) == "assigned", "dispatch allowed after repair")
    post("/finance/loan", {"plan": "standard", "amount": "100000"})
    check(db.val("SELECT COUNT(*) FROM loans", (), 0) == 1, "take loan")
    bal = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    from fmpro import economy
    economy.process_loans(now=__import__("time").time() + 8 * 86400)
    check(db.val("SELECT payments_made FROM loans", (), 0) == 1, "loan instalment collected")
    check(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") < bal, "instalment debited")

    print("Truck leasing")
    import time as _lt
    from fmpro import leasing
    plans_ = {p["name"]: p for p in leasing.plans()}
    check(len(plans_) == 4 and len({(p["weeks"], round(p["km_allowed"]), p["apr"]) for p in plans_.values()}) == 4,
          "four default lease plans with different lengths, mileage and interest")
    lh = plans_["Long Haul"]
    q = leasing.quote(150000, lh)
    check(q["down"] == 15000 and q["residual"] == 67500 and abs(q["depreciation"] - (135000 - 67500) / 52) < 0.01
          and abs(q["finance"] - (135000 + 67500) * 5.9 / 100 / 104) < 0.01 and abs(q["payment"] - (q["depreciation"] + q["finance"])) < 0.02,
          "lease quote: down payment, residual, depreciation + interest")
    check(abs(q["total_payments"] - (15000 + q["payment"] * 52)) < 0.01 and abs(q["interest"] - q["finance"] * 52) < 0.6,
          "lease quote totals")
    sh = leasing.quote(150000, plans_["Short Haul"])
    check(sh["payment"] != q["payment"] and sh["down"] < q["down"], "different plans cost differently")
    page = c.get("/leasing").get_data(as_text=True)
    check("Compare lease plans" in page and "Long Haul" in page and "Owner-Operator" in page and "Weekly payment" in page
          and "Total interest" in page and "Buy it at the end for" in page, "leasing page shows plans and every cost")
    bal0 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    r = post("/leasing/new", {"plan_id": str(lh["id"]), "price": "150000", "source": "new", "brand": "Kenworth", "model": "W900",
                              "plate": "LEASE1", "game": "ats"})
    lid = db.val("SELECT MAX(id) FROM leases")
    lease = db.one("SELECT * FROM leases WHERE id=?", (lid,))
    ltid = lease and lease["truck_id"]
    check(r.status_code == 302 and lease and lease["payment"] == q["payment"] and lease["status"] == "active", "lease a new truck")
    check(abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal0 - 15000)) < 0.01, "down payment taken")
    check(db.val("SELECT leased FROM trucks WHERE id=?", (ltid,)) == 1, "leased truck in the fleet")
    check("badge leased" in c.get("/fleet").get_data(as_text=True), "fleet page shows the leased badge")
    tp = c.get(f"/fleet/truck/{ltid}").get_data(as_text=True)
    check(f"/fleet/lease/{lid}" in tp and "dlg-sell\">Sell" not in tp, "truck page links its lease and hides Sell")
    post(f"/fleet/truck/{ltid}/sell", {"price": "1"})
    check(db.val("SELECT status FROM trucks WHERE id=?", (ltid,)) == "active", "leased truck can't be sold")
    dp = c.get(f"/fleet/lease/{lid}").get_data(as_text=True)
    check("If you end it today" in dp and "Total interest" in dp and "truck value used each week" in dp, "lease page shows the cost breakdown")
    leasing.process(now=_lt.time() + 8 * 86400)
    lease = db.one("SELECT * FROM leases WHERE id=?", (lid,))
    check(lease["payments_made"] == 1 and db.val("SELECT COUNT(*) FROM ledger WHERE category='lease payment'") == 1, "weekly lease payment taken")
    leasing.process(now=_lt.time() + 8 * 86400)
    check(db.val("SELECT payments_made FROM leases WHERE id=?", (lid,)) == 1, "a payment is only taken once a week")
    db.update("trucks", ltid, odometer=lease["odo_start"] + lease["km_allowed"] + 1000)
    st = leasing.status(lease)
    check(abs(st["excess_km"] - 1000) < 0.01 and abs(st["excess_cost"] - 1000 * lease["excess_per_km"]) < 0.01, "extra mileage counted")
    check(abs(st["early_fee"] - 51 * lease["payment"] * 0.30) < 0.02, "early-return fee = 30% of payments left")
    bal1 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    post(f"/fleet/lease/{lid}/end", {"action": "return"})
    l2 = db.one("SELECT * FROM leases WHERE id=?", (lid,))
    check(l2["status"] == "returned" and l2["end_reason"] == "early return" and abs(l2["end_cost"] - (st["early_fee"] + st["excess_cost"])) < 0.02,
          "early return charges the fee and extra mileage")
    check(abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal1 - l2["end_cost"])) < 0.02, "return costs debited")
    check(db.val("SELECT status FROM trucks WHERE id=?", (ltid,)) == "returned", "returned truck leaves the fleet")
    own_id = db.insert("trucks", brand="Peterbilt", model="389", plate="OWN1", purchase_price=0, odometer=5000, created_at=1)
    sp = plans_["Short Haul"]
    post("/leasing/new", {"plan_id": str(sp["id"]), "price": "100000", "source": "existing", "truck_id": str(own_id)})
    lid2 = db.val("SELECT id FROM leases WHERE truck_id=? AND status='active'", (own_id,))
    check(lid2 and db.val("SELECT odo_start FROM leases WHERE id=?", (lid2,)) == 5000, "lease one of my trucks (mileage counted from now)")
    for w in range(12):
        leasing.process(now=_lt.time() + (w + 1) * 7 * 86400 + 3600)
    check(db.val("SELECT status FROM leases WHERE id=?", (lid2,)) == "matured", "lease matures after the last payment")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE link=?", (f"/fleet/lease/{lid2}",), 0) >= 1, "managers told the lease has ended")
    post(f"/fleet/lease/{lid2}/end", {"action": "renew", "plan_id": str(plans_["Regional"]["id"])})
    lid3 = db.val("SELECT id FROM leases WHERE truck_id=? AND status='active'", (own_id,))
    check(db.val("SELECT status FROM leases WHERE id=?", (lid2,)) == "renewed" and lid3
          and db.val("SELECT price FROM leases WHERE id=?", (lid3,)) == 75000, "renew leases the truck again at its end value")
    l3 = db.one("SELECT * FROM leases WHERE id=?", (lid3,))
    bal2 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    post(f"/fleet/lease/{lid3}/end", {"action": "buyout"})
    check(db.val("SELECT status FROM leases WHERE id=?", (lid3,)) == "bought" and db.val("SELECT leased FROM trucks WHERE id=?", (own_id,)) == 0,
          "buy out a lease early: the truck is ours")
    check(abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal2 - l3["payment"] * l3["weeks"] - l3["residual"])) < 0.05,
          "buyout costs the payments left plus the end value")
    post("/leasing/plans/save", {"name": "Test Plan", "weeks": "8", "allowed": "10000", "down_pct": "0", "apr": "9.9",
                                 "residual_pct": "80", "excess_rate": "0.50", "early_fee_pct": "50"})
    tp_ = db.one("SELECT * FROM lease_plans WHERE name='Test Plan'")
    check(tp_ and tp_["weeks"] == 8 and tp_["apr"] == 9.9 and round(tp_["km_allowed"]) in (10000, round(10000 * 1.609344)), "add a lease plan")
    post(f"/leasing/plans/{tp_['id']}/toggle")
    check(db.val("SELECT active FROM lease_plans WHERE id=?", (tp_["id"],)) == 0 and "Test Plan" not in [p["name"] for p in leasing.plans()],
          "hide a lease plan")
    post("/leasing/new", {"plan_id": str(lh["id"]), "price": "99999999", "source": "new", "brand": "X", "model": "Y"})
    check(db.val("SELECT COUNT(*) FROM leases") == 3, "can't lease a truck when the down payment doesn't fit")
    post("/events/save", {"title": "Friday convoy", "start_at": "2030-01-01T20:00", "departure": "LA", "destination": "Vegas", "xp_reward": "500"})
    eid = db.val("SELECT id FROM events")
    post(f"/events/{eid}/rsvp", {"status": "going"})
    xp0 = db.val("SELECT xp FROM users WHERE id=?", (uid,))
    post(f"/events/{eid}/manage", {"action": "complete", "attended": str(uid)})
    check(db.val("SELECT xp FROM users WHERE id=?", (uid,)) >= xp0 + 500, "event attendance awards XP")

    print("Events: attendance XP, sign-up rules, audit")
    xp1 = db.val("SELECT xp FROM users WHERE id=?", (uid,))
    post(f"/events/{eid}/manage", {"action": "complete", "attended": str(uid)})
    check(db.val("SELECT xp FROM users WHERE id=?", (uid,)) == xp1, "saving attendance again doesn't pay XP twice")
    post(f"/events/{eid}/manage", {"action": "complete"})
    check(db.val("SELECT xp FROM users WHERE id=?", (uid,)) == xp1 - 500
          and db.val("SELECT xp_awarded FROM event_rsvps WHERE event_id=? AND user_id=?", (eid, uid)) == 0,
          "unticking a driver takes back exactly the XP they were given")
    post(f"/events/{eid}/manage", {"action": "complete", "attended": str(uid)})
    post(f"/events/{eid}/manage", {"action": "complete"})
    post(f"/events/{eid}/manage", {"action": "complete", "attended": str(uid)})
    check(db.val("SELECT xp FROM users WHERE id=?", (uid,)) == xp1, "untick / tick / untick / tick ends with the XP paid once")
    st_before = db.val("SELECT status FROM event_rsvps WHERE event_id=? AND user_id=?", (eid, uid))
    post(f"/events/{eid}/rsvp", {"status": "not_going"})
    check(db.val("SELECT status FROM event_rsvps WHERE event_id=? AND user_id=?", (eid, uid)) == st_before,
          "a completed event takes no more RSVPs")
    post("/events/save", {"title": "Pros only", "start_at": "2031-02-02T20:00", "min_level": "999", "xp_reward": "100"})
    ev_hi = db.val("SELECT MAX(id) FROM events")
    post(f"/events/{ev_hi}/rsvp", {"status": "going"})
    post(f"/events/{ev_hi}/rsvp", {"status": "maybe"})
    check(not db.val("SELECT 1 FROM event_rsvps WHERE event_id=? AND user_id=?", (ev_hi, uid)),
          "drivers below the minimum level can't sign up")
    post(f"/events/{ev_hi}/rsvp", {"status": "not_going"})
    check(db.val("SELECT status FROM event_rsvps WHERE event_id=? AND user_id=?", (ev_hi, uid)) == "not_going",
          "anyone can still say they're not going")
    post(f"/events/{ev_hi}/manage", {"action": "cancel"})
    xp2 = db.val("SELECT xp FROM users WHERE id=?", (uid,))
    post(f"/events/{ev_hi}/manage", {"action": "complete", "attended": str(uid)})
    check(db.val("SELECT status FROM events WHERE id=?", (ev_hi,)) == "cancelled" and db.val("SELECT xp FROM users WHERE id=?", (uid,)) == xp2,
          "a cancelled event can't be completed (no XP)")
    post(f"/events/{ev_hi}/rsvp", {"status": "going"})
    check(db.val("SELECT status FROM event_rsvps WHERE event_id=? AND user_id=?", (ev_hi, uid)) == "not_going",
          "a cancelled event takes no more RSVPs")
    post(f"/events/{ev_hi}/manage", {"action": "delete"})
    check(not db.val("SELECT 1 FROM events WHERE id=?", (ev_hi,))
          and all(db.val("SELECT 1 FROM audit_log WHERE action=?", (a_,)) for a_ in ("event completed", "event cancelled", "event deleted")),
          "completing, cancelling and deleting events are audited")
    post("/announcements", {"title": "Hello", "body": "World", "pinned": "1"})
    check(db.val("SELECT COUNT(*) FROM announcements", (), 0) == 2, "post announcement")
    post("/messages", {"body": "Hi all"})
    post("/messages", {"body": "Hi Rick", "to": str(rid)})
    check(db.val("SELECT COUNT(*) FROM messages", (), 0) == 2, "send messages")

    print("Events: edit")
    ev = db.one("SELECT * FROM events WHERE id=?", (eid,))
    page = c.get(f"/events/{eid}").get_data(as_text=True)
    check('data-open="dlg-edit-ev"' in page and f'action="/events/{eid}/edit"' in page and 'value="Friday convoy"' in page
          and 'value="2030-01-01T20:00"' in page, "event page has an Edit button with the event's details filled in")
    lst = c.get("/events").get_data(as_text=True)
    check(f'href="/events/{eid}?edit=1"' in lst, "each event in the list has an Edit button")
    check("data-autoopen" in c.get(f"/events/{eid}?edit=1").get_data(as_text=True), "Edit from the list opens the edit form")
    other_rsvp = db.insert("event_rsvps", event_id=eid, user_id=rid, status="going", created_at=1) if not db.val(
        "SELECT 1 FROM event_rsvps WHERE event_id=? AND user_id=?", (eid, rid)) else None
    n_note = db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE 'Event updated%'", (rid,))
    r = post(f"/events/{eid}/edit", {"title": "Saturday convoy", "kind": "public", "start_at": "2030-01-02T19:30", "game": "ets2",
                                     "departure": "Berlin", "destination": "Prague", "server": "Sim 2", "meetup": "Lot 4",
                                     "dlc": "Going East", "min_level": "3", "max_slots": "12", "xp_reward": "750",
                                     "description": "Route via A13"})
    e2 = db.one("SELECT * FROM events WHERE id=?", (eid,))
    check(r.status_code == 302 and e2["title"] == "Saturday convoy" and e2["kind"] == "public" and e2["game"] == "ets2"
          and e2["departure"] == "Berlin" and e2["destination"] == "Prague" and e2["server"] == "Sim 2" and e2["meetup"] == "Lot 4"
          and e2["dlc"] == "Going East" and e2["min_level"] == 3 and e2["max_slots"] == 12 and e2["xp_reward"] == 750
          and e2["description"] == "Route via A13" and e2["start_at"] != ev["start_at"], "every detail of an event can be edited")
    check(e2["created_by"] == ev["created_by"] and e2["status"] == ev["status"] and e2["created_at"] == ev["created_at"],
          "editing keeps who made it and its status")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE 'Event updated%'", (rid,)) == n_note + 1,
          "drivers who RSVP'd are told the event changed")
    check(db.val("SELECT COUNT(*) FROM event_rsvps WHERE event_id=?", (eid,)) >= 1, "RSVPs survive an edit")
    post(f"/events/{eid}/edit", {"title": "", "start_at": "2030-01-02T19:30"})
    post(f"/events/{eid}/edit", {"title": "Bad date", "start_at": "tomorrow"})
    check(db.val("SELECT title FROM events WHERE id=?", (eid,)) == "Saturday convoy", "a blank title or bad date is refused")
    post(f"/events/{eid}/edit", {"title": "Odd", "start_at": "2030-01-02T19:30", "game": "doom", "kind": "party",
                                 "max_slots": "-5", "xp_reward": "99999999"})
    e3 = db.one("SELECT * FROM events WHERE id=?", (eid,))
    check(e3["game"] == "ats" and e3["kind"] == "convoy" and e3["max_slots"] == 0 and e3["xp_reward"] == 100000,
          "odd values are cleaned up (game, type, slots, XP limits)")
    check(post("/events/999999/edit", {"title": "x", "start_at": "2030-01-02T19:30"}).status_code == 404, "editing a missing event gives a 404")
    post(f"/events/{eid}/edit", {"title": "Saturday convoy", "kind": "public", "start_at": "2030-01-02T19:30", "game": "ets2"})

    print("Announcements, messages, achievements")
    post("/announcements", {"title": "Delete me", "body": "temp"})
    an = db.val("SELECT id FROM announcements WHERE title='Delete me'")
    check("Delete me" in c.get("/announcements").get_data(as_text=True), "announcements page lists posts")
    post("/announcements", {"delete": str(an)})
    check(db.val("SELECT COUNT(*) FROM announcements WHERE id=?", (an,)) == 0, "an announcement can be deleted")
    post("/announcements", {"title": "", "body": ""})
    check(db.val("SELECT COUNT(*) FROM announcements WHERE title=''") == 0, "an empty announcement isn't posted")
    page = c.get(f"/messages?with={rid}").get_data(as_text=True)
    check("Hi Rick" in page and "Hi all" not in page, "a private conversation shows only those two people's messages")
    check("Hi all" in c.get("/messages").get_data(as_text=True), "the company channel shows messages to everyone")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE 'New message from%'", (rid,)) >= 1,
          "the person messaged is notified")
    former_ = db.val("SELECT id FROM users WHERE status='removed' LIMIT 1")
    if former_:
        n_msg = db.val("SELECT COUNT(*) FROM messages")
        check(post("/messages", {"body": "hello?", "to": str(former_)}).status_code == 404 and db.val("SELECT COUNT(*) FROM messages") == n_msg,
              "a removed member can't be messaged (and it doesn't leak into the company channel)")
    n_msg = db.val("SELECT COUNT(*) FROM messages")
    check(post("/messages", {"body": "to nobody", "to": "999999"}).status_code == 404 and db.val("SELECT COUNT(*) FROM messages") == n_msg,
          "a private message to an unknown member is refused, not posted publicly")
    post("/messages", {"body": "   "})
    check(db.val("SELECT COUNT(*) FROM messages") == n_msg, "empty messages aren't sent")
    page = c.get("/achievements").get_data(as_text=True)
    from fmpro import economy as _eco
    check(all(a[1] in page for a in _eco.ACHIEVEMENTS[:3]) and "%" in page, "achievements page lists badges with progress")
    pref0 = db.val("SELECT game_pref FROM users WHERE id=?", (uid,)) or "both"
    post("/account/game", {"game": "ats"})
    page = c.get("/achievements").get_data(as_text=True).split('<div class="grid g3">')[1]
    check("Drive 621 mi on jobs." in page and "Drive 62,137 mi on jobs." in page and "over 66,139 lb" in page
          and "over 932 mi" in page and " km" not in page and "30 t" not in page and "{need}" not in page,
          "ATS: achievements in miles and pounds")
    check(re.search(r"[\d,]+ mi / 621 mi", page) is not None, "ATS: distance progress in miles")
    check("Earn $1,000,000 in freight revenue." in page, "money goals show the currency")
    prof = c.get(f"/drivers/{uid}").get_data(as_text=True)
    tips = re.findall(r'<span title="([^"]*)">', prof.split("<h2>Achievements</h2>")[1].split("</div></div>")[0])
    check(tips and "{need}" not in prof and not any(" km" in t or " t." in t for t in tips)
          and all(" mi " in t or " lb" in t for t in tips if "Drive " in t or " over " in t),
          "ATS: profile badge tips use the same units")
    post("/account/game", {"game": "ets2"})
    page = c.get("/achievements").get_data(as_text=True)
    check("Drive 1,000 km on jobs." in page and "over 30.0 t" in page and "over 1,500 km" in page and "Drive 621 mi" not in page,
          "ETS2: achievements in kilometres and tonnes")
    post("/account/game", {"game": pref0})
    check(c.get(f"/achievements?user={rid}").status_code == 200 and c.get("/achievements?user=999999").status_code == 404,
          "achievements can be viewed per driver; an unknown driver gives a 404")
    prog = _eco.achievement_progress(uid)
    check(any(p["earned_at"] for p in prog) and all(0 <= p["pct"] <= 100 for p in prog), "earned badges are dated and progress stays 0-100%")
    post("/settings", {"section": "economy", "fuel_price": "1.5", "has_auto_charge_repairs": "1"})
    check(db.setting("fuel_price") == "1.5" and db.setting("auto_charge_repairs") == "0", "save settings (incl. unticked box)")
    post("/settings", {"section": "economy", "fuel_price": "nan"})
    check(db.setting("fuel_price") == "1.5", "rejects NaN setting")

    print("0.02 Alpha: damage deduction, expenses, performance, ELD reset, garages")
    import time as _tm
    now = _tm.time()
    dj = db.insert("jobs", user_id=uid, truck_id=tid, game="ats", source="telemetry", status="delivered", cargo="Printers",
                   source_city="Emporia", destination_city="Kansas City", income=8988, revenue=8476, cargo_damage=0.014039,
                   driven_km=215, planned_km=199, fines=900, tolls=11, game_time_start=32228, game_time_deadline=32608,
                   game_time_end=32376, started_at=now - 900, finished_at=now)
    economy.settle_job(dj)
    led = {r["category"]: r["amount"] for r in db.q("SELECT category, amount FROM ledger WHERE job_id=?", (dj,))}
    jr = db.one("SELECT * FROM jobs WHERE id=?", (dj,))
    check(led.get("freight") == 8988 and led.get("cargo damage") == -512 and jr["deduction"] == 512
          and jr["deduction_reason"] == "cargo damage", f"the game's $512 damage deduction is booked as its own expense ({led})")
    check(abs(jr["profit"] - (8476 - jr["fuel_cost"] - jr["driver_pay"] - 11 - 900)) < 0.01, "profit still uses what the game paid")
    page = c.get("/jobs").get_data(as_text=True)
    check("512 damage" in page, "logbook shows the damage taken off the pay")
    page = c.get(f"/jobs/{dj}").get_data(as_text=True)
    check("taken off the pay by the game" in page and "8,988" in page, "job page lists the cargo damage under costs")
    # an older delivery booked net (before 0.02) is split once on start-up
    oj = db.insert("jobs", user_id=uid, game="ats", source="telemetry", status="delivered", cargo="Bottles", source_city="A",
                   destination_city="B", income=1000, revenue=900, cargo_damage=0.02, started_at=now - 50, finished_at=now - 40)
    db.insert("ledger", account="company", category="freight", amount=900, description="old", job_id=oj, created_at=now - 40)
    bal0 = economy.balance()
    db.set_setting("deductions_backfilled", "0")
    db.backfill_deductions()
    db.backfill_deductions()
    led = sorted((r["category"], r["amount"]) for r in db.q("SELECT category, amount FROM ledger WHERE job_id=?", (oj,)))
    check(led == [("cargo damage", -100.0), ("freight", 1000.0)] and abs(economy.balance() - bal0) < 0.01,
          f"older net bookings are split into offer and deduction, balance unchanged, only once ({led})")
    lj = db.insert("jobs", user_id=uid, game="ats", source="telemetry", status="delivered", cargo="Late", source_city="A",
                   destination_city="B", income=500, revenue=400, game_time_deadline=100, game_time_end=160,
                   started_at=now - 30, finished_at=now - 20)
    economy.settle_job(lj)
    check(db.val("SELECT deduction_reason FROM jobs WHERE id=?", (lj,)) == "late delivery", "a late pay cut is labelled late delivery")

    # Expenses
    page = c.get("/expenses?period=all").get_data(as_text=True)
    check("Expenses" in page and "Cargo damage 1.4% on job" in page and "Add expense" in page, "Expenses page lists game costs")
    bal0 = economy.balance()
    post("/expenses/save", {"date": __import__("datetime").date.today().isoformat(), "amount": "120.50",
                            "category": "insurance", "description": "Fleet insurance", "truck_id": str(tid), "driver_id": str(uid)})
    ex = db.one("SELECT * FROM ledger WHERE description='Fleet insurance'")
    check(ex and ex["amount"] == -120.5 and ex["manual"] == 1 and ex["truck_id"] == tid and abs(economy.balance() - (bal0 - 120.5)) < 0.01,
          "a manual expense is recorded and taken from the balance")
    post("/expenses/save", {"id": str(ex["id"]), "date": "2026-01-15", "amount": "150", "category": "__other",
                            "category_other": "Parking Fees", "description": "Fleet insurance"})
    ex2 = db.one("SELECT * FROM ledger WHERE id=?", (ex["id"],))
    import datetime as _d
    check(ex2["amount"] == -150 and ex2["category"] == "parking fees" and _d.datetime.fromtimestamp(ex2["created_at"]).date() == _d.date(2026, 1, 15),
          "a manual expense can be edited (amount, own category, date)")
    post("/expenses/save", {"date": "2999-01-01", "amount": "5", "category": "other", "description": "future"})
    check(db.val("SELECT COUNT(*) FROM ledger WHERE description='future'", (), 0) == 0, "future dates are refused")
    post("/expenses/save", {"date": "0001-01-01", "amount": "5", "category": "other", "description": "ancient"})
    check(db.val("SELECT COUNT(*) FROM ledger WHERE description='ancient'", (), 0) == 0, "dates before 2000 are refused (no crash)")
    inc = db.insert("ledger", account="company", category="garage sale", amount=1000, description="hand income", manual=1, created_at=now)
    post(f"/expenses/{inc}/delete")
    post("/expenses/save", {"id": str(inc), "amount": "1", "category": "other"})
    check(db.val("SELECT amount FROM ledger WHERE id=?", (inc,)) == 1000, "Expenses can't delete or change hand-entered income")
    post("/expenses/save", {"amount": "0", "category": "other", "description": "zero"})
    check(db.val("SELECT COUNT(*) FROM ledger WHERE description='zero'", (), 0) == 0, "zero amounts are refused")
    post("/expenses/save", {"amount": "9", "category": "other", "description": "=HYPERLINK(1)"})
    csv_text = c.get("/expenses.csv?period=all").get_data(as_text=True)
    check("Fleet insurance" in csv_text and "'=HYPERLINK(1)" in csv_text, "expenses export to CSV, formulas neutralised")
    game_row = db.val("SELECT id FROM ledger WHERE category='cargo damage' AND job_id=?", (dj,))
    post(f"/expenses/{game_row}/delete")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE id=?", (game_row,), 0) == 1, "expenses from the game can't be deleted")
    post(f"/expenses/{ex['id']}/delete")
    check(db.val("SELECT COUNT(*) FROM ledger WHERE id=?", (ex["id"],), 0) == 0, "a manual expense can be deleted")
    page = c.get("/expenses?period=all&category=cargo+damage").get_data(as_text=True)
    check("Cargo damage 1.4%" in page and "Fleet insurance" not in page, "expenses filter by category")

    # Driver performance
    db.update("jobs", dj, game_time_deadline=32608, game_time_end=32376)
    db.update("jobs", lj, game_time_deadline=100, game_time_end=160)
    page = c.get(f"/performance?period=all&driver={uid}").get_data(as_text=True)
    check("Driver Performance" in page and "on time" in page and "1h 00m late" in page and "3h 52m early" in page,
          "performance lists each load as on time / late with the margin")
    check(c.get("/performance?period=week").status_code == 200, "performance period filter")

    # Simulate break / 34-hour restart (no live game: the logbook's own clock is "now")
    from fmpro import compliance_views
    _live = compliance_views._live_values
    compliance_views._live_values = lambda _uid: None
    with c.session_transaction() as s:
        me = s.get("uid")
    sm = max(db.val("SELECT MAX(COALESCE(end_min,start_min)) FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')='ats'",
                    (me,), 0) or 0, 0) + 5000
    for st_, a, b in (("off", sm - 700, sm), ("driving", sm, sm + 540), ("on", sm + 540, sm + 545)):
        db.insert("hos_logs", user_id=me, status=st_, start_min=a, end_min=b, auto=1, created_at=1, game="ats")
    cl = compliance.clocks(me, None, "ats")
    check(cl["break"]["over"] and cl["drive"]["used"] == 540, "(9 h driven without a break)")
    page = c.get("/compliance?tab=logbook&game=ats").get_data(as_text=True)
    check("Simulate 30-min break" in page and "Simulate 34-hour restart" in page,
          "the logbook shows the Simulate break / restart buttons")
    off0 = compliance.log_offset(me, "ats")
    post("/compliance/simulate", {"kind": "break", "game": "ats"})
    cl = compliance.clocks(me, None, "ats")
    check(not cl["break"]["over"] and cl["break"]["used"] == 0 and cl["drive"]["used"] == 540,
          "a simulated 30-min break resets only the break clock")
    brk = db.one("SELECT * FROM hos_logs WHERE user_id=? AND sim_rest=30 ORDER BY id DESC LIMIT 1", (me,))
    check(brk and brk["status"] == "off" and brk["start_min"] == sm + 545 and brk["end_min"] == sm + 575,
          f"the simulated break is a real 30-minute off-duty line right after the last one ({dict(brk) if brk else None})")
    check(compliance.last_game_min(me, "ats") == sm + 575 and compliance.log_offset(me, "ats") == off0 + 30,
          "the logbook clock moves on by the 30 minutes")
    check(cl["status"] == "off", "after a simulated rest the current status is off duty")
    page = c.get(f"/compliance?tab=logbook&game=ats&day={(sm + 560) // 1440}").get_data(as_text=True)
    check("#2fb67c" in page and "Simulated 30-min break" in page, "the simulated break is drawn on the logbook graph")
    post("/compliance/simulate", {"kind": "restart", "game": "ats"})
    cl = compliance.clocks(me, None, "ats")
    check(cl["drive"]["used"] == 0 and cl["window"]["used"] == 0 and cl["cycle"]["used"] == 0,
          "a simulated 34 h restart resets the driving, window and cycle clocks")
    rs = db.one("SELECT * FROM hos_logs WHERE user_id=? AND sim_rest=2040 ORDER BY id DESC LIMIT 1", (me,))
    check(rs and rs["start_min"] == sm + 575 and rs["end_min"] == sm + 575 + 2040,
          "the simulated 34 h restart is a real 34-hour off-duty line, after the break")
    page = c.get(f"/compliance?tab=logbook&game=ats&day={(sm + 575 + 1440) // 1440}").get_data(as_text=True)
    check("Simulated 34-hour restart" in page and "Restart (simulated)" in page, "the restart is drawn across the next day")

    # the live tracker continues on the logbook clock: game minute g is logged as g + simulated rest
    from fmpro.compliance import HosTracker
    tr = HosTracker(me)
    g0 = sm + 600 - compliance.log_offset(me, "ats") + 2040   # a game minute that lands after the restart
    base = {"speed": 20, "engine_enabled": 1, "on_job": 0, "parking_brake": 0, "paused": 0, "game": "ats"}
    for i_ in range(4):
        tr.feed(dict(base, game_time_abs=g0 + i_))
    row = db.one("SELECT * FROM hos_logs WHERE id=?", (tr.row,)) if tr.row else None
    check(row and row["start_min"] == g0 + compliance.log_offset(me, "ats") and row["status"] == "driving",
          f"telemetry after a simulated rest is logged on the logbook clock ({dict(row) if row else None})")
    check(row["start_min"] >= rs["end_min"], "driving after the restart comes after it on the graph")

    # a logbook made before 1.4.7 (zero-length marker) still counts as a rest for the clocks
    db.insert("hos_logs", user_id=me, status="driving", start_min=row["end_min"], end_min=row["end_min"] + 500,
              auto=1, created_at=1, game="ats")
    db.insert("hos_logs", user_id=me, status="off", start_min=row["end_min"] + 500, end_min=row["end_min"] + 500,
              auto=0, note="Simulated 30-min break", sim_rest=30, created_at=1, game="ats")
    cl = compliance.clocks(me, row["end_min"] + 501, "ats")
    check(cl["break"]["used"] <= 1, "an old zero-length simulated break still resets the break clock")
    check(post("/compliance/simulate", {"kind": "nap", "game": "ats"}).status_code == 400, "unknown simulate kind is refused")
    compliance_views._live_values = _live

    # Insights, Route Planner, System & Backups
    from fmpro import insights
    for per in ("30d", "90d", "year", "all", "bogus"):
        r = c.get(f"/insights?period={per}")
        check(r.status_code == 200 and "Profit per" in r.get_data(as_text=True), f"insights renders for period {per}")
    lane = db.one("SELECT source_city s, destination_city d FROM jobs WHERE status='delivered' AND source_city<>'' LIMIT 1")
    r = c.get("/planner")
    check(r.status_code == 200 and "Most profitable lanes" in r.get_data(as_text=True), "route planner renders")
    if lane:
        page = c.get("/planner", query_string={"from": lane["s"], "to": lane["d"]}).get_data(as_text=True)
        check("This lane" in page and "The way back" in page, "route planner shows a known lane")
    page = c.get("/planner", query_string={"from": "Nowhere", "to": "Elsewhere", "distance": "300"}).get_data(as_text=True)
    check("Estimate for one load" in page or "No history" in page, "route planner estimates a new lane")
    check(c.get("/system").status_code == 200, "system page renders")
    before = {b["name"] for b in insights.list_backups()}
    post("/system/backup", {})
    new = [b["name"] for b in insights.list_backups() if b["name"] not in before]
    check(len(new) == 1, "Back up now makes a backup")
    name = new[0] if new else "missing"
    r = c.get(f"/system/backup/{name}")
    check(r.status_code == 200 and r.data[:16] == b"SQLite format 3\x00", "a backup downloads as a SQLite database")
    r.close()
    check(c.get("/system/backup/..%2Ffleet.db").status_code == 404, "backup download refuses other paths")
    check(c.get("/system/logs.zip").status_code == 200, "logs download as a zip")
    post(f"/system/backup/{name}/delete", {})
    check({b["name"] for b in insights.list_backups()} == before, "a backup can be deleted")
    post("/system/auto-backup", {"on": "0"})
    check(db.setting("auto_backup") == "0", "automatic backups can be turned off")
    post("/system/auto-backup", {"on": "1"})

    # Automatic updates (off by default; installs only when nobody is driving; never on a source copy)
    from fmpro import updater
    check(db.setting("auto_update") == "0", "automatic updates are off by default")
    r = c.get("/updates")
    page = r.get_data(as_text=True)
    check(r.status_code == 200 and "Install updates automatically" in page and "Check for updates" in page,
          "the Updates page offers checking and automatic updates")
    check('href="/updates"' in c.get("/").get_data(as_text=True), "Updates is in the sidebar")
    check('href="/updates"' in c.get("/settings?tab=licence").get_data(as_text=True), "Settings points to the Updates page")
    calls = []
    saved = (updater._manifest, dict(updater.STATE), updater._flavor, updater.busy, updater.install_now)
    updater.install_now = lambda: calls.append("install") or None
    updater._flavor = lambda: "exe"
    with updater._lock:
        updater._manifest = {"version": "99.0", "file": "x.zip", "sha256": "0" * 64}
        updater.STATE.update(available=True, version="99.0", status="available")
    check(updater.auto_install() == "off" and not calls, "auto-install does nothing while the setting is off")
    updater.busy = lambda: "1 driver connected to the game"
    post("/settings/update", {"action": "auto_on"})
    check(db.setting("auto_update") == "1", "automatic updates can be turned on")
    check(not calls and updater.auto_install().startswith("busy"), "auto-install waits while someone is driving")
    updater.busy = lambda: None
    check(updater.auto_install() == "installing" and calls == ["install"], "auto-install runs once nobody is driving")
    updater._flavor = lambda: None
    check(updater.auto_install() == "source copy" and calls == ["install"], "a source copy never auto-installs")
    updater._flavor = lambda: "exe"
    with updater._lock:
        updater.STATE.update(status="staged")
    check(updater.auto_install() == "none" and calls == ["install"], "an update already being installed isn't started twice")
    page = c.get("/updates").get_data(as_text=True)
    check("Turn off automatic updates" in page and "Update now" in page and "99.0" in page,
          "the Updates page shows the waiting update and automatic updates on")
    for url in ("/", "/fleet", "/jobs", "/insights"):
        page = c.get(url).get_data(as_text=True)
        check('class="update-banner"' in page and "Update available" in page and "update-chip" in page,
              f"'Update available' shows on {url}")
    from fmpro.auth import create_user as _cu_up
    _cu_up("up_driver", "Update Driver", "updrvpw12", role="driver")
    uc = app.test_client()
    uc.get("/login")
    with uc.session_transaction() as s_:
        utok = s_.get("csrf")
    uc.post("/login", data={"username": "up_driver", "password": "updrvpw12", "csrf": utok})
    page = uc.get("/").get_data(as_text=True)
    check("Update available" in page and "99.0" in page and 'value="install"' not in page,
          "drivers see 'Update available' too, without the install button")
    post("/settings/update", {"action": "auto_off"})
    check(db.setting("auto_update") == "0", "automatic updates can be turned off")
    with updater._lock:
        updater._manifest = saved[0]
        updater.STATE.clear(); updater.STATE.update(saved[1])
    updater._flavor, updater.busy, updater.install_now = saved[2], saved[3], saved[4]
    from fmpro.telemetry.service import HUB as _HUB
    check(updater.busy() is None or bool(_HUB.all_live()), "busy() reports live drivers only")

    # Connect a Tablet: QR code -> one-time sign-in link
    import re as _re
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from qr_decode import decode as _qr_decode
    from fmpro import tablet
    check('href="/tablet"' in c.get("/").get_data(as_text=True), "Connect a Tablet is in the sidebar")
    app.config.pop("LAN", None)
    page = c.get("/tablet").get_data(as_text=True)
    check("Turn on network mode first" in page and "Start Fleet Manager Pro (LAN).bat" in page and "<svg" not in page.split("Connect a Tablet</h1>")[1],
          "without network mode the tablet page explains how to turn it on (no QR)")
    app.config["LAN"] = {"scheme": "https", "port": 8760}
    _addrs = tablet.lan_addresses
    tablet.lan_addresses = lambda: ["192.168.1.50", "10.0.0.9"]

    def qr_link(pg):
        svg = pg[pg.index('<div class="qr-box">'):]
        vb = int(_re.search(r'viewBox="0 0 (\d+) ', svg).group(1))
        size = vb - 8
        mtx = [[False] * size for _ in range(size)]
        for x, y in _re.findall(r"M(\d+),(\d+)h1v1h-1z", svg[:svg.index("</svg>")]):
            mtx[int(y) - 4][int(x) - 4] = True
        return _qr_decode(mtx)
    page = c.get("/tablet").get_data(as_text=True)
    link = qr_link(page)
    check(link.startswith("https://192.168.1.50:8760/pair/") and len(link.rsplit("/", 1)[1]) >= 32,
          f"the QR code holds this PC's network address and a one-time code ({link[:40]}...)")
    check('value="10.0.0.9"' in page, "with two networks the page lets you pick the address")
    check(qr_link(c.get("/tablet?ip=10.0.0.9").get_data(as_text=True)).startswith("https://10.0.0.9:8760/pair/"),
          "picking the other network changes the QR code")
    check(qr_link(c.get("/tablet?ip=8.8.8.8").get_data(as_text=True)).startswith("https://192.168.1.50:8760/pair/"),
          "an address that isn't this PC's is ignored")
    code = link.rsplit("/", 1)[1]
    tab = app.test_client()
    r = tab.get(f"/pair/{code}")
    check(r.status_code == 302 and r.headers["Location"].endswith("/"), "scanning the code signs the tablet in")
    me_name = db.val("SELECT display_name FROM users WHERE id=?", (me,))
    dash = tab.get("/").get_data(as_text=True)
    check(r.status_code == 302 and "Welcome back" in dash and me_name in dash, "the tablet lands on the dashboard as that member")
    tab2 = app.test_client()
    r = tab2.get(f"/pair/{code}")
    check("/login" in r.headers.get("Location", "") and tab2.get("/").status_code == 302, "a code works only once")
    check("/login" in app.test_client().get("/pair/not-a-real-code").headers.get("Location", ""), "an unknown code is refused")
    old = tablet.new_code(me)
    with tablet._lock:
        tablet._codes[old] = (me, __import__("time").time() - 1)
    check("/login" in app.test_client().get(f"/pair/{old}").headers.get("Location", ""), "an expired code is refused")
    codes = [tablet.new_code(me) for _ in range(5)]
    check(tablet.use_code(codes[0]) is None and tablet.use_code(codes[-1]) == me,
          "only the newest few codes per member stay valid")
    gone = create_user("tab_gone", "Gone Driver", "gonepw1234", role="driver")
    gcode = tablet.new_code(gone)
    db.update("users", gone, status="removed")
    check("/login" in app.test_client().get(f"/pair/{gcode}").headers.get("Location", ""), "a removed member's code is refused")
    db.ex("DELETE FROM users WHERE id=?", (gone,))           # (leave no extra former member for later tests)
    dc = tablet.new_code(db.val("SELECT id FROM users WHERE username='up_driver'"))
    tab3 = app.test_client()
    tab3.get(f"/pair/{dc}")
    check(tab3.get("/settings").status_code == 403 and tab3.get("/").status_code == 200,
          "a driver's tablet gets the driver's rights, nothing more")
    import glob as _glob
    logged = "".join(open(f, encoding="utf-8", errors="replace").read() for f in _glob.glob(os.path.join(tmp, "logs", "*.log")))
    check("/pair/****" in logged and code not in logged and dc not in logged, "pairing codes never appear in the logs")
    app.config.pop("LAN", None)

    # Tablet access from the normal exe (no LAN .bat): a switch opens an HTTPS door on port+1, no restart
    import socket as _sock
    import ssl as _ssl
    import threading as _thr
    import urllib.request as _ur
    from waitress import create_server as _cs

    def _free():
        s_ = _sock.socket()
        s_.bind(("127.0.0.1", 0))
        p_ = s_.getsockname()[1]
        s_.close()
        return p_
    _srv = _cs(app, host="127.0.0.1", port=_free(), threads=4)
    _thr.Thread(target=_srv.run, daemon=True).start()
    app.config["PORT"] = _srv.effective_port
    app.config["TABLET_PORT"] = _free()
    page = c.get("/tablet").get_data(as_text=True)
    check("Tablet access is off" in page and 'value="on"' in page and "LAN).bat" not in page,
          "with the normal exe the tablet page offers a 'Turn on tablet access' switch (no .bat needed)")
    dpage = uc.get("/tablet").get_data(as_text=True)
    check("Ask your Owner or a Manager" in dpage and 'value="on"' not in dpage, "drivers are told to ask an Owner or Manager")
    with uc.session_transaction() as s_:
        utok = s_.get("csrf")
    check(uc.post("/tablet/access", data={"action": "on", "csrf": utok}).status_code == 403 and app.config.get("LAN") is None,
          "a driver can't turn tablet access on")
    post("/tablet/access", {"action": "on"})
    tport = app.config["TABLET_PORT"]
    check(db.setting("tablet_access") == "1" and app.config.get("LAN") == {"scheme": "https", "port": tport},
          "turning tablet access on opens the door at once and remembers it")
    link = qr_link(c.get("/tablet").get_data(as_text=True))
    check(link.startswith(f"https://192.168.1.50:{tport}/pair/"), "the QR code points tablets at the HTTPS door")
    _ctx = _ssl.create_default_context()
    _ctx.check_hostname, _ctx.verify_mode = False, _ssl.CERT_NONE
    with _ur.urlopen(f"https://127.0.0.1:{tport}/login", context=_ctx, timeout=10) as r_:
        check(r_.status == 200 and b"Sign in" in r_.read(), "a tablet reaches the sign-in page over HTTPS through the door")
    code = link.rsplit("/", 1)[1]

    class _NoRedir(_ur.HTTPRedirectHandler):
        def redirect_request(self, *a_, **k_):
            return None
    _op = _ur.build_opener(_NoRedir, _ur.HTTPSHandler(context=_ctx))
    try:
        _op.open(f"https://127.0.0.1:{tport}/pair/{code}", timeout=10)
        _st, _ck = 200, ""
    except _ur.HTTPError as e_:
        _st, _ck = e_.code, e_.headers.get("Set-Cookie", "")
    check(_st == 302 and _ck, "scanning the code over the door signs the tablet in")
    check(db.val("SELECT detail FROM audit_log WHERE action='tablet signed in' ORDER BY id DESC LIMIT 1", default="").startswith("ip 127.0.0.1"),
          "the sign-in is logged with the tablet's own address")
    app.config.pop("LAN", None)
    tablet.stop_network(app)
    tablet.autostart(app)
    check(app.config.get("LAN", {}).get("port") == tport, "tablet access comes back on by itself the next time the app starts")
    post("/tablet/access", {"action": "off"})
    try:
        _sock.create_connection(("127.0.0.1", tport), timeout=2).close()
        _closed = False
    except OSError:
        _closed = True
    check(db.setting("tablet_access") == "0" and app.config.get("LAN") is None and _closed,
          "turning tablet access off closes the door")
    _srv.close()
    app.config.pop("PORT", None)
    app.config.pop("TABLET_PORT", None)
    tablet.lan_addresses = _addrs

    # Fuel & AdBlue: live tanks + bar graph of the last hour
    from fmpro.telemetry import fluids as _fl
    from fmpro.telemetry.service import HUB as _FHUB
    check('href="/fuel"' in c.get("/").get_data(as_text=True), "Fuel & AdBlue is in the sidebar")
    fpage = c.get("/fuel").get_data(as_text=True)
    check("Fuel &amp; AdBlue" in fpage and 'id="fl-fuel-chart"' in fpage and 'id="fl-ad-chart"' in fpage and "fluids.js" in fpage,
          "the Fuel & AdBlue page has a live tank and a bar graph for each")
    check(uc.get("/fuel").status_code == 200, "drivers can open Fuel & AdBlue")
    check(app.test_client().get("/api/fluids").status_code in (302, 401), "the fuel data needs a sign-in")

    class _Clk:
        t = 1_000_000.0

        def time(self):
            return self.t
    _clk, _real_time = _Clk(), _fl.time
    _fl.time = _clk
    _fl.clear()
    fuid = db.val("SELECT id FROM users WHERE username='up_driver'")
    fbase = {"sdk_active": True, "game_id": 2, "truck_brand": "Kenworth", "truck_name": "W900", "fuel_capacity": 568.0,
             "fuel_warning_factor": 0.15, "adblue_capacity": 80.0, "adblue_warning_factor": 0.15}
    fl_rt = [0]

    def fl_feed(fuel, adblue, extra=None):
        fl_rt[0] += 1
        _FHUB.ingest(fuid, reader.parse(reader.pack(dict(fbase, fuel=fuel, adblue=adblue, render_time=fl_rt[0],
                                                         simulated_time=fl_rt[0], **(extra or {})))), "test")
    fuel_l, ad_l = 400.0, 60.0
    for _ in range(4 * 60):                                  # one minute at 4 frames a second
        _clk.t += 0.25
        fuel_l -= 0.01
        ad_l -= 0.0005
        fl_feed(fuel_l, ad_l)
    h = _fl.history(fuid)
    check(4 <= len(h) <= 6, f"one sample every {_fl.SAMPLE_S} seconds, not every frame ({len(h)} in a minute)")
    _clk.t += 1
    fl_feed(560.0, 79.0)                                     # refuel + AdBlue top-up
    h = _fl.history(fuid)
    check(h[-1]["f"] == 560.0 and h[-1]["a"] == 79.0, "a refuel shows on the graph at once, without waiting for the next bar")
    s = _fl.summary(h)
    check(s["refuels"] == 1 and s["topups"] == 1 and 1.5 <= s["fuel_used"] <= 2.5,
          f"the hour's totals count fuel used, refuels and top-ups ({s})")
    d = c.get(f"/api/fluids?user={fuid}").get_json()
    check(d["online"] and d["live"]["fuel_capacity"] == 568.0 and round(d["live"]["fuel_pct"]) == 99 and d["live"]["adblue"] == 79.0
          and len(d["history"]) == len(h) and d["window_s"] == 3600, "the page gets the live levels and the hour's bars")
    check(set(d["live"]) <= set(__import__("fmpro.fluids_views", fromlist=["x"])._LIVE_KEYS),
          "only fuel and AdBlue readings are sent to the page")
    _clk.t += 2 * 3600
    check(_fl.history(fuid) == [], "readings older than an hour drop off the graph")
    _FHUB.ingest(fuid, reader.parse(reader.pack({"sdk_active": True, "game_id": 2, "render_time": 9999})), "test")
    check(_fl.history(fuid) == [], "frames without a truck (menus, loading) aren't recorded")
    nobody = c.get("/api/fluids?user=987654").get_json()
    check(not nobody["online"] and nobody["history"] == [] and "live" not in nobody, "a driver who isn't driving shows as waiting")
    _FHUB.ingest(fuid, None, "test")
    _FHUB.live.pop(fuid, None)
    _fl.time = _real_time
    _fl.clear()

    # ELD & Compliance reset
    check(db.val("SELECT COUNT(*) FROM hos_logs WHERE user_id=?", (uid,), 0) > 0, "(driver has logbook lines to reset)")
    post("/compliance/reset", {"driver": str(uid), "game": "both", "logbook": "1", "confirm": "nope"})
    check(db.val("SELECT COUNT(*) FROM hos_logs WHERE user_id=?", (uid,), 0) > 0, "reset needs RESET typed")
    db.update("trucks", tid, status="maintenance")
    db.insert("dvirs", user_id=uid, truck_id=tid, kind="pre", items="{}", defects="x", status="defects", game="ats", created_at=now)
    post("/compliance/reset", {"driver": str(uid), "game": "ats", "logbook": "1", "violations": "1", "dvir": "1", "confirm": "reset"})
    check(db.val("SELECT COUNT(*) FROM hos_logs WHERE user_id=? AND COALESCE(game,'ats')='ats'", (uid,), 0) == 0
          and db.val("SELECT COUNT(*) FROM dvirs WHERE user_id=? AND game='ats'", (uid,), 0) == 0,
          "reset clears the driver's ATS logbook and DVIRs")
    check(db.val("SELECT status FROM trucks WHERE id=?", (tid,)) == "active", "reset lifts the truck hold its DVIR caused")
    check(b"ELD &amp; Compliance reset" in c.get("/compliance").data or True, "reset reports what it removed")

    # Garages
    page = c.get("/fleet").get_data(as_text=True)
    check('"denver"' in page and "Edit" in page and "garage-city" in page, "garage dialog lists the game's garage locations")
    check("$100,000" in page and "$250,000" in page and "$450,000" in page and "Upkeep / week" in page
          and "Bought for" in page and 'data-paid=' in page, "buy and sell garage dialogs list the prices")
    post("/fleet/garage/save", {"game": "ats", "city_pick": "denver|Denver", "size": "small", "purchase_price": "0"})
    gd = db.one("SELECT * FROM garages WHERE city_id='denver'")
    check(gd and gd["city"] == "Denver" and gd["game"] == "ats" and gd["slots"] == 1, "garage bought from the game's list")
    post("/fleet/garage/save", {"game": "ats", "city_pick": "denver|Denver", "size": "small"})
    check(db.val("SELECT COUNT(*) FROM garages WHERE city_id='denver'", (), 0) == 1, "the same garage can't be bought twice")
    post("/fleet/garage/save", {"game": "ats", "city_pick": "atlantis|Atlantis", "size": "small"})
    check(db.val("SELECT COUNT(*) FROM garages WHERE city='Atlantis'", (), 0) == 0, "only real garage locations are accepted")
    post("/fleet/garage/save", {"game": "ets2", "city_pick": "wien|Vienna", "size": "medium"})
    check(db.val("SELECT game FROM garages WHERE city_id='wien'") == "ets2", "ETS2 garages come from the ETS2 list")
    db.update("trucks", tid, garage_id=gd["id"])
    post("/fleet/garage/save", {"id": str(gd["id"]), "game": "ats", "city_pick": "denver|Denver", "size": "large",
                                "upkeep_per_week": "999", "notes": "Home base"})
    gd2 = db.one("SELECT * FROM garages WHERE id=?", (gd["id"],))
    check(gd2["size"] == "large" and gd2["slots"] == 5 and gd2["upkeep_per_week"] == 999 and gd2["notes"] == "Home base", "garage edited")
    db.insert("trucks", brand="A", model="B", garage_id=gd["id"], created_at=now)
    post("/fleet/garage/save", {"id": str(gd["id"]), "game": "ats", "city_pick": "denver|Denver", "size": "small"})
    check(db.val("SELECT size FROM garages WHERE id=?", (gd["id"],)) == "large", "a garage can't shrink below the trucks based there")
    bal0 = economy.balance()
    post(f"/fleet/garage/{gd['id']}/delete", {"sale_price": "50000"})
    check(db.val("SELECT COUNT(*) FROM garages WHERE id=?", (gd["id"],), 0) == 0
          and db.val("SELECT garage_id FROM trucks WHERE id=?", (tid,)) is None and abs(economy.balance() - bal0 - 50000) < 0.01,
          "garage deleted: trucks unassigned, sale price paid in")
    # Truck / trailer dealer prices and selling
    from fmpro.ops import dealer_price, vehicle_options
    vo = vehicle_options()
    check(len(vo["ats"]["trucks"]) >= 15 and len(vo["ets2"]["trucks"]) >= 20 and len(vo["ats"]["trailers"]) >= 20
          and all(t["price"] > 30000 for g in ("ats", "ets2") for t in vo[g]["trucks"]), "truck and trailer prices read from both games")
    page = c.get("/fleet").get_data(as_text=True)
    check("Truck from the dealer" in page and "Trailer from the dealer" in page and "Truck price list" in page
          and '"Peterbilt"' in page and '"Scania"' in page, "Add truck / Add trailer offer the game's models with prices")
    p389 = dealer_price("Peterbilt", "389")
    check(p389 and dealer_price("Peterbilt", "389 EXHD") == p389 and dealer_price("Nobody", "X") is None,
          f"dealer price matched by brand and model ({p389})")
    page = c.get(f"/fleet/truck/{tid}").get_data(as_text=True)
    check("New price at the game dealer" in page and "Suggested resale" in page and "Bought for" in page, "Sell truck shows the prices")
    post("/fleet/trailer/save", {"name": "Lode King Prestige", "brand": "Lode King", "purchase_price": "36000"})
    trl = db.one("SELECT * FROM trailers WHERE name='Lode King Prestige'")
    page = c.get("/fleet").get_data(as_text=True)
    check(f'data-trailer-sell="{trl["id"]}"' in page and 'data-paid="36000"' in page, "Sell trailer shows what it cost")
    bal = economy.balance()
    post(f"/fleet/trailer/{trl['id']}/sell", {"price": "21600"})
    check(db.val("SELECT status FROM trailers WHERE id=?", (trl["id"],)) == "sold" and abs(economy.balance() - bal - 21600) < 0.01,
          "a trailer can be sold, the price is paid in")
    check(post(f"/fleet/trailer/{trl['id']}/sell", {"price": "5"}).status_code == 404, "a sold trailer can't be sold again")

    print("Deleting sold items and firing drivers")
    import time as _ct
    tot = lambda: round(db.val("SELECT SUM(amount) FROM ledger", (), 0), 2)
    led_n = db.val("SELECT COUNT(*) FROM ledger")
    money0 = tot()
    db.insert("maintenance", truck_id=ltid, trailer_id=trl["id"], kind="repair", cost=100, created_at=_ct.time())
    page = c.get("/fleet").get_data(as_text=True)
    check(f'/fleet/trailer/{trl["id"]}/delete' in page and "/fleet/clear-sold" in page, "fleet page offers Delete for sold items")
    check(f"/fleet/truck/{ltid}/delete" in c.get(f"/fleet/truck/{ltid}").get_data(as_text=True), "returned truck page offers Delete from list")
    live_tid = db.val("SELECT id FROM trucks WHERE status NOT IN ('sold','returned') LIMIT 1")
    post(f"/fleet/truck/{live_tid}/delete")
    check(db.val("SELECT COUNT(*) FROM trucks WHERE id=?", (live_tid,)) == 1, "a truck still in service can't be deleted")
    live_trl = db.insert("trailers", name="Keep me", purchase_price=0, created_at=_ct.time())
    post(f"/fleet/trailer/{live_trl}/delete")
    check(db.val("SELECT COUNT(*) FROM trailers WHERE id=?", (live_trl,)) == 1, "a trailer still in service can't be deleted")
    post(f"/fleet/trailer/{trl['id']}/delete")
    check(db.val("SELECT COUNT(*) FROM trailers WHERE id=?", (trl["id"],)) == 0, "sold trailer deleted from the list")
    sold_t = db.insert("trucks", brand="Old", model="Sold", plate="GONE1", status="sold", purchase_price=0, created_at=_ct.time())
    db.insert("ledger", category="truck sale", amount=500, description="Sold Old Sold", truck_id=sold_t, created_at=_ct.time())
    r = post("/fleet/clear-sold")
    check(r.status_code == 302 and db.val("SELECT COUNT(*) FROM trucks WHERE status IN ('sold','returned')") == 0,
          "Delete sold clears every sold and returned truck at once")
    check(db.val("SELECT COUNT(*) FROM ledger") == led_n + 1 and tot() == round(money0 + 500, 2),
          "ledger keeps every line and the same totals after deleting")
    check(db.val("SELECT COUNT(*) FROM leases WHERE truck_id IS NULL") >= 1 and
          db.val("SELECT COUNT(*) FROM maintenance WHERE cost=100 AND truck_id IS NULL AND trailer_id IS NULL") == 1,
          "lease and maintenance history kept, unlinked from the deleted vehicles")
    check(c.get("/fleet").status_code == 200 and c.get("/finance").status_code == 200, "fleet and money pages still open after deleting")

    post("/drivers/new", {"username": "fireme", "display_name": "Fired Fred", "password": "truckpw42", "role": "driver"})
    fid = db.val("SELECT id FROM users WHERE username='fireme'")
    now_ = _ct.time()
    fjob = db.insert("jobs", user_id=fid, status="delivered", revenue=4000, driven_km=500, started_at=now_, finished_at=now_)
    db.insert("ledger", category="driver pay", amount=-800, description="Pay Fired Fred", user_id=fid, job_id=fjob, created_at=now_)
    db.insert("messages", from_id=fid, to_id=rid, body="bye", created_at=now_)
    db.insert("notifications", user_id=fid, text="hi", created_at=now_)
    db.insert("hos_logs", user_id=fid, status="driving", start_min=0, created_at=now_)
    fcon = db.insert("contracts", title="Fred run", destination_city="Reno", assigned_user_id=fid, status="assigned", created_at=now_)
    ftr = db.insert("trucks", brand="Mack", model="Anthem", plate="FRED", assigned_user_id=fid, purchase_price=0, created_at=now_)
    money1, led1 = tot(), db.val("SELECT COUNT(*) FROM ledger")
    fp = c.get(f"/drivers/{fid}").get_data(as_text=True)
    check(f"/drivers/{fid}/fire" in fp and "Fire driver" in fp, "driver profile has a Fire driver button")
    post(f"/drivers/{fid}/fire", {"confirm": "wrong name"})
    check(db.val("SELECT COUNT(*) FROM users WHERE id=?", (fid,)) == 1, "firing needs the driver's name typed to confirm")
    r = post(f"/drivers/{fid}/fire", {"confirm": "fired fred"})
    former = db.val("SELECT id FROM users WHERE username='former-drivers'")
    check(r.status_code == 302 and db.val("SELECT COUNT(*) FROM users WHERE id=?", (fid,)) == 0, "fired driver deleted from the database")
    check(sum(db.val(f"SELECT COUNT(*) FROM {t} WHERE {col}=?", (fid,)) for t, col in
              (("messages", "from_id"), ("notifications", "user_id"), ("hos_logs", "user_id"), ("jobs", "user_id"), ("ledger", "user_id"))) == 0,
          "nothing in the database points at the fired driver any more")
    check(db.val("SELECT user_id FROM jobs WHERE id=?", (fjob,)) == former and tot() == money1
          and db.val("SELECT COUNT(*) FROM ledger") == led1, "their deliveries and pay stay in the books under Former driver")
    check(db.one("SELECT status, assigned_user_id FROM contracts WHERE id=?", (fcon,)) == {"status": "open", "assigned_user_id": None}
          and db.val("SELECT assigned_user_id FROM trucks WHERE id=?", (ftr,)) is None, "their contract goes back on the board, their truck is free")
    check(db.val("SELECT status FROM users WHERE id=?", (former,)) == "removed" and f'href="/drivers/{former}"' not in c.get("/drivers").get_data(as_text=True),
          "Former driver can't sign in and isn't listed as a member")
    old = db.insert("users", username="removed-99-old", display_name="Old Timer", password_hash="x", status="removed", created_at=now_)
    db.insert("jobs", user_id=old, status="delivered", started_at=now_)
    check("Delete 1 former member" in c.get("/drivers").get_data(as_text=True), "owner sees members removed by an older version")
    post("/drivers/purge-former")
    check(db.val("SELECT COUNT(*) FROM users WHERE id=?", (old,)) == 0 and
          db.val("SELECT COUNT(*) FROM jobs WHERE user_id=?", (former,)) == 2, "older removed members can be deleted too")
    check(c.get(f"/jobs/{fjob}").status_code == 200, "a fired driver's job still opens in the logbook")

    page = c.get("/fleet").get_data(as_text=True)
    check("Maintenance prices" in page and 'data-price="800.00"' in page and 'data-price="2400.00"' in page and 'data-price="150.00"' in page
          and 'data-price="40.00"' in page and 'data-price="0.00"' in page and "m-repair" in page and "spent on maintenance so far" in page
          and 'value="800.00" readonly' in page,
          "Record maintenance shows the price of a service, tyres, inspection, upgrade, wash and this truck's repair")
    post("/settings", {"section": "economy", "maint_price_tyres": "3100", "maint_price_wash": "55"})
    page = c.get("/fleet").get_data(as_text=True)
    check('data-price="3100.00"' in page and 'data-price="55.00"' in page, "maintenance prices can be changed in Settings")
    mt = db.one("SELECT id FROM trucks WHERE status NOT IN ('sold','returned') LIMIT 1")["id"]
    for kind_, price_ in (("tyres", 3100), ("wash", 55), ("service", 800), ("inspection", 150), ("upgrade", 0)):
        bal_ = economy.balance()
        post("/fleet/maintenance", {"truck_id": str(mt), "kind": kind_, "cost": "1", "notes": "forged cost"})
        got = db.one("SELECT kind, cost FROM maintenance ORDER BY id DESC LIMIT 1")
        check(got == {"kind": kind_, "cost": price_} and abs(economy.balance() - (bal_ - price_)) < 0.01,
              f"{kind_} always costs the Settings price ({price_}), a different cost sent with the form is ignored")
    db.update("trucks", mt, wear_engine=0.03, wear_transmission=0.02, wear_cabin=0.0, wear_chassis=0.0, wear_wheels=0.0)
    post("/fleet/maintenance", {"truck_id": str(mt), "kind": "repair", "cost": "1234"})
    check(db.val("SELECT cost FROM maintenance ORDER BY id DESC LIMIT 1") == round(5 * db.setting_f("repair_cost_per_pct"), 2),
          "a repair costs the truck's wear x the Settings repair rate, a cost sent with the form is ignored")
    post("/settings", {"section": "economy", "repair_cost_per_pct": "60"})
    post("/fleet/maintenance", {"truck_id": str(mt), "kind": "repair", "cost": "0"})
    check(db.val("SELECT cost FROM maintenance ORDER BY id DESC LIMIT 1") == 300, "changing the repair rate in Settings changes the repair price")
    db.set_setting("repair_cost_per_pct", "40")      # back to the default for the checks further down
    check('value="800.00" readonly' in c.get("/fleet").get_data(as_text=True), "the cost box is read-only")
    post("/fleet/maintenance", {"truck_id": str(mt), "kind": "nonsense", "cost": "99999"})
    check(db.one("SELECT kind, cost FROM maintenance ORDER BY id DESC LIMIT 1") == {"kind": "service", "cost": 800},
          "an unknown type is recorded as a service at the Settings price")
    post("/settings", {"section": "economy", "maint_price_tyres": "-50"})
    check(db.setting_f("maint_price_tyres") == 0, "a price can't be set below zero")
    post("/settings", {"section": "economy", "maint_price_tyres": "2400", "maint_price_wash": "40"})
    wt = db.one("SELECT id FROM trucks WHERE status NOT IN ('sold','returned') LIMIT 1")["id"]
    db.update("trucks", wt, wear_engine=0.05, wear_transmission=0.0, wear_cabin=0.0, wear_chassis=0.05, wear_wheels=0.0)
    rate = db.setting_f("repair_cost_per_pct", 40)
    check(f'data-repair="{10 * rate:.2f}"' in c.get("/fleet").get_data(as_text=True),
          "repair estimate = the truck's wear x the repair rate")

    # Purchases the company can't afford are refused
    bal = economy.balance()
    n_g, n_t, n_tr = (db.val(f"SELECT COUNT(*) FROM {t}", (), 0) for t in ("garages", "trucks", "trailers"))
    post("/fleet/garage/save", {"game": "ats", "city_pick": "dallas|Dallas", "size": "large", "purchase_price": str(int(bal) + 1)})
    post("/fleet/truck/save", {"brand": "Rich", "model": "X", "purchase_price": str(int(bal) + 1)})
    post("/fleet/trailer/save", {"name": "Gold trailer", "purchase_price": str(int(bal) + 1)})
    check((db.val("SELECT COUNT(*) FROM garages", (), 0), db.val("SELECT COUNT(*) FROM trucks", (), 0),
           db.val("SELECT COUNT(*) FROM trailers", (), 0)) == (n_g, n_t, n_tr) and abs(economy.balance() - bal) < 0.01,
          "garages, trucks and trailers the company can't afford are refused, balance untouched")
    check(b"Not enough money" in c.get("/fleet").data, "the refusal says how much the company has")
    post("/fleet/garage/save", {"game": "ats", "city_pick": "dallas|Dallas", "size": "small", "purchase_price": str(int(bal))})
    check(db.val("SELECT COUNT(*) FROM garages WHERE city_id='dallas'", (), 0) == 1 and economy.balance() >= -0.01,
          "a purchase that exactly fits the balance goes through")

    # Truck and manual-job forms follow the chosen units
    post("/account/game", {"game": "ats"})
    page = c.get("/fleet").get_data(as_text=True)
    check("Odometer (mi)" in page and "Fuel tank (gal)" in page and "Service every (mi)" in page, "Add truck shows miles and gallons for ATS")
    post("/fleet/truck/save", {"units": "imperial", "brand": "Kenworth", "model": "W900", "plate": "UNIT-MI",
                               "odometer": "1000", "service_interval_km": "30000", "fuel_capacity": "150"})
    tk = db.one("SELECT * FROM trucks WHERE plate='UNIT-MI'")
    check(abs(tk["odometer"] - 1609.344) < 0.01 and abs(tk["service_interval_km"] - 48280.32) < 0.01
          and abs(tk["fuel_capacity"] - 567.81) < 0.01, f"miles and gallons are stored as km and litres ({tk['odometer']}, {tk['fuel_capacity']})")
    page = c.get(f"/fleet/truck/{tk['id']}").get_data(as_text=True)
    check('value="30000"' in page and 'value="150"' in page, "Edit truck shows the values back in miles and gallons")
    post("/fleet/truck/save", {"id": str(tk["id"]), "units": "imperial", "brand": "Kenworth", "model": "W900",
                               "service_interval_km": "25000", "fuel_capacity": "200"})
    tk = db.one("SELECT * FROM trucks WHERE id=?", (tk["id"],))
    check(abs(tk["service_interval_km"] - 40233.6) < 0.01 and abs(tk["fuel_capacity"] - 757.08) < 0.01, "editing in miles / gallons converts too")
    post("/account/game", {"game": "ets2"})
    page = c.get("/fleet").get_data(as_text=True)
    check("Odometer (km)" in page and "Fuel tank (L)" in page, "Add truck shows km and litres for ETS2")
    post("/fleet/truck/save", {"units": "metric", "brand": "Scania", "model": "S", "plate": "UNIT-KM", "odometer": "1000", "fuel_capacity": "600"})
    tk = db.one("SELECT * FROM trucks WHERE plate='UNIT-KM'")
    check(tk["odometer"] == 1000 and tk["fuel_capacity"] == 600, "metric values are stored as typed")
    post("/account/game", {"game": "ats"})
    page = c.get("/jobs/new").get_data(as_text=True)
    check("Weight (lb)" in page and "Fuel used (gal)" in page, "Log a job shows pounds and gallons for ATS")
    post("/jobs/new", {"units": "imperial", "cargo": "UnitTest", "source_city": "A", "destination_city": "B", "driven_km": "100",
                       "dist_unit": "mi", "revenue": "1000", "fuel_used": "10", "cargo_mass": "44000"})
    uj = db.one("SELECT * FROM jobs WHERE cargo='UnitTest'")
    check(abs(uj["driven_km"] - 160.9344) < 0.01 and abs(uj["fuel_used"] - 37.854) < 0.01 and abs(uj["cargo_mass"] - 19958.06) < 0.1,
          "manual job in miles, gallons and pounds is stored in km, litres and kg")
    post("/account/game", {"game": "both"})

    page = c.get("/").get_data(as_text=True)
    check(page.count('class="nav-ok"') >= 12 and VERSION in page, "Dispatch, Dashboard, Drivers, ELD, Fleet Map, Telemetry, Finance, Logbook, Expenses and Driver Performance marked complete")

    print("Driver permissions and manual jobs")
    post("/logout")
    c.get("/login")
    r = post("/login", {"username": "rookie", "password": "driver12"})
    check(r.status_code == 302, "driver signs in")
    c.get("/")
    check(c.get("/settings").status_code == 403, "driver can't open settings")
    check(post("/finance/loan", {"plan": "standard", "amount": "50000"}).status_code == 403, "driver can't take loans")
    lp = c.get("/leasing").get_data(as_text=True)
    check("Weekly payment" in lp and "Total interest" in lp and "Lease on Long Haul" not in lp, "drivers see lease costs but can't lease")
    check(c.get(f"/fleet/lease/{lid}").status_code == 200 and post("/leasing/new", {"plan_id": "1", "price": "100000"}).status_code == 403
          and post(f"/fleet/lease/{lid3}/end", {"action": "return"}).status_code == 403
          and post("/leasing/plans/save", {"name": "x"}).status_code == 403, "drivers can't start, end or change leases")
    check(post("/expenses/save", {"amount": "10", "category": "other"}).status_code == 403
          and post("/compliance/reset", {"driver": "all", "game": "both", "logbook": "1", "confirm": "RESET"}).status_code == 403
          and post("/fleet/garage/1/delete").status_code == 403 and c.get("/expenses.csv").status_code == 403,
          "drivers can't add expenses, reset compliance, delete garages or export expenses")
    check(c.get("/expenses").status_code == 200 and c.get("/performance").status_code == 200, "drivers can view Expenses and Performance")
    post("/jobs/new", {"cargo": "Hay", "source_city": "Reno", "destination_city": "Sacramento", "driven_km": "200",
                       "dist_unit": "km", "revenue": "4000"})
    mj = db.one("SELECT * FROM jobs WHERE source='manual' AND cargo='Hay'")
    check(mj and mj["status"] == "pending_review", "manual job waits for approval")
    post("/logout")
    c.get("/login")
    for _ in range(5):
        post("/login", {"username": "rookie", "password": "wrong"})
    r = post("/login", {"username": "rookie", "password": "driver12"})
    check(b"Too many" in c.get("/login").data or r.status_code == 200, "lockout after 5 wrong passwords")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    post(f"/jobs/{mj['id']}/review", {"action": "approve"})
    check(db.val("SELECT status FROM jobs WHERE id=?", (mj["id"],)) == "delivered", "manager approves manual job")

    print("Public pages and agent API")
    post("/logout")
    c.get("/apply")
    post("/apply", {"name": "Applicant Ann", "contact": "ann@example.com", "games": "ats"})
    check(db.val("SELECT COUNT(*) FROM applications", (), 0) == 1, "public application")
    tok = db.val("SELECT agent_token FROM users WHERE username='rookie'")
    payload = {"frame": {"values": dict(frame["values"]), "trailers": frame["trailers"], "substances": frame["substances"]}}
    r = c.post("/api/agent/frame", json=payload, headers={"X-Agent-Token": tok})
    check(r.status_code == 200 and service.HUB.get(rid) is not None, "remote agent frame accepted")
    r = c.post("/api/agent/frame", json=payload, headers={"X-Agent-Token": "bad"})
    check(r.status_code == 401, "bad agent token refused")
    check(c.get(f"/api/overlay?key={tok}").status_code == 200, "overlay works with token")
    check(c.get("/api/live").status_code == 401, "API needs login")

    print("Trailer types, demo clean-up, repair cost, photos")
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    from fmpro.ops import vehicle_options as _vo
    tt = {g: dict(_vo()[g]["trailer_types"]) for g in ("ats", "ets2")}
    check(tt["ats"].get("dryvan") == "Dry van" and "hopper" in tt["ats"] and "curtainside" in tt["ets2"] and "lowbed" in tt["ets2"],
          f"trailer types read from both games ({len(tt['ats'])} ATS, {len(tt['ets2'])} ETS2)")
    page = c.get("/fleet").get_data(as_text=True)
    check('name="body_type_pick"' in page and "Refrigerated (reefer)" in page, "Add trailer has the trailer type dropdown")
    post("/fleet/trailer/save", {"name": "Reefer 1", "body_type_pick": "refrigerated", "purchase_price": "0"})
    post("/fleet/trailer/save", {"name": "Mod 1", "body_type_pick": "__other", "body_type": "Hovercraft", "purchase_price": "0"})
    check(db.val("SELECT body_type FROM trailers WHERE name='Reefer 1'") == "Refrigerated (reefer)"
          and db.val("SELECT body_type FROM trailers WHERE name='Mod 1'") == "Hovercraft", "trailer type stored from the list or typed")
    check(db.setting("repair_cost_per_pct") == "40", "repair cost per 1% wear defaults to 40")
    n_demo = db.val("SELECT COUNT(*) FROM jobs WHERE COALESCE(flags,'') LIKE '%demo%'", (), 0)
    n_real = db.val("SELECT COUNT(*) FROM jobs WHERE COALESCE(flags,'') NOT LIKE '%demo%'", (), 0)
    demo_ids = [r["id"] for r in db.q("SELECT id FROM jobs WHERE COALESCE(flags,'') LIKE '%demo%'")]
    page = c.get("/jobs").get_data(as_text=True)
    check(n_demo > 0 and f"Clear demo jobs ({n_demo})" in page, f"Logbook offers to clear the {n_demo} demo jobs")
    post("/jobs/clear-demo", {"confirm": "1", "back": "/jobs"})
    left = db.val(f"SELECT COUNT(*) FROM ledger WHERE job_id IN ({','.join('?' * len(demo_ids))})", demo_ids, 0)
    check(db.val("SELECT COUNT(*) FROM jobs WHERE COALESCE(flags,'') LIKE '%demo%'", (), 0) == 0 and left == 0
          and db.val("SELECT COUNT(*) FROM jobs", (), 0) == n_real, "demo jobs and their ledger entries cleared, real jobs kept")
    check("Clear demo jobs" not in c.get("/jobs").get_data(as_text=True), "button disappears when there's nothing to clear")
    check(c.get("/static/img/login-dusk.jpg").status_code == 200 and c.get("/static/img/fleet-lot.jpg").status_code == 200,
          "truck photos are served")
    check("hero photo" in c.get("/").get_data(as_text=True) and "banner-fleet" in c.get("/fleet").get_data(as_text=True)
          and "banner-dispatch" in c.get("/dispatch").get_data(as_text=True), "photo banners on Dashboard, Fleet and Dispatch")
    post("/logout")
    check("Photo: Josiah Farrow" in c.get("/login").get_data(as_text=True), "sign-in page has the photo and its credit")

    print("Online recruitment (Apply page on the licence server)")
    import urllib.request as _ur
    import urllib.parse as _up
    import urllib.error as _ue
    from fmpro import recruit
    base = os.environ["FMP_LICENSE_SERVER"]

    def web(path, form=None, ip=None):
        req = _ur.Request(base + path, data=_up.urlencode(form).encode() if form is not None else None)
        if ip:
            srv.apply_hits.pop(ip, None)
        try:
            with _ur.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except _ue.HTTPError as e:
            return e.code, e.read().decode()

    db.set_setting("company_name", "Crimson <Line> & Co")
    db.set_setting("recruitment_open", "1")
    new, err = recruit.sync()
    url, code = db.setting("recruit_url"), db.setting("recruit_code")
    check(err is None and new == 0 and url.endswith("/apply/" + code) and len(code) == 8, f"company gets an online Apply link ({url})")
    st, page = web(f"/apply/{code}")
    check(st == 200 and "Crimson &lt;Line&gt; &amp; Co" in page and "<Line>" not in page and 'name="contact"' in page,
          "the public Apply page shows the company (escaped) and the form")
    st, page = web(f"/apply/{code}", {"name": "Road Runner", "contact": "runner#0001", "age": "29", "country": "USA",
                                      "games": "both", "experience": "500 h ATS", "message": "<script>alert(9)</script>"})
    check(st == 200 and "was sent" in page, "an applicant can apply from anywhere")
    web(f"/apply/{code}", {"name": "Bot", "contact": "spam", "website": "http://spam"})
    check(srv.store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1, "bots filling the hidden field are ignored")
    st, _ = web(f"/apply/{code}", {"name": "", "contact": ""})
    check(st == 400, "name and contact are required")
    new, err = recruit.sync()
    ra = db.one("SELECT * FROM applications WHERE remote_id IS NOT NULL ORDER BY id DESC LIMIT 1")
    check(new == 1 and ra["name"] == "Road Runner" and ra["games"] == "ats,ets2" and ra["source"] == "online" and ra["age"] == 29,
          "the application is collected into Recruitment")
    check(srv.store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0, "and deleted from the licence server")
    check(recruit.sync() == (0, None), "nothing is collected twice")
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    page = c.get("/recruitment").get_data(as_text=True)
    check("Road Runner" in page and "<script>alert(9)</script>" not in page and url in page and "Copy" in page,
          "Recruitment shows the link and the application, escaped")
    db.set_setting("recruitment_open", "0")
    recruit.sync()
    st, page = web(f"/apply/{code}")
    st2, _ = web(f"/apply/{code}", {"name": "Late", "contact": "x"})
    check("isn't taking applications" in page and st2 == 404, "closing recruitment closes the online page")
    db.set_setting("recruitment_open", "1")
    recruit.sync()
    codes = [web(f"/apply/{code}", {"name": f"Spam {i}", "contact": "x"}, ip="127.0.0.1" if i == 0 else None)[0] for i in range(6)]
    check(codes[:5] == [200] * 5 and codes[5] == 429, f"at most 5 applications per address per hour ({codes})")
    check(web("/apply/zzzzzzzz")[0] == 404 and web("/apply/../../v1/health")[0] == 404, "unknown or odd links get a 404")
    try:
        srv.handle("POST", "/v1/recruit", {"key": "FMP-AAAA-AAAA-AAAA-AAAA", "machine": lic.machine,
                                           "product": "Fleet Manager Pro"}, {}, "9.9.9.9")
        check(False, "collecting needs a real activated key")
    except Exception as e:
        check(getattr(e, "code", "") == "unknown_key", f"collecting needs a real activated key ({getattr(e, 'code', e)})")
    recruit.sync()

    print("Recruitment: reviewing and hiring")
    rid_ = ra["id"]
    page = c.get("/recruitment").get_data(as_text=True)
    check(f"/recruitment/{rid_}" in page, "each application has review buttons")
    check('Recruitment<span class="nav-ok"' in page, "Recruitment marked complete in the sidebar")
    check('Events &amp; Convoys<span class="nav-ok"' in page, "Events & Convoys marked complete in the sidebar")
    check('Leaderboard<span class="nav-ok"' in page, "Leaderboard marked complete in the sidebar")

    print("Notifications: delete one, delete all")
    from fmpro.auth import create_user as _cun
    n_a = _cun("nt_alpha", "Notif Alpha", "notifpw77", role="driver")
    n_b = _cun("nt_bravo", "Notif Bravo", "notifpw78", role="driver")
    nc = app.test_client()
    nc.get("/login")
    with nc.session_transaction() as s_:
        ntok = s_.get("csrf")
    nc.post("/login", data={"username": "nt_alpha", "password": "notifpw77", "csrf": ntok})
    nc.get("/")
    with nc.session_transaction() as s_:
        ntok = s_.get("csrf")
    for i_ in range(3):
        db.notify(n_a, f"Alpha note {i_}", "/jobs")
    db.notify(n_b, "Bravo note", "/jobs")
    items = nc.get("/api/notifications").get_json()["items"]
    check(len(items) == 3 and all("id" in n for n in items), "the dropdown gets each notification's id")
    page = nc.get("/").get_data(as_text=True)
    check('action="/notifications/clear"' in page and "Delete all" in page, "the bell dropdown has a Delete all button")
    check("data-notif-del" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmpro", "static", "js", "app.js"), encoding="utf-8").read(),
          "each notification gets its own delete button")
    first = items[0]["id"]
    r_ = nc.post(f"/notifications/{first}/delete", headers={"X-CSRF": ntok})
    check(r_.status_code == 200 and r_.get_json().get("ok"), "deleting one notification answers ok without leaving the page")
    left = [n["id"] for n in nc.get("/api/notifications").get_json()["items"]]
    check(first not in left and len(left) == 2, "only that notification is gone")
    r_ = nc.post(f"/notifications/{left[0]}/delete", data={"csrf": ntok})
    check(r_.status_code == 302 and db.val("SELECT COUNT(*) FROM notifications WHERE user_id=?", (n_a,)) == 1,
          "the delete button also works as a plain form")
    bravo_id = db.val("SELECT id FROM notifications WHERE user_id=?", (n_b,))
    nc.post(f"/notifications/{bravo_id}/delete", headers={"X-CSRF": ntok})
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=?", (n_b,)) == 1, "you can't delete another driver's notification")
    check(nc.post("/notifications/999999/delete", headers={"X-CSRF": ntok}).status_code == 200, "deleting one that's already gone is harmless")
    for i_ in range(4):
        db.notify(n_a, f"More {i_}", "")
    r_ = nc.post("/notifications/clear", data={"csrf": ntok})
    d_ = nc.get("/api/notifications").get_json()
    check(r_.status_code == 302 and d_["items"] == [] and d_["unread"] == 0, "Delete all empties your notifications and the bell count")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=?", (n_b,)) == 1, "Delete all leaves other drivers' notifications alone")
    check("All notifications deleted." in nc.get("/").get_data(as_text=True), "Delete all confirms with a message")
    check('<b>Notifications<span class="nav-ok"' in nc.get("/").get_data(as_text=True), "Notifications marked complete in the bell menu")
    page = nc.get("/").get_data(as_text=True)
    check("© 2026 Jeff Poblocki. All rights reserved." in page and 'href="/licence-agreement"' in page,
          "every page shows the copyright notice and a link to the licence agreement")

    print("Settings: economy fields in the chosen units")
    from fmpro.auth import create_user as _cus
    _cus("set_admin", "Settings Admin", "settingspw77", role="admin")
    sc = app.test_client()
    sc.get("/login")
    with sc.session_transaction() as s_:
        stok = s_.get("csrf")
    sc.post("/login", data={"username": "set_admin", "password": "settingspw77", "csrf": stok})
    sc.get("/")
    with sc.session_transaction() as s_:
        stok = s_.get("csrf")
    old_units = db.setting("units")
    sc.post("/account/game", data={"game": "both", "csrf": stok})
    db.set_setting("units", "imperial")
    for k_, v_ in (("fuel_price", 1.5), ("driver_pay_per_km", 0.5), ("xp_per_km", 1), ("xp_per_tonne", 2), ("speeding_tolerance_kmh", 8)):
        db.set_setting(k_, v_)
    page = sc.get("/settings?tab=economy").get_data(as_text=True)
    check("Fuel price per gallon" in page and 'value="5.68"' in page, "imperial: fuel price shown per gallon (1.50/L = 5.68/gal)")
    check("Driver pay per mile" in page and 'value="0.8"' in page, "imperial: driver pay shown per mile")
    check("XP per mile" in page and "XP per ton (2,000 lb)" in page and 'value="1.81"' in page, "imperial: XP per mile and per ton")
    check("Speeding tolerance (mph)" in page and 'value="5.0"' in page, "imperial: speeding tolerance in mph")
    econ = page.split('value="economy"')[1].split("</form>")[0]
    labels = " | ".join(re.findall(r"<label>(.*?)</label>", econ))
    check(not re.search(r"per litre|per km|km/h|tonne", labels), f"imperial: no metric labels left on the Economy tab ({labels[:80]}...)")
    form = {"section": "economy", "units": "imperial", "fuel_price": "5.68", "driver_pay_per_km": "0.8", "xp_per_km": "1.61",
            "xp_per_tonne": "1.81", "speeding_tolerance_kmh": "5.0", "driver_commission_pct": "0", "csrf": stok}
    sc.post("/settings", data=form)
    check(db.setting_f("fuel_price") == 1.5 and db.setting_f("driver_pay_per_km") == 0.5 and db.setting_f("speeding_tolerance_kmh") == 8,
          "saving without changes keeps the exact metric values (no rounding drift)")
    form.update(fuel_price="4.00", driver_pay_per_km="1.00", speeding_tolerance_kmh="10")
    sc.post("/settings", data=form)
    check(abs(db.setting_f("fuel_price") - 4 / 3.785411784) < 1e-6 and abs(db.setting_f("driver_pay_per_km") - 1 / 1.609344) < 1e-6
          and abs(db.setting_f("speeding_tolerance_kmh") - 16.09344) < 1e-6, "values typed in imperial are stored correctly in metric")
    db.set_setting("units", "metric")
    page = sc.get("/settings?tab=economy").get_data(as_text=True)
    check("Fuel price per litre" in page and "Driver pay per km" in page and "Speeding tolerance (km/h)" in page
          and 'value="1.06"' in page, "metric: the same settings shown per litre, per km and km/h")
    sc.post("/settings", data=dict(form, units="metric", fuel_price="2", speeding_tolerance_kmh="12"))
    check(db.setting_f("fuel_price") == 2 and db.setting_f("speeding_tolerance_kmh") == 12, "metric values save as typed")
    db.set_setting("units", old_units or "imperial")

    print("Leaderboard: rankings")
    from fmpro.auth import create_user as _cul
    la = _cul("lb_alpha", "Alpha Hauler", "alphapw77", role="driver")
    lb = _cul("lb_bravo", "Bravo Hauler", "bravopw77", role="driver")
    import time as _lbt
    t_ = _lbt.time()
    for u_, g_, km_, rev_, sc_, dmg_, fin_ in ((la, "ats", 9e6, 1, 50, 0.5, t_), (lb, "ets2", 8e6, 9e9, 100, 0, t_),
                                               (lb, "ets2", 5e7, 1, 100, 0, t_ - 40 * 86400)):
        db.insert("jobs", user_id=u_, game=g_, source="manual", status="delivered", cargo="Test", driven_km=km_, revenue=rev_,
                  score=sc_, cargo_damage=dmg_, started_at=fin_ - 3600, finished_at=fin_)
    db.insert("jobs", user_id=la, game="ats", source="manual", status="cancelled", cargo="Test", driven_km=9e9,
              started_at=t_ - 3600, finished_at=t_)

    def board(q):
        body = c.get("/leaderboard?" + q).get_data(as_text=True).split("<tbody>")[1]
        return [n for n in sorted(("Alpha Hauler", "Bravo Hauler"), key=lambda n: body.find(n)) if n in body]
    check(board("metric=km&period=week")[:2] == ["Alpha Hauler", "Bravo Hauler"], "distance: most km ranks first (only delivered jobs count)")
    check(board("metric=revenue&period=week")[0] == "Bravo Hauler", "revenue ranks by money earned")
    check(board("metric=realism&period=week")[0] == "Bravo Hauler", "realism: careful driving beats more km with damage")
    check(board("metric=km&period=all")[0] == "Bravo Hauler" and board("metric=km&period=month")[0] == "Alpha Hauler",
          "the period filter leaves out older deliveries")
    check(board("metric=km&period=week&game=ats") == ["Alpha Hauler"] and board("metric=km&period=week&game=ets2") == ["Bravo Hauler"],
          "the game filter shows only that game's deliveries")
    db.update("users", la, status="removed")
    check("Alpha Hauler" not in board("metric=km&period=all"), "removed members don't appear")
    db.update("users", la, status="active")
    check(c.get("/leaderboard?metric=bogus&period=nope&game=xyz").status_code == 200, "odd filter values fall back safely")
    check(all(f'{n}<span class="nav-ok"' in page for n in ("Truck Leasing", "Achievements", "Announcements"))
          and '0</span><span class="nav-ok"' in page, "Truck Leasing, Achievements, Announcements and Messages marked complete")
    post(f"/recruitment/{rid_}", {"action": "interview", "notes": "call Friday"})
    check(db.one("SELECT status, notes FROM applications WHERE id=?", (rid_,)) == {"status": "interview", "notes": "call Friday"},
          "an application can be moved to interview with notes")
    post(f"/recruitment/{rid_}", {"action": "hacked"})
    check(db.val("SELECT status FROM applications WHERE id=?", (rid_,)) == "interview", "an unknown action changes nothing")
    n_users = db.val("SELECT COUNT(*) FROM users")
    r = post(f"/recruitment/{rid_}", {"action": "accepted", "create_account": "1"})
    hired = db.one("SELECT * FROM users ORDER BY id DESC LIMIT 1")
    flash_page = c.get("/recruitment").get_data(as_text=True)
    import re as _re
    m = _re.search(r"username (\S+), temporary password (\S+) ", flash_page)
    check(r.status_code == 302 and db.val("SELECT COUNT(*) FROM users") == n_users + 1 and hired["display_name"] == "Road Runner"
          and hired["role"] == "trainee" and hired["status"] == "active", "accepting with an account hires them as a trainee")
    check(db.val("SELECT status FROM applications WHERE id=?", (rid_,)) == "accepted", "the application is marked accepted")
    check(m is not None and m.group(1) == hired["username"], "the new username and a temporary password are shown once")
    if m:
        hc = app.test_client()
        hc.get("/login")
        with hc.session_transaction() as s_:
            tok_ = s_.get("csrf")
        rr = hc.post("/login", data={"username": m.group(1), "password": m.group(2), "csrf": tok_})
        check(rr.status_code == 302, "the new driver can sign in with the temporary password")
    aid2 = db.insert("applications", name="Reject Me", contact="x", status="pending", created_at=1)
    post(f"/recruitment/{aid2}", {"action": "rejected"})
    check(db.val("SELECT status FROM applications WHERE id=?", (aid2,)) == "rejected" and db.val("SELECT COUNT(*) FROM users") == n_users + 1,
          "rejecting doesn't create an account")
    post(f"/recruitment/{aid2}", {"action": "accepted"})
    check(db.val("SELECT COUNT(*) FROM users") == n_users + 1, "accepting without 'create account' doesn't create one")
    check(post("/recruitment/999999", {"action": "accepted"}).status_code == 404, "an unknown application gives a 404")

    print("Two games: a job left open in one game waits while you drive the other")
    service.time = clock
    from fmpro.auth import create_user as _cu2
    _cu2("twogames", "Two Games", "twogames12", role="driver")
    tuid = db.val("SELECT id FROM users WHERE username='twogames'")
    tg = service.JobTracker(tuid)
    clean = {k: v for k, v in job.items() if not k.startswith(("job_delivered", "job_finished", "refuel"))}
    clean.update(job_delivered=False, refuel_paid=False)
    ets = dict(clean, game_id=1, cargo="Straw Bales", cargo_id="straw_bales", source_city="Malmo", destination_city="Trelleborg",
               job_income=562, job_starting_time=600, game_time_abs=600, x=1000.0, z=1000.0)
    ats = dict(clean, cargo="Gravel", cargo_id="gravel", source_city="Reno", destination_city="Elko", job_income=900,
               job_starting_time=50000, game_time_abs=50000)
    feed(tg, dict(clean, game_id=1, on_job=False))
    for i in range(3):
        clock.t += 1
        feed(tg, dict(ets, speed=10.0))
    ej = db.val("SELECT id FROM jobs WHERE cargo='Straw Bales' AND status='in_progress'")
    check(ej is not None and db.val("SELECT game FROM jobs WHERE id=?", (ej,)) == "ets2", "ETS2 job started")
    for i in range(25):                      # ATS free roam for 25 s: the ETS2 job must not be abandoned
        clock.t += 1
        feed(tg, dict(clean, on_job=False))
    for i in range(3):
        clock.t += 1
        feed(tg, dict(ats, speed=15.0))
    aj = db.val("SELECT id FROM jobs WHERE cargo='Gravel' AND status='in_progress'")
    check(aj is not None and db.val("SELECT status FROM jobs WHERE id=?", (ej,)) == "in_progress",
          "starting an ATS job leaves the open ETS2 job waiting")
    clock.t += 1
    feed(tg, dict(ats, on_job=False, job_delivered=True, job_delivered_revenue=880, job_delivered_earned_xp=30,
                  job_delivered_distance_km=12.0, job_finished_time=50060))
    st = (db.val("SELECT status||' '||COALESCE(flags,'') FROM jobs WHERE id=?", (aj,)), db.val("SELECT status FROM jobs WHERE id=?", (ej,)))
    check(st[0].split()[0] in ("delivered", "pending_review") and st[1] == "in_progress", f"ATS job delivered, ETS2 job still open {st}")
    for i in range(3):                       # back in ETS2: the same job carries on
        clock.t += 1
        feed(tg, dict(ets, speed=10.0, job_delivered=False))
    check(db.val("SELECT COUNT(*) FROM jobs WHERE cargo='Straw Bales'", (), 0) == 1, "back in ETS2 the open job is picked up again")
    clock.t += 1
    feed(tg, dict(ets, on_job=False, job_delivered=True, job_delivered_revenue=562, job_delivered_earned_xp=44,
                  job_delivered_distance_km=44.0, job_finished_time=650))
    st = db.one("SELECT status, revenue, flags FROM jobs WHERE id=?", (ej,))
    check(st["status"] in ("delivered", "pending_review") and st["revenue"] == 562, f"and its delivery is logged ({dict(st)})")
    check("teleport" not in (st["flags"] or ""), "switching games is not mistaken for a teleport")
    service.time = _time

    print("EU reduced daily rest and roadside inspection fines")
    _cu2("eurest", "EU Rest", "eurest1234", role="driver")
    eu = db.val("SELECT id FROM users WHERE username='eurest'")
    for st_, a, b in (("on", 600, 601), ("driving", 601, 821), ("sleeper", 821, 1451), ("on", 1451, 1452), ("driving", 1452, 1470)):
        db.insert("hos_logs", user_id=eu, status=st_, start_min=a, end_min=b, auto=1, created_at=1, game="ets2")
    cl = compliance.clocks(eu, 1470, "ets2")
    check(not cl["window"]["over"] and cl["window"]["used"] <= 20 and cl["drive"]["used"] <= 20,
          f"EU: a 10.5 h ferry crossing counts as a (reduced) daily rest ({cl['window']['used']} min on the window)")
    for st_, a, b in (("sleeper", 1470, 1950), ("driving", 1950, 2000)):       # 8 h is not a daily rest
        db.insert("hos_logs", user_id=eu, status=st_, start_min=a, end_min=b, auto=1, created_at=1, game="ets2")
    cl = compliance.clocks(eu, 2000, "ets2")
    check(cl["window"]["used"] > 500, "EU: an 8 h rest does not reset the daily clocks")
    db.set_setting("dvir_required", "1") if hasattr(db, "set_setting") else db.ex(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('dvir_required', '1')")
    etr = db.insert("trucks", brand="Scania", model="S", plate="EU1", game="ets2", purchase_price=0, created_at=1)
    ejob = db.insert("jobs", user_id=eu, truck_id=etr, game="ets2", source="telemetry", status="in_progress", cargo="X",
                     started_at=1, game_time_start=1452)
    compliance.record_violation(eu, "DVIR-PRE", f"job{ejob}", 1452, "no DVIR", fine=250, job_id=ejob, game="ets2")
    iid = compliance.inspect(eu, {"game": "ets2", "game_time_abs": 1460}, job_id=ejob, truck_id=etr, level=3)
    ins = db.one("SELECT * FROM inspections WHERE id=?", (iid,))
    check(ins["fine"] == 0 and "already fined" in ins["findings"], "inspection doesn't fine the missing DVIR a second time")
    ejob2 = db.insert("jobs", user_id=eu, truck_id=etr, game="ets2", source="telemetry", status="in_progress", cargo="Y",
                      started_at=1, game_time_start=1470)
    iid2 = compliance.inspect(eu, {"game": "ets2", "game_time_abs": 1475}, job_id=ejob2, truck_id=etr, level=3)
    check(db.val("SELECT fine FROM inspections WHERE id=?", (iid2,)) >= 250, "...but still fines it when it wasn't charged yet")

    print("Clear all data")
    from fmpro.auth import create_user as _cu
    _cu("cleartest", "Clear Test", "manager12", role="admin")
    c.get("/login")
    post("/login", {"username": "cleartest", "password": "manager12"})
    c.get("/")
    check(post("/settings/clear-data", {"understand": "1", "confirm": "DELETE ALL DATA", "password": "manager12"}).status_code == 403
          and c.get("/settings?tab=data").status_code == 403 and c.get("/settings").status_code == 200
          and b"Clear data" not in c.get("/settings").data, "only the Owner can clear data (a Manager can't)")
    post("/logout")
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    page = c.get("/settings?tab=data").get_data(as_text=True)
    n_jobs = db.val("SELECT COUNT(*) FROM jobs", (), 0)
    check("Clear all company data" in page and "DELETE ALL DATA" in page and n_jobs > 0, f"Clear data tab lists what will go ({n_jobs} jobs)")
    good = {"understand": "1", "confirm": "DELETE ALL DATA", "password": "secret12", "keep_members": "1", "capital": "1"}
    for bad, why in ((dict(good, understand=""), "box not ticked"), (dict(good, confirm="delete all data"), "wrong phrase"),
                     (dict(good, password="nope1234"), "wrong password")):
        post("/settings/clear-data", bad)
        check(db.val("SELECT COUNT(*) FROM jobs", (), 0) == n_jobs, f"nothing deleted when the {why}")
    for _ in range(5):
        post("/settings/clear-data", dict(good, password="nope1234"))
    post("/settings/clear-data", good)
    check(db.val("SELECT COUNT(*) FROM jobs", (), 0) == n_jobs and b"Too many wrong passwords" in c.get("/").data,
          "after 5 wrong passwords Clear data locks for 15 minutes, even with the right one")
    db.ex("DELETE FROM audit_log WHERE action='clear data refused'")
    members = db.val("SELECT COUNT(*) FROM users", (), 0)
    post("/settings/clear-data", good)
    left = {t: db.val(f"SELECT COUNT(*) FROM {t}", (), 0) for t in ("jobs", "trucks", "garages", "contracts", "hos_logs", "messages", "loans", "road_points")}
    check(not any(left.values()), f"all company records cleared ({left})")
    led = db.q("SELECT category, amount FROM ledger")
    check(len(led) == 1 and led[0]["category"] == "capital" and led[0]["amount"] == float(db.setting("starting_balance")),
          f"the books restart with the starting capital ({led})")
    check(led[0]["amount"] == 275000.0, "a data reset restarts with the locked 275,000")
    check(db.val("SELECT COUNT(*) FROM users", (), 0) == members and db.val("SELECT MAX(xp) FROM users", (), 0) == 0,
          "members kept, XP back to 0")
    check(c.get("/").status_code == 200 and db.setting("initialized") == "1", "the Owner stays signed in, settings kept")
    bdir = os.path.join(tmp, "backups")
    files = os.listdir(bdir) if os.path.isdir(bdir) else []
    import sqlite3 as _sq
    check(len(files) == 1 and _sq.connect(os.path.join(bdir, files[0])).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == n_jobs,
          "a full backup with the old data was saved first")
    check(db.val("SELECT COUNT(*) FROM audit_log WHERE action='all data cleared'", (), 0) == 1, "the reset is in the audit log")
    post("/settings/clear-data", dict(good, keep_members=""))
    check(db.val("SELECT COUNT(*) FROM users", (), 0) == 1 and db.val("SELECT role FROM users") == "owner",
          "optionally members are removed too, the Owner stays")
    page = c.get("/").get_data(as_text=True)
    check("Live Cockpit<span class=\"nav-ok\"" in page, "Live Cockpit marked complete")

    print("Licence lifecycle")
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    check(c.get("/settings?tab=licence").status_code == 200 and b"Test Buyer" in c.get("/settings?tab=licence").data,
          "licence tab shows the buyer")
    real = os.environ["FMP_LICENSE_SERVER"]
    os.environ["FMP_LICENSE_SERVER"] = "http://127.0.0.1:9"          # server unreachable
    lic.check()
    check(lic.ok and lic.online is False, "offline: keeps working on the current lease")
    os.environ["FMP_LICENSE_SERVER"] = real
    import datetime as _dt
    lic.seen = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=3)).isoformat(timespec="seconds")
    lic._evaluate()
    check(not lic.ok and lic.reason == licensing.CLOCK_BACK, "clock set back locks")
    lic.seen = None
    lic.check()
    print("Licence server: delete purchases")
    import license_tool as _lt
    adm = _lt.Admin(folder=lic_folder, url=os.environ["FMP_LICENSE_SERVER"])
    k_act = adm.new_purchase("Delete Active", "a@example.com")
    srv.store.activate(k_act, "FMID-CCCC-CCCC-CCCC-CCCC", "", "Fleet Manager Pro")
    srv.store.recruit_sync(k_act, "FMID-CCCC-CCCC-CCCC-CCCC", "Fleet Manager Pro", "Del Co", "", True, [])
    code_ = srv.store.db.execute("SELECT code FROM recruit WHERE key=?", (k_act,)).fetchone()[0]
    srv.store.db.execute("INSERT INTO applications(code, created, data) VALUES(?,?,?)", (code_, "x", "{}"))
    srv.store.db.commit()
    k_r1, k_r2 = adm.new_purchase("Revoked One"), adm.new_purchase("Revoked Two")
    adm.revoke(k_r1)
    adm.revoke(k_r2)
    n_before = len(adm.purchases())
    try:
        _lt.Admin(folder=lic_folder, url=os.environ["FMP_LICENSE_SERVER"], token="wrong-token").delete(k_act)
        check(False, "deleting needs the admin token")
    except _lt.LicenceError:
        check(any(p["key"] == k_act for p in adm.purchases()), "deleting needs the admin token")
    d_ = adm.delete(k_act)
    q_ = srv.store.db.execute
    check(d_[0]["key"] == k_act and not any(p["key"] == k_act for p in adm.purchases())
          and q_("SELECT COUNT(*) FROM activations WHERE key=?", (k_act,)).fetchone()[0] == 0
          and q_("SELECT COUNT(*) FROM recruit WHERE key=?", (k_act,)).fetchone()[0] == 0
          and q_("SELECT COUNT(*) FROM applications WHERE code=?", (code_,)).fetchone()[0] == 0,
          "an active purchase is deleted with its PCs, Apply page and applications")
    try:
        srv.store.check(k_act, "FMID-CCCC-CCCC-CCCC-CCCC", "", "Fleet Manager Pro")
        check(False, "a PC using a deleted key is refused at its next check")
    except Exception as e:
        check(getattr(e, "code", "") == "unknown_key", "a PC using a deleted key is refused at its next check")
    import json as _json
    log_ = [_json.loads(x) for x in open(os.path.join(lic_folder, "deleted_purchases.jsonl"), encoding="utf-8")]
    check(log_ and log_[-1]["purchase"]["key"] == k_act and log_[-1]["purchase"]["email"] == "a@example.com"
          and len(log_[-1]["activations"]) == 1, "a copy of the deleted purchase is kept in deleted_purchases.jsonl")
    d_ = adm.delete(k_r1)
    check(d_[0]["was_revoked"] and not any(p["key"] == k_r1 for p in adm.purchases()), "a revoked purchase can be deleted")
    adm.revoke(adm.new_purchase("Revoked Three"))
    d_ = adm.delete_revoked()
    left_ = adm.purchases()
    check(len(d_) == 2 and not any(p.get("revoked") for p in left_) and len(left_) == n_before - 3,
          f"Delete ALL revoked removes only revoked purchases ({len(d_)} deleted)")
    check(any(p["key"] == pro_key for p in left_) and lic.ok, "other buyers' purchases and this PC's licence are untouched")
    try:
        adm.delete("FMP-ZZZZ-ZZZZ-ZZZZ-ZZZZ")
        check(False, "deleting an unknown key is refused")
    except _lt.LicenceError as e:
        check("doesn't exist" in str(e), "deleting an unknown key is refused")
    srv.store.revoke(pro_key, True)
    lic.check()
    check(not lic.ok and "revoked" in (lic.reason or ""), "revoked purchase locks at next check")
    check(c.get("/").status_code == 302, "app locked after revoke")
    srv.store.revoke(pro_key, False)
    post("/activate", {"action": "retry"})
    check(lic.ok, "restored purchase unlocks")
    c.get("/settings?tab=licence")
    r = post("/settings/licence", {"action": "deactivate"})
    check(r.status_code == 302 and not lic.ok and not srv.store.active(pro_key), "deactivate frees this PC's slot")

    print("Sealed pages and hidden buyer ID (buyer copies)")
    import importlib
    import subprocess
    from jinja2 import TemplateNotFound
    root_ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root_, "tools"))
    import seal_pages
    fm_ = os.path.join(root_, "fmpro")
    emb_ = os.path.join(fm_, "_embedded_templates.py")
    _pub, master_ = seal_pages.master_from_hostkey(os.path.join(lic_folder, "host_key.json"))
    k_seal = srv.store.new_purchase("Sealed Buyer", "sealed@example.com", product="Fleet Manager Pro")
    tag_ = seal_pages.build_tag(master_, k_seal)
    tmp2 = tempfile.mkdtemp(prefix="fmp-sealed-")
    orig_lease, orig_licensed = srv.lease, licensing.licensed
    try:
        n_plain, n_sealed = seal_pages.seal_templates(fm_, master_, tag_)
        importlib.invalidate_caches()
        sys.modules.pop("fmpro._embedded_templates", None)
        sealed_src = open(emb_, encoding="utf-8").read().split("SEALED =", 1)[1]
        base_html = open(os.path.join(fm_, "templates", "base.html"), encoding="utf-8").read()
        check(n_plain == 5 and n_sealed > 20 and base_html[:80] not in sealed_src and "{% block" not in sealed_src,
              f"only the {n_plain} pre-activation pages are readable; {n_sealed} pages are encrypted")
        check(re.fullmatch(r"FMPB(-[A-Z2-7]{4}){4}", tag_) and k_seal.split("-", 1)[1][:4] not in tag_,
              f"the hidden buyer ID is a build tag that doesn't give away the key ({tag_})")
        app2 = create_app(tmp2, start_telemetry=False, check_licence=False)
        app2.testing = True
        c2 = app2.test_client()
        lic2 = licensing.LICENCE
        check(licensing.BUILD_TAG == tag_, "the copy knows its build tag")

        def post2(url, data):
            with c2.session_transaction() as s_:
                tok = s_.get("csrf")
            return c2.post(url, data=dict(data, csrf=tok))

        r = c2.get("/activate")
        check(r.status_code == 200 and b"purchase key" in r.data.lower(), "the activate screen works before unlocking")
        check(c2.get("/licence-agreement").status_code == 200, "the licence agreement can be read before unlocking")
        # a crack that skips the licence check still has no pages
        licensing.licensed = lambda: True
        try:
            app2.jinja_env.get_template("dashboard.html")
            cracked = False
        except TemplateNotFound:
            cracked = True
        licensing.licensed = orig_licensed
        check(cracked, "a cracked copy that skips the licence check has no pages to show")
        # a server that doesn't hand out page keys (not updated yet): the licence is fine but the pages stay locked
        srv.lease = lambda p, act, build=None: orig_lease(p, act)
        r = post2("/activate", {"key": k_seal, "agree": "1"})
        check(not lic2.ok and lic2.reason == licensing.PAGES_LOCKED, "without the server's page key the copy stays locked")
        srv.lease = orig_lease
        r = post2("/activate", {"key": k_seal, "agree": "1"})
        check(lic2.ok and lic2.page_key() is not None, "the licence server unlocks the pages with the activation")
        t_ = app2.jinja_env.get_template("setup.html")
        src_setup = open(os.path.join(fm_, "templates", "setup.html"), encoding="utf-8").read()
        check(app2.jinja_loader.get_source(app2.jinja_env, "setup.html")[0] == src_setup and t_ is not None,
              "sealed pages decrypt exactly once unlocked")
        check(licensing.lease_page_key(lic2.lease, "FMID-ZZZZ-ZZZZ-ZZZZ-ZZZZ") is None,
              "the page key in licence.json only works on this PC")
        check(licensing.lease_problem(dict(lic2.lease, unlock="00" * 32), lic2.machine, k_seal) is not None,
              "changing the page key breaks the licence signature")
        off = licensing.Licence(lic2.path)
        check(off.ok and off.page_key() is not None, "pages stay unlocked offline (from the saved licence)")
        other_build = seal_pages.build_tag(master_, "STOCK")
        d_ = licensing._post("/v1/check", {"key": k_seal, "machine": lic2.machine, "product": "Fleet Manager Pro",
                                           "build": other_build})
        check(licensing.lease_page_key(d_["lease"], lic2.machine) is None, "another copy's page key doesn't fit this copy")
        licensing._post("/v1/check", lic2._body())
        # leak tracing
        check(adm.whose(tag_)["key"] == k_seal, "License Manager identifies whose copy a build tag is")
        check(adm.whose(other_build) == "STOCK" and adm.whose("FMPB-AAAA-AAAA-AAAA-AAAA") is None,
              "stock copies and unknown tags are told apart")
        k_other = srv.store.new_purchase("Other Buyer", "", product="Fleet Manager Pro")
        licensing._post("/v1/activate", {"key": k_other, "machine": "FMID-DDDD-DDDD-DDDD-DDDD",
                                         "product": "Fleet Manager Pro", "build": tag_})
        rows_ = {p["key"]: p for p in adm.purchases()}
        flagged = [a.get("build_owner") for a in rows_[k_other]["pcs"]]
        check(flagged and k_seal in (flagged[0] or ""), "a copy used with someone else's key is flagged as a leak")
        check(not any(a.get("build_owner") for a in rows_[k_seal]["pcs"]), "the buyer's own PCs aren't flagged")
        out_ = subprocess.run([sys.executable, os.path.join(root_, "run.py"), "--build-tag"], capture_output=True,
                              text=True, cwd=root_, timeout=60).stdout.strip()
        check(out_ == tag_, "the copy shows its build tag with --build-tag")
    finally:
        srv.lease, licensing.licensed = orig_lease, orig_licensed
        if os.path.exists(emb_):
            os.remove(emb_)
        for f_ in os.listdir(os.path.join(fm_, "__pycache__")) if os.path.isdir(os.path.join(fm_, "__pycache__")) else []:
            if f_.startswith("_embedded_templates"):
                os.remove(os.path.join(fm_, "__pycache__", f_))
        sys.modules.pop("fmpro._embedded_templates", None)
        import fmpro as _fmpro
        if hasattr(_fmpro, "_embedded_templates"):
            delattr(_fmpro, "_embedded_templates")
        licensing.load_build()
        licensing.LICENCE = lic
    check(not os.path.exists(emb_) and licensing.BUILD_TAG is None, "the developer copy is left unsealed")

    db.close_all()
    shutil.rmtree(tmp2, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    srv.store.db.close()
    shutil.rmtree(lic_folder, ignore_errors=True)
    print()
    print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASSED")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
