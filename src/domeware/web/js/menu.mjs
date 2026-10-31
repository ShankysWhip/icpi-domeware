// domeware HUD · The menu page (top left).
import { $, S, at, h, hm, placeAll, seg } from "./core.mjs";
import { FX, Present, View, setGlass, setLoop, setTz } from "./app.mjs";
import { Drones, Panels, Radar, X_SVG } from "./panels.mjs";

// ---------- menu page (top left) ----------
export const MARK = '<img class="logo" src="/app/icpi.svg" alt="ICPI">';
export const Drawer = {
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
    this.presentSw = sw(false, () => Present.set(!Present.on));
    this.tzSeg = seg([["UTC", "UTC"], ["LOCAL", "Local"]], S.tz, setTz);
    this.links = [
      link("report.html", "Session report", '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>'),
      link("session.kml", "Google Earth file", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
    ];
    const keys = [["Space", "play / pause"], ["← →", "5 s back / forward (Shift: 60 s)"], ["Home End", "start / end of the activity"],
      ["1 – 6", "open or close an instrument"], ["P", "hide or show the panels"], ["M", "this menu"], ["Esc", "close"],
      ["C", "copy this view"], ["R", "reload at this moment"], ["F", "presentation: full screen, pointer hidden"], ["Wheel", "zoom the timeline"], ["Dbl-click", "fit the timeline to the activity"]];
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
          ctl("This view", "this moment and these instruments as one line, to show someone exactly this", this.copyBtn, "C"),
          ctl("Presentation", "full screen; the pointer hides when it rests: for filming or a big screen", this.presentSw, "F")),
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
