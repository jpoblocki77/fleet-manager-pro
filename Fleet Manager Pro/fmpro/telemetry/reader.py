"""Read and decode the scs-telemetry.dll shared memory block."""
import ctypes
import struct
import sys

from .layout import (FIELDS, TRAILER_FIELDS, TRAILER_BASE, TRAILER_SIZE, TRAILERS,
                     SUBSTANCE_BASE, SUBSTANCE_COUNT, SUBSTANCE_LEN, MMF_NAME, MMF_SIZE, GAMES)

FILE_MAP_READ = 0x0004
_FMT = {"bool": "?", "u32": "I", "i32": "i", "f32": "f", "f64": "d", "u64": "Q", "i64": "q"}
_SIZE = {"bool": 1, "u32": 4, "i32": 4, "f32": 4, "f64": 8, "u64": 8, "i64": 8}


class SharedMemory:
    """Read-only view onto the plugin's memory-mapped file (Windows only)."""

    def __init__(self, name=MMF_NAME, size=MMF_SIZE):
        self.name, self.size = name, size
        self.handle = self.view = None

    @property
    def is_open(self):
        return bool(self.view)

    def open(self):
        if sys.platform != "win32":
            return False
        k32 = ctypes.windll.kernel32
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_uint32, ctypes.c_size_t]
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenFileMappingW(FILE_MAP_READ, 0, self.name)
        if not h:
            return False
        v = k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, self.size)
        if not v:
            k32.CloseHandle(h)
            return False
        self.handle, self.view = h, v
        return True

    def read(self):
        return ctypes.string_at(self.view, self.size) if self.view else None

    def close(self):
        if sys.platform == "win32":
            k32 = ctypes.windll.kernel32
            if self.view:
                k32.UnmapViewOfFile(self.view)
            if self.handle:
                k32.CloseHandle(self.handle)
        self.handle = self.view = None


def _clean(v):
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return 0.0
    return v


def _read(buf, kind, off, count):
    if kind == "str":
        return buf[off:off + count].split(b"\0", 1)[0].decode("utf-8", "replace")
    fmt = "<" + str(count) + _FMT[kind]
    vals = struct.unpack_from(fmt, buf, off)
    if kind in ("f32", "f64"):
        vals = tuple(round(_clean(v), 6) for v in vals)
    return vals[0] if count == 1 else list(vals)


def parse(buf):
    """Decode every field into {'values': {...}, 'groups': {...}, 'trailers': [...], 'substances': [...]}"""
    values, groups = {}, {}
    for key, group, kind, off, count in FIELDS:
        v = _read(buf, kind, off, count)
        values[key] = v
        groups.setdefault(group, []).append(key)

    wc = max(0, min(16, values["truck_wheel_count"]))
    for key in groups.get("wheels", []):
        if isinstance(values[key], list):
            values[key] = values[key][:wc]
    for key in ("gear_ratios_forward",):
        values[key] = values[key][:max(0, min(24, values["gear_count_forward"]))]
    for key in ("gear_ratios_reverse",):
        values[key] = values[key][:max(0, min(8, values["gear_count_reverse"]))]
    sel = max(0, min(32, values["selector_count"]))
    n_slots = 2 ** sel if sel else 0
    for key in ("hshifter_position", "hshifter_bitmask", "hshifter_resulting"):
        values[key] = values[key][:min(32, n_slots)]

    trailers = []
    for i in range(TRAILERS):
        base = TRAILER_BASE + i * TRAILER_SIZE
        t = {k: _read(buf, kind, base + off, cnt) for k, kind, off, cnt in TRAILER_FIELDS}
        if not (t["id"] or t["attached"] or t["name"]):
            continue
        twc = max(0, min(16, t["wheel_count"]))
        for k in list(t):
            if k.startswith("wheel_") and isinstance(t[k], list):
                t[k] = t[k][:twc]
        t["slot"] = i
        trailers.append(t)

    substances = [s for s in (_read(buf, "str", SUBSTANCE_BASE + i * SUBSTANCE_LEN, SUBSTANCE_LEN)
                              for i in range(SUBSTANCE_COUNT)) if s]
    values["game"] = GAMES.get(values["game_id"], "unknown")
    values.update(derive(values, trailers))
    return {"values": values, "groups": groups, "trailers": trailers, "substances": substances}


