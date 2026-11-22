"""The drivers test: two in-game hours, marked from live telemetry. Pure logic - no database, no pages.

The state is a plain dict (kept as JSON in licence_tests), so a test survives a restart of the program.

  1. Pre-trip   parked: work through the truck's controls and condition
  2. Control    couple a trailer, back up straight, back up round a bend, one controlled stop
  3. Road       drive until the two hours are up, losing marks for mistakes
  4. Park       stop, parking brake on, engine off

Parts 1 and 2 share the first hour; whatever is left of the two hours is the road part. An accident, a serious offence
or skipping time (sleeping, ferries, loading a save) ends the test at once; below PASS marks it is failed as well.
"""
import math

LENGTH = 120            # in-game minutes for the whole test
PREP_LIMIT = 60         # parts 1 and 2 must be done by then
MIN_MOVING = 30         # in-game minutes on the move in the road part
PARK_LIMIT = 30         # in-game minutes to find somewhere to park after the two hours
PASS = 80

PRETRIP = [
    ("brake", "Set the parking brake"), ("engine", "Start the engine"), ("air", "Air pressure up, no warning"),
    ("lights", "Low beam headlights on"), ("left", "Left turn signal"), ("right", "Right turn signal"),
    ("hazard", "Hazard lights"), ("wipers", "Wipers"), ("condition", "Truck damage under 10%"),
    ("fuel", "Enough fuel, no fuel warning"),
]
CONTROL = [
    ("couple", "Couple a trailer"), ("straight", "Back up 20 m (65 ft) in a straight line"),
    ("bend", "Back up round a bend (turn the truck 30 degrees)"), ("stop", "Reach 30 km/h (20 mph), then stop smoothly"),
]
STAGES = {"pretrip": "Pre-trip check", "control": "Control skills", "road": "Road drive", "park": "Park",
          "passed": "Passed", "failed": "Failed", "cancelled": "Cancelled"}
STAGE_NEWS = {
    "control": "Drivers test: pre-trip passed. Part 2: couple a trailer, back up straight, back up round a bend, then one smooth stop.",
    "road": "Drivers test: control skills passed. Part 3: drive on the road until the two hours are up.",
    "park": "Drivers test: two hours are up. Pull over somewhere safe, set the parking brake and switch the engine off.",
}
# a fine for one of these ends the test; any other fine costs 15 marks
OVER = ("passed", "failed", "cancelled")
SERIOUS = {"crash", "red_signal", "wrong_way", "illegal_border_crossing", "avoid_inspection", "illegal_trailer"}


def new(v):
    return {"stage": "pretrip", "game": v["game"], "truck": [v["truck_id"], v["license_plate"]], "clock": 0.0,
            "last_min": v["game_time_abs"], "last_t": None, "score": 100, "reason": "", "marks": [], "done": [],
            "moving": 0.0, "km": 0.0, "cool": {}, "acc": {}, "caps": {}}


def _fail(st, why):
    st["stage"], st["reason"] = "failed", why


def _mark(st, code, text, pts, now, every=5, cap=None):
    """Take marks off, at most once per `every` seconds and `cap` times for one kind of mistake."""
    if now - st["cool"].get(code, -1e9) < every or (cap and st["caps"].get(code, 0) >= cap):
        return
    st["cool"][code] = now
    st["caps"][code] = st["caps"].get(code, 0) + 1
    _take(st, text, pts)


def _take(st, text, pts):
    st["score"] = max(0, st["score"] - pts)
    st["marks"] = (st["marks"] + [{"at": int(st["clock"]), "text": text, "pts": pts}])[-40:]
    if st["score"] < PASS:
        _fail(st, f"The score fell below {PASS}: {text[0].lower() + text[1:]}.")


def fined(st, code, text):
    if st["stage"] in OVER:
        return
    if code in SERIOUS:
        _fail(st, f"{text}.")
    else:
        _take(st, f"Fined: {text[0].lower() + text[1:]}", 15)


def _turned(a, b):
    """Degrees from heading a to heading b (the game counts headings as 0..1 turns)."""
    return ((b - a + 0.5) % 1.0 - 0.5) * 360


