"""Developer tool: build a demo company with a month of believable history, for showing the program off.

    python tools/make_demo_data.py <demo data folder> [--fresh]

Everything goes through the app's own code (its pages and the telemetry tracker), so the books, XP, logbook and
stats add up exactly as they would after real driving. The clock is moved back four weeks while it runs, so the
history, weekly payments and upkeep are spread over the last month.

Safety: it only ever writes into a folder it made itself (marked with a DEMO_DATA file). It refuses any folder
that already holds a company without that mark, so it can't touch a real company's data.
"""
import os
import shutil
import sys
import time as _real_time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

MARK = "DEMO_DATA"
DAY = 86400.0
OWNER = ("demo", "Demo Owner", "demo-owner-26")
DRIVERS = [  # username, name, role, country, bio
    ("hank", "Hank Calloway", "admin", "United States", "Twenty years on the road. Runs the yard and the schedule."),
    ("rosa", "Rosa Delgado", "dispatcher", "United States", "Keeps every load moving. Coffee first, questions later."),
    ("carla", "Carla Nguyen", "dispatcher", "United States", "Second dispatcher. Runs the east board and night loads."),
    ("marcus", "Marcus Webb", "driver", "Canada", "Heavy haul specialist. Never met a hill he didn't like."),
    ("tina", "Tina Brooks", "driver", "United States", "West coast regular. Night runs and clean logs."),
    ("vic", "Vic Romano", "driver", "United States", "Reefer runs up and down I-5. Always early."),
    ("deej", "DeAndre Price", "driver", "United States", "Flatbed and heavy. Straps checked twice."),
    ("priya", "Priya Nair", "driver", "Canada", "Cross-border regular. Paperwork always in order."),
    ("boomer", "Bo Hendricks", "driver", "United States", "Old school. Knows every truck stop west of Denver."),
    ("earl", "Earl Jennings", "trainee", "United States", "New to the company and learning fast."),
    ("shane", "Shane O'Dell", "trainee", "United States", "Second-week trainee. Keen and careful."),
]
DRIVER_PASSWORD = "demo-driver-26"  # nosec B105  # sample password for throw-away demo accounts, not a real secret


class Clock:
    """The time module, with time() moved to wherever the demo's story needs it."""

    def __init__(self, start):
        self.t = start

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(_real_time, name)


