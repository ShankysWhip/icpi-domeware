// domeware HUD. One clock (S.t, ms since epoch, UTC) drives the Rerun web viewer (3D) and every panel here.
// The data comes from views/hud/data.json (dw replay); the viewer files from domeware-vendor (dw setup).

const qs = new URLSearchParams(location.search);
const SHOT = qs.get("shot") === "1";  // dw shots: a picture is being taken (no motion, nothing saved or reported)
const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

// ---------- formatting ----------
const pad = (n, w = 2) => String(Math.floor(Math.abs(n))).padStart(w, "0");
const fmts = {};
function parts(ms, tz, extra = {}) {
  const key = tz + JSON.stringify(extra);
  const f = (fmts[key] ||= new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month: "short", day: "2-digit",
    weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", ...extra }));
  const o = {};
  for (const p of f.formatToParts(new Date(ms))) o[p.type] = p.value;
  return o;
}
const zone = () => (S.tz === "UTC" ? "UTC" : S.data.tz_local);
const hms = (ms) => { const p = parts(ms, zone()); return `${p.hour}:${p.minute}:${p.second}`; };
const hm = (ms) => { const p = parts(ms, zone()); return `${p.hour}:${p.minute}`; };
function dur(ms, signed = true) {
  const sign = ms < 0 ? "−" : "+";
  const s = Math.floor(Math.abs(ms) / 1000);
  return `${signed ? sign : ""}${pad(s / 3600)}:${pad((s % 3600) / 60)}:${pad(s % 60)}`;
}
const fx = (v, d = 1) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(d));
const dist = (m) => (m === null || m === undefined || !Number.isFinite(m) ? "—" : m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(2)} km`);
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// Drone colours are picked for the dark 3D scene; on the white panels a light one (yellow, white) needs more ink.
function ink(hex) {
  const n = parseInt(String(hex).slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  if (lum <= 0.52) return hex;
  const k = 0.52 / lum;
  return "#" + [r, g, b].map((c) => Math.round(c * k).toString(16).padStart(2, "0")).join("");
}
const KIND_COLOR = { radar: "#d9620b", operator: "#b7700b", drone_home: "#475569", pilot: "#a3540a", weather: "#0a8db6",
  rf: "#6252cc", camera: "#0f766e", sensor: "#4f46e5", effector: "#cf3a57", other: "#64748b" };
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function bisect(ts, t) {
  if (!ts || !ts.length || t < ts[0]) return -1;
  let lo = 0, hi = ts.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= t) lo = m; else hi = m - 1; }
  return lo;
}
function at(ts, vs, t, maxAge = 120000) {
  if (!vs) return null;
  const i = bisect(ts, t);
  if (i < 0 || t - ts[i] > maxAge) return null;
  return vs[i];
}
function droneAt(d, key, t) {  // drone series are one value per second from series.t[0]
  const v = d.series[key];
  if (!v) return null;
  const i = Math.round((t - d.series.t[0]) / 1000);
  return i >= 0 && i < v.length ? v[i] : null;
}
const inFlight = (d, t) => d.flying.some(([a, b]) => t >= a - 500 && t <= b + 500);  // as dw compare counts flight
const STEPS = [1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5];
const niceStep = (raw) => STEPS.find((s) => s >= raw) || STEPS[STEPS.length - 1];
const RINGS = [20, 40, 60, 80, 100, 120, 160, 200, 300, 400, 600, 800, 1000, 1200, 1600, 2000, 2400, 3000, 4000, 6000, 8000, 12000];
const niceCeil = (v) => RINGS.find((r) => r >= v) || RINGS[RINGS.length - 1];

// ---------- state ----------
const S = {
  data: null, t: 0, playing: false, speed: 1, loop: true, tz: "UTC", open: new Set(["over", "atmo"]), away: false, glass: true,
  wx: { wind: true, gust: true, dir: true, temp: true, rh: false, pressure: false },
  filter: "all", sel: null, scope: "site", drone: null, full: [0, 1], act: [0, 1], view: [0, 1],
};
function padRange([a, b], f) {
  const w = (b - a) * f;
  return [Math.max(S.full[0], a - w), Math.min(S.full[1], b + w)];
}

// ---------- viewer bridge ----------
const V = { viewer: null, ready: false, rec: null, lastPush: null, lastPushAt: 0 };
window.__hud = { S, V };  // for checks from the browser console
function setLoad(text, error = false) {
  $("#loadSub").textContent = text;
  $("#loading").classList.toggle("error", error);
}
async function startViewer() {
  setLoad("loading the 3D engine (first time: a few seconds)");
  const { WebViewer } = await import("/viewer/index.js");
  const viewer = new WebViewer();
  V.viewer = viewer;
  V.rec = S.data.recording_id;
  viewer.on("recording_open", (ev) => { if (ev && ev.recording_id && ev.recording_id === S.data.recording_id) V.rec = ev.recording_id; setLoad("receiving the replay"); });
  viewer.on("time_update", (ev) => onViewerTime(typeof ev === "number" ? ev : ev && ev.time));
  viewer.on("selection_change", (ev) => onSelection((ev && ev.items) || []));
  viewer.on("play", () => { try { viewer.set_playing(V.rec, false); } catch { /* not ready */ } setPlaying(true); });
  // full URLs: the viewer reads "/data/x" as the host "data"
  const urls = ["/data/replay.rrd", "/data/replay_hud.rbl"].map((u) => new URL(u, location.href).href);
  await viewer.start(urls, $("#viewer"), {
    hide_welcome_screen: true, check_for_updates_on_startup: false, theme: "light", width: "100%", height: "100%" });
  for (const p of ["top", "blueprint", "selection", "time"]) { try { viewer.override_panel_state(p, "hidden"); } catch { /* older viewer */ } }
  waitForTimeline(0);
}
function waitForTimeline(tries) {
  let r = null;
  try { r = V.viewer.get_time_range(V.rec, S.data.timeline); } catch { /* still loading */ }
  if (r && r.max > r.min) {
    V.ready = true;
    try { V.viewer.set_active_timeline(V.rec, S.data.timeline); } catch { /* fine */ }
    try { V.viewer.set_playing(V.rec, false); } catch { /* fine */ }  // our clock leads; the viewer follows
    if (!S.data.window) { S.full = [r.min / 1e6, r.max / 1e6]; if (!S.data.activity) S.act = S.full.slice(); S.view = padRange(S.act, 0.04); TL.render(); }
    push(true);
    $("#loading").classList.add("gone");
    if (qs.get("play") === "1") setPlaying(true);
    return;
  }
  if (tries > 480) { setLoad("the replay did not load — rebuild it with  dw replay", true); return; }
  setTimeout(() => waitForTimeline(tries + 1), 250);
}
function push(force = false) {
  if (!V.ready) return;
  const ms = Math.round(S.t);
  if (!force && ms === V.lastPush) return;
  try { V.viewer.set_current_time(V.rec, S.data.timeline, ms * 1e6); } catch { /* viewer busy */ }
  V.lastPush = ms;
  V.lastPushAt = performance.now();
}
function onViewerTime(ns) {
  if (!ns || !V.ready || S.playing) return;
  if (performance.now() - V.lastPushAt < 400) return;  // our own time coming back
  const ms = ns / 1e6;
  if (Math.abs(ms - S.t) > 50) { S.t = ms; refresh(true); }
}
function onSelection(items) {
  for (const it of items) {
    if (it.type !== "entity" || !it.entity_path) continue;
    const p = "/" + String(it.entity_path).replace(/^\/+/, "");
    const eq = S.data.equipment.find((e) => p === e.entity || p.startsWith(e.entity + "/"));
    if (eq) { Panels.set("equip", true); Equip.select(eq.id, true); return; }
    const d = S.data.drones.find((x) => p === x.entity || p.startsWith(x.entity + "/"));
    if (d) { S.drone = d.name; Panels.set("drones", true); Drones.update(); Equip.renderScope(); return; }
  }
}

// ---------- clock & transport ----------
function seek(ms) {
  S.t = clamp(ms, S.full[0], S.full[1]);
  push(true);
  refresh(true);
}
function setPlaying(on) {
  if (on && S.t >= S.act[1] - 200) S.t = S.act[0];
  S.playing = on;
  FX.ema = 16.7;
  FX.n = 0;
  TL.playIcon();
  Clock.led.classList.toggle("on", on);
  if (!on) push(true);
}
let lastFrame = performance.now(), lastUi = 0;
const FX = { ema: 16.7, n: 0, manual: false, fps: null };  // frame time while playing
function frame(now) {
  const dt = Math.min(250, now - lastFrame);
  lastFrame = now;
  if (S.playing) {
    S.t += dt * S.speed;
    if (S.t >= S.act[1]) { if (S.loop) S.t = S.act[0]; else { S.t = S.act[1]; setPlaying(false); } }
    push();
    if (dt < 100 && !document.hidden) {  // playback smoothness (reported with the ping); glass costs GPU time: slow playback goes solid
      FX.ema += (dt - FX.ema) * 0.05;
      if (++FX.n > 240) {
        FX.fps = 1000 / FX.ema;
        if (S.glass && !FX.manual && !SHOT && FX.ema > 30) setGlass(false);
      }
    }
  }
  TL.playhead();
  if (now - lastUi > 80) { lastUi = now; refresh(false); }
  requestAnimationFrame(frame);
}
function refresh(full) {  // only the open instruments are kept up to date
  Clock.update();
  if (Panels.is("over")) Over.update();
  if (Panels.is("drones")) Drones.update();
  if (Panels.is("radar")) Radar.update();
  if (Panels.is("log")) Log.update();
  if (Panels.is("atmo")) Atmo.update();
  if (Panels.is("equip")) Equip.updateLive();
  if (full) TL.playhead();
}

// ---------- small widgets ----------
function seg(options, value, onChange, cls = "") {
  const el = h("div", { class: `seg ${cls}`, role: "radiogroup" });
  const ind = h("i", { class: "ind", "aria-hidden": "true" });
  el.append(ind);
  for (const [val, label, extra, disabled] of options) {
    const b = h("button", { class: val === value ? "on" : "", "data-v": val, role: "radio", "aria-checked": String(val === value),
      disabled: disabled ? true : null }, label, extra ? h("em", {}, extra) : null);
    onPress(b, () => { if (b.disabled || el.value === val) return; el.set(val); onChange(val); });
    el.append(b);
  }
  el.value = value;
  el.place = () => {  // the pill follows the chosen option (also after a resize or when a hidden panel shows)
    const on = el.querySelector("button.on");
    if (!on || !on.offsetWidth) return;
    ind.style.width = `${on.offsetWidth}px`;
    ind.style.transform = `translateX(${on.offsetLeft - 3}px)`;
    ind.style.opacity = "1";
  };
  el.set = (v) => {
    el.value = v;
    for (const x of el.querySelectorAll("button")) { const on = x.dataset.v === v; x.classList.toggle("on", on); x.setAttribute("aria-checked", String(on)); }
    requestAnimationFrame(el.place);
  };
  requestAnimationFrame(el.place);
  return el;
}
function placeAll() { for (const s of document.querySelectorAll(".seg")) if (s.place) s.place(); }
const Tip = {
  el: null,
  show(html, x, y) {
    const el = (this.el ||= $("#tip"));
    el.innerHTML = html;
    el.hidden = false;
    const r = el.getBoundingClientRect();
    el.style.left = `${clamp(x + 14, 6, innerWidth - r.width - 6)}px`;
    el.style.top = `${clamp(y - r.height - 12, 6, innerHeight - r.height - 6)}px`;
  },
  hide() { if (this.el) this.el.hidden = true; },
};

// ---------- status bar ----------
function buildStatus() {
  const st = S.data.status, el = $("#status");
  const chip = (k, v, state, title) => h("span", { class: `stat ${state}`, title }, h("i"), h("b", {}, k), h("span", {}, v));
  el.replaceChildren(
    chip("RADAR", st.radar_tracks ? `${st.radar_tracks} TRACKS` : "PENDING", st.radar_tracks ? "ok" : "wait",
      st.radar_tracks ? "Radar tracks in this replay" : "No radar tracks in this session yet"),
    chip("DRONES", st.drones ? `${st.drones} PATH${st.drones > 1 ? "S" : ""}` : st.drone_logs_encrypted ? "LOG ENCRYPTED" : "NONE",
      st.drones ? "ok" : st.drone_logs_encrypted ? "warn" : "off",
      st.drone_logs_encrypted ? "The DJI log is encrypted: only its header (time, distance) is read until it is decoded" : ""),
    chip("WEATHER", st.weather ? st.weather.toUpperCase() : "NONE", st.weather ? "ok" : "off"),
    chip("SCREENS", String(st.screens), st.screens ? "ok" : "off"),
  );
}

// ---------- mission clock card ----------
const Clock = {
  build() {
    const root = $("#clock");
    this.time = h("div", { class: "time" }, "--:--:--");
    this.frac = h("span", {}, ".000");
    this.zone = h("span", {}, "UTC");
    this.date = h("span", { class: "date" });
    this.tp = h("span", {}, "+00:00:00");
    this.of = h("span", {});
    this.phase = h("span", { class: "phase" });
    this.fill = h("i", { class: "fill" });
    this.led = h("i", { class: "led", title: "Lit while the replay plays" });
    const bar = h("div", { class: "progress", title: "Click to jump within the activity" }, this.fill);
    const [a, b] = S.act;
    for (const r of S.data.runs) bar.append(h("i", { class: "mk", style: `left:${((r.start - a) / (b - a)) * 100}%` }));
    for (const sc of S.data.screens) if (sc.t >= a && sc.t <= b) bar.append(h("i", { class: "mk shot", style: `left:${((sc.t - a) / (b - a)) * 100}%` }));
    bar.addEventListener("click", (e) => { const r = bar.getBoundingClientRect(); seek(a + ((e.clientX - r.left) / r.width) * (b - a)); });
    this.tzSeg = seg([["UTC", "UTC"], ["LOCAL", "Local"]], S.tz, setTz);
    root.append(
      h("div", { class: "card-head" }, h("span", { class: "label" }, this.led, "Mission clock"), h("i", { class: "rule" }), this.tzSeg),
      h("div", { class: "body" },
        h("div", { class: "clock-row" },
          h("div", { class: "clock-main" }, this.time, h("div", { class: "time-sub" }, this.frac, this.zone)),
          h("div", { class: "clock-t" }, h("b", {}, h("span", { class: "k" }, "T"), this.tp), this.of)),
        h("div", { class: "clock-meta" }, this.date, this.phase),
        bar));
    // the same clock, small, on the map while the panels are hidden
    this.miniT = h("b", {}, "--:--:--");
    this.miniZ = h("span", {}, "UTC");
    this.miniP = h("small", {});
    $("#mini").append(this.miniT, this.miniZ, h("i"), this.miniP);
  },
  update() {
    const d = S.data, t = S.t, [a, b] = S.act;
    const p = parts(t, zone(), { timeZoneName: "shortOffset" });
    const time = `${p.hour}:${p.minute}:${p.second}`, zn = S.tz === "UTC" ? "UTC" : (p.timeZoneName || "LOCAL").toUpperCase();
    this.time.textContent = time;
    this.frac.textContent = "." + pad(((t % 1000) + 1000) % 1000, 3);
    this.zone.textContent = zn;
    this.date.textContent = `${p.weekday} ${p.day} ${p.month} ${p.year}`.toUpperCase();
    this.tp.textContent = dur(t - a);
    this.of.textContent = `/ ${dur(b - a, false)}`;
    this.fill.style.width = `${clamp((t - a) / (b - a), 0, 1) * 100}%`;
    const run = d.runs.find((r) => t >= r.start && t <= r.end);
    const hd = d.flights_header.find((f) => t >= f.start && t <= f.end);
    this.phase.textContent = run ? `${run.id} · ${run.part}` : hd ? `${hd.name} airborne · log` : t < a ? "before activity" : t > b ? "after activity" : "";
    if (S.away) { this.miniT.textContent = time; this.miniZ.textContent = zn; this.miniP.textContent = `T${dur(t - a)}`; }
  },
};

// ---------- instruments ----------
const SPARK = { wind: ["#0a8db6", 2], gust: ["#d48a0e", 2], temp: ["#b13c8b", 1.5] };  // colour, smallest range drawn
const Over = {
  build(root) {
    const d = S.data;
    this.c = {};
    const tiles = h("div", { class: "tiles" });
    for (const [k, label] of [["wind", "WIND"], ["gust", "GUST"], ["temp", "TEMP"], ["air", "AIRBORNE"], ["radar", "RADAR HOLD"], ["ev", "EVENTS"]]) {
      const v = h("div", { class: "v" }), m = h("div", { class: "m" }), s = h("div", { class: "s" });
      const t = h("div", { class: "tile" }, h("div", { class: "k" }, h("i"), label), v, m, s);
      this.c[k] = { t, v, m, s };
      tiles.append(t);
    }
    root.append(tiles);
    // the last 15 minutes of weather, up to the current minute
    for (const [k, [color]] of Object.entries(SPARK)) {
      const c = this.c[k];
      c.m.style.color = color;
      c.area = sv("path", { class: "a" });
      c.line = sv("path", { class: "l" });
      c.dot = h("i", { class: "dot", hidden: true });
      c.m.append(sv("svg", { viewBox: "0 0 100 16", preserveAspectRatio: "none" }, c.area, c.line), c.dot);
    }
    // one square per drone: in the air, and held by the radar
    const flights = d.drones.length ? d.drones : d.flights_header, n = Math.min(24, flights.length);
    for (const k of ["air", "radar"]) {
      const c = this.c[k];
      c.sq = [];
      c.state = [];
      if (k === "radar" && !d.radar) { c.m.append(h("div", { class: "pending", title: "Radar data pending" })); continue; }
      const box = h("div", { class: "sq" });
      for (let i = 0; i < n; i++) { const q = h("i", { title: flights[i].name }); box.append(q); c.sq.push(q); }
      c.m.append(box);
    }
    // where the events fall in the activity
    const [a, b] = S.act, strip = h("div", { class: "evstrip" });
    this.marks = d.events.filter((e) => e.t >= a && e.t <= b).map((e) => {
      const m = h("b", { style: `left:${((e.t - a) / (b - a)) * 100}%` });
      strip.append(m);
      return [e.t, m];
    });
    this.cur = h("i", { class: "cur" });
    strip.append(this.cur);
    this.c.ev.m.append(strip);
    this.evN = -1;
  },
  spark(k, t) {
    const wx = S.data.weather, c = this.c[k];
    if (!wx || !wx[k]) return;
    const m0 = Math.floor(t / 60000) * 60000;
    if (m0 !== c.key) {  // redrawn once a minute (or after a jump); the dot moves every frame
      c.key = m0;
      c.t0 = m0 - 14 * 60000;
      c.t1 = m0 + 60000;
      const ts = wx.t, vs = wx[k], pts = [];
      for (let i = Math.max(0, bisect(ts, c.t0 - 60000)); i < ts.length && ts[i] <= c.t1; i++) if (vs[i] !== null) pts.push([ts[i], vs[i]]);
      c.lo = null;
      c.line.setAttribute("d", "");
      c.area.setAttribute("d", "");
      if (pts.length > 1) {
        let lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
        const least = SPARK[k][1];
        if (hi - lo < least) { const mid = (lo + hi) / 2; lo = mid - least / 2; hi = mid + least / 2; }
        c.lo = lo;
        c.hi = hi;
        const X = (tt) => (((tt - c.t0) / (c.t1 - c.t0)) * 100).toFixed(2), Y = (v) => (1.5 + (1 - (v - lo) / (hi - lo)) * 13).toFixed(2);
        const line = pts.map(([tt, v], i) => `${i ? "L" : "M"}${X(tt)},${Y(v)}`).join("");
        c.line.setAttribute("d", line);
        c.area.setAttribute("d", `${line}L${X(pts[pts.length - 1][0])},16L${X(pts[0][0])},16Z`);
      }
    }
    const val = at(wx.t, wx[k], t);
    if (val === null || c.lo === null) { c.dot.hidden = true; return; }
    c.dot.hidden = false;
    c.dot.style.left = `${clamp((t - c.t0) / (c.t1 - c.t0), 0, 1) * 100}%`;
    c.dot.style.top = `${((1.5 + (1 - (val - c.lo) / (c.hi - c.lo)) * 13) / 16) * 100}%`;
  },
  squares(c, states) {  // touch only the squares that changed
    states.forEach((st, i) => {
      if (!c.sq[i] || c.state[i] === st) return;
      c.state[i] = st;
      const [cls, bg] = st.split("|");
      c.sq[i].className = cls;
      c.sq[i].style.background = bg || "";
    });
  },
  update() {
    const d = S.data, t = S.t, c = this.c, wx = d.weather;
    const v = (k) => (wx && wx[k] ? at(wx.t, wx[k], t) : null);
    const wind = v("wind"), gust = v("gust"), dir = v("dir"), temp = v("temp"), rh = v("rh");
    this.tile(c.wind, wind === null ? "—" : `${fx(wind)}<small>m/s</small>`, dir === null ? "no data" : `from ${compass(dir)} ${fx(dir, 0)}°`);
    this.tile(c.gust, gust === null ? "—" : `${fx(gust)}<small>m/s</small>`, S.maxGust !== null ? `max ${fx(S.maxGust)} m/s` : "");
    this.tile(c.temp, temp === null ? "—" : `${fx(temp)}<small>°C</small>`, rh === null ? "" : `humidity ${fx(rh, 0)} %`);
    for (const k of Object.keys(SPARK)) this.spark(k, t);
    if (d.drones.length) {
      const up = d.drones.map((x) => inFlight(x, t));
      const held = d.drones.map((x, i) => up[i] && droneAt(x, "err", t) !== null);
      const nUp = up.filter(Boolean).length, nHeld = held.filter(Boolean).length;
      this.tile(c.air, `${nUp}<small>/ ${d.drones.length}</small>`, "drones in the air", nUp ? "ok" : "");
      this.squares(c.air, up.map((u, i) => (u ? `on|${d.drones[i].ui}` : "")));
      if (d.radar) {
        this.tile(c.radar, nUp ? `${nHeld}<small>/ ${nUp}</small>` : "—", "tracked by the radar", nUp ? (nHeld === nUp ? "ok" : "bad") : "");
        this.squares(c.radar, up.map((u, i) => (!u ? "" : held[i] ? "ok" : "bad")));
      } else this.tile(c.radar, "PENDING", "no radar data yet", "wait");
    } else {
      const fl = d.flights_header.map((f) => t >= f.start && t <= f.end), nUp = fl.filter(Boolean).length, hd = d.flights_header[0];
      this.tile(c.air, hd ? `${nUp}<small>/ ${fl.length}</small>` : "—", hd ? `${hd.name} · path pending` : "no drone logs", nUp ? "ok" : "");
      this.squares(c.air, fl.map((u) => (u ? "on|var(--accent)" : "")));
      if (d.radar) this.tile(c.radar, String(Radar.alive(t)), "tracks alive", "");
      else this.tile(c.radar, "PENDING", "radar data pending", "wait");
    }
    const past = d.events.filter((e) => e.t <= t);
    this.tile(c.ev, `${past.length}<small>/ ${d.events.length}</small>`, past.length ? past[past.length - 1].text : "none yet");
    if (past.length !== this.evN) { this.evN = past.length; for (const [et, m] of this.marks) m.classList.toggle("past", et <= t); }
    const [a, b] = S.act;
    this.cur.style.left = `${clamp((t - a) / (b - a), 0, 1) * 100}%`;
  },
  tile(cell, value, sub, state = "") {
    if (cell.vh !== value) { cell.vh = value; cell.v.innerHTML = value; }
    if (cell.sh !== sub) { cell.sh = sub; cell.s.textContent = sub; cell.s.title = sub; }
    const cls = `tile ${state}`;
    if (cell.t.className !== cls) cell.t.className = cls;
  },
};
const Drones = {
  c: {},
  build(root) {
    const d = S.data;
    if (!d.drones.length) {
      const hd = d.flights_header[0];
      root.append(h("div", { class: "empty" }, hd
        ? `${hd.name}: the flight log is encrypted, so only its header is known — ${dist(hd.distance_m)} flown, max ${fx(hd.max_height_m, 0)} m high, max ${fx(hd.max_speed_ms, 1)} m/s. The path appears here once the log is decoded.`
        : "No drone paths in this session."));
      return;
    }
    const rows = h("div", { class: "rows" });
    rows.append(h("div", { class: "drow h" }, h("i"), h("span", {}, "DRONE"), h("span", {}, "RANGE"), h("span", {}, "ALT"), h("span", {}, "SPEED"), h("span", { style: "justify-self:end" }, "RADAR")));
    for (const dr of d.drones) {
      const cells = { range: h("span"), alt: h("span"), spd: h("span"), badge: h("span", { class: "badge off" }, "—") };
      const row = h("div", { class: "drow", title: `${dr.name}: click to mark it on the equipment scope` }, h("i", { class: "bar", style: `background:${dr.ui}` }),
        h("span", { class: "n" }, dr.name), cells.range, cells.alt, cells.spd, cells.badge);
      row.addEventListener("click", () => { S.drone = S.drone === dr.name ? null : dr.name; Equip.renderScope(); this.update(); });
      this.c[dr.name] = { row, ...cells };
      rows.append(row);
    }
    root.append(rows);
  },
  update() {
    const d = S.data, t = S.t;
    for (const dr of d.drones) {
      const cc = this.c[dr.name], up = inFlight(dr, t), err = droneAt(dr, "err", t);
      cc.row.classList.toggle("sel", S.drone === dr.name);
      cc.range.textContent = up ? dist(droneAt(dr, "range", t)) : "—";
      cc.alt.textContent = up ? `${fx(droneAt(dr, "height", t), 0)} m` : "—";
      cc.spd.textContent = up ? fx(droneAt(dr, "speed", t), 1) : "—";
      const [cls, txt] = !up ? ["off", "GROUND"] : !d.radar ? ["off", "NO DATA"] : err !== null ? ["ok", `TRK ${fx(err, 0)} m`] : ["bad", "LOST"];
      cc.badge.className = `badge ${cls}`;
      cc.badge.textContent = txt;
    }
  },
};
const Radar = {
  build(root) {
    const d = S.data;
    if (!d.radar) {
      root.append(h("div", { class: "empty" }, d.status.drone_logs_encrypted || d.status.screens
        ? "Radar data pending: the tracks arrive from the third party. Everything else in this session is ready."
        : "No radar tracks in this session."));
      return;
    }
    this.big = h("div", { class: "big" }, "0");
    this.tot = h("div", { class: "s" });
    const bars = h("div", { class: "bars" });
    for (const dr of d.drones) {
      const share = dr.tracked_share ?? 0;
      bars.append(h("div", { class: "hbar" },
        h("div", {}, h("div", { class: "t" }, h("b", {}, dr.name), h("span", {}, dr.err_median_m !== null ? `median ${fx(dr.err_median_m, 1)} m off` : "never tracked")),
          h("div", { class: "track" }, h("i", { style: `width:${share * 100}%;background:${dr.ui}` }))),
        h("div", { class: "pct", style: `color:${share > 0.9 ? "var(--green)" : share > 0.5 ? "var(--amber)" : "var(--red)"}` }, `${Math.round(share * 100)}%`)));
    }
    root.append(h("div", { class: "rtop" }, this.big, h("div", {}, h("div", { class: "k" }, "Tracks alive now"), this.tot)), bars,
      d.drones.length ? h("div", { class: "legend-note" }, "Share of airborne seconds with a radar track on the drone.") : null);
  },
  update() {
    if (!this.big) return;
    this.big.textContent = String(this.alive(S.t));
    this.tot.textContent = `${S.data.radar.tracks} tracks in the replay`;
  },
  alive(t) {
    const r = S.data.radar;
    if (!r) return 0;
    const i = Math.floor((t - r.t0) / r.step_ms);
    return i >= 0 && i < r.alive.length ? r.alive[i] : 0;
  },
};
const Log = {
  build(root) {
    this.el = h("div", { class: "log" });
    root.append(S.data.events.length ? this.el : h("div", { class: "empty" }, "No events in this session."));
  },
  update() {
    if (!S.data.events.length) return;
    const t = S.t, past = S.data.events.filter((e) => e.t <= t).slice(-12).reverse();
    const fresh = past.length > 0 && t - past[0].t < 4000;
    const key = past.map((e) => e.t).join(",") + (fresh ? "+" : "");
    if (key === this.key) return;
    this.key = key;
    this.el.replaceChildren(...past.map((e, i) => h("div", { class: i === 0 && fresh ? "new" : "", title: "Jump here", onclick: () => seek(e.t) },
      h("span", {}, hms(e.t)), e.text)));
    if (!past.length) this.el.append(h("div", {}, h("span", {}, "—"), "no events yet at this moment"));
  },
};

// ---------- instrument rail: one press opens or closes an instrument, any number can be open ----------
const INSTR = [
  { k: "over", label: "Overview", icon: '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>' },
  { k: "drones", label: "Drones", n: () => S.data.drones.length,
    icon: '<circle cx="6" cy="6" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/><circle cx="18" cy="18" r="2.6"/><path d="M8 8l8 8M16 8l-8 8"/>' },
  { k: "radar", label: "Radar", n: () => (S.data.radar ? S.data.radar.tracks : 0),
    icon: '<path d="M4 18a10 10 0 0 1 15-9"/><path d="M8 18a6 6 0 0 1 8.4-5.5"/><circle cx="12" cy="18" r="1.8"/><path d="M12 18 19 6"/>' },
  { k: "log", label: "Events", n: () => S.data.events.length,
    icon: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="4.5" cy="18" r="1.2"/>' },
  { k: "atmo", label: "Weather", icon: '<path d="M3 9h11a3 3 0 1 0-3-3"/><path d="M3 14h15a3 3 0 1 1-3 3"/><path d="M3 19h7"/>' },
  { k: "equip", label: "Equipment", n: () => S.data.equipment.length,
    icon: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12 4 7.5"/>' },
];
const X_SVG = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>';
const PREFS = "domeware.hud.view";
function onPress(el, fn) {  // act on press, not on release: it feels instant (Enter or Space on a focused button still works)
  el.addEventListener("pointerdown", (e) => { if (e.button === 0) fn(); });
  el.addEventListener("click", (e) => { if (e.detail === 0) fn(); });
}
const Panels = {
  build() {
    const stack = $("#stack"), rail = $("#rail");
    this.cards = {};
    this.btns = {};
    INSTR.forEach((it, i) => {
      const key = String(i + 1);
      const close = h("button", { class: "x", title: `Close (key ${key})`, "aria-label": `Close ${it.label}`, html: X_SVG });
      close.addEventListener("click", () => this.set(it.k, false));
      const meta = h("span", { class: "meta" }), body = h("div", { class: "body", id: `p-${it.k}` });
      const card = h("section", { class: "card glass inst", id: `card-${it.k}`, hidden: true, "aria-label": it.label },
        h("div", { class: "card-head" }, h("span", { class: "label" }, h("em", {}, key.padStart(2, "0")), it.label), h("i", { class: "rule" }), meta, close), body);
      stack.append(card);
      this.cards[it.k] = { card, body, meta };
      const n = it.n ? it.n() : 0;
      const b = h("button", { class: "rb", "aria-pressed": "false", title: `${it.label}: show or hide (key ${key})` },
        h("kbd", {}, key), n ? h("em", {}, String(n)) : null,
        h("span", { class: "ico", html: `<svg viewBox="0 0 24 24" aria-hidden="true">${it.icon}</svg>` }), h("span", { class: "t" }, it.label));
      onPress(b, () => this.toggle(it.k));
      rail.append(b);
      this.btns[it.k] = b;
    });
    this.hideIco = h("span", { class: "ico" });
    this.hideTxt = h("span", { class: "t" }, "Hide");
    this.hideBtn = h("button", { class: "rb hide", title: "Hide or show all panels for more map (key P)" }, h("kbd", {}, "P"), this.hideIco, this.hideTxt);
    onPress(this.hideBtn, () => this.away(!S.away));
    rail.append(h("i", { class: "rail-sep" }), this.hideBtn);
  },
  meta(k, text) { this.cards[k].meta.textContent = text; },
  restore(want = {}) {  // what was open last time on this computer; a view in the address wins and is not saved
    try {
      const p = SHOT ? null : JSON.parse(localStorage.getItem(PREFS) || "null");
      if (p && Array.isArray(p.open)) S.open = new Set(p.open.filter((k) => INSTR.some((it) => it.k === k)));
      else if (innerHeight < 880) S.open = new Set(["over"]);  // a smaller screen starts with more map
      if (p && p.away) S.away = true;
      if (p && p.glass === false) { S.glass = false; FX.manual = true; }
    } catch { /* no storage here: the defaults */ }
    if (want.open) S.open = new Set(want.open);
    if (want.away !== undefined) S.away = want.away;
    setGlass(S.glass, false);
    for (const it of INSTR) this.sync(it.k);
    this.away(S.away, false);
  },
  save() { if (!SHOT) try { localStorage.setItem(PREFS, JSON.stringify({ open: [...S.open], away: S.away, glass: S.glass })); } catch { /* private window */ } },
  is(k) { return S.open.has(k) && !S.away; },
  toggle(k) { this.set(k, !S.open.has(k)); },
  set(k, on) {
    if (on) S.open.add(k); else S.open.delete(k);
    this.sync(k);
    if (on && S.away) this.away(false);
    this.save();
  },
  sync(k) {
    const on = S.open.has(k), b = this.btns[k];
    this.cards[k].card.hidden = !on;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    if (on && !S.away) this.show(k);
  },
  show(k) {  // fill a card the moment it opens
    if (k === "atmo") Atmo.render();
    else if (k === "equip") Equip.renderScope();
    else ({ over: Over, drones: Drones, radar: Radar, log: Log })[k].update();
    requestAnimationFrame(placeAll);
  },
  away(on, save = true) {
    S.away = on;
    $("#side").classList.toggle("away", on);
    $("#mini").hidden = !on;
    this.hideBtn.classList.toggle("on", on);
    this.hideIco.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${on ? "M15 6l-6 6 6 6" : "M9 6l6 6-6 6"}"/></svg>`;
    this.hideTxt.textContent = on ? "Show" : "Hide";
    if (Drawer.panelSw) Drawer.panelSw.setAttribute("aria-checked", String(!on));
    if (on) Clock.update(); else for (const k of S.open) this.show(k);
    if (save) this.save();
  },
};
function setTz(v) {
  if (S.tz === v) return;
  S.tz = v;
  Clock.tzSeg.set(v);
  if (Drawer.tzSeg) Drawer.tzSeg.set(v);
  Log.key = null;
  TL.render();
  Atmo.render();
  Drawer.times();
  refresh(true);
}
function setGlass(on, save = true) {
  S.glass = on;
  document.body.classList.toggle("solid", !on);
  if (Drawer.glassSw) Drawer.glassSw.setAttribute("aria-checked", String(on));
  if (save) Panels.save();
}
function setLoop(on) {
  S.loop = on;
  TL.loop.classList.toggle("on", on);
  if (Drawer.loopSw) Drawer.loopSw.setAttribute("aria-checked", String(on));
}

