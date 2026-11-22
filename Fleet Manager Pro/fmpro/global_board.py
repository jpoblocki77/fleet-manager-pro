"""Global leaderboard: every Fleet Manager Pro company ranked together on the licence server.

Every few minutes this copy sends its company name and totals (distance, revenue, deliveries, tonnage, average score,
XP, realism, drivers who delivered) for the last week / month / year / all time, per game, and gets back every company
on the board. Driver names are only sent when the company turns that on. The board is kept here so the page works
offline and shows when it was last updated. Leaving the board deletes this company's entry on the server."""
import json
import logging
import threading
import time

from . import db, licensing

EVERY = 10 * 60                     # seconds between updates
DAY = 86400
PERIODS = {"week": 7 * DAY, "month": 30 * DAY, "year": 365 * DAY, "all": None}
GAMES = ("all", "ats", "ets2")
# the order of the numbers the server stores for each period and game
METRICS = ("km", "revenue", "jobs", "mass", "score", "xp", "realism", "drivers")
TOP_DRIVERS = 10
_lock = threading.Lock()

_REALISM = "j.driven_km * (j.score/100.0) * (j.score/100.0) * (1 - j.cargo_damage)"
_WHERE = ("j.status='delivered' AND j.finished_at>=? AND u.status!='removed' "
          "AND (j.flags IS NULL OR j.flags NOT LIKE '%demo%')")      # simulator test drives never count


def _since(period, now):
    span = PERIODS[period]
    return now - span if span else 0


def _nums(r):
    return [round(r[0] or 0), round(r[1] or 0), int(r[2] or 0), round((r[3] or 0) / 1000.0, 1),
            round(r[4] or 0, 1), int(r[5] or 0), round(r[6] or 0), int(r[7] or 0)]


def collect(share_drivers=False, now=None):
    """This company's totals, shaped the way the server stores them: (stats, drivers)."""
    now = now or time.time()
    stats, drivers = {}, {}
    for per in PERIODS:
        since = _since(per, now)
        stats[per] = {}
        for g in GAMES:
            gsql, args = ("", (since,)) if g == "all" else (" AND j.game=?", (since, g))
            r = db.conn().execute(f"""SELECT SUM(j.driven_km), SUM(j.revenue), COUNT(*), SUM(j.cargo_mass), AVG(j.score),
                                      SUM(j.xp), SUM({_REALISM}), COUNT(DISTINCT j.user_id)
                                      FROM jobs j JOIN users u ON u.id=j.user_id WHERE {_WHERE}{gsql}""",  # nosec B608  # fixed SQL text; every value is a ? parameter
                                  args).fetchone()
            stats[per][g] = _nums(r)
        if share_drivers:
            rows = db.conn().execute(f"""SELECT u.display_name, SUM(j.driven_km), SUM(j.revenue), COUNT(*), SUM(j.cargo_mass),
                                         AVG(j.score), SUM(j.xp), SUM({_REALISM}), 1
                                         FROM jobs j JOIN users u ON u.id=j.user_id WHERE {_WHERE}
                                         GROUP BY u.id ORDER BY SUM(j.driven_km) DESC LIMIT ?""",  # nosec B608  # fixed SQL text; every value is a ? parameter
                                     (since, TOP_DRIVERS)).fetchall()
            drivers[per] = [{"name": str(r[0] or "")[:40], "v": _nums(r[1:])} for r in rows if r[0]]
    return stats, drivers


def joined():
    return db.setting("global_board", "1") == "1"


def sync():
    """Send this company's totals and fetch the whole board. Returns an error message, or None."""
    if not licensing.licensed():
        return "Fleet Manager Pro isn't activated, so the global leaderboard is off."
    with _lock:
        lic = licensing.LICENCE
        join = joined()
        share = db.setting("global_board_drivers", "0") == "1"
        stats, drivers = collect(share) if join else ({}, {})
        body = {"key": lic.key, "machine": lic.machine, "product": licensing.PRODUCT, "join": join,
                "company": (db.setting("company_name", "") or "").strip()[:60],
                "currency": (db.setting("currency", "$") or "")[:3], "stats": stats, "drivers": drivers}
        if len(json.dumps(body)) > 9500:                    # the server takes at most 10,000 bytes
            for per in drivers:
                drivers[per] = drivers[per][:5]
            if len(json.dumps(body)) > 9500:
                body["drivers"] = {}
        try:
            d = licensing._post("/v1/leaderboard", body, timeout=15)
        except licensing.ServerRefused as e:
            db.set_setting("global_board_error", e.message[:300])
            return e.message
        except ConnectionError as e:
            db.set_setting("global_board_error", str(e)[:300])
            return str(e)
        board = {"you": d.get("you"), "companies": d.get("companies") or [], "drivers": d.get("drivers") or {}}
        db.set_setting("global_board_cache", json.dumps(board, separators=(",", ":")))
        db.set_setting("global_board_url", str(d.get("page_url") or "")[:200])
        db.set_setting("global_board_synced", str(time.time()))
        db.set_setting("global_board_error", "")
        return None


def _num(v, i):
    try:
        x = float(v[i])
    except (TypeError, ValueError, IndexError):
        return 0.0
    return x if x == x else 0.0


def board(metric="km", period="month", game="all"):
    """The cached board ranked by one measure: (companies, drivers). Each row has value, jobs, km, drivers."""
    mi = METRICS.index(metric) if metric in METRICS else 0
    period = period if period in PERIODS else "month"
    game = game if game in GAMES else "all"
    try:
        data = json.loads(db.setting("global_board_cache", "") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    you = data.get("you")
    rows = []
    for c in data.get("companies") or []:
        if not isinstance(c, dict):
            continue
        v = ((c.get("stats") or {}).get(period) or {}).get(game) if isinstance(c.get("stats"), dict) else None
        if not isinstance(v, list) or not _num(v, 2):
            continue
        rows.append({"company": str(c.get("company") or "")[:60], "currency": str(c.get("currency") or "")[:3],
                     "value": _num(v, mi), "jobs": int(_num(v, 2)), "km": _num(v, 0), "drivers": int(_num(v, 7)),
                     "you": bool(you) and c.get("id") == you})
    rows.sort(key=lambda r: -r["value"])
    drivers = []
    shared = data.get("drivers") if isinstance(data.get("drivers"), dict) else {}
    if game == "all" and isinstance(shared.get(period), list):
        for d in shared[period]:
            if isinstance(d, dict) and isinstance(d.get("v"), list):
                drivers.append({"name": str(d.get("name") or "")[:40], "company": str(d.get("company") or "")[:60],
                                "currency": str(d.get("currency") or "")[:3],
                                "value": _num(d["v"], mi), "jobs": int(_num(d["v"], 2)), "km": _num(d["v"], 0),
                                "you": bool(you) and d.get("id") == you})
        drivers.sort(key=lambda r: -r["value"])
    return rows[:100], drivers[:25]


def start():
    """Update in the background: shortly after start, then every 10 minutes."""
    def loop():
        stop = threading.Event()
        stop.wait(45)
        while True:
            try:
                sync()
            except Exception as e:                           # never let a hiccup stop the loop
                logging.getLogger(__name__).warning("global leaderboard sync failed: %s", e)
            stop.wait(EVERY)
    threading.Thread(target=loop, daemon=True, name="global-board").start()
