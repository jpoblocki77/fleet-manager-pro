/* Fuel & AdBlue: live tanks + bar graph of the last hour */
(function () {
  const { $, esc } = FMP;
  const NS = "http://www.w3.org/2000/svg";
  const W = 960, H = 200, PAD = 4;
  const imp = FMP.imp();
  const fixed = (n, d = 1) => Number(n || 0).toFixed(d);
  const ago = (sec) => sec >= 86400 * 2 ? Math.round(sec / 86400) + " days" : sec >= 86400 ? "1 day"
    : sec >= 7200 ? Math.round(sec / 3600) + " hours" : sec >= 3600 ? "1 hour" : Math.round(sec / 60) + " min";

  // colour of one level: the game's own warning level is red, twice that is amber
  function tone(pct, warnPct, kind) {
    if (pct <= warnPct) return "bad";
    if (pct <= warnPct * 2) return "warn";
    return kind;
  }

  function drawChart(svg, samples, key, capKey, warnFrac, kind, now, windowS, sampleS) {
    const warnPct = (warnFrac || 0.15) * 100;
    let html = "";
    for (const p of [25, 50, 75, 100]) {
      const y = H - (H - PAD) * p / 100;
      html += `<line x1="0" x2="${W}" y1="${y}" y2="${y}" class="fl-grid"/><text x="6" y="${y + 16}" class="fl-ylab">${p}%</text>`;
    }
    const slots = windowS / sampleS, bw = W / slots;
    for (const s of samples) {
      const cap = s[capKey];
      if (!cap) continue;
      const pct = Math.max(0, Math.min(100, s[key] / cap * 100));
      const age = now - s.t;
      if (age > windowS) continue;
      const x = W - (age / windowS) * W - bw;
      const h = Math.max(1, (H - PAD) * pct / 100);
      const label = `${new Date(s.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${Math.round(pct)}% · ${FMP.liq(s[key], key === "a" ? 1 : 0)}`;
      html += `<rect x="${Math.max(0, x + 0.6).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${Math.max(1, bw - 1.2).toFixed(1)}" height="${h.toFixed(1)}" class="fl-bar ${tone(pct, warnPct, kind)}"><title>${esc(label)}</title></rect>`;
    }
    const wy = H - (H - PAD) * warnPct / 100;
    html += `<line x1="0" x2="${W}" y1="${wy}" y2="${wy}" class="fl-warnline"/><text x="${W - 4}" y="${wy - 4}" text-anchor="end" class="fl-ylab warn">warning ${Math.round(warnPct)}%</text>`;
    if (!samples.length) html += `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="fl-empty">No readings yet - the bars fill in while the game is running</text>`;
    svg.innerHTML = html;
  }

  function tank(prefix, pct, warnFrac) {
    const p = Math.max(0, Math.min(100, pct || 0));
    $(`#fl-${prefix}-fill`).style.height = p + "%";
    $(`#fl-${prefix}-fill`).className = "fl-fill " + tone(p, (warnFrac || 0.15) * 100, prefix === "fuel" ? "fuel" : "adblue");
    $(`#fl-${prefix}-warn`).style.bottom = (warnFrac || 0.15) * 100 + "%";
    $(`#fl-${prefix}-pct`).textContent = Math.round(p) + "%";
    $(`#fl-${prefix}-tank`).setAttribute("aria-valuenow", Math.round(p));
  }

  const badge = (warn, pct, warnFrac) => warn ? '<span class="badge bad">Warning light on</span>'
    : pct <= (warnFrac || 0.15) * 200 ? '<span class="badge warn">Getting low</span>' : '<span class="badge ok">OK</span>';
  const kv = (rows) => rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

  function render(d) {
    const v = d.live;
    $("#fl-offline").style.display = v ? "none" : "";
    $("#fl-live").style.display = v ? "" : "none";
    if (v) {
      tank("fuel", v.fuel_pct, v.fuel_warning_factor);
      tank("ad", v.adblue_pct, v.adblue_warning_factor);
      $("#fl-fuel-badge").innerHTML = badge(v.fuel_warning, v.fuel_pct, v.fuel_warning_factor);
      $("#fl-ad-badge").innerHTML = badge(v.adblue_warning, v.adblue_pct, v.adblue_warning_factor);
      const per100 = (v.fuel_avg_consumption || 0) * 100;
      const cons = imp ? (per100 > 0 ? fixed(235.215 / per100, 1) + " mpg" : "—") : (per100 > 0 ? fixed(per100, 1) + " L/100 km" : "—");
      const hrs = v.speed_kmh > 5 && v.fuel_range > 0 ? v.fuel_range / v.speed_kmh : 0;
      $("#fl-fuel-kv").innerHTML = kv([["In the tank", FMP.liq(v.fuel)], ["Tank size", FMP.liq(v.fuel_capacity)],
        ["Range", v.fuel_range > 0 ? FMP.dist(v.fuel_range) : "—"], ["Average use", cons],
        ["Empty in", hrs ? (hrs >= 1 ? fixed(hrs, 1) + " h" : Math.round(hrs * 60) + " min") + " at this speed" : "—"]]);
      $("#fl-ad-kv").innerHTML = kv([["In the tank", FMP.liq(v.adblue, 1)], ["Tank size", FMP.liq(v.adblue_capacity)],
        ["Truck", esc(`${v.truck_brand || ""} ${v.truck_name || ""}`.trim() || "—")],
        ["Game", v.game === "ets2" ? "Euro Truck Simulator 2" : v.game === "ats" ? "American Truck Simulator" : "—"]]);
    }
    const h = d.history || [];
    const last = v || {};
    drawChart($("#fl-fuel-chart"), h, "f", "fc", last.fuel_warning_factor, "fuel", d.server_time, d.window_s, d.sample_s);
    drawChart($("#fl-ad-chart"), h, "a", "ac", last.adblue_warning_factor, "adblue", d.server_time, d.window_s, d.sample_s);
    const s = d.summary || {};
    const span = ago(d.window_s), when = "in the last " + span.replace(/^1 /, "");
    $("#fl-ax0").textContent = span + " ago";
    $("#fl-ax1").textContent = ago(d.window_s / 2) + " ago";
    $("#fl-range-title").textContent = $("#range").selectedOptions[0].textContent;
    $("#fl-step").textContent = "one bar every " + (d.sample_s < 60 ? Math.round(d.sample_s) + " seconds" : ago(d.sample_s)) + " · updates live";
    const stat = (label, value, sub) => `<div class="card stat"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
    $("#fl-sum").innerHTML = stat("Fuel used", FMP.liq(s.fuel_used || 0), when) +
      stat("AdBlue used", FMP.liq(s.adblue_used || 0, 1), when) +
      stat("Refuels", s.refuels || 0, when) + stat("AdBlue top-ups", s.topups || 0, when);
  }

  let busy = false;
  function tick() {
    if (busy) return;
    busy = true;
    FMP.get("/api/fluids?user=" + encodeURIComponent($("#who").value) + "&range=" + encodeURIComponent($("#range").value)).then(render).catch(() => {}).finally(() => { busy = false; });
  }
  const remember = () => history.replaceState(null, "", "?user=" + encodeURIComponent($("#who").value) + "&range=" + encodeURIComponent($("#range").value));
  $("#who").addEventListener("change", () => { remember(); tick(); });
  $("#range").addEventListener("change", () => { remember(); tick(); });
  tick();
  setInterval(tick, 2000);
})();