// ---------- atmosphere ----------
const WX = [
  ["wind", "Wind", "#0a8db6", "left"], ["gust", "Gust", "#d48a0e", "left"], ["dir", "Dir", "#5b7fa6", "band"],
  ["temp", "Temp", "#b13c8b", "right"], ["rh", "RH", "#138a52", "right"], ["pressure", "Press", "#6252cc", "right"],
];
const WX_UNIT = { wind: "m/s", gust: "m/s", dir: "°", temp: "°C", rh: "%", pressure: "hPa" };
function linePath(ts, vs, X, Y) {
  let d = "", pen = false;
  for (let i = 0; i < ts.length; i++) {
    const v = vs[i];
    if (v === null || v === undefined) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${X(ts[i]).toFixed(1)},${Y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}
function areaPath(ts, vs, X, Y, y0) {
  let d = "", run = [];
  const flush = () => { if (run.length > 1) d += `M${run[0][0]},${y0}` + run.map(([x, y]) => `L${x},${y}`).join("") + `L${run[run.length - 1][0]},${y0}Z`; run = []; };
  for (let i = 0; i < ts.length; i++) {
    const v = vs[i];
    if (v === null || v === undefined) { flush(); continue; }
    run.push([X(ts[i]).toFixed(1), Y(v).toFixed(1)]);
  }
  flush();
  return d;
}
function bandPath(ts, lo, hi, X, Y) {  // the area between two series, only where both have a value
  let d = "", run = [];
  const flush = () => {
    if (run.length > 1) d += `M${run.map(([x, , y]) => `${x},${y}`).join("L")}L${run.slice().reverse().map(([x, y]) => `${x},${y}`).join("L")}Z`;
    run = [];
  };
  for (let i = 0; i < ts.length; i++) {
    if (lo[i] === null || hi[i] === null || lo[i] === undefined || hi[i] === undefined) { flush(); continue; }
    run.push([X(ts[i]).toFixed(1), Y(lo[i]).toFixed(1), Y(hi[i]).toFixed(1)]);
  }
  flush();
  return d;
}
const Atmo = {
  build() {
    const root = $("#p-atmo");
    const wx = S.data.weather;
    if (!wx) { root.append(h("div", { class: "empty" }, "No weather data in this session.")); return; }
    // compass
    const cmp = sv("svg", { class: "compass", viewBox: "-52 -52 104 104" });
    cmp.append(sv("circle", { class: "face", r: 45 }), sv("circle", { class: "ring", r: 30, "stroke-dasharray": "2 4" }));
    for (let a = 0; a < 360; a += 15) {
      const major = a % 90 === 0, r0 = major ? 37 : 40, rad = (a * Math.PI) / 180;
      cmp.append(sv("line", { class: `tick ${major ? "major" : ""}`, x1: Math.sin(rad) * r0, y1: -Math.cos(rad) * r0, x2: Math.sin(rad) * 44, y2: -Math.cos(rad) * 44 }));
    }
    for (const [l, a] of [["N", 0], ["E", 90], ["S", 180], ["W", 270]]) {
      const rad = (a * Math.PI) / 180;
      cmp.append(sv("text", { class: l === "N" ? "n" : "", x: Math.sin(rad) * 50, y: -Math.cos(rad) * 50 }, l));
    }
    this.needle = sv("g", { class: "needle" },
      sv("line", { class: "tail", x1: 0, y1: -34, x2: 0, y2: 18 }),
      sv("path", { d: "M0,30 L-7,14 L0,18 L7,14 Z" }), sv("circle", { r: 3.2, fill: "#0a8db6" }));
    cmp.append(this.needle);
    // numbers
    this.vWind = h("span", { class: "v" }, "—");
    this.vFrom = h("span", { class: "from" }, "—");
    this.kv = {};
    const kv = (k, label) => { const b = h("b", {}, "—"); this.kv[k] = b; return h("div", { class: "kv" }, h("span", {}, label), b); };
    const now = h("div", { class: "wx-now" },
      h("div", { class: "main" }, this.vWind, h("span", { class: "u" }, "M/S WIND"), this.vFrom),
      kv("gust", "GUST"), kv("temp", "TEMP"), kv("rh", "HUMIDITY"), kv("pressure", "PRESSURE"));
    // series choice
    const chips = h("div", { class: "chips atmo-chips" });
    for (const [k, label, color] of WX) {
      if (!wx[k]) continue;
      const c = h("button", { class: `chip ${S.wx[k] ? "on" : ""}`, style: `color:${color}`, title: `Show or hide ${label.toLowerCase()} in the chart` }, h("i"), label);
      c.setAttribute("aria-pressed", String(!!S.wx[k]));
      c.addEventListener("click", () => {
        S.wx[k] = !S.wx[k];
        c.classList.toggle("on", S.wx[k]);
        c.setAttribute("aria-pressed", String(S.wx[k]));
        c.classList.remove("pop"); void c.offsetWidth; c.classList.add("pop");
        this.render();
      });
      chips.append(c);
    }
    this.svg = sv("svg", {});
    const chart = h("div", { class: "chart", title: "Left axis m/s. Temperature, humidity and pressure each on their own scale (values in the tooltip). Arrows: where the wind blows. Click to jump there." },
      this.svg);
    root.append(h("div", { class: "atmo-top" }, cmp, now), chips, chart);
    new ResizeObserver(() => { if (Panels.is("atmo")) this.render(); }).observe(chart);
    this.svg.addEventListener("pointermove", (e) => this.hover(e));
    this.svg.addEventListener("pointerleave", () => { Tip.hide(); if (this.hov) this.hov.setAttribute("visibility", "hidden"); });
    this.svg.addEventListener("click", (e) => { const t = this.timeAt(e); if (t !== null) seek(t); });
  },
  timeAt(e) {
    if (!this.geo) return null;
    const r = this.svg.getBoundingClientRect(), { pl, iw, x0, x1 } = this.geo;
    const x = ((e.clientX - r.left) / r.width) * this.geo.W;
    return x0 + ((x - pl) / iw) * (x1 - x0);
  },
  render() {
    const wx = S.data.weather;
    if (!wx || !this.svg || !Panels.is("atmo")) return;
    const svg = this.svg, W = svg.clientWidth || 380, Hh = svg.clientHeight || 160;
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`);
    svg.replaceChildren();
    const pl = 30, pr = 40, pt = 24, pb = 18, iw = W - pl - pr, ih = Hh - pt - pb;
    const [x0, x1] = S.full;
    const X = (t) => pl + ((t - x0) / (x1 - x0)) * iw;
    const left = [...(wx.wind || []), ...(wx.gust || [])].filter((v) => v !== null);
    const ymax = Math.max(4, Math.ceil(Math.max(...left, 0) + 1));
    const Y = (v) => pt + ih - (v / ymax) * ih;
    this.geo = { W, pl, iw, x0, x1, X, Y, pt, ih };
    const defs = sv("defs", {}, sv("linearGradient", { id: "gWind", x1: 0, y1: 0, x2: 0, y2: 1 },
      sv("stop", { offset: "0%", "stop-color": "#0a8db6", "stop-opacity": 0.28 }), sv("stop", { offset: "100%", "stop-color": "#0a8db6", "stop-opacity": 0 })),
      sv("filter", { id: "glow", x: "-20%", y: "-20%", width: "140%", height: "140%" }, sv("feGaussianBlur", { stdDeviation: 2.2, result: "b" }),
        sv("feMerge", {}, sv("feMergeNode", { in: "b" }), sv("feMergeNode", { in: "SourceGraphic" }))));
    svg.append(defs);
    const [a, b] = S.act;
    svg.append(sv("rect", { class: "act", x: X(a), y: pt, width: Math.max(1, X(b) - X(a)), height: ih }));
    const grid = sv("g", { class: "grid" }), axis = sv("g", { class: "axis" });
    const ystep = ymax <= 6 ? 1 : ymax <= 12 ? 2 : 5;
    for (let v = 0; v <= ymax; v += ystep) {
      grid.append(sv("line", { x1: pl, x2: pl + iw, y1: Y(v), y2: Y(v) }));
      axis.append(sv("text", { x: pl - 6, y: Y(v) + 3, "text-anchor": "end" }, String(v)));
    }
    const step = niceStep((x1 - x0) / 4);
    for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) {
      grid.append(sv("line", { x1: X(t), x2: X(t), y1: pt, y2: pt + ih }));
      axis.append(sv("text", { x: X(t), y: Hh - 5, "text-anchor": "middle" }, hm(t)));
    }
    svg.append(grid, axis);
    if (S.wx.wind && wx.wind) {
      svg.append(sv("path", { d: areaPath(wx.t, wx.wind, X, Y, pt + ih), fill: "url(#gWind)" }));
      svg.append(sv("path", { d: linePath(wx.t, wx.wind, X, Y), fill: "none", stroke: "#0a8db6", "stroke-width": 2 }));
    }
    if (S.wx.wind && S.wx.gust && wx.wind && wx.gust) {  // how gusty: the band between wind and gust
      svg.append(sv("path", { d: bandPath(wx.t, wx.wind, wx.gust, X, Y), fill: "#d48a0e", opacity: 0.13 }));
    }
    if (S.wx.gust && wx.gust) svg.append(sv("path", { d: linePath(wx.t, wx.gust, X, Y), fill: "none", stroke: "#d48a0e", "stroke-width": 1.5, "stroke-dasharray": "5 3" }));
    this.rs = {};
    let rightAxis = false;
    for (const [k, , color, side] of WX) {
      if (side !== "right" || !S.wx[k] || !wx[k]) continue;
      const vs = wx[k].filter((v) => v !== null);
      if (!vs.length) continue;
      let lo = Math.min(...vs), hi = Math.max(...vs);
      const padv = Math.max((hi - lo) * 0.15, k === "pressure" ? 0.3 : k === "rh" ? 2 : 0.5);
      lo -= padv; hi += padv;
      const Yr = (v) => pt + ih - ((v - lo) / (hi - lo)) * ih;
      this.rs[k] = Yr;
      svg.append(sv("path", { d: linePath(wx.t, wx[k], X, Yr), fill: "none", stroke: color, "stroke-width": 1.5, opacity: 0.9 }));
      if (!rightAxis) {
        rightAxis = true;
        for (const f of [0.15, 0.5, 0.85]) {
          const v = lo + (hi - lo) * f;
          axis.append(sv("text", { x: pl + iw + 5, y: Yr(v) + 3, fill: color, "text-anchor": "start" }, v.toFixed(k === "rh" ? 0 : 1)));
        }
      }
    }
    if (S.wx.dir && wx.dir) {  // arrows along the top: where the wind blows
      const band = sv("g", {});
      let lastX = -99;
      for (let i = 0; i < wx.t.length; i++) {
        const d = wx.dir[i];
        if (d === null) continue;
        const x = X(wx.t[i]);
        if (x - lastX < 15 || x < pl || x > pl + iw) continue;
        lastX = x;
        band.append(sv("path", { d: "M0,-5 L3.6,3.5 L0,1.6 L-3.6,3.5 Z", fill: "#5b7fa6", opacity: 0.8, transform: `translate(${x.toFixed(1)},${pt - 11}) rotate(${(d + 180).toFixed(0)})` }));
      }
      svg.append(band);
    }
    this.cur = sv("g", { class: "cursor" }, sv("line", { y1: pt - 4, y2: pt + ih }));
    this.dots = {};
    for (const [k, , color, side] of WX) {
      if (side === "band" || !S.wx[k] || !wx[k] || (side === "right" && !this.rs[k])) continue;
      const c = sv("circle", { r: 3.6, fill: color, stroke: "#02060c", "stroke-width": 1.5 });
      this.dots[k] = c;
      this.cur.append(c);
    }
    this.hov = sv("g", { class: "hover", visibility: "hidden" }, sv("line", { y1: pt, y2: pt + ih }));
    svg.append(this.hov, this.cur);
    this.update();
  },
  update() {
    const wx = S.data.weather;
    if (!wx || !this.needle) return;
    const t = S.t;
    const v = (k) => at(wx.t, wx[k], t);
    const wind = v("wind"), dir = v("dir");
    this.vWind.textContent = fx(wind);
    this.vFrom.innerHTML = dir === null ? "—" : `<small>FROM</small>${compass(dir)} ${fx(dir, 0)}°`;
    if (dir !== null) this.needle.setAttribute("transform", `rotate(${dir.toFixed(1)})`);
    this.kv.gust.textContent = `${fx(v("gust"))} m/s`;
    this.kv.temp.textContent = `${fx(v("temp"))} °C`;
    this.kv.rh.textContent = `${fx(v("rh"), 0)} %`;
    this.kv.pressure.textContent = `${fx(v("pressure"))} hPa`;
    if (!this.geo || !this.cur || !Panels.is("atmo")) return;
    const { X, Y } = this.geo, x = X(t);
    this.cur.setAttribute("transform", `translate(${x.toFixed(1)},0)`);
    this.cur.querySelector("line").setAttribute("x1", 0);
    this.cur.querySelector("line").setAttribute("x2", 0);
    for (const [k, c] of Object.entries(this.dots)) {
      const val = v(k);
      if (val === null) { c.setAttribute("visibility", "hidden"); continue; }
      c.setAttribute("visibility", "visible");
      c.setAttribute("cx", 0);
      c.setAttribute("cy", (this.rs[k] || Y)(val).toFixed(1));
    }
  },
  hover(e) {
    const t = this.timeAt(e), wx = S.data.weather;
    if (t === null || t < this.geo.x0 || t > this.geo.x1) return;
    const x = this.geo.X(t);
    this.hov.setAttribute("visibility", "visible");
    this.hov.setAttribute("transform", `translate(${x.toFixed(1)},0)`);
    const rows = WX.filter(([k]) => wx[k]).map(([k, label, color]) => {
      const val = at(wx.t, wx[k], t);
      return `<span style="color:${color}">${label.toUpperCase()}</span><b>${val === null ? "—" : k === "dir" ? `${compass(val)} ${fx(val, 0)}°` : `${fx(val, k === "rh" ? 0 : 1)} ${WX_UNIT[k]}`}</b>`;
    }).join("");
    Tip.show(`<b>${hms(t)}</b> <span style="color:var(--muted)">${S.tz}</span><div class="rows2">${rows}</div>`, e.clientX, e.clientY);
  },
};

// ---------- equipment ----------
const ICON = {
  radar: '<path d="M4 18a10 10 0 0 1 15-9"/><path d="M8 18a6 6 0 0 1 8.4-5.5"/><circle cx="12" cy="18" r="1.8"/><path d="M12 18 19 6"/>',
  drone_home: '<circle cx="6" cy="6" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/><circle cx="18" cy="18" r="2.6"/><path d="M8 8l8 8M16 8l-8 8"/>',
  weather: '<path d="M3 9h11a3 3 0 1 0-3-3"/><path d="M3 14h15a3 3 0 1 1-3 3"/><path d="M3 19h7"/>',
  rf: '<path d="M12 21V11"/><circle cx="12" cy="9" r="2"/><path d="M7.8 13.2a6 6 0 0 1 0-8.4"/><path d="M16.2 4.8a6 6 0 0 1 0 8.4"/><path d="M5 16a9.5 9.5 0 0 1 0-14"/><path d="M19 2a9.5 9.5 0 0 1 0 14"/>',
  operator: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
  pilot: '<circle cx="12" cy="7" r="3.2"/><path d="M5 21a7 7 0 0 1 14 0"/>',
  camera: '<rect x="3" y="7" width="13" height="10" rx="1.5"/><path d="M16 11l5-3v8l-5-3z"/>',
  sensor: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8" stroke-dasharray="2 3"/>',
  effector: '<path d="M12 2v6M12 16v6M2 12h6M16 12h6"/><circle cx="12" cy="12" r="4"/>',
  other: '<path d="M12 3l9 9-9 9-9-9z"/>',
};
const GROUPS = [
  ["all", "All equipment", () => true],
  ["sensors", "Sensors", (k) => ["radar", "rf", "camera", "sensor"].includes(k)],
  ["weather", "Weather", (k) => k === "weather"],
  ["launch", "Take-off points", (k) => k === "drone_home"],
  ["crew", "Crew & operators", (k) => ["pilot", "operator"].includes(k)],
  ["effectors", "Effectors", (k) => k === "effector"],
  ["other", "Other", (k) => k === "other"],
];
const Equip = {
  build() {
    const root = $("#p-equip");
    const items = S.data.equipment;
    if (!items.length) { root.append(h("div", { class: "empty" }, "No equipment known yet: add rows to equipment.csv.")); return; }
    // the choice list
    this.ddBtn = h("button", {});
    this.menu = h("div", { class: "menu", hidden: true });
    const dd = h("div", { class: "dd" }, this.ddBtn, this.menu);
    this.ddBtn.addEventListener("click", (e) => { e.stopPropagation(); this.menu.hidden = !this.menu.hidden; });
    document.addEventListener("click", () => { this.menu.hidden = true; });
    for (const [key, label, test] of GROUPS) {
      const n = items.filter((i) => test(i.kind)).length;
      if (!n) continue;
      const b = h("button", { "data-k": key }, label, h("em", {}, String(n)));
      b.addEventListener("click", () => { S.filter = key; this.menu.hidden = true; this.renderList(); this.renderScope(); });
      this.menu.append(b);
    }
    const hasDrones = S.data.drones.length > 0;
    this.scopeSeg = seg([["site", "Site"], ["area", "Area", null, !hasDrones]], "site", (v) => { S.scope = v; this.renderScope(); });
    this.svg = sv("svg", { class: "scope" });
    this.live = sv("g", {});
    this.note = h("div", { class: "scope-note" });
    const wrap = h("div", { class: "scope-wrap" }, this.svg, h("div", { class: "sweep" }, h("i")), h("div", { class: "scope-ctl" }, this.scopeSeg), this.note);
    root.append(h("div", { class: "eq-head" }, dd, h("span", { class: "note" }, "RADAR AT THE CENTRE")), wrap, (this.list = h("div", { class: "eq-list" })));
    this.renderList();
    new ResizeObserver(() => { if (Panels.is("equip")) this.renderScope(); }).observe(wrap);
  },
  visible() { const g = GROUPS.find(([k]) => k === S.filter) || GROUPS[0]; return S.data.equipment.filter((i) => g[2](i.kind)); },
  select(id, fromViewer = false) {
    S.sel = S.sel === id && !fromViewer ? null : id;
    const it = S.data.equipment.find((i) => i.id === id);
    if (it && !this.visible().includes(it)) S.filter = "all";
    this.renderList();
    this.renderScope();
    const row = this.list && this.list.querySelector(`[data-id="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  },
  renderList() {
    if (!this.list) return;
    const g = GROUPS.find(([k]) => k === S.filter) || GROUPS[0];
    const vis = this.visible();
    this.ddBtn.replaceChildren(h("span", {}, g[1]), h("em", {}, String(vis.length)));
    for (const b of this.menu.children) b.classList.toggle("on", b.dataset.k === S.filter);
    // a swarm's take-off points fold into one row under "All": click it to see them
    const homes = vis.filter((i) => i.kind === "drone_home");
    const fold = S.filter === "all" && homes.length > 3 && !homes.some((i) => i.id === S.sel);
    const shown = fold ? vis.filter((i) => i.kind !== "drone_home") : vis;
    const group = fold ? (() => {
      const row = h("div", { class: "eq-row" }, h("div", { class: "eq-main" },
        h("span", { class: "eq-ico", style: `color:${KIND_COLOR.drone_home}`, html: `<svg viewBox="0 0 24 24">${ICON.drone_home}</svg>` }),
        h("div", {}, h("div", { class: "eq-name" }, "Take-off points"), h("div", { class: "eq-type" }, `${homes.length} drones · click to list them`)),
        h("div", { class: "eq-pos" }, dist(Math.min(...homes.map((i) => i.range_m))), h("small", {}, "nearest to the radar"))));
      row.addEventListener("click", () => { S.filter = "launch"; this.renderList(); this.renderScope(); });
      return row;
    })() : null;
    this.list.replaceChildren(...(group ? [group] : []), ...shown.map((it) => {
      const ico = h("span", { class: "eq-ico", style: `color:${KIND_COLOR[it.kind] || KIND_COLOR.other}`, html: `<svg viewBox="0 0 24 24">${ICON[it.kind] || ICON.other}</svg>` });
      const main = h("div", { class: "eq-main" }, ico,
        h("div", { style: "min-width:0" }, h("div", { class: "eq-name", title: it.name }, it.name), h("div", { class: "eq-type" }, it.type)),
        h("div", { class: "eq-pos" }, it.id === "radar" ? "ORIGIN" : dist(it.range_m), h("small", {}, it.id === "radar" ? "radar frame" : `${fx(it.bearing_deg, 0)}° · ${compass(it.bearing_deg)}`)));
      main.tabIndex = 0;
      main.setAttribute("role", "button");
      main.setAttribute("aria-expanded", String(S.sel === it.id));
      main.addEventListener("click", () => this.select(it.id));
      main.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.select(it.id); } });
      const row = h("div", { class: `eq-row ${S.sel === it.id ? "sel" : ""}`, "data-id": it.id }, main);
      if (S.sel === it.id) {
        const f = (k, v, wide = false) => h("div", { class: `f ${wide ? "w" : ""}` }, h("span", {}, k), h("b", { html: v }));
        row.append(h("div", { class: "eq-detail" },
          f("TYPE", esc(it.type.toUpperCase())),
          f("HEIGHT AGL", `${fx(it.height_m, 1)} m${it.height_assumed ? '<span class="tag">ASSUMED</span>' : ""}`),
          f("POSITION", `${it.lat.toFixed(5)}, ${it.lon.toFixed(5)}`),
          f("FROM RADAR", it.id === "radar" ? "—" : `${dist(it.range_m)} @ ${fx(it.bearing_deg, 0)}°`),
          f("SOURCE", esc(it.source), true),
          it.note ? f("NOTE", esc(it.note), true) : null));
      }
      return row;
    }));
  },
  renderScope() {
    if (!this.svg || !Panels.is("equip")) return;
    const svg = this.svg, W = svg.clientWidth || 380, Hh = svg.clientHeight || 236;
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`);
    svg.replaceChildren();
    const cx = W / 2, cy = Hh / 2 + 6, R = Math.min(W, Hh) / 2 - 22;
    const items = S.data.equipment, drones = S.data.drones;
    let need = Math.max(25, ...items.map((i) => i.range_m)) * 1.2;
    if (S.scope === "area") for (const d of drones) need = Math.max(need, ...d.series.range.filter((v) => v !== null).map((v) => v * 1.05));
    const maxR = niceCeil(need);
    this.geo = { cx, cy, R, maxR };
    this.note.textContent = S.scope === "site" ? `RADAR-CENTRED · ${maxR} m` : `RADAR-CENTRED · ${dist(maxR)}`;
    const P = (e, n) => { let x = (e / maxR) * R, y = (n / maxR) * R; const r = Math.hypot(x, y); if (r > R) { x *= R / r; y *= R / r; } return [cx + x, cy - y, r > R]; };
    for (let k = 1; k <= 4; k++) {
      const r = (R * k) / 4;
      svg.append(sv("circle", { class: `ring ${k === 4 ? "outer" : ""}`, cx, cy, r }));
      svg.append(sv("text", { class: "rl", x: cx + 4, y: cy - r - 3 }, dist((maxR * k) / 4)));
    }
    for (let a = 0; a < 360; a += 30) {
      const rad = (a * Math.PI) / 180;
      svg.append(sv("line", { class: "cross", x1: cx + Math.sin(rad) * (a % 90 ? R - 5 : 0), y1: cy - Math.cos(rad) * (a % 90 ? R - 5 : 0), x2: cx + Math.sin(rad) * R, y2: cy - Math.cos(rad) * R }));
    }
    svg.append(sv("text", { class: "north", x: cx, y: cy - R - 6 }, "N"));
    const vis = new Set(this.visible().map((i) => i.id));
    this.labels = [];  // [x, y, width] of each name drawn: names that would overlap are left to the tooltip
    for (const it of items) {
      const [x, y] = it.id === "radar" ? [cx, cy] : P(it.e, it.n);
      const sel = S.sel === it.id;
      const g = sv("g", { class: `item ${sel ? "sel" : ""}`, opacity: vis.has(it.id) ? 1 : 0.22 });
      if (sel) g.append(sv("circle", { class: "halo", cx: x, cy: y, r: 9 }));
      if (it.kind === "radar") g.append(sv("path", { d: `M${x},${y - 8} L${x + 7},${y + 5} L${x - 7},${y + 5} Z`, fill: KIND_COLOR.radar, stroke: "#fff", "stroke-width": 1.5 }));
      else g.append(sv("circle", { class: "dot", cx: x, cy: y, r: sel ? 6.5 : 5, fill: KIND_COLOR[it.kind] || KIND_COLOR.other }));
      if (sel || (items.length <= 3 && this.free(x + 9, y - 8, it.name, this.labels))) {
        g.append(sv("text", { x: x + 9, y: y - 8 }, it.name));
        this.labels.push([x + 9, y - 8, it.name.length * 6]);
      }
      g.addEventListener("click", () => this.select(it.id));
      g.addEventListener("pointermove", (e) => Tip.show(`<b>${esc(it.name)}</b><br><span style="color:var(--muted)">${esc(it.type)} · ${it.id === "radar" ? "origin" : `${dist(it.range_m)} @ ${fx(it.bearing_deg, 0)}°`}</span>`, e.clientX, e.clientY));
      g.addEventListener("pointerleave", () => Tip.hide());
      svg.append(g);
    }
    this.live = sv("g", {});
    svg.append(this.live);
    this.P = P;
    this.liveKey = null;
    this.updateLive();
  },
  free(x, y, name, taken) {  // room for a name here?
    const w = name.length * 6;
    return !taken.some(([tx, ty, tw]) => x < tx + tw + 6 && tx < x + w + 6 && Math.abs(y - ty) < 12);
  },
  updateLive() {
    if (!this.live || !this.P || !Panels.is("equip") || !S.data.drones.length) return;
    const t = S.t, key = `${Math.round(t / 250)}|${S.drone}|${S.scope}`;
    if (key === this.liveKey) return;  // nothing moved since the last drawing
    this.liveKey = key;
    this.live.replaceChildren();
    const taken = (this.labels || []).slice();
    for (const d of S.data.drones) {
      const e = droneAt(d, "e", t), n = droneAt(d, "n", t);
      if (e === null || n === null) continue;
      let tail = "";
      for (let k = 30; k >= 0; k -= 2) {
        const te = droneAt(d, "e", t - k * 1000), tn = droneAt(d, "n", t - k * 1000);
        if (te === null || tn === null) continue;
        const [x, y] = this.P(te, tn);
        tail += `${tail ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      }
      const [x, y, out] = this.P(e, n);
      const big = S.drone === d.name;
      const tracked = droneAt(d, "err", t) !== null;
      const g = sv("g", { class: "drone" });
      if (tail) g.append(sv("path", { d: tail, fill: "none", stroke: d.ui, "stroke-width": 1.6, opacity: 0.55 }));
      g.append(sv("circle", { cx: x, cy: y, r: big ? 6 : 4.5, fill: d.ui, opacity: out ? 0.55 : 1 }));
      if (S.data.radar) g.append(sv("circle", { cx: x, cy: y, r: big ? 9 : 7, fill: "none", stroke: tracked ? "#138a52" : "#cf3a57", "stroke-width": 1.2, opacity: 0.85 }));
      if (big || (S.data.drones.length <= 4 && this.free(x + 8, y + 3, d.name, taken))) {
        g.append(sv("text", { x: x + 8, y: y + 3, fill: d.ui }, d.name));
        taken.push([x + 8, y + 3, d.name.length * 6]);
      }
      this.live.append(g);
    }
  },
};

