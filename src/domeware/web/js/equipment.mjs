// domeware HUD · The equipment instrument: choice list, radar-centred scope and list.
import { $, KIND_COLOR, S, Tip, compass, dist, droneAt, esc, fx, h, niceCeil, seg, sv } from "./core.mjs";
import { frame } from "./app.mjs";
import { Panels } from "./panels.mjs";

// ---------- equipment ----------
export const ICON = {
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
export const GROUPS = [
  ["all", "All equipment", () => true],
  ["sensors", "Sensors", (k) => ["radar", "rf", "camera", "sensor"].includes(k)],
  ["weather", "Weather", (k) => k === "weather"],
  ["launch", "Take-off points", (k) => k === "drone_home"],
  ["crew", "Crew & operators", (k) => ["pilot", "operator"].includes(k)],
  ["effectors", "Effectors", (k) => k === "effector"],
  ["other", "Other", (k) => k === "other"],
];
export const Equip = {
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
