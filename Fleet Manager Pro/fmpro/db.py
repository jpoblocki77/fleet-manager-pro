"""SQLite storage. One connection per thread, WAL mode, schema created on start."""
import json
import re
import sqlite3
import threading
import time

from . import tracelog

_local = threading.local()
_all = []                      # every connection opened, so close_all() can release the file
DB_PATH = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS divisions (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT DEFAULT '',
    color TEXT DEFAULT '#c8102e');

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'driver', division_id INTEGER REFERENCES divisions(id),
    status TEXT NOT NULL DEFAULT 'active', xp INTEGER NOT NULL DEFAULT 0,
    color TEXT DEFAULT '#c8102e', bio TEXT DEFAULT '', country TEXT DEFAULT '', game_pref TEXT DEFAULT 'both',
    discord TEXT DEFAULT '', steam_id TEXT DEFAULT '', truckersmp_id TEXT DEFAULT '',
    pay_per_km REAL, agent_token TEXT UNIQUE, failed_logins INTEGER DEFAULT 0,
    locked_until REAL DEFAULT 0, last_seen REAL, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL, discord TEXT DEFAULT '',
    age INTEGER, country TEXT DEFAULT '', games TEXT DEFAULT '', experience TEXT DEFAULT '',
    message TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', notes TEXT DEFAULT '',
    reviewed_by INTEGER REFERENCES users(id), created_at REAL NOT NULL, reviewed_at REAL);

CREATE TABLE IF NOT EXISTS garages (
    id INTEGER PRIMARY KEY, city TEXT NOT NULL, game TEXT DEFAULT 'ats', size TEXT DEFAULT 'small',
    slots INTEGER NOT NULL DEFAULT 3, upkeep_per_week REAL NOT NULL DEFAULT 0,
    purchase_price REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS trucks (
    id INTEGER PRIMARY KEY, game_truck_id TEXT DEFAULT '', brand TEXT NOT NULL, model TEXT NOT NULL,
    plate TEXT DEFAULT '', plate_country TEXT DEFAULT '', garage_id INTEGER REFERENCES garages(id),
    assigned_user_id INTEGER REFERENCES users(id), odometer REAL DEFAULT 0,
    purchase_price REAL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
    wear_engine REAL DEFAULT 0, wear_transmission REAL DEFAULT 0, wear_cabin REAL DEFAULT 0,
    wear_chassis REAL DEFAULT 0, wear_wheels REAL DEFAULT 0, last_service_odo REAL DEFAULT 0,
    service_interval_km REAL DEFAULT 50000, fuel_capacity REAL DEFAULT 0, notes TEXT DEFAULT '',
    auto_detected INTEGER DEFAULT 0, created_at REAL NOT NULL, last_seen REAL);

CREATE TABLE IF NOT EXISTS trailers (
    id INTEGER PRIMARY KEY, game_trailer_id TEXT DEFAULT '', name TEXT NOT NULL,
    body_type TEXT DEFAULT '', brand TEXT DEFAULT '', plate TEXT DEFAULT '',
    garage_id INTEGER REFERENCES garages(id), owned INTEGER DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active', wear REAL DEFAULT 0, purchase_price REAL DEFAULT 0,
    auto_detected INTEGER DEFAULT 0, created_at REAL NOT NULL, last_seen REAL);

CREATE TABLE IF NOT EXISTS maintenance (
    id INTEGER PRIMARY KEY, truck_id INTEGER REFERENCES trucks(id), trailer_id INTEGER REFERENCES trailers(id),
    kind TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0, odometer REAL, notes TEXT DEFAULT '',
    user_id INTEGER REFERENCES users(id), auto INTEGER DEFAULT 0, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, game TEXT DEFAULT 'ats',
    source_city TEXT DEFAULT '', destination_city TEXT NOT NULL, cargo TEXT DEFAULT '', cargo_id TEXT DEFAULT '',
    source_company TEXT DEFAULT '', destination_company TEXT DEFAULT '',
    source_city_id TEXT DEFAULT '', destination_city_id TEXT DEFAULT '',
    min_mass_t REAL DEFAULT 0, bonus REAL NOT NULL DEFAULT 0, priority TEXT DEFAULT 'normal',
    deadline REAL, assigned_user_id INTEGER REFERENCES users(id), status TEXT NOT NULL DEFAULT 'open',
    notes TEXT DEFAULT '', job_id INTEGER, created_by INTEGER REFERENCES users(id),
    created_at REAL NOT NULL, completed_at REAL);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    truck_id INTEGER REFERENCES trucks(id), contract_id INTEGER REFERENCES contracts(id),
    game TEXT DEFAULT 'ats', source TEXT NOT NULL DEFAULT 'telemetry',
    status TEXT NOT NULL DEFAULT 'in_progress',
    source_city TEXT DEFAULT '', source_company TEXT DEFAULT '',
    destination_city TEXT DEFAULT '', destination_company TEXT DEFAULT '',
    cargo TEXT DEFAULT '', cargo_id TEXT DEFAULT '', cargo_mass REAL DEFAULT 0, unit_count INTEGER DEFAULT 0,
    market TEXT DEFAULT '', special INTEGER DEFAULT 0, trailer_name TEXT DEFAULT '',
    planned_km REAL DEFAULT 0, driven_km REAL DEFAULT 0, odo_start REAL, odo_end REAL,
    income REAL DEFAULT 0, revenue REAL DEFAULT 0, driver_pay REAL DEFAULT 0, profit REAL DEFAULT 0,
    fuel_used REAL DEFAULT 0, fuel_cost REAL DEFAULT 0, tolls REAL DEFAULT 0, fines REAL DEFAULT 0,
    ferry REAL DEFAULT 0, train REAL DEFAULT 0, penalty REAL DEFAULT 0,
    cargo_damage REAL DEFAULT 0, truck_wear_start REAL DEFAULT 0, truck_wear_end REAL DEFAULT 0,
    max_speed REAL DEFAULT 0, avg_speed REAL DEFAULT 0, speeding_s REAL DEFAULT 0, driving_s REAL DEFAULT 0,
    game_xp INTEGER DEFAULT 0, xp INTEGER DEFAULT 0, score INTEGER DEFAULT 0,
    autopark INTEGER DEFAULT 0, autoload INTEGER DEFAULT 0, flags TEXT DEFAULT '',
    game_time_start INTEGER, game_time_deadline INTEGER, game_time_end INTEGER,
    notes TEXT DEFAULT '', started_at REAL NOT NULL, finished_at REAL,
    reviewed_by INTEGER REFERENCES users(id));