// ---------- timeline ----------
const LANES = [  // h: lane height; hs: on a smaller screen
  { key: "ruler", label: "TIME", h: 26, hs: 22 },
  { key: "activity", label: "ACTIVITY", h: 34, hs: 30 },
  { key: "drones", label: "DRONES", h: 0, hs: 0 },
  { key: "radar", label: "RADAR", h: 22, hs: 18 },
  { key: "events", label: "EVENTS", h: 20, hs: 18 },
  { key: "screens", label: "SCREENS", h: 42, hs: 38 },
  { key: "wind", label: "WIND", h: 34, hs: 26 },
];
const laneH = (key) => LANES.find((l) => l.key === key).h;
function evKind(text) {
  const t = text.toLowerCase();
  if (t.startsWith("note")) return "note";
  if (/take-?off|landing|landed|armed|disarm|failsafe|mode/.test(t)) return "drone";
  if (/ (start|end)$/.test(t)) return "run";
  if (/alarm|class|track|first/.test(t)) return "radar";
  return "";
}
const PLAY = '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>';
const PAUSE = '<svg viewBox="0 0 24 24"><path d="M6 4h4.5v16H6zM13.5 4H18v16h-4.5z"/></svg>';
const TL = {
  build() {
    const root = $("#timeline");
    this.playBtn = h("button", { class: "playbtn", title: "Play / pause (Space)", html: PLAY, onclick: () => setPlaying(!S.playing) });
    this.nowB = h("b", {}, "--:--:--");
    this.nowS = h("span", {}, "UTC");
    const tr = (label, title, fn) => h("button", { title, onclick: fn }, label);
    this.speed = seg([["0.5", "½×"], ["1", "1×"], ["2", "2×"], ["5", "5×"], ["10", "10×"], ["30", "30×"]], "1", (v) => { S.speed = +v; });
    this.loop = h("button", { class: "chip loopchip on", title: "Loop the activity" }, h("i"), "LOOP");
    this.loop.addEventListener("click", () => setLoop(!S.loop));
    root.append(h("div", { class: "tl-ctrl" },
      h("div", { class: "tl-top" }, this.playBtn, h("div", { class: "tl-now" }, this.nowB, this.nowS)),
      h("div", { class: "transport" }, tr("|◀", "Start of the activity (Home)", () => seek(S.act[0])), tr("−10 s", "Back 10 s (← 5 s)", () => seek(S.t - 10000)),
        tr("+10 s", "Forward 10 s (→ 5 s)", () => seek(S.t + 10000)), tr("▶|", "End of the activity (End)", () => seek(S.act[1]))),
      h("div", { class: "speed-row" }, this.speed, this.loop)));
    if (innerHeight < 880) for (const L of LANES) L.h = L.hs;
    const n = S.data.drones.length;
    LANES.find((l) => l.key === "drones").h = n ? clamp(n * (n <= 8 ? 14 : 9) + 10, 28, 72) : 24;  // room for height profiles
    this.labels = h("div", { class: "tl-labels" });
    this.track = h("div", { class: "tl-track" });
    this.grid = h("div", { style: "position:absolute;inset:0;pointer-events:none" });
    this.track.append(this.grid);
    this.lanes = {};
    for (const L of LANES) {
      this.labels.append(h("div", { class: L.key, style: `height:${L.h}px` }, L.label));
      const lane = h("div", { class: `lane ${L.key}`, style: `height:${L.h}px` });
      this.track.append(lane);
      this.lanes[L.key] = lane;
    }
    this.ph = h("div", { class: "playhead" }, h("span", {}, ""));
    this.hv = h("div", { class: "hoverline", hidden: true }, h("span", {}, ""));
    this.track.append(this.ph, this.hv);
    const zb = (label, title, fn) => h("button", { title, onclick: fn }, label);
    root.append(h("div", { class: "tl-main" }, this.labels, this.track,
      h("div", { class: "tl-zoom" }, zb("FIT", "Fit the activity (double-click)", () => this.setView(padRange(S.act, 0.04))),
        zb("ALL", "Everything in the replay", () => this.setView(S.full)), zb("+", "Zoom in (wheel)", () => this.zoom(1 / 1.6, S.t)), zb("−", "Zoom out (wheel)", () => this.zoom(1.6, S.t)))));
    const tk = this.track;
    tk.addEventListener("pointerdown", (e) => { if (e.target.closest(".thumb")) return; this.drag = true; tk.setPointerCapture(e.pointerId); seek(this.timeAt(e)); });
    tk.addEventListener("pointermove", (e) => { if (this.drag) seek(this.timeAt(e)); this.hover(e); });
    tk.addEventListener("pointerup", () => { this.drag = false; });
    tk.addEventListener("pointerleave", () => { this.hv.hidden = true; });
    tk.addEventListener("wheel", (e) => { e.preventDefault(); this.zoom(e.deltaY > 0 ? 1.25 : 0.8, this.timeAt(e)); }, { passive: false });
    tk.addEventListener("dblclick", () => this.setView(padRange(S.act, 0.04)));
    this.render();
  },
  timeAt(e) { const r = this.track.getBoundingClientRect(); return S.view[0] + ((e.clientX - r.left) / r.width) * (S.view[1] - S.view[0]); },
  x(t) { return ((t - S.view[0]) / (S.view[1] - S.view[0])) * this.W; },
  setView([a, b]) {
    const span = Math.max(5000, b - a);
    a = clamp(a, S.full[0], S.full[1] - Math.min(span, S.full[1] - S.full[0]));
    S.view = [a, Math.min(S.full[1], a + span)];
    this.render();
  },
  zoom(f, anchor) {
    const [a, b] = S.view, span = clamp((b - a) * f, 5000, S.full[1] - S.full[0]);
    const k = (anchor - a) / (b - a);
    this.setView([anchor - k * span, anchor - k * span + span]);
  },
  playIcon() {
    this.playBtn.innerHTML = S.playing ? PAUSE : PLAY;
    this.playBtn.classList.toggle("playing", S.playing);
    this.playBtn.setAttribute("aria-label", S.playing ? "Pause" : "Play");
  },
  hover(e) {
    const t = this.timeAt(e);
    this.hv.hidden = false;
    this.hv.style.transform = `translateX(${this.x(t)}px)`;
    this.hv.firstChild.textContent = hms(t);
  },
  playhead() {
    if (!this.W) return;
    const [a, b] = S.view, span = b - a;
    if (S.playing && (S.t > b - span * 0.04 || S.t < a)) {
      const na = clamp(S.t - span * 0.12, S.full[0], S.full[1] - span);
      if (Math.abs(na - a) > 1) { this.setView([na, na + span]); return; }  // at the end of the data the view stays put
    }
    const x = this.x(S.t), inside = S.t >= a && S.t <= b;
    this.ph.style.display = inside ? "" : "none";
    this.ph.style.transform = `translateX(${x}px)`;
    const sec = Math.floor(S.t / 1000) + S.tz;
    if (sec !== this.lastSec) {  // text only when the second changes: formatting dates every frame is not free
      this.lastSec = sec;
      const txt = hms(S.t);
      this.ph.firstChild.textContent = txt;
      this.nowB.textContent = txt;
      this.nowS.textContent = `${S.tz === "UTC" ? "UTC" : "LOCAL"} · ${dur(S.t - S.act[0])}`;
    }
  },
  render() {
    this.W = this.track.clientWidth || 1;
    const d = S.data, [v0, v1] = S.view, X = (t) => this.x(t), within = (a, b) => b >= v0 && a <= v1;
    this.labels.querySelector(".ruler").textContent = S.tz === "UTC" ? "UTC" : "LOCAL";
    // ruler + grid
    const L = this.lanes;
    for (const lane of Object.values(L)) lane.replaceChildren();
    this.grid.replaceChildren();
    const step = niceStep((v1 - v0) / Math.max(1, this.W / 110)), minor = step / (step >= 6e4 ? 4 : 5);
    for (let t = Math.ceil(v0 / minor) * minor; t <= v1; t += minor) {
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      L.ruler.append(h("div", { class: `tick ${major ? "" : "minor"}`, style: `left:${X(t)}px` }, major ? h("span", {}, step < 6e4 ? hms(t) : hm(t)) : null));
      if (major) this.grid.append(h("div", { class: "gridline", style: `left:${X(t)}px` }));
    }
    // activity and runs
    const band = (lane, a, b, cls, text, title, pos = "") => {
      if (!within(a, b)) return;
      const x0 = X(a), w = Math.max(3, X(b) - x0);
      lane.append(h("div", { class: `band ${cls}`, style: `left:${x0}px;width:${w}px;${pos}`, title }, w > 70 ? text : ""));
    };
    // two rows when there are runs: the whole run (or the activity) above, its phases below
    const whole = d.runs.filter((r) => /^run$/i.test(r.part)), phases = d.runs.filter((r) => !/^run$/i.test(r.part));
    const top = phases.length ? "top:3px;bottom:auto;height:11px;font-size:8.5px" : "";
    const low = "top:16px;bottom:3px";
    if (whole.length) for (const r of whole) band(L.activity, r.start, r.end, "", `${r.id} · RUN`.toUpperCase(), `${r.id}: the whole run`, top);
    else if (d.activity) band(L.activity, d.activity[0], d.activity[1], "", `ACTIVITY  ${hm(d.activity[0])} → ${hm(d.activity[1])}`, "The time window the replay is built around", top);
    for (const r of phases) band(L.activity, r.start, r.end, "run", `${r.part}`.toUpperCase(), `${r.id} ${r.part}`, low);
    // drones
    if (d.drones.length) {
      const n = d.drones.length, lh = LANES.find((l) => l.key === "drones").h, rowH = (lh - 6) / n;
      if (n <= 8) {  // each drone's height as a faint profile in its own row, under the radar bars
        const top = Math.max(1, ...d.drones.flatMap((dr) => (dr.series.height || []).filter((v) => v !== null)));
        const prof = sv("svg", { viewBox: `0 0 ${this.W} ${lh}`, preserveAspectRatio: "none" });
        d.drones.forEach((dr, i) => {
          const hs = dr.series.height, ts = dr.series.t;
          if (!hs) return;
          const y0 = 3 + (i + 1) * rowH - 1, span = rowH - 2;
          const i0 = Math.max(0, Math.floor((v0 - ts[0]) / 1000)), i1 = Math.min(ts.length - 1, Math.ceil((v1 - ts[0]) / 1000));
          const stepI = Math.max(1, Math.ceil((i1 - i0) / (this.W / 2)));
          const sub = { t: [], h: [] };
          for (let k = i0; k <= i1; k += stepI) { sub.t.push(ts[k]); sub.h.push(hs[k]); }
          prof.append(sv("path", { d: areaPath(sub.t, sub.h, X, (v) => y0 - (v / top) * span, y0), fill: dr.ui, opacity: 0.16 }));
          prof.append(sv("path", { d: linePath(sub.t, sub.h, X, (v) => y0 - (v / top) * span), fill: "none", stroke: dr.ui, "stroke-width": 1, opacity: 0.55 }));
        });
        L.drones.append(prof);
      }
      d.drones.forEach((dr, i) => {
        const y = 3 + i * rowH + rowH / 2;
        for (const [a, b] of dr.flying) {
          if (!within(a, b)) continue;
          const cls = d.radar ? "seg-lost" : "seg-fly";
          L.drones.append(h("div", { class: cls, style: `left:${X(a)}px;width:${Math.max(2, X(b) - X(a))}px;top:${y - (d.radar ? 3 : 2)}px;${d.radar ? "" : `background:${dr.ui}`}`, title: `${dr.name}: airborne${d.radar ? ", radar not on it" : ""}` }));
        }
        for (const [a, b] of dr.tracked) {
          if (!within(a, b)) continue;
          L.drones.append(h("div", { class: "seg-trk", style: `left:${X(a)}px;width:${Math.max(2, X(b) - X(a))}px;top:${y - 3}px;background:${dr.ui}`, title: `${dr.name}: tracked by the radar` }));
        }
      });
    } else if (d.flights_header.length) {
      for (const f of d.flights_header) band(L.drones, f.start, f.end, "hdr", `${f.name.toUpperCase()} · FROM LOG HEADER · PATH NOT DECODED`, "The DJI log is encrypted: its header gives the flight time");
    } else L.drones.append(h("div", { class: "lane-note" }, "no drone logs"));
    // radar density
    if (d.radar) {
      const r = d.radar, max = Math.max(1, ...r.alive), lh = laneH("radar"), bins = Math.max(1, Math.floor(this.W / 2));
      let path = "";
      for (let k = 0; k < bins; k++) {
        const ta = v0 + ((v1 - v0) * k) / bins, tb = v0 + ((v1 - v0) * (k + 1)) / bins;
        const ia = Math.max(0, Math.floor((ta - r.t0) / r.step_ms)), ib = Math.min(r.alive.length, Math.ceil((tb - r.t0) / r.step_ms));
        let m = 0;
        for (let i = ia; i < ib; i++) m = Math.max(m, r.alive[i]);
        if (m) path += `M${(k * 2).toFixed(0)},${lh}V${(lh - 2 - (m / max) * (lh - 6)).toFixed(1)}`;
      }
      const svg = sv("svg", { viewBox: `0 0 ${this.W} ${lh}`, preserveAspectRatio: "none" }, sv("path", { d: path, stroke: "#0a8db6", "stroke-width": 1.6, opacity: 0.8 }));
      L.radar.append(svg);
    } else L.radar.append(h("div", { class: "lane-note" }, d.status.drone_logs_encrypted || d.status.screens ? "radar data pending (third party)" : "no radar data"));
    // events
    for (const e of d.events) {
      if (e.t < v0 || e.t > v1) continue;
      const m = h("div", { class: `ev ${evKind(e.text)}`, style: `left:${X(e.t)}px` });
      m.addEventListener("pointerenter", (ev) => Tip.show(`<b>${hms(e.t)}</b><br>${esc(e.text)}`, ev.clientX, ev.clientY));
      m.addEventListener("pointerleave", () => Tip.hide());
      L.events.append(m);
    }
    // screens: thumbnails, or a thin mark when they would overlap
    let last = -999;
    d.screens.forEach((sc, i) => {
      if (sc.t < v0 || sc.t > v1) return;
      const x = X(sc.t);
      const room = x - last >= 54;
      const el = room
        ? h("div", { class: "thumb", style: `left:${x}px;top:4px` }, h("img", { src: `/data/${sc.thumb}`, alt: "", loading: "lazy" }))
        : h("div", { class: "thumb pip", style: `left:${x}px;top:24px` });
      if (room) last = x;
      el.addEventListener("pointerenter", (ev) => Tip.show(`<b>${hms(sc.t)}</b> <span style="color:var(--muted)">${esc(sc.group.toUpperCase())} · ${i + 1}/${d.screens.length}</span><img src="/data/${sc.full}" alt="">`, ev.clientX, ev.clientY));
      el.addEventListener("pointerleave", () => Tip.hide());
      el.addEventListener("click", (ev) => { ev.stopPropagation(); Tip.hide(); Lightbox.open(i); });
      L.screens.append(el);
    });
    if (!d.screens.length) L.screens.append(h("div", { class: "lane-note" }, "no screenshots"));
    // wind sparkline
    const wx = d.weather;
    if (wx && wx.wind) {
      const lh = laneH("wind"), top = Math.max(1, ...[...wx.wind, ...(wx.gust || [])].filter((v) => v !== null));
      const Y = (v) => lh - 3 - (v / top) * (lh - 7);
      const svg = sv("svg", { viewBox: `0 0 ${this.W} ${lh}`, preserveAspectRatio: "none" },
        sv("defs", {}, sv("linearGradient", { id: "gw2", x1: 0, y1: 0, x2: 0, y2: 1 }, sv("stop", { offset: "0%", "stop-color": "#0a8db6", "stop-opacity": 0.3 }), sv("stop", { offset: "100%", "stop-color": "#0a8db6", "stop-opacity": 0 }))),
        sv("path", { d: areaPath(wx.t, wx.wind, X, Y, lh), fill: "url(#gw2)" }),
        sv("path", { d: linePath(wx.t, wx.wind, X, Y), fill: "none", stroke: "#0a8db6", "stroke-width": 1.4 }),
        wx.gust ? sv("path", { d: linePath(wx.t, wx.gust, X, Y), fill: "none", stroke: "#d48a0e", "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.85 }) : null);
      L.wind.append(svg);
    } else L.wind.append(h("div", { class: "lane-note" }, "no weather data"));
    this.playhead();
  },
};

// ---------- screenshots, large ----------
const Lightbox = {
  i: 0,
  build() {
    $("#lbClose").addEventListener("click", () => this.close());
    $("#lbPrev").addEventListener("click", () => this.open(this.i - 1));
    $("#lbNext").addEventListener("click", () => this.open(this.i + 1));
    $("#lbJump").addEventListener("click", () => { seek(S.data.screens[this.i].t); this.close(); });
    $("#lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") this.close(); });
  },
  open(i) {
    const list = S.data.screens;
    if (!list.length) return;
    this.i = (i + list.length) % list.length;
    const sc = list[this.i];
    $("#lbImg").src = `/data/${sc.full}`;
    $("#lbTime").textContent = `${hms(sc.t)} ${S.tz === "UTC" ? "UTC" : ""}`;
    $("#lbName").textContent = `${sc.group.toUpperCase()} · ${this.i + 1}/${list.length} · ${sc.name}`;
    $("#lightbox").hidden = false;
  },
  close() { $("#lightbox").hidden = true; },
};

// ---------- a view in the address: ?t=13:45:10&open=over,radar&panels=0&menu=1&tz=local (dw hud --at/--open, dw shots) ----------
function parseAt(v) {
  if (!v) return null;
  if (/^\d{12,}$/.test(v)) return +v;
  if (/^\d{4}-\d\d-\d\dT/.test(v)) { const ms = Date.parse(v); return Number.isFinite(ms) ? ms : null; }
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(v.trim());
  if (!m) return null;
  const ms = ((+m[1] * 60 + +m[2]) * 60 + +(m[3] || 0)) * 1000 + +(m[4] || "0").padEnd(3, "0");
  const day = Math.floor(S.act[0] / 864e5) * 864e5;  // that UTC time on the activity's day, or the next day in the replay
  const at = [day, day - 864e5, day + 864e5].map((d) => d + ms);
  return at.find((x) => x >= S.full[0] && x <= S.full[1]) ?? at[0];
}
const View = {
  read() {
    const v = {}, t = parseAt(qs.get("t")), open = qs.get("open");
    if (t !== null) v.t = clamp(t, S.full[0], S.full[1]);
    if (open !== null) v.open = open === "all" ? INSTR.map((i) => i.k) : open.split(",").filter((k) => INSTR.some((i) => i.k === k));
    if (qs.get("panels") === "0") v.away = true;
    if (qs.get("menu") === "1") v.menu = true;
    if ((qs.get("tz") || "").toLowerCase() === "local") v.tz = "LOCAL";
    return v;
  },
  at() { const p = parts(S.t, "UTC"); return `${p.hour}:${p.minute}:${p.second}`; },
  open() { const o = INSTR.map((i) => i.k).filter((k) => S.open.has(k)); return o.length ? o.join(",") : "none"; },
  command() { return `dw hud ${S.data.session} --at ${this.at()} --open ${this.open()}${S.away ? " --hide" : ""}`; },
  async copy() {  // the same moment and instruments, as one line to paste in chat or a terminal
    const text = this.command();
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch {
      const ta = h("textarea", { style: "position:fixed;left:-9999px" }, text);
      document.body.append(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch { /* no clipboard here */ }
      ta.remove();
    }
    toast(ok ? `Copied: ${text}` : text);
  },
  reload() {  // the page again at this moment, with these instruments (and playing, if it was)
    location.href = `${location.pathname}?t=${this.at()}&open=${this.open()}${S.away ? "&panels=0" : ""}`
      + `${S.tz === "LOCAL" ? "&tz=local" : ""}${S.playing ? "&play=1" : ""}`;
  },
};
let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}
// new code while the window is open: the page reloads at the same moment; new server code needs Start replay again
const Ver = {
  web: null,
  check(r) {
    if (!r) return;
    if (Ver.web === null) Ver.web = r.web;
    const b = $("#update");
    if (r.restart) { b.dataset.kind = "restart"; b.textContent = "domeware updated · close this window and press Start replay"; }
    else if (r.web !== Ver.web) { b.dataset.kind = "reload"; b.textContent = "New version · reload (R)"; }
    else return;
    b.hidden = false;
  },
};
function sendPing() {  // keeps the server running; reports the window size and playback speed (kept on this computer only)
  const q = SHOT ? "" : `?w=${innerWidth}&h=${innerHeight}&dpr=${devicePixelRatio.toFixed(2)}&glass=${S.glass ? 1 : 0}`
    + (FX.fps ? `&fps=${FX.fps.toFixed(1)}` : "");
  fetch(`/api/ping${q}`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then(Ver.check).catch(() => {});
}

// ---------- menu page (top left) ----------
const MARK = '<svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 18a9 9 0 0 1 18 0"/><path d="M7 18a5 5 0 0 1 10 0"/><circle cx="12" cy="18" r="1.4"/><path d="M12 18 18.5 7.5"/></svg>';
const Drawer = {
  isOpen: false,
  build() {
    const d = S.data, st = d.status;
    const sec = (n, title, ...kids) => h("section", { class: "dr-sec" }, h("h4", {}, h("em", {}, n), title, h("i")), ...kids);
    const row = (k, ...v) => h("div", { class: "dr-row" }, h("span", {}, k), h("b", {}, ...v));
    const led = (state) => h("i", { class: `led ${state}` });
    const ctl = (title, sub, control, key) => h("div", { class: "dr-ctl" },
      h("div", {}, h("b", {}, title, key ? " " : null, key ? h("kbd", {}, key) : null), h("small", {}, sub)), control);
    const sw = (on, fn) => { const s = h("button", { class: "switch", role: "switch", "aria-checked": String(on) }); s.addEventListener("click", fn); return s; };
    const link = (file, title, icon) => h("a", { class: "dr-act off", href: `/data/${file}`, "data-file": file, "aria-disabled": "true",
      target: file.endsWith(".html") ? "_blank" : null, rel: "noopener", download: file.endsWith(".kml") ? file : null },
    h("span", { html: `<svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg>` }), h("div", {}, h("b", {}, title), h("small", {}, "checking …")));
    this.closeBtn = h("button", { class: "x", "aria-label": "Close the menu", title: "Close (Esc)", html: X_SVG });
    this.closeBtn.addEventListener("click", () => this.close());
    this.actT = h("span", {});
    this.panelSw = sw(!S.away, () => Panels.away(!S.away));
    this.loopSw = sw(S.loop, () => setLoop(!S.loop));
    this.glassSw = sw(S.glass, () => { FX.manual = true; setGlass(!S.glass); });
    this.copyBtn = h("button", { class: "copybtn", onclick: () => View.copy() }, "Copy");
    this.tzSeg = seg([["UTC", "UTC"], ["LOCAL", "Local"]], S.tz, setTz);
    this.links = [
      link("report.html", "Session report", '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>'),
      link("session.kml", "Google Earth file", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
    ];
    const keys = [["Space", "play / pause"], ["← →", "5 s back / forward (Shift: 60 s)"], ["Home End", "start / end of the activity"],
      ["1 – 6", "open or close an instrument"], ["P", "hide or show the panels"], ["M", "this menu"], ["Esc", "close"],
      ["C", "copy this view"], ["R", "reload at this moment"], ["Wheel", "zoom the timeline"], ["Dbl-click", "fit the timeline to the activity"]];
    $("#drawer").append(
      h("div", { class: "dr-head" }, h("span", { html: MARK }), h("b", {}, "DOMEWARE"), h("span", {}, "MENU"), this.closeBtn),
      h("div", { class: "dr-body" },
        sec("01", "Session",
          row("Session", d.session),
          row("Site", d.site.name, Number.isFinite(d.site.lat) ? h("small", {}, `${d.site.lat.toFixed(5)}, ${d.site.lon.toFixed(5)}`) : null),
          row("Activity", this.actT),
          row("Data", st.simulated ? "SIMULATED" : "recorded in the field")),
        sec("02", "Data in this replay",
          row("Radar", led(st.radar_tracks ? "ok" : "wait"), st.radar_tracks ? `${st.radar_tracks} tracks` : "pending (third party)"),
          row("Drones", led(st.drones ? "ok" : st.drone_logs_encrypted ? "warn" : ""),
            st.drones ? `${st.drones} flight path${st.drones > 1 ? "s" : ""}` : st.drone_logs_encrypted ? "log encrypted: header only" : "none"),
          row("Weather", led(st.weather ? "ok" : ""), st.weather || "none"),
          row("Screens", led(st.screens ? "ok" : ""), `${st.screens} photos`),
          row("Equipment", led(d.equipment.length ? "ok" : ""), `${d.equipment.length} items`)),
        sec("03", "View",
          ctl("Panels", "the instruments over the map", this.panelSw, "P"),
          ctl("Time", "UTC for evidence, local for the field team", this.tzSeg),
          ctl("Loop", "start again at the end of the activity", this.loopSw),
          ctl("Glass panels", "the map shows through; turns off by itself if playback is slow", this.glassSw),
          ctl("This view", "this moment and these instruments as one line, to show someone exactly this", this.copyBtn, "C")),
        sec("04", "Open", ...this.links),
        sec("05", "Keys", h("div", { class: "dr-keys" }, ...keys.flatMap(([k, v]) => [h("kbd", {}, k), h("span", {}, v)])))),
      h("div", { class: "dr-foot" }, `Replay built ${String(d.generated_utc).slice(0, 16).replace("T", " ")} UTC · id ${String(d.recording_id).slice(0, 8)}`,
        h("br"), "Served from this computer only (127.0.0.1)"));
    $("#menuBtn").addEventListener("click", () => this.toggle());
    $("#scrim").addEventListener("click", () => this.close());
    this.times();
  },
  times() {
    const a = S.data.activity;
    if (this.actT) this.actT.textContent = a ? `${hm(a[0])} → ${hm(a[1])} ${S.tz === "UTC" ? "UTC" : "local"}` : "—";
  },
  async check() {  // offer only the files this session has
    for (const a of this.links) {
      let ok = false;
      try { ok = (await fetch(a.getAttribute("href"), { method: "HEAD", cache: "no-store" })).ok; } catch { /* server gone */ }
      const report = a.dataset.file.endsWith(".html");
      a.classList.toggle("off", !ok);
      a.setAttribute("aria-disabled", String(!ok));
      a.querySelector("small").textContent = ok ? (report ? "opens in a new window" : "download it, then open it in Google Earth")
        : `not made yet: run  dw ${report ? "report" : "kml"}`;
    }
  },
  open() {
    this.isOpen = true;
    const el = $("#drawer");
    el.classList.add("open");
    el.removeAttribute("inert");
    el.setAttribute("aria-hidden", "false");
    $("#scrim").classList.add("on");
    $("#menuBtn").setAttribute("aria-expanded", "true");
    this.times();
    this.check();
    requestAnimationFrame(() => { placeAll(); this.closeBtn.focus({ preventScroll: true }); });
  },
  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    const el = $("#drawer");
    el.classList.remove("open");
    el.setAttribute("inert", "");
    el.setAttribute("aria-hidden", "true");
    $("#scrim").classList.remove("on");
    $("#menuBtn").setAttribute("aria-expanded", "false");
    $("#menuBtn").focus({ preventScroll: true });
  },
  toggle() { if (this.isOpen) this.close(); else this.open(); },
};

// ---------- keys, resize, start ----------
function bindHelp() {
  const b = $("#helpBtn");
  if (!b) return;
  const html = '<b>Keys</b><div class="keys2"><kbd>Space</kbd><span>play / pause</span><kbd>← →</kbd><span>5 s back / forward</span>'
    + '<kbd>Shift</kbd><span>with ← → : 60 s</span><kbd>Home End</kbd><span>start / end of the activity</span>'
    + '<kbd>1 – 6</kbd><span>open or close an instrument</span><kbd>P</kbd><span>hide or show the panels</span><kbd>M</kbd><span>menu</span><kbd>C</kbd><span>copy this view</span>'
    + '<kbd>Wheel</kbd><span>zoom the timeline</span><kbd>Dbl-click</kbd><span>fit the activity</span></div>'
    + `<div class="prov">Data built ${esc(String(S.data.generated_utc).slice(0, 16).replace("T", " "))} UTC<br>Session ${esc(S.data.session)}`
    + ` · replay ${esc(String(S.data.recording_id).slice(0, 8))}</div>`;
  b.addEventListener("pointerenter", (e) => Tip.show(html, e.clientX - 260, e.clientY + 200));
  b.addEventListener("pointerleave", () => Tip.hide());
}
function bindKeys() {
  window.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, select, textarea")) return;
    const box = !$("#lightbox").hidden, plain = !e.ctrlKey && !e.altKey && !e.metaKey;
    const digit = /^(?:Digit|Numpad)([1-6])$/.exec(e.code);
    let used = true;
    if (e.code === "Space" && !box) setPlaying(!S.playing);
    else if (e.code === "ArrowLeft") box ? Lightbox.open(Lightbox.i - 1) : seek(S.t - (e.shiftKey ? 60000 : 5000));
    else if (e.code === "ArrowRight") box ? Lightbox.open(Lightbox.i + 1) : seek(S.t + (e.shiftKey ? 60000 : 5000));
    else if (e.code === "Home") seek(S.act[0]);
    else if (e.code === "End") seek(S.act[1]);
    else if (digit && plain && !box) Panels.toggle(INSTR[+digit[1] - 1].k);
    else if (e.code === "KeyP" && plain && !box) Panels.away(!S.away);
    else if (e.code === "KeyM" && plain && !box) Drawer.toggle();
    else if (e.code === "KeyC" && plain && !box) View.copy();
    else if (e.code === "KeyR" && plain && !box) View.reload();
    else if (e.code === "Escape") { if (!box && Drawer.isOpen) Drawer.close(); else Lightbox.close(); }
    else used = false;
    if (used) { e.preventDefault(); e.stopPropagation(); }
  }, true);
}
let resizeTimer = null;
function onResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { TL.render(); placeAll(); sendPing(); }, 120);  // the weather chart and the scope follow their own size
}
async function init() {
  const res = await fetch("/data/hud/data.json", { cache: "no-store" });
  if (!res.ok) { setLoad("no HUD data: run  dw replay  for this session", true); return; }
  S.data = await res.json();
  const d = S.data;
  S.act = d.activity || d.window || [Date.now() - 6e5, Date.now()];
  const gusts = ((d.weather && d.weather.gust) || []).filter((v) => v !== null);
  S.maxGust = gusts.length ? Math.max(...gusts) : null;
  for (const dr of d.drones) dr.ui = ink(dr.color);  // panel colour; the 3D keeps dr.color
  S.full = d.window || S.act.slice();
  S.view = padRange(S.act, 0.04);
  S.t = S.act[0];
  if (SHOT) document.body.classList.add("shot");
  const want = View.read();
  if (want.t !== undefined) { S.t = want.t; if (S.t < S.view[0] || S.t > S.view[1]) S.view = padRange([S.t - 3e5, S.t + 3e5], 0); }
  if (want.tz) S.tz = want.tz;
  $("#sessionName").textContent = d.session;
  $("#siteName").textContent = d.site.name;
  $("#stageSite").textContent = d.site.name.toUpperCase();
  const deg = (v, pos, neg) => `${Math.abs(v).toFixed(4)}°${v >= 0 ? pos : neg}`;
  if (Number.isFinite(d.site.lat) && Number.isFinite(d.site.lon)) $("#stageGeo").textContent = `${deg(d.site.lat, "N", "S")}  ${deg(d.site.lon, "E", "W")}`;
  $("#simTag").hidden = !d.status.simulated;
  document.title = `${d.session} · domeware HUD`;
  buildStatus();
  Clock.build();
  Panels.build();
  Over.build($("#p-over"));
  Drones.build($("#p-drones"));
  Radar.build($("#p-radar"));
  Log.build($("#p-log"));
  Atmo.build();
  Equip.build();
  Panels.meta("drones", d.drones.length ? `${d.drones.length} logged` : d.flights_header.length ? "path pending" : "none");
  Panels.meta("radar", d.radar ? `${d.radar.tracks} tracks` : "pending");
  Panels.meta("log", `${d.events.length} in all`);
  Panels.meta("atmo", d.weather ? `${d.weather.source} · 1 min` : "none");
  Panels.meta("equip", `${d.equipment.length} items`);
  TL.build();
  Lightbox.build();
  Drawer.build();
  Panels.restore(want);
  if (want.menu) Drawer.open();
  bindKeys();
  bindHelp();
  window.addEventListener("resize", onResize);
  refresh(true);
  window.__hud.R = { refresh, TL, Clock, Panels, Over, Drones, Radar, Log, Atmo, Equip, Drawer, View };  // for timing checks from the console
  requestAnimationFrame(frame);
  setTimeout(placeAll, 300);  // fonts settle, then the sliding pills find their places
  sendPing();
  setInterval(sendPing, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sendPing(); });
  $("#update").addEventListener("click", () => { if ($("#update").dataset.kind === "reload") View.reload(); });
  window.addEventListener("pagehide", () => { try { navigator.sendBeacon("/api/bye"); } catch { /* best effort */ } });
  startViewer().catch((err) => { console.error(err); setLoad(`the 3D viewer did not start: ${err && err.message ? err.message : err}`, true); });
}
init();
