/* Orbit: the timeline under the map. Lanes of lines, dots, bands and event ticks over a
   shared time axis. Click or drag to move the playhead, wheel to zoom, Shift+drag to pan,
   double-click to show everything. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const GUTTER = 74, AXIS = 20;
  const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400].map((s) => s * 1000);

  function Timeline(canvas, opts) {
    this.c = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts || {};
    this.lanes = [];
    this.t0 = NaN; this.t1 = NaN; this.v0 = NaN; this.v1 = NaN;
    this.t = NaN;
    this.hoverX = -1;
    this.tz = 0;
    this.shade = [];
    this.bind();
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement);
  }
  Timeline.prototype.setRange = function (t0, t1, keepView) {
    const had = U.isNum(this.v0);
    this.t0 = t0; this.t1 = t1;
    if (!keepView || !had) { this.v0 = t0; this.v1 = t1; }
    this.draw();
  };
  Timeline.prototype.setView = function (v0, v1) { this.v0 = v0; this.v1 = v1; this.draw(); };
  Timeline.prototype.setLanes = function (lanes) { this.lanes = lanes; this.draw(); };
  Timeline.prototype.setTime = function (t) { this.t = t; this.draw(); };
  Timeline.prototype.x = function (t) { return GUTTER + ((t - this.v0) / (this.v1 - this.v0)) * (this.w - GUTTER - 8); };
  Timeline.prototype.tAt = function (x) { return this.v0 + ((x - GUTTER) / (this.w - GUTTER - 8)) * (this.v1 - this.v0); };

  Timeline.prototype.bind = function () {
    const c = this.c;
    let drag = null;
    const pos = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener('pointerdown', (e) => {
      if (!U.isNum(this.v0)) return;
      const [x] = pos(e);
      c.setPointerCapture(e.pointerId);
      if (e.shiftKey || e.button === 1 || e.button === 2) drag = { mode: 'pan', x, v0: this.v0, v1: this.v1 };
      else { drag = { mode: 'seek' }; if (x >= GUTTER && this.opts.onSeek) this.opts.onSeek(U.clamp(this.tAt(x), this.t0, this.t1)); }
    });
    c.addEventListener('pointermove', (e) => {
      const [x] = pos(e);
      this.hoverX = x;
      if (drag && drag.mode === 'seek' && this.opts.onSeek) this.opts.onSeek(U.clamp(this.tAt(Math.max(GUTTER, x)), this.t0, this.t1));
      else if (drag && drag.mode === 'pan') {
        const dt = ((x - drag.x) / (this.w - GUTTER)) * (drag.v1 - drag.v0);
        this.panTo(drag.v0 - dt, drag.v1 - dt);
      } else this.draw();
    });
    const end = () => { drag = null; };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('pointerleave', () => { this.hoverX = -1; this.draw(); });
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('dblclick', () => { this.v0 = this.t0; this.v1 = this.t1; this.draw(); if (this.opts.onView) this.opts.onView(); });
    c.addEventListener('wheel', (e) => {
      if (!U.isNum(this.v0)) return;
      e.preventDefault();
      const [x] = pos(e);
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const d = (e.shiftKey ? e.deltaY : e.deltaX) / (this.w - GUTTER) * (this.v1 - this.v0);
        this.panTo(this.v0 + d, this.v1 + d);
        return;
      }
      const f = Math.exp(e.deltaY * 0.0022);
      const tm = this.tAt(Math.max(GUTTER, x));
      let span = U.clamp((this.v1 - this.v0) * f, 5000, Math.max(10000, (this.t1 - this.t0) * 1.02));
      const a = (tm - this.v0) / (this.v1 - this.v0);
      this.panTo(tm - a * span, tm - a * span + span);
      if (this.opts.onView) this.opts.onView();
    }, { passive: false });
  };
  Timeline.prototype.panTo = function (v0, v1) {
    const span = v1 - v0, lo = this.t0 - span * 0.05, hi = this.t1 + span * 0.05;
    if (v0 < lo) { v0 = lo; v1 = lo + span; }
    if (v1 > hi) { v1 = hi; v0 = hi - span; }
    this.v0 = v0; this.v1 = v1;
    this.draw();
  };

  Timeline.prototype.draw = function () {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
  };
  Timeline.prototype.render = function () {
    const c = this.c, dpr = window.devicePixelRatio || 1;
    const w = c.parentElement.clientWidth, h = c.parentElement.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    this.w = w; this.h = h;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const font = getComputedStyle(document.body).fontFamily;
    ctx.font = '11px ' + font;
    if (!U.isNum(this.v0) || this.v1 <= this.v0) {
      ctx.fillStyle = '#6c6e75';
      ctx.fillText('No data', GUTTER, h / 2);
      return;
    }
    const lanes = this.lanes.filter((l) => !l.hidden);
    const avail = h - AXIS - 4;
    const totalW = lanes.reduce((s, l) => s + (l.weight || 1), 0) || 1;
    let y = 2;
    // flight / airborne shading
    for (const s of this.shade) {
      const xa = Math.max(GUTTER, this.x(s.t0)), xb = Math.min(w - 8, this.x(s.t1));
      if (xb > xa) { ctx.fillStyle = s.color; ctx.fillRect(xa, 0, xb - xa, h - AXIS); }
    }
    this.laneBoxes = [];
    for (const lane of lanes) {
      const lh = Math.max(16, (avail * (lane.weight || 1)) / totalW);
      this.drawLane(ctx, lane, y, lh);
      this.laneBoxes.push({ lane, y, h: lh });
      y += lh;
    }
    this.drawAxis(ctx, h - AXIS);
    // playhead
    if (U.isNum(this.t)) {
      const x = this.x(this.t);
      if (x >= GUTTER - 1 && x <= w) {
        ctx.fillStyle = '#ff7a1a';
        ctx.fillRect(Math.round(x) - 1, 0, 2, h - AXIS + 4);
        ctx.beginPath(); ctx.moveTo(x - 5, 0); ctx.lineTo(x + 5, 0); ctx.lineTo(x, 6); ctx.fill();
      }
    }
    // hover read-out
    if (this.hoverX >= GUTTER && this.hoverX <= w) {
      const t = this.tAt(this.hoverX);
      ctx.strokeStyle = 'rgba(255,255,255,0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(this.hoverX) + 0.5, 0); ctx.lineTo(Math.round(this.hoverX) + 0.5, h - AXIS); ctx.stroke();
      const parts = [U.fmtClock(t, this.tz, true)];
      for (const b of this.laneBoxes) if (b.lane.readout) { const s = b.lane.readout(t); if (s) parts.push(s); }
      const text = parts.join('   ');
      const tw = ctx.measureText(text).width + 12;
      let bx = this.hoverX + 10;
      if (bx + tw > w - 4) bx = this.hoverX - tw - 10;
      ctx.fillStyle = 'rgba(38,39,43,0.97)';
      ctx.fillRect(bx, 4, tw, 18);
      ctx.fillStyle = '#ececee';
      ctx.fillText(text, bx + 6, 17);
    }
  };

  Timeline.prototype.drawLane = function (ctx, lane, y, lh) {
    const w = this.w;
    ctx.fillStyle = 'rgba(255,255,255,0.025)';
    ctx.fillRect(GUTTER, y, w - GUTTER - 8, lh - 2);
    ctx.fillStyle = '#a4a6ac';
    ctx.textBaseline = 'middle';
    ctx.fillText(lane.label, 8, y + Math.min(lh / 2, 12));
    ctx.textBaseline = 'alphabetic';
    const top = y + 3, bot = y + lh - 5;
    if (lane.kind === 'bands') return this.drawBands(ctx, lane, top, bot);
    if (lane.kind === 'ticks') return this.drawTicks(ctx, lane, top, bot);
    let lo = lane.min, hi = lane.max;
    if (!U.isNum(lo) || !U.isNum(hi)) {
      lo = Infinity; hi = -Infinity;
      for (const s of lane.series) for (let i = 0; i < s.v.length; i++) { const v = s.v[i]; if (U.isNum(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (U.isNum(lane.min)) lo = lane.min;
      if (hi - lo < (lane.minSpan || 1)) hi = lo + (lane.minSpan || 1);
      hi += (hi - lo) * 0.08;
    }
    const Y = (v) => bot - ((v - lo) / (hi - lo)) * (bot - top);
    ctx.fillStyle = '#6c6e75';
    ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
    ctx.fillText(fmtAxis(hi) + (lane.unit ? ' ' + lane.unit : ''), 8, top + 22);
    ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
    for (const g of lane.guides || []) {
      if (g.v < lo || g.v > hi) continue;
      ctx.strokeStyle = g.color || 'rgba(255,255,255,0.2)';
      ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(GUTTER, Y(g.v)); ctx.lineTo(w - 8, Y(g.v)); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(GUTTER, y, w - GUTTER - 8, lh); ctx.clip();
    for (const s of lane.series) {
      const ts = s.t, vs = s.v;
      if (!ts || !ts.length) continue;
      const i0 = Math.max(0, U.idxLE(ts, this.v0) - 1), i1 = Math.min(ts.length - 1, U.idxLE(ts, this.v1) + 1);
      ctx.strokeStyle = ctx.fillStyle = s.color;
      ctx.lineWidth = s.width || 1.5;
      if (s.kind === 'dots') {
        const r = s.r || 1.6;
        for (let i = i0; i <= i1; i++) {
          if (!U.isNum(vs[i])) continue;
          const x = this.x(ts[i]);
          ctx.fillStyle = s.colors ? s.colors[i] : s.color;
          ctx.fillRect(x - r, Y(vs[i]) - r, 2 * r, 2 * r);
        }
        continue;
      }
      ctx.beginPath();
      let pen = false, lastX = -1e9, gap = s.gap || 0;
      for (let i = i0; i <= i1; i++) {
        const v = vs[i];
        if (!U.isNum(v) || (gap && i > i0 && ts[i] - ts[i - 1] > gap)) { pen = false; if (!U.isNum(v)) continue; }
        const x = this.x(ts[i]);
        if (pen && x - lastX < 0.6 && i < i1) continue;
        if (pen) ctx.lineTo(x, Y(v)); else { ctx.moveTo(x, Y(v)); pen = true; }
        lastX = x;
      }
      ctx.stroke();
    }
    ctx.restore();
  };
  Timeline.prototype.drawBands = function (ctx, lane, top, bot) {
    const rows = lane.rows || [];
    const rh = Math.max(4, Math.min(12, (bot - top) / Math.max(1, rows.length)));
    rows.forEach((row, k) => {
      const y = top + k * rh + (bot - top - rows.length * rh) / 2;
      for (const s of row.segs) {
        const xa = Math.max(GUTTER, this.x(s.t0)), xb = Math.min(this.w - 8, this.x(s.t1));
        if (xb < GUTTER || xa > this.w) continue;
        ctx.fillStyle = s.color;
        ctx.fillRect(xa, y, Math.max(1, xb - xa), rh - 2);
      }
    });
  };
  Timeline.prototype.drawTicks = function (ctx, lane, top, bot) {
    for (const it of lane.items || []) {
      const x = this.x(it.t);
      if (x < GUTTER || x > this.w - 8) continue;
      ctx.fillStyle = it.color || '#a4a6ac';
      ctx.fillRect(Math.round(x), top + 2, 2, bot - top - 2);
    }
  };
  Timeline.prototype.drawAxis = function (ctx, y) {
    const span = this.v1 - this.v0, px = this.w - GUTTER - 8;
    const step = STEPS.find((s) => (s / span) * px >= 70) || STEPS[STEPS.length - 1];
    const off = this.tz * 60000;
    ctx.fillStyle = '#6c6e75';
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    let t = Math.ceil((this.v0 + off) / step) * step - off;
    for (; t <= this.v1; t += step) {
      const x = this.x(t);
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, y); ctx.stroke();
      const s = step < 60000 ? U.fmtClock(t, this.tz) : U.fmtClock(t, this.tz).slice(0, 5);
      ctx.fillText(s, x - ctx.measureText(s).width / 2, y + 14);
    }
  };
  function fmtAxis(v) {
    const a = Math.abs(v);
    return a >= 1000 ? (v / 1000).toFixed(1) + 'k' : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(0) : v.toFixed(1);
  }
  SF.Timeline = Timeline;
})(typeof window !== 'undefined' ? window : globalThis);
