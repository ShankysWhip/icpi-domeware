// domeware HUD · The weather instrument: compass, numbers and the chart; line and area helpers.
import { $, S, Tip, at, compass, fx, h, hm, hms, niceStep, sv } from "./core.mjs";
import { seek } from "./app.mjs";
import { Panels } from "./panels.mjs";

// ---------- atmosphere ----------
export const WX = [
  ["wind", "Wind", "#0a8db6", "left"], ["gust", "Gust", "#d48a0e", "left"], ["dir", "Dir", "#5b7fa6", "band"],
  ["temp", "Temp", "#b13c8b", "right"], ["rh", "RH", "#138a52", "right"], ["pressure", "Press", "#6252cc", "right"],
];
export const WX_UNIT = { wind: "m/s", gust: "m/s", dir: "°", temp: "°C", rh: "%", pressure: "hPa" };
export function linePath(ts, vs, X, Y) {
  let d = "", pen = false;
  for (let i = 0; i < ts.length; i++) {
    const v = vs[i];
    if (v === null || v === undefined) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${X(ts[i]).toFixed(1)},${Y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}
export function areaPath(ts, vs, X, Y, y0) {
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
export function bandPath(ts, lo, hi, X, Y) {  // the area between two series, only where both have a value
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
export const Atmo = {
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
