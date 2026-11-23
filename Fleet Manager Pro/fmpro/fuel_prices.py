"""What diesel and AdBlue cost, and the record of every fill-up.

  * Diesel: the game's own price in the state or country the truck is in (bundled, tools/build_fuel_prices.py), or the
    company's price (Settings, Economy) where the game has none or the switch is off.
  * AdBlue: the games fill it for free, so the company sets its price.

The telemetry doesn't say which state the truck is in: it is the state of the nearest city, from the bundled map and
the cities learned from driving.
"""
import json
import logging
import math
import os
import re
import time
import unicodedata

from . import RES, db, economy

log = logging.getLogger("fmpro.fluids")
FAR = 14_000                        # game metres: further than this from any known city, the state isn't guessed
LABEL = {"fuel": "Diesel", "adblue": "AdBlue"}
# bundled map cities in countries the game's city list doesn't cover, or spelled another way there
MORE = {"ats": {}, "ets2": {"newcastle": "United Kingdom", "frankfurt": "Germany", "st. petersburg": "Russia", "zagreb": "Croatia",
        "split": "Croatia", "ljubljana": "Slovenia", "belgrade": "Serbia", "sarajevo": "Bosnia", "athens": "Greece",
        "thessaloniki": "Greece", "skopje": "Macedonia", "tirana": "Albania", "podgorica": "Montenegro", "pristina": "Kosovo"}}
_static = None                      # (prices, city -> state, bundled cities)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fuel_fills (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, t REAL NOT NULL, game TEXT DEFAULT '', kind TEXT NOT NULL,
    litres REAL NOT NULL, price REAL NOT NULL, cost REAL NOT NULL, place TEXT DEFAULT '', from_pct REAL, to_pct REAL,
    job_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_fuel_fills ON fuel_fills(user_id, t);