def derive(v, trailers):
    """Convenience values computed from the raw ones (km/h, mph, %, clock...)."""
    ms = v["speed"]
    fuel_pct = (v["fuel"] / v["fuel_capacity"] * 100) if v["fuel_capacity"] else 0
    adblue_pct = (v["adblue"] / v["adblue_capacity"] * 100) if v["adblue_capacity"] else 0
    gta = v["game_time_abs"]
    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][(gta // 1440) % 7]
    eta_min = v["job_delivery_time_abs"] - gta if v["job_delivery_time_abs"] else 0
    wear = [v["wear_engine"], v["wear_transmission"], v["wear_cabin"], v["wear_chassis"], v["wear_wheels"]]
    gd = v["gear_dashboard"]
    return {
        "speed_kmh": round(ms * 3.6, 2), "speed_mph": round(ms * 2.236936, 2),
        "speed_limit_kmh": round(v["speed_limit"] * 3.6, 1),
        "speed_limit_mph": round(v["speed_limit"] * 2.236936, 1),
        "cruise_kmh": round(v["cruise_control_speed"] * 3.6, 1),
        "cruise_mph": round(v["cruise_control_speed"] * 2.236936, 1),
        "fuel_pct": round(fuel_pct, 1), "adblue_pct": round(adblue_pct, 1),
        "gear_label": "N" if gd == 0 else (f"R{-gd}" if gd < 0 else str(gd)),
        "game_clock": f"{dow} {(gta % 1440) // 60:02d}:{gta % 60:02d}",
        "delivery_eta_min": eta_min,
        "navigation_km": round(v["navigation_distance"] / 1000, 2),
        "navigation_mi": round(v["navigation_distance"] / 1609.344, 2),
        "cargo_tonnes": round(v["cargo_mass"] / 1000, 2),
        "truck_damage_pct": round(max(wear) * 100, 2),
        "trailer_damage_pct": round(max([max(t["wear_chassis"], t["wear_wheels"], t["wear_body"])
                                         for t in trailers if t["attached"]] or [0]) * 100, 2),
        "trailers_attached": sum(1 for t in trailers if t["attached"]),
        "heading_deg": round((v["heading"] % 1) * 360, 1),
        "speeding": v["speed_limit"] > 1 and ms > v["speed_limit"] + 1.4,
    }


def pack(values, trailers=(), substances=()):
    """Build a raw buffer from values - used by the built-in simulator and tests."""
    buf = bytearray(MMF_SIZE)

    def put(kind, off, count, val):
        if kind == "str":
            b = str(val or "").encode()[:count - 1]
            buf[off:off + len(b)] = b
            return
        arr = list(val) if isinstance(val, (list, tuple)) else [val]
        arr = (arr + [0] * count)[:count]
        struct.pack_into("<" + str(count) + _FMT[kind], buf, off, *arr)

    for key, _g, kind, off, count in FIELDS:
        if key in values:
            put(kind, off, count, values[key])
    for i, t in enumerate(list(trailers)[:TRAILERS]):
        base = TRAILER_BASE + i * TRAILER_SIZE
        for k, kind, off, cnt in TRAILER_FIELDS:
            if k in t:
                put(kind, base + off, cnt, t[k])
    for i, s in enumerate(list(substances)[:SUBSTANCE_COUNT]):
        put("str", SUBSTANCE_BASE + i * SUBSTANCE_LEN, SUBSTANCE_LEN, s)
    return bytes(buf)


def field_count():
    """Total number of scalar data points the parser exposes."""
    n = sum(c if k != "str" else 1 for _, _, k, _, c in FIELDS)
    n += TRAILERS * sum(c if k != "str" else 1 for _, k, _, c in TRAILER_FIELDS)
    return n + SUBSTANCE_COUNT
