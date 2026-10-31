// domeware HUD · Shared helpers: elements, formatting, the state, small widgets. No other module is imported here.

export const qs = new URLSearchParams(location.search);
export const SHOT = qs.get("shot") === "1";  // dw shots: a picture is being taken (no motion, nothing saved or reported)
export const QUIET = SHOT || qs.get("bench") === "1";  // shots and the speed bench: nothing saved, reported or switched by itself
export const $ = (sel, root = document) => root.querySelector(sel);
export const SVGNS = "http://www.w3.org/2000/svg";

export function h(tag, attrs = {}, ...kids) {
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
export function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

// ---------- formatting ----------
export const pad = (n, w = 2) => String(Math.floor(Math.abs(n))).padStart(w, "0");
export const fmts = {};
export function parts(ms, tz, extra = {}) {
  const key = tz + JSON.stringify(extra);
  const f = (fmts[key] ||= new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month: "short", day: "2-digit",
    weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", ...extra }));
  const o = {};
  for (const p of f.formatToParts(new Date(ms))) o[p.type] = p.value;
  return o;
}
export const zone = () => (S.tz === "UTC" ? "UTC" : S.data.tz_local);
export const hms = (ms) => { const p = parts(ms, zone()); return `${p.hour}:${p.minute}:${p.second}`; };
export const hm = (ms) => { const p = parts(ms, zone()); return `${p.hour}:${p.minute}`; };
export function dur(ms, signed = true) {
  const sign = ms < 0 ? "−" : "+";
  const s = Math.floor(Math.abs(ms) / 1000);
  return `${signed ? sign : ""}${pad(s / 3600)}:${pad((s % 3600) / 60)}:${pad(s % 60)}`;
}
export const fx = (v, d = 1) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(d));
export const dist = (m) => (m === null || m === undefined || !Number.isFinite(m) ? "—" : m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(2)} km`);
export const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// Drone colours are picked for the dark 3D scene; on the white panels a light one (yellow, white) needs more ink.
export function ink(hex) {
  const n = parseInt(String(hex).slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  if (lum <= 0.52) return hex;
  const k = 0.52 / lum;
  return "#" + [r, g, b].map((c) => Math.round(c * k).toString(16).padStart(2, "0")).join("");
}
// a calmer, opaque version of a colour (mixed with white): for long stripes that should not shout
export function soften(hex, k = 0.5) {
  const n = parseInt(String(hex).slice(1), 16);
  return "#" + [n >> 16, (n >> 8) & 255, n & 255].map((c) => Math.round(c + (255 - c) * k).toString(16).padStart(2, "0")).join("");
}
export const KIND_COLOR = { radar: "#d9620b", operator: "#b7700b", drone_home: "#475569", pilot: "#a3540a", weather: "#0a8db6",
  rf: "#6252cc", camera: "#0f766e", sensor: "#4f46e5", effector: "#cf3a57", other: "#64748b" };
export const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export function bisect(ts, t) {
  if (!ts || !ts.length || t < ts[0]) return -1;
  let lo = 0, hi = ts.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= t) lo = m; else hi = m - 1; }
  return lo;
}
export function at(ts, vs, t, maxAge = 120000) {
  if (!vs) return null;
  const i = bisect(ts, t);
  if (i < 0 || t - ts[i] > maxAge) return null;
  return vs[i];
}
export function droneAt(d, key, t) {  // drone series are one value per second from series.t[0]
  const v = d.series[key];
  if (!v) return null;
  const i = Math.round((t - d.series.t[0]) / 1000);
  return i >= 0 && i < v.length ? v[i] : null;
}
export const inFlight = (d, t) => d.flying.some(([a, b]) => t >= a - 500 && t <= b + 500);  // as dw compare counts flight
export const STEPS = [1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5];
export const niceStep = (raw) => STEPS.find((s) => s >= raw) || STEPS[STEPS.length - 1];
export const RINGS = [20, 40, 60, 80, 100, 120, 160, 200, 300, 400, 600, 800, 1000, 1200, 1600, 2000, 2400, 3000, 4000, 6000, 8000, 12000];
export const niceCeil = (v) => RINGS.find((r) => r >= v) || RINGS[RINGS.length - 1];

// ---------- state ----------
export const S = {
  data: null, t: 0, playing: false, speed: 1, loop: true, tz: "UTC", open: new Set(["over", "atmo"]), away: false, glass: true,
  wx: { wind: true, gust: true, dir: true, temp: true, rh: false, pressure: false },
  filter: "all", sel: null, scope: "site", drone: null, full: [0, 1], act: [0, 1], view: [0, 1],
};
export function padRange([a, b], f) {
  const w = (b - a) * f;
  return [Math.max(S.full[0], a - w), Math.min(S.full[1], b + w)];
}

// ---------- small widgets ----------
export function seg(options, value, onChange, cls = "") {
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
export function placeAll() { for (const s of document.querySelectorAll(".seg")) if (s.place) s.place(); }
export const Tip = {
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


export function onPress(el, fn) {  // act on press, not on release: it feels instant (Enter or Space on a focused button still works)
  el.addEventListener("pointerdown", (e) => { if (e.button === 0) fn(); });
  el.addEventListener("click", (e) => { if (e.detail === 0) fn(); });
}
