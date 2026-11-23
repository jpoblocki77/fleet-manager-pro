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

  const cents = (v) => FMP.cur + Number(v || 0).toLocaleString([], { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const perUnit = (p) => cents(imp ? p * 3.785411784 : p) + (imp ? "/gal" : "/L");
  // the price where the truck is now, and what filling this tank to the top would cost
  function priceRows(prices, price, room, isFuel) {
    if (!prices) return [];
    const where = isFuel ? (prices.source === "game" ? " in " + prices.place : " (company price)") : "";
    const left = Math.max(0, room || 0);
    return [[isFuel ? "Diesel price" : "Price", esc(perUnit(price) + where)],
      ["Fill it up", left < 0.5 ? "Full" : `${esc(FMP.liq(left, isFuel ? 0 : 1))} for <b>${esc(cents(left * price))}</b>`]];
  }

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
        ...priceRows(d.prices, d.prices && d.prices.fuel, v.fuel_capacity - v.fuel, true),
        ["Empty in", hrs ? (hrs >= 1 ? fixed(hrs, 1) + " h" : Math.round(hrs * 60) + " min") + " at this speed" : "—"]]);
      $("#fl-ad-kv").innerHTML = kv([["In the tank", FMP.liq(v.adblue, 1)], ["Tank size", FMP.liq(v.adblue_capacity)],
        ...priceRows(d.prices, d.prices && d.prices.adblue, v.adblue_capacity - v.adblue, false),
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
      stat("Diesel bought", cents(s.fuel_cost), (s.refuels || 0) + " fill-ups " + when) +
      stat("AdBlue bought", cents(s.adblue_cost), (s.topups || 0) + " fill-ups " + when);
    renderLog(d.refuel_log || [], when);
  }

  const logWhen = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  function renderLog(log, when) {
    $("#fl-log-when").textContent = log.length ? log.length + (log.length === 1 ? " fill " : " fills ") + when : "";
    if (!log.length) {
      $("#fl-log").innerHTML = `<p class="muted" style="margin:6px 2px">No refuels or AdBlue top-ups ${esc(when)}. Fill up in the game and each one is listed here.</p>`;
      return;
    }
    const rows = log.map((e) => {
      const isFuel = e.kind === "fuel";
      return `<tr><td>${esc(logWhen(e.t))}</td><td><i class="sw ${isFuel ? "fuel" : "adblue"}"></i>${isFuel ? "Diesel" : "AdBlue"}</td><td>${esc(e.place || "—")}</td>` +
        `<td class="num">${esc(FMP.liq(e.litres, isFuel ? 0 : 1))}</td><td class="num">${esc(perUnit(e.price))}</td><td class="num"><b>${esc(cents(e.cost))}</b></td>` +
        `<td class="num muted">${e.to_pct == null ? "—" : e.from_pct + "% → " + e.to_pct + "%"}</td></tr>`;
    }).join("");
    $("#fl-log").innerHTML = `<table class="small"><thead><tr><th>When</th><th>Fluid</th><th>Where</th><th class="num">Added</th><th class="num">Price</th><th class="num">Cost</th>` +
      `<th class="num">Level</th></tr></thead><tbody>${rows}</tbody></table>`;
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
