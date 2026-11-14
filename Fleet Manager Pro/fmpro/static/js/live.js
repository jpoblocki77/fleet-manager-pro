/* Live cockpit */
(function () {
  const { $, esc } = FMP;
  const NS = "http://www.w3.org/2000/svg";

  function arcPath(cx, cy, r, a0, a1) {
    const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}`;
  }
  const A0 = Math.PI * 0.8, A1 = Math.PI * 2.2;
  function initGauge(svg, max, step, redFrom) {
    svg.innerHTML = "";
    const mk = (tag, at) => { const n = document.createElementNS(NS, tag); for (const k in at) n.setAttribute(k, at[k]); svg.appendChild(n); return n; };
    mk("path", { d: arcPath(100, 90, 78, A0, A1), stroke: "var(--panel-2)", "stroke-width": 14, fill: "none", "stroke-linecap": "round" });
    if (redFrom) mk("path", { d: arcPath(100, 90, 78, A0 + (A1 - A0) * (redFrom / max), A1), stroke: "rgba(200,16,46,.35)", "stroke-width": 14, fill: "none" });
    const val = mk("path", { d: arcPath(100, 90, 78, A0, A0 + 0.001), stroke: "url(#gg)", "stroke-width": 14, fill: "none", "stroke-linecap": "round" });
    const defs = mk("defs", {});
    defs.innerHTML = '<linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="#2a5592"/><stop offset="1" stop-color="#e23552"/></linearGradient>';
    for (let v = 0; v <= max; v += step) {
      const a = A0 + (A1 - A0) * (v / max);
      const t = mk("text", { x: 100 + 58 * Math.cos(a), y: 94 + 58 * Math.sin(a), "text-anchor": "middle", fill: "var(--muted)", "font-size": 9, "font-weight": 600 });
      t.textContent = v >= 1000 ? v / 1000 + "k" : v;
    }
    const needle = mk("line", { x1: 100, y1: 90, x2: 100, y2: 26, stroke: "#e23552", "stroke-width": 3, "stroke-linecap": "round" });
    mk("circle", { cx: 100, cy: 90, r: 6, fill: "#e23552" });
    return { set(v) { const f = Math.max(0, Math.min(1, v / max)); const a = A0 + (A1 - A0) * f;
      val.setAttribute("d", arcPath(100, 90, 78, A0, Math.max(A0 + 0.001, a)));
      needle.setAttribute("x2", 100 + 64 * Math.cos(a)); needle.setAttribute("y2", 90 + 64 * Math.sin(a)); } };
  }
  const imp = FMP.imp();
  const gSpeed = initGauge($("#g-speed"), imp ? 90 : 140, imp ? 10 : 20, imp ? 75 : 120);
  let gRpm = initGauge($("#g-rpm"), 3000, 500, 2300), rpmMax = 3000;
  $("#u-speed").textContent = FMP.speedU();

  const pctBar = (label, frac, cls = "navy", txt) => `<div style="margin-bottom:8px"><div class="between small"><span class="muted">${label}</span><b>${txt ?? Math.round(frac * 100) + "%"}</b></div><div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(100, frac * 100))}%"></i></div></div>`;
  const kv = (rows) => `<dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const lamp = (label, on, color = "") => `<span class="lamp ${on ? "on " + color : ""}">${label}</span>`;
  const fixed = (n, d = 1) => Number(n).toFixed(d);

  function render(d) {
    const v = d.values, j = d.job;
    gSpeed.set(Math.abs(FMP.speed(v.speed_kmh)));
    if (v.engine_rpm_max && Math.abs(v.engine_rpm_max - rpmMax) > 50) { rpmMax = Math.ceil(v.engine_rpm_max / 500) * 500; gRpm = initGauge($("#g-rpm"), rpmMax, 500, v.engine_rpm_max * 0.9); }
    gRpm.set(v.engine_rpm);
    $("#v-speed").textContent = Math.round(Math.abs(FMP.speed(v.speed_kmh)));
    $("#v-rpm").textContent = Math.round(v.engine_rpm);
    $("#v-gear").textContent = v.gear_label;
    const lim = imp ? v.speed_limit_mph : v.speed_limit_kmh;
    const sign = $("#v-limit");
    sign.className = "sign" + (imp ? "" : " eu");
    sign.innerHTML = imp ? `<small>SPEED<br>LIMIT</small><b>${lim > 0 ? Math.round(lim / 5) * 5 : "—"}</b>` : `<b>${lim > 0 ? Math.round(lim) : "—"}</b>`;
    sign.style.outline = v.speeding ? "3px solid #e23552" : "";
    $("#v-cruise").textContent = v.cruise_control ? Math.round(imp ? v.cruise_mph : v.cruise_kmh) : "off";
    $("#v-clock").textContent = v.game_clock;

    // job
    if (v.on_job || v.job_delivered) {
      const dl = v.delivery_eta_min;
      $("#v-jobstate").innerHTML = v.special_job ? '<span class="badge crimson">special</span>' : '<span class="badge in_progress">on job</span>';
      $("#v-job").innerHTML = `<div style="font-size:18px;font-weight:700">${esc(v.cargo)} <span class="muted small">${FMP.mass(v.cargo_mass)}${v.unit_count > 1 ? " · " + v.unit_count + " units" : ""}</span></div>
        <div class="muted" style="margin-bottom:10px">${esc(v.source_company)}, ${esc(v.source_city)} → <b style="color:var(--text)">${esc(v.destination_company)}, ${esc(v.destination_city)}</b></div>` +
        kv([["Income", FMP.money(v.job_income)], ["Planned", FMP.dist(v.planned_distance_km)], ["Remaining", FMP.dist(v.navigation_km, 1)],
          ["Nav ETA", FMP.mins(v.navigation_time / 60)], ["Deadline", v.job_delivery_time_abs ? FMP.mins(dl) : "none"],
          ["Cargo damage", fixed(v.cargo_damage * 100, 1) + "%"], ["Market", esc(v.job_market.replace(/_/g, " "))],
          j && ["Driven (logged)", FMP.dist(j.driven_km, 1)], j && ["Fuel used", FMP.liq(j.fuel_used)],
          j && ["Tolls / fines", FMP.money(j.tolls) + " / " + FMP.money(j.fines)], j && ["Top speed", Math.round(FMP.speed(j.max_speed)) + " " + FMP.speedU()],
          j && j.driving_s > 0 && ["Speeding", fixed(j.speeding_s / j.driving_s * 100, 1) + "% of time"],
          j && j.contract_id && ["Contract", `<a href="/dispatch">#${j.contract_id}</a>`]]);
    } else {
      $("#v-jobstate").innerHTML = '<span class="badge">free roam</span>';
      $("#v-job").innerHTML = '<p class="muted">No job loaded. Pick one up from the freight market, cargo market or a contract.</p>';
    }
    // truck
    $("#v-plate").textContent = `${v.license_plate} · ${v.license_plate_country}`;
    $("#v-truck").innerHTML = `<div style="font-size:18px;font-weight:700">${esc(v.truck_brand)} ${esc(v.truck_name)}</div><div class="muted small" style="margin-bottom:10px">${esc(v.truck_id)}</div>` +
      kv([["Odometer", FMP.dist(v.odometer)], ["Gears", `${v.gear_count_forward} fwd / ${v.gear_count_reverse} rev`], ["Shifter", esc(v.shifter_type)],
        ["Retarder", `${v.retarder_level} / ${v.retarder_step_count}`], ["Differential", fixed(v.gear_differential, 2)], ["Fuel tank", FMP.liq(v.fuel_capacity)],
        ["Engine", v.engine_enabled ? "running" : "off"], ["Game", `${esc(v.game.toUpperCase())} · plugin rev ${Number(v.plugin_revision)}`], ["Source", esc(d.source) + (d.age > 2 ? ` (${d.age}s old)` : "")]]);
    // fluids
    $("#v-fluids").innerHTML = pctBar("Fuel " + FMP.liq(v.fuel), v.fuel_pct / 100, v.fuel_warning ? "" : "navy") +
      pctBar("AdBlue " + FMP.liq(v.adblue), v.adblue_pct / 100, v.adblue_warning ? "" : "navy") +
      kv([["Range", FMP.dist(v.fuel_range)], ["Avg consumption", imp ? fixed(235.215 / Math.max(0.01, v.fuel_avg_consumption * 100), 1) + " mpg" : fixed(v.fuel_avg_consumption * 100, 1) + " L/100km"],
        ["Air pressure", FMP.pres(v.air_pressure)], ["Brake temp", FMP.temp(v.brake_temperature)], ["Oil pressure", FMP.pres(v.oil_pressure)],
        ["Oil temp", FMP.temp(v.oil_temperature)], ["Water temp", FMP.temp(v.water_temperature)], ["Battery", fixed(v.battery_voltage, 1) + " V"]]);
    // damage
    $("#v-damage").innerHTML = [["Engine", v.wear_engine], ["Transmission", v.wear_transmission], ["Cabin", v.wear_cabin], ["Chassis", v.wear_chassis], ["Wheels", v.wear_wheels]]
      .map(([k, w]) => pctBar(k, w, w > 0.1 ? "" : w > 0.03 ? "warn" : "ok", fixed(w * 100, 1) + "%")).join("") +
      pctBar("Cargo", v.cargo_damage, v.cargo_damage > 0.05 ? "" : "ok", fixed(v.cargo_damage * 100, 1) + "%");
    // inputs
    $("#v-inputs").innerHTML = pctBar("Throttle", v.effective_throttle, "ok") + pctBar("Brake", v.effective_brake, "") + pctBar("Clutch", v.effective_clutch, "warn") +
      pctBar("Steering", (v.effective_steering + 1) / 2, "navy", fixed(v.effective_steering * 100, 0)) +
      `<div class="lamps">${lamp("Parking brake", v.parking_brake, "red")}${lamp("Engine brake", v.motor_brake, "amber")}${lamp("Diff lock", v.differential_lock, "amber")}${lamp("Lift axle", v.lift_axle)}${lamp("Trailer lift axle", v.trailer_lift_axle)}${lamp("Wipers", v.wipers)}</div>`;
    $("#v-lamps").innerHTML = [lamp("◀ Left", v.blinker_left_on, "green"), lamp("Right ▶", v.blinker_right_on, "green"), lamp("Hazard", v.lights_hazard, "amber"),
      lamp("Parking", v.lights_parking), lamp("Low beam", v.lights_beam_low, "green"), lamp("High beam", v.lights_beam_high), lamp("Beacon", v.lights_beacon, "amber"),
      lamp("Brake", v.lights_brake, "red"), lamp("Reverse", v.lights_reverse), lamp("Aux front " + v.lights_aux_front, v.lights_aux_front > 0), lamp("Aux roof " + v.lights_aux_roof, v.lights_aux_roof > 0),
      lamp("Electric", v.electric_enabled, "green"), lamp("Engine", v.engine_enabled, "green"), lamp("Cruise", v.cruise_control, "green")].join("");
    $("#v-warn").innerHTML = [lamp("Fuel", v.fuel_warning, "red"), lamp("AdBlue", v.adblue_warning, "red"), lamp("Air pressure", v.air_pressure_warning, "red"),
      lamp("Air EMERGENCY", v.air_pressure_emergency, "red"), lamp("Oil pressure", v.oil_pressure_warning, "red"), lamp("Water temp", v.water_temperature_warning, "red"),
      lamp("Battery", v.battery_voltage_warning, "red"), lamp("Speeding", v.speeding, "red")].join("");
    $("#v-nav").innerHTML = kv([["Route distance", FMP.dist(v.navigation_km, 1)], ["Route time", FMP.mins(v.navigation_time / 60)],
      ["Next rest stop", v.next_rest_stop > 0 ? FMP.mins(v.next_rest_stop) : "—"], ["Heading", fixed(v.heading_deg, 0) + "°"],
      ["World X / Y / Z", `${fixed(v.x, 0)} / ${fixed(v.y, 0)} / ${fixed(v.z, 0)}`], ["Pitch / roll", `${fixed(v.pitch * 360, 1)}° / ${fixed(v.roll * 360, 1)}°`],
      ["Accel (x,y,z)", v.linear_acceleration.map((n) => fixed(n, 2)).join(", ") + " m/s²"], ["Time scale", fixed(v.time_scale, 1) + "×"],
      ["Paused", v.paused ? "yes" : "no"], ["Multiplayer offset", v.multiplayer_time_offset]]);
    // trailers
    const trs = d.trailers || [];
    $("#v-trailers").innerHTML = trs.length ? trs.map((t) => `<div style="padding:8px 0;border-bottom:1px solid var(--line-soft)"><div class="between"><b>${esc(t.name || t.id)}</b>${t.attached ? '<span class="badge ok">attached</span>' : '<span class="badge">detached</span>'}</div>
      <div class="small muted">${esc(t.brand)} · ${esc(t.body_type)} · ${esc(t.chain_type)} · ${esc(t.license_plate)} ${esc(t.license_plate_country)} · ${Number(t.wheel_count)} wheels</div>
      <div class="small">Wear body ${fixed(t.wear_body * 100)}% · chassis ${fixed(t.wear_chassis * 100)}% · wheels ${fixed(t.wear_wheels * 100)}% · cargo ${fixed(t.cargo_damage * 100)}%</div></div>`).join("")
      : '<p class="muted">No trailer.</p>';
    const wc = v.truck_wheel_count;
    $("#v-wheelcount").textContent = wc + " wheels";
    const subs = d.substances || [];
    $("#v-wheels").innerHTML = Array.from({ length: wc }, (_, i) => `<div class="wheel"><b>#${i + 1} ${v.wheel_powered[i] ? "⚙" : ""}${v.wheel_steerable[i] ? "↻" : ""}</b>
      ${v.wheel_on_ground[i] ? "on ground" : "<span class='neg'>airborne</span>"}<br>${esc(subs[v.wheel_substance[i]] || "#" + v.wheel_substance[i])}<br>
      susp ${fixed(v.wheel_suspension_deflection[i] * 100, 1)} cm · r ${fixed(v.wheel_radius[i], 2)} m${v.wheel_liftable[i] ? "<br>lift " + fixed(v.wheel_lift[i], 2) : ""}</div>`).join("");
  }

  function tick() {
    FMP.get("/api/live?user=" + $("#who").value).then((d) => {
      $("#offline").style.display = d.online ? "none" : "";
      $("#cockpit").style.display = d.online ? "" : "none";
      if (!d.online) { $("#off-msg").textContent = d.status; return; }
      render(d);
    }).catch(() => {});
  }
  const hm = (m) => `${Math.floor(Math.max(0, m) / 60)}:${String(Math.max(0, m) % 60).padStart(2, "0")}`;
  function hos() {
    FMP.get("/api/hos?user=" + $("#who").value).then((d) => {
      const c = d.clocks;
      if (!c) { $("#v-hos").innerHTML = '<p class="muted">No logbook yet.</p>'; return; }
      $("#v-hosrules").textContent = `${c.rules_label} · ${d.statuses[c.status]}`;
      $("#v-hos").innerHTML = [["Driving left", c.drive], ["Window left", c.window], ["Until break", c.break], ["Cycle left", c.cycle]]
        .map(([l, k]) => `<div><div class="between small"><span class="muted">${l}</span><b class="${k.over ? "neg" : ""}">${hm(k.left)}</b></div>
          <div class="bar ${k.pct < 75 ? "ok" : k.pct < 100 ? "warn" : ""}"><i style="width:${k.pct}%"></i></div></div>`).join("");
      $("#v-hosholds").innerHTML = d.holds.length ? `<div class="small neg" style="margin-top:8px">⛔ ${d.holds.map(esc).join(" · ")}</div>` : "";
    }).catch(() => {});
  }
  $("#who").addEventListener("change", () => { tick(); hos(); });
  tick(); setInterval(tick, 250);
  hos(); setInterval(hos, 5000);
})();
