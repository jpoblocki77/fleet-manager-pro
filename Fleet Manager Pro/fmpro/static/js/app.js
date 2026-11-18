/* Fleet Manager Pro — shared front-end helpers (no external libraries) */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const FMP = (window.FMP = { $, $$, esc });

  // ---------- theme ----------
  const root = document.documentElement;
  try { const t = localStorage.getItem("fmp-theme"); if (t) root.dataset.theme = t; } catch (e) {}
  FMP.toggleTheme = () => {
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    try { localStorage.setItem("fmp-theme", root.dataset.theme); } catch (e) {}
  };

  // ---------- units / formatting ----------
  FMP.units = document.body?.dataset.units || "imperial";
  FMP.cur = document.body?.dataset.currency || "$";
  FMP.imp = () => FMP.units === "imperial";
  FMP.speed = (kmh) => (FMP.imp() ? kmh * 0.621371 : kmh);
  FMP.speedU = () => (FMP.imp() ? "mph" : "km/h");
  FMP.dist = (km, d = 0) => (FMP.imp() ? (km * 0.621371).toFixed(d) + " mi" : Number(km).toFixed(d) + " km");
  FMP.mass = (kg) => (FMP.imp() ? Math.round(kg * 2.20462).toLocaleString() + " lb" : (kg / 1000).toFixed(1) + " t");
  FMP.liq = (l, d = 0) => (FMP.imp() ? (l * 0.264172).toFixed(d) + " gal" : Number(l).toFixed(d) + " L");
  FMP.money = (v) => (v < 0 ? "-" : "") + FMP.cur + Math.abs(Math.round(v)).toLocaleString();
  FMP.pres = (psi) => (FMP.imp() ? Math.round(psi) + " psi" : (psi * 0.0689476).toFixed(1) + " bar");
  FMP.temp = (c) => (FMP.imp() ? Math.round(c * 9 / 5 + 32) + "°F" : Math.round(c) + "°C");
  FMP.mins = (m) => { m = Math.round(m); if (m < 0) return "late " + FMP.mins(-m); const h = Math.floor(m / 60); return (h ? h + "h " : "") + (m % 60) + "m"; };

  // ---------- fetch with CSRF ----------
  FMP.csrf = document.querySelector('meta[name="csrf"]')?.content || "";
  FMP.get = (url) => fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));

  // ---------- sidebar, dialogs, dropdowns ----------
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-open]");
    if (t) { e.preventDefault(); const d = document.getElementById(t.dataset.open); if (d) { d.showModal(); } }
    const c = e.target.closest("[data-close]");
    if (c) { e.preventDefault(); c.closest("dialog")?.close(); }
    const m = e.target.closest(".menu-btn");
    if (m) $(".side")?.classList.toggle("open");
    if (e.target.closest(".side-scrim")) $(".side")?.classList.remove("open");
    const bell = e.target.closest("#bell");
    const dd = $("#notif-dd");
    if (bell && dd) { dd.classList.toggle("open"); }
    else if (dd && !e.target.closest("#notif-dd")) dd.classList.remove("open");
    const row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a,button,form,input")) location.href = row.dataset.href;
    const conf = e.target.closest("[data-confirm]");
    if (conf && !confirm(conf.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  // ---------- game picker (sets the units everywhere) ----------
  document.addEventListener("change", (e) => {
    if (e.target.id !== "game-pick") return;
    const fd = new FormData();
    fd.append("csrf", FMP.csrf);
    fd.append("game", e.target.value);
    fetch("/account/game", { method: "POST", body: fd, credentials: "same-origin", headers: { "X-CSRF": FMP.csrf } })
      .then((r) => (r.ok ? location.reload() : alert("Couldn't save your game choice - reload the page and try again.")));
  });

  // ---------- notifications + telemetry chip ----------
  function pollNotifs() {
    if (!$("#bell")) return;
    FMP.get("/api/notifications").then((d) => {
      const p = $("#bell .pill");
      if (p) { p.textContent = d.unread; p.style.display = d.unread ? "" : "none"; }
      const mp = $("#nav-msg-pill");
      if (mp) { mp.textContent = d.messages; mp.style.display = d.messages ? "" : "none"; }
      const list = $("#notif-list");
      if (list) list.innerHTML = d.items.length ? d.items.map((n) =>
        `<div class="item notif ${n.read ? "" : "unread"}"><a href="${esc(n.link || "#")}">${esc(n.text)}<div class="small faint">${new Date(n.created_at * 1000).toLocaleString()}</div></a>` +
        `<button type="button" class="notif-del" data-notif-del="${Number(n.id)}" title="Delete this notification" aria-label="Delete this notification">&times;</button></div>`).join("")
        : '<div class="item muted">No notifications.</div>';
      const clr = $("#notif-clear");
      if (clr) clr.style.display = d.items.length ? "" : "none";
    }).catch(() => {});
  }
  FMP.pollNotifs = pollNotifs;
  // delete one notification without leaving the page (the dropdown stays open)
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-notif-del]");
    if (!b) return;
    e.preventDefault();
    b.disabled = true;
    fetch(`/notifications/${Number(b.dataset.notifDel)}/delete`, { method: "POST", credentials: "same-origin", headers: { "X-CSRF": FMP.csrf } })
      .then((r) => { if (!r.ok) throw r.status; b.closest(".item")?.remove(); pollNotifs(); })
      .catch(() => { b.disabled = false; alert("Couldn't delete that notification - reload the page and try again."); });
  });
  function pollChip() {
    const chip = $("#tele-chip");
    if (!chip) return;
    FMP.get("/api/live").then((d) => {
      const dot = $(".dot", chip), txt = $(".txt", chip);
      const demo = /DEMO/.test(d.status);
      dot.className = "dot" + (d.online ? (demo ? " demo" : " on") : "");
      if (d.online) {
        const v = d.values;
        txt.textContent = `${Math.round(FMP.speed(v.speed_kmh))} ${FMP.speedU()} · ${v.truck_brand} ${v.truck_name}` +
          (v.on_job ? ` · ${v.cargo} → ${v.destination_city}` : " · free roam") + (demo ? " · DEMO" : "");
      } else txt.textContent = d.status;
    }).catch(() => {});
  }
  document.addEventListener("DOMContentLoaded", () => {
    pollNotifs(); pollChip();
    setInterval(pollNotifs, 20000); setInterval(pollChip, 2000);
  });

  // ---------- SVG charts ----------
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); parent && parent.appendChild(n); return n; };
  const nice = (v) => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };
  const short = (v) => Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + "k" : String(Math.round(v));

  /** FMP.chart(container, {labels, series:[{name, color, values, type:'bar'|'line'}], fmt}) */
  FMP.chart = function (box, opt) {
    box.innerHTML = "";
    const W = box.clientWidth || 600, H = box.clientHeight || 220, L = 48, R = 10, T = 10, B = 26;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", preserveAspectRatio: "none" }, box);
    const labels = opt.labels || [];
    const all = opt.series.flatMap((s) => s.values);
    if (!labels.length) { const t = el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, svg); t.textContent = "No data yet"; return; }
    const max = nice(Math.max(1, ...all) * 1.05), n = labels.length;
    const x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
    const y = (v) => T + (H - T - B) * (1 - v / max);
    for (let k = 0; k <= 4; k++) {
      const v = (max * k) / 4, yy = y(v);
      el("line", { x1: L, x2: W - R, y1: yy, y2: yy, class: "grid-line" }, svg);
      const t = el("text", { x: L - 6, y: yy + 4, "text-anchor": "end" }, svg); t.textContent = (opt.prefix || "") + short(v);
    }
    const step = Math.ceil(n / Math.max(1, Math.floor((W - L) / 70)));
    labels.forEach((lb, i) => { if (i % step) return; const t = el("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, svg); t.textContent = lb; });
    const bars = opt.series.filter((s) => s.type !== "line");
    const bw = Math.max(3, Math.min(28, ((W - L - R) / n) * 0.7 / Math.max(1, bars.length)));
    bars.forEach((s, si) => s.values.forEach((v, i) => {
      const xx = x(i) - (bw * bars.length) / 2 + si * bw;
      const r = el("rect", { x: xx, y: y(v), width: bw - 1, height: Math.max(0, H - B - y(v)), rx: 2, fill: s.color }, svg);
      el("title", {}, r).textContent = `${labels[i]} · ${s.name}: ${(opt.prefix || "") + Math.round(v).toLocaleString()}`;
    }));
    opt.series.filter((s) => s.type === "line").forEach((s) => {
      const d = s.values.map((v, i) => (i ? "L" : "M") + x(i) + " " + y(v)).join(" ");
      el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2.5, "stroke-linejoin": "round" }, svg);
      s.values.forEach((v, i) => { const c = el("circle", { cx: x(i), cy: y(v), r: 3, fill: s.color }, svg); el("title", {}, c).textContent = `${labels[i]} · ${s.name}: ${Math.round(v).toLocaleString()}`; });
    });
  };

  /** Donut: FMP.donut(container, [{label, value, color}]) */
  FMP.donut = function (box, items) {
    box.innerHTML = "";
    const total = items.reduce((a, b) => a + b.value, 0);
    const svg = el("svg", { viewBox: "0 0 120 120", width: 150, height: 150 }, box);
    if (!total) { const t = el("text", { x: 60, y: 64, "text-anchor": "middle" }, svg); t.textContent = "—"; return; }
    let a0 = -Math.PI / 2;
    items.forEach((it) => {
      const a1 = a0 + (it.value / total) * Math.PI * 2;
      const large = a1 - a0 > Math.PI ? 1 : 0, r = 52, ri = 34;
      const p = (a, rr) => `${60 + rr * Math.cos(a)} ${60 + rr * Math.sin(a)}`;
      const d = it.value === total ? `M ${p(0, r)} A ${r} ${r} 0 1 1 ${p(Math.PI * 1.999, r)} L ${p(Math.PI * 1.999, ri)} A ${ri} ${ri} 0 1 0 ${p(0, ri)} Z`
        : `M ${p(a0, r)} A ${r} ${r} 0 ${large} 1 ${p(a1, r)} L ${p(a1, ri)} A ${ri} ${ri} 0 ${large} 0 ${p(a0, ri)} Z`;
      const path = el("path", { d, fill: it.color }, svg);
      el("title", {}, path).textContent = `${it.label}: ${Math.round(it.value).toLocaleString()}`;
      a0 = a1;
    });
  };

  // ---------- world map canvas (pan/zoom, trails, markers) ----------
  FMP.WorldMap = function (canvas) {
    const ctx = canvas.getContext("2d");
    const st = { cx: 0, cz: 0, scale: 0.004, trails: [], markers: [], follow: null, userMoved: false, bg: null };
    let drag = null;
    const resize = () => { const r = canvas.getBoundingClientRect(); canvas.width = r.width * devicePixelRatio; canvas.height = r.height * devicePixelRatio; draw(); };
    const toScreen = (x, z) => [(x - st.cx) * st.scale * devicePixelRatio + canvas.width / 2, (z - st.cz) * st.scale * devicePixelRatio + canvas.height / 2];
    function grid() {
      const step = Math.pow(10, Math.ceil(Math.log10(80 / st.scale)));
      ctx.strokeStyle = "rgba(143,168,204,.08)"; ctx.lineWidth = 1;
      const w = canvas.width / devicePixelRatio / st.scale, h = canvas.height / devicePixelRatio / st.scale;
      for (let gx = Math.floor((st.cx - w) / step) * step; gx < st.cx + w; gx += step) { const [sx] = toScreen(gx, 0); ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, canvas.height); ctx.stroke(); }
      for (let gz = Math.floor((st.cz - h) / step) * step; gz < st.cz + h; gz += step) { const [, sy] = toScreen(0, gz); ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(canvas.width, sy); ctx.stroke(); }
    }
    // real-geography background: borders, region names, cities; plus roads and places learned from driving
    function background() {
      const b = st.bg;
      if (!b) return;
      const dpr = devicePixelRatio, W = canvas.width, H = canvas.height;
      const path = (ln) => { for (let i = 0; i < ln.length; i += 2) { const [sx, sy] = toScreen(ln[i], ln[i + 1]); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); } };
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.strokeStyle = "rgba(143,168,204,.42)"; ctx.lineWidth = dpr;
      ctx.beginPath(); (b.lines || []).forEach(path); ctx.stroke();
      ctx.textAlign = "center"; ctx.fillStyle = "rgba(143,168,204,.28)";
      ctx.font = `700 ${Math.max(9, Math.min(26, st.scale * 3600)) * dpr}px Segoe UI, sans-serif`;
      if (st.scale > 0.0016) (b.regions || []).forEach((r) => {
        const [sx, sy] = toScreen(r.p[0], r.p[1]);
        if (sx > 0 && sy > 0 && sx < W && sy < H) ctx.fillText(r.n.toUpperCase(), sx, sy);
      });
      ctx.strokeStyle = "#e0a23a"; ctx.lineWidth = Math.max(1.5, Math.min(6, st.scale * 500)) * dpr; ctx.globalAlpha = 0.9;
      ctx.beginPath(); (b.roads || []).forEach(path); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.textAlign = "left";
      const learned = new Set((b.places || []).map((p) => p.n.toLowerCase()));
      const fs = 11 * dpr;
      (b.cities || []).forEach((c) => {
        if (learned.has(c.n.toLowerCase())) return;
        if (!(c.s === 1 || (c.s === 2 && st.scale > 0.006) || (c.s === 3 && st.scale > 0.012))) return;
        const [sx, sy] = toScreen(c.p[0], c.p[1]);
        if (sx < -20 || sy < -20 || sx > W + 20 || sy > H + 20) return;
        ctx.strokeStyle = "rgba(200,214,236,.75)"; ctx.lineWidth = 1.5 * dpr;
        ctx.beginPath(); ctx.arc(sx, sy, 3 * dpr, 0, 7); ctx.stroke();
        ctx.font = `${c.s === 1 ? 600 : 400} ${fs}px Segoe UI, sans-serif`; ctx.fillStyle = "rgba(200,214,236,.8)";
        ctx.fillText(c.n, sx + 7 * dpr, sy + 4 * dpr);
      });
      (b.places || []).forEach((p) => {
        if (st.scale < 0.0022 && p.visits < 3) return;
        const [sx, sy] = toScreen(p.x, p.z);
        if (sx < -20 || sy < -20 || sx > W + 20 || sy > H + 20) return;
        ctx.fillStyle = "#e23552"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * dpr;
        ctx.beginPath(); ctx.arc(sx, sy, 4.5 * dpr, 0, 7); ctx.fill(); ctx.stroke();
        ctx.font = `700 ${fs}px Segoe UI, sans-serif`; ctx.fillStyle = "#fff";
        ctx.fillText(p.n, sx + 8 * dpr, sy + 4 * dpr);
      });
    }
    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      grid();
      background();
      st.trails.forEach((t) => {
        if (t.points.length < 2) return;
        ctx.strokeStyle = t.color || "#e23552"; ctx.lineWidth = 3 * devicePixelRatio; ctx.lineJoin = "round"; ctx.globalAlpha = 0.85;
        ctx.beginPath(); t.points.forEach((p, i) => { const [sx, sy] = toScreen(p.x, p.z); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); }); ctx.stroke();
        ctx.globalAlpha = 1;
        const [s0x, s0y] = toScreen(t.points[0].x, t.points[0].z);
        ctx.fillStyle = "#2fb67c"; ctx.beginPath(); ctx.arc(s0x, s0y, 5 * devicePixelRatio, 0, 7); ctx.fill();
      });
      st.markers.forEach((m) => {
        const [sx, sy] = toScreen(m.x, m.z);
        const ang = -(m.heading || 0) * Math.PI * 2;
        ctx.save(); ctx.translate(sx, sy); ctx.rotate(ang);
        ctx.fillStyle = m.color || "#c8102e"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 2 * devicePixelRatio;
        const s = 9 * devicePixelRatio;
        ctx.beginPath(); ctx.moveTo(0, -s * 1.4); ctx.lineTo(s, s); ctx.lineTo(0, s * 0.45); ctx.lineTo(-s, s); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.restore();
        ctx.font = `${600} ${12 * devicePixelRatio}px Segoe UI, sans-serif`; ctx.fillStyle = "#fff";
        ctx.fillText(m.label || "", sx + 14 * devicePixelRatio, sy + 4 * devicePixelRatio);
      });
    }
    // what to show when nobody is driving: the roads driven so far, or else the whole region
    function bgPoints() {
      const b = st.bg || {}, out = [];
      (b.roads || []).forEach((ln) => { for (let i = 0; i < ln.length; i += 2) out.push({ x: ln[i], z: ln[i + 1] }); });
      (b.places || []).forEach((p) => out.push({ x: p.x, z: p.z }));
      if (out.length > 1) return out;
      return (b.cities || []).map((c) => ({ x: c.p[0], z: c.p[1] }));
    }
    function fit(points) {
      if (!points.length) return;
      const xs = points.map((p) => p.x), zs = points.map((p) => p.z);
      const minx = Math.min(...xs), maxx = Math.max(...xs), minz = Math.min(...zs), maxz = Math.max(...zs);
      st.cx = (minx + maxx) / 2; st.cz = (minz + maxz) / 2;
      const r = canvas.getBoundingClientRect();
      st.scale = Math.min(0.05, 0.8 * Math.min(r.width / Math.max(500, maxx - minx), r.height / Math.max(500, maxz - minz)));
      draw();
    }
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); st.userMoved = true; st.scale *= e.deltaY < 0 ? 1.2 : 1 / 1.2; st.scale = Math.max(1e-5, Math.min(2, st.scale)); draw(); }, { passive: false });
    canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, cx: st.cx, cz: st.cz }; st.follow = null; st.userMoved = true; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => { if (!drag) return; st.cx = drag.cx - (e.clientX - drag.x) / st.scale; st.cz = drag.cz - (e.clientY - drag.y) / st.scale; draw(); });
    canvas.addEventListener("pointerup", () => (drag = null));
    window.addEventListener("resize", resize);
    resize();
    return {
      st, draw, fit,
      set(trails, markers) {
        st.trails = trails; st.markers = markers;
        if (!st.userMoved && !st.follow) {
          let pts = trails.flatMap((t) => t.points).concat(markers);
          if (!pts.length && st.bg) pts = bgPoints();
          if (pts.length) fit(pts);
        }
        if (st.follow) { const m = markers.find((k) => k.id === st.follow); if (m) { st.cx = m.x; st.cz = m.z; } }
        draw();
      },
      focus(id) { st.follow = id; const m = st.markers.find((k) => k.id === id); if (m) { st.cx = m.x; st.cz = m.z; st.scale = Math.max(st.scale, 0.02); draw(); } },
      background(b) { st.bg = b; if (!st.userMoved && !st.follow && !st.markers.length) { const p = bgPoints(); if (p.length) fit(p); } draw(); },
      overview() { st.userMoved = true; st.follow = null; const p = (st.bg && st.bg.cities || []).map((c) => ({ x: c.p[0], z: c.p[1] })); if (p.length) fit(p); },
      refit() { st.userMoved = false; st.follow = null; if (!st.markers.length && !st.trails.length && st.bg) return fit(bgPoints()); fit(st.trails.flatMap((t) => t.points).concat(st.markers)); },
    };
  };
})();
