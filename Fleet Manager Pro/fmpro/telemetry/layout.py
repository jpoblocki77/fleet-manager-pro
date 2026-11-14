"""Complete memory map of the scs-telemetry.dll shared memory block ("Local\\SCSTelemetry").

The plugin (scs-sdk-plugin, MIT licence, bundled in /plugin) writes one fixed-layout
structure of 32 KiB.  It is organised in zones by data type.  Every data point the
plugin publishes is listed here, so the parser exposes all of it.

Each entry: (key, group, kind, offset, count)
    kind  : bool | u32 | i32 | f32 | f64 | u64 | i64 | str
    count : array length for numeric kinds, byte length for str
"""

MMF_NAME = "Local\\SCSTelemetry"
MMF_SIZE = 32 * 1024

W = 16          # wheel slots in the plugin
TRAILERS = 10   # trailer slots
TRAILER_BASE = 6000
TRAILER_SIZE = 1560

FIELDS = [
    # ---------------------------------------------------------------- zone 1: header
    ("sdk_active", "game", "bool", 0, 1),
    ("paused", "game", "bool", 4, 1),
    ("time", "game", "u64", 8, 1),
    ("simulated_time", "game", "u64", 16, 1),
    ("render_time", "game", "u64", 24, 1),
    ("multiplayer_time_offset", "game", "i64", 32, 1),
    # ---------------------------------------------------------------- zone 2: unsigned int
    ("plugin_revision", "game", "u32", 40, 1),
    ("game_version_major", "game", "u32", 44, 1),
    ("game_version_minor", "game", "u32", 48, 1),
    ("game_id", "game", "u32", 52, 1),
    ("telemetry_version_major", "game", "u32", 56, 1),
    ("telemetry_version_minor", "game", "u32", 60, 1),
    ("game_time_abs", "game", "u32", 64, 1),                 # minutes since game start
    ("gear_count_forward", "config", "u32", 68, 1),
    ("gear_count_reverse", "config", "u32", 72, 1),
    ("retarder_step_count", "config", "u32", 76, 1),
    ("truck_wheel_count", "config", "u32", 80, 1),
    ("selector_count", "config", "u32", 84, 1),
    ("job_delivery_time_abs", "job", "u32", 88, 1),
    ("max_trailer_count", "config", "u32", 92, 1),
    ("unit_count", "job", "u32", 96, 1),
    ("planned_distance_km", "job", "u32", 100, 1),
    ("shifter_slot", "controls", "u32", 104, 1),
    ("retarder_level", "controls", "u32", 108, 1),
    ("lights_aux_front", "lights", "u32", 112, 1),
    ("lights_aux_roof", "lights", "u32", 116, 1),
    ("wheel_substance", "wheels", "u32", 120, W),
    ("hshifter_position", "config", "u32", 184, 32),
    ("hshifter_bitmask", "config", "u32", 312, 32),
    ("job_delivered_delivery_time", "events", "u32", 440, 1),
    ("job_starting_time", "job", "u32", 444, 1),
    ("job_finished_time", "events", "u32", 448, 1),
    # ---------------------------------------------------------------- zone 3: int
    ("next_rest_stop", "navigation", "i32", 500, 1),         # minutes
    ("gear", "drivetrain", "i32", 504, 1),
    ("gear_dashboard", "drivetrain", "i32", 508, 1),
    ("hshifter_resulting", "config", "i32", 512, 32),
    ("job_delivered_earned_xp", "events", "i32", 640, 1),
    # ---------------------------------------------------------------- zone 4: float
    ("time_scale", "game", "f32", 700, 1),
    ("fuel_capacity", "config", "f32", 704, 1),
    ("fuel_warning_factor", "config", "f32", 708, 1),
    ("adblue_capacity", "config", "f32", 712, 1),
    ("adblue_warning_factor", "config", "f32", 716, 1),
    ("air_pressure_warning_level", "config", "f32", 720, 1),
    ("air_pressure_emergency_level", "config", "f32", 724, 1),
    ("oil_pressure_warning_level", "config", "f32", 728, 1),
    ("water_temperature_warning_level", "config", "f32", 732, 1),
    ("battery_voltage_warning_level", "config", "f32", 736, 1),
    ("engine_rpm_max", "config", "f32", 740, 1),
    ("gear_differential", "config", "f32", 744, 1),
    ("cargo_mass", "job", "f32", 748, 1),                      # kg
    ("wheel_radius", "wheels", "f32", 752, W),
    ("gear_ratios_forward", "config", "f32", 816, 24),
    ("gear_ratios_reverse", "config", "f32", 912, 8),
    ("unit_mass", "job", "f32", 944, 1),
    ("speed", "drivetrain", "f32", 948, 1),                    # m/s, negative when reversing
    ("engine_rpm", "drivetrain", "f32", 952, 1),
    ("input_steering", "controls", "f32", 956, 1),
    ("input_throttle", "controls", "f32", 960, 1),
    ("input_brake", "controls", "f32", 964, 1),
    ("input_clutch", "controls", "f32", 968, 1),
    ("effective_steering", "controls", "f32", 972, 1),
    ("effective_throttle", "controls", "f32", 976, 1),
    ("effective_brake", "controls", "f32", 980, 1),
    ("effective_clutch", "controls", "f32", 984, 1),
    ("cruise_control_speed", "drivetrain", "f32", 988, 1),   # m/s
    ("air_pressure", "engine", "f32", 992, 1),               # psi
    ("brake_temperature", "engine", "f32", 996, 1),          # deg C
    ("fuel", "engine", "f32", 1000, 1),                      # litres
    ("fuel_avg_consumption", "engine", "f32", 1004, 1),      # l/km
    ("fuel_range", "engine", "f32", 1008, 1),                # km
    ("adblue", "engine", "f32", 1012, 1),                    # litres
    ("oil_pressure", "engine", "f32", 1016, 1),              # psi
    ("oil_temperature", "engine", "f32", 1020, 1),
    ("water_temperature", "engine", "f32", 1024, 1),
    ("battery_voltage", "engine", "f32", 1028, 1),
    ("dashboard_backlight", "lights", "f32", 1032, 1),
    ("wear_engine", "damage", "f32", 1036, 1),
    ("wear_transmission", "damage", "f32", 1040, 1),
    ("wear_cabin", "damage", "f32", 1044, 1),
    ("wear_chassis", "damage", "f32", 1048, 1),
    ("wear_wheels", "damage", "f32", 1052, 1),
    ("odometer", "drivetrain", "f32", 1056, 1),              # km
    ("navigation_distance", "navigation", "f32", 1060, 1),   # m
    ("navigation_time", "navigation", "f32", 1064, 1),       # s
    ("speed_limit", "navigation", "f32", 1068, 1),           # m/s
    ("wheel_suspension_deflection", "wheels", "f32", 1072, W),
    ("wheel_velocity", "wheels", "f32", 1136, W),
    ("wheel_steering", "wheels", "f32", 1200, W),
    ("wheel_rotation", "wheels", "f32", 1264, W),
    ("wheel_lift", "wheels", "f32", 1328, W),
    ("wheel_lift_offset", "wheels", "f32", 1392, W),
    ("job_delivered_cargo_damage", "events", "f32", 1456, 1),
    ("job_delivered_distance_km", "events", "f32", 1460, 1),
    ("refuel_amount", "events", "f32", 1464, 1),
    ("cargo_damage", "job", "f32", 1468, 1),
    # ---------------------------------------------------------------- zone 5: bool
    ("wheel_steerable", "wheels", "bool", 1500, W),
    ("wheel_simulated", "wheels", "bool", 1516, W),
    ("wheel_powered", "wheels", "bool", 1532, W),
    ("wheel_liftable", "wheels", "bool", 1548, W),
    ("cargo_loaded", "job", "bool", 1564, 1),
    ("special_job", "job", "bool", 1565, 1),
    ("parking_brake", "controls", "bool", 1566, 1),
    ("motor_brake", "controls", "bool", 1567, 1),
    ("air_pressure_warning", "warnings", "bool", 1568, 1),
    ("air_pressure_emergency", "warnings", "bool", 1569, 1),
    ("fuel_warning", "warnings", "bool", 1570, 1),
    ("adblue_warning", "warnings", "bool", 1571, 1),
    ("oil_pressure_warning", "warnings", "bool", 1572, 1),
    ("water_temperature_warning", "warnings", "bool", 1573, 1),
    ("battery_voltage_warning", "warnings", "bool", 1574, 1),
    ("electric_enabled", "engine", "bool", 1575, 1),
    ("engine_enabled", "engine", "bool", 1576, 1),
    ("wipers", "controls", "bool", 1577, 1),
    ("blinker_left_active", "lights", "bool", 1578, 1),
    ("blinker_right_active", "lights", "bool", 1579, 1),
    ("blinker_left_on", "lights", "bool", 1580, 1),
    ("blinker_right_on", "lights", "bool", 1581, 1),
    ("lights_parking", "lights", "bool", 1582, 1),
    ("lights_beam_low", "lights", "bool", 1583, 1),
    ("lights_beam_high", "lights", "bool", 1584, 1),
    ("lights_beacon", "lights", "bool", 1585, 1),
    ("lights_brake", "lights", "bool", 1586, 1),
    ("lights_reverse", "lights", "bool", 1587, 1),
    ("lights_hazard", "lights", "bool", 1588, 1),
    ("cruise_control", "drivetrain", "bool", 1589, 1),
    ("wheel_on_ground", "wheels", "bool", 1590, W),
    ("shifter_toggle", "controls", "bool", 1606, 2),
    ("differential_lock", "controls", "bool", 1608, 1),
    ("lift_axle", "controls", "bool", 1609, 1),
    ("lift_axle_indicator", "controls", "bool", 1610, 1),
    ("trailer_lift_axle", "controls", "bool", 1611, 1),
    ("trailer_lift_axle_indicator", "controls", "bool", 1612, 1),
    ("job_delivered_autopark_used", "events", "bool", 1613, 1),
    ("job_delivered_autoload_used", "events", "bool", 1614, 1),
    # ---------------------------------------------------------------- zone 6: float vectors
    ("cabin_position", "vectors", "f32", 1640, 3),
    ("head_position", "vectors", "f32", 1652, 3),
    ("hook_position", "vectors", "f32", 1664, 3),
    ("wheel_position_x", "wheels", "f32", 1676, W),
    ("wheel_position_y", "wheels", "f32", 1740, W),
    ("wheel_position_z", "wheels", "f32", 1804, W),
    ("linear_velocity", "vectors", "f32", 1868, 3),
    ("angular_velocity", "vectors", "f32", 1880, 3),
    ("linear_acceleration", "vectors", "f32", 1892, 3),
    ("angular_acceleration", "vectors", "f32", 1904, 3),
    ("cabin_angular_velocity", "vectors", "f32", 1916, 3),
    ("cabin_angular_acceleration", "vectors", "f32", 1928, 3),
    # ---------------------------------------------------------------- zone 7: float placements
    ("cabin_offset", "vectors", "f32", 2000, 3),
    ("cabin_offset_rotation", "vectors", "f32", 2012, 3),
    ("head_offset", "vectors", "f32", 2024, 3),
    ("head_offset_rotation", "vectors", "f32", 2036, 3),
    # ---------------------------------------------------------------- zone 8: world placement
    ("x", "position", "f64", 2200, 1),
    ("y", "position", "f64", 2208, 1),
    ("z", "position", "f64", 2216, 1),
    ("heading", "position", "f64", 2224, 1),     # 0..1 turns
    ("pitch", "position", "f64", 2232, 1),
    ("roll", "position", "f64", 2240, 1),
    # ---------------------------------------------------------------- zone 9: strings
    ("truck_brand_id", "truck", "str", 2300, 64),
    ("truck_brand", "truck", "str", 2364, 64),
    ("truck_id", "truck", "str", 2428, 64),
    ("truck_name", "truck", "str", 2492, 64),
    ("cargo_id", "job", "str", 2556, 64),
    ("cargo", "job", "str", 2620, 64),
    ("destination_city_id", "job", "str", 2684, 64),
    ("destination_city", "job", "str", 2748, 64),
    ("destination_company_id", "job", "str", 2812, 64),
    ("destination_company", "job", "str", 2876, 64),
    ("source_city_id", "job", "str", 2940, 64),
    ("source_city", "job", "str", 3004, 64),
    ("source_company_id", "job", "str", 3068, 64),
    ("source_company", "job", "str", 3132, 64),
    ("shifter_type", "config", "str", 3196, 16),
    ("license_plate", "truck", "str", 3212, 64),
    ("license_plate_country_id", "truck", "str", 3276, 64),
    ("license_plate_country", "truck", "str", 3340, 64),
    ("job_market", "job", "str", 3404, 32),
    ("fine_offence", "events", "str", 3436, 32),
    ("ferry_source_name", "events", "str", 3468, 64),
    ("ferry_target_name", "events", "str", 3532, 64),
    ("ferry_source_id", "events", "str", 3596, 64),
    ("ferry_target_id", "events", "str", 3660, 64),
    ("train_source_name", "events", "str", 3724, 64),
    ("train_target_name", "events", "str", 3788, 64),
    ("train_source_id", "events", "str", 3852, 64),
    ("train_target_id", "events", "str", 3916, 64),
    # ---------------------------------------------------------------- zone 10/11: 64-bit
    ("job_income", "job", "u64", 4000, 1),
    ("job_cancelled_penalty", "events", "i64", 4200, 1),
    ("job_delivered_revenue", "events", "i64", 4208, 1),
    ("fine_amount", "events", "i64", 4216, 1),
    ("toll_amount", "events", "i64", 4224, 1),
    ("ferry_amount", "events", "i64", 4232, 1),
    ("train_amount", "events", "i64", 4240, 1),
    # ---------------------------------------------------------------- zone 12: event flags
    ("on_job", "flags", "bool", 4300, 1),
    ("job_finished", "flags", "bool", 4301, 1),
    ("job_cancelled", "flags", "bool", 4302, 1),
    ("job_delivered", "flags", "bool", 4303, 1),
    ("fined", "flags", "bool", 4304, 1),
    ("tollgate", "flags", "bool", 4305, 1),
    ("ferry", "flags", "bool", 4306, 1),
    ("train", "flags", "bool", 4307, 1),
    ("refuel", "flags", "bool", 4308, 1),
    ("refuel_paid", "flags", "bool", 4309, 1),
]

