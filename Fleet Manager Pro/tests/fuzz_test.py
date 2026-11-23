"""Robustness scan: every page and form, as every role, with odd input. Nothing may answer 500.

    python tests/fuzz_test.py

Uses a throw-away data folder; never touches ./data. No licence server needed (the licence check is off).
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmpro import create_app, db  # noqa: E402
from fmpro.auth import create_user  # noqa: E402

FAILS = []
ODD = ["", "0", "-1", "abc", "9999999999", "1e309", "nan", "<script>alert(1)</script>", "' OR 1=1 --", "../../etc/passwd",
       "2026-13-45", "0001-01-01", "‮\u0000x", "x" * 5000]


def main():
    tmp = tempfile.mkdtemp(prefix="fmp-fuzz-")
    app = create_app(tmp, start_telemetry=False, check_licence=False)
    app.testing = False                                # a crash becomes a 500 the scan can count
    db.set_setting("eula_accepted", "fuzz test")      # this scan is about the pages, not the agreement screen
    c = app.test_client()
    c.get("/setup")
    with c.session_transaction() as s:
        tok = s.get("csrf")
    c.post("/setup", data={"csrf": tok, "company_name": "Fuzz Freight", "company_tag": "FZ", "username": "owner",
                           "display_name": "Owner", "password": "ownerpass1", "password2": "ownerpass1", "units": "imperial",
                           "currency": "$", "default_game": "ats", "starting_balance": "1000000"})
    for role in ("trainee", "driver", "dispatcher", "admin"):
        create_user(f"u_{role}", role.title(), "pass12345", role=role)
    now = 1.7e9
    uid = db.val("SELECT id FROM users WHERE username='owner'")
    tid = db.insert("trucks", brand="Peterbilt", model="389", plate="FZ1", purchase_price=95000, created_at=now)
    gid = db.insert("garages", city="Denver", city_id="denver", game="ats", size="small", slots=1, purchase_price=100000, created_at=now)
    trl = db.insert("trailers", name="Box", purchase_price=20000, created_at=now)
    jid = db.insert("jobs", user_id=uid, truck_id=tid, game="ats", source="telemetry", status="delivered", cargo="Printers",
                    source_city="A", destination_city="B", income=1000, revenue=900, cargo_damage=0.02, started_at=now, finished_at=now)
    cid = db.insert("contracts", title="C", destination_city="Denver", status="open", created_at=now)
    from fmpro import leasing
    lt = db.insert("trucks", brand="Volvo", model="VNL", plate="FZL", purchase_price=0, created_at=now)
    lsid = leasing.start(lt, leasing.plans()[0], 120000, uid)
    eid = db.insert("ledger", account="company", category="insurance", amount=-50, description="x", manual=1, created_at=now)

    get_urls = ["/", "/live", "/map", "/telemetry", "/jobs", f"/jobs/{jid}", "/jobs/new", "/dispatch", "/compliance",
                "/compliance?tab=dvir", "/compliance?tab=inspections", "/compliance?tab=violations", "/cdl", "/cdl/test/panel", "/bank", "/fleet",
                f"/fleet/truck/{tid}", "/finance", "/expenses", "/expenses.csv", "/performance", "/drivers",
                f"/drivers/{uid}", "/events", "/leaderboard", "/achievements", "/announcements", "/messages",
                "/recruitment", "/settings", "/settings?tab=data", "/settings?tab=audit", "/settings?tab=licence", "/profile",
                "/leasing", f"/fleet/lease/{lsid}", "/api/live", "/api/live/all", "/api/hos", "/api/export/jobs.csv", "/api/export/ledger.csv"]
    params = ["period", "category", "truck", "driver", "q", "kind", "page", "status", "game", "day", "tab", "user", "all"]
    posts = {
        "/expenses/save": ["amount", "date", "category", "category_other", "description", "truck_id", "driver_id", "job_id", "id"],
        f"/expenses/{eid}/delete": [], "/compliance/reset": ["driver", "game", "logbook", "confirm"],
        "/settings/clear-data": ["understand", "confirm", "password"], f"/fleet/garage/{gid}/delete": ["sale_price"],
        "/fleet/garage/save": ["game", "city_pick", "city", "size", "purchase_price", "upkeep_per_week", "id", "notes"],
        "/fleet/truck/save": ["brand", "model", "units", "odometer", "service_interval_km", "fuel_capacity", "purchase_price", "id"],
        "/fleet/trailer/save": ["name", "body_type_pick", "body_type", "purchase_price"], f"/fleet/trailer/{trl}/sell": ["price"],
        f"/fleet/truck/{tid}/sell": ["price"], "/jobs/clear-demo": ["confirm", "back"],
        "/jobs/new": ["cargo", "source_city", "destination_city", "driven_km", "dist_unit", "units", "fuel_used", "cargo_mass", "revenue"],
        "/dispatch/new": ["title", "game", "destination_city_pick", "source_city_pick", "source_company_pick", "cargo_pick", "bonus"],
        f"/dispatch/{cid}/to-game": ["save"], "/finance/entry": ["direction", "amount", "category"],
        "/leasing/new": ["plan_id", "price", "source", "truck_id", "brand", "model", "plate", "game"],
        f"/fleet/lease/{lsid}/end": ["action", "plan_id"], "/leasing/plans/1/toggle": [],
        "/leasing/plans/save": ["id", "name", "weeks", "allowed", "excess_rate", "down_pct", "apr", "residual_pct", "early_fee_pct"],
        f"/fleet/truck/{tid}/delete": [], f"/fleet/trailer/{trl}/delete": [], "/fleet/clear-sold": [],
        "/drivers/9999/fire": ["confirm"], "/drivers/purge-former": [],
        "/cdl/driver/%d" % uid: ["action"], "/cdl/pay": [], "/bank/loan": ["plan", "amount"], "/bank/loan/1/payoff": [], "/cdl/test/start": [], "/cdl/test/cancel": [], "/cdl/issue-all": [],
        "/compliance/status": ["status", "game"], "/settings": ["section", "repair_cost_per_pct", "fuel_price", "adblue_price"],
    }
    # valid values for each form; the scan swaps one field at a time for an odd value
    base = {
        "/expenses/save": {"amount": "12.5", "date": "2026-01-02", "category": "insurance", "description": "ok"},
        "/compliance/reset": {"driver": str(uid), "game": "ats", "logbook": "1", "confirm": "RESET"},
        "/settings/clear-data": {"understand": "1", "confirm": "nope"},
        "/fleet/garage/save": {"game": "ats", "city_pick": "dallas|Dallas", "size": "small", "purchase_price": "0"},
        "/fleet/truck/save": {"brand": "Mack", "model": "Anthem", "units": "imperial", "purchase_price": "0"},
        "/fleet/trailer/save": {"name": "T", "body_type_pick": "dryvan", "purchase_price": "0"},
        "/jobs/new": {"cargo": "Hay", "source_city": "A", "destination_city": "B", "driven_km": "10", "dist_unit": "mi",
                      "units": "imperial", "revenue": "100"},
        "/dispatch/new": {"title": "x", "game": "ats", "destination_city_pick": "denver|Denver"},
        "/finance/entry": {"direction": "expense", "amount": "1", "category": "x"},
        "/leasing/new": {"plan_id": "1", "price": "50000", "source": "new", "brand": "Mack", "model": "Anthem", "game": "ats"},
        f"/fleet/lease/{lsid}/end": {"action": "renew", "plan_id": "1"},
        "/leasing/plans/save": {"name": "F", "weeks": "10", "allowed": "5000", "excess_rate": "0.2", "down_pct": "5", "apr": "5",
                                "residual_pct": "50", "early_fee_pct": "20"},
        "/settings": {"section": "economy"}, "/bank/loan": {"plan": "short", "amount": "600"},
    }
    for who, pw in [("owner", "ownerpass1")] + [(f"u_{r}", "pass12345") for r in ("admin", "dispatcher", "driver", "trainee")]:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("csrf")
        c.post("/login", data={"csrf": tok, "username": who, "password": pw})
        c.get("/")
        with c.session_transaction() as s:
            tok = s.get("csrf")
        n = 0
        for url in get_urls:
            for k in [None] + params:
                for v in (ODD if k else [None]):
                    q = url if k is None else f"{url}{'&' if '?' in url else '?'}{k}={v}"
                    r = c.get(q)
                    n += 1
                    if r.status_code >= 500:
                        FAILS.append(f"{who} GET {q[:120]} -> {r.status_code}")
        for url, fields in posts.items():
            if url == "/settings/clear-data" and who == "owner":
                fields = ["understand", "confirm"]                 # never send the real password
            for f in fields or [None]:
                for v in ODD:
                    data = dict(base.get(url, {}), csrf=tok)
                    if f:
                        data[f] = v
                    r = c.post(url, data=data)
                    n += 1
                    if r.status_code >= 500:
                        FAILS.append(f"{who} POST {url} {f}={v[:40]!r} -> {r.status_code}")
        for url in [u for u in get_urls if not u.endswith(".csv")]:   # stored input never comes back as live HTML
            # (CSV exports hold raw text by design; they download as attachments with nosniff)
            body = c.get(url).get_data(as_text=True)
            if "<script>alert(1)</script>" in body:
                FAILS.append(f"{who} GET {url} shows unescaped script text")
        print(f"  {who:<13} {n} requests, {len([x for x in FAILS if x.startswith(who + ' ')])} server errors")
        c.post("/logout", data={"csrf": tok})
    db.close_all()
    shutil.rmtree(tmp, ignore_errors=True)
    for f in FAILS[:40]:
        print("  FAIL", f)
    print()
    print(f"FAILED: {len(FAILS)} server errors" if FAILS else "ALL PASSED")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
