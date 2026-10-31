// domeware HUD · The mission clock card, the instruments (overview, drones, radar, events) and the rail.
import { $, S, SHOT, at, bisect, clamp, compass, dist, droneAt, dur, fx, h, hms, inFlight, onPress, pad, parts, placeAll, seg, sv, zone } from "./core.mjs";
import { FX, frame, seek, setGlass, setTz } from "./app.mjs";
import { Atmo } from "./weather.mjs";
import { Equip } from "./equipment.mjs";
import { Drawer } from "./menu.mjs";

// ---------- mission clock card ----------
export const Clock = {
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
export const SPARK = { wind: ["#0a8db6", 2], gust: ["#d48a0e", 2], temp: ["#b13c8b", 1.5] };  // colour, smallest range drawn
export const Over = {
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
export const Drones = {
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
export const Radar = {
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
export const Log = {
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
export const INSTR = [
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
export const X_SVG = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>';
export const PREFS = "domeware.hud.view";
export const Panels = {
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