SUBSTANCE_BASE, SUBSTANCE_COUNT, SUBSTANCE_LEN = 4400, 25, 64

# Offsets inside one trailer slot
TRAILER_FIELDS = [
    ("wheel_steerable", "bool", 0, W),
    ("wheel_simulated", "bool", 16, W),
    ("wheel_powered", "bool", 32, W),
    ("wheel_liftable", "bool", 48, W),
    ("wheel_on_ground", "bool", 64, W),
    ("attached", "bool", 80, 1),
    ("wheel_substance", "u32", 84, W),
    ("wheel_count", "u32", 148, 1),
    ("cargo_damage", "f32", 152, 1),
    ("wear_chassis", "f32", 156, 1),
    ("wear_wheels", "f32", 160, 1),
    ("wear_body", "f32", 164, 1),
    ("wheel_suspension_deflection", "f32", 168, W),
    ("wheel_velocity", "f32", 232, W),
    ("wheel_steering", "f32", 296, W),
    ("wheel_rotation", "f32", 360, W),
    ("wheel_lift", "f32", 424, W),
    ("wheel_lift_offset", "f32", 488, W),
    ("wheel_radius", "f32", 552, W),
    ("linear_velocity", "f32", 616, 3),
    ("angular_velocity", "f32", 628, 3),
    ("linear_acceleration", "f32", 640, 3),
    ("angular_acceleration", "f32", 652, 3),
    ("hook_position", "f32", 664, 3),
    ("wheel_position_x", "f32", 676, W),
    ("wheel_position_y", "f32", 740, W),
    ("wheel_position_z", "f32", 804, W),
    ("x", "f64", 872, 1),
    ("y", "f64", 880, 1),
    ("z", "f64", 888, 1),
    ("heading", "f64", 896, 1),
    ("pitch", "f64", 904, 1),
    ("roll", "f64", 912, 1),
    ("id", "str", 920, 64),
    ("cargo_accessory_id", "str", 984, 64),
    ("body_type", "str", 1048, 64),
    ("brand_id", "str", 1112, 64),
    ("brand", "str", 1176, 64),
    ("name", "str", 1240, 64),
    ("chain_type", "str", 1304, 64),
    ("license_plate", "str", 1368, 64),
    ("license_plate_country", "str", 1432, 64),
    ("license_plate_country_id", "str", 1496, 64),
]

# Human labels / units used by the telemetry explorer
UNITS = {
    "speed": "m/s", "cruise_control_speed": "m/s", "speed_limit": "m/s",
    "engine_rpm": "rpm", "engine_rpm_max": "rpm", "fuel": "L", "fuel_capacity": "L",
    "adblue": "L", "adblue_capacity": "L", "fuel_avg_consumption": "L/km", "fuel_range": "km",
    "air_pressure": "psi", "oil_pressure": "psi", "brake_temperature": "°C",
    "oil_temperature": "°C", "water_temperature": "°C", "battery_voltage": "V",
    "odometer": "km", "navigation_distance": "m", "navigation_time": "s",
    "cargo_mass": "kg", "unit_mass": "kg", "next_rest_stop": "min", "game_time_abs": "min",
    "job_delivery_time_abs": "min", "planned_distance_km": "km", "refuel_amount": "L",
    "job_delivered_distance_km": "km",
}

GROUP_ORDER = ["game", "truck", "drivetrain", "engine", "controls", "lights", "warnings",
               "damage", "job", "navigation", "position", "events", "flags", "config",
               "vectors", "wheels", "trailers", "substances"]

GAMES = {0: "unknown", 1: "ets2", 2: "ats"}