def prepare(folder, fresh):
    folder = os.path.abspath(folder)
    has_db = os.path.exists(os.path.join(folder, "fleet.db"))
    marked = os.path.exists(os.path.join(folder, MARK))
    if has_db and not marked:
        sys.exit(f"Refusing: {folder} holds a company that isn't demo data.")
    if has_db and not fresh:
        sys.exit(f"{folder} already has demo data. Use --fresh to rebuild it.")
    if has_db:
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MARK), "w", encoding="utf-8") as fh:
        fh.write("Demo company made by tools/make_demo_data.py. Safe to delete or rebuild.\n")
    return folder


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    folder = prepare(args[0], "--fresh" in sys.argv)

    DAYS = int(os.environ.get("DEMO_DAYS", "34"))   # weeks of simulated history; set DEMO_DAYS=0 to dry-run the fleet/finance setup
    clock = Clock(_real_time.time() - (DAYS + 2) * DAY)
    import fmpro
    for name, mod in list(sys.modules.items()):   # every fmpro module that imported time now reads the story clock
        if name.startswith("fmpro") and getattr(mod, "time", None) is _real_time:
            mod.time = clock
    from fmpro import create_app, db
    from fmpro.telemetry import demo, reader, service

    app = create_app(folder, start_telemetry=False, check_licence=False)
    app.testing = True
    c = app.test_client()

    def post(url, data=None):
        with c.session_transaction() as s:
            d = dict(data or {}, csrf=s.get("csrf"))
        r = c.post(url, data=d)
        if r.status_code >= 400:
            print(f"  ! {url} -> {r.status_code}")
        return r

    def login(user, pw):
        c.get("/logout")
        c.get("/login")
        post("/login", {"username": user, "password": pw})
        c.get("/")                                 # settle a fresh CSRF token before the first post after login

    # ---------------------------------------------------------------- company
    c.get("/setup")
    post("/setup", {"company_name": "McCoy Trucking Co.", "company_tag": "MCT", "username": OWNER[0],
                    "display_name": OWNER[1], "password": OWNER[2], "password2": OWNER[2],
                    "units": "imperial", "currency": "$", "default_game": "ats"})
    if db.setting("initialized") != "1":
        sys.exit("Setup didn't complete - stopping.")
    owner_id = db.val("SELECT id FROM users WHERE username=?", (OWNER[0],))
    db.update("users", owner_id, game_pref="ats", country="United States",
              bio="Owner of McCoy Trucking Co. Keep the shiny side up.")
    c.get("/")                                    # first request after setup settles the owner's session (so admins can be added)

    ids = {}
    for user, name, role, country, bio in DRIVERS:
        post("/drivers/new", {"username": user, "display_name": name, "password": DRIVER_PASSWORD, "role": role})
        ids[user] = db.val("SELECT id FROM users WHERE username=?", (user,))
        db.update("users", ids[user], game_pref="ats", country=country, bio=bio)
    print(f"company + {len(ids) + 1} members")

    # ---------------------------------------------------------------- fleet and money
    clock.t += 0.2 * DAY
    post("/finance/loan", {"plan": "expansion", "amount": "600000"})
    post("/fleet/garage/save", {"city": "Los Angeles", "size": "small"})
    post("/fleet/truck/save", {"brand": "Peterbilt", "model": "579", "plate": "MCT 101", "purchase_price": "148000"})
    post("/fleet/truck/save", {"brand": "Kenworth", "model": "T680", "plate": "MCT 104", "purchase_price": "142000"})
    post("/fleet/trailer/save", {"name": "53ft Dry Van", "purchase_price": "38000"})
    post("/fleet/trailer/save", {"name": "48ft Reefer", "purchase_price": "52000"})
    post("/fleet/trailer/save", {"name": "48ft Flatbed", "purchase_price": "34000"})
    post("/fleet/trailer/save", {"name": "53ft Reefer", "purchase_price": "56000"})
    plans = {p["name"]: p["id"] for p in db.q("SELECT id, name FROM lease_plans")} if db.val(
        "SELECT name FROM sqlite_master WHERE name='lease_plans'") else {}
    for plan, brand, model, price in [("Long Haul", "Kenworth", "W900", "165000"),
                                      ("Regional", "Freightliner", "Cascadia", "155000"),
                                      ("Long Haul", "Peterbilt", "389", "172000"),
                                      ("Regional", "International", "LT", "149000"),
                                      ("Long Haul", "Volvo", "VNL 860", "151000")]:
        if plan in plans:
            post("/leasing/new", {"plan_id": str(plans[plan]), "price": price, "source": "new",
                                  "brand": brand, "model": model})
    trucks = [t["id"] for t in db.q("SELECT id FROM trucks ORDER BY id")]
    for tid, user in zip(trucks, ["marcus", "tina", "hank", "vic", "deej", "priya", "boomer"]):
        db.update("trucks", tid, assigned_user_id=ids[user])
    bought = db.val("SELECT COUNT(*) FROM trucks WHERE COALESCE(leased, 0) = 0")
    ntrailers = db.val("SELECT COUNT(*) FROM trailers")
    print(f"garage, {len(trucks)} trucks ({bought} bought, {len(trucks) - bought} leased), {ntrailers} trailers, loan")

    # ---------------------------------------------------------------- a month of driving
    roster = [owner_id, ids["marcus"], ids["tina"], ids["vic"], ids["deej"], ids["priya"],
              ids["hank"], ids["boomer"], ids["earl"], ids["shane"], ids["marcus"], ids["tina"]]
    trackers, sims = {}, {}
    delivered = 0
    for day in range(DAYS):
        for n in range(2 if day % 3 else 1):
            uid = roster[(day * 2 + n) % len(roster)]
            if uid not in trackers:
                trackers[uid], sims[uid] = service.JobTracker(uid), demo.Simulator()
            target = db.val("SELECT COUNT(*) FROM jobs WHERE status IN ('delivered','pending_review')") + 1
            for i in range(80000):
                clock.t += 0.25
                trackers[uid].feed(reader.parse(sims[uid].step()))
                if i % 100 == 0 and db.val(
                        "SELECT COUNT(*) FROM jobs WHERE status IN ('delivered','pending_review')") >= target:
                    delivered += 1
                    break
            clock.t += 3 * 3600
        clock.t = max(clock.t, _real_time.time() - (DAYS - day) * DAY)
        login(OWNER[0], OWNER[2])
        c.get("/")                                   # lets the app run its weekly charges on the story's dates
    print(f"{delivered} deliveries over the last month")

    # ---------------------------------------------------------------- maintenance and compliance
    from fmpro import compliance
    maint = [(trucks[0], "service", "850"), (trucks[0], "tyres", "2400")]
    if len(trucks) > 1:
        maint += [(trucks[1], "service", "720"), (trucks[1], "brakes", "1300")]
    if len(trucks) > 3:
        maint += [(trucks[3], "service", "680"), (trucks[3], "tyres", "2250")]
    for tid, kind, cost in maint:
        post("/fleet/maintenance", {"truck_id": str(tid), "kind": kind, "cost": cost})
    ok_form = {f"item_{k}": "ok" for k, *_ in compliance.DVIR_ITEMS}
    for who, idx in [("tina", 1), ("vic", 2), ("deej", 3), ("marcus", 0)]:
        if len(trucks) > idx:
            login(who, DRIVER_PASSWORD)
            post("/compliance/dvir", dict(ok_form, kind="pre", truck_id=str(trucks[idx]), signed="1"))
    # one report with a defect, so the compliance board shows a real find
    if len(trucks) > 2:
        login("boomer", DRIVER_PASSWORD)
        defect_form = dict(ok_form, kind="post", truck_id=str(trucks[2]), signed="1")
        key0 = compliance.DVIR_ITEMS[0][0]
        defect_form[f"item_{key0}"] = "defect"
        defect_form[f"note_{key0}"] = "Marker light out on the driver side."
        post("/compliance/dvir", defect_form)
    login(OWNER[0], OWNER[2])

    # ---------------------------------------------------------------- dispatch board
    clock.t = _real_time.time() - 0.5 * DAY
    for title, src, dst, cargo, bonus, hours in [
            ("Lumber to Reno", "sacramento|Sacramento", "reno|Reno", "lumber|Lumber", "750", "48"),
            ("Hot load: electronics to Phoenix", "los_angeles|Los Angeles", "phoenix|Phoenix", "electronics|Electronics", "1500", "24"),
            ("Hay for El Paso", "albuquerque|Albuquerque", "el_paso|El Paso", "hay|Hay", "400", "72"),
            ("Excavator to Las Vegas", "bakersfield|Bakersfield", "las_vegas|Las Vegas", "excavator|Excavator", "2200", "36"),
            ("Frozen food to San Diego", "los_angeles|Los Angeles", "san_diego|San Diego", "frozen_food|Frozen Food", "600", "20"),
            ("Iron pipes to Denver", "salt_lake|Salt Lake City", "denver|Denver", "pipes|Iron Pipes", "1800", "60"),
            ("Logs run: Seattle to Portland", "seattle|Seattle", "portland|Portland", "logs|Logs", "500", "30"),
            ("Gravel to Tucson", "phoenix|Phoenix", "tucson|Tucson", "gravel|Gravel", "350", "18"),
            ("Furniture to Dallas", "houston|Houston", "dallas|Dallas", "furniture|Furniture", "900", "40"),
            ("Grain haul: Tulsa to Amarillo", "tulsa|Tulsa", "amarillo|Amarillo", "grain|Grain", "700", "54")]:
        post("/dispatch/new", {"title": title, "game": "ats", "source_city_pick": src,
                               "destination_city_pick": dst, "cargo_pick": cargo, "bonus": bonus, "deadline_hours": hours})
    print(f"{db.val('SELECT COUNT(*) FROM contracts')} contracts")

    # ---------------------------------------------------------------- community
    clock.t = _real_time.time() - 3 * DAY
    post("/announcements", {"title": "Welcome to McCoy Trucking Co.!",
                            "body": "Glad to have you on board. Check the Dispatch board for open loads, keep your "
                                    "logs clean, and keep the shiny side up.", "pinned": "1"})
    post("/announcements", {"title": "New Peterbilt in the yard",
                            "body": "MCT 101 is back from service with new tyres. Marcus, she's yours."})
    post("/announcements", {"title": "Winter driving - stay sharp",
                            "body": "Chains in the side box, check your tyres before the passes, and slow it down "
                                    "on the grades. No load is worth it. Report any defect on your DVIR."})
    post("/announcements", {"title": "Driver of the month: Tina",
                            "body": "Most clean miles and a perfect log sheet this month. Nice work, Tina - "
                                    "drinks are on the company at the next convoy stop."})
    soon = _real_time.localtime(_real_time.time() + 4 * DAY)
    later = _real_time.localtime(_real_time.time() + 11 * DAY)
    third = _real_time.localtime(_real_time.time() + 18 * DAY)
    post("/events/save", {"title": "Friday Night Convoy: LA to Vegas", "start_at": _real_time.strftime("%Y-%m-%dT20:00", soon),
                          "departure": "Los Angeles", "destination": "Las Vegas", "xp_reward": "500"})
    post("/events/save", {"title": "Desert Run: Phoenix to El Paso", "start_at": _real_time.strftime("%Y-%m-%dT19:00", later),
                          "departure": "Phoenix", "destination": "El Paso", "xp_reward": "750"})
    post("/events/save", {"title": "Pacific Coast Cruise: Seattle to Portland", "start_at": _real_time.strftime("%Y-%m-%dT18:00", third),
                          "departure": "Seattle", "destination": "Portland", "xp_reward": "600"})
    post("/messages", {"body": "Great month everyone - revenue is up. Convoy on Friday, don't be late!"})
    login("rosa", DRIVER_PASSWORD)
    post("/messages", {"body": "Two new loads on the board. The Phoenix electronics run pays a bonus."})
    login("tina", DRIVER_PASSWORD)
    post("/messages", {"body": "I'll take the San Diego reefer run. Back by the weekend."})
    login("carla", DRIVER_PASSWORD)
    post("/messages", {"body": "Heads up: Denver pipes load is urgent, needs a flatbed. Who's open?"})
    login("deej", DRIVER_PASSWORD)
    post("/messages", {"body": "I've got the flatbed. I'll grab the Denver run."})
    login(OWNER[0], OWNER[2])
    now = _real_time.time()
    apps = [
            ("Dale Morrison", "dale.m#4821", 34, "United States", "3 years in ATS", "Looking for a laid-back company to run with.", 2),
            ("Sofia Reyes", "sofiar#1290", 27, "Mexico", "1 year, mostly ETS2", "Want to try ATS with a crew.", 1),
            ("Kyle Benton", "kbenton#7733", 41, "United States", "5 years, VTC experience", "Can drive nights and weekends.", 0.3),
            ("Marcus Lind", "mlind#2048", 29, "United States", "2 years, solo flatbed", "Looking to run regional, straps are my thing.", 4),
            ("Jen Park", "jpark#5561", 33, "Canada", "4 years, reefer", "Clean record, prefer west coast lanes.", 3),
            ("Omar Haddad", "ohaddad#8820", 38, "United States", "6 years OTR", "Heavy haul and oversize certified.", 5),
            ("Becca Stone", "bstone#0417", 24, "United States", "New, just finished CDL", "Eager to learn from a good crew.", 0.6)]
    for name, contact, age, country, exp, msg, ago in apps:
        db.insert("applications", name=name, contact=contact, discord=contact, age=age, country=country, games="ATS",
                  experience=exp, message=msg, created_at=now - ago * DAY)
    print(f"announcements, 3 upcoming convoys, messages, {len(apps)} job applications")

    for name, mod in list(sys.modules.items()):     # back to the real clock
        if name.startswith("fmpro") and getattr(mod, "time", None) is clock:
            mod.time = _real_time
    bal = db.val("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE account='company'")
    print(f"done: company balance ${bal:,.2f}")


if __name__ == "__main__":
    main()
