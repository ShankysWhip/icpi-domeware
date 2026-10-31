// domeware HUD · The timeline dock and the large screenshot view.
import { $, S, Tip, at, clamp, dur, esc, h, hm, hms, niceStep, padRange, seg, sv } from "./core.mjs";
import { frame, seek, setLoop, setPlaying } from "./app.mjs";
import { areaPath, linePath } from "./weather.mjs";

// ---------- timeline ----------
export const LANES = [  // h: lane height; hs: on a smaller screen
  { key: "ruler", label: "TIME", h: 26, hs: 22 },
  { key: "activity", label: "ACTIVITY", h: 34, hs: 30 },
  { key: "drones", label: "DRONES", h: 0, hs: 0 },
  { key: "radar", label: "RADAR", h: 22, hs: 18 },
  { key: "events", label: "EVENTS", h: 20, hs: 18 },
  { key: "screens", label: "SCREENS", h: 42, hs: 38 },
  { key: "wind", label: "WIND", h: 34, hs: 26 },
];
export const laneH = (key) => LANES.find((l) => l.key === key).h;
export function evKind(text) {
  const t = text.toLowerCase();
  if (t.startsWith("note")) return "note";
  if (/take-?off|landing|landed|armed|disarm|failsafe|mode/.test(t)) return "drone";
  if (/ (start|end)$/.test(t)) return "run";
  if (/alarm|class|track|first/.test(t)) return "radar";
  return "";
}
export const PLAY = '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>';
export const PAUSE = '<svg viewBox="0 0 24 24"><path d="M6 4h4.5v16H6zM13.5 4H18v16h-4.5z"/></svg>';
export const TL = {
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
export const Lightbox = {
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