CREATE INDEX IF NOT EXISTS ix_jobs_user ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS ix_jobs_finished ON jobs(finished_at);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), user_id INTEGER REFERENCES users(id),
    kind TEXT NOT NULL, amount REAL DEFAULT 0, detail TEXT DEFAULT '', x REAL, z REAL,
    created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_job ON job_events(job_id);

CREATE TABLE IF NOT EXISTS job_points (
    job_id INTEGER NOT NULL REFERENCES jobs(id), t REAL NOT NULL, x REAL, z REAL, speed REAL);
CREATE INDEX IF NOT EXISTS ix_points_job ON job_points(job_id);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL DEFAULT 'company', user_id INTEGER REFERENCES users(id),
    category TEXT NOT NULL, amount REAL NOT NULL, description TEXT DEFAULT '',
    job_id INTEGER REFERENCES jobs(id), created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ledger_acct ON ledger(account, user_id, created_at);

CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY, lender TEXT NOT NULL DEFAULT 'Bank', principal REAL NOT NULL,
    apr REAL NOT NULL, weeks INTEGER NOT NULL, installment REAL NOT NULL, balance REAL NOT NULL,
    payments_made INTEGER NOT NULL DEFAULT 0, next_due REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT DEFAULT 'convoy', game TEXT DEFAULT 'ats',
    description TEXT DEFAULT '', start_at REAL NOT NULL, meetup TEXT DEFAULT '', departure TEXT DEFAULT '',
    destination TEXT DEFAULT '', server TEXT DEFAULT '', dlc TEXT DEFAULT '', min_level INTEGER DEFAULT 0,
    max_slots INTEGER DEFAULT 0, xp_reward INTEGER DEFAULT 0, status TEXT DEFAULT 'scheduled',
    created_by INTEGER REFERENCES users(id), created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS event_rsvps (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL, attended INTEGER DEFAULT 0,
    created_at REAL NOT NULL, PRIMARY KEY (event_id, user_id));

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, pinned INTEGER DEFAULT 0,
    level TEXT DEFAULT 'info', author_id INTEGER REFERENCES users(id), created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, from_id INTEGER REFERENCES users(id), to_id INTEGER REFERENCES users(id),
    body TEXT NOT NULL, read INTEGER DEFAULT 0, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS user_achievements (
    user_id INTEGER NOT NULL REFERENCES users(id), code TEXT NOT NULL, earned_at REAL NOT NULL,
    PRIMARY KEY (user_id, code));

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), kind TEXT DEFAULT 'info',
    text TEXT NOT NULL, link TEXT DEFAULT '', read INTEGER DEFAULT 0, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS hos_logs (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL,
    start_min INTEGER NOT NULL, end_min INTEGER, truck_id INTEGER, location TEXT DEFAULT '',
    auto INTEGER DEFAULT 1, note TEXT DEFAULT '', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_hos_user ON hos_logs(user_id, start_min);

CREATE TABLE IF NOT EXISTS hos_violations (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), rule TEXT NOT NULL,
    detail TEXT DEFAULT '', game_min INTEGER, period_key TEXT, fine REAL DEFAULT 0, created_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hos_viol ON hos_violations(user_id, rule, period_key);

CREATE TABLE IF NOT EXISTS dvirs (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), truck_id INTEGER REFERENCES trucks(id),
    kind TEXT NOT NULL, items TEXT NOT NULL, defects TEXT DEFAULT '', status TEXT NOT NULL,
    odometer REAL, game_min INTEGER, location TEXT DEFAULT '', created_at REAL NOT NULL,
    repaired_at REAL, repaired_by INTEGER);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), truck_id INTEGER, job_id INTEGER,
    level INTEGER NOT NULL, result TEXT NOT NULL, findings TEXT NOT NULL, fine REAL DEFAULT 0,
    game_min INTEGER, location TEXT DEFAULT '', created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS road_points (
    id INTEGER PRIMARY KEY, game TEXT NOT NULL, cx INTEGER NOT NULL, cz INTEGER NOT NULL,
    x REAL NOT NULL, z REAL NOT NULL, user_id INTEGER, t REAL NOT NULL, UNIQUE (game, cx, cz));