def step(st, v, prev, dt, now, tol_kmh=5.0, accident=None):
    """One telemetry frame. `prev` is the frame before it (None after a pause), `dt` the real seconds between them."""
    if st["stage"] in OVER:
        return
    if v["game"] != st["game"] or [v["truck_id"], v["license_plate"]] != st["truck"]:
        return _fail(st, "You changed truck or game during the test.")
    if accident:
        return _fail(st, f"You had an accident ({accident}).")
    gm = v["game_time_abs"]
    diff = gm - st["last_min"]
    real = now - st["last_t"] if st["last_t"] else 0
    st["last_min"], st["last_t"] = gm, now
    if diff < -2:
        return _fail(st, "An earlier save was loaded.")
    if v["ferry"] or v["train"]:
        return _fail(st, "You took a ferry or train.")
    if diff > 5 + min(30.0, max(0.0, real)) * 0.6:    # the game runs at most about 20 game-seconds per real second
        if real > 30:
            st["stage"], st["reason"] = "cancelled", "The connection was lost while you were driving."
            return None
        return _fail(st, "Time was skipped (sleeping or fast travel).")
    if prev and dt <= 3 and (abs(v["x"] - prev["x"]) > 400 or abs(v["z"] - prev["z"]) > 400):
        return _fail(st, "The truck was moved (tow or fast travel).")
    st["clock"] += max(0, diff)
    dt = min(2.0, dt) if prev and dt > 0 else 0.0
    if st["stage"] in ("pretrip", "control") and st["clock"] > PREP_LIMIT:
        return _fail(st, "The pre-trip check and control skills took longer than an in-game hour.")
    if st["stage"] == "pretrip":
        _pretrip(st, v, now)
    elif st["stage"] == "control":
        _control(st, v, prev, dt, now)
    elif st["stage"] == "road":
        _road(st, v, prev, dt, diff, now, tol_kmh)
        if st["stage"] == "road" and st["clock"] >= LENGTH:
            if st["moving"] < MIN_MOVING:
                return _fail(st, f"Only {int(st['moving'])} in-game minutes were driven in the road part; it needs {MIN_MOVING}.")
            st["stage"] = "park"
    elif st["stage"] == "park":
        if st["clock"] > LENGTH + PARK_LIMIT:
            return _fail(st, "You didn't park in time after the two hours.")
        _road(st, v, prev, dt, 0, now, tol_kmh)
        if st["stage"] == "park" and abs(v["speed"]) < 0.3 and v["parking_brake"] and not v["engine_enabled"]:
            st["stage"] = "passed"


def _done(st, key):
    if key not in st["done"]:
        st["done"].append(key)


def _pretrip(st, v, now):
    if abs(v["speed"]) > 2:
        _mark(st, "early", "Drove off before finishing the pre-trip check", 10, now, every=1e9)
        return
    if abs(v["speed"]) > 0.5:
        return
    wear = max(v["wear_engine"], v["wear_transmission"], v["wear_cabin"], v["wear_chassis"], v["wear_wheels"])
    seen = {"brake": v["parking_brake"], "engine": v["engine_enabled"],
            "air": v["engine_enabled"] and not v["air_pressure_warning"] and not v["air_pressure_emergency"],
            "lights": v["lights_beam_low"], "left": v["blinker_left_active"] and not v["lights_hazard"],
            "right": v["blinker_right_active"] and not v["lights_hazard"], "hazard": v["lights_hazard"],
            "wipers": v["wipers"], "condition": wear < 0.10, "fuel": not v["fuel_warning"] and v["fuel"] > 1}
    for key, ok in seen.items():
        if ok:
            _done(st, key)
    if all(k in st["done"] for k, _l in PRETRIP):
        st["stage"] = "control"


