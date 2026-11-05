/* Orbit: the A4 test report. Blocks of HTML (text, tables, SVG charts, the map image) are
   flowed into fixed 210 × 297 mm pages inside a hidden frame, so page breaks, page numbers
   and table headers come out the same in WebKit (Mac) and Chromium (Windows), whether the
   result is printed to PDF or opened as a web page. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util, A = SF.analysis;
  const RP = SF.report = {};
  const esc = U.esc;
  const C = { ink: '#14181d', muted: '#5f6b76', faint: '#9aa5af', line: '#dfe4e9', accent: '#ff7a1a', drone: '#ff7a1a', label: '#e0399f', track: '#1f9ad1', miss: '#e5484d', grid: '#edf0f3' };
  const PAGE_W = 794, PAGE_H = 1123, PAD_X = 57, HEAD = 68, FOOT = 54;
  const CONTENT_W = PAGE_W - 2 * PAD_X;

  // ------------------------------------------------------------------ small SVG chart kit
  function niceStep(span, target) {
    const raw = span / Math.max(1, target), p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
  }
  function timeTicks(t0, t1, n, tz) {
    const steps = [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600].map((s) => s * 1000);
    const st = steps.find((s) => (t1 - t0) / s <= n) || 3600e3;
    const out = [], off = tz * 60000;
    for (let t = Math.ceil((t0 + off) / st) * st - off; t <= t1; t += st) out.push({ t, label: U.fmtClock(t, tz).slice(0, st < 60000 ? 8 : 5) });
    return out;
  }
  function frame(w, h, m) { return { w, h, x0: m.l, x1: w - m.r, y0: m.t, y1: h - m.b }; }
  const svgOpen = (w, h) => '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">';
  const txt = (x, y, s, o) => '<text x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" font-size="' + ((o && o.size) || 10) + '" fill="' + ((o && o.fill) || C.muted) + '" text-anchor="' + ((o && o.anchor) || 'start') + '"' + (o && o.weight ? ' font-weight="' + o.weight + '"' : '') + '>' + esc(s) + '</text>';
  function polyline(pts, color, width, dash) {
    if (pts.length < 2) return '';
    return '<polyline fill="none" stroke="' + color + '" stroke-width="' + width + '" stroke-linejoin="round" stroke-linecap="round"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + ' points="' + pts.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ') + '"/>';
  }
  function yAxis(f, lo, hi, unit, n) {
    let out = '';
    const st = niceStep(hi - lo, n || 4);
    for (let v = Math.ceil(lo / st) * st; v <= hi + 1e-9; v += st) {
      const y = f.y1 - ((v - lo) / (hi - lo)) * (f.y1 - f.y0);
      out += '<line x1="' + f.x0 + '" x2="' + f.x1 + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '" stroke="' + C.grid + '"/>';
      out += txt(f.x0 - 6, y + 3.5, (Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(+v.toFixed(2))) , { anchor: 'end', size: 9 });
    }
    if (unit) out += txt(f.x0 - 6, f.y0 - 7, unit, { anchor: 'end', size: 9, fill: C.faint });
    return out;
  }
  function xTimeAxis(f, t0, t1, tz) {
    let out = '<line x1="' + f.x0 + '" x2="' + f.x1 + '" y1="' + f.y1 + '" y2="' + f.y1 + '" stroke="' + C.line + '"/>';
    for (const tk of timeTicks(t0, t1, 8, tz)) {
      const x = f.x0 + ((tk.t - t0) / (t1 - t0)) * (f.x1 - f.x0);
      out += '<line x1="' + x.toFixed(1) + '" x2="' + x.toFixed(1) + '" y1="' + f.y1 + '" y2="' + (f.y1 + 4) + '" stroke="' + C.faint + '"/>' + txt(x, f.y1 + 15, tk.label, { anchor: 'middle', size: 9 });
    }
    return out;
  }
  // radar status over the flight as a coloured strip
  RP.statusStrip = function (an, w, tz) {
    const se = an.series, h = 54, f = frame(w, h, { l: 8, r: 8, t: 8, b: 22 });
    const t0 = se.t[0], t1 = se.t[se.t.length - 1] + se.step;
    let out = svgOpen(w, h) + '<rect x="' + f.x0 + '" y="' + f.y0 + '" width="' + (f.x1 - f.x0) + '" height="' + (f.y1 - f.y0) + '" fill="' + C.grid + '"/>';
    let k = 0;
    while (k < se.t.length) {
      const st = se.cls[k] ? 2 : se.det[k] ? 1 : 0;
      let j = k;
      while (j + 1 < se.t.length && (se.cls[j + 1] ? 2 : se.det[j + 1] ? 1 : 0) === st) j++;
      const xa = f.x0 + ((se.t[k] - t0) / (t1 - t0)) * (f.x1 - f.x0), xb = f.x0 + ((se.t[j] + se.step - t0) / (t1 - t0)) * (f.x1 - f.x0);
      out += '<rect x="' + xa.toFixed(2) + '" y="' + f.y0 + '" width="' + Math.max(0.6, xb - xa).toFixed(2) + '" height="' + (f.y1 - f.y0) + '" fill="' + (st === 2 ? C.label : st === 1 ? C.track : C.miss) + '"/>';
      k = j + 1;
    }
    return out + xTimeAxis(f, t0, t1, tz) + '</svg>';
  };
  // height and range from the radar over time
  RP.profile = function (drone, an, sensor, ground, radar, w, tz) {
    const d = drone.data, win = an.window, h = 230;
    const t0 = win.t0 - 20000, t1 = win.t1 + 20000;
    const f1 = frame(w, 120, { l: 44, r: 10, t: 16, b: 6 }), f2 = { w, h, x0: 44, x1: w - 10, y0: 140, y1: h - 22 };
    const X = (f, t) => f.x0 + ((t - t0) / (t1 - t0)) * (f.x1 - f.x0);
    let maxH = 10;
    const hp = [], rp = [];
    const step = Math.max(1, Math.floor(d.t.length / 900));
    for (let i = 0; i < d.t.length; i += step) {
      if (d.t[i] < t0 || d.t[i] > t1) continue;
      maxH = Math.max(maxH, d.h[i]);
      hp.push([d.t[i], d.h[i]]);
      if (sensor) rp.push([d.t[i], U.dist(sensor.lat, sensor.lon, d.lat[i], d.lon[i])]);
    }
    const hiH = Math.ceil((maxH * 1.12) / 10) * 10;
    const Y1 = (v) => f1.y1 - (v / hiH) * (f1.y1 - f1.y0);
    let out = svgOpen(w, h) + txt(f1.x0, 10, 'Height above takeoff', { size: 9.5, fill: C.ink, weight: 600 }) + yAxis(f1, 0, hiH, '', 3);
    if (radar && U.isNum(ground)) {
      for (const hit of an.hits) {
        const tr = radar.trackMap.get(hit.id);
        const v = tr.alt[hit.i] - ground;
        if (hit.t < t0 || hit.t > t1 || !U.isNum(v)) continue;
        out += '<circle cx="' + X(f1, hit.t).toFixed(1) + '" cy="' + Y1(U.clamp(v, 0, hiH)).toFixed(1) + '" r="1.3" fill="' + (A.isDroneLabel(hit.cls, an.opts.labels) ? C.label : C.track) + '" fill-opacity=".75"/>';
      }
    }
    out += polyline(hp.map(([t, v]) => [X(f1, t), Y1(Math.max(0, v))]), C.drone, 1.6);
    if (rp.length) {
      const hiR = Math.ceil((Math.max(...rp.map((p) => p[1])) * 1.1) / 100) * 100 || 100;
      const Y2 = (v) => f2.y1 - (v / hiR) * (f2.y1 - f2.y0);
      out += txt(f2.x0, f2.y0 - 8, 'Distance from the radar', { size: 9.5, fill: C.ink, weight: 600 }) + yAxis(f2, 0, hiR, '', 3);
      out += polyline(rp.map(([t, v]) => [X(f2, t), Y2(v)]), '#c25d10', 1.4);
    }
    return out + xTimeAxis(f2, t0, t1, tz) + '</svg>';
  };
  // detection and classification by distance band
  RP.rangeBars = function (an, w) {
    const bins = an.rangeBins || [], h = 190, f = frame(w, h, { l: 40, r: 10, t: 14, b: 34 });
    let out = svgOpen(w, h) + yAxis(f, 0, 100, '%', 4);
    const bw = (f.x1 - f.x0) / Math.max(1, bins.length);
    bins.forEach((b, k) => {
      const x = f.x0 + k * bw, gw = Math.min(26, bw * 0.34);
      const hd = (b.det / b.n) * (f.y1 - f.y0), hc = (b.cls / b.n) * (f.y1 - f.y0);
      out += '<rect x="' + (x + bw / 2 - gw - 1).toFixed(1) + '" y="' + (f.y1 - hd).toFixed(1) + '" width="' + gw.toFixed(1) + '" height="' + hd.toFixed(1) + '" fill="' + C.track + '"/>';
      out += '<rect x="' + (x + bw / 2 + 1).toFixed(1) + '" y="' + (f.y1 - hc).toFixed(1) + '" width="' + gw.toFixed(1) + '" height="' + hc.toFixed(1) + '" fill="' + C.label + '"/>';
      out += txt(x + bw / 2, f.y1 + 13, (b.lo >= 1000 ? (b.lo / 1000).toFixed(b.lo % 1000 ? 2 : 0) : b.lo) + '–' + (b.hi >= 1000 ? (b.hi / 1000).toFixed(b.hi % 1000 ? 2 : 0) + ' km' : b.hi + ' m'), { anchor: 'middle', size: 8.5 });
      out += txt(x + bw / 2, f.y1 + 24, U.fmtDur(b.n, true), { anchor: 'middle', size: 8, fill: C.faint });
    });
    return out + '</svg>';
  };
  // horizontal miss distance over time with CEP50/CEP95 lines
  RP.errorTime = function (an, w, tz) {
    const hits = an.hits, h = 170, f = frame(w, h, { l: 40, r: 10, t: 14, b: 22 });
    const t0 = an.window.t0 - 20000, t1 = an.window.t1 + 20000;
    const hi = Math.max(10, Math.ceil(((an.acc && an.acc.cep95) || 50) * 1.6 / 10) * 10);
    const X = (t) => f.x0 + ((t - t0) / (t1 - t0)) * (f.x1 - f.x0), Y = (v) => f.y1 - (Math.min(v, hi) / hi) * (f.y1 - f.y0);
    let out = svgOpen(w, h) + yAxis(f, 0, hi, 'm', 4);
    for (const x of hits) out += '<circle cx="' + X(x.t).toFixed(1) + '" cy="' + Y(x.eH).toFixed(1) + '" r="1.4" fill="' + (A.isDroneLabel(x.cls, an.opts.labels) ? C.label : C.track) + '" fill-opacity=".7"/>';
    if (an.acc) for (const [v, name] of [[an.acc.cep50, 'CEP50'], [an.acc.cep95, 'CEP95']]) {
      if (!U.isNum(v)) continue;
      out += '<line x1="' + f.x0 + '" x2="' + f.x1 + '" y1="' + Y(v).toFixed(1) + '" y2="' + Y(v).toFixed(1) + '" stroke="' + C.ink + '" stroke-dasharray="4 3" stroke-width=".8"/>' + txt(f.x1 - 2, Y(v) - 3, name + ' ' + v.toFixed(0) + ' m', { anchor: 'end', size: 8.5, fill: C.ink });
    }
    return out + xTimeAxis(f, t0, t1, tz) + '</svg>';
  };
  // where the radar put the drone, relative to its true position (north up), with CEP circles
  RP.missScatter = function (an, size) {
    const hits = an.hits, a = an.acc || {}, m = 30, c = size / 2;
    const lim = Math.max(10, Math.ceil(((a.cep95 || 40) * 1.35) / 10) * 10);
    const S = (v) => (v / lim) * (c - m);
    let out = svgOpen(size, size);
    for (const r of [lim / 2, lim]) out += '<circle cx="' + c + '" cy="' + c + '" r="' + S(r).toFixed(1) + '" fill="none" stroke="' + C.grid + '"/>' + txt(c + S(r) * 0.71 + 2, c - S(r) * 0.71 - 2, r + ' m', { size: 8, fill: C.faint });
    out += '<line x1="' + m + '" x2="' + (size - m) + '" y1="' + c + '" y2="' + c + '" stroke="' + C.line + '"/><line x1="' + c + '" x2="' + c + '" y1="' + m + '" y2="' + (size - m) + '" stroke="' + C.line + '"/>';
    out += txt(c, m - 8, 'N', { anchor: 'middle', size: 9, fill: C.ink, weight: 700 }) + txt(size - m + 9, c + 3, 'E', { size: 9, fill: C.ink, weight: 700 });
    for (const x of hits) {
      if (Math.abs(x.east) > lim || Math.abs(x.north) > lim) continue;
      out += '<circle cx="' + (c + S(x.east)).toFixed(1) + '" cy="' + (c - S(x.north)).toFixed(1) + '" r="1.5" fill="' + (A.isDroneLabel(x.cls, an.opts.labels) ? C.label : C.track) + '" fill-opacity=".55"/>';
    }
    if (U.isNum(a.cep50)) out += '<circle cx="' + c + '" cy="' + c + '" r="' + S(Math.min(a.cep50, lim)).toFixed(1) + '" fill="none" stroke="' + C.ink + '" stroke-width="1.2"/>';
    if (U.isNum(a.cep95)) out += '<circle cx="' + c + '" cy="' + c + '" r="' + S(Math.min(a.cep95, lim)).toFixed(1) + '" fill="none" stroke="' + C.ink + '" stroke-width="1" stroke-dasharray="4 3"/>';
    if (U.isNum(a.biasE)) {
      const bx = c + S(a.biasE), by = c - S(a.biasN);
      out += '<line x1="' + c + '" y1="' + c + '" x2="' + bx.toFixed(1) + '" y2="' + by.toFixed(1) + '" stroke="' + C.accent + '" stroke-width="2.2"/><circle cx="' + bx.toFixed(1) + '" cy="' + by.toFixed(1) + '" r="3.2" fill="' + C.accent + '"/>';
    }
    out += '<circle cx="' + c + '" cy="' + c + '" r="2.6" fill="' + C.ink + '"/>';
    return out + '</svg>';
  };

  // ------------------------------------------------------------------ blocks
  const kv = (rows) => '<table class="kv">' + rows.filter((r) => r && r[1] !== undefined && r[1] !== null && r[1] !== '').map((r) => '<tr><th>' + esc(r[0]) + '</th><td>' + r[1] + '</td></tr>').join('') + '</table>';
  const pct = (x) => (U.isNum(x) ? (x * 100).toFixed(1) + ' %' : '–');
  const pm = (st, d, unit) => (st && st.n ? (st.mean >= 0 ? '+' : '−') + Math.abs(st.mean).toFixed(d) + ' ± ' + st.std.toFixed(d) + ' ' + unit : '–');
  function kpi(v, l, s, cls) { return '<div class="kpi ' + (cls || '') + '"><div class="v">' + v + '</div><div class="l">' + esc(l) + '</div>' + (s ? '<div class="s">' + esc(s) + '</div>' : '') + '</div>'; }
  const grade = (x) => (x >= 0.9 ? 'good' : x >= 0.6 ? 'mid' : 'poor');
  // a table that can be split across pages: {split: true, head, rows}
  const table = (head, rows, cls) => ({ split: true, cls: cls || '', head: '<tr>' + head.map((h) => '<th>' + esc(h) + '</th>').join('') + '</tr>', rows: rows.map((r) => '<tr>' + r.map((c) => '<td>' + c + '</td>').join('') + '</tr>') });

  RP.blocks = function (S, assets) {
    const { drone, radar, weather } = S;
    const an = S.analysis || {};
    const sensor = an.sensor;
    const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : sensor && U.isNum(sensor.alt) ? sensor.alt : NaN;
    const tz = S.tzOffset(), tzName = S.settings.tz === 'local' ? U.fmtOffset(tz) : 'UTC';
    const tm = (t) => (U.isNum(t) ? U.fmtClock(t, tz) : '–');
    const meta = S.meta || {};
    const d0 = drone ? drone.data.t[0] : radar ? radar.t0 : Date.now();
    // several drones: the detail pages are about the selected one, the fleet table and CWA about all
    const fleet = S.fleetAn && S.fleet.length > 1, who = fleet ? ' · ' + esc(drone.short) : '';
    const B = [];
    const cover = [];
    cover.push('<div class="kicker">C-UAS flight test report</div><h1>' + esc(meta.title || S.title()) + '</h1>');
    cover.push('<div class="sub">' + esc([meta.site, U.fmtDate(d0, tz), an.window ? tm(an.window.t0) + '–' + tm(an.window.t1) + ' ' + tzName : ''].filter(Boolean).join(' · ')) + '</div>');
    B.push(cover.join(''));
    if (S.anySimulated()) B.push('<p class="warn">This report uses simulated demo data. It does not describe a real flight.</p>');
    B.push('<div class="cols2">' + kv([
      ['Test date', U.fmtDate(d0, tz)], ['Flight', an.window ? tm(an.window.t0) + ' – ' + tm(an.window.t1) + ' ' + tzName + ' (' + U.fmtDur((an.window.t1 - an.window.t0) / 1000) + ')' : ''],
      ['Site', esc(meta.site || '')], ['Organisation', esc(meta.org || '')], ['Prepared by', esc(meta.author || '')],
    ]) + kv([
      ['Drone', drone ? esc(drone.product || drone.name) : ''], ['Radar', radar ? esc((sensor && sensor.name) || radar.name) + (sensor && U.isNum(sensor.freqMHz) ? ' · ' + (sensor.freqMHz / 1000).toFixed(2) + ' GHz' : '') : ''],
      ['Weather station', weather ? esc(weather.name) : ''], ['Report created', esc(new Date().toISOString().slice(0, 16).replace('T', ' ')) + ' UTC'],
    ]) + '</div>');
    if (an.coverage) {
      B.push('<h2>Summary' + who + '</h2><p class="lead">' + esc(A.summaryText(drone, radar, an)) + '</p>' + (meta.notes ? '<p class="notes">' + esc(meta.notes).replace(/\n/g, '<br>') + '</p>' : ''));
      const a = an.acc || {};
      B.push('<div class="kpis">' +
        kpi(pct(an.coverage.detected), 'Probability of detection', 'share of airborne time tracked', grade(an.coverage.detected)) +
        kpi(pct(an.coverage.classified), 'Probability of classification', 'share labelled as a drone', grade(an.coverage.classified)) +
        kpi(an.firstDetect ? '+' + an.firstDetect.after.toFixed(0) + ' s' : '–', 'Time to detect', an.firstDetect && U.isNum(an.firstDetect.range) ? 'at ' + U.fmtDist(an.firstDetect.range) : '') +
        kpi(an.firstClass ? '+' + an.firstClass.after.toFixed(0) + ' s' : '–', 'Time to classify', an.firstClass && U.isNum(an.firstClass.range) ? 'at ' + U.fmtDist(an.firstClass.range) : '') +
        kpi(U.f(a.cep50, 0, 'm'), 'CEP50', 'median horizontal error') +
        kpi(U.f(a.cep95, 0, 'm'), 'CEP95', '95 % of positions within') +
        kpi(String(an.breaks.length), 'Track breaks', an.breaks.length ? 'longest ' + an.longestBreak.toFixed(0) + ' s' : 'none') +
        kpi(String(an.falseTracks.length), 'False drone tracks', 'other tracks labelled drone', an.falseTracks.length ? 'mid' : 'good') + '</div>');
      if (S.cwa) {
        const W = SF.cwa, cw = S.cwa;
        B.push('<p class="cwaline">CWA 18150 overall score <b class="g-' + W.grade(cw.overall) + '">' + cw.overall.toFixed(2) + '</b> of 1 · ' +
          W.FUNCTIONS.map(([k, n]) => n.toLowerCase() + ' ' + cw.fn[k].toFixed(2)).join(' · ') + (cw.isDefault ? ' <span class="muted">(Orbit\'s default scoring targets)</span>' : '') + '</p>');
      }
      if (fleet) {
        B.push('<h3>Drones in this scene</h3>');
        B.push(table(['Drone', 'Aircraft', 'Tracked', 'Drone label', 'Time to detect', 'CEP50', 'Track IDs'], S.fleet.map((d, k) => {
          const fa = S.fleetAn[k], fc = fa.coverage || {}, acc = fa.acc || {};
          return [(d === drone ? '<b>' : '') + esc(d.short) + (d === drone ? '</b> (details)' : ''), esc(d.product || d.name), pct(fc.detected), pct(fc.classified), fa.firstDetect ? '+' + fa.firstDetect.after.toFixed(0) + ' s' : '–', U.f(acc.cep50, 1, 'm'), String((fa.matchedIds || []).length)];
        })));
        B.push('<p class="small muted">The summary, the figures above and the detail pages are for ' + esc(drone.short) + '; the CWA 18150 evaluation covers all ' + S.fleet.length + ' drones. Each radar update counts only for the nearest drone.</p>');
      }
      B.push('<h3>Radar status during the flight' + who + '</h3>' + RP.statusStrip(an, CONTENT_W, tz) + '<div class="legend"><i style="background:' + C.label + '"></i>Labelled as a drone <i style="background:' + C.track + '"></i>Tracked, other label <i style="background:' + C.miss + '"></i>Not tracked</div>');
    } else if (drone || radar) {
      B.push('<h2>Summary</h2><p class="lead">' + (drone && !radar ? 'Only a drone log was analysed; add a radar file for detection results.' : 'Only radar data was analysed; add the drone\'s flight log to measure detection against the truth.') + '</p>');
    }
    if (assets && assets.map) {
      B.push({ pageBreak: true });
      B.push('<h2>Flight and radar tracks</h2><img class="map" src="' + assets.map + '" alt="Map">' +
        '<div class="legend">' + (an.coverage ? '<i style="background:' + C.label + '"></i>Drone path, labelled as a drone <i style="background:' + C.track + '"></i>tracked <i style="background:' + C.miss + '"></i>not tracked ' : '<i style="background:' + C.drone + '"></i>Drone path ') + '<span class="tri"></span>Radar with range rings</div>');
    }
    if (drone && an.window) B.push('<h3>Flight profile' + who + '</h3>' + RP.profile(drone, an, sensor, ground, radar, CONTENT_W, tz) + '<div class="legend"><i style="background:' + C.drone + '"></i>Drone log <i class="dotk" style="background:' + C.label + '"></i>Radar height, labelled drone <i class="dotk" style="background:' + C.track + '"></i>Radar height, other label</div>');
    if (an.coverage) {
      const a = an.acc || {};
      B.push({ pageBreak: true });
      B.push('<h2>Detection and accuracy' + who + '</h2>');
      if (an.rangeBins && an.rangeBins.length) B.push('<h3>Detection by distance from the radar</h3>' + RP.rangeBars(an, CONTENT_W) + '<div class="legend"><i style="background:' + C.track + '"></i>Tracked <i style="background:' + C.label + '"></i>Labelled as a drone · below each band: time the drone spent in it</div>');
      if (an.hits.length) {
        B.push('<h3>Position error over time</h3>' + RP.errorTime(an, CONTENT_W, tz));
        B.push('<div class="cols2 top"><div><h3>Where the radar put the drone</h3>' + RP.missScatter(an, 300) + '<div class="legend small">Each dot is one radar position relative to the logged drone position (centre), north up. Solid circle: CEP50; dashed: CEP95; orange: mean offset.</div></div><div><h3>Accuracy</h3>' +
          kv([['Matched radar updates', String(a.n)], ['CEP50', U.f(a.cep50, 1, 'm')], ['CEP95', U.f(a.cep95, 1, 'm')], ['RMSE', U.f(a.rmse, 1, 'm')], ['Mean offset', U.isNum(a.biasMag) ? U.f(a.biasMag, 1, 'm') + ' toward ' + a.biasDir.toFixed(0) + '°' : '–'],
            ['Range error', pm(a.range, 1, 'm')], ['Bearing error', pm(a.az, 2, '°')], ['Height error', pm(a.height, 1, 'm')], ['Update interval', U.isNum(an.updateInterval) ? an.updateInterval.toFixed(1) + ' s (median)' : '–'],
            ['Longest unbroken track', U.fmtDur(an.longestTrack)], ['Track IDs on the drone', an.matchedIds.length + ' (' + an.trackSwitches + ' changes)'], ['Label consistency', pct(an.labelShare)]]) + '</div></div>');
      }
      if (S.cwa) {
        const W = SF.cwa, cw = S.cwa, sc = (x) => (U.isNum(x) ? x.toFixed(2) : '–');
        B.push({ pageBreak: true });
        B.push('<h2>CWA 18150 performance evaluation</h2><p>The detection, tracking and identification metrics of CWA 18150:2024 (clause 13.4.7, Tables 65–67), measured against the drone\'s own log. As in clause 13.4.6, every metric is scored from 0 (worst) to 1 (best) against the scoring context in the table; the scores are averaged per function, weighted, and the three function scores are averaged into the overall score.</p>');
        B.push('<div class="kpis">' + kpi(sc(cw.overall), 'Overall score', 'mean of the three functions', W.grade(cw.overall)) +
          W.FUNCTIONS.map(([k, n]) => kpi(sc(cw.fn[k]), n, W.METRICS.filter((m) => m.fn === k).length + ' metrics, weighted mean', W.grade(cw.fn[k]))).join('') + '</div>');
        const rows = [];
        for (const [k, n] of W.FUNCTIONS) {
          W.METRICS.filter((m) => m.fn === k).forEach((m, i) => {
            const r = cw.metrics[m.id], g = 'g-' + W.grade(r.score);
            rows.push([i ? '' : '<b>' + esc(n) + '</b>', esc(m.name), esc(W.valueText(m, r)), esc(W.ctxText(m, r.target)), esc(W.ctxText(m, r.worst)), String(+r.weight.toFixed(2)),
              '<b class="' + g + '">' + r.score.toFixed(2) + '</b><span class="sbar"><i class="' + g + '" style="width:' + Math.round(r.score * 100) + '%"></i></span>']);
          });
        }
        B.push(table(['Function', 'Metric', 'Value', 'Target (1)', 'Worst (0)', 'Weight', 'Score'], rows, 'cwa'));
        B.push('<p class="small muted">' + esc(W.notesText(cw, radar && radar.name)) + '</p>');
        B.push('<div class="defs"><h3>What the metrics measure</h3>' + kv(W.METRICS.map((m) => [m.name, esc(m.def)])) + '</div>');
      }
      B.push({ pageBreak: true });
      B.push('<h2>Tables' + who + '</h2>');
      B.push('<h3>Track breaks</h3>');
      B.push(an.breaks.length ? table(['From', 'To', 'Length', 'Distance', 'Height', 'Note'], an.breaks.map((b) => [tm(b.t0), tm(b.t1), b.dur.toFixed(1) + ' s', U.fmtDist(b.range), U.f(b.h, 0, 'm'), esc(b.reason)])) : '<p class="muted">No breaks longer than ' + S.aopts.breakGap + ' s.</p>');
      const matched = [...an.trackInfo.values()].filter((x) => x.matched);
      B.push('<h3>Radar tracks on the drone</h3>');
      B.push(matched.length ? table(['Track', 'Label', 'From', 'To', 'Updates', 'Median error'], matched.map((x) => [esc(x.id), esc(x.cls), tm(x.first), tm(x.last), String(x.close), U.f(x.medErr, 1, 'm')])) : '<p class="muted">No radar track followed the drone within ' + S.aopts.gate + ' m.</p>');
      if (an.falseTracks.length) {
        B.push('<h3>Other tracks labelled as a drone</h3>');
        B.push(table(['Track', 'Label', 'From', 'To', 'Updates', 'Closest to drone'], an.falseTracks.map((x) => [esc(x.id), esc(x.cls), tm(x.first), tm(x.last), String(x.n), isFinite(x.minDist) ? U.fmtDist(x.minDist) : 'not in the air together'])));
      }
      if (an.rangeBins && an.rangeBins.length) {
        B.push('<h3>Detection by distance</h3>');
        B.push(table(['Distance', 'Time in band', 'Tracked', 'Labelled drone'], an.rangeBins.map((b) => [b.lo + '–' + b.hi + ' m', U.fmtDur(b.n), pct(b.det / b.n), pct(b.cls / b.n)])));
      }
    }
    if (weather && an.weather && an.weather.samples) {
      const ws = an.weather;
      const wn = (k) => U.stats(weather.data[k].filter((v, i) => U.isNum(v) && weather.data.t[i] >= an.window.t0 - 60000 && weather.data.t[i] <= an.window.t1 + 60000));
      const rh = wn('rh'), pr = wn('pres'), rain = wn('rainRate');
      B.push('<h3>Weather during the flight</h3>' + kv([['Station', esc(weather.name) + (weather.sensors ? ' (' + esc(weather.sensors) + ')' : '')], ['Wind', U.f(ws.wind.mean, 1, 'm/s') + ' mean, ' + U.f(ws.wind.max, 1, 'm/s') + ' max'], ['Gusts', 'up to ' + U.f(ws.gust.max, 1, 'm/s')],
        ['Wind from', U.isNum(ws.dir) ? ws.dir.toFixed(0) + '°' : '–'], ['Temperature', U.f(ws.temp.mean, 1, '°C')], ['Humidity', rh.n ? U.f(rh.mean, 0, '%') : ''], ['Pressure', pr.n ? U.f(pr.mean, 1, 'hPa') : ''], ['Rain', rain.n ? (rain.max > 0 ? 'up to ' + U.f(rain.max, 1, 'mm/h') : 'none') : '']]));
    }
    B.push({ pageBreak: true });
    B.push('<h2>Data and method</h2>');
    const checks = A.checks(drone, radar, weather, an, S.sync);
    if (checks.length) B.push('<h3>Data checks</h3><ul class="checks">' + checks.map((k) => '<li class="' + k.level + '">' + esc(k.text) + '</li>').join('') + '</ul>');
    B.push('<h3>Sources</h3>' + kv([
      ...(fleet ? S.fleet.map((d) => ['Drone ' + d.short, esc(d.file || '') + ' · ' + esc(SF.tracks.sourceText(d)) + ' · ' + d.data.t.length + ' positions']) :
        [['Drone log', drone ? esc(S.files.drone || '') + ' · ' + esc(SF.tracks.sourceText(drone)) + ' · ' + drone.data.t.length + ' positions' : '']]),
      ['Radar', radar ? esc(S.files.radar || '') + ' · ' + radar.tracks.length + ' tracks, ' + radar.updates + ' updates' + (sensor && sensor.version ? ' · software ' + esc(sensor.version) : '') : ''],
      ['Radar position', sensor ? sensor.lat.toFixed(6) + ', ' + sensor.lon.toFixed(6) + (U.isNum(sensor.alt) ? ' · antenna ' + sensor.alt.toFixed(1) + ' m, ground ' + U.f(sensor.ground, 1, 'm') + ' MSL' : '') : ''],
      ['Weather', weather ? esc(S.files.weather || '') + ' · ' + weather.data.t.length + ' records' : ''],
      ['Clock offsets applied', S.offsets.drone || S.offsets.radar || S.offsets.weather ? 'drone ' + esc(S.offsetText('drone')) + ', radar ' + esc(S.offsetText('radar')) + ', weather ' + esc(S.offsetText('weather')) : 'none'],
    ]));
    B.push('<h3>Method</h3><p>The drone\'s own flight log is the reference (ground truth). Each radar update is compared with the drone\'s logged position at the same moment, interpolated between log records. A radar track counts as the drone when at least half of its updates during the flight lie within ' + S.aopts.gate + ' m of the drone. At each second between takeoff and landing the drone counts as tracked when such an update lies within ±' + S.aopts.tol + ' s, and as classified when that track carries one of the labels ' + esc(S.aopts.labels.join(', ')) + '. A break is a gap of more than ' + S.aopts.breakGap + ' s between matching updates. Heights are compared above the radar site\'s ground level, which assumes level ground.' + (fleet ? ' With ' + S.fleet.length + ' drones in the air, a radar update counts only for the drone nearest to it.' : '') + '</p>');
    B.push('<h3>Definitions</h3>' + kv([
      ['Probability of detection', 'Share of airborne seconds with a matching radar update.'], ['Probability of classification', 'Share of airborne seconds where the matching track was labelled as a drone.'],
      ['Time to detect / classify', 'Seconds from takeoff to the first matching update / the first one labelled as a drone.'], ['CEP50, CEP95', 'Horizontal distance within which 50 % / 95 % of the matching radar positions fall.'],
      ['RMSE', 'Root mean square of the horizontal miss distance.'], ['Mean offset', 'Average radar position minus logged position; a large value suggests an alignment error.'],
      ['Range and bearing error', 'Error along and across the radar beam, as mean ± standard deviation.'], ['False drone track', 'A track labelled as a drone that never followed the test drone.'],
    ]));
    B.push('<p class="muted small">Map imagery © Esri, Maxar, Earthstar Geographics. Report made with Orbit ' + esc((SF.native.info && SF.native.info.version) || '') + (S.license ? ', licensed to ' + esc(SF.license.who(S.license)) + ' (license ' + esc(S.license.id) + ')' : '') + '.</p>');
    return B;
  };

  // ------------------------------------------------------------------ pagination
  const CSS = '@page{size:210mm 297mm;margin:0}*{box-sizing:border-box}html,body{margin:0;padding:0;background:#fff}' +
    'body{font:10.5px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:' + C.ink + ';-webkit-print-color-adjust:exact;print-color-adjust:exact}' +
    '.page{position:relative;width:' + PAGE_W + 'px;height:' + PAGE_H + 'px;overflow:hidden;page-break-after:always;break-after:page;background:#fff}' +
    '.page:last-child{page-break-after:auto;break-after:auto}' +
    '.hd{position:absolute;left:' + PAD_X + 'px;right:' + PAD_X + 'px;top:26px;display:flex;justify-content:space-between;align-items:center;font-size:9px;color:' + C.muted + ';border-bottom:2px solid ' + C.accent + ';padding-bottom:7px}' +
    '.hd b{color:' + C.ink + ';font-size:10px;letter-spacing:.3px}.hd .brand{display:flex;align-items:center;gap:7px}' +
    '.ft{position:absolute;left:' + PAD_X + 'px;right:' + PAD_X + 'px;bottom:24px;display:flex;justify-content:space-between;font-size:8.5px;color:' + C.faint + ';border-top:1px solid ' + C.line + ';padding-top:6px}' +
    '.body{position:absolute;left:' + PAD_X + 'px;right:' + PAD_X + 'px;top:' + HEAD + 'px;height:' + (PAGE_H - HEAD - FOOT) + 'px;overflow:hidden}' +
    '.kicker{color:' + C.accent + ';font-weight:700;text-transform:uppercase;letter-spacing:1px;font-size:9.5px;margin-top:10px}' +
    'h1{font-size:23px;line-height:1.2;margin:4px 0 4px;font-weight:700}.sub{color:' + C.muted + ';font-size:11px;margin-bottom:14px}' +
    'h2{font-size:14px;margin:16px 0 8px;padding-bottom:4px;border-bottom:1px solid ' + C.line + '}h3{font-size:11px;margin:12px 0 6px;color:' + C.ink + '}' +
    'p{margin:0 0 8px}.lead{font-size:11.5px;line-height:1.55}.notes{color:' + C.muted + ';border-left:3px solid ' + C.line + ';padding-left:8px}.muted{color:' + C.muted + '}.small{font-size:9px}' +
    '.warn{background:#fff4d6;border:1px solid #f0c24b;padding:6px 9px;border-radius:6px}' +
    '.cols2{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}' +
    'table{border-collapse:collapse;width:100%}td{vertical-align:top}table.kv th{text-align:left;color:' + C.muted + ';font-weight:500;padding:2.5px 10px 2.5px 0;vertical-align:top;white-space:nowrap;width:1%}table.kv td{padding:2.5px 0}' +
    'table.grid th{text-align:left;color:' + C.muted + ';font-weight:600;border-bottom:1.5px solid ' + C.ink + ';padding:4px 6px 4px 0;font-size:9.5px}table.grid td{border-bottom:1px solid ' + C.line + ';padding:3.5px 6px 3.5px 0;font-variant-numeric:tabular-nums}' +
    // results as a ruled grid (no boxes): the numbers carry the page
    '.kpis{display:grid;grid-template-columns:repeat(4,1fr);margin:6px 0 10px;border-top:1.5px solid ' + C.ink + ';border-bottom:1px solid ' + C.line + '}' +
    '.kpi{padding:8px 10px 8px 10px;border-left:1px solid ' + C.line + '}.kpi:nth-child(4n+1){border-left:0;padding-left:0}.kpi:nth-child(n+5){border-top:1px solid ' + C.line + '}' +
    '.kpi .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.3px}.kpi .l{font-size:9.5px;font-weight:600;margin-top:1px}.kpi .s{font-size:8.5px;color:' + C.muted + '}' +
    '.legend{font-size:9px;color:' + C.muted + ';margin:3px 0 8px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px}.legend i{display:inline-block;width:14px;height:5px;border-radius:2px;margin-left:6px}.legend i:first-child{margin-left:0}.legend i.dotk{width:6px;height:6px;border-radius:50%}' +
    '.legend .tri{display:inline-block;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:9px solid ' + C.label + ';margin-left:8px}.legend.small{display:block}' +
    'img.map{width:100%;border-radius:6px;display:block;max-height:560px;object-fit:cover}' +
    'ul.checks{list-style:none;margin:0;padding:0}ul.checks li{position:relative;padding:3px 0 3px 18px}ul.checks li:before{position:absolute;left:0;top:3px;width:12px;height:12px;border-radius:50%;font:700 8px/12px sans-serif;text-align:center;color:#fff}' +
    'ul.checks li.ok:before{content:"\\2713";background:#13803d}ul.checks li.warn:before{content:"!";background:#c98a00}ul.checks li.info:before{content:"i";background:' + C.faint + '}' +
    'svg{display:block}' +
    '.g-good{color:#13803d}.g-mid{color:#a06c00}.g-poor{color:#c62828}.cwaline{margin:0 0 10px}' +
    '.defs table.kv th,.defs table.kv td{font-size:9px;line-height:1.35;padding-top:1.2px;padding-bottom:1.2px}' +
    '.sbar{display:inline-block;width:42px;height:5px;background:' + C.grid + ';border-radius:3px;margin-left:6px;vertical-align:middle;overflow:hidden}.sbar i{display:block;height:100%}' +
    '.sbar i.g-good{background:#13803d}.sbar i.g-mid{background:#c98a00}.sbar i.g-poor{background:#c62828}table.grid.cwa td{white-space:nowrap}table.grid.cwa td:nth-child(2){white-space:normal}' +
    '@media screen{body{background:#5c6670}.page{margin:16px auto;box-shadow:0 6px 30px rgba(0,0,0,.35)}}';
  const LOGO = '<svg width="16" height="16" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#1d1e21"/><g fill="none" stroke="#fff" stroke-linecap="round" transform="rotate(-24 32 32)"><path d="M10 32a22 9.5 0 0 1 44 0" stroke-opacity=".45" stroke-width="3.4"/><path d="M54 32a22 9.5 0 0 1-44 0" stroke-width="3.6"/></g><circle cx="32" cy="32" r="4.4" fill="#fff"/><circle cx="51.3" cy="28.6" r="8.6" fill="#1d1e21"/><circle cx="51.3" cy="28.6" r="6.2" fill="#ff7a1a"/></svg>';

  /* Flow the blocks into pages in a hidden frame. Returns {html, pages}. */
  RP.build = async function (S, assets) {
    const blocks = RP.blocks(S, assets);
    const ifr = document.createElement('iframe');
    ifr.style.cssText = 'position:fixed;left:-20000px;top:0;width:' + PAGE_W + 'px;height:' + PAGE_H + 'px;border:0;visibility:hidden';
    document.body.appendChild(ifr);
    const doc = ifr.contentDocument;
    doc.open();
    doc.write('<!doctype html><html><head><meta charset="utf-8"><style>' + CSS + '</style></head><body></body></html>');
    doc.close();
    const title = (S.meta && S.meta.title) || S.title();
    const org = (S.meta && S.meta.org) || '';
    const pages = [];
    let body = null;
    const newPage = () => {
      const p = doc.createElement('div');
      p.className = 'page';
      p.innerHTML = '<div class="hd"><span class="brand">' + LOGO + '<b>ORBIT</b> · C-UAS flight test report</span><span>' + esc(title) + '</span></div><div class="body"></div><div class="ft"><span>' + esc(org ? org + ' · ' : '') + 'Made with Orbit</span><span class="pn"></span></div>';
      doc.body.appendChild(p);
      pages.push(p);
      body = p.querySelector('.body');
    };
    const fits = () => body.scrollHeight <= body.clientHeight + 1;
    const wait = () => new Promise((r) => setTimeout(r, 0));
    newPage();
    for (const b of blocks) {
      if (typeof b === 'object' && b.pageBreak) { if (body.children.length) newPage(); continue; }
      if (typeof b === 'object' && b.split === true) {
        // split long tables row by row, repeating the header on each page
        let i = 0;
        while (i < b.rows.length) {
          const t = doc.createElement('table');
          t.className = 'grid ' + b.cls;
          t.innerHTML = b.head;
          body.appendChild(t);
          let added = 0;
          while (i < b.rows.length) {
            t.insertAdjacentHTML('beforeend', b.rows[i]);
            if (!fits()) { t.lastElementChild.remove(); break; }
            i++; added++;
          }
          if (!added) {
            const prev = t.previousElementSibling;
            t.remove();
            if (!body.children.length) { i++; continue; }
            const carry = prev && /^\s*<h[23][^>]*>[^<]*<\/h[23]>\s*$/.test(prev.innerHTML) ? prev : null;
            if (carry) carry.remove();
            newPage();
            if (carry) body.appendChild(carry);
            continue;
          }
          if (i < b.rows.length) newPage();
        }
        continue;
      }
      const wrap = doc.createElement('div');
      wrap.innerHTML = b;
      const imgs = [...wrap.querySelectorAll('img')];
      body.appendChild(wrap);
      if (imgs.length) await Promise.all(imgs.map((im) => (im.complete ? 0 : new Promise((r) => { im.onload = im.onerror = r; }))));
      if (!fits() && body.children.length > 1) {
        // a heading should travel with what follows it
        const prev = wrap.previousElementSibling;
        const carry = prev && /^\s*<h[23][^>]*>[^<]*<\/h[23]>\s*$/.test(prev.innerHTML) ? prev : null;
        wrap.remove();
        if (carry) carry.remove();
        newPage();
        if (carry) body.appendChild(carry);
        body.appendChild(wrap);
      }
      await wait();
    }
    pages.forEach((p, k) => { p.querySelector('.pn').textContent = 'Page ' + (k + 1) + ' of ' + pages.length; });
    const head = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + esc(title) + ' – Orbit report</title><style>';
    const content = pages.map((p) => p.outerHTML).join('');
    const html = head + CSS + '</style></head><body>' + content + '</body></html>';
    const printHtml = head + CSS.replace(/@media screen\{[^}]*\{[^}]*\}[^}]*\{[^}]*\}\}/, '') + '</style></head><body>' + content + '</body></html>';
    ifr.remove();
    return { html, printHtml, pages: pages.length };
  };
})(typeof window !== 'undefined' ? window : globalThis);
