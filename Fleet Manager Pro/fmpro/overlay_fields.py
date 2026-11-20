"""What the in-game overlay can show, how each value is written, and each driver's overlay settings.

FIELDS is the menu a driver picks from on the Stream Overlay page. Values are formatted here (in the driver's units)
so the overlay window only has to draw text. Settings are kept per driver in the settings table (overlay_cfg_<id>).
"""
import json

from . import db

# key: (label, group, kind)   kind decides the formatting; "hos.*" keys come from the logbook, the rest from telemetry
FIELDS = {
    "speed_kmh": ("Speed", "Driving", "speed"),
    "speed_limit_kmh": ("Speed limit", "Driving", "speed"),
    "cruise_kmh": ("Cruise control", "Driving", "cruise"),
    "gear_label": ("Gear", "Driving", "text"),
    "engine_rpm": ("RPM", "Driving", "rpm"),
    "game_clock": ("Game time", "Driving", "text"),
    "odometer": ("Odometer", "Driving", "dist"),
    "parking_brake": ("Parking brake", "Driving", "onoff"),
    "lights_beam_low": ("Headlights", "Driving", "onoff"),

    "cargo": ("Cargo", "Job", "text"),
    "cargo_mass": ("Cargo weight", "Job", "mass"),
    "route": ("Route", "Job", "route"),
    "job_income": ("Pay", "Job", "money"),
    "navigation_km": ("Distance to go", "Job", "dist"),
    "navigation_time": ("Drive time to go", "Job", "navtime"),
    "delivery_eta_min": ("Time to deadline", "Job", "deadline"),
    "planned_distance_km": ("Planned distance", "Job", "dist"),
    "cargo_damage": ("Cargo damage", "Job", "frac"),

    "fuel_pct": ("Fuel", "Fuel & truck", "pct"),
    "fuel": ("Fuel (amount)", "Fuel & truck", "liq"),
    "fuel_range": ("Range", "Fuel & truck", "dist"),
    "fuel_avg_consumption": ("Fuel economy", "Fuel & truck", "economy"),
    "adblue_pct": ("AdBlue", "Fuel & truck", "pct"),
    "truck_damage_pct": ("Truck damage", "Fuel & truck", "pct1"),
    "trailer_damage_pct": ("Trailer damage", "Fuel & truck", "pct1"),
    "truck": ("Truck", "Fuel & truck", "truck"),
    "oil_temperature": ("Oil temperature", "Fuel & truck", "temp"),
    "water_temperature": ("Water temperature", "Fuel & truck", "temp"),
    "air_pressure": ("Air pressure", "Fuel & truck", "pres"),
    "battery_voltage": ("Battery", "Fuel & truck", "volt"),

    "hos.status": ("Duty status", "Hours of Service", "duty"),
    "hos.available": ("Drive time left", "Hours of Service", "hm"),
    "hos.break_left": ("Break due in", "Hours of Service", "hm"),
    "hos.window_left": ("Duty window left", "Hours of Service", "hm"),
    "hos.cycle_left": ("Cycle left", "Hours of Service", "hm"),
    "next_rest_stop": ("Next rest stop", "Hours of Service", "hm"),
}

DEFAULT_FIELDS = ["speed_kmh", "speed_limit_kmh", "gear_label", "route", "navigation_km", "delivery_eta_min",
                  "fuel_pct", "hos.status", "hos.available", "hos.break_left"]
CORNERS = {"tl": "Top left", "tc": "Top middle", "tr": "Top right", "ml": "Middle left", "mr": "Middle right",
           "bl": "Bottom left", "bc": "Bottom middle", "br": "Bottom right"}
HOTKEYS = {"ctrl+i": "Ctrl + I", "ctrl+shift+f": "Ctrl + Shift + F", "ctrl+alt+o": "Ctrl + Alt + O",
           "f10": "F10", "f11": "F11", "ctrl+f12": "Ctrl + F12"}