"""


def init():
    new = not db.val("SELECT 1 FROM sqlite_master WHERE name='fuel_fills'")
    db.conn().executescript(SCHEMA)
    if new:                         # the refuels booked by earlier versions, at what was charged for them
        for e in db.q("SELECT * FROM job_events WHERE kind='refuel'"):
            m = re.match(r"(\d+) L$", e["detail"] or "")
            if m and int(m.group(1)):
                db.insert("fuel_fills", user_id=e["user_id"], t=e["created_at"], kind="fuel", litres=float(m.group(1)),
                          price=round(e["amount"] / float(m.group(1)), 4), cost=e["amount"], job_id=e["job_id"])
    db.conn().commit()


def _fold(name):
    """'Kraków' and 'Krakow' are the same city."""
    return "".join(c for c in unicodedata.normalize("NFKD", name.lower().replace("ł", "l")) if not unicodedata.combining(c))


def _data():
    global _static
    if _static is None:
        try:
            with open(os.path.join(RES, "fmpro", "cargo_list.json"), encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        prices, states, cities = raw.get("fuel_prices", {}), {}, {}
        for g in ("ats", "ets2"):
            names = {}
            for c in raw.get("cities", {}).get(g, []):
                for n in (c.get("name"), c.get("native")):
                    if n:
                        names.setdefault(_fold(n), set()).add(c["country"])
            # a name in two states (Kansas City) says nothing about where the truck is
            states[g] = {**{n: next(iter(s)) if len(s) == 1 else None for n, s in names.items()}, **MORE[g]}
            try:
                with open(os.path.join(RES, "fmpro", "static", "map", f"{g}.json"), encoding="utf-8") as f:
                    cities[g] = [(_fold(c["n"]), c["p"][0], c["p"][1]) for c in json.load(f)["cities"]]
            except (OSError, ValueError, KeyError):
                cities[g] = []
        _static = (prices, states, cities)
    return _static


def state_at(game, x, z):
    """The state / country a position is in, or ''."""
    prices, states, cities = _data()
    if game not in states or not (math.isfinite(x) and math.isfinite(z)) or (not x and not z):
        return ""
    known = states[game]
    learned = {_fold(p["name"]): (p["x"], p["z"]) for p in db.q("SELECT name, x, z FROM map_places WHERE game=?", (game,))}
    spots = [(n, cx, cz) for n, cx, cz in cities[game] if n not in learned] + [(n, *p) for n, p in learned.items()]
    best = min(((math.hypot(cx - x, cz - z), known.get(n, "")) for n, cx, cz in spots if known.get(n, "") is not None), default=(FAR, ""))
    return best[1] if best[0] < FAR else ""


def diesel(game, x, z):
    """(price per litre, state or country, "game" or "company")."""
    place = state_at(game, x, z)
    p = _data()[0].get(game, {}).get(place)
    if p and db.setting("fuel_price_game", "1") == "1":
        return p, place, "game"
    return db.setting_f("fuel_price", 1.25), place, "company"


def adblue():
    return db.setting_f("adblue_price", 1.2)


def last_paid(uid, game=None):
    """What this driver last paid for a litre of diesel in this game: the fuel they are burning. Fill-ups from before
    the game was recorded count for either game."""
    p = db.val("SELECT price FROM fuel_fills WHERE user_id=? AND kind='fuel' AND (? IS NULL OR game=? OR game='') "
               "ORDER BY t DESC LIMIT 1", (uid, game, game))
    return db.setting_f("fuel_price", 1.25) if p is None else p


def book(uid, kind, litres, game, x, z, job_id=None, from_pct=None, to_pct=None):
    """Price one fill-up and charge it to the company."""
    price, place, _src = diesel(game, x, z)
    if kind == "adblue":
        price = adblue()
    now, cost, litres = time.time(), round(litres * price, 2), round(litres, 1)
    db.insert("fuel_fills", user_id=uid, t=now, game=game, kind=kind, litres=litres, price=price, cost=cost, place=place,
              from_pct=from_pct, to_pct=to_pct, job_id=job_id)
    amount = f"{litres:.0f} L" if kind == "fuel" else f"{litres:.1f} L"
    detail = f"{amount}{', ' + place if place else ''}"
    db.insert("job_events", job_id=job_id, user_id=uid, kind="refuel" if kind == "fuel" else "adblue", amount=cost,
              detail=detail, x=x, z=z, created_at=now)
    economy.post(kind, -cost, f"{'Refuel' if kind == 'fuel' else 'AdBlue'}: {detail} ({economy.user_name(uid)})", job_id=job_id)
    log.info("driver #%s: %s %.1f L x %.4f = %.2f (%s)", uid, LABEL[kind], litres, price, cost, place or "company price")


def fills(uid, seconds, limit=50):
    return db.q("SELECT t, kind, litres, price, cost, place, from_pct, to_pct FROM fuel_fills WHERE user_id=? AND t>=? "
                "ORDER BY t DESC, id DESC LIMIT ?", (uid, time.time() - seconds, limit))


def totals(uid, seconds):
    out = {"refuels": 0, "topups": 0, "fuel_cost": 0.0, "adblue_cost": 0.0}
    for r in db.q("SELECT kind, COUNT(*) n, SUM(cost) c FROM fuel_fills WHERE user_id=? AND t>=? GROUP BY kind",
                  (uid, time.time() - seconds)):
        out["refuels" if r["kind"] == "fuel" else "topups"] = r["n"]
        out[r["kind"] + "_cost"] = round(r["c"], 2)
    return out


class AdBlueMeter:
    """Adds up the AdBlue that runs in at a pump (the game raises no event for it). A pump fills bit by bit while the
    truck stands still; a level that leaps is a save or another truck being loaded."""
    QUIET_S = 10                    # nothing more for this long: the fill is finished

    def __init__(self, user_id):
        self.user_id, self.prev, self.fill = user_id, None, None

    def reset(self):
        self.done()
        self.prev = None

    def feed(self, v, now, job_id, same_truck=True):
        cap, a = v["adblue_capacity"], v["adblue"]
        p, self.prev = self.prev, {"a": a, "t": now, "cap": cap, "gt": v["game_time_abs"], "x": v["x"], "z": v["z"]}
        if (p is None or not same_truck or cap <= 0 or cap != p["cap"] or v["game_time_abs"] < p["gt"]
                or math.hypot(v["x"] - p["x"], v["z"] - p["z"]) > 15):
            self.done()
            return
        rise, still = a - p["a"], abs(v["speed"]) < 1
        # the game pumps about 3 litres a second
        if still and rise > 0.02 and rise <= 2 + 12 * min(max(now - p["t"], 0), 2) and (p["a"] > 0.01 or rise < 5):
            f = self.fill = self.fill or {"l": 0.0, "from": p["a"] / cap * 100, "game": v["game"], "job": job_id}
            f.update(l=f["l"] + rise, to=a / cap * 100, t=now, x=v["x"], z=v["z"])
        elif self.fill and (not still or now - self.fill["t"] > self.QUIET_S):
            self.done()

    def done(self):
        f, self.fill = self.fill, None
        if f and f["l"] >= 0.5:
            book(self.user_id, "adblue", f["l"], f["game"], f["x"], f["z"], f["job"], round(f["from"]), round(f["to"]))
