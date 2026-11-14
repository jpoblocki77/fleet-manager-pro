"""Built-in telemetry simulator. Produces raw buffers in exactly the DLL's layout so the
whole pipeline (parser -> tracker -> logbook -> economy) can be tried without the game."""
import math
import secrets
import time

from .reader import pack

_rng = secrets.SystemRandom()   # game dice; the OS generator, not the predictable random module

CITIES = {  # name: (x, z) in world metres (approximate, for drawing only)
    "Los Angeles": (-95000, 12000), "San Diego": (-88000, 30000), "Las Vegas": (-60000, -8000),
    "Phoenix": (-40000, 28000), "Tucson": (-30000, 38000), "Sacramento": (-108000, -38000),
    "San Francisco": (-118000, -30000), "Reno": (-92000, -42000), "Flagstaff": (-38000, 8000),
    "Albuquerque": (-5000, 10000), "El Paso": (0, 42000), "Bakersfield": (-98000, -2000),
}
CARGO = [("Lumber", "lumber", 18500), ("Canned Beef", "canned_beef", 14200), ("Excavator", "excavator", 32000),
         ("Frozen Vegetables", "frozen_veg", 16800), ("Pipes", "pipes", 21300), ("Electronics", "electronics", 9200),
         ("Hay Bales", "hay", 12400), ("Transformer", "transformer", 38000)]
COMPANIES = ["Walbert", "Bushnell Farms", "Chemso", "Coastline Mining", "Gal's Oil", "Voltison", "Wallbert"]
SUBSTANCES = ["static", "road", "road_snow", "dirt", "snow", "grass", "rough", "concrete", "asphalt"]
SIM_SPEEDUP = 20