DEFAULTS = {"fields": DEFAULT_FIELDS, "corner": "tr", "opacity": 90, "scale": 100, "hotkey": "ctrl+i",
            "alerts": True, "hos_warnings": True, "only_driving": True, "autostart": False}


def config(uid):
    """This driver's overlay settings, with anything missing or unknown filled from the defaults."""
    try:
        saved = json.loads(db.setting(f"overlay_cfg_{uid}", "") or "{}")
    except ValueError:
        saved = {}
    return clean(saved if isinstance(saved, dict) else {})


def clean(d):
    out = dict(DEFAULTS)
    f = [k for k in (d.get("fields") or []) if k in FIELDS] if isinstance(d.get("fields"), list) else None
    out["fields"] = list(dict.fromkeys(f)) if f is not None else list(DEFAULT_FIELDS)
    out["corner"] = d.get("corner") if d.get("corner") in CORNERS else DEFAULTS["corner"]
    out["hotkey"] = d.get("hotkey") if d.get("hotkey") in HOTKEYS else DEFAULTS["hotkey"]
    for k, lo, hi in (("opacity", 30, 100), ("scale", 60, 200)):
        try:
            out[k] = int(min(hi, max(lo, int(d.get(k, DEFAULTS[k])))))
        except (TypeError, ValueError):
            out[k] = DEFAULTS[k]
    for k in ("alerts", "hos_warnings", "only_driving", "autostart"):
        out[k] = bool(d.get(k, DEFAULTS[k]))
    return out


def save(uid, d):
    cfg = clean(d)
    db.set_setting(f"overlay_cfg_{uid}", json.dumps(cfg))
    return cfg


# ------------------------------------------------------------------------------------------------ formatting
def _hm(m):
    m = max(0, int(round(m or 0)))
    return f"{m // 60}:{m % 60:02d}"


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def value(key, v, hud, imperial, currency="$"):
    """(text, state) for one field. state is '' (normal), 'warn' or 'bad' so the overlay can colour it."""
    kind = FIELDS[key][2]
    hos = (hud or {}).get("hos") or {}
    raw = hos.get(key[4:]) if key.startswith("hos.") else v.get(key)
    on_job = bool(v.get("on_job"))
    dist = (lambda km, d=0: f"{_num(km) * 0.621371:,.{d}f} mi" if imperial else f"{_num(km):,.{d}f} km")
    if kind == "speed":
        if key == "speed_limit_kmh" and _num(raw) < 1:
            return "—", ""
        sp = abs(_num(v.get("speed_mph" if imperial else "speed_kmh"))) if key == "speed_kmh" else \
            _num(v.get("speed_limit_mph" if imperial else "speed_limit_kmh"))
        return f"{sp:.0f} {'mph' if imperial else 'km/h'}", ("bad" if key == "speed_kmh" and v.get("speeding") else "")
    if kind == "cruise":
        if not v.get("cruise_control"):
            return "Off", ""
        return f"{_num(v.get('cruise_mph' if imperial else 'cruise_kmh')):.0f} {'mph' if imperial else 'km/h'}", ""
    if kind == "rpm":
        return f"{_num(raw):,.0f}", ("warn" if _num(v.get("engine_rpm_max")) and _num(raw) > 0.85 * _num(v.get("engine_rpm_max")) else "")
    if kind == "dist":
        if key in ("navigation_km", "planned_distance_km") and not on_job:
            return "—", ""
        return dist(raw, 1 if key == "navigation_km" and _num(raw) < 10 else 0), ""
    if kind == "onoff":
        return ("On" if raw else "Off"), ""
    if kind == "mass":
        return (f"{_num(raw) * 2.20462:,.0f} lb" if imperial else f"{_num(raw) / 1000:,.1f} t") if on_job else "—", ""
    if kind == "route":
        return (f"{v.get('source_city') or '?'} → {v.get('destination_city') or '?'}" if on_job else "Free roam"), ""
    if kind == "money":
        return (f"{currency}{_num(raw):,.0f}" if on_job else "—"), ""
    if kind == "navtime":
        return (_hm(_num(raw) / 60) if on_job and _num(raw) else "—"), ""
    if kind == "deadline":
        if not on_job or not v.get("job_delivery_time_abs"):
            return "—", ""
        m = _num(raw)
        drive = _num(v.get("navigation_time")) / 60
        return ("late " + _hm(-m) if m < 0 else _hm(m)), ("bad" if m < 0 else "warn" if drive > m * 0.85 else "")
    if kind == "frac":
        return f"{_num(raw) * 100:.1f}%", ("warn" if _num(raw) > 0.05 else "")
    if kind == "pct":
        low = (key == "fuel_pct" and v.get("fuel_warning")) or (key == "adblue_pct" and v.get("adblue_warning"))
        return f"{_num(raw):.0f}%", ("bad" if low else "")
    if kind == "pct1":
        return f"{_num(raw):.1f}%", ("warn" if _num(raw) > 10 else "")
    if kind == "liq":
        return (f"{_num(raw) * 0.264172:,.0f} gal" if imperial else f"{_num(raw):,.0f} L"), ""
    if kind == "economy":
        lkm = _num(raw)                                       # litres per km
        if lkm <= 0:
            return "—", ""
        return (f"{1 / (lkm * 0.264172 / 0.621371):.1f} mpg" if imperial else f"{lkm * 100:.1f} L/100km"), ""
    if kind == "truck":
        return f"{v.get('truck_brand') or ''} {v.get('truck_name') or ''}".strip() or "—", ""
    if kind == "temp":
        return (f"{_num(raw) * 9 / 5 + 32:.0f}°F" if imperial else f"{_num(raw):.0f}°C"), ""
    if kind == "pres":
        return (f"{_num(raw):.0f} psi" if imperial else f"{_num(raw) * 0.0689476:.1f} bar"), \
            ("bad" if v.get("air_pressure_warning") else "")
    if kind == "volt":
        return f"{_num(raw):.1f} V", ("bad" if v.get("battery_voltage_warning") else "")
    if kind == "duty":
        return {"driving": "Driving", "on": "On duty", "off": "Off duty", "sleeper": "Sleeper berth"}.get(raw, "—"), ""
    if kind == "hm":
        if raw is None or (key == "next_rest_stop" and _num(raw) <= 0):
            return "—", ""
        state = "bad" if _num(raw) <= 0 else "warn" if _num(raw) < (30 if key == "hos.break_left" else 60) else ""
        return _hm(raw), state
    return (str(raw) if raw not in (None, "") else "—"), ""


