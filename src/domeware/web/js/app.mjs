// domeware HUD · The app: viewer bridge, one clock, status bar, views in the address, keys and start.
import { $, QUIET, S, SHOT, Tip, at, clamp, esc, h, ink, padRange, parts, placeAll, qs } from "./core.mjs";
import { Clock, Drones, INSTR, Log, Over, Panels, Radar } from "./panels.mjs";
import { Atmo } from "./weather.mjs";
import { Equip } from "./equipment.mjs";
import { Lightbox, TL } from "./timeline.mjs";
import { Drawer } from "./menu.mjs";

// ---------- viewer bridge ----------
export const V = { viewer: null, ready: false, rec: null, lastPush: null, lastPushAt: 0 };
window.__hud = { S, V };  // for checks from the browser console
export function setLoad(text, error = false) {
  $("#loadSub").textContent = text;
  $("#loading").classList.toggle("error", error);
}
export async function startViewer() {
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
export function waitForTimeline(tries) {
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
export function push(force = false) {
  if (!V.ready) return;
  const ms = Math.round(S.t);
  if (!force && ms === V.lastPush) return;
  try { V.viewer.set_current_time(V.rec, S.data.timeline, ms * 1e6); } catch { /* viewer busy */ }
  V.lastPush = ms;
  V.lastPushAt = performance.now();
}
export function onViewerTime(ns) {
  if (!ns || !V.ready || S.playing) return;
  if (performance.now() - V.lastPushAt < 400) return;  // our own time coming back
  const ms = ns / 1e6;
  if (Math.abs(ms - S.t) > 50) { S.t = ms; refresh(true); }
}
export function onSelection(items) {
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
export function seek(ms) {
  S.t = clamp(ms, S.full[0], S.full[1]);
  push(true);
  refresh(true);
}
export function setPlaying(on) {
  if (on && S.t >= S.act[1] - 200) S.t = S.act[0];
  S.playing = on;
  FX.ema = 16.7;
  FX.n = 0;
  TL.playIcon();
  Clock.led.classList.toggle("on", on);
  if (!on) push(true);
}
export let lastFrame = performance.now(), lastUi = 0;
export const FX = { ema: 16.7, n: 0, manual: false, fps: null };  // frame time while playing
export function frame(now) {
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
        if (S.glass && !FX.manual && !QUIET && FX.ema > 30) setGlass(false);
      }
    }
  }
  TL.playhead();
  if (now - lastUi > 80) { lastUi = now; refresh(false); }
  requestAnimationFrame(frame);
}
export function refresh(full) {  // only the open instruments are kept up to date
  Clock.update();
  if (Panels.is("over")) Over.update();
  if (Panels.is("drones")) Drones.update();
  if (Panels.is("radar")) Radar.update();
  if (Panels.is("log")) Log.update();
  if (Panels.is("atmo")) Atmo.update();
  if (Panels.is("equip")) Equip.updateLive();
  if (full) TL.playhead();
}

// ---------- status bar ----------
export function buildStatus() {
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

export function setTz(v) {
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
export function setGlass(on, save = true) {
  S.glass = on;
  document.body.classList.toggle("solid", !on);
  if (Drawer.glassSw) Drawer.glassSw.setAttribute("aria-checked", String(on));
  if (save) Panels.save();
}
export function setLoop(on) {
  S.loop = on;
  TL.loop.classList.toggle("on", on);
  if (Drawer.loopSw) Drawer.loopSw.setAttribute("aria-checked", String(on));
}


// ---------- a view in the address: ?t=13:45:10&open=over,radar&panels=0&menu=1&tz=local (dw hud --at/--open, dw shots) ----------
export function parseAt(v) {
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
export const View = {
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
export let toastTimer = null;
export function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}
// new code while the window is open: the page reloads at the same moment; new server code needs Start replay again
export const Ver = {
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
// presentation: full screen and the pointer hides when it rests (for filming, or a big screen in a meeting)
export const Present = {
  on: false,
  timer: null,
  set(on) {
    this.on = on;
    document.body.classList.toggle("present", on);
    if (on) {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {});
      Tip.hide();
      this.rest();
    } else {
      clearTimeout(this.timer);
      document.body.classList.remove("idle");
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    }
    if (Drawer.presentSw) Drawer.presentSw.setAttribute("aria-checked", String(on));
  },
  rest() {
    document.body.classList.remove("idle");
    clearTimeout(this.timer);
    if (this.on) this.timer = setTimeout(() => document.body.classList.add("idle"), 2000);
  },
};
document.addEventListener("pointermove", () => { if (Present.on) Present.rest(); });
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && Present.on) Present.set(false); });

export function sendPing() {  // keeps the server running; reports the window size and playback speed (kept on this computer only)
  const q = QUIET ? "" : `?w=${innerWidth}&h=${innerHeight}&dpr=${devicePixelRatio.toFixed(2)}&glass=${S.glass ? 1 : 0}`
    + (FX.fps ? `&fps=${FX.fps.toFixed(1)}` : "");
  fetch(`/api/ping${q}`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then(Ver.check).catch(() => {});
}

// ---------- keys, resize, start ----------
export function bindHelp() {
  const b = $("#helpBtn");
  if (!b) return;
  const html = '<b>Keys</b><div class="keys2"><kbd>Space</kbd><span>play / pause</span><kbd>← →</kbd><span>5 s back / forward</span>'
    + '<kbd>Shift</kbd><span>with ← → : 60 s</span><kbd>Home End</kbd><span>start / end of the activity</span>'
    + '<kbd>1 – 6</kbd><span>open or close an instrument</span><kbd>P</kbd><span>hide or show the panels</span><kbd>M</kbd><span>menu</span><kbd>C</kbd><span>copy this view</span><kbd>F</kbd><span>presentation (full screen)</span>'
    + '<kbd>Wheel</kbd><span>zoom the timeline</span><kbd>Dbl-click</kbd><span>fit the activity</span></div>'
    + `<div class="prov">Data built ${esc(String(S.data.generated_utc).slice(0, 16).replace("T", " "))} UTC<br>Session ${esc(S.data.session)}`
    + ` · replay ${esc(String(S.data.recording_id).slice(0, 8))}</div>`;
  b.addEventListener("pointerenter", (e) => Tip.show(html, e.clientX - 260, e.clientY + 200));
  b.addEventListener("pointerleave", () => Tip.hide());
}
export function bindKeys() {
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
    else if (e.code === "KeyF" && plain && !box) Present.set(!Present.on);
    else if (e.code === "Escape") { if (!box && Drawer.isOpen) Drawer.close(); else Lightbox.close(); }
    else used = false;
    if (used) { e.preventDefault(); e.stopPropagation(); }
  }, true);
}
export let resizeTimer = null;
export function onResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { TL.render(); placeAll(); sendPing(); }, 120);  // the weather chart and the scope follow their own size
}
export async function init() {
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
  window.__hud.R = { refresh, TL, Clock, Panels, Over, Drones, Radar, Log, Atmo, Equip, Drawer, View, setGlass, setPlaying, Present };  // for timing checks from the console
  requestAnimationFrame(frame);
  setTimeout(placeAll, 300);  // fonts settle, then the sliding pills find their places
  sendPing();
  setInterval(sendPing, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sendPing(); });
  $("#update").addEventListener("click", () => { if ($("#update").dataset.kind === "reload") View.reload(); });
  window.addEventListener("pagehide", () => { try { navigator.sendBeacon("/api/bye"); } catch { /* best effort */ } });
  startViewer().catch((err) => { console.error(err); setLoad(`the 3D viewer did not start: ${err && err.message ? err.message : err}`, true); });
}