def _control(st, v, prev, dt, now):
    trailer = v.get("trailers_attached", 0) > 0
    if trailer:
        _done(st, "couple")
    a = st["acc"]
    if v["speed"] < -0.1 and trailer:                  # reversing (the game's speed is negative)
        if a.get("rev") is None:
            a.update(rev=v["heading"], rev_m=0.0, turn=0.0)
        elif prev:
            a["rev_m"] += abs(v["speed"]) * dt
            a["turn"] += _turned(prev["heading"], v["heading"])
            if abs(v["speed_kmh"]) > 15:
                _mark(st, "fastback", "Backing up too fast", 3, now, every=10, cap=3)
        if abs(a["turn"]) <= 8 and a["rev_m"] >= 20:
            _done(st, "straight")
        if abs(a["turn"]) >= 30 and a["rev_m"] >= 5:
            _done(st, "bend")
        if abs(a["turn"]) > 8 and "straight" not in st["done"] and "bend" in st["done"]:
            a["rev"] = None                            # the bend is done; start measuring a straight line again
    elif v["speed"] > 0.5 or not trailer:
        a["rev"] = None
    # one smooth stop from at least 30 km/h
    if v["speed_kmh"] >= 30:
        a["fast"] = True
        a["rough"] = False
    if a.get("fast") and prev and dt > 0 and (abs(prev["speed"]) - abs(v["speed"])) / dt > 4.5:
        a["rough"] = True
    if a.get("fast") and abs(v["speed"]) < 0.3:
        if not a.get("rough"):
            _done(st, "stop")
        else:
            _mark(st, "roughstop", "Rough stop: brake earlier and try again", 3, now, every=5, cap=3)
        a["fast"] = a["rough"] = False
    if st["stage"] == "control" and all(k in st["done"] for k, _l in CONTROL):
        st["stage"] = "road"
        st["acc"] = {}


def _road(st, v, prev, dt, diff, now, tol):
    kmh = abs(v["speed_kmh"])
    if kmh > 5:
        st["moving"] += max(0, diff)
        st["km"] += kmh * dt / 3600
    if not prev or dt <= 0:
        return
    a = st["acc"]
    # speeding: 2 marks for every 10 seconds, 4 when far over
    over = kmh - v["speed_limit_kmh"] if v["speed_limit_kmh"] > 1 else 0
    if over > tol:
        a["over_s"] = a.get("over_s", 0) + dt
        if a["over_s"] >= 10:
            a["over_s"] = 0
            far = over > 20
            by = f"{over / 1.609344:.0f} mph" if st["game"] == "ats" else f"{over:.0f} km/h"
            _take(st, f"Speeding ({by} over)", 4 if far else 2)
            if st["stage"] == "failed":
                return
    if kmh > 5 and prev["speed"] > 2 and (prev["speed"] - v["speed"]) / dt > 4.5:
        _mark(st, "brake", "Harsh braking", 3, now)
    deg = _turned(prev["heading"], v["heading"])
    if kmh > 25 and abs(v["speed"]) * math.radians(abs(deg)) / dt > 4.5:
        _mark(st, "corner", "Taking a bend too fast", 3, now)
    hour = (v["game_time_abs"] % 1440) // 60
    if kmh > 5 and (hour >= 20 or hour < 6) and not (v["lights_beam_low"] or v["lights_beam_high"]):
        a["dark_s"] = a.get("dark_s", 0) + dt
        if a["dark_s"] >= 8:
            a["dark_s"] = 0
            _mark(st, "dark", "Driving in the dark without headlights", 5, now, every=30)
    else:
        a["dark_s"] = 0
    if kmh > 8 and v["parking_brake"]:
        _mark(st, "pbrake", "Driving with the parking brake on", 5, now, every=30)
    # a turn at a junction (70 degrees within 10 seconds, at low speed) needs a signal
    if v["blinker_left_active"] or v["blinker_right_active"] or v["lights_hazard"]:
        a["blink_t"] = now
    turns = [t for t in a.get("turns", []) if now - t[0] <= 10]
    if 5 < kmh < 40:
        turns.append([now, deg])
    else:
        turns = []
    if abs(sum(t[1] for t in turns)) >= 70:
        if now - a.get("blink_t", -1e9) > 14:
            _mark(st, "signal", "Turning without a signal", 2, now, every=20, cap=4)
        turns = []
    a["turns"] = turns


def view(st):
    """What the page shows, ready for JSON."""
    return {"stage": st["stage"], "stage_label": STAGES.get(st["stage"], st["stage"]), "score": st["score"], "pass": PASS,
            "clock": int(st["clock"]), "length": LENGTH, "left": max(0, int(LENGTH - st["clock"])),
            "moving": int(st["moving"]), "km": round(st["km"], 1), "game": st["game"], "reason": st["reason"],
            "pretrip": [{"label": lb, "ok": k in st["done"]} for k, lb in PRETRIP],
            "control": [{"label": lb, "ok": k in st["done"]} for k, lb in CONTROL],
            "marks": st["marks"][::-1][:12]}