def rows(cfg, v, hud, imperial, currency="$"):
    return [[FIELDS[k][0], *value(k, v, hud, imperial, currency)] for k in cfg["fields"]]


# ------------------------------------------------------------------------------------------------ logbook warnings
def hos_warnings(hud, v):
    """Hours of Service warnings that are true right now: (code, text, level). The overlay shows each code once,
    when it first appears, so a warning re-shows only after it has cleared (after a break or a rest)."""
    out = []
    h = (hud or {}).get("hos")
    if h:
        left = h.get("available", 0)
        if left <= 0:
            out.append(("drive-0", "Out of drive time. Stop and take your rest.", "bad"))
        else:
            for t in (15, 30, 60):
                if left <= t:
                    out.append((f"drive-{t}", f"{_hm(left)} of drive time left today.", "warn"))
                    break
        brk = h.get("break_left", 999)
        if brk <= 0 and h.get("status") == "driving":
            out.append(("break-0", f"{h.get('break_len', 30)}-minute break overdue. Pull over for a break.", "bad"))
        elif brk <= 30:
            out.append((f"break-{15 if brk <= 15 else 30}", f"Break due in {_hm(brk)}.", "warn"))
        if 0 < h.get("cycle_left", 9999) <= 120:
            out.append(("cycle-120", f"Only {_hm(h['cycle_left'])} left in your weekly cycle.", "warn"))
    rest = _num(v.get("next_rest_stop"))
    if 0 < rest <= 30:
        out.append(("rest-30", f"Rest stop needed in {_hm(rest)} (game fatigue).", "warn"))
    return out