CREATE TABLE IF NOT EXISTS map_places (
    id INTEGER PRIMARY KEY, game TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE, x REAL NOT NULL, z REAL NOT NULL,
    visits INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL, UNIQUE (game, name));

CREATE TABLE IF NOT EXISTS login_failures (ip TEXT NOT NULL, username TEXT NOT NULL, t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_login_fail ON login_failures(ip, t);

CREATE TABLE IF NOT EXISTS fluid_samples (
    id INTEGER PRIMARY KEY, user_id INTEGER, t REAL NOT NULL, game TEXT DEFAULT '',
    fuel REAL DEFAULT 0, fuel_cap REAL DEFAULT 0, adblue REAL DEFAULT 0, adblue_cap REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_fluid_user_t ON fluid_samples(user_id, t);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, detail TEXT DEFAULT '',
    created_at REAL NOT NULL);
"""

STARTING_CAPITAL = 275000          # every new company starts with this; it can't be changed

DEFAULT_SETTINGS = {
    "company_name": "", "company_tag": "", "company_motto": "", "company_color": "#c8102e", "theme_navy": "", "theme_accent": "",
    "currency": "$", "units": "imperial", "default_game": "ats",
    "tablet_access": "0",               # 1 = tablets/phones on this network can connect (HTTPS on port+1)
    "fluid_alerts": "1",                # 1 = notify the driver and managers when fuel or AdBlue runs low
    "auto_update": "0",                 # 1 = install verified updates by themselves when nobody is driving
    "auto_backup": "1",                 # daily copy of fleet.db in data/backups (newest 14 kept)
    "fuel_price": "1.25",               # per litre, used to cost fuel burnt on a job
    "driver_pay_per_km": "0.40",        # paid to the driver from company revenue
    "driver_commission_pct": "0",       # optional % of revenue on top of per-km pay
    "xp_per_km": "1.0", "xp_per_tonne": "2.0",
    "speeding_tolerance_kmh": "5",
    "auto_create_trucks": "1", "auto_charge_repairs": "1", "repair_cost_per_pct": "40",
    # price list shown in Record maintenance (a repair is priced from the truck's wear instead)
    "maint_price_service": "800", "maint_price_tyres": "2400", "maint_price_inspection": "150",
    "maint_price_upgrade": "0", "maint_price_wash": "40", "dvir_repair_per_defect": "250",
    "agent_jobs_need_approval": "0",    # hold every remote driver's delivery for a manager
    "require_manual_approval": "1",     # manual (non-telemetry) jobs wait for approval
    "telemetry_mode": "auto",           # auto | demo | off
    "telemetry_user_id": "",            # who local telemetry is credited to
    "discord_webhook": "", "webhook_on_delivery": "1", "webhook_on_event": "1",
    "webhook_applications": "", "webhook_on_application": "1",   # new driver applications: own channel optional
    "recruitment_open": "1", "recruitment_text": "We're hiring professional, friendly drivers!",
    "global_board": "1", "global_board_drivers": "0",   # the global leaderboard: join it / share driver names
    "starting_balance": str(STARTING_CAPITAL), "initialized": "0",
    # compliance (ELD / Hours of Service, DVIR, roadside inspections)
    "hos_enabled": "1", "hos_rules_ats": "us70", "hos_rules_ets2": "eu",
    "hos_fine": "500", "dvir_required": "1", "dvir_fine": "250",
    "inspections_enabled": "1", "inspection_chance_pct": "15", "oos_fine": "1500",
}


def init(path):
    global DB_PATH
    DB_PATH = path
    c = conn()
    c.executescript(SCHEMA)
    # columns added after a database was first created
    for table, col, decl in (("users", "game_pref", "TEXT DEFAULT 'both'"), ("trucks", "game", "TEXT"),
                             ("contracts", "cargo_id", "TEXT DEFAULT ''"),
                             ("contracts", "source_company", "TEXT DEFAULT ''"),
                             ("contracts", "destination_company", "TEXT DEFAULT ''"),
                             ("contracts", "source_city_id", "TEXT DEFAULT ''"),
                             ("contracts", "destination_city_id", "TEXT DEFAULT ''"),
                             ("hos_logs", "game", "TEXT"), ("hos_violations", "game", "TEXT"), ("dvirs", "game", "TEXT"),
                             ("contracts", "game_slot", "TEXT DEFAULT ''"), ("contracts", "sent_at", "REAL"),
                             # 0.02: pay deductions (cargo damage / late), expenses, garages from the game's list
                             ("jobs", "deduction", "REAL DEFAULT 0"), ("jobs", "deduction_reason", "TEXT DEFAULT ''"),
                             ("ledger", "truck_id", "INTEGER"), ("ledger", "driver_id", "INTEGER"),
                             ("ledger", "manual", "INTEGER DEFAULT 0"), ("ledger", "entered_by", "INTEGER"),
                             ("garages", "city_id", "TEXT DEFAULT ''"), ("garages", "notes", "TEXT DEFAULT ''"),
                             ("applications", "remote_id", "INTEGER"), ("applications", "source", "TEXT DEFAULT 'local'"),
                             ("event_rsvps", "xp_awarded", "INTEGER DEFAULT 0"),
                             # 1.4.4: a simulated rest (minutes) - a zero-length marker line the HOS clocks count as rest
                             ("hos_logs", "sim_rest", "INTEGER DEFAULT 0")):
        if col not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            if col == "game" and table in ("hos_logs", "hos_violations", "dvirs"):
                c.execute(f"UPDATE {table} SET game='ats' WHERE game IS NULL")     # logbooks before the split  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
            if (table, col) == ("event_rsvps", "xp_awarded"):                  # XP already paid before 1.01
                c.execute("""UPDATE event_rsvps SET xp_awarded=COALESCE((SELECT xp_reward FROM events
                             WHERE events.id=event_rsvps.event_id AND events.status='completed'), 0) WHERE attended=1""")
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    c.execute("UPDATE settings SET value=? WHERE key='starting_balance'", (str(STARTING_CAPITAL),))   # locked
    c.commit()
    backfill_map()
    backfill_deductions()
    from . import leasing, trailer_leasing
    leasing.init()
    trailer_leasing.init()
    if setting("repair_cost_lowered") != "1":            # 0.02: 180 per 1% wear made repairs cost far too much
        if setting("repair_cost_per_pct") == "180":
            set_setting("repair_cost_per_pct", "40")
        set_setting("repair_cost_lowered", "1")


def deduction_reason(job):
    """Why the game paid less than the job offered."""
    if (job.get("cargo_damage") or 0) > 0:
        return "cargo damage"
    if job.get("game_time_deadline") and job.get("game_time_end") and job["game_time_end"] > job["game_time_deadline"]:
        return "late delivery"
    return "pay deduction"


def backfill_deductions():
    """0.02: deliveries booked before pay deductions were shown. The game pays the offer minus cargo damage
    (or lateness); the ledger had only the net amount. Book the offer as freight and the deduction as an
    expense - same balance, but the $ lost to damage is now visible in the logbook and on Expenses."""
    if setting("deductions_backfilled") == "1":
        return
    c = conn()
    rows = c.execute("""SELECT * FROM jobs WHERE status='delivered' AND source='telemetry' AND revenue > 0
                        AND income > revenue AND COALESCE(deduction, 0) = 0""").fetchall()
    for r in rows:
        j = dict(r)
        amount = round(j["income"] - j["revenue"], 2)
        reason = deduction_reason(j)
        led = c.execute("SELECT id, created_at FROM ledger WHERE job_id=? AND category='freight' AND account='company' "
                        "AND ABS(amount - ?) < 0.01 ORDER BY id LIMIT 1", (j["id"], j["revenue"])).fetchone()
        if led:
            c.execute("UPDATE ledger SET amount=? WHERE id=?", (round(j["income"], 2), led[0]))
            c.execute("INSERT INTO ledger(account, category, amount, description, job_id, truck_id, driver_id, created_at) "
                      "VALUES ('company', ?, ?, ?, ?, ?, ?, ?)",
                      (reason, -amount, _deduction_text(j, reason), j["id"], j["truck_id"], j["user_id"], led[1]))
        c.execute("UPDATE jobs SET deduction=?, deduction_reason=? WHERE id=?", (amount, reason, j["id"]))
    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('deductions_backfilled', '1')")
    c.commit()


def _deduction_text(j, reason):
    route = f"{j['source_city']} → {j['destination_city']}"
    if reason == "cargo damage":
        return f"Cargo damage {j['cargo_damage'] * 100:.1f}% on job #{j['id']} {route}"
    return f"{reason.capitalize()} on job #{j['id']} {route}"


def backfill_map():
    """First start with the map: turn the route trails of earlier (real, non-demo) jobs into roads and places."""
    if setting("map_backfilled") == "1":
        return
    c = conn()
    rows = c.execute("""SELECT j.game, p.x, p.z, j.user_id, p.t FROM job_points p JOIN jobs j ON j.id=p.job_id
                        WHERE j.source='telemetry' AND COALESCE(j.flags,'') NOT LIKE '%demo%' AND j.game IN ('ats','ets2')
                        AND (p.x != 0 OR p.z != 0) ORDER BY p.job_id, p.t""").fetchall()
    for g, x, z, uid, t in rows:
        c.execute("INSERT OR IGNORE INTO road_points(game, cx, cz, x, z, user_id, t) VALUES (?,?,?,?,?,?,?)",
                  (g, int(x // 400), int(z // 400), x, z, uid, t))
    ends = c.execute("""SELECT j.game, j.destination_city, p.x, p.z FROM jobs j JOIN job_points p ON p.job_id=j.id
                        WHERE j.status='delivered' AND j.source='telemetry' AND COALESCE(j.flags,'') NOT LIKE '%demo%'
                        AND j.destination_city != '' AND j.game IN ('ats','ets2')
                        AND p.t = (SELECT MAX(t) FROM job_points WHERE job_id=j.id)""").fetchall()
    for g, name, x, z in ends:
        c.execute("INSERT OR IGNORE INTO map_places(game, name, x, z, updated_at) VALUES (?,?,?,?,?)",
                  (g, name, x, z, time.time()))
    c.commit()
    set_setting("map_backfilled", "1")


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
        _all.append(c)
    return c


def close_all():
    """Close every thread's connection (used at shutdown and by the tests)."""
    for c in list(_all):
        try:
            c.close()
        except sqlite3.Error:
            pass
    _all.clear()
    _local.conn = None


def q(sql, args=()):
    t = time.perf_counter()
    rows = [dict(r) for r in conn().execute(sql, args).fetchall()]
    tracelog.db_read(sql, args, (time.perf_counter() - t) * 1000, len(rows))
    return rows


def one(sql, args=()):
    t = time.perf_counter()
    r = conn().execute(sql, args).fetchone()
    tracelog.db_read(sql, args, (time.perf_counter() - t) * 1000, int(r is not None))
    return dict(r) if r else None


def val(sql, args=(), default=None):
    t = time.perf_counter()
    r = conn().execute(sql, args).fetchone()
    tracelog.db_read(sql, args, (time.perf_counter() - t) * 1000, int(r is not None))
    return default if r is None or r[0] is None else r[0]


def ex(sql, args=(), _cols=None):
    t = time.perf_counter()
    c = conn()
    try:
        cur = c.execute(sql, args)
        c.commit()
    except Exception as e:
        tracelog.db_error(sql, args, e, _cols)
        raise
    tracelog.db_write(sql, args, (time.perf_counter() - t) * 1000, _cols, cur.lastrowid)
    return cur.lastrowid


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def ident(*names):
    """Refuse anything but plain SQL identifiers (table / column names are only ever written in the code, never taken
    from a request - this makes sure that stays true)."""
    for n in names:
        if not _IDENT.match(str(n)):
            raise ValueError(f"bad SQL identifier: {n!r}")
    return names[0] if len(names) == 1 else names


def insert(table, **cols):
    ident(table, *cols)
    keys = ",".join(cols)
    marks = ",".join("?" * len(cols))
    return ex(f"INSERT INTO {table} ({keys}) VALUES ({marks})", tuple(cols.values()), _cols=list(cols))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


def update(table, row_id, **cols):
    if not cols:
        return
    ident(table, *cols)
    sets = ",".join(f"{k}=?" for k in cols)
    ex(f"UPDATE {table} SET {sets} WHERE id=?", tuple(cols.values()) + (row_id,), _cols=list(cols))  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter


# ------------------------------------------------------------------ settings
_settings_lock = threading.Lock()


def setting(key, default=None):
    v = val("SELECT value FROM settings WHERE key=?", (key,))
    return default if v is None else v


def setting_f(key, default=0.0):
    try:
        return float(setting(key, default))
    except (TypeError, ValueError):
        return default


def set_setting(key, value):
    with _settings_lock:
        ex("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
           (key, str(value)))


def all_settings():
    return {r["key"]: r["value"] for r in q("SELECT key, value FROM settings")}


def audit(user_id, action, detail=""):
    insert("audit_log", user_id=user_id, action=action,
           detail=detail if isinstance(detail, str) else json.dumps(detail), created_at=time.time())


def notify(user_id, text, link="", kind="info"):
    insert("notifications", user_id=user_id, text=text, link=link, kind=kind, created_at=time.time())
