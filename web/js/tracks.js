/* Orbit: drone tracks from exports: CSV (Airdata, PhantomHelp, dji-log, Litchi or any
   CSV with time + lat/lon), GPX and KML gx:Track. The result has the same shape as a decoded
   DJI log, so everything downstream treats them the same. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util, X = SF.xml;
  const T = SF.tracks = {};

  function unitFactor(name, kind) {
    const n = name.toLowerCase();
    if (kind === 'len') {
      if (/\(f(ee)?t\)|\[f(ee)?t\]|_ft\b|\bfeet\b/.test(n)) return 0.3048;
      return 1;
    }
    if (kind === 'spd') {
      if (/mph/.test(n)) return 0.44704;
      if (/km\/?h|kph|kmh/.test(n)) return 1 / 3.6;
      if (/\(kn|\[kn|knots?|\bkt\b/.test(n)) return 0.514444;
      return 1;
    }
    return 1;
  }
  const PATTERNS = {
    timeUtc: [/^datetime\s*\(utc\)$/i, /^custom\.datetime$/i, /date.?time.*utc|utc.*date.?time|time.?utc|timestamp.?utc/i, /^gps.?time$/i],
    timeAny: [/^datetime$/i, /^date.?time/i, /^timestamp/i, /^time$/i, /^time\s*\(/i, /^custom\.updatetime/i, /^date$/i],
    date: [/^custom\.date/i, /^date(\s|$|\()/i],
    tOffsetMs: [/^time\s*\(millisecond\)$/i, /^time\s*\(ms\)$/i, /^offset.?ms$/i],
    flyTime: [/^osd\.flytime\s*\[s\]$/i, /^osd\.flytime$/i, /flight.?time\s*\(s\)/i],
    lat: [/^(osd\.)?latitude$/i, /^lat$/i, /latitude/i],
    lon: [/^(osd\.)?longitude$/i, /^lon$|^lng$|^long$/i, /longitude/i],
    h: [/height_above_takeoff/i, /^osd\.height\s*\[/i, /^osd\.height$/i, /rel(ative)?.?alt/i, /^height/i, /altitude\s*\(m\)/i],
    alt: [/altitude_above_sea/i, /^osd\.altitude/i, /^alt(itude)?(_msl|\s*msl)?\b/i, /^ele(vation)?$/i, /gps.?alt/i],
    hs: [/^speed\s*\(/i, /^osd\.hspeed/i, /^h.?speed/i, /^speed$/i, /ground.?speed/i],
    vs: [/^osd\.vspeed/i, /^v.?speed/i, /^zspeed/i, /climb/i],
    yaw: [/compass_heading/i, /^osd\.yaw/i, /^yaw/i, /^heading/i, /^course/i],
    bat: [/^battery_percent/i, /^battery\.chargelevel/i, /^battery(\s*%|\s*\(%\))?$/i, /charge.?level/i, /remaining.?battery/i],
    volt: [/^voltage\s*\(v\)/i, /^battery\.voltage/i, /^voltage$/i],
    gps: [/^satellites/i, /^osd\.gpsnum/i, /^gps.?num/i, /^sats?$/i],
    mode: [/^flycstate$/i, /^osd\.flycstate$/i, /flight.?mode/i],
    msg: [/^message$/i, /^app\.tip/i, /^app\.warning/i],
  };
  function findCol(header, key, used) {
    for (const re of PATTERNS[key]) {
      const j = header.findIndex((h, k) => !used.has(k) && re.test(h.trim()));
      if (j >= 0) { used.add(j); return j; }
    }
    return -1;
  }

  T.fromRows = function (rows, opts) {
    opts = opts || {};
    rows = rows.filter((r) => r.length && !(r.length === 1 && /^sep=/i.test(r[0])));
    const hi = rows.findIndex((r) => r.some((c) => /lat/i.test(c)) && r.some((c) => /lon|lng/i.test(c)));
    if (hi < 0) throw new Error('No latitude/longitude columns found.');
    const header = rows[hi].map((h) => String(h).trim());
    const used = new Set(), c = {};
    for (const k of ['timeUtc', 'tOffsetMs', 'flyTime', 'lat', 'lon', 'h', 'alt', 'hs', 'vs', 'yaw', 'bat', 'volt', 'gps', 'mode', 'msg']) c[k] = findCol(header, k, used);
    c.timeAny = c.timeUtc >= 0 ? -1 : findCol(header, 'timeAny', used);
    c.date = findCol(header, 'date', used);
    const localTime = c.timeUtc < 0 && c.timeAny >= 0 && /local/i.test(header[c.timeAny]);
    const fh = c.h >= 0 ? unitFactor(header[c.h], 'len') : 1, fa = c.alt >= 0 ? unitFactor(header[c.alt], 'len') : 1;
    const fs = c.hs >= 0 ? unitFactor(header[c.hs], 'spd') : 1, fv = c.vs >= 0 ? unitFactor(header[c.vs], 'spd') : 1;
    const D = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [], bat: [], volt: [], gps: [], mode: [], motor: [], ground: [] };
    const events = [], modes = [];
    let base = NaN, prevMode = null;
    const tzFor = (ms) => (U.isNum(opts.tz) ? opts.tz : localTime ? -new Date(ms).getTimezoneOffset() : 0);
    for (let i = hi + 1; i < rows.length; i++) {
      const r = rows[i];
      const lat = U.num(r[c.lat]), lon = U.num(r[c.lon]);
      if (!U.isNum(lat) || !U.isNum(lon) || (lat === 0 && lon === 0)) continue;
      let t = NaN;
      const tcol = c.timeUtc >= 0 ? c.timeUtc : c.timeAny;
      if (tcol >= 0 && r[tcol]) {
        let s = String(r[tcol]).trim();
        if (c.date >= 0 && c.date !== tcol && r[c.date] && !/\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2}/.test(s)) s = String(r[c.date]).trim() + ' ' + s;
        const guess = U.parseTime(s, { tz: 0 });
        t = U.isNum(guess) ? U.parseTime(s, { tz: c.timeUtc >= 0 ? 0 : tzFor(guess) }) : NaN;
      }
      if (c.tOffsetMs >= 0 && U.isNum(U.num(r[c.tOffsetMs]))) {
        if (!U.isNum(base) && U.isNum(t)) base = t - U.num(r[c.tOffsetMs]);
        if (U.isNum(base)) t = base + U.num(r[c.tOffsetMs]);
      }
      if (!U.isNum(t) && c.flyTime >= 0) t = (opts.baseTime || Date.UTC(2000, 0, 1)) + U.num(r[c.flyTime]) * 1000;
      if (!U.isNum(t)) continue;
      D.t.push(t); D.lat.push(lat); D.lon.push(lon);
      D.h.push(c.h >= 0 ? U.num(r[c.h]) * fh : NaN);
      D.alt.push(c.alt >= 0 ? U.num(r[c.alt]) * fa : NaN);
      D.hs.push(c.hs >= 0 ? U.num(r[c.hs]) * fs : NaN);
      D.vs.push(c.vs >= 0 ? U.num(r[c.vs]) * fv : NaN);
      D.yaw.push(c.yaw >= 0 ? (U.num(r[c.yaw]) + 360) % 360 : NaN);
      D.bat.push(c.bat >= 0 ? U.num(r[c.bat]) : NaN);
      D.volt.push(c.volt >= 0 ? U.num(r[c.volt]) : NaN);
      D.gps.push(c.gps >= 0 ? U.num(r[c.gps]) : NaN);
      const m = c.mode >= 0 ? String(r[c.mode] || '').trim() : '';
      if (m) {
        let mi = modes.indexOf(m);
        if (mi < 0) { modes.push(m); mi = modes.length - 1; }
        D.mode.push(mi);
        if (prevMode !== null && m !== prevMode) events.push({ t, kind: 'mode', text: 'Flight mode: ' + m });
        prevMode = m;
      } else D.mode.push(-1);
      D.motor.push(NaN); D.ground.push(NaN);
      if (c.msg >= 0 && r[c.msg] && String(r[c.msg]).trim()) events.push({ t, kind: /warn/i.test(header[c.msg]) ? 'warn' : 'tip', text: String(r[c.msg]).trim() });
    }
    return finish(D, events, opts, { modes, format: 'csv', note: localTime ? 'Times were in local time; read with ' + U.fmtOffset(tzFor(D.t[0] || Date.now())) + '.' : '' });
  };

  function finish(D, events, opts, extra) {
    if (D.t.length < 2) throw new Error('Fewer than two usable track points.');
    // sort by time and drop duplicate timestamps
    const order = D.t.map((t, i) => i).sort((a, b) => D.t[a] - D.t[b]);
    const keep = order.filter((i, k) => k === 0 || D.t[i] !== D.t[order[k - 1]]);
    for (const k in D) D[k] = keep.map((i) => D[k][i]);
    const n = D.t.length;
    // height above takeoff from MSL altitude when the file has none
    if (!D.h.some(U.isNum)) {
      const a0 = D.alt.find(U.isNum);
      D.h = D.alt.map((a) => (U.isNum(a) && U.isNum(a0) ? a - a0 : 0));
    }
    // derived speeds and heading
    const f = U.frame(D.lat[0], D.lon[0]);
    for (let i = 0; i < n; i++) {
      const j = Math.min(n - 1, i + 1), k = Math.max(0, i - 1);
      const dt = (D.t[j] - D.t[k]) / 1000;
      if (!U.isNum(D.hs[i]) && dt > 0) D.hs[i] = Math.hypot(f.x(D.lon[j]) - f.x(D.lon[k]), f.y(D.lat[j]) - f.y(D.lat[k])) / dt;
      if (!U.isNum(D.vs[i]) && dt > 0) D.vs[i] = (D.h[j] - D.h[k]) / dt;
      if (!U.isNum(D.yaw[i]) && (D.lat[j] !== D.lat[k] || D.lon[j] !== D.lon[k])) D.yaw[i] = U.bearing(D.lat[k], D.lon[k], D.lat[j], D.lon[j]);
    }
    const home = { lat: D.lat[0], lon: D.lon[0], alt: D.alt.find(U.isNum) };
    return {
      kind: 'drone', source: extra.format, name: opts.name || 'Drone track', product: opts.product || '', data: D, events, pilot: [], homes: [home], home,
      details: null, info: { modes: extra.modes || [] }, note: extra.note || '', t0: D.t[0], t1: D.t[n - 1],
    };
  }

  T.finish = finish; // shared with the ArduPilot readers
  // What kind of log a drone came from, in words
  T.sourceText = (d) => (d.source === 'dji' ? 'DJI flight record, format ' + d.version : d.source === 'ardupilot' ? 'ArduPilot DataFlash log' + (d.product ? ' (' + d.product + ')' : '')
    : d.source === 'tlog' ? 'MAVLink telemetry log' + (d.label ? ' (' + d.label + ')' : '') : String(d.source).toUpperCase());

  T.fromGPX = function (text, opts) {
    const root = X.parse(text);
    const D = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [], bat: [], volt: [], gps: [], mode: [], motor: [], ground: [] };
    X.walk(root, (n) => {
      if (n.name !== 'trkpt' && n.name !== 'rtept') return;
      const t = U.parseTime(X.get(n, 'time'));
      const lat = parseFloat(n.attrs.lat), lon = parseFloat(n.attrs.lon);
      if (!U.isNum(t) || !U.isNum(lat) || !U.isNum(lon)) return;
      D.t.push(t); D.lat.push(lat); D.lon.push(lon); D.alt.push(X.num(n, 'ele')); D.h.push(NaN);
      D.hs.push(X.num(n, 'speed')); D.vs.push(NaN); D.yaw.push(X.num(n, 'course'));
      D.bat.push(NaN); D.volt.push(NaN); D.gps.push(X.num(n, 'sat')); D.mode.push(-1); D.motor.push(NaN); D.ground.push(NaN);
      return false;
    });
    const name = X.get(root.children[0] || root, 'trk/name') || (opts && opts.name);
    return finish(D, [], Object.assign({}, opts, { name }), { format: 'gpx' });
  };

  T.fromKML = function (text, opts) {
    const root = X.parse(text);
    const D = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [], bat: [], volt: [], gps: [], mode: [], motor: [], ground: [] };
    let relative = false;
    X.walk(root, (n) => {
      if (n.name !== 'Track') return;
      relative = /relative/i.test(X.get(n, 'altitudeMode') || '');
      const whens = n.children.filter((c) => c.name === 'when').map((c) => U.parseTime(c.text));
      const coords = n.children.filter((c) => c.name === 'coord').map((c) => c.text.trim().split(/\s+/).map(Number));
      for (let i = 0; i < Math.min(whens.length, coords.length); i++) {
        const [lon, lat, alt] = coords[i];
        if (!U.isNum(whens[i]) || !U.isNum(lat)) continue;
        D.t.push(whens[i]); D.lat.push(lat); D.lon.push(lon);
        D.alt.push(relative ? NaN : alt); D.h.push(relative ? alt : NaN);
        for (const k of ['hs', 'vs', 'yaw', 'bat', 'volt', 'gps', 'motor', 'ground']) D[k].push(NaN);
        D.mode.push(-1);
      }
      return false;
    });
    if (D.t.length < 2) throw new Error('The KML has no time-stamped track (gx:Track with <when> values).');
    return finish(D, [], opts || {}, { format: 'kml' });
  };
})(typeof window !== 'undefined' ? window : globalThis);