class Simulator:
    def __init__(self):
        self.t0 = time.time()
        self.last = self.t0
        self.odo = 182_340.0
        self.fuel = 520.0
        self.game_min = 3 * 1440 + 8 * 60
        self.job = None
        self.flags = {}
        self.wear = [0.01, 0.008, 0.02, 0.015, 0.03]
        self.idle_until = self.t0 + 4
        self.x, self.z = CITIES["Los Angeles"]
        self.speed = 0.0
        self.cargo_damage = 0.0

    def _new_job(self):
        names = list(CITIES)
        src = min(names, key=lambda n: math.hypot(CITIES[n][0] - self.x, CITIES[n][1] - self.z))
        dst = _rng.choice([n for n in names if n != src])
        cargo = _rng.choice(CARGO)
        dist = math.hypot(CITIES[dst][0] - CITIES[src][0], CITIES[dst][1] - CITIES[src][1]) / 1000 * 1.25
        self.job = {"src": src, "dst": dst, "cargo": cargo, "km": dist, "done": 0.0,
                    "src_comp": _rng.choice(COMPANIES), "dst_comp": _rng.choice(COMPANIES),
                    "income": int(dist * _rng.uniform(4.5, 7.5) * (1 + cargo[2] / 40000)),
                    "deadline": self.game_min + int(dist / 70 * 60) + 240,
                    "toll_at": _rng.uniform(0.2, 0.4), "fine_at": _rng.uniform(0.5, 0.8) if _rng.random() < 0.4 else 2,
                    "ferry_at": 2, "events": set()}
        self.cargo_damage = 0.0

    def _pulse(self, name, secs=1.5):
        self.flags[name] = time.time() + secs

    def step(self):
        now = time.time()
        dt = min(0.5, now - self.last)
        self.last = now
        v = {"sdk_active": True, "paused": False, "plugin_revision": 12, "game_id": 2,
             "game_version_major": 1, "game_version_minor": 5, "telemetry_version_major": 1,
             "telemetry_version_minor": 5, "time_scale": 19.0}
        target = 0.0
        if self.job is None and now >= self.idle_until:
            self._new_job()
        j = self.job
        if j:
            target = 24.6 + 2.5 * math.sin(now / 9)  # ~88-98 km/h
            if j["fine_at"] - 0.04 < j["done"] < j["fine_at"]:
                target = 31.5  # speeding stretch before the fine
        self.speed += (target - self.speed) * min(1, dt * 0.8)
        km = self.speed * dt * SIM_SPEEDUP / 1000
        self.odo += km
        self.fuel = max(0, self.fuel - km * 0.38)
        self.game_min_f = getattr(self, "game_min_f", float(self.game_min)) + dt * SIM_SPEEDUP / 60 * 19 / 19
        self.game_min = int(self.game_min_f)
        if j:
            j["done"] = min(1.0, j["done"] + km / j["km"])
            sx, sz = CITIES[j["src"]]
            dx, dz = CITIES[j["dst"]]
            p = j["done"]
            wig = math.sin(p * math.pi * 3) * 2500
            nx, nz = sx + (dx - sx) * p, sz + (dz - sz) * p
            ang = math.atan2(dz - sz, dx - sx)
            nx, nz = nx - math.sin(ang) * wig, nz + math.cos(ang) * wig
            self.heading = (math.atan2(-(nx - self.x), -(nz - self.z)) / (2 * math.pi)) % 1 if (nx, nz) != (self.x, self.z) else getattr(self, "heading", 0)
            self.x, self.z = nx, nz
            self.cargo_damage = min(0.2, self.cargo_damage + (0.00002 if _rng.random() < 0.05 else 0))
            for wi in range(5):
                self.wear[wi] = min(1, self.wear[wi] + km * 0.000002)
            if p > j["toll_at"] and "toll" not in j["events"]:
                j["events"].add("toll"); self._pulse("tollgate"); self.toll = _rng.choice([12, 18, 24, 31])
            if p > j["fine_at"] and "fine" not in j["events"]:
                j["events"].add("fine"); self._pulse("fined"); self.fine = _rng.choice([150, 250, 400])
            if self.fuel < 140 and "refuel" not in j["events"]:
                j["events"].add("refuel"); self.refuel_l = 520 - self.fuel; self.fuel = 520.0
                self._pulse("refuel"); self._pulse("refuel_paid")
            if p >= 1.0:
                self._pulse("job_delivered", 2.5); self._pulse("job_finished", 2.5)
                self.delivered = dict(j)
                self.delivered["xp"] = int(j["km"] * 2.2)
                self.delivered["damage"] = self.cargo_damage
                self.job = None
                self.idle_until = now + 6
                self.speed = 0
        active = {k for k, until in self.flags.items() if until > now}
        on_job = self.job is not None
        cj = self.job or (getattr(self, "delivered", None) if "job_delivered" in active else None)
        ms = self.speed
        limit = 29.06 if cj else 0
        v.update({
            "simulated_time": int((now - self.t0) * 1e6 * SIM_SPEEDUP), "time": int((now - self.t0) * 1e6),
            "render_time": int((now - self.t0) * 1e6), "game_time_abs": self.game_min,
            "gear_count_forward": 18, "gear_count_reverse": 4, "retarder_step_count": 3, "truck_wheel_count": 10,
            "selector_count": 2, "max_trailer_count": 10, "shifter_type": "arcade",
            "fuel_capacity": 568.0, "fuel_warning_factor": 0.15, "adblue_capacity": 80.0, "adblue_warning_factor": 0.15,
            "air_pressure_warning_level": 65.0, "air_pressure_emergency_level": 35.0, "oil_pressure_warning_level": 10.0,
            "water_temperature_warning_level": 105.0, "battery_voltage_warning_level": 22.0,
            "engine_rpm_max": 2500.0, "gear_differential": 2.64,
            "gear_ratios_forward": [14.4, 12.29, 10.37, 8.85, 7.47, 6.38, 5.39, 4.6, 3.88, 3.31, 2.8, 2.39, 2.02, 1.72, 1.45, 1.24, 1.05, 0.9],
            "gear_ratios_reverse": [-13.69, -11.69, -3.69, -3.15],
            "wheel_radius": [0.5] * 10, "wheel_steerable": [1, 1] + [0] * 8, "wheel_simulated": [1] * 10,
            "wheel_powered": [0, 0, 1, 1, 1, 1, 0, 0, 0, 0], "wheel_liftable": [0] * 10, "wheel_on_ground": [1] * 10,
            "wheel_substance": [8] * 10, "wheel_velocity": [ms / 0.5 / (2 * math.pi)] * 10,
            "wheel_rotation": [((now * ms) % 1)] * 10, "wheel_suspension_deflection": [0.01 * math.sin(now * 3 + i) for i in range(10)],
            "wheel_steering": [0.02 * math.sin(now / 3)] * 2 + [0] * 8,
            "wheel_position_x": [-1, 1, -1, 1, -1, 1, -1, 1, -1, 1], "wheel_position_y": [0.5] * 10,
            "wheel_position_z": [-3.3, -3.3, 1.1, 1.1, 2.4, 2.4, 0, 0, 0, 0],
            "speed": ms, "engine_rpm": 650 + ms * 52 if ms > 0.5 else 650,
            "gear": min(12, max(0, int(ms / 2.2))) if ms > 0.3 else 0,
            "gear_dashboard": min(12, max(0, int(ms / 2.2))) if ms > 0.3 else 0,
            "input_throttle": 0.6 if ms < 26 else 0.3, "effective_throttle": 0.6 if ms < 26 else 0.3,
            "input_steering": 0.02 * math.sin(now / 3), "effective_steering": 0.02 * math.sin(now / 3),
            "cruise_control": bool(on_job and ms > 20), "cruise_control_speed": 26.8 if on_job and ms > 20 else 0,
            "air_pressure": 120.0, "brake_temperature": 42.0, "fuel": self.fuel, "fuel_avg_consumption": 0.38,
            "fuel_range": self.fuel / 0.38, "adblue": 61.0, "oil_pressure": 52.0, "oil_temperature": 88.0,
            "water_temperature": 84.0 + math.sin(now / 30), "battery_voltage": 27.6, "dashboard_backlight": 0.8,
            "wear_engine": self.wear[0], "wear_transmission": self.wear[1], "wear_cabin": self.wear[2],
            "wear_chassis": self.wear[3], "wear_wheels": self.wear[4], "odometer": self.odo,
            "navigation_distance": max(0, (1 - self.job["done"]) * self.job["km"] * 1000) if self.job else 0,
            "navigation_time": max(0, (1 - self.job["done"]) * self.job["km"] * 1000 / 24) if self.job else 0,
            "speed_limit": limit, "electric_enabled": True, "engine_enabled": True,
            "lights_parking": True, "lights_beam_low": True, "blinker_left_active": False,
            "lights_brake": target < ms - 0.5, "parking_brake": not on_job and ms < 0.2,
            "next_rest_stop": 540 - (self.game_min % 600), "x": self.x, "y": 12.0, "z": self.z,
            "heading": getattr(self, "heading", 0), "linear_velocity": [0, 0, -ms],
            "linear_acceleration": [0, 0, (target - ms) * 0.8], "cabin_position": [0, 1.6, -1.8],
            "head_position": [-0.6, 1.0, -0.3], "hook_position": [0, 1.0, 1.4],
            "truck_brand_id": "kenworth", "truck_brand": "Kenworth", "truck_id": "vehicle.kenworth.w900",
            "truck_name": "W900", "license_plate": "FMP 0001", "license_plate_country": "California",
            "license_plate_country_id": "california",
            "on_job": on_job, "cargo_loaded": on_job,
        })
        if cj:
            v.update({"cargo": cj["cargo"][0], "cargo_id": cj["cargo"][1], "cargo_mass": float(cj["cargo"][2]),
                      "unit_count": 1, "unit_mass": float(cj["cargo"][2]), "source_city": cj["src"],
                      "source_city_id": cj["src"].lower().replace(" ", "_"), "destination_city": cj["dst"],
                      "destination_city_id": cj["dst"].lower().replace(" ", "_"), "source_company": cj["src_comp"],
                      "source_company_id": cj["src_comp"].lower(), "destination_company": cj["dst_comp"],
                      "destination_company_id": cj["dst_comp"].lower(), "job_income": cj["income"],
                      "planned_distance_km": int(cj["km"]), "job_delivery_time_abs": cj["deadline"],
                      "job_market": "freight_market", "cargo_damage": self.cargo_damage})
        for f in ("job_delivered", "job_finished", "fined", "tollgate", "refuel", "refuel_paid", "ferry", "train"):
            v[f] = f in active
        if "job_delivered" in active and getattr(self, "delivered", None):
            d = self.delivered
            v.update({"job_delivered_revenue": d["income"], "job_delivered_earned_xp": d["xp"],
                      "job_delivered_cargo_damage": d["damage"], "job_delivered_distance_km": d["km"],
                      "job_delivered_delivery_time": int(d["km"] / 70 * 60)})
        if "fined" in active:
            v.update({"fine_offence": "speeding_camera", "fine_amount": getattr(self, "fine", 250)})
        if "tollgate" in active:
            v["toll_amount"] = getattr(self, "toll", 18)
        if "refuel" in active:
            v["refuel_amount"] = getattr(self, "refuel_l", 300.0)
        trailers = []
        if cj:
            trailers.append({"attached": True, "id": "scs_flatbed", "name": "Flatbed", "brand": "SCS",
                             "brand_id": "scs", "body_type": "flatbed", "chain_type": "single", "wheel_count": 6,
                             "cargo_damage": self.cargo_damage, "wear_chassis": 0.02, "wear_wheels": 0.03,
                             "wear_body": 0.01, "license_plate": "TRL 4471", "license_plate_country": "California",
                             "wheel_on_ground": [1] * 6, "wheel_simulated": [1] * 6, "wheel_radius": [0.5] * 6,
                             "x": self.x, "y": 12.0, "z": self.z})
        return pack(v, trailers, SUBSTANCES)
