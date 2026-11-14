/* Telemetry explorer: renders every field from META and keeps values live. */
(function () {
  const { $, $$, esc } = FMP;
  let paused = false, group = "all", last = null, built = false;
  const prev = {};
  const DERIVED = ["game", "speed_kmh", "speed_mph", "speed_limit_kmh", "speed_limit_mph", "cruise_kmh", "cruise_mph", "fuel_pct",
    "adblue_pct", "gear_label", "game_clock", "delivery_eta_min", "navigation_km", "navigation_mi", "cargo_tonnes",
    "truck_damage_pct", "trailer_damage_pct", "trailers_attached", "heading_deg", "speeding"];

  const fmt = (v) => {
    if (Array.isArray(v)) return v.length ? "[" + v.map(fmt).join(", ") + "]" : "[]";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
    if (typeof v === "boolean") return v ? "true" : "false";
    return v === "" ? '""' : String(v);
  };
  function card(title, id, rows, note = "") {
    return `<div class="card flush" data-group="${id}"><div class="card-head" style="padding:14px 16px 4px"><h2 style="text-transform:capitalize">${esc(title)}</h2><span class="right small muted">${note}</span></div>
      <table class="tx-table"><tbody>${rows.map((r) => `<tr data-k="${esc(r.key)}" data-path="${esc(r.path)}"><td class="k">${esc(r.key)}${r.unit ? ` <span class="faint small">${esc(r.unit)}</span>` : ""}</td>
      <td class="faint">${esc(r.kind || "")}${r.offset !== undefined ? " @" + r.offset : ""}</td><td class="v" data-v></td></tr>`).join("")}</tbody></table></div>`;
  }
  function build(d) {
    const html = [];
    ORDER.forEach((g) => {
      if (META[g]) html.push(card(g, g, META[g].map((f) => ({ key: f.key, path: "values." + f.key, kind: f.kind + (f.count > 1 && f.kind !== "str" ? `[${f.count}]` : ""), offset: f.offset, unit: f.unit })), META[g].length + " fields"));
    });
    html.push(card("derived", "derived", DERIVED.map((k) => ({ key: k, path: "values." + k, kind: "calc" })), "computed by Fleet Manager Pro"));
    (d.trailers || []).forEach((t) => html.push(card(`trailer slot ${t.slot}`, "trailers",
      TRAILER_FIELDS.map(([k, kind, off, cnt]) => ({ key: k, path: `trailer.${t.slot}.${k}`, kind: kind + (cnt > 1 && kind !== "str" ? `[${cnt}]` : ""), offset: 6000 + t.slot * 1560 + off })), t.attached ? "attached" : "")));
    html.push(card("substances", "substances", (d.substances || []).map((s, i) => ({ key: "substance[" + i + "]", path: "sub." + i, kind: "str" })), (d.substances || []).length + " defined"));
    $("#groups").innerHTML = html.join("");
    built = (d.trailers || []).length + ":" + (d.substances || []).length;
    filter();
  }
  function lookup(d, path) {
    const [a, b, c] = path.split(".");
    if (a === "values") return d.values[b];
    if (a === "trailer") { const t = (d.trailers || []).find((x) => String(x.slot) === b); return t ? t[c] : undefined; }
    if (a === "sub") return (d.substances || [])[+b];
  }
  function update(d) {
    const sig = (d.trailers || []).length + ":" + (d.substances || []).length;
    if (built !== sig) build(d);
    $$("tr[data-path]").forEach((tr) => {
      const s = fmt(lookup(d, tr.dataset.path));
      if (prev[tr.dataset.path] !== s) {
        tr.querySelector("[data-v]").textContent = s;
        if (prev[tr.dataset.path] !== undefined) { tr.classList.add("changed"); setTimeout(() => tr.classList.remove("changed"), 60); }
        prev[tr.dataset.path] = s;
      }
    });
  }
  function filter() {
    const q = $("#q").value.trim().toLowerCase();
    $$("[data-group]").forEach((c) => {
      const okG = group === "all" || c.dataset.group === group;
      let any = false;
      $$("tr[data-k]", c).forEach((tr) => { const ok = !q || tr.dataset.k.toLowerCase().includes(q); tr.style.display = ok ? "" : "none"; any = any || ok; });
      c.style.display = okG && any ? "" : "none";
    });
  }
  $("#q").addEventListener("input", filter);
  $("#chips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip"); if (!c) return;
    $$(".chip").forEach((x) => x.classList.toggle("on", x === c)); group = c.dataset.g; filter();
  });
  $("#pause").addEventListener("click", (e) => { paused = !paused; e.target.textContent = paused ? "Resume" : "Pause"; });
  $("#dl").addEventListener("click", () => {
    if (!last) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(last, null, 2)], { type: "application/json" }));
    a.download = "telemetry-snapshot.json"; a.click();
  });
  function tick() {
    if (paused) return;
    FMP.get("/api/live?user=" + $("#who").value).then((d) => {
      $("#state").textContent = d.online ? `Live · ${d.source} · ${d.values.game.toUpperCase()} · plugin rev ${d.values.plugin_revision} · ${d.status}` : d.status;
      if (!d.online) return;
      last = d;
      update(d);
    }).catch(() => {});
  }
  tick(); setInterval(tick, 500);
  const rawGo = $("#raw-go");
  if (rawGo) rawGo.addEventListener("click", () => {
    fetch("/api/telemetry/raw?offset=" + $("#raw-off").value).then((r) => r.json()).then((d) => {
      $("#raw").textContent = d.error ? d.error : d.rows.map((r) => String(r.offset).padStart(5, " ") + "  " + r.hex.padEnd(48, " ") + "  " + r.ascii).join("\n");
    });
  });
})();
