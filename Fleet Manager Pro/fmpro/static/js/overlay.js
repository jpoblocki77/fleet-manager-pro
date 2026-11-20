/* Fleet Manager Pro — stream overlay (OBS browser source, second screen or tablet). Polls /api/overlay twice a second. */
(function () {
  "use strict";
  const body = document.body;
  const key = body.dataset.key || "";
  const $ = (id) => document.getElementById(id);
  const set = (id, t) => { const n = $(id); if (n && n.textContent !== t) n.textContent = t; };
  const width = (id, pct) => { const n = $(id); if (n) n.style.width = Math.max(0, Math.min(100, pct)) + "%"; };
  const flag = (id, cls, on) => { const n = $(id); if (n) n.classList.toggle(cls, !!on); };
  const hm = (m) => { m = Math.max(0, Math.round(m)); const h = Math.floor(m / 60); return h + ":" + String(m % 60).padStart(2, "0"); };
  const HEX = /^#[0-9a-f]{6}$/i;
  let accentSet = false, fails = 0;

  function offline(status) {
    $("ov-offline").hidden = false;
    document.querySelectorAll(".pod[data-part]").forEach((p) => { p.hidden = true; });
    set("ov-status", status || "Start ATS or ETS2 with the telemetry plugin installed.");
  }

  function online() {
    $("ov-offline").hidden = true;
    document.querySelectorAll(".pod[data-part]").forEach((p) => { p.hidden = false; });
  }

  function brand(h) {
    if (!h) return;
    if (!accentSet && HEX.test(h.color || "")) { body.style.setProperty("--ov-accent", h.color); accentSet = true; }
    set("ov-company", h.company || "Fleet Manager Pro");
    set("ov-tag", (h.tag || "FMP").slice(0, 5));
    set("ov-driver", h.driver || "");
  }

  function speed(v) {
    const imp = FMP.imp();
    const cur = Math.abs(imp ? v.speed_mph : v.speed_kmh);
    const lim = imp ? v.speed_limit_mph : v.speed_limit_kmh;
    const top = imp ? 90 : 140;                                   // the dial's full sweep
    set("ov-speed", String(Math.round(cur)));
    set("ov-speed-u", FMP.speedU());
    const dial = $("ov-dial");
    if (dial) {
      dial.setAttribute("stroke-dasharray", Math.min(100, cur / top * 100).toFixed(1) + " 100");
      dial.style.opacity = cur < 0.5 ? 0 : 1;
    }
    const mark = $("ov-dial-limit");
    if (mark) {                                                   // a dot on the arc at the speed limit
      const a = Math.PI * Math.min(1, lim / top);
      mark.setAttribute("cx", (60 - 50 * Math.cos(a)).toFixed(1));
      mark.setAttribute("cy", (62 - 50 * Math.sin(a)).toFixed(1));
      mark.toggleAttribute("hidden", !(lim > 1));
    }
    const sign = $("ov-limit");
    if (sign) { sign.hidden = !(lim > 1); sign.textContent = lim > 1 ? String(Math.round(lim)) : ""; }
    const cc = $("ov-cruise");
    if (cc) { cc.hidden = !v.cruise_control; cc.textContent = v.cruise_control ? "CC " + Math.round(imp ? v.cruise_mph : v.cruise_kmh) : ""; }
    body.classList.toggle("speeding", !!v.speeding);
  }

  function gear(v) {
    set("ov-gear", v.gear_label || "N");
    const max = v.engine_rpm_max > 0 ? v.engine_rpm_max : 2500;
    width("ov-rpm-bar", v.engine_rpm / max * 100);
    flag("ov-rpm-bar", "hot", v.engine_rpm / max > 0.85);
    set("ov-rpm", Math.round(v.engine_rpm).toLocaleString() + " rpm");
  }

  function trip(v) {
    if (!$("ov-cargo")) return;
    if (!v.on_job) {
      set("ov-trip-lbl", "Free roam"); set("ov-pay", ""); set("ov-cargo", `${v.truck_brand || ""} ${v.truck_name || ""}`.trim() || "No job");
      set("ov-from", ""); set("ov-to", ""); width("ov-rail", 0); $("ov-truck").style.left = "0%";
      set("ov-left", "Pick up a load to start a trip"); return;
    }
    set("ov-trip-lbl", v.special_job ? "Special transport" : "On the job");
    set("ov-pay", v.job_income ? FMP.money(v.job_income) : "");
    set("ov-cargo", `${v.cargo} · ${FMP.mass(v.cargo_mass)}`);
    set("ov-from", v.source_city || ""); set("ov-to", v.destination_city || "");
    const plan = v.planned_distance_km || 0, left = v.navigation_km || 0;
    const done = plan > 0 ? Math.max(0, Math.min(100, (1 - left / plan) * 100)) : 0;
    width("ov-rail", done); $("ov-truck").style.left = done + "%";
    set("ov-left", left > 0 ? `${FMP.dist(left)} to go · about ${FMP.mins(v.navigation_time / 60)} driving` : "Arriving");
  }

  function deadline(v) {
    if (!$("ov-deadline")) return;
    const pod = $("ov-deadline").closest(".pod");
    if (!v.on_job || !v.job_delivery_time_abs) {
      set("ov-deadline", "—"); set("ov-eta", v.on_job ? "No deadline" : "No job"); pod.classList.remove("late", "tight"); return;
    }
    const m = v.delivery_eta_min, drive = (v.navigation_time || 0) / 60;
    set("ov-deadline", m < 0 ? "-" + hm(-m) : hm(m));
    pod.classList.toggle("late", m < 0);
    pod.classList.toggle("tight", m >= 0 && drive > 0 && drive > m * 0.85);
    set("ov-eta", m < 0 ? "Late" : drive > m ? "Behind schedule" : "On schedule");
  }

  function fuel(v) {
    if (!$("ov-fuel")) return;
    set("ov-fuel", Math.round(v.fuel_pct) + "%"); width("ov-fuel-bar", v.fuel_pct);
    set("ov-adblue", Math.round(v.adblue_pct) + "%"); width("ov-adblue-bar", v.adblue_pct);
    flag("ov-fuel-bar", "low", v.fuel_warning); flag("ov-adblue-bar", "low", v.adblue_warning);
    set("ov-range", v.fuel_range > 0 ? FMP.dist(v.fuel_range) + " range" : " ");
  }

  function hos(v, h) {
    if (!$("ov-hos-drive")) return;
    const c = h && h.hos;
    set("ov-rest", v.next_rest_stop > 0 ? hm(v.next_rest_stop) : "—");
    flag("ov-rest", "warn", v.next_rest_stop > 0 && v.next_rest_stop < 60);
    if (!c) { set("ov-hos-drive", "—"); set("ov-hos-break", "—"); set("ov-duty", ""); return; }
    set("ov-hos-drive", hm(Math.min(c.drive_left, c.window_left, c.cycle_left)));
    set("ov-hos-break", hm(c.break_left));
    flag("ov-hos-drive", "warn", c.available < 60); flag("ov-hos-break", "warn", c.break_left < 30);
    set("ov-duty", { driving: "Driving", on: "On duty", off: "Off duty", sleeper: "Sleeper" }[c.status] || "");
  }

  function damage(v) {
    if (!$("ov-dmg-truck")) return;
    const pct = (x) => (x < 10 ? x.toFixed(1) : Math.round(x)) + "%";
    set("ov-dmg-truck", pct(v.truck_damage_pct)); set("ov-dmg-trailer", pct(v.trailer_damage_pct || 0));
    set("ov-dmg-cargo", pct((v.cargo_damage || 0) * 100));
    flag("ov-dmg-cargo", "warn", v.cargo_damage > 0.05);
  }

  function fields(rows) {
    const box = $("ov-rows");
    if (!box) return;
    const html = (rows || []).map(([label, val, state]) =>
      `<div class="row-f"><span>${FMP.esc(label)}</span><b class="${state ? "st-" + state : ""}">${FMP.esc(val)}</b></div>`).join("");
    if (box.innerHTML !== html) box.innerHTML = html;
  }

  // pop-ups: logbook events (each arrives once) and Hours of Service warnings (shown when they first appear)
  let since = "", seenWarn = new Set(), lastDuty = null;
  function toast(text, level, secs) {
    const box = $("ov-toasts");
    if (!box) return;
    const t = document.createElement("div");
    t.className = "toast lv-" + (level || "info");
    t.textContent = text;
    box.appendChild(t);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 400); }, (secs || 9) * 1000);
  }
  function alerts(d) {
    (d.alerts || []).forEach((a) => toast(a.text, a.level));
    const now = new Set((d.warnings || []).map((w) => w.code));
    (d.warnings || []).forEach((w) => { if (!seenWarn.has(w.code)) toast(w.text, w.level, 12); });
    seenWarn = now;
    const duty = d.hud && d.hud.hos && d.hud.hos.status;
    if (duty && lastDuty && duty !== lastDuty)
      toast("Duty status: " + ({ driving: "Driving", on: "On duty", off: "Off duty", sleeper: "Sleeper berth" }[duty] || duty), "info", 6);
    if (duty) lastDuty = duty;
  }

  function tick() {
    const q = new URLSearchParams();
    if (key) q.set("key", key);
    if (since) q.set("since", since);
    fetch("/api/overlay?" + q.toString(), { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        fails = 0;
        since = String(d.server_time || "");
        alerts(d);
        brand(d.hud);
        if (!d.online || !d.values) return offline(d.status === "offline" ? "" : d.status);
        online();
        const v = d.values;
        speed(v); gear(v); trip(v); deadline(v); fuel(v); hos(v, d.hud); damage(v); fields(d.rows);
        set("ov-clock", v.game_clock || "—");
      })
      .catch((e) => { if (++fails > 3) offline(e === 401 ? "This overlay link is no longer valid. Copy a new one from Overlay setup." : "Can't reach Fleet Manager Pro."); });
  }

  tick();
  setInterval(tick, 500);
})();
