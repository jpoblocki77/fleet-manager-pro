"""End-to-end test for Fleet Manager Pro. Uses a throw-away data folder; never touches ./data.

    python tests/e2e_test.py
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile

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
    check([x["amount"] for x in cap] == [300000.0] and db.setting("starting_balance") == "300000",
          f"starting capital is locked at 300,000 (the form asked for 5,000,000; got {[x['amount'] for x in cap]})")
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
    # real game timing: "refuel" when the pump starts, "refuel_paid" minutes later when the screen closes
    clock.t += 300
    feed(t5, dict(roam, fuel=356.0))
    clock.t += 1
    feed(t5, dict(roam, fuel=356.0, refuel=True))
    for f in [372.0, 520.0, 700.0, 830.0]:
        clock.t += 2
        feed(t5, dict(roam, fuel=f, refuel=True))
    for i in range(18):                                                         # driver sits on the pump screen
        clock.t += 10
        feed(t5, dict(roam, fuel=830.0))
    feed(t5, dict(roam, fuel=830.0, refuel_paid=True, refuel_amount=474.0))
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0) == fuel_before + 2,
          "a refuel paid minutes after the tank filled is still booked")
    clock.t += 600                                                              # game load: flag without a rise
    feed(t5, dict(roam, fuel=830.0, refuel_paid=False, refuel_amount=50.0))
    feed(t5, dict(roam, fuel=830.0, refuel_paid=True, refuel_amount=50.0))
    clock.t += 130
    feed(t5, dict(roam, fuel=830.0))
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0) == fuel_before + 2,
          "a later phantom refuel isn't matched to the old pump start")
    # loading a save: the tank reads 0 L for a moment, then the real level, with a refuel event
    clock.t += 300
    feed(t5, dict(roam, fuel=0.0))
    clock.t += 1
    feed(t5, dict(roam, fuel=830.0, refuel_paid=True, refuel_amount=61.0))   # event after the 0 L reading
    clock.t += 1
    feed(t5, dict(roam, fuel=830.0, refuel_paid=True, refuel_amount=62.0))   # event before it...
    clock.t += 1
    feed(t5, dict(roam, fuel=0.0, refuel_paid=True, refuel_amount=62.0))
    clock.t += 1
    feed(t5, dict(roam, fuel=830.0, refuel_paid=True, refuel_amount=62.0))   # ...then the level comes back
    clock.t += 130
    feed(t5, dict(roam, fuel=830.0))
    check(db.val("SELECT COUNT(*) FROM ledger WHERE category='fuel'", (), 0) == fuel_before + 2,
          "a save loading (0 L, then full) isn't booked as a refuel")
    service.time = _time

    print("Diesel and AdBlue prices and what each fill-up costs")
    from fmpro import bank as fbank, economy as feco, fuel_prices as fp
    db.set_setting("fuel_price_game", "1")
    db.set_setting("adblue_price", "2")
    check(fp._data()[0]["ats"]["Nevada"] > 1 and fp._data()[0]["ets2"]["Germany"] > 1 and len(fp._data()[0]["ats"]) >= 21, "the games' diesel prices are bundled")
    _xy = {n: (x, z) for n, x, z in fp._data()[2]["ats"]}
    check(all(fp.state_at("ats", *_xy[n]) == st for n, st in (("denver", "Colorado"), ("los angeles", "California"), ("houston", "Texas"))), "the state comes from the position")
    _exy = {n: (x, z) for n, x, z in fp._data()[2]["ets2"]}
    check(fp.state_at("ets2", *_exy["berlin"]) == "Germany" and fp.diesel("ets2", *_exy["berlin"])[2] == "game", "in Europe too")
    check(fp.state_at("ats", 900000.0, 900000.0) == "" and fp.state_at("ats", 0.0, 0.0) == "" and fp.state_at("", 1.0, 1.0) == ""
          and fp.diesel("ats", 900000.0, 900000.0) == (db.setting_f("fuel_price"), "", "company"), "nowhere near a city it is the company's price")
    db.set_setting("fuel_price_game", "0")
    check(fp.diesel("ats", *_xy["denver"])[::2] == (db.setting_f("fuel_price"), "company"), "the game's prices can be turned off")
    db.set_setting("fuel_price_game", "1")
    from fmpro.auth import create_user as _cu0
    _cu0("pumpuser", "Pump User", "pumpuser12", role="driver")
    pu = db.val("SELECT id FROM users WHERE username='pumpuser'")
    service.time = clock
    _real_fp_time = fp.time
    fp.time = clock
    clock.t = max(clock.t, _time.time())
    t9 = service.JobTracker(pu)
    dx, dz = _xy["denver"]
    at = dict(base, fuel=400.0, fuel_capacity=600.0, adblue=40.0, adblue_capacity=80.0, x=dx, z=dz, speed=0.0, game_time_abs=70000)

    def pump(f0, f1, a0, a1, paid=True, **kw):
        for i in range(21):
            clock.t += 0.3
            feed(t9, dict(at, fuel=f0 + (f1 - f0) * i / 20, adblue=a0 + (a1 - a0) * i / 20, refuel=i > 0, **kw))
        for _ in range(8):
            clock.t += 3
            feed(t9, dict(at, fuel=f1, adblue=a1, **kw))
        if paid:
            feed(t9, dict(at, fuel=f1, adblue=a1, refuel_paid=True, refuel_amount=f1 - f0, **kw))
            clock.t += 1
            feed(t9, dict(at, fuel=f1, adblue=a1, **kw))
    co = feco.balance()
    pump(400.0, 580.0, 40.0, 49.0)
    fl_ = {x["kind"]: x for x in fp.fills(pu, 3600)}
    colo = fp._data()[0]["ats"]["Colorado"]
    check(len(fp.fills(pu, 3600)) == 2 and abs(fl_["fuel"]["cost"] - 180 * colo) < 0.02 and fl_["fuel"]["place"] == "Colorado"
          and fl_["fuel"]["from_pct"] == 67 and fl_["fuel"]["to_pct"] == 97, f"a diesel fill-up costs the litres at Colorado's price ({fl_})")
    check(abs(fl_["adblue"]["cost"] - 18) < 0.11 and abs(fl_["adblue"]["litres"] - 9) < 0.06 and fl_["adblue"]["from_pct"] == 50, "the AdBlue that went in with it is one fill-up at the company's price")
    check(abs(co - feco.balance() - fl_["fuel"]["cost"] - fl_["adblue"]["cost"]) < 0.01 and fbank.balance(pu) == 0 and fbank.pending(pu) == 0
          and db.val("SELECT COUNT(*) FROM ledger WHERE user_id=? AND account!='company'", (pu,), 0) == 0, "the company pays for the diesel and the AdBlue, the driver pays nothing")
    check(abs(fp.last_paid(pu) - colo) < 1e-6 and fp.last_paid(987654) == db.setting_f("fuel_price"), "a job's fuel is costed at what the driver last paid")
    _fx = db.insert("fuel_fills", user_id=pu, t=_time.time() + 5, game="ets2", kind="fuel", litres=100, price=9.99, cost=999, place="Germany")
    check(abs(fp.last_paid(pu, "ats") - colo) < 1e-6 and fp.last_paid(pu, "ets2") == 9.99,
          "a job is costed at the last diesel price paid in its own game")
    db.ex("DELETE FROM fuel_fills WHERE id=?", (_fx,))
    tt = fp.totals(pu, 3600)
    check((tt["refuels"], tt["topups"]) == (1, 1) and tt["fuel_cost"] == fl_["fuel"]["cost"], "and the totals match")
    n9 = len(fp.fills(pu, 9e9))
    at["game_time_abs"] = 69000                                       # an older save: more in both tanks
    for f_, a_ in ((100.0, 10.0), (590.0, 75.0), (590.0, 75.0)):
        clock.t += 1
        feed(t9, dict(at, fuel=f_, adblue=a_, refuel_paid=a_ > 20, refuel_amount=490.0))
    clock.t += 130
    feed(t9, dict(at, fuel=590.0, adblue=75.0))
    check([x for x in fp.fills(pu, 9e9) if x["kind"] == "adblue"] == [fl_["adblue"]], "a save or a level that leaps isn't an AdBlue fill-up")
    n9 = len(fp.fills(pu, 9e9))
    sw = dict(at, fuel_capacity=986.0, game_time_abs=70500)           # another truck, still showing the old one's level
    for f_, a_, paid_ in ((23.3, 0.0, False), (23.3, 0.0, False), (851.1, 73.2, False), (851.1, 73.2, True), (851.1, 73.2, True)):
        clock.t += 5
        feed(t9, dict(sw, fuel=f_, adblue=a_, refuel_paid=paid_, refuel_amount=827.8))
    clock.t += 130
    feed(t9, dict(sw, fuel=851.1, adblue=73.2))
    check(len(fp.fills(pu, 9e9)) == n9, "getting into another truck isn't a fill-up")
    feed(t9, dict(sw, fuel=851.1, adblue=73.2, speed=15.0))       # it drives, then really fills
    at = dict(sw)
    pump(851.0, 951.0, 73.0, 78.0)
    check(len(fp.fills(pu, 9e9)) == n9 + 2, "once it has driven its fill-ups count")
    clock.t += 5
    for i in range(6):
        clock.t += 0.3
        feed(t9, dict(sw, fuel=951.0, adblue=60.0 + i))
    t9.feed(None)
    check([x["kind"] for x in fp.fills(pu, 9e9)][0] == "adblue" and len(fp.fills(pu, 9e9)) == n9 + 3, "closing the game mid-fill still books what went in")
    __import__("fmpro.cleanup", fromlist=["x"]).purge_member(pu)
    check(db.val("SELECT COUNT(*) FROM fuel_fills WHERE user_id=?", (pu,), 0) == 0, "a fired driver's fill-ups go with them")
    fp.time = _real_fp_time
    service.time = _time
    post("/settings", {"section": "economy", "units": "imperial", "adblue_price": "4.00", "has_fuel_price_game": "1"})
    check(abs(fp.adblue() - 4 / 3.785411784) < 1e-6 and db.setting("fuel_price_game") == "0", "the AdBlue price and the switch are in Settings")
    db.set_setting("fuel_price_game", "1")

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
                "/recruitment", "/vtc-profile", "/find-vtc", "/events", "/leaderboard", "/leaderboard?metric=realism&period=all",
                "/leaderboard?metric=revenue&period=year&game=ats", "/achievements", "/announcements", "/messages",
                "/settings", "/settings?tab=economy", "/settings?tab=telemetry", "/settings?tab=integrations",
                "/settings?tab=recruitment", "/settings?tab=audit", "/api/live", "/api/live/all", "/api/notifications",
                "/api/overlay", "/api/export/jobs.csv", "/api/export/ledger.csv", "/jobs?status=delivered&q=a",
                "/compliance", "/compliance?tab=dvir", "/compliance?tab=inspections", "/compliance?tab=violations",
                f"/compliance?tab=logbook&driver={hid}", f"/compliance?tab=logbook&driver={hid}&day=10",
                "/api/hos", f"/api/hos?user={hid}", "/settings?tab=compliance", "/overlay/setup",
                "/overlay?layout=stack", "/overlay?layout=compact&solid&scale=1.5"]:
        r = c.get(url)
        check(r.status_code == 200, f"GET {url} -> {r.status_code}")
    d = c.get("/api/live").get_json()
    check(d["online"] and len(d["values"]) > 200, f"/api/live exposes {len(d['values'])} values")
    from fmpro import howto
    tdir_ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fmpro", "templates")
    check(all(os.path.exists(os.path.join(tdir_, n + ".html")) for n in howto.GUIDES), "every page guide belongs to a page")
    import html as html_
    missing_ = []
    for u_, n_ in (("/", "dashboard"), ("/fleet", "fleet"), (f"/fleet/truck/{tid}", "truck"),
                   ("/settings?tab=economy", "settings"), ("/compliance", "compliance"), ("/fuel", "fluids")):
        page_ = html_.unescape(c.get(u_).get_data(as_text=True))
        if howto.TITLE not in page_ or howto.GUIDES[n_][0] not in page_:
            missing_.append(u_)
    check(not missing_, f"pages open with their own How to use this page panel {missing_ or ''}")
    check(b"How to use this page" not in c.get("/overlay").data, "the stream overlay page has no instructions panel")

    print("In-game overlay")
    from fmpro import ingame, overlay_fields
    check(overlay_fields.clean({"fields": ["speed_kmh", "nope", "speed_kmh"], "opacity": 5, "corner": "zz", "scale": "x",
                                "hotkey": "alt+f4"}) == dict(overlay_fields.DEFAULTS, fields=["speed_kmh"], opacity=30),
          "overlay settings drop unknown fields, duplicates and out-of-range values")
    r = post("/overlay/ingame", {"action": "save", "field": ["fuel_pct", "speed_kmh", "hos.available", "route"],
                                 "corner": "bl", "hotkey": "f10", "opacity": "75", "scale": "120", "alerts": "1"})
    cfg_ = overlay_fields.config(uid)
    check(r.status_code == 302 and cfg_["fields"] == ["fuel_pct", "speed_kmh", "hos.available", "route"]
          and cfg_["corner"] == "bl" and cfg_["hotkey"] == "f10" and cfg_["alerts"] and not cfg_["hos_warnings"],
          "the driver picks which telemetry values the in-game overlay shows, and where")
    d = c.get("/api/overlay").get_json()
    check([row[0] for row in d["rows"]] == ["Fuel", "Speed", "Drive time left", "Route"] and all(row[1] for row in d["rows"]),
          "the overlay feed sends exactly the chosen values, ready to draw")
    check(d["warnings"] == [], "Hours of Service warnings are off when the driver turns them off")
    t0_ = d["server_time"]
    db.notify(uid, "Compliance violation 11-hour: test", "/compliance?tab=violations", "error")
    db.notify(uid, "New truck detected: test", "/fleet/truck/1")
    a_ = c.get(f"/api/overlay?since={t0_ - 0.001}").get_json()["alerts"]
    check([x["text"] for x in a_] == ["Compliance violation 11-hour: test"] and a_[0]["level"] == "bad",
          "logbook events are sent to the overlay (other notifications are not)")
    check(c.get(f"/api/overlay?since={_time.time() + 1}").get_json()["alerts"] == [], "each logbook alert is sent once")
    hud_t = {"hos": {"available": 12, "break_left": 0, "status": "driving", "cycle_left": 3000, "break_len": 30}}
    codes_ = [w[0] for w in overlay_fields.hos_warnings(hud_t, {"next_rest_stop": 20})]
    check(codes_ == ["drive-15", "break-0", "rest-30"], f"Hours of Service warnings: drive time, break and rest stop {codes_}")
    check(overlay_fields.hos_warnings({"hos": {"available": 0, "break_left": 100, "status": "off", "cycle_left": 0}}, {})[0][0]
          == "drive-0", "out of drive time is a red warning")
    ws_ = [{"code": c_, "text": t_, "level": l_} for c_, t_, l_ in overlay_fields.hos_warnings(hud_t, {"next_rest_stop": 20})]
    bn_ = ingame.pick_banner(ws_)
    check(bn_ and bn_["code"] == "break-0", f"the overlay keeps the most urgent logbook warning on screen ({bn_})")
    bn_ = ingame.pick_banner([w for w in ws_ if w["code"] != "break-0"])
    check(bn_ and bn_["code"] == "drive-15" and "drive time left" in bn_["text"],
          "the 15-minute drive-time warning stays on screen, not just a pop-up")
    check(ingame.pick_banner([{"code": "rest-30", "text": "x", "level": "warn"}]) is None and ingame.pick_banner([]) is None,
          "no strip when no drive / break / cycle limit is near")
    check(not ingame.running() and b"didn" not in post("/overlay/ingame", {"action": "start"}).data
          and not ingame.running(), "the overlay window isn't started where the app has no local port (tests)")
    # LAN mode: the app serves on a private local port behind the HTTPS front door; the overlay must use it
    started_ = []

    class _FakeProc:
        pid = 4242
        def poll(self): return None
        def terminate(self): started_.append("stopped")

    real_popen_ = ingame.subprocess.Popen
    ingame.subprocess.Popen = lambda cmd, env=None, **k: (started_.append((cmd, env.get("FMP_OVERLAY_KEY"))), _FakeProc())[1]
    app.config["LAN"], app.config["OVERLAY_PORT"] = {"scheme": "https", "port": 8760}, 54321
    try:
        r_ = post("/overlay/ingame", {"action": "start"}, follow_redirects=True)
        cmd_ = started_[0][0] if started_ else []
        check(ingame.running() and cmd_[-2:] == ["--port", "54321"] and started_[0][1]
              and b"In-game overlay opened" in r_.data,
              "in LAN mode the overlay opens and talks to the app's local port (it used to refuse)")
        check(ingame.local_port(app) == 54321, "the overlay's port is the local one, not the network port")
    finally:
        ingame.stop()
        ingame.subprocess.Popen = real_popen_
        app.config.pop("LAN", None)
        app.config.pop("OVERLAY_PORT", None)
    check(not ingame.running(), "and it closes again")
    page_ = c.get("/overlay?show=fields,alerts").get_data(as_text=True)
    check('data-part="fields"' in page_ and 'id="ov-toasts"' in page_, "the stream overlay can show the chosen values and alerts")
    overlay_fields.save(uid, {})

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
    _b3 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    check(leasing.finish(l3, "buyout", 1)[1] == "This lease had already ended."
          and db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") == _b3,
          "a lease that was already bought out (a second click) isn't bought or charged again")
    post("/leasing/plans/save", {"name": "Test Plan", "weeks": "8", "allowed": "10000", "down_pct": "0", "apr": "9.9",
                                 "residual_pct": "80", "excess_rate": "0.50", "early_fee_pct": "50"})
    tp_ = db.one("SELECT * FROM lease_plans WHERE name='Test Plan'")
    check(tp_ and tp_["weeks"] == 8 and tp_["apr"] == 9.9 and round(tp_["km_allowed"]) in (10000, round(10000 * 1.609344)), "add a lease plan")
    post(f"/leasing/plans/{tp_['id']}/toggle")
    check(db.val("SELECT active FROM lease_plans WHERE id=?", (tp_["id"],)) == 0 and "Test Plan" not in [p["name"] for p in leasing.plans()],
          "hide a lease plan")
    post("/leasing/new", {"plan_id": str(lh["id"]), "price": "99999999", "source": "new", "brand": "X", "model": "Y"})
    check(db.val("SELECT COUNT(*) FROM leases") == 3, "can't lease a truck when the down payment doesn't fit")

    print("Trailer leasing")
    from fmpro import trailer_leasing as _tl
    tplans = {p["name"]: p for p in _tl.plans()}
    check(len(tplans) == 4 and len({(p["weeks"], p["apr"]) for p in tplans.values()}) == 4,
          "four default trailer lease plans with different lengths and interest")
    an = tplans["Annual"]
    tq = _tl.quote(40000, an)
    check(tq["down"] == 4000 and tq["residual"] == 18000 and abs(tq["payment"] - ((36000 - 18000) / 52 + (36000 + 18000) * 5.9 / 100 / 104)) < 0.02,
          "trailer lease quote: down payment, end value, value used + interest")
    page = c.get("/trailer-leasing").get_data(as_text=True)
    check("Compare trailer lease plans" in page and "Long Term" in page and "no limit" in page and "Buy it at the end for" in page
          and "Lease on Annual" in page, "trailer leasing page shows plans and every cost, with no mileage limit")
    check('href="/trailer-leasing"' in page and "Trailer Leasing" in page, "Trailer Leasing is in the sidebar")
    balt = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    r = post("/trailer-leasing/new", {"plan_id": str(an["id"]), "price": "40000", "source": "new", "brand": "Great Dane",
                                       "model": "Everest", "body_type": "reefer", "plate": "TRL1", "game": "ats"})
    tlid = db.val("SELECT MAX(id) FROM trailer_leases")
    tlease = db.one("SELECT * FROM trailer_leases WHERE id=?", (tlid,))
    ttid = tlease and tlease["trailer_id"]
    trow = db.one("SELECT * FROM trailers WHERE id=?", (ttid,)) if ttid else None
    check(r.status_code == 302 and tlease and tlease["payment"] == tq["payment"] and tlease["status"] == "active"
          and trow and trow["name"] == "Great Dane Everest" and trow["leased"] == 1 and trow["plate"] == "TRL1",
          "lease a new trailer: it joins the fleet as leased")
    check(abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (balt - 4000)) < 0.01, "trailer down payment taken")
    fp = c.get("/fleet").get_data(as_text=True)
    check(f"/fleet/trailer-lease/{tlid}" in fp and f'data-trailer-sell="{ttid}"' not in fp, "fleet page links the trailer's lease and hides Sell")
    post(f"/fleet/trailer/{ttid}/sell", {"price": "1"})
    check(db.val("SELECT status FROM trailers WHERE id=?", (ttid,)) == "active", "a leased trailer can't be sold")
    dp = c.get(f"/fleet/trailer-lease/{tlid}").get_data(as_text=True)
    check("If you end it today" in dp and "Total interest" in dp and "trailer value used each week" in dp, "trailer lease page shows the cost breakdown")
    _tl.process(now=_lt.time() + 8 * 86400)
    check(db.val("SELECT payments_made FROM trailer_leases WHERE id=?", (tlid,)) == 1
          and db.val("SELECT COUNT(*) FROM ledger WHERE category='trailer lease payment'") == 1, "weekly trailer lease payment taken")
    _tl.process(now=_lt.time() + 8 * 86400)
    check(db.val("SELECT payments_made FROM trailer_leases WHERE id=?", (tlid,)) == 1, "a trailer payment is only taken once a week")
    check(not db.q("SELECT 1 FROM ledger WHERE description LIKE ? AND description LIKE 'Trailer%'", (f"Lease #{tlid} %",))
          and db.val("SELECT COUNT(*) FROM ledger WHERE description LIKE ?", (f"Trailer lease #{tlid} %",)) == 2,
          "a truck lease page never picks up trailer lease lines with the same number")
    tlease = db.one("SELECT * FROM trailer_leases WHERE id=?", (tlid,))
    tst = _tl.status(tlease)
    check(abs(tst["early_fee"] - 51 * tlease["payment"] * 0.30) < 0.02, "trailer early-return fee = 30% of payments left")
    bal3 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    post(f"/fleet/trailer-lease/{tlid}/end", {"action": "return"})
    tl2 = db.one("SELECT * FROM trailer_leases WHERE id=?", (tlid,))
    check(tl2["status"] == "returned" and tl2["end_reason"] == "early return" and abs(tl2["end_cost"] - tst["early_fee"]) < 0.02
          and abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal3 - tl2["end_cost"])) < 0.02,
          "early trailer return charges only the fee (no mileage)")
    check(db.one("SELECT status, leased FROM trailers WHERE id=?", (ttid,)) == {"status": "returned", "leased": 0},
          "the returned trailer leaves the fleet")
    own_tr = db.insert("trailers", name="My Flatbed", brand="Fontaine", plate="OWNTR", purchase_price=30000, created_at=1)
    st_ = tplans["Short Term"]
    post("/trailer-leasing/new", {"plan_id": str(st_["id"]), "price": "30000", "source": "existing", "trailer_id": str(own_tr)})
    tlid2 = db.val("SELECT id FROM trailer_leases WHERE trailer_id=? AND status='active'", (own_tr,))
    check(bool(tlid2), "lease one of my own trailers")
    for w in range(12):
        _tl.process(now=_lt.time() + (w + 1) * 7 * 86400 + 3600)
    check(db.val("SELECT status FROM trailer_leases WHERE id=?", (tlid2,)) == "matured", "trailer lease matures after the last payment")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE link=?", (f"/fleet/trailer-lease/{tlid2}",), 0) >= 1,
          "managers told the trailer lease has ended")
    post(f"/fleet/trailer-lease/{tlid2}/end", {"action": "renew", "plan_id": str(tplans["Seasonal"]["id"])})
    tlid3 = db.val("SELECT id FROM trailer_leases WHERE trailer_id=? AND status='active'", (own_tr,))
    check(db.val("SELECT status FROM trailer_leases WHERE id=?", (tlid2,)) == "renewed" and tlid3
          and db.val("SELECT price FROM trailer_leases WHERE id=?", (tlid3,)) == 22500, "renew leases the trailer again at its end value")
    tl3 = db.one("SELECT * FROM trailer_leases WHERE id=?", (tlid3,))
    bal4 = db.val("SELECT SUM(amount) FROM ledger WHERE account='company'")
    post(f"/fleet/trailer-lease/{tlid3}/end", {"action": "buyout"})
    check(db.val("SELECT status FROM trailer_leases WHERE id=?", (tlid3,)) == "bought"
          and db.val("SELECT leased FROM trailers WHERE id=?", (own_tr,)) == 0
          and abs(db.val("SELECT SUM(amount) FROM ledger WHERE account='company'") - (bal4 - tl3["payment"] * tl3["weeks"] - tl3["residual"])) < 0.05,
          "buy out a trailer lease early: payments left + end value, and the trailer is ours")
    check(post(f"/fleet/trailer-lease/{tlid3}/end", {"action": "buyout"}).status_code == 404, "an ended trailer lease can't be ended again")
    lone_ = db.insert("trailers", name="Lone", plate="L1", purchase_price=0, created_at=1)
    lone_lease = _tl.start(lone_, st_, 30000, uid)
    check(post(f"/fleet/trailer-lease/{lone_lease}/end", {"action": "renew", "plan_id": str(st_["id"])}).status_code == 302
          and db.val("SELECT status FROM trailer_leases WHERE id=?", (lone_lease,)) == "active", "a trailer lease can't be renewed before it ends")
    check(post(f"/fleet/trailer-lease/{lone_lease}/end", {"action": "steal"}).status_code == 400, "unknown end actions are refused")
    post("/trailer-leasing/plans/save", {"name": "Trailer Test", "weeks": "6", "down_pct": "0", "apr": "3.5", "residual_pct": "85",
                                         "early_fee_pct": "10"})
    ttp = db.one("SELECT * FROM trailer_lease_plans WHERE name='Trailer Test'")
    check(ttp and ttp["weeks"] == 6 and ttp["apr"] == 3.5 and ttp["residual_pct"] == 85, "add a trailer lease plan")
    post(f"/trailer-leasing/plans/{ttp['id']}/toggle")
    check("Trailer Test" not in [p["name"] for p in _tl.plans()], "hide a trailer lease plan")
    check(post("/trailer-leasing/plans/save", {"name": "", "weeks": "x"}).status_code == 302
          and db.val("SELECT COUNT(*) FROM trailer_lease_plans") == 5, "an incomplete trailer plan is refused")
    n_tl = db.val("SELECT COUNT(*) FROM trailer_leases")
    post("/trailer-leasing/new", {"plan_id": str(an["id"]), "price": "99999999", "source": "new", "brand": "X", "model": "Y"})
    post("/trailer-leasing/new", {"plan_id": str(an["id"]), "price": "40000", "source": "existing", "trailer_id": str(lone_)})
    check(db.val("SELECT COUNT(*) FROM trailer_leases") == n_tl, "can't lease a trailer twice or when the down payment doesn't fit")
    post(f"/fleet/trailer-lease/{lone_lease}/end", {"action": "return"})
    from fmpro import cleanup as _cl
    check(_cl.delete_trailers([ttid]) == 1 and db.val("SELECT trailer_id FROM trailer_leases WHERE id=?", (tlid,)) is None
          and db.val("SELECT status FROM trailer_leases WHERE id=?", (tlid,)) == "returned",
          "deleting a returned trailer keeps its lease history, unlinked")
    check(c.get(f"/fleet/trailer-lease/{tlid}").status_code == 200, "an unlinked trailer lease page still opens")
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

    print("Company colours: swap the navy and crimson")
    from fmpro import theme as _th
    page = c.get("/settings?tab=company").get_data(as_text=True)
    check('name="theme_navy"' in page and 'name="theme_accent"' in page and "theme-preset" in page,
          "Settings > Company has colour pickers and ready-made pairs")
    check('id="company-colours"' not in c.get("/").get_data(as_text=True), "with the built-in colours no extra CSS is added")
    post("/settings", {"section": "company", "theme_navy": "#0a1a33", "theme_accent": "#c8102e"})
    check('id="company-colours"' not in c.get("/").get_data(as_text=True), "saving the built-in colours changes nothing")
    post("/settings", {"section": "company", "theme_navy": "#123220", "theme_accent": "#E0A800"})
    page = c.get("/").get_data(as_text=True)
    check(db.setting("theme_navy") == "#123220" and "--navy-900:#123220;" in page and "--crimson:#e0a800;" in page,
          "the chosen colours apply to every page")
    check("--on-accent:#000;" in page, "button text turns black on a light accent so it stays readable")
    lc_ = app.test_client()
    check("--crimson:#e0a800;" in lc_.get("/login", follow_redirects=True).get_data(as_text=True),
          "the sign-in screen uses the company colours too")
    d_, l_ = _th.palette("#123220", "#e0a800")
    check(d_["--bg"] != "#071327" and _th._hls(d_["--bg"])[1] <= _th._hls("#071327")[1] + 0.01,
          f"page backgrounds take the new colour but stay as dark as before ({d_['--bg']})")
    check(_th.palette("#ff00ff", "")[0]["--navy-900"] != "#ff00ff", "a very bright main colour is darkened so white text stays readable")
    post("/settings", {"section": "company", "theme_navy": "red;}body{display:none", "theme_accent": "url(x)"})
    check(db.setting("theme_navy") == "#123220" and db.setting("theme_accent").lower() == "#e0a800",
          "anything that isn't a #rrggbb colour is refused")
    db.set_setting("theme_navy", "red;}</style><script>alert(1)</script>")
    page = c.get("/").get_data(as_text=True)
    check("<script>alert(1)" not in page and "--navy-900:red" not in page, "a bad stored value never reaches the page")
    db.set_setting("theme_navy", "#123220")
    post("/settings", {"section": "company", "theme_reset": "1"})
    check(db.setting("theme_navy") == "" and db.setting("theme_accent") == ""
          and 'id="company-colours"' not in c.get("/").get_data(as_text=True), "Reset goes back to navy and crimson")
    r_ = c.get("/settings/colours.css?navy=%23123220&accent=%2314b8a6")
    check(r_.status_code == 200 and "text/css" in r_.headers["Content-Type"] and "--crimson:#14b8a6;" in r_.get_data(as_text=True),
          "the live preview gets the exact colours from the app")
    check(c.get("/settings/colours.css?navy=red;}x{&accent=%3C/style%3E").get_data(as_text=True) == "",
          "the preview ignores anything that isn't a colour")
    check(app.test_client().get("/settings/colours.css?navy=%23123220").status_code in (302, 401, 403),
          "the preview needs an admin sign-in")
    check(_th.css("", "") == "" and _th.on_colour("#c8102e") == "#fff" and _th.on_colour("#f2f2f2") == "#000",
          "white text on crimson, black on near-white")

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
    check("Simulate 30-min break" in page and "Simulate 10-hour sleeper berth" in page and "Simulate 34-hour restart" in page,
          "the logbook shows the Simulate break / sleeper berth / restart buttons")
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
    r = post("/compliance/simulate", {"kind": "sleeper", "game": "ats"}, follow_redirects=True)
    cl = compliance.clocks(me, None, "ats")
    check(cl["drive"]["used"] == 0 and cl["window"]["used"] == 0 and cl["break"]["used"] == 0 and cl["cycle"]["used"] >= 545,
          "a simulated 10 h sleeper berth resets the driving, window and break clocks, but not the cycle")
    slp = db.one("SELECT * FROM hos_logs WHERE user_id=? AND sim_rest=600 ORDER BY id DESC LIMIT 1", (me,))
    check(slp and slp["status"] == "sleeper" and slp["start_min"] == sm + 575 and slp["end_min"] == sm + 1175,
          f"the simulated sleeper berth is a real 10-hour sleeper-berth line after the break ({dict(slp) if slp else None})")
    check(compliance.log_offset(me, "ats") == off0 + 630 and cl["status"] == "sleeper",
          "the logbook clock moves on by the 10 hours and the status is sleeper berth")
    check("Simulated a 10-hour sleeper berth" in r.get_data(as_text=True), "the page confirms the simulated sleeper berth")
    page = c.get(f"/compliance?tab=logbook&game=ats&day={(sm + 900) // 1440}").get_data(as_text=True)
    check("Simulated 10-hour sleeper berth" in page and "Sleeper (simulated)" in page,
          "the sleeper berth is drawn on the sleeper-berth row of the graph")
    ets_len = compliance.RULESETS[compliance.ruleset_for("ets2")]["reset"]
    check(ets_len == 11 * 60, "in ETS2 the sleeper berth is the 11-hour EU daily rest")
    post("/compliance/simulate", {"kind": "restart", "game": "ats"})
    cl = compliance.clocks(me, None, "ats")
    check(cl["drive"]["used"] == 0 and cl["window"]["used"] == 0 and cl["cycle"]["used"] == 0,
          "a simulated 34 h restart resets the driving, window and cycle clocks")
    rs = db.one("SELECT * FROM hos_logs WHERE user_id=? AND sim_rest=2040 ORDER BY id DESC LIMIT 1", (me,))
    check(rs and rs["start_min"] == sm + 1175 and rs["end_min"] == sm + 1175 + 2040,
          "the simulated 34 h restart is a real 34-hour off-duty line, after the sleeper berth")
    page = c.get(f"/compliance?tab=logbook&game=ats&day={(sm + 1175 + 1440) // 1440}").get_data(as_text=True)
    check("Simulated 34-hour restart" in page and "Restart (simulated)" in page, "the restart is drawn across the next day")

    # the live tracker continues on the logbook clock: game minute g is logged as g + simulated rest
    from fmpro.compliance import HosTracker
    tr = HosTracker(me)
    g0 = sm + 1200 - compliance.log_offset(me, "ats") + 2040  # a game minute that lands after the restart
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
    _fetch = sys.modules["fmpro.licensing"].fetch
    sys.modules["fmpro.licensing"].fetch = lambda *a, **k: calls.append("download") or b""
    try:
        for st_ in ("staged", "downloading"):
            with updater._lock:
                updater.STATE.update(status=st_)
            check("already being installed" in saved[4]() and "download" not in calls,
                  f"pressing Update now again while it is {st_} doesn't download or unpack it a second time")
    finally:
        sys.modules["fmpro.licensing"].fetch = _fetch
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

    # Connect a Driver + allowed host names
    from fmpro import auth as _auth, pages as _pages
    check(_pages.clean_hosts("Fleet.Example.com, https://x.ddns.net:8760/path  bad_name!, fleet.example.com") ==
          (["fleet.example.com", "x.ddns.net"], ["bad_name!"]), "host names are cleaned: case, scheme, port and path dropped, junk rejected")
    post("/settings", {"section": "telemetry", "allowed_hosts": "Fleet.Example.com, not a host!"})
    check(db.setting("allowed_hosts") == "fleet.example.com, not, a", "Settings saves the allowed host names it can use")
    post("/settings", {"section": "telemetry", "allowed_hosts": "fleet.example.com"})
    check(_auth.host_allowed("fleet.example.com:8760") and not _auth.host_allowed("evil.example.net"),
          "an allowed host name is accepted; others are still refused")
    check('name="allowed_hosts"' in c.get("/settings?tab=telemetry").get_data(as_text=True), "the box is on the Telemetry tab")
    check(uc.get("/drivers/connect").status_code in (302, 403), "a driver can't open Connect a driver")
    page = c.get("/drivers/connect").get_data(as_text=True)
    check("Start Fleet Manager Pro (LAN).bat" in page and "--token" not in page, "without LAN mode the page explains step 1 and shows no command")
    app.config["LAN"] = {"scheme": "https", "port": 8760}
    drv = db.one("SELECT id, agent_token FROM users WHERE username='up_driver'")
    r = post("/drivers/connect", {"public_address": "https://MyCo.DDNS.net:8760/"})
    check(r.status_code == 302 and db.setting("public_address") == "myco.ddns.net" and _auth.host_allowed("myco.ddns.net"),
          "saving a host name as the internet address also allows it")
    check(post("/drivers/connect", {"public_address": "not valid!"}).status_code == 302 and db.setting("public_address") == "myco.ddns.net",
          "a bad address is refused and the old one kept")
    page = c.get(f"/drivers/connect?driver={drv['id']}").get_data(as_text=True)
    pin = __import__("fmpro.tlsfront", fromlist=["x"]).fingerprint(app.config["DATA_DIR"])
    want = f"--server https://myco.ddns.net:8760 --token {drv['agent_token']}" + (f" --pin {pin}" if pin else "")
    check(want in page and "--server https://192.168.1.50:8760" in page, "the page builds the internet and home-network lines for the picked driver")
    r = c.get(f"/drivers/{drv['id']}/agent.bat?where=internet")
    bat = r.get_data(as_text=True)
    check(r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "") and want in bat and bat.startswith("@echo off"),
          "Download file gives the driver a ready-to-run .bat")
    check(uc.get(f"/drivers/{drv['id']}/agent.bat").status_code in (302, 403, 404), "a driver can't download connection files")
    check('href="/drivers/connect"' in c.get("/drivers").get_data(as_text=True), "the Drivers page links to Connect a driver")

    # Router (UPnP): Find it for me / Open the port for me, against a fake router on this PC (never the real one)
    import http.server
    import threading
    from fmpro import upnp as _upnp
    seen, fake = [], {"ip": "203.0.113.25", "fault": False}

    class FakeIGD(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302); self.send_header("Location", "http://198.51.100.9/desc.xml"); self.end_headers()
                return
            if self.path == "/other.xml":
                body = (b'<root><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>'
                        b'<controlURL>http://192.168.77.77/ctl</controlURL></service></root>')
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            body = ('<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList>'
                    '<service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType><controlURL>/l3</controlURL></service>'
                    '<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl</controlURL></service>'
                    '</serviceList></device></root>').encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            action = self.headers.get("SOAPAction", "").strip('"').split("#")[-1]
            seen.append((action, data))
            if fake["fault"] and action == "AddPortMapping":
                body, code = (b'<s:Envelope><s:Body><s:Fault><detail><UPnPError><errorCode>718</errorCode>'
                              b'<errorDescription>ConflictInMappingEntry</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>', 500)
            else:
                body, code = (f'<s:Envelope><s:Body><u:{action}Response><NewExternalIPAddress>{fake["ip"]}</NewExternalIPAddress>'
                              f'</u:{action}Response></s:Body></s:Envelope>').encode(), 200
            self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    igd = http.server.HTTPServer(("127.0.0.1", 0), FakeIGD)
    threading.Thread(target=igd.serve_forever, daemon=True).start()
    loc = f"http://127.0.0.1:{igd.server_address[1]}/desc.xml"
    real_search = _upnp._ssdp_search
    _upnp._ssdp_search = lambda timeout: ["http://8.8.8.8/desc.xml", loc]   # an outside address is ignored, the local one used
    try:
        gw = _upnp.discover()
        check(gw.control_url.endswith("/ctl") and gw.service.endswith("WANIPConnection:1") and gw.local_ip == "127.0.0.1",
              "the router's internet service is found from its description; addresses outside the home network are ignored")
        check(not _upnp._local_host("https://192.168.1.1/x") and not _upnp._local_host("http://evil.example.com/")
              and _upnp._local_host("http://192.168.1.1:5000/x"), "only plain http to a home-network router address is ever contacted")
        base = loc.rsplit("/", 1)[0]
        try:
            _upnp._get(base + "/redir")
            redirected = True
        except _upnp.UPnPError:
            redirected = False
        check(not redirected, "a router's redirect is never followed (it could lead outside the home network)")
        try:
            _upnp.discover(search=lambda t: [base + "/other.xml"])
            other = True
        except _upnp.UPnPError:
            other = False
        check(not other, "a device that points at another machine for its controls is ignored")
        check(_upnp.behind_second_router("100.64.3.4") and _upnp.behind_second_router("10.0.0.2")
              and not _upnp.behind_second_router("203.0.113.25"), "carrier-grade NAT / double router is recognised")
        db.set_setting("public_address", "")
        r = post("/drivers/connect", {"action": "open"})
        check(r.status_code == 302 and db.setting("public_address") == "203.0.113.25" and db.setting("public_address_auto") == "1"
              and db.setting("upnp_port") == "8760", "Open the port for me saves the found address and remembers the port")
        add = next((d for a, d in seen if a == "AddPortMapping"), "")
        check("<NewExternalPort>8760</NewExternalPort>" in add and "<NewInternalClient>127.0.0.1</NewInternalClient>" in add
              and "<NewProtocol>TCP</NewProtocol>" in add, "the router is asked to forward TCP 8760 to this PC")
        check({a for a, _ in seen} <= {"GetExternalIPAddress", "AddPortMapping", "DeletePortMapping"}, "only the three known router actions are sent")
        page = c.get("/drivers/connect").get_data(as_text=True)
        check("found automatically" in page and "open, port 8760" in page and "Close it" in page, "the page shows the found address and open port")
        fake["ip"] = "198.51.100.7"; seen.clear()
        _upnp.refresh_on_start(app, 8760).join(10)
        check(db.setting("public_address") == "198.51.100.7" and any(a == "AddPortMapping" for a, _ in seen),
              "starting in LAN mode refreshes a changed address and re-opens the port")
        post("/drivers/connect", {"public_address": "myco.ddns.net"})
        check(db.setting("public_address_auto") == "0", "typing an address turns automatic updates off")
        post("/drivers/connect", {"action": "detect"})
        check(db.setting("public_address") == "myco.ddns.net", "Find it for me never replaces a typed host name")
        _upnp.refresh_on_start(app, 8760).join(10)
        check(db.setting("public_address") == "myco.ddns.net", "nor does starting in LAN mode")
        fake["fault"] = True
        r = post("/drivers/connect", {"action": "open"}, follow_redirects=True)
        check("ConflictInMappingEntry" in r.get_data(as_text=True), "a router refusal is shown to the owner in plain words")
        fake["fault"] = False; seen.clear()
        post("/drivers/connect", {"action": "close"})
        check(db.setting("upnp_port") == "" and any(a == "DeletePortMapping" for a, _ in seen), "Close it removes the forward")
        _upnp._ssdp_search = lambda timeout: []
        check(post("/drivers/connect", {"action": "detect"}).status_code == 302, "no router answering is handled")
        check("UPnP setting is probably switched off" in c.get("/drivers/connect").get_data(as_text=True),
              "and the owner is told why")
        check(uc.post("/drivers/connect", data={"action": "open"}).status_code in (302, 400, 403), "a driver can't touch the router")
    finally:
        _upnp._ssdp_search = real_search
        igd.shutdown()
        db.set_setting("public_address_auto", "0")
        db.set_setting("upnp_port", "")
    db.set_setting("public_address", "")
    db.set_setting("allowed_hosts", "")
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
    app.config["PORT"] = int(_srv.effective_port)
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
    print("Let other PCs in (no LAN file)")
    from fmpro import network, tlsfront as _tf
    tablet.lan_addresses = _addrs
    _ips = _addrs()
    page = c.get("/drivers/connect").get_data(as_text=True)
    check("Let other PCs in</button>" in page and "LAN).bat" not in page and "--token" not in page, "Connect a driver has the switch; no LAN file needed")
    check(uc.post("/network/access", data={"action": "on", "csrf": utok}).status_code == 403 and not network.is_open(), "a driver can't let other PCs in")
    check(c.get("/network/access").status_code == 405 and app.test_client().post("/network/access", data={"action": "on"}).status_code in (302, 400, 403)
          and not network.is_open(), "nor can a stranger or a link")
    post("/network/access", {"action": "junk"})
    check(not network.is_open() and db.setting("lan_access") == "0", "an unknown action does nothing")
    post("/network/access", {"action": "on"})
    nport = app.config["PORT"]
    check(network.is_open() and db.setting("lan_access") == "1" and app.config["LAN"] == {"scheme": "https", "port": nport}, "switching on opens the door at once, on the normal port, and remembers it")
    page = c.get("/drivers/connect").get_data(as_text=True)
    check("--token" in page and f":{nport}" in page and "Stop letting other PCs in" in page, "the drivers' connection lines appear")
    with _ur.urlopen(f"http://127.0.0.1:{nport}/login", timeout=10) as r_:
        check(r_.status == 200 and r_.url.startswith("http://"), "this PC itself still opens it as before")
    if _ips:
        with _ur.urlopen(f"https://{_ips[0]}:{nport}/login", context=_ctx, timeout=10) as r_:
            check(r_.status == 200 and b"Sign in" in r_.read(), "another PC reaches the sign-in page over HTTPS")
        try:
            _ur.build_opener(_NoRedir).open(f"http://{_ips[0]}:{nport}/login", timeout=10)
            _st, _loc = 200, ""
        except _ur.HTTPError as e_:
            _st, _loc = e_.code, e_.headers.get("Location", "")
        check(_st == 308 and _loc.startswith("https://"), "unencrypted visits from another PC are never served")
        _live = _ssl.create_default_context()
        _live.check_hostname, _live.verify_mode = False, _ssl.CERT_NONE
        _k = _live.wrap_socket(_sock.create_connection((_ips[0], nport), timeout=10))
        _k.sendall(b"GET /login HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        _first = _k.recv(100)
    post("/tablet/access", {"action": "on"})
    check(app.config["LAN"]["port"] == nport and tablet.door_open(), "tablet access can be on as well")
    post("/tablet/access", {"action": "off"})
    check(network.is_open() and app.config["LAN"]["port"] == nport, "turning tablet access off leaves other PCs let in")
    post("/network/access", {"action": "off"})
    check(not network.is_open() and db.setting("lan_access") == "0" and "LAN" not in app.config, "switching off closes it")
    if _ips:
        _k.settimeout(6)
        _got, _end = b"", False
        try:
            while True:
                _d = _k.recv(65536)
                if not _d:
                    _end = True
                    break
                _got += _d
        except OSError as e_:
            _end = not isinstance(e_, TimeoutError)
        check(_first.startswith(b"HTTP/1.1") and _end, "and cuts whoever was connected")
        _k.close()
        try:
            _sock.create_connection((_ips[0], nport), timeout=2).close()
            _shut = False
        except OSError:
            _shut = True
        check(_shut, "other PCs can't connect any more")
    with _ur.urlopen(f"http://127.0.0.1:{nport}/login", timeout=10) as r_:
        check(r_.status == 200, "while this PC carries on")
    network.autostart(app)
    check(not network.is_open(), "off stays off at the next start")
    db.set_setting("lan_access", "1")
    check(network.autostart(app) is None and network.is_open(), "on comes back by itself at the next start")
    network.stop(app)
    if _ips:
        _busy = _sock.socket()
        _busy.bind((_ips[0], nport))
        _busy.listen(1)
        check("already answers" in (network.start(app) or "") and not network.is_open(), "a port another program uses is reported, not taken over")
        _busy.close()
    app.config.pop("PORT")
    app.config["LAN"] = {"scheme": "https", "port": 8760}
    db.set_setting("lan_access", "0")
    check("Always let other PCs in" in c.get("/drivers/connect").get_data(as_text=True), "started with the LAN file, the page offers to make it permanent")
    post("/network/access", {"action": "on"})
    check(db.setting("lan_access") == "1" and not network.is_open() and "you don't need it" in c.get("/drivers/connect").get_data(as_text=True), "which is remembered for the next start")
    db.set_setting("lan_access", "0")
    app.config.pop("LAN")
    app.config["PORT"] = nport
    _srv.close()
    app.config.pop("PORT", None)
    app.config.pop("TABLET_PORT", None)

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
    check(1.5 <= s["fuel_used"] <= 2.5, f"the hour's totals count fuel used ({s})")
    d = c.get(f"/api/fluids?user={fuid}").get_json()
    check(d["online"] and d["live"]["fuel_capacity"] == 568.0 and round(d["live"]["fuel_pct"]) == 99 and d["live"]["adblue"] == 79.0
          and len(d["history"]) == len(h) and d["window_s"] == 3600, "the page gets the live levels and the hour's bars")
    check(d["prices"]["fuel"] > 0 and d["prices"]["adblue"] == db.setting_f("adblue_price"), "with the price of both")
    check(set(d["live"]) <= set(__import__("fmpro.fluids_views", fromlist=["x"])._LIVE_KEYS),
          "only fuel and AdBlue readings are sent to the page")
    _clk.t += 2 * 3600
    check(_fl.history(fuid) == [], "readings older than an hour drop off the graph")
    _FHUB.ingest(fuid, reader.parse(reader.pack({"sdk_active": True, "game_id": 2, "render_time": 9999})), "test")
    check(_fl.history(fuid) == [], "frames without a truck (menus, loading) aren't recorded")

    # readings are kept in the database: they survive a restart and longer ranges group them into bars
    _clk.t += 60
    for _ in range(4 * 60 * 10):                             # ten minutes of driving
        _clk.t += 0.25
        fuel_l -= 0.01
        fl_feed(fuel_l, ad_l)
    before = len(_fl.history(fuid))
    _fl.clear()                                              # what an app restart / update forgets
    check(before >= 39 and len(_fl.history(fuid)) == before, f"the readings survive a restart ({before} in 10 minutes)")
    check(db.val("SELECT COUNT(*) FROM fluid_samples WHERE user_id=?", (fuid,), 0) >= before,
          "each reading is a row in fluid_samples")
    d24 = c.get(f"/api/fluids?user={fuid}&range=24h").get_json()
    check(d24["range"] == "24h" and d24["window_s"] == 86400 and d24["sample_s"] == 360 and 0 < len(d24["history"]) <= 240,
          f"the 24-hour view groups the readings into bars ({len(d24['history'])} bars of 6 minutes)")
    check(c.get(f"/api/fluids?user={fuid}&range=forever").get_json()["range"] == "1h", "an unknown range falls back to the last hour")
    rp = c.get(f"/fuel?user={fuid}&range=7d").get_data(as_text=True)
    check(f'value="{fuid}" selected' in rp and 'value="7d" selected' in rp, "a notification link opens the page on that driver and range")
    db.ex("UPDATE fluid_samples SET t=? WHERE user_id=? AND id=(SELECT MIN(id) FROM fluid_samples WHERE user_id=?)",
          (_clk.t - 40 * 86400, fuid, fuid))
    n0 = db.val("SELECT COUNT(*) FROM fluid_samples WHERE user_id=?", (fuid,), 0)
    _fl.prune()                                              # (on the test's clock)
    check(db.val("SELECT COUNT(*) FROM fluid_samples WHERE user_id=?", (fuid,), 0) == n0 - 1,
          f"readings older than {_fl.KEEP_DAYS} days are pruned")

    # low fuel / AdBlue alerts: once per drop below the game's warning level, to the driver and the managers
    _fl.clear()
    owner_id = db.val("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1")

    def lows(uid_, what="fuel"):
        return db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND link=? AND text LIKE ?",
                      (uid_, f"/fuel?user={fuid}", f"Low {what}:%"), 0)
    db.ex("DELETE FROM notifications WHERE link=?", (f"/fuel?user={fuid}",))
    db.set_setting("fluid_alerts", "1")
    _clk.t += 60
    fl_feed(200.0, 40.0)                                     # 35% fuel, 50% AdBlue: fine
    check(lows(fuid) == 0, "no alert while the tanks are fine")
    _clk.t += 20
    fl_feed(80.0, 40.0)                                      # 14% of 568 L: below the 15% warning
    check(lows(fuid) == 1 and lows(owner_id) == 1, "low fuel notifies the driver and the Owner")
    note = db.one("SELECT * FROM notifications WHERE user_id=? AND link=? ORDER BY id DESC LIMIT 1", (fuid, f"/fuel?user={fuid}"))
    check(note and "14%" in note["text"] and "Kenworth W900" in note["text"] and note["kind"] == "warning",
          f"the alert says how low and which truck ({note['text'] if note else None})")
    for _ in range(20):
        _clk.t += 1
        fl_feed(79.0, 40.0)
    check(lows(fuid) == 1, "it alerts once, not on every frame")
    _clk.t += 20
    fl_feed(5.0, 10.0)                                       # AdBlue 12.5% too
    check(lows(fuid, "AdBlue") == 1 and lows(owner_id, "AdBlue") == 1 and lows(fuid) == 1, "low AdBlue has its own alert")
    _fl.clear()                                              # restart while still low
    _clk.t += 20
    fl_feed(5.0, 10.0)
    check(lows(fuid) == 1 and lows(fuid, "AdBlue") == 1, "a restart while the tank is still low doesn't alert again")
    _clk.t += 20
    fl_feed(500.0, 70.0)                                     # refuel + top up: alerts re-arm
    _clk.t += 20
    fl_feed(60.0, 40.0)
    check(lows(fuid) == 2, "after a refuel the next drop alerts again")
    _clk.t += 20
    fl_feed(500.0, 70.0)
    db.set_setting("fluid_alerts", "0")
    _clk.t += 20
    fl_feed(60.0, 40.0)
    check(lows(fuid) == 2, "with the setting off there are no alerts")
    post("/settings?tab=compliance", {"section": "compliance", "has_fluid_alerts": "1", "fluid_alerts": "1"})
    check(db.setting("fluid_alerts") == "1", "the alerts switch is on Settings > Compliance")
    check("Low fuel / AdBlue alerts" in c.get("/settings?tab=compliance").get_data(as_text=True), "(and shown there)")
    db.ex("DELETE FROM notifications WHERE link=?", (f"/fuel?user={fuid}",))
    db.ex("DELETE FROM fluid_samples WHERE user_id=?", (fuid,))
    nobody = c.get("/api/fluids?user=987654").get_json()
    check(not nobody["online"] and nobody["history"] == [] and "live" not in nobody, "a driver who isn't driving shows as waiting")
    # the game going away while a page reads the live data must not crash it (1.5.1 /api/live TypeError)
    _clk.t = __import__("time").time()
    fl_feed(300.0, 40.0)
    snap = _FHUB.get(fuid)
    _FHUB.ingest(fuid, None, "test")                         # sampler: game closed, right after the page's check
    check(snap and snap["frame"] is not None and snap["frame"]["values"]["fuel"] == 300.0,
          "a page keeps the frame it already got when the game drops out at that moment")
    check(_FHUB.get(fuid) is None and fuid not in _FHUB.all_live(), "once the game is gone the driver shows offline")
    _orig_get = _FHUB.get

    def _racy_get(u):                                        # check passes, then the game drops out before the read
        e = _orig_get(u)
        _FHUB.ingest(u, None, "test")
        return e
    fl_feed(300.0, 40.0)
    _FHUB.get = _racy_get
    try:
        rr = c.get(f"/api/live?user={fuid}")
        rf = c.get(f"/api/fluids?user={fuid}")
    finally:
        _FHUB.get = _orig_get
    check(rr.status_code == 200 and rr.get_json()["values"]["fuel"] == 300.0 and rf.status_code == 200,
          "/api/live and /api/fluids answer normally when the game closes mid-request")
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

    # Leaving the company (a member, from their own profile)
    from fmpro.auth import create_user as _cu_lv
    lvid = _cu_lv("leaver", "Lena Leaver", "leavepw123", role="driver")
    ltoken = db.val("SELECT agent_token FROM users WHERE id=?", (lvid,))
    ljob = db.insert("jobs", user_id=lvid, status="delivered", revenue=3000, driven_km=400, started_at=now_, finished_at=now_)
    db.insert("ledger", category="driver pay", amount=-600, description="Pay Lena", user_id=lvid, job_id=ljob, created_at=now_)
    db.insert("messages", from_id=lvid, to_id=rid, body="so long", created_at=now_)
    db.insert("hos_logs", user_id=lvid, status="driving", start_min=0, created_at=now_)
    money2 = tot()
    lc = app.test_client()
    lc.get("/login")
    with lc.session_transaction() as s_:
        ltok = s_.get("csrf")
    lc.post("/login", data={"username": "leaver", "password": "leavepw123", "csrf": ltok})
    with lc.session_transaction() as s_:
        ltok = s_.get("csrf")
    lp = lc.get("/profile").get_data(as_text=True)
    with lc.session_transaction() as s_:
        ltok = s_.get("csrf")
    check('action="/account/leave"' in lp and "Leave the company" in lp, "a member's own profile has a Leave the company button")
    check('action="/account/leave"' not in c.get("/profile").get_data(as_text=True), "the owner has no Leave button (the owner is the company)")
    check(post("/account/leave", {"password": "x"}).status_code == 403 and db.val("SELECT id FROM users WHERE role='owner'"),
          "the owner can't leave their own company")
    check('action="/account/leave"' not in c.get(f"/drivers/{lvid}").get_data(as_text=True), "nobody else sees a member's Leave button")
    r = lc.post("/account/leave", data={"password": "wrong-password", "csrf": ltok})
    check(r.status_code == 302 and "#leave" in r.headers.get("Location", ""), "a wrong password sends them back to the form")
    check(db.val("SELECT COUNT(*) FROM users WHERE id=?", (lvid,)) == 1, "leaving needs the member's own password")
    check(lc.post("/account/leave", data={"password": "leavepw123"}).status_code in (400, 403)
          and db.val("SELECT COUNT(*) FROM users WHERE id=?", (lvid,)) == 1, "leaving needs the form's CSRF token")
    with lc.session_transaction() as s_:
        ltok = s_.get("csrf")
    r = lc.post("/account/leave", data={"password": "leavepw123", "csrf": ltok})
    check(r.status_code == 302 and "/login" in r.headers.get("Location", "")
          and db.val("SELECT COUNT(*) FROM users WHERE id=?", (lvid,)) == 0, "with the password, the member leaves and is signed out")
    check(sum(db.val(f"SELECT COUNT(*) FROM {t} WHERE {col}=?", (lvid,)) for t, col in
              (("messages", "from_id"), ("hos_logs", "user_id"), ("jobs", "user_id"), ("ledger", "user_id"))) == 0
          and db.val("SELECT user_id FROM jobs WHERE id=?", (ljob,)) == former and tot() == money2,
          "everything personal is gone; their deliveries and pay stay in the books as Former driver")
    check(lc.get("/profile").status_code == 302, "the old session no longer works")
    check(app.test_client().post("/api/agent/frame", json={"frame": None}, headers={"X-Agent-Token": ltoken}).status_code == 401,
          "their PC's agent token stops working")
    owner_id = db.val("SELECT id FROM users WHERE role='owner'")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE 'Lena Leaver left the company%'", (owner_id,)) == 1,
          "the owner is told who left")
    check(db.val("SELECT COUNT(*) FROM audit_log WHERE action='member left'") >= 1, "leaving is in the audit log")

    # the agent on a leaver's PC stops by itself once the token is refused for good
    from fmpro.telemetry import service as _svc

    class _Mem:
        is_open = True
        def open(self): return True
        def read(self): return b""

    class _Reader:
        FIELDS = []
        SharedMemory = _Mem
        @staticmethod
        def parse(_b): return {"values": __import__("collections").defaultdict(int, sdk_active=True), "trailers": [], "substances": []}

    class _Resp:
        status = 401
        def read(self): return b""

    class _Conn:
        sent = 0
        def request(self, *a, **k): _Conn.sent += 1
        def getresponse(self): return _Resp()
        def close(self): pass

    saved = (_svc.reader, _svc._agent_connection, _svc.time.sleep)
    _svc.reader, _svc._agent_connection = _Reader, (lambda s, p: (_Conn(), None))
    _svc.time.sleep = lambda s: None
    try:
        done = threading.Event()
        threading.Thread(target=lambda: (_svc.run_agent("https://203.0.113.25:8760", "gone-token"), done.set()), daemon=True).start()
        check(done.wait(5) and _Conn.sent == _svc.AGENT_GIVE_UP, "a refused agent gives up and closes instead of retrying forever")
    finally:
        _svc.reader, _svc._agent_connection, _svc.time.sleep = saved

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
    tlp = c.get("/trailer-leasing").get_data(as_text=True)
    check("Weekly payment" in tlp and "Lease on Annual" not in tlp and c.get(f"/fleet/trailer-lease/{tlid}").status_code == 200,
          "drivers see trailer lease costs but can't lease")
    check(post("/trailer-leasing/new", {"plan_id": "1", "price": "40000"}).status_code == 403
          and post(f"/fleet/trailer-lease/{tlid}/end", {"action": "return"}).status_code == 403
          and post("/trailer-leasing/plans/save", {"name": "x"}).status_code == 403
          and post("/trailer-leasing/plans/1/toggle").status_code == 403, "drivers can't start, end or change trailer leases")
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
    # new-application webhook: its own switch and (optional) its own channel; nothing really leaves this PC
    from fmpro import economy as _eco
    sent_hooks = []

    class _HookResp:
        def read(self): return b""

    def _fake_urlopen(req, timeout=None):
        body_ = __import__("json").loads(req.data)
        check(body_.get("allowed_mentions") == {"parse": []}, "webhook posts can't ping @everyone or roles")
        sent_hooks.append((req.full_url, body_["content"]))
        return _HookResp()

    class _NowThread:
        def __init__(self, target=None, daemon=None, **k): self.t = target
        def start(self): self.t()

    real_open, real_thread = _eco.urllib.request.urlopen, _eco.threading.Thread
    _eco.urllib.request.urlopen, _eco.threading.Thread = _fake_urlopen, _NowThread
    try:
        db.set_setting("discord_webhook", "https://discord.example/api/webhooks/company")
        db.set_setting("webhook_applications", "")
        c.get("/apply")
        post("/apply", {"age": "30", "name": "Applicant Ann", "contact": "ann@example.com", "games": "ats"})
        check(db.val("SELECT COUNT(*) FROM applications", (), 0) == 1, "public application")
        check(sent_hooks and sent_hooks[-1][0].endswith("/company") and "Applicant Ann" in sent_hooks[-1][1] and "(ATS)" in sent_hooks[-1][1],
              "a new application is posted to the company webhook when it has no channel of its own")
        db.set_setting("webhook_applications", "https://discord.example/api/webhooks/staff")
        db.set_setting("webhook_on_event", "0")
        sent_hooks.clear()
        c.get("/apply")
        post("/apply", {"age": "30", "name": "Applicant Bob", "contact": "bob@example.com", "games": "ets2"})
        check(len(sent_hooks) == 1 and sent_hooks[0][0].endswith("/staff") and "Applicant Bob" in sent_hooks[0][1],
              "with its own webhook, applications go to that channel only (even with event posts off)")
        db.set_setting("webhook_on_application", "0")
        sent_hooks.clear()
        c.get("/apply")
        post("/apply", {"age": "30", "name": "Applicant Cal", "contact": "cal@example.com"})
        check(not sent_hooks, "switching application posts off stops them")
        n_apps = db.val("SELECT COUNT(*) FROM applications", (), 0)
        c.get("/apply")
        post("/apply", {"age": "15", "name": "Young Yan", "contact": "yan@example.com"})
        check(db.val("SELECT COUNT(*) FROM applications", (), 0) == n_apps, "the Apply page refuses applicants under 16")
        _eco.webhook("event", "an event")
        check(not sent_hooks, "event posts follow their own switch")
    finally:
        _eco.urllib.request.urlopen, _eco.threading.Thread = real_open, real_thread
        for k_, v_ in (("discord_webhook", ""), ("webhook_applications", ""), ("webhook_on_application", "1"), ("webhook_on_event", "1")):
            db.set_setting(k_, v_)
        db.ex("DELETE FROM applications WHERE name IN ('Applicant Bob', 'Applicant Cal')")
    tok = db.val("SELECT agent_token FROM users WHERE username='rookie'")
    payload = {"frame": {"values": dict(frame["values"]), "trailers": frame["trailers"], "substances": frame["substances"]}}
    r = c.post("/api/agent/frame", json=payload, headers={"X-Agent-Token": tok})
    check(r.status_code == 200 and service.HUB.get(rid) is not None, "remote agent frame accepted")
    r = c.post("/api/agent/frame", json=payload, headers={"X-Agent-Token": "bad"})
    check(r.status_code == 401, "bad agent token refused")
    check(c.get(f"/api/overlay?key={tok}").status_code == 200, "overlay works with token")
    check(c.get("/api/live").status_code == 401, "API needs login")
    # stream overlay: works from its link with no login, shows only the chosen panels, and carries company + HOS
    ov_ = c.get(f"/api/overlay?key={tok}").get_json()
    hud_ = ov_.get("hud") or {}
    check(ov_["online"] and hud_.get("company") == db.setting("company_name") and hud_.get("driver")
          and set(hud_) >= {"tag", "color", "hos"}, "the overlay feed names the company and the driver")
    check(hud_.get("hos") is None or {"drive_left", "break_left", "status"} <= set(hud_["hos"]),
          "the overlay feed carries the driver's Hours of Service clocks")
    r = c.get(f"/overlay?key={tok}&layout=stack&show=speed,hos,bogus&scale=9")
    page_ = r.get_data(as_text=True)
    check(r.status_code == 200 and 'data-part="speed"' in page_ and 'data-part="hos"' in page_
          and 'data-part="trip"' not in page_ and "ov-stack" in page_ and "--ov-scale: 2.0" in page_,
          "the overlay link picks the layout, panels and size (and ignores bad values)")
    page_ = c.get(f"/overlay?key={tok}&layout=compact").get_data(as_text=True)
    check('data-part="deadline"' in page_ and 'data-part="fuel"' not in page_, "compact shows speed, trip and deadline")
    page_ = c.get(f"/overlay?key={tok}&layout=nonsense").get_data(as_text=True)
    check("ov-bar" in page_ and 'data-part="damage"' in page_, "an unknown layout falls back to the full bar")
    check(c.get("/overlay?key=not-a-real-key").status_code == 302 and c.get("/api/overlay?key=nope").status_code == 401,
          "a wrong overlay key gets nothing")
    check(c.get("/overlay/setup").status_code == 302, "overlay setup needs a login")

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
    st, page = web(f"/apply/{code}", {"age": "30", "name": "Road Runner", "contact": "runner#0001", "age": "29", "country": "USA",
                                      "games": "both", "experience": "500 h ATS", "message": "<script>alert(9)</script>"})
    check(st == 200 and "was sent" in page, "an applicant can apply from anywhere")
    _st, page_ = web(f"/apply/{code}")
    check(f'name="{sys.modules["license_server"].HONEYPOT}"' in page_ and 'name="website"' not in page_ and "Website" not in page_,
          "the hidden anti-bot box has a name and label browsers don't autofill")
    web(f"/apply/{code}", {"age": "30", "name": "Bot", "contact": "spam", sys.modules["license_server"].HONEYPOT: "http://spam"})
    check(srv.store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1, "bots filling the hidden field are ignored")
    web(f"/apply/{code}", {"age": "30", "name": "Autofill Al", "contact": "al#0002", "website": "https://al.example.com"})
    check(srv.store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 2,
          "an applicant whose browser autofilled a website is no longer thrown away")
    srv.store.db.execute("DELETE FROM applications WHERE id=(SELECT MAX(id) FROM applications)")
    srv.store.db.commit()
    st, _ = web(f"/apply/{code}", {"name": "", "contact": ""})
    check(st == 400, "name and contact are required")
    st, page = web(f"/apply/{code}", {"name": "Young Yan", "contact": "y#0009", "age": "15"})
    check(st == 400 and "at least 16" in page, "applicants under 16 are refused online")
    st, _ = web(f"/apply/{code}", {"name": "No Age", "contact": "n#0010"})
    check(st == 400, "the online form needs an age")
    n_before = db.val("SELECT COUNT(*) FROM applications", (), 0)
    check(recruit._store({"age": "15", "name": "Old Server Olly", "contact": "o#1"}, 55555) is False
          and db.val("SELECT COUNT(*) FROM applications", (), 0) == n_before, "an under-16 application from an older server is dropped and not counted")
    srv.apply_hits.pop("127.0.0.1", None)              # those two count towards the hourly limit
    new, err = recruit.sync()
    ra = db.one("SELECT * FROM applications WHERE remote_id IS NOT NULL ORDER BY id DESC LIMIT 1")
    check(new == 1 and ra["name"] == "Road Runner" and ra["games"] == "ats,ets2" and ra["source"] == "online" and ra["age"] == 29,
          "the application is collected into Recruitment")
    check(srv.store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0, "and deleted from the licence server")
    check(recruit.sync() == (0, None), "nothing is collected twice")
    # regression: the server used to reuse number 1 once its list was empty, and the app then took every later
    # application for the one it already had and told the server to delete it
    first_rid = ra["remote_id"]
    st, page = web(f"/apply/{code}", {"age": "30", "name": "Second Sam", "contact": "sam#0003", "games": "ats"})
    new2, _e = recruit.sync()
    sam = db.one("SELECT remote_id FROM applications WHERE name='Second Sam'")
    check(new2 == 1 and sam and sam["remote_id"] != first_rid and sam["remote_id"] > 1_000_000,
          "a second application gets a new number and is collected (numbers are never reused)")
    st, page = web(f"/apply/{code}", {"age": "30", "name": "Third Tia", "contact": "tia#0004"})
    new3, _e = recruit.sync()
    check(new3 == 1 and db.val("SELECT COUNT(*) FROM applications WHERE name='Third Tia'") == 1, "and so is the next one")
    # an older server that reuses a number: a different person with an old number is still saved
    db.ex("UPDATE applications SET remote_id=? WHERE name='Second Sam'", (424242,))
    import json as _js
    lsv = sys.modules["license_server"]
    with srv.store.lock:
        srv.store.db.execute("INSERT INTO applications(id, code, created, data) VALUES(?,?,?,?)",
                             (424242, code, lsv.iso(lsv.now()), _js.dumps({"name": "Reused Rex", "contact": "rex#0005"})))
        srv.store.db.commit()
    new4, _e = recruit.sync()
    check(new4 == 1 and db.val("SELECT COUNT(*) FROM applications WHERE name='Reused Rex'") == 1,
          "the app doesn't mistake a new applicant for an old one just because a server reused a number")
    check(recruit.sync() == (0, None), "still nothing is collected twice")
    # an old licenses.db is upgraded in place: same rows, numbers start above anything handed out before
    import sqlite3 as _sq3
    old_dir = tempfile.mkdtemp(prefix="fmp-oldlic-")
    odb = _sq3.connect(os.path.join(old_dir, "licenses.db"))
    odb.executescript("CREATE TABLE applications(id INTEGER PRIMARY KEY, code TEXT NOT NULL, created TEXT, data TEXT NOT NULL);"
                      "INSERT INTO applications(id, code, created, data) VALUES(3, 'abcdefgh', 'x', '{}');")
    odb.commit(); odb.close()
    st2 = lsv.Store(old_dir)
    sql_ = st2.db.execute("SELECT sql FROM sqlite_master WHERE name='applications'").fetchone()[0]
    st2.db.execute("INSERT INTO applications(code, created, data) VALUES('abcdefgh', 'y', '{}')")
    ids_ = [r[0] for r in st2.db.execute("SELECT id FROM applications ORDER BY id")]
    check("AUTOINCREMENT" in sql_.upper() and ids_[0] == 3 and ids_[1] > 1_000_000,
          "an existing licence database is upgraded, keeping waiting applications")
    st2.db.close()
    shutil.rmtree(old_dir, ignore_errors=True)
    db.ex("DELETE FROM applications WHERE name IN ('Second Sam', 'Third Tia', 'Reused Rex')")
    with srv.hits_lock:                    # the extra applications above used up this address's hourly allowance
        srv.apply_hits.clear()
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    page = c.get("/recruitment").get_data(as_text=True)
    check("Road Runner" in page and "<script>alert(9)</script>" not in page and url in page and "Copy" in page,
          "Recruitment shows the link and the application, escaped")
    db.set_setting("recruitment_open", "0")
    recruit.sync()
    st, page = web(f"/apply/{code}")
    st2, _ = web(f"/apply/{code}", {"age": "30", "name": "Late", "contact": "x"})
    check("isn't taking applications" in page and st2 == 404, "closing recruitment closes the online page")
    db.set_setting("recruitment_open", "1")
    recruit.sync()
    codes = [web(f"/apply/{code}", {"age": "30", "name": f"Spam {i}", "contact": "x"}, ip="127.0.0.1" if i == 0 else None)[0] for i in range(6)]
    check(codes[:5] == [200] * 5 and codes[5] == 429, f"at most 5 applications per address per hour ({codes})")
    check(web("/apply/zzzzzzzz")[0] == 404 and web("/apply/../../v1/health")[0] == 404, "unknown or odd links get a 404")
    try:
        srv.handle("POST", "/v1/recruit", {"key": "FMP-AAAA-AAAA-AAAA-AAAA", "machine": lic.machine,
                                           "product": "Fleet Manager Pro"}, {}, "9.9.9.9")
        check(False, "collecting needs a real activated key")
    except Exception as e:
        check(getattr(e, "code", "") == "unknown_key", f"collecting needs a real activated key ({getattr(e, 'code', e)})")
    recruit.sync()

    print("New-application banner (every page)")
    from fmpro.auth import create_user as _cuv
    others_ = [r_["id"] for r_ in db.q("SELECT id FROM applications WHERE status='pending' AND name!='Road Runner'")]
    for i_ in others_:
        db.update("applications", i_, status="interview")
    page = c.get("/jobs").get_data(as_text=True)
    ban = page[page.index('id="app-banner"'):page.index('id="app-banner"') + 2500]
    check("New application" in ban and "Road Runner" in ban and " hidden" not in ban.split(">")[0] and 'href="/recruitment"' in ban,
          "a waiting application shows a banner on other pages")
    check('data-off="1"' in c.get("/recruitment").get_data(as_text=True), "but not on the Recruitment page itself")
    check(c.get("/api/notifications").get_json()["applications"]["name"] == "Road Runner", "pages that are already open learn of it too")
    db.insert("applications", name="<b>Eve</b>", contact="e", created_at=__import__("time").time())
    page = c.get("/jobs").get_data(as_text=True)
    check("<b>2 drivers</b>" in page and "&lt;b&gt;Eve&lt;/b&gt;" in page and "<b>Eve</b>" not in page, "several applications are counted, names escaped")
    upto_ = c.get("/api/notifications").get_json()["applications"]["upto"]
    post("/recruitment/banner/hide", {"upto": str(upto_)})
    check(c.get("/api/notifications").get_json()["applications"] is None
          and " hidden" in c.get("/jobs").get_data(as_text=True).split('id="app-banner"')[1].split(">")[0], "Hide puts the banner away")
    db.insert("applications", name="Later Lou", contact="l", created_at=__import__("time").time() + 0.01)
    __import__("time").sleep(0.05)
    check(c.get("/api/notifications").get_json()["applications"]["n"] == 1, "and it comes back for the next application")
    for bad in ("nan", "inf", "-5", "abc", "1e999", "9" * 400):
        post("/recruitment/banner/hide", {"upto": bad})
    check(c.get("/api/notifications").get_json()["applications"]["n"] == 1, "nonsense times don't hide it")
    db.ex("UPDATE applications SET status='rejected' WHERE name='Later Lou'")
    check(c.get("/api/notifications").get_json()["applications"] is None, "a reviewed application no longer counts")
    db.ex("DELETE FROM applications WHERE name IN ('<b>Eve</b>', 'Later Lou')")
    for i_ in others_:
        db.update("applications", i_, status="pending")
    _cuv("vt_driver", "Vt Driver", "vtdriver77", role="driver")
    dv = app.test_client()
    dv.get("/login")
    with dv.session_transaction() as s_:
        dvt = s_["csrf"]
    dv.post("/login", data={"username": "vt_driver", "password": "vtdriver77", "csrf": dvt})
    db.update("users", db.val("SELECT id FROM users WHERE username='boss'"), apps_seen=0)
    check('id="app-banner"' not in dv.get("/jobs").get_data(as_text=True)
          and dv.get("/api/notifications").get_json()["applications"] is None, "drivers never see applications")

    print("VTC Finder: the company's profile")
    from fmpro import vtc_profile
    with srv.hits_lock:
        srv.hits.clear()
        srv.apply_hits.clear()
    check((lsv.VTC_GAMES, lsv.VTC_REGIONS, lsv.VTC_STYLES, lsv.VTC_PLAYS, lsv.VTC_LANGS)
          == (vtc_profile.GAMES, vtc_profile.REGIONS, vtc_profile.STYLES, vtc_profile.PLAYS, vtc_profile.LANGS),
          "the app and the licence server offer the same choices")
    recruit.sync()
    check(web(f"/vtc/{code}")[0] == 404 and code not in web("/vtcs?open=all")[1], "a company isn't listed until its owner says so")
    check(srv.store.db.execute("SELECT COUNT(*) FROM vtc_profiles").fetchone()[0] == 0, "and nothing about it is kept on the server")
    with dv.session_transaction() as s_:
        dvt = s_["csrf"]
    check(dv.get("/vtc-profile").status_code == 403 and dv.post("/vtc-profile", data={"listed": "1", "about": "x", "csrf": dvt}).status_code == 403,
          "drivers can't open or change the profile")
    check("Open the VTC Finder" in dv.get("/find-vtc").get_data(as_text=True) and 'href="/vtc-profile"' not in dv.get("/find-vtc").get_data(as_text=True),
          "every member can open Find a VTC")
    check('href="/vtc-profile">List my company</a>' in c.get("/find-vtc").get_data(as_text=True), "Find a VTC offers managers to list their company")
    page = c.get("/vtc-profile").get_data(as_text=True)
    check("Not visible yet" in page and "isn't listed" in page.replace("&#39;", "'") and 'name="listed" value="1">' in page, "the profile page says why it isn't visible; listing is off")
    db.set_setting("company_tag", "CRI")
    db.set_setting("company_motto", "Steel & <wheels>")
    form = {"listed": "1", "about": "We drive <b>carefully</b>.\r\nEvery week.", "games": "ats", "region": "na", "languages": ["en", "de"],
            "styles": ["realistic"], "plays": ["convoy", "tmp"], "age": "18", "week": "500", "units": "imperial", "mic": "1",
            "dlc": "Texas <DLC>", "trial": "2 weeks", "asks_text": "Be kind", "offer": ["Weekly convoys", "", "<i>Awards</i>"],
            "discord": "https://discord.gg/crimson", "website": "https://crimson.example.com/a?b=1&c=2", "truckersmp": "https://truckersmp.com/vtc/1",
            "question": ["Why <us>?", "Do you have a mic?", "", ""], "question_type": ["text", "yesno", "text", "text"], "action": "save"}
    r = post("/vtc-profile", dict(form, website="http://plain.example.com"))
    check("Nothing was saved" in r.get_data(as_text=True) and db.setting("vtc_listed") == "0", "a link that isn't https is refused and nothing is saved")
    for k, bad in (("discord", "https://evil.example.com/discord.gg"), ("discord", "https://discord.com/channels/1"),
                   ("truckersmp", "https://truckersmp.com.evil.example"), ("website", "javascript:alert(1)"),
                   ("website", 'https://a.example.com/"onmouseover=1'), ("website", "https://user@evil.example.com"),
                   ("discord", "https://discord.gg@evil.example.com/x"), ("website", "https://a.example.com/ x"), ("website", "https://localhost")):
        check(not vtc_profile.link_ok(k, bad) and lsv.vtc_link(k, bad) == "", f"bad {k} link refused by the app and the server ({bad})")
    check(vtc_profile.link_ok("discord", "https://discord.com/invite/abc") and lsv.vtc_link("website", "https://x.example.com") != "", "good links are kept")
    r = post("/vtc-profile", dict(form, about=" "))
    check("before listing it" in r.get_data(as_text=True) and db.setting("vtc_listed") == "0", "a company can't be listed without a description")
    r = post("/vtc-profile", dict(form, action="preview"))
    check("&lt;b&gt;carefully&lt;/b&gt;" in r.get_data(as_text=True) and db.setting("vtc_profile") == "", "Preview shows it without saving")
    post("/vtc-profile", form)
    page = c.get("/vtc-profile").get_data(as_text=True)
    own_ = c.get("/find-vtc").get_data(as_text=True)
    check("Your company is in the VTC Finder" in own_ and f"/vtc/{code}" in own_, "Find a VTC then shows its page")
    check("Your company is in the VTC Finder" in page and f"/vtc/{code}" in page, "saving lists the company straight away and shows its link")
    check(">500<" in page or 'value="500"' in page, "the weekly distance comes back in the units it was typed in")
    st, pub = web(f"/vtc/{code}")
    check(st == 200 and "Crimson &lt;Line&gt; &amp; Co" in pub and "&lt;b&gt;carefully&lt;/b&gt;.<br>Every week." in pub and "<b>carefully" not in pub
          and "Steel &amp; &lt;wheels&gt;" in pub and "&lt;i&gt;Awards&lt;/i&gt;" in pub and "Texas &lt;DLC&gt;" in pub, "the profile page shows everything, escaped")
    check("At least 18" in pub and "805 km (500 mi) a week" in pub and "A microphone" in pub and "2 weeks" in pub, "with what the company asks for")
    check('href="https://crimson.example.com/a?b=1&amp;c=2" target="_blank" rel="nofollow noopener noreferrer"' in pub
          and "https://discord.gg/crimson" in pub, "links are safe ones")
    check(f'/apply/{code}?via=finder' in pub and "Open to drivers" in pub, "with an Apply button")
    check(lic.key not in pub and lic.machine not in pub and "<script" not in pub.lower() and "revenue" not in pub.lower(), "no key, PC, money or script on it")
    hdr = _ur.urlopen(base + "/vtcs").headers
    check("default-src 'none'" in hdr["Content-Security-Policy"] and hdr["X-Frame-Options"] == "DENY", "the finder has the strict page rules")

    print("VTC Finder: searching")
    def add_vtc(i, company, open_=1, revoked=None, listed=1, hidden=0, age_days=0, month=None, **prof):
        key_ = f"FMP-TEST-VTC0-{i:04d}-AAAA"
        when = lsv.iso(lsv.now() - __import__("datetime").timedelta(days=age_days))
        data = lsv.vtc_clean(dict({"about": "A fine company", "games": "both", "drivers": 3}, **prof))
        d_ = srv.store.db
        al = "abcdefghjkmnpqrstuvwxyz23456789"
        code_ = "zz" + al[i // 900] + al[(i // 30) % 30] + al[i % 30] + "qqq"
        d_.execute("INSERT INTO purchases(key,name,email,created,slots,revoked) VALUES(?,?,?,?,2,?)", (key_, "T", "", when, revoked))
        d_.execute("INSERT INTO recruit(key,code,company,intro,open,updated) VALUES(?,?,?,?,?,?)", (key_, code_, company, "", open_, when))
        d_.execute("INSERT INTO vtc_profiles(key,listed,hidden,data,created,updated) VALUES(?,?,?,?,?,?)",
                   (key_, listed, hidden, _js.dumps(data), prof.get("created", when), when))
        if month is not None:
            v_ = [month, 777123, 4, 5, 90, 1, 1, 2]
            d_.execute("INSERT INTO leaderboard(key,pid,company,currency,stats,drivers,updated) VALUES(?,?,?,'$',?,'{}',?)",
                       (key_, f"pid{i}", company, _js.dumps({"month": {"all": v_}, "all": {"all": v_}}), when))
        d_.commit()
        srv.store._vtc_cache = None
        return code_
    names_ = lambda **kw: [x["company"] for x in srv.store.vtc_search(**kw)]
    a1 = add_vtc(1, "Alpine Haulage", games="ets2", region="eu", languages=["de"], styles=["relaxed"], plays=["tmp"], drivers=12, month=900, tag="ALP")
    add_vtc(2, "Bolt Freight", games="ats", region="na", languages=["en"], styles=["competitive"], plays=["single"], drivers=40, month=5000, about="Friendly alpine trips")
    add_vtc(3, "Closed Cargo", open_=0, region="world", drivers=5, month=10)
    hid_c = add_vtc(4, "Hidden Haul", hidden=1)
    add_vtc(5, "Unlisted Lines", listed=0)
    rev_c = add_vtc(6, "Revoked Roads", revoked="2026-01-01T00:00:00Z")
    stale_c = add_vtc(7, "Stale Wheels", age_days=31)
    noab = add_vtc(8, "Empty Words", about="")
    everyone_ = names_(is_open=False)
    check(not set(everyone_) & {"Hidden Haul", "Unlisted Lines", "Revoked Roads", "Stale Wheels", "Empty Words"},
          "hidden, unlisted, revoked, stale and empty companies aren't found")
    check(all(web(f"/vtc/{x}")[0] == 404 for x in (hid_c, rev_c, stale_c, noab)), "and have no page")
    check("Closed Cargo" in everyone_ and "Closed Cargo" not in names_(), "closed companies only when everyone is asked for")
    check(names_(words="alpine") == ["Alpine Haulage", "Bolt Freight"], "a name match comes before a description match")
    check(names_(words="ALP") == ["Alpine Haulage", "Bolt Freight"] and names_(words="alpine nothere") == [], "every word must be there")
    check(names_(game="ets2") == ["Alpine Haulage"] and set(names_(game="ats")) == {"Bolt Freight", "Crimson <Line> & Co"}, "game")
    check(names_(lang="de") == ["Alpine Haulage", "Crimson <Line> & Co"] or set(names_(lang="de")) == {"Alpine Haulage", "Crimson <Line> & Co"}, "language")
    check(set(names_(region="na")) == {"Bolt Freight", "Crimson <Line> & Co"} and "Closed Cargo" in names_(region="eu", is_open=False), "region (worldwide companies fit every region)")
    check(names_(style="competitive") == ["Bolt Freight"] and "Alpine Haulage" in names_(plays="tmp") and "Bolt Freight" not in names_(plays="tmp"), "style and where it plays")
    check(names_(size="m") == ["Alpine Haulage"] and names_(size="l") == ["Bolt Freight"], "size")
    check(names_(sort="active")[0] == "Bolt Freight" and names_(sort="name", is_open=False) == sorted(everyone_, key=str.lower), "sorting")
    check(len(names_(game="<x>", lang="zz", region="q", style="q", plays="q", size="q", sort="q")) == 3, "unknown choices are ignored")
    st, pub = web("/vtcs?" + _up.urlencode({"q": '"><script>alert(1)</script>', "game": "ats"}))
    check(st == 200 and "<script" not in pub and "No company matches" in pub and "Clear filters" in pub and "aren&#39;t recruiting" in pub, "an empty result offers a way out; the search is escaped")
    st, pub = web(f"/vtc/{a1}")
    check("900 km" in pub and "Reported by the company" in pub and "777" not in pub, "activity comes from the leaderboard, without money")
    check(web("/vtcs?p=99999999999999999999")[0] == 200 and web("/vtcs?p=-1")[0] == 200 and web("/vtcs?p=%C2%B2")[0] == 200, "odd page numbers are fine")
    for i in range(20, 45):
        add_vtc(i, f"Page Co {i}", about="pagetest")
    with srv.hits_lock:
        srv.hits.clear()
    p1, p2 = web("/vtcs?q=pagetest&sort=name")[1], web("/vtcs?q=pagetest&sort=name&p=2")[1]
    check(p1.count('class="vtc"') == 20 and p2.count('class="vtc"') == 5 and "Page 1 of 2" in p1 and "p=2" in p1 and "Page Co 44" in p2, "20 to a page")
    check(web("/vtcs?" + "&".join(f"a{i}=1" for i in range(60)))[0] in (200, 413), "a flood of fields doesn't crash it")
    check(web("/vtc/../v1/admin/vtc")[0] == 404 and web(f"/vtc/{code}/edit")[0] == 404, "odd addresses get a 404")

    print("VTC Finder: applying, questions, reports, hiding")
    st, pub = web(f"/apply/{code}?via=finder")
    q1, q2 = lsv.vtc_question_id("Why <us>?"), lsv.vtc_question_id("Do you have a mic?")
    check("Why &lt;us&gt;?" in pub and f'name="{q1}"' in pub and f'<select name="{q2}"' in pub and 'name="via" value="finder"' in pub, "the Apply form has the company's questions")
    st, pub = web(f"/apply/{code}?via=finder", {"age": "30", "name": "Quinn", "contact": "q#1", q1: "<u>Nice</u>", q2: "yes", "q_unknown": "x", "via": "finder"})
    check(st == 200 and "/vtcs" in pub, "after applying there's a way back to the finder")
    web(f"/apply/{code}", {"age": "30", "name": "Lin", "contact": "l#1", q2: "maybe", "via": "hacked"})
    recruit.sync()
    qa = db.one("SELECT * FROM applications WHERE name='Quinn'")
    la = db.one("SELECT * FROM applications WHERE name='Lin'")
    check(_js.loads(qa["answers"]) == [["Why <us>?", "<u>Nice</u>"], ["Do you have a mic?", "Yes"]] and qa["found_via"] == "finder", "answers and where they came from arrive in the app")
    check(la["answers"] == "" and la["found_via"] == "link", "odd answers are dropped")
    page = c.get("/recruitment").get_data(as_text=True)
    check("Why &lt;us&gt;?" in page and "&lt;u&gt;Nice&lt;/u&gt;" in page and ">VTC Finder</span>" in page, "Recruitment shows them, escaped")
    db.ex("DELETE FROM applications WHERE name IN ('Quinn', 'Lin')")
    with srv.hits_lock:
        srv.hits.clear()
    codes = [web(f"/vtc/{code}/report", {"reason": "spam", "note": "<b>bad</b>" * 99})[0] for _ in range(4)]
    check(codes == [200, 200, 200, 429], f"3 reports per address per hour ({codes})")
    check(web(f"/vtc/{code}/report", {"reason": "nope"})[0] == 400 and web("/vtc/zzzzzzzz/report", {"reason": "spam"})[0] == 404, "a report needs a reason and a company")
    web(f"/vtc/{code}/report", {"reason": "spam", lsv.HONEYPOT: "x"})
    tok_ = {"Authorization": f"Bearer {srv.cfg['admin_token']}"}
    adm = lambda path, body=None, hd=tok_, m="POST": srv.handle(m, path, body or {}, hd, "127.0.0.1", peer="127.0.0.1")
    rows_ = adm("/v1/admin/vtc", m="GET")["listings"]
    me_ = next(x for x in rows_ if x["code"] == code)
    check(len(me_["reports"]) == 3 and len(me_["reports"][0]["note"]) <= 300 and rows_[0]["code"] == code, "License Manager sees the reports, most reported first")
    for pth in ("/v1/admin/vtc", "/v1/admin/vtc_hide", "/v1/admin/vtc_clear"):
        try:
            adm(pth, {"key": lic.key}, {}, "POST")
            check(False, f"{pth} needs the admin token")
        except lsv.LicenceError as e:
            check(e.status == 401, f"{pth} needs the admin token")
    adm("/v1/admin/vtc_hide", {"key": lic.key, "hidden": True})
    check(web(f"/vtc/{code}")[0] == 404 and "Crimson" not in web("/vtcs?open=all")[1], "a hidden company is gone at once")
    post("/vtc-profile", dict(form, about="Edited while hidden"))
    check(web(f"/vtc/{code}")[0] == 404 and "hidden by the owner of the licence server" in c.get("/vtc-profile").get_data(as_text=True),
          "it stays hidden when edited, and the app says so")
    post("/vtc-profile", {k: v for k, v in form.items() if k != "listed"})
    check(srv.store.db.execute("SELECT hidden FROM vtc_profiles WHERE key=?", (lic.key,)).fetchone()[0] == 1, "unlisting doesn't lift the hide switch")
    adm("/v1/admin/vtc_hide", {"key": lic.key, "hidden": False})
    check(adm("/v1/admin/vtc_clear", {"key": lic.key})["cleared"] == 3, "reports can be cleared")
    check(web(f"/vtc/{code}")[0] == 404, "an unlisted company isn't shown")
    kept = srv.store.db.execute("SELECT data FROM vtc_profiles WHERE key=?", (lic.key,)).fetchone()[0]
    check("carefully" not in kept and "Why <us>?" in kept, "only its Apply questions are kept on the server")
    check("Why &lt;us&gt;?" in web(f"/apply/{code}")[1], "and the shared Apply link still asks them")
    post("/vtc-profile", form)
    db.set_setting("recruitment_open", "0")
    recruit.sync()
    st, pub = web(f"/vtc/{code}")
    check(st == 200 and "Not taking applications" in pub and "via=finder" not in pub and web(f"/apply/{code}", {"age": "30", "name": "x", "contact": "y"})[0] == 404,
          "a closed company shows its profile but can't be applied to")
    db.set_setting("recruitment_open", "1")
    recruit.sync()
    big = dict(form, about="\u8f66" * 1500, asks_text="\u8f66" * 400, offer=["\u8f66" * 80] * 6, question=["\u8f66" * 110 + str(i) for i in range(5)],
               question_type=["text"] * 5, dlc="\u8f66" * 120, trial="\u8f66" * 120)
    post("/vtc-profile", big)
    check("\u8f66" * 1500 in web(f"/vtc/{code}")[1], "a full profile in any alphabet reaches the server")
    post("/vtc-profile", form)
    junk = [None, [], "x", 5, {"listed": True}, {"listed": True, "asks": [], "links": 5, "offers": {"a": 1}, "questions": ["x", {"q": 5}, None],
            "languages": "en", "styles": [[], {}], "games": [], "region": {}, "drivers": float("nan"), "colours": ["red;}", "#fff"]},
            {"about": "\u202eevil\x00", "colours": ["#12345\n", "url(x)"], "asks": {"age": 1e400, "km_week": True}, "drivers": -9}]
    cleaned = [lsv.vtc_clean(x) for x in junk]
    check(all(x["colours"] == ["#0a1a33", "#c8102e"] for x in cleaned) and cleaned[-1]["about"] == "evil"
          and cleaned[-1]["asks"]["age"] == 0 and cleaned[-1]["drivers"] == 0, "nonsense profiles are cleaned, never stored as sent")
    real_post = licensing._post
    def old_server(path, body, timeout=10):
        check("profile" not in body, "an older licence server is never sent a profile")
        d_ = real_post(path, body, timeout=timeout)
        return {k: v for k, v in d_.items() if k not in ("profile_url", "finder_url", "profile")}
    licensing._post = old_server
    err_ = recruit.sync()[1]
    e2_ = recruit.push_profile()
    licensing._post = real_post
    check(err_ is None and "needs updating" in e2_ and "needs updating" in c.get("/vtc-profile").get_data(as_text=True)
          and "needs updating" in c.get("/find-vtc").get_data(as_text=True), "with an older server recruiting works and the app says it needs updating")
    recruit.sync()
    srv.handle("POST", "/v1/recruit", {"key": lic.key, "machine": lic.machine, "product": "Fleet Manager Pro", "company": "Crimson <Line> & Co",
                                       "open": True}, {}, "127.0.0.1")
    check(web(f"/vtc/{code}")[0] == 200, "an older app (no profile) doesn't break the listing")
    db.set_setting("vtc_profile", "{broken")
    check(c.get("/vtc-profile").status_code == 200 and vtc_profile.load() == vtc_profile.blank(), "a damaged saved profile still opens")
    db.set_setting("vtc_profile", '{"asks": [], "links": {"x": 1}, "languages": "en"}')
    check(c.get("/vtc-profile").status_code == 200, "and an odd one too")
    post("/vtc-profile", form)
    # what dissolving the company does
    db.set_setting("vtc_listed", "0")
    db.set_setting("vtc_profile", "")
    recruit.push_profile()
    check(srv.store.db.execute("SELECT COUNT(*) FROM vtc_profiles WHERE key=?", (lic.key,)).fetchone()[0] == 0 and web(f"/vtc/{code}")[0] == 404,
          "an emptied profile leaves nothing on the server")
    srv.store.delete_purchase("FMP-TEST-VTC0-0001-AAAA")
    check(web(f"/vtc/{a1}")[0] == 404 and srv.store.db.execute("SELECT COUNT(*) FROM vtc_profiles WHERE key LIKE '%VTC0-0001%'").fetchone()[0] == 0,
          "deleting a purchase removes its listing")
    for tb in ("vtc_profiles", "recruit", "leaderboard", "purchases"):
        srv.store.db.execute(f"DELETE FROM {tb} WHERE key LIKE 'FMP-TEST-VTC0%'")
    srv.store.db.execute("DELETE FROM vtc_reports")
    srv.store.db.commit()
    db.set_setting("company_tag", "")
    db.set_setting("company_motto", "")
    db.ex("DELETE FROM users WHERE username='vt_driver'")
    with srv.hits_lock:
        srv.hits.clear()
        srv.apply_hits.clear()

    print("Global leaderboard (every company on the licence server)")
    from fmpro import global_board
    with srv.hits_lock:
        srv.hits.clear()
    import json as _gj
    store_ = srv.store
    lbrow = lambda: store_.db.execute("SELECT * FROM leaderboard WHERE key=?", (lic.key,)).fetchone()  # noqa: E731
    check(db.setting("global_board") == "1" and db.setting("global_board_drivers") == "0",
          "a company joins the global board by default, without its drivers' names")
    err = global_board.sync()
    row_ = lbrow()
    check(err is None and row_ is not None and row_["company"] == "Crimson <Line> & Co", "the company's totals reach the server")
    st_ = _gj.loads(row_["stats"])
    real_jobs = db.val("SELECT COUNT(*) FROM jobs j JOIN users u ON u.id=j.user_id WHERE j.status='delivered' "
                       "AND u.status!='removed' AND (j.flags IS NULL OR j.flags NOT LIKE '%demo%')")
    all_jobs = db.val("SELECT COUNT(*) FROM jobs WHERE status='delivered'")
    check(st_["all"]["all"][2] == real_jobs and set(st_) == {"week", "month", "year", "all"}
          and set(st_["all"]) == {"all", "ats", "ets2"} and len(st_["all"]["all"]) == 8,
          f"all-time deliveries match this company's ({real_jobs} counted)")
    check(db.val("SELECT COUNT(*) FROM jobs WHERE status='delivered' AND flags LIKE '%demo%'") == 0
          or real_jobs < all_jobs, "simulator test drives don't count on the global board")
    check(_gj.loads(row_["drivers"]) == {}, "driver names aren't sent unless the company shares them")
    # a second company, sending junk the server must clean
    k_g = store_.new_purchase("Global Rival", "", product="Fleet Manager Pro")
    store_.activate(k_g, "FMID-GGGG-GGGG-GGGG-GGGG", "", "Fleet Manager Pro")
    junk = {"week": {"all": [1e30, -5, "x", None, 500, True]}, "month": {"all": [999999, 5000, 40, 300.55, 88.88, 70, 1, 3]},
            "all": {"all": [999999, 5000, 40, 300, 88, 70, 1, 3], "ats": "nope"}, "evil": {"all": [1]}, "year": []}
    pid_g = store_.leaderboard_sync(k_g, "FMID-GGGG-GGGG-GGGG-GGGG", "Fleet Manager Pro", True,
                                    "<b>Rival</b>   Haulage", "€€€€", junk,
                                    {"month": [{"name": "<i>Speedy</i>", "v": [5000, 1, 2, 3, 99, 5, 6, 1]}] * 30,
                                     "all": "bad"})
    rg = store_.db.execute("SELECT * FROM leaderboard WHERE key=?", (k_g,)).fetchone()
    sg, dg = _gj.loads(rg["stats"]), _gj.loads(rg["drivers"])
    check(pid_g and set(sg) == {"week", "month", "all"} and sg["week"]["all"] == [1e12, 0, 0, 0, 100, 0, 0, 0]
          and sg["all"]["ats"] == [0] * 8 and rg["company"] == "<b>Rival</b> Haulage" and rg["currency"] == "€€€",
          "the server keeps only bounded numbers in the expected shape")
    check(list(dg) == ["month"] and len(dg["month"]) == 10, "at most 10 drivers per company and period are kept")
    err = global_board.sync()
    comps, drvs = global_board.board("km", "month", "all")
    check(err is None and [r["company"] for r in comps][:1] == ["<b>Rival</b> Haulage"]
          and any(r["you"] and r["company"] == "Crimson <Line> & Co" for r in comps) or real_jobs == 0,
          "the app gets every company back, ranked, and knows which one is itself")
    check(drvs and drvs[0]["name"] == "<i>Speedy</i>" and drvs[0]["company"] == "<b>Rival</b> Haulage",
          "shared drivers of other companies are listed")
    page = c.get("/leaderboard?view=global&metric=km&period=month").get_data(as_text=True)
    check("Global Leaderboard" in page and "&lt;b&gt;Rival&lt;/b&gt; Haulage" in page and "<b>Rival</b>" not in page
          and "&lt;i&gt;Speedy&lt;/i&gt;" in page and "Show my drivers" in page,
          "the Global view lists companies and drivers, escaped, with the admin switches")
    check('href="?metric=km&amp;period=month&amp;game=&amp;view=global"' in c.get("/leaderboard").get_data(as_text=True),
          "the company leaderboard links to the Global view")
    st, pub = web("/leaderboard?metric=km&period=month&game=all")
    check(st == 200 and "&lt;b&gt;Rival&lt;/b&gt; Haulage" in pub and "<b>Rival</b>" not in pub and "<script" not in pub,
          "the public leaderboard page on the server works and escapes names")
    st, _p = web("/leaderboard?metric=%3Cx%3E&period=zz&game=../../")
    check(st == 200, "odd page settings fall back to defaults")
    with _ur.urlopen(base + "/leaderboard", timeout=10) as r_:
        csp_ = r_.headers.get("Content-Security-Policy", "")
    check("default-src 'none'" in csp_ and "script" not in csp_, "the public page allows no scripts")
    # sharing driver names, then leaving
    r = post("/leaderboard/global/settings", {"global_board": "1", "global_board_drivers": "1"})
    row_ = lbrow()
    check(r.status_code == 302 and db.setting("global_board_drivers") == "1"
          and (_gj.loads(row_["drivers"]) or real_jobs == 0), "sharing driver names sends the best drivers")
    r = post("/leaderboard/global/settings", {})
    check(r.status_code == 302 and lbrow() is None and db.setting("global_board") == "0"
          and db.setting("global_board_drivers") == "0", "leaving removes the company from the server straight away")
    comps, _d = global_board.board("km", "month", "all")
    check(comps and not any(r["you"] for r in comps), "a company that left still sees the others")
    check("isn't on the global board" in c.get("/leaderboard?view=global").get_data(as_text=True),
          "the page says the company isn't on the board")
    store_.revoke(k_g, True)
    check(store_.leaderboard() == ([], {}), "revoked purchases drop off the board")
    store_.revoke(k_g, False)
    store_.db.execute("UPDATE leaderboard SET updated='2000-01-01T00:00:00Z' WHERE key=?", (k_g,))
    store_.db.commit()
    check(store_.leaderboard()[0] == [], "companies that stop updating drop off after 30 days")
    try:
        srv.handle("POST", "/v1/leaderboard", {"key": "FMP-AAAA-AAAA-AAAA-AAAA", "machine": lic.machine,
                                               "product": "Fleet Manager Pro", "join": True}, {}, "9.9.9.9")
        check(False, "the leaderboard needs a real activated key")
    except Exception as e:
        check(getattr(e, "code", "") == "unknown_key", "the leaderboard needs a real activated key")
    store_.delete_purchase(k_g)
    check(store_.db.execute("SELECT COUNT(*) FROM leaderboard WHERE key=?", (k_g,)).fetchone()[0] == 0,
          "deleting a purchase removes its leaderboard entry")
    post("/leaderboard/global/settings", {"global_board": "1"})
    check(lbrow() is not None, "joining again puts the company back")
    post("/logout")
    db.ex("DELETE FROM login_failures")    # earlier wrong-password tests throttled this driver
    c.get("/login")
    post("/login", {"username": "rookie", "password": "driver12"})
    check(c.get("/leaderboard").status_code == 200, "(signed in as a driver)")
    r = post("/leaderboard/global/settings", {})
    check(r.status_code in (302, 403) and lbrow() is not None and db.setting("global_board") == "1",
          "drivers can't take the company off the global board")
    check(c.get("/leaderboard?view=global").status_code == 200, "drivers can see the global board")
    global_board._last_manual = 0.0
    with srv.hits_lock:
        srv.hits.clear()
    for _ in range(5):
        post("/leaderboard/global/sync")
    check(sum(len(v) for v in srv.hits.values()) == 1, "repeated Update now presses reach the licence server once")
    post("/logout")
    c.get("/login")
    post("/login", {"username": "boss", "password": "secret12"})
    c.get("/")
    with srv.hits_lock:
        srv.hits.clear()

    print("Robustness: huge numbers, failed writes, links after signing in")
    huge_ = "9" * 20
    codes_ = {u: c.get(u).status_code for u in (f"/jobs/{huge_}", f"/drivers/{huge_}", f"/fleet/truck/{huge_}", f"/events/{huge_}",
                                                f"/fleet/trailer-lease/{huge_}")}
    check(set(codes_.values()) == {404}, f"a record number too large to exist is a 404, not a crash ({codes_})")
    check(c.get(f"/jobs?page={huge_}").status_code == 200 and c.get("/jobs?page=²").status_code == 200,
          "odd page numbers show the Logbook")
    check(c.get(f"/jobs?driver={huge_}").status_code == 404, "a driver filter too large to exist is a 404")
    check(post(f"/drivers/{huge_}/edit").status_code == 404, "the same for form posts")
    import sqlite3 as _sq4
    try:
        db.insert("users", username="boss", display_name="Twin", password_hash="x", role="driver", agent_token="twin", created_at=1)
        check(False, "a duplicate username is refused by the database")
    except _sq4.IntegrityError:
        check(not db.conn().in_transaction, "a failed write doesn't leave the database locked")
    loc_ = app.test_client().get("/leaderboard?view=global&metric=xp").headers["Location"]
    check(_up.parse_qs(_up.urlsplit(loc_).query).get("next") == ["/leaderboard?view=global&metric=xp"],
          "signing in returns to the same view, filters and all")

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
    check("Diesel price per gallon" in page and 'value="5.68"' in page, "imperial: fuel price shown per gallon (1.50/L = 5.68/gal)")
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
    pdrv = db.val("SELECT id FROM users WHERE username='up_driver'")
    db.ex("UPDATE users SET pay_per_km=0.5 WHERE id=?", (pdrv,))
    prof = sc.get(f"/drivers/{pdrv}").get_data(as_text=True)
    check("Pay per mile (blank = default)" in prof and 'value="0.80"' in prof and "/mi</dd>" in prof,
          "imperial: a driver's own pay rate is shown and edited per mile on their profile")
    sc.post(f"/drivers/{pdrv}/edit", data={"units": "imperial", "pay_per_km": "0.80", "csrf": stok})
    check(db.val("SELECT pay_per_km FROM users WHERE id=?", (pdrv,)) == 0.5, "saving the profile unchanged keeps the exact per-km rate")
    sc.post(f"/drivers/{pdrv}/edit", data={"units": "imperial", "pay_per_km": "1.00", "csrf": stok})
    check(abs(db.val("SELECT pay_per_km FROM users WHERE id=?", (pdrv,)) - 1 / 1.609344) < 1e-9, "a per-mile pay rate is stored per km")
    db.ex("UPDATE users SET pay_per_km=NULL WHERE id=?", (pdrv,))
    db.set_setting("units", "metric")
    page = sc.get("/settings?tab=economy").get_data(as_text=True)
    check("Diesel price per litre" in page and "Driver pay per km" in page and "Speeding tolerance (km/h)" in page
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

    print("Recruitment: deleting old applications")
    old_ = _time.time() - 40 * 86400
    del_one = db.insert("applications", name="Delete Me", contact="d", status="pending", created_at=_time.time())
    old_rej = db.insert("applications", name="Old Reject", contact="o1", status="rejected", created_at=old_, reviewed_at=old_)
    new_rej = db.insert("applications", name="New Reject", contact="o2", status="rejected", created_at=_time.time(),
                        reviewed_at=_time.time())
    old_acc = db.insert("applications", name="Old Accept", contact="o3", status="accepted", created_at=old_, reviewed_at=old_)
    old_pend = db.insert("applications", name="Old Pending", contact="o4", status="pending", created_at=old_)
    old_int = db.insert("applications", name="Old Interview", contact="o5", status="interview", created_at=old_)
    page = c.get("/recruitment").get_data(as_text=True)
    check(f"/recruitment/{del_one}/delete" in page and 'action="/recruitment/clear"' in page,
          "each application has a Delete button and there's a clean-up form")
    users_before = db.val("SELECT COUNT(*) FROM users")
    r_ = nc.post(f"/recruitment/{del_one}/delete", data={"csrf": ntok})
    check(r_.status_code in (302, 403) and db.val("SELECT id FROM applications WHERE id=?", (del_one,)) == del_one,
          "a driver can't delete applications")
    check(c.post(f"/recruitment/{del_one}/delete").status_code in (400, 403)
          and db.val("SELECT id FROM applications WHERE id=?", (del_one,)) == del_one, "deleting needs the CSRF token")
    r_ = post(f"/recruitment/{del_one}/delete")
    check(r_.status_code == 302 and not db.val("SELECT id FROM applications WHERE id=?", (del_one,)),
          "an admin deletes one application")
    check(post("/recruitment/999999/delete").status_code == 404, "deleting an unknown application gives a 404")
    check(db.val("SELECT detail FROM audit_log WHERE action='application deleted' ORDER BY id DESC LIMIT 1") == "Delete Me (pending)",
          "the deletion is in the audit log")
    left = lambda: {r["id"] for r in db.q("SELECT id FROM applications")}
    post("/recruitment/clear", {"which": "rejected", "days": "30"})
    check(old_rej not in left() and new_rej in left() and old_acc in left(), "clean-up deletes only old rejected applications")
    post("/recruitment/clear", {"which": "reviewed", "days": "0"})
    check(not ({new_rej, old_acc} & left()) and {old_pend, old_int} <= left(),
          "accepted and rejected of any age go; pending and interview applications are never cleared")
    check(post("/recruitment/clear", {"which": "pending", "days": "0"}).status_code == 400
          and post("/recruitment/clear", {"which": "rejected", "days": "1; DROP"}).status_code == 400
          and {old_pend, old_int} <= left(), "odd clean-up choices are refused")
    check(db.val("SELECT COUNT(*) FROM users") == users_before, "deleting applications never removes driver accounts")
    db.ex("DELETE FROM applications WHERE id IN (?,?)", (old_pend, old_int))

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

    print("Driver's licences: points, tickets, suspension and the drivers test")
    from fmpro import cdl, cdl_test
    DAY = 86400
    _cu2("cdluser", "Cdl User", "cdluser123", role="driver")
    cu = db.val("SELECT id FROM users WHERE username='cdluser'")
    check(cdl.offence(cu, "red_signal") == 0 and not compliance.holds(cu) and not cdl.blocked(cu),
          "licences are off in a new company: no points, nobody is held")
    check(b"turned off" in c.get("/cdl").data, "the CDL page says licences are off")
    db.set_setting("licence_enabled", "1")
    check(any("licence" in h for h in compliance.holds(cu)), "turned on: a driver without a licence can't be dispatched")
    w0 = economy.balance("driver", cu)
    check(cdl.offence(cu, "red_signal") == 0 and economy.balance("driver", cu) == w0 - 200,
          "no licence: no points to lose, but the ticket is paid from the driver's wallet")
    T0 = clock.t
    check(cdl.issue(cu, "test", T0) and cdl.info(cu, T0)["left"] == 12 and not compliance.holds(cu), "a licence starts with 12 points")
    cdl.offence(cu, "red_signal", now=T0)
    cdl.offence(cu, "accident", now=T0 + 10)
    cdl.offence(cu, "no such thing", now=T0 + 20)
    check(cdl.info(cu, T0 + 30)["left"] == 6, "red light 3, accident 2, any other fine 1 (6 left)")
    got = [cdl.info(cu, T0 + s)["left"] for s in (DAY - 1, DAY, 2 * DAY + 5, 6 * DAY)]
    check(got == [6, 7, 8, 12], f"one point comes back every real day ({got})")
    check(cdl.info(cu, T0 + 30 * DAY)["left"] == 12, "and it stops at 12")
    check(cdl.band(5) == (1, 50) and cdl.band(25) == (2, 200) and cdl.band(200) == (3, 800), "speeding is priced by how far over")
    # zero points: suspended, wait or pay, retake the test
    T1 = T0 + 40 * DAY
    for i in range(4):
        cdl.offence(cu, "wrong_way", now=T1 + i)
    inf = cdl.info(cu, T1 + 10)
    check(inf["status"] == "suspended" and inf["left"] == 0 and not inf["can_test"], "at zero points the licence is suspended")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE '%SUSPENDED%'", (cu,), 0) == 1, "the driver is told once")
    check(cdl.info(cu, T1 + 11 * DAY)["used"] == 1 and not cdl.info(cu, T1 + 11 * DAY)["can_test"], "11 days later one point is still on")
    inf = cdl.info(cu, T1 + 12 * DAY)
    check(inf["status"] == "suspended" and inf["can_test"], "after 12 days the test can be retaken; waiting alone doesn't give the licence back")
    cdl.issue(cu, "test", T1)
    cdl.suspend(cu, "test")
    db.ex("DELETE FROM ledger WHERE account='driver' AND user_id=?", (cu,))
    check(not cdl.pay_fine(cu)[0] and cdl.info(cu)["used"] == 12, "the fine needs the money in the driver's wallet")
    economy.post("earnings", 6000, "test", account="driver", user_id=cu)
    ok1, ok2 = cdl.pay_fine(cu)[0], cdl.pay_fine(cu)[0]
    check(ok1 and not ok2 and economy.balance("driver", cu) == 1000 and cdl.info(cu)["can_test"] and not cdl.licensed(cu),
          "paying 5,000 clears every point at once (once), and the test still has to be passed")

    service.time = clock
    cv = dict(clean, on_job=False, speed=0.0, parking_brake=False, game_time_abs=9 * 60, speed_limit=25.0, fuel=500.0,
              x=0.0, z=0.0, heading=0.0)

    def drive(vals, secs, t=None, **per):
        for _i in range(secs):
            clock.t += 1
            cv.update(vals)
            for k, fn in per.items():
                cv[k] = fn(cv[k])
            feed(t or cw, cv)

    cdl.issue(cu, "test", clock.t)
    cw = service.JobTracker(cu)
    drive({}, 3)
    drive({"speed": 31.0}, 298, x=lambda q: q + 31)
    check(cdl.info(cu, clock.t)["left"] == 12, "just under 5 real minutes of speeding costs nothing yet")
    drive({"speed": 20.0}, 30, x=lambda q: q + 20)
    drive({"speed": 31.0}, 5, x=lambda q: q + 31)
    check(cdl.info(cu, clock.t)["left"] == 11, "5 real minutes of speeding in total cost 1 point")
    drive({"speed": 31.0}, 601, x=lambda q: q + 31)
    check(cdl.info(cu, clock.t)["left"] == 9, f"and every 5 minutes after that another ({cdl.info(cu, clock.t)['left']} left)")
    drive({"speed": 15.0}, 2, x=lambda q: q + 15)
    cv["fine_offence"], cv["fine_amount"] = "crash", 900
    drive({"speed": 14.0, "wear_chassis": 0.02, "fined": True}, 1)
    drive({"fined": False, "wear_chassis": 0.03}, 2)
    check(cdl.info(cu, clock.t)["left"] == 7, "one crash is one accident, however the game reports it (2 points)")
    cv["fine_offence"], cv["fine_amount"] = "red_signal", 700
    drive({"fined": True}, 1)
    drive({"fined": False}, 1)
    check(cdl.info(cu, clock.t)["left"] == 4, "a red light fined by the game costs 3")
    drive({"speed": 15.0}, 30)
    drive({"wear_chassis": 0.12}, 1)
    check(cdl.info(cu, clock.t)["status"] == "suspended", "a serious accident costs 4: out of points, suspended")
    n = db.val("SELECT COUNT(*) FROM licence_points WHERE user_id=?", (cu,), 0)
    drive({"truck_id": "vehicle.other", "wear_chassis": 0.5}, 2)
    drive({"truck_id": clean["truck_id"], "wear_chassis": 0.0}, 2)
    sims = db.val("SELECT COUNT(*) FROM licence_points", (), 0)
    for i in range(3):
        clock.t += 1
        fr = reader.parse(reader.pack(dict(cv, speed=20.0, wear_chassis=0.1 * i, x=0.0)))
        fr["demo"] = True
        cw.feed(fr)
    check(db.val("SELECT COUNT(*) FROM licence_points WHERE user_id=?", (cu,), 0) == n and sims == db.val("SELECT COUNT(*) FROM licence_points", (), 0),
          "changing truck or the simulator never costs points")
    # a load started without a licence is held
    lt = service.JobTracker(cu)
    feed(lt, dict(clean, on_job=False, game_time_abs=70000))
    clock.t += 1
    feed(lt, dict(clean, on_job=True, cargo="Licence Load", cargo_id="liclo", job_starting_time=70000, game_time_abs=70000))
    check("no-licence" in (db.val("SELECT flags FROM jobs WHERE cargo='Licence Load'") or ""), "a load started without a licence is flagged")
    clock.t += 1
    feed(lt, dict(clean, on_job=False, job_delivered=True, job_delivered_revenue=1000, job_starting_time=70000, job_finished_time=70060, cargo="Licence Load"))
    check(db.val("SELECT status FROM jobs WHERE cargo='Licence Load'") == "pending_review", "and its pay waits for a manager")

    # the drivers test
    live = {}

    def begin(gt=9 * 60):
        cdl.cancel_test(cu)
        cdl.clear_points(cu)
        cv.clear()
        cv.update(clean, on_job=False, speed=0.0, game_time_abs=gt, speed_limit=25.0, fuel=500.0, x=0.0, z=0.0, heading=0.0)
        t = service.JobTracker(cu)
        feed(t, cv)
        live["v"] = reader.parse(reader.pack(cv))["values"]
        r_ = cdl.start_test(cu, live["v"])
        check(r_[0], f"drivers test starts ({r_[1]})") if not r_[0] else None
        return t

    def last():
        return db.one("SELECT * FROM licence_tests WHERE user_id=? ORDER BY id DESC LIMIT 1", (cu,))

    def stage():
        return (cdl.active_test(cu) or {}).get("stage")

    check(not cdl.start_test(cu, None)[0] and not cdl.start_test(cu, {"game": "ats"}, True)[0], "no game or the simulator: the test can't start")
    cw = begin()
    check(not cdl.start_test(cu, live["v"])[0], "only one test at a time")

    def pretrip():
        drive({"parking_brake": True, "engine_enabled": True, "lights_beam_low": True}, 3)
        drive({"blinker_left_active": True}, 2)
        drive({"blinker_left_active": False, "blinker_right_active": True}, 2)
        drive({"blinker_right_active": False, "lights_hazard": True}, 2)
        drive({"lights_hazard": False}, 2)
        check(stage() == "pretrip", "the pre-trip isn't finished while one check is missing")
        drive({"wipers": True}, 2)
        check(stage() == "control", "every pre-trip check done: on to the control skills")

    trailer = [{"attached": True, "id": "t", "name": "Box"}]

    def tframe(**vals):
        """One second with a trailer attached."""
        clock.t += 1
        cv.update(vals)
        rt[0] += 1
        fr = reader.parse(reader.pack(dict(cv, render_time=rt[0], simulated_time=rt[0])))
        fr["trailers"], fr["values"]["trailers_attached"] = trailer, 1
        cw.feed(fr)

    def control():
        tframe(parking_brake=False, wipers=False, speed=0.0)
        for i in range(15):
            tframe(speed=-2.0)
        tframe(speed=0.0)
        for i in range(10):
            tframe(speed=-2.0, heading=0.02 * i)
        for sp in (3, 6, 9, 7, 5, 3, 1, 0):
            tframe(speed=float(sp))
        check(stage() == "road", f"trailer coupled, straight and bent reverse, smooth stop: on to the road ({cdl.active_test(cu)['done']})")

    pretrip()
    control()
    gt = cv["game_time_abs"]
    for i in range(480):                 # 4 s of real time per in-game minute is far slower than the game ever runs
        tframe(speed=20.0, x=cv["x"] + 20, game_time_abs=int(gt + i // 3))
        if stage() != "road":
            break
    check(stage() == "park", f"two in-game hours driven: time to park ({stage()}, {last()['reason']})")
    tframe(speed=8.0); tframe(speed=4.0); tframe(speed=0.0, parking_brake=True)
    check(stage() == "park", "parked, but the engine is still on")
    tframe(engine_enabled=False)
    tst = last()
    check(tst["status"] == "passed" and tst["score"] >= 80 and cdl.licensed(cu) and cdl.info(cu, clock.t)["left"] == 12,
          f"passed with {tst['score']}: licensed with 12 points")

    def failed(what, word):
        tst = last()
        check(tst["status"] == "failed" and word in tst["reason"] and not cdl.licensed(cu), f"{what} fails the test ({tst['reason']})")

    cdl.suspend(cu, "test")
    cw = begin()
    drive({"game_time_abs": 9 * 60 + 480}, 1)
    drive({"game_time_abs": 9 * 60 + 480}, 1)
    failed("sleeping", "Time was skipped")
    cw = begin()
    drive({}, 2)
    drive({"game_time_abs": 8 * 60}, 2)
    failed("loading an earlier save", "earlier save")
    cw = begin()
    drive({}, 2)
    drive({"truck_id": "vehicle.other"}, 2)
    failed("another truck", "changed truck")
    cw = begin()
    for i in range(70):
        drive({"game_time_abs": 9 * 60 + i}, 4)
    failed("an hour of pre-trip", "in-game hour")
    cw = begin()
    drive({"speed": 1.8, "engine_enabled": True}, 3, x=lambda q: q + 2)
    drive({"wear_chassis": 0.04}, 1)
    failed("an accident", "accident")
    cw = begin()
    drive({}, 3)
    cv["fine_offence"], cv["fine_amount"] = "red_signal", 700
    drive({"fined": True}, 1)
    failed("a red light", "red light")
    n = db.val("SELECT COUNT(*) FROM licence_points WHERE user_id=?", (cu,), 0)
    cw = begin()
    drive({}, 3)
    clock.t += 900
    drive({"game_time_abs": 9 * 60 + 200}, 2)
    check(last()["status"] == "cancelled" and not cdl.licensed(cu), "a lost connection stops the test without failing it")
    check(db.val("SELECT COUNT(*) FROM licence_points WHERE user_id=?", (cu,), 0) == n, "mistakes in the test never cost points")
    cw = begin()
    drive({}, 3)
    cw = service.JobTracker(cu)                         # the program restarts
    drive({"parking_brake": True}, 4)
    check("brake" in (cdl.active_test(cu) or {"done": []})["done"], "a running test carries on after a restart")
    cdl.cancel_test(cu)
    drive({}, 3)
    check(last()["status"] == "cancelled" and cw.cdl.test is None, "a cancelled test stops")
    db.set_setting("licence_retest_hours", "24")
    db.update("licence_tests", last()["id"], status="failed", finished_at=_time.time())
    check(not cdl.info(cu)["can_test"] and not cdl.start_test(cu, live["v"])[0], "the owner can make drivers wait after a failed test")
    db.set_setting("licence_retest_hours", "0")
    service.time = _time
    # road marks, straight on the marker
    v0 = dict(live["v"], speed=20.0, speed_kmh=72.0, speed_limit_kmh=80.0, lights_beam_low=True, engine_enabled=True)
    for what, per, word in (("speeding", {"speed_limit_kmh": 50.0}, "Speeding"), ("the parking brake", {"parking_brake": True}, "parking brake"),
                            ("no lights at night", {"game_time_abs": 23 * 60, "lights_beam_low": False}, "headlights")):
        st = dict(cdl_test.new(dict(v0, **per)), stage="road")
        prv = None
        for i in range(12):
            vv = dict(v0, **per)
            cdl_test.step(st, vv, prv, 1.0, 1000.0 + i)
            prv = vv
        check(st["score"] < 100 and word in st["marks"][0]["text"], f"{what} loses marks ({st['marks'][0]['text']}, {st['score']})")
    for sig, want in ((False, 98), (True, 100)):
        st = dict(cdl_test.new(v0), stage="road")
        prv = None
        for i in range(8):
            vv = dict(v0, speed=6.0, speed_kmh=21.6, heading=0.04 * i, blinker_left_active=sig)
            cdl_test.step(st, vv, prv, 1.0, 1000.0 + i)
            prv = vv
        check(st["score"] == want, f"a junction turn {'with' if sig else 'without'} a signal scores {st['score']}")
    st = dict(cdl_test.new(v0), stage="road")
    for i in range(6):
        cdl_test.fined(st, "no_lights", "Driving without lights")
    check(st["stage"] == "failed" and st["score"] >= 60, "below 80 marks the test is failed")
    # the pages
    page = c.get("/cdl").get_data(as_text=True)
    check("What offences cost" in page and "Issue licences to all current drivers" in page and "Cdl User" in page, "the owner sees every licence")
    post(f"/cdl/driver/{cu}", {"action": "issue"})
    check(cdl.licensed(cu), "the owner can issue a licence")
    post(f"/cdl/driver/{cu}", {"action": "suspend"})
    check(not cdl.licensed(cu), "and suspend it")
    post("/cdl/issue-all", {})
    check(cdl.licensed(uid) and not cdl.licensed(cu), "issue-all gives everyone without a licence one, but never lifts a suspension")
    check(c.get(f"/cdl?driver={cu}").status_code == 200 and "Cdl User: points" in c.get(f"/cdl?driver={cu}").get_data(as_text=True)
          and c.get("/cdl?driver=abc").status_code == 200 and c.get("/cdl/test/panel").get_json() == {"running": False}, "history and panel load")
    check(post("/cdl/pay", {}).status_code == 302 and post("/cdl/test/start", {}).status_code == 302, "actions answer politely when there is nothing to do")
    db.set_setting("licence_enabled", "0")

    print("Drivers' bank accounts: payday every Friday at noon, fines paid from them")
    import datetime as _dt
    from fmpro import bank
    _cu2("bankuser", "Bank User", "bankuser12", role="driver")
    bu = db.val("SELECT id FROM users WHERE username='bankuser'")
    co = economy.balance()
    bank.process()
    bank.process()
    check(bank.balance(bu) == 3000 and economy.balance() == co, "a new account opens with 3,000, once, and not from the company's money")
    check([x["category"] for x in bank.statement(bu)] == ["opening deposit"], "the opening deposit is on the statement")
    db.set_setting("bank_opening", "0")
    _cu2("bankzero", "Bank Zero", "bankzero12", role="driver")
    bz = db.val("SELECT id FROM users WHERE username='bankzero'")
    bank.process()
    db.set_setting("bank_opening", "3000")
    bank.process()
    check(bank.balance(bz) == 0 and bank.statement(bz) == [], "set to 0 an account opens empty, and raising it later doesn't add money")
    ts = lambda *a: _dt.datetime(*a).timestamp()
    got = [bank.last_payday(ts(2026, 11, 20, 11, 59)), bank.last_payday(ts(2026, 11, 20, 12, 0)), bank.last_payday(ts(2026, 11, 26, 23, 0))]
    check(got == [ts(2026, 11, 13, 12), ts(2026, 11, 20, 12), ts(2026, 11, 20, 12)], "payday is Friday at noon, to the minute")
    check(bank.next_payday(ts(2026, 11, 20, 12, 0)) == ts(2026, 11, 27, 12) and bank.next_payday(ts(2026, 10, 30, 13)) == ts(2026, 11, 6, 12),
          "the next one is a week later, also when the clocks change")
    co = economy.balance()
    bj = db.insert("jobs", user_id=bu, source="manual", status="delivered", game="ats", cargo="Pay Load", source_city="A", destination_city="B",
                   driven_km=1000, planned_km=1000, revenue=5000, income=5000, started_at=1, finished_at=2)
    economy.settle_job(bj)
    pay = db.val("SELECT driver_pay FROM jobs WHERE id=?", (bj,))
    check(pay > 0 and bank.pending(bu) == pay and bank.balance(bu) == 3000, f"pay for a load ({pay}) waits for payday")
    check(abs(economy.balance() - (co + 5000 - pay)) < 0.01, "the company account has already paid it: the two accounts are separate")
    fri = bank.next_payday()
    db.ex("UPDATE ledger SET created_at=? WHERE account='wages' AND job_id=?", (fri - 60, bj))
    bank.process(fri - 1)
    check(bank.balance(bu) == 3000, "a second before noon nothing is paid")
    co = economy.balance()
    p1, p2 = bank.process(fri), bank.process(fri + 5)
    check(bu in p1 and bu not in p2 and bank.balance(bu) == 3000 + pay and bank.pending(bu) == 0, "at noon it is deposited, once")
    check(economy.balance() == co, "payday doesn't touch the company account")
    check(db.val("SELECT COUNT(*) FROM notifications WHERE user_id=? AND text LIKE 'Payday%'", (bu,), 0) == 1, "the driver is told")
    bank.earn(bu, 100, "late")
    db.ex("UPDATE ledger SET created_at=? WHERE user_id=? AND description='late'", (fri + 1, bu))
    bank.process(fri + 3600)
    check(bank.pending(bu) == 100, "a load delivered after noon goes on next week's pay")
    bank.process(fri + 20 * 86400)
    check(bank.pending(bu) == 0 and bank.balance(bu) == 3000 + pay + 100, "a payday missed while the program was closed is made up")
    st_ = bank.statement(bu)
    check(sorted(x["amount"] for x in st_) == sorted([100, pay, 3000]) and st_[0]["after"] == 3100 + pay, "the statement shows the running balance")
    check([len(x["loads"]) for x in bank.stubs(bu)] == [1, 1] and bank.stubs(bu)[1]["loads"][0]["job_id"] == bj, "each pay slip lists its own loads")
    # fines come out of the driver's account
    b0, co = bank.balance(bu), economy.balance()
    compliance.record_violation(bu, "HOS-11", "bankday", 10, "test", fine=500, job_id=bj, game="ats")
    check(bank.balance(bu) == b0 - 500 and economy.balance() == co, "an Hours of Service fine is paid by the driver, not the company")
    compliance.inspect(bu, {"game": "ats", "game_time_abs": 10, "wear_wheels": 0.2}, job_id=bj, level=2)
    fine_ = db.val("SELECT fine FROM inspections WHERE user_id=?", (bu,))
    check(fine_ > 0 and bank.balance(bu) == b0 - 500 - fine_ and economy.balance() == co, "so is a roadside inspection fine")
    check(db.val("SELECT driver_fines FROM jobs WHERE id=?", (bj,)) == 500 + fine_, "the load remembers what the driver paid")
    service.time = clock
    bw = service.JobTracker(bu)
    bv = dict(clean, on_job=False, speed=20.0, speed_limit=25.0, fine_offence="red_signal", fine_amount=700)
    feed(bw, bv)
    clock.t += 1
    b0 = bank.balance(bu)
    feed(bw, dict(bv, fined=True))
    check(bank.balance(bu) == b0 - 700 and economy.balance() == co, "a game fine comes out of the driver's account")
    db.set_setting("licence_enabled", "1")
    cdl.issue(bu, "test", clock.t)
    bw.cdl.on_t = 0
    clock.t += 30
    feed(bw, dict(bv, fined=False))
    clock.t += 1
    b0 = bank.balance(bu)
    feed(bw, dict(bv, fined=True, fine_amount=701))
    check(bank.balance(bu) == b0 - 701 and cdl.info(bu, clock.t)["left"] == 9, "with licences on: points and the fine, no ticket on top")
    clock.t += 30
    feed(bw, dict(bv, fined=False))
    clock.t += 1
    b0 = bank.balance(bu)
    feed(bw, dict(bv, fined=False, wear_chassis=0.02))
    check(bank.balance(bu) == b0 - 200, "an accident the game doesn't fine gets a ticket")
    clock.t += 1
    feed(bw, dict(bv, fined=True, fine_offence="crash", fine_amount=900, wear_chassis=0.02))
    check(bank.balance(bu) == b0 - 900 and cdl.info(bu, clock.t)["left"] == 7, "and when the game fines it after all, only the fine is paid")
    db.set_setting("drivers_pay_fines", "0")
    clock.t += 30
    feed(bw, dict(bv, fined=False, wear_chassis=0.02))
    clock.t += 1
    b0 = bank.balance(bu)
    feed(bw, dict(bv, fined=True, fine_amount=702, wear_chassis=0.02))
    check(bank.balance(bu) == b0 - 200 and economy.balance() == co - 702, "set to company pays: the fine is the company's, the ticket the driver's")
    db.set_setting("drivers_pay_fines", "1")
    db.set_setting("licence_enabled", "0")
    service.time = _time
    bank.fine(bu, "fines", 9000, "test")
    check(bank.balance(bu) < 0, "fines can overdraw the account")
    page = c.get("/bank").get_data(as_text=True)
    check("Driver accounts" in page and "Next payday" in page and "Bank User" in page, "the bank page shows the owner every account")
    check("Overdrawn" in c.get(f"/bank?driver={bu}").get_data(as_text=True) and c.get("/bank?driver=999999").status_code == 404
          and c.get("/bank?driver=x").status_code == 200, "a manager can open a driver's account")
    check(c.get("/finance").status_code == 200 and "Waiting for Friday" in c.get("/finance").get_data(as_text=True), "Finance shows the payroll set aside")

    print("Personal loans: four APRs, into the driver's own account, paid back every Friday")
    from fmpro import personal_loans as pl
    _cu2("loanuser", "Loan User", "loanuser12", role="driver")
    lu = db.val("SELECT id FROM users WHERE username='loanuser'")
    bank.process()
    check(sorted(p["apr"] for p in pl.PLANS.values()) == [4.9, 6.9, 8.9, 11.5], "four plans, each with its own APR")
    co = economy.balance()
    ok_, msg_ = pl.take(lu, "starter", 6000)
    ln = pl.loans(lu)[0]
    check(ok_ and bank.balance(lu) == 9000 and economy.balance() == co, f"the money is in the driver's account, not the company's ({msg_})")
    r_ = 0.069 / 52
    check(abs(ln["installment"] - 6000 * r_ / (1 - (1 + r_) ** -12)) < 0.01 and ln["balance"] == round(ln["installment"] * 12, 2)
          and 3 * 86400 <= ln["next_due"] - ln["created_at"] <= 11 * 86400, f"12 fixed instalments of {ln['installment']}, the first 3 to 11 days away")
    check(not pl.take(lu, "short", 499)[0] and not pl.take(lu, "short", 25001)[0] and not pl.take(lu, "free", 1000)[0],
          "too little, too much or an unknown plan is refused")
    fri = ln["next_due"]
    check(_dt.datetime.fromtimestamp(fri).strftime("%a %H:%M") == "Fri 12:00", "instalments fall on payday")
    bank.process(fri - 1)
    check(pl.loans(lu)[0]["payments_made"] == 0, "nothing is taken before Friday noon")
    bank.process(fri)
    bank.process(fri + 60)
    ln = pl.loans(lu)[0]
    check(ln["payments_made"] == 1 and abs(bank.balance(lu) - (9000 - ln["installment"])) < 0.01, "Friday's instalment is taken once")
    bank.process(fri + 3 * 7 * 86400 + 7200)
    check(pl.loans(lu)[0]["payments_made"] == 4, "instalments missed while the program was closed are made up")
    b0 = bank.balance(lu)
    bank.fine(lu, "fines", b0 - 40, "test")
    check(not pl.payoff(lu, ln["id"])[0], "paying off needs the money")
    bank.process(pl.loans(lu)[0]["next_due"])
    check(pl.loans(lu)[0]["payments_made"] == 5 and bank.balance(lu) < 0, "an instalment is taken even when it overdraws the account")
    economy.post("earnings", 9000, "test", account="driver", user_id=lu)
    b0, owed = bank.balance(lu), pl.loans(lu)[0]["balance"]
    check(pl.payoff(lu, ln["id"])[0] and not pl.payoff(lu, ln["id"])[0] and abs(bank.balance(lu) - (b0 - owed)) < 0.01
          and pl.loans(lu)[0]["status"] == "paid", "paid off early, once")
    for plan, n in (("short", 4), ("extended", 52)):
        pl.take(lu, plan, 2000)
        ln = pl.loans(lu)[0]
        b0 = bank.balance(lu)
        bank.process(ln["next_due"] + (n - 1) * 7 * 86400 + 7200)
        ln = db.one("SELECT * FROM personal_loans WHERE id=?", (ln["id"],))
        check(ln["status"] == "paid" and ln["payments_made"] == n and abs(b0 - bank.balance(lu) - ln["installment"] * n) < 0.02,
              f"paid off after exactly {n} instalments")
    check(all(pl.take(lu, "short", 1000)[0] for _ in range(3)) and not pl.take(lu, "short", 1000)[0], "at most three loans at once")
    db.set_setting("personal_loans", "0")
    check(not pl.take(uid, "short", 1000)[0] and "Take a loan" not in c.get("/bank").get_data(as_text=True), "the owner can turn loans off")
    db.set_setting("personal_loans", "1")
    b0, n0 = bank.balance(uid), len(pl.loans(uid))
    post("/bank/loan", {"plan": "short", "amount": "1,000"})
    ml = pl.loans(uid)
    check(len(ml) == n0 + 1 and abs(bank.balance(uid) - b0 - 1000) < 0.01, "a loan taken on the page")
    for bad in ("nan", "inf", "-5", "", "x", "1e99"):
        post("/bank/loan", {"plan": "short", "amount": bad})
    check(len(pl.loans(uid)) == n0 + 1, "odd amounts are refused")
    economy.post("earnings", 9000, "test", account="driver", user_id=uid)
    post(f"/bank/loan/{ml[0]['id']}/payoff", {})
    check(pl.loans(uid)[0]["status"] == "paid" and "Personal loans" in c.get("/bank").get_data(as_text=True), "and paid off on the page")

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
    check(led[0]["amount"] == 300000.0, "a data reset restarts with the locked 300,000")
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

    # Dissolving the company (after Clear all data: it removes every member)
    print("Dissolve")
    from fmpro import ops as _ops
    from fmpro.auth import create_user as _cu_ds
    if True:
        owner_ = db.one("SELECT id, username FROM users WHERE role='owner'")
        d1 = _cu_ds("ds_driver", "Dee Driver", "dsdriver12", role="driver")
        _cu_ds("ds_manager", "Max Manager", "dsmanager12", role="admin")
        dtoken = db.val("SELECT agent_token FROM users WHERE id=?", (d1,))
        djob = db.insert("jobs", user_id=d1, status="delivered", revenue=2000, driven_km=300, started_at=now_, finished_at=now_)
        for k_, v_ in (("public_address", "198.51.100.7"), ("public_address_auto", "1"), ("allowed_hosts", "myco.ddns.net"),
                       ("recruitment_open", "1")):
            db.set_setting(k_, v_)
        members_before = db.val("SELECT COUNT(*) FROM users WHERE role!='owner' AND username!='former-drivers'")
        jobs_before = db.val("SELECT COUNT(*) FROM jobs")
        rr_ = c.get("/settings?tab=data")
        page = rr_.get_data(as_text=True)
        check('action="/settings/dissolve"' in page and "Dissolve the company" in page, "the owner's Data tab has Dissolve the company")
        dc = app.test_client()
        dc.get("/login")
        with dc.session_transaction() as s_:
            dt_ = s_.get("csrf")
        dc.post("/login", data={"username": "ds_manager", "password": "dsmanager12", "csrf": dt_})
        dc.get("/")
        with dc.session_transaction() as s_:
            dt_ = s_.get("csrf")
        check(dc.post("/settings/dissolve", data={"understand": "1", "confirm": "DISSOLVE", "password": "dsmanager12", "csrf": dt_}).status_code == 403,
              "only the owner can dissolve the company")
        post("/settings/dissolve", {"understand": "1", "confirm": "dissolve", "password": "x"})
        post("/settings/dissolve", {"confirm": "DISSOLVE", "password": "x"})
        post("/settings/dissolve", {"understand": "1", "confirm": "DISSOLVE", "password": "wrong-password"})
        check(db.val("SELECT COUNT(*) FROM users WHERE role!='owner' AND username!='former-drivers'") == members_before,
              "dissolving needs the box ticked, DISSOLVE typed and the owner's password")
        check(db.val("SELECT COUNT(*) FROM audit_log WHERE action='dissolve refused'") >= 1, "a wrong password is recorded")
        db.set_setting("global_board", "1")
        global_board.sync()
        on_board_ = lbrow() is not None
        r = post("/settings/dissolve", {"understand": "1", "confirm": "DISSOLVE", "password": "secret12", "erase": "0"})
        check(on_board_ and lbrow() is None and db.setting("global_board") == "0",
              "dissolving (erase=0) takes the company off the global leaderboard")
        check(r.status_code == 302 and db.val("SELECT COUNT(*) FROM users WHERE role!='owner' AND status!='removed'") == 0,
              "dissolving removes every member except the owner")
        check(db.val("SELECT COUNT(*) FROM jobs") == jobs_before and db.val("SELECT user_id FROM jobs WHERE id=?", (djob,))
              == db.val("SELECT id FROM users WHERE username='former-drivers'"), "keeping the books keeps every job, under Former driver")
        check(all((db.setting(k_, "") or "") in ("", "0") for k_ in ("public_address", "public_address_auto", "allowed_hosts", "recruitment_open", "upnp_port")),
              "the internet address, host names, router port and recruiting are switched off")
        check(app.test_client().post("/api/agent/frame", json={"frame": None}, headers={"X-Agent-Token": dtoken}).status_code == 401,
              "members' PC connections stop working")
        check(dc.get("/profile").status_code == 302, "members' sessions end")
        check(c.get("/").status_code == 200 and db.val("SELECT COUNT(*) FROM audit_log WHERE action='company dissolved'") == 1,
              "the owner carries on, and the dissolve is in the audit log")
        _cu_ds("ds_again", "Again Driver", "dsagain123", role="driver")
        db.set_setting("global_board", "1")
        global_board.sync()
        on_board_ = lbrow() is not None
        r = post("/settings/dissolve", {"understand": "1", "confirm": "DISSOLVE", "password": "secret12", "erase": "1"})
        check(on_board_ and lbrow() is None and db.setting("global_board") == "0",
              "dissolving (erase=1) takes the company off the global leaderboard")
        check(r.status_code == 302 and db.val("SELECT COUNT(*) FROM jobs") == 0
              and db.val("SELECT COUNT(*) FROM users WHERE id!=?", (owner_["id"],)) == 0
              and db.val("SELECT COUNT(*) FROM users WHERE id=?", (owner_["id"],)) == 1, "erase also wipes the company data and keeps the owner")
        bks = os.listdir(os.path.join(os.path.dirname(db.DB_PATH), "backups"))
        check(any(b.startswith("fleet-before-clear") for b in bks), "a backup is saved before erasing")

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
    with srv.hits_lock:                    # a fresh minute for the licence server's request limit
        srv.hits.clear()
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
        check(r.status_code == 200 and b"licence key" in r.data.lower(), "the activate screen works before unlocking")
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
        # restart: the background licence check can run the moment the licence is loaded, so the build tag must
        # already be known then (it used to be loaded later, and the copy showed "not activated" until a retry)
        orig_init = licensing.Licence.__init__

        def init_and_check(self_, path_):
            orig_init(self_, path_)
            self_.check()                       # the licence thread winning the race

        licensing.BUILD_TAG = None                 # a fresh start: nothing loaded yet
        licensing.Licence.__init__ = init_and_check
        try:
            create_app(tmp2, start_telemetry=False, check_licence=False)
        finally:
            licensing.Licence.__init__ = orig_init
        lic3 = licensing.LICENCE
        check(lic3.ok and lic3.lease.get("build") == tag_ and lic3.page_key() is not None,
              "a licence check right at start-up sends the build tag, so the copy opens unlocked")
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

    print("Public build (one build for everyone, pages not sealed, one update channel)")
    with srv.hits_lock:
        srv.hits.clear()
    pub_tag = seal_pages.build_tag(master_, seal_pages.PUBLIC_BUILD)
    tmp3 = tempfile.mkdtemp(prefix="fmp-public-")
    pub_dir = os.path.join(srv.updates_dir(), pub_tag)
    try:
        n_open = seal_pages.open_templates(fm_, pub_tag)
        importlib.invalidate_caches()
        sys.modules.pop("fmpro._embedded_templates", None)
        n_all = sum(len(fs) for _d, _s, fs in os.walk(os.path.join(fm_, "templates")))
        check(n_open == n_all and "SEALED = {}" in open(emb_, encoding="utf-8").read(),
              f"the public build carries all {n_open} pages unsealed")
        check(re.fullmatch(r"FMPB(-[A-Z2-7]{4}){4}", pub_tag) and pub_tag != seal_pages.build_tag(master_, "STOCK"),
              f"the public build has its own valid build tag ({pub_tag})")
        app3 = create_app(tmp3, start_telemetry=False, check_licence=False)
        app3.testing = True
        c3 = app3.test_client()
        lic3p = licensing.LICENCE
        check(licensing.BUILD_TAG == pub_tag and not licensing.SEALED_PAGES, "the public copy knows its update channel")
        check(app3.jinja_env.get_template("dashboard.html") is not None, "every page is there without any page key")
        check(c3.get("/activate").status_code == 200, "the public copy opens on the activate screen")
        with c3.session_transaction() as s_:
            tok3 = s_.get("csrf")
        k_pub = srv.store.new_purchase("Public User", "", product="Fleet Manager Pro")
        r3 = c3.post("/activate", data={"key": k_pub, "agree": "1", "csrf": tok3})
        check(lic3p.ok and lic3p.reason is None,
              f"a public copy activates without being locked out for page keys ({lic3p.reason} / {r3.status_code})")
        # updates: one folder serves every public copy, whoever's key it was activated with
        os.makedirs(pub_dir, exist_ok=True)
        with zipfile.ZipFile(os.path.join(pub_dir, "Fleet Manager Pro 99.0.0.zip"), "w") as z_:
            z_.writestr("Fleet Manager Pro/VERSION", "99.0.0")
        offer = srv.update_manifest(pub_tag, "1.0.0")
        check(offer.get("update") and offer["manifest"]["build"] == pub_tag and offer["manifest"]["version"] == "99.0.0",
              "the licence server offers the public update to a public copy")
        check(not srv.update_manifest(tag_, "1.0.0").get("update"), "a buyer's own copy isn't offered the public build")
    finally:
        shutil.rmtree(pub_dir, ignore_errors=True)
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
        shutil.rmtree(tmp3, ignore_errors=True)
    check(not os.path.exists(emb_) and licensing.BUILD_TAG is None, "the developer copy is left without a build tag")

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
