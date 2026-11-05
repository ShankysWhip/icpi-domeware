/* Orbit: exports — printable HTML report, combined 1-second CSV, KML for Google Earth,
   and a session file that reopens everything (including unlocked DJI tracks) offline. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util, A = SF.analysis;
  const E = SF.exporters = {};
  const esc = U.esc;
  const pct = (x) => (U.isNum(x) ? (x * 100).toFixed(1) + ' %' : '–');

  // ------------------------------------------------------------------ combined CSV (1 s)
  E.csv = function (S) {
    const { drone, radar, weather } = S;
    const an = S.analysis || {};
    const w = an.window || (drone ? { t0: drone.data.t[0], t1: drone.data.t[drone.data.t.length - 1] } : radar ? { t0: radar.t0, t1: radar.t1 } : weather ? { t0: weather.t0, t1: weather.t1 } : null);
    if (!w) throw new Error('Nothing to export yet.');
    const sensor = radar ? SF.radar.sensor(radar) : null;
    const hits = an.hits || [];
    const ht = hits.map((h) => h.t);
    const header = ['utc', 't_s', 'drone_lat', 'drone_lon', 'drone_height_m', 'drone_alt_m', 'drone_hspeed_mps', 'drone_vspeed_mps', 'drone_heading_deg',
      'battery_pct', 'satellites', 'range_from_radar_m', 'radar_detected', 'radar_drone_label', 'radar_track', 'radar_class', 'radar_lat', 'radar_lon',
      'radar_height_m', 'error_horizontal_m', 'error_vertical_m', 'wind_mps', 'gust_mps', 'wind_dir_deg', 'temp_c', 'rh_pct', 'pressure_hpa', 'rain_mmh'];
    const rows = [];
    const r1 = (x, d = 1) => (U.isNum(x) ? +x.toFixed(d) : '');
    const step = Math.max(1000, Math.ceil((w.t1 - w.t0) / 86400 / 1000) * 1000);
    for (let t = Math.ceil(w.t0 / 1000) * 1000; t <= w.t1; t += step) {
      const p = drone ? A.droneAt(drone, t) : null;
      const i = p ? p.i : -1;
      let hit = null, best = Infinity;
      const k = U.idxLE(ht, t + S.aopts.tol * 1000);
      for (let j = k; j >= 0 && ht[j] >= t - S.aopts.tol * 1000; j--) { const d = Math.abs(ht[j] - t); if (d < best) { best = d; hit = hits[j]; } }
      const tr = hit ? radar.trackMap.get(hit.id) : null;
      const W = (kind) => (weather ? r1(SF.weather.at(weather, kind, t), 2) : '');
      rows.push([U.fmtISO(t), r1((t - w.t0) / 1000, 0), p ? r1(p.lat, 7) : '', p ? r1(p.lon, 7) : '', p ? r1(p.h) : '', p ? r1(p.alt) : '', p ? r1(p.hs, 2) : '', p ? r1(p.vs, 2) : '', p ? r1(p.yaw) : '',
        i >= 0 ? r1(drone.data.bat[i], 0) : '', i >= 0 ? r1(drone.data.gps[i], 0) : '', p && sensor ? r1(U.dist(sensor.lat, sensor.lon, p.lat, p.lon)) : '',
        hit ? 1 : drone && radar ? 0 : '', hit ? (A.isDroneLabel(hit.cls, S.aopts.labels) ? 1 : 0) : '', hit ? hit.id : '', hit ? hit.cls : '',
        tr ? r1(tr.lat[hit.i], 7) : '', tr ? r1(tr.lon[hit.i], 7) : '', tr && sensor && U.isNum(sensor.ground) ? r1(tr.alt[hit.i] - sensor.ground) : '',
        hit ? r1(hit.eH) : '', hit ? r1(hit.eV) : '', W('wind'), W('gust'), W('dir'), W('temp'), W('rh'), W('pres'), W('rainRate')]);
    }
    return U.toCSV(header, rows);
  };

  // ------------------------------------------------------------------ KML
  const kmlColor = (hex, alpha = 'ff') => { const h = hex.replace('#', ''); return alpha + h.slice(4, 6) + h.slice(2, 4) + h.slice(0, 2); };
  E.kml = function (S) {
    const { drone, radar } = S;
    const an = S.analysis || {};
    const sensor = radar ? SF.radar.sensor(radar) : null;
    const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : 0;
    const out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">', '<Document>',
      '<name>' + esc(S.title()) + '</name>', '<open>1</open>',
      '<Style id="drone"><LineStyle><color>' + kmlColor('#ff7a1a') + '</color><width>3</width></LineStyle><PolyStyle><color>' + kmlColor('#ff7a1a', '40') + '</color></PolyStyle><IconStyle><color>' + kmlColor('#ff7a1a') + '</color></IconStyle></Style>',
      '<Style id="match"><LineStyle><color>' + kmlColor('#4cc9f0') + '</color><width>2.5</width></LineStyle><IconStyle><color>' + kmlColor('#4cc9f0') + '</color><scale>0.6</scale></IconStyle></Style>',
      '<Style id="label"><LineStyle><color>' + kmlColor('#ff4fd8') + '</color><width>2</width></LineStyle><IconStyle><color>' + kmlColor('#ff4fd8') + '</color><scale>0.6</scale></IconStyle></Style>',
      '<Style id="other"><LineStyle><color>' + kmlColor('#9aa4b2', 'a0') + '</color><width>1.2</width></LineStyle><IconStyle><scale>0.4</scale></IconStyle></Style>',
      '<Style id="ring"><LineStyle><color>80ffffff</color><width>1</width></LineStyle></Style>'];
    if (drone) {
      const d = drone.data;
      const step = Math.max(1, Math.floor(d.t.length / 4000));
      out.push('<Folder><name>Drone (' + esc(drone.name) + ')</name><Placemark><name>Drone track</name><styleUrl>#drone</styleUrl><gx:Track><altitudeMode>relativeToGround</altitudeMode>');
      for (let i = 0; i < d.t.length; i += step) out.push('<when>' + U.fmtISO(d.t[i]) + '</when>');
      for (let i = 0; i < d.t.length; i += step) out.push('<gx:coord>' + d.lon[i].toFixed(7) + ' ' + d.lat[i].toFixed(7) + ' ' + Math.max(0, d.h[i]).toFixed(1) + '</gx:coord>');
      out.push('</gx:Track></Placemark>');
      out.push('<Placemark><name>Drone path (extruded)</name><styleUrl>#drone</styleUrl><LineString><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode><coordinates>');
      for (let i = 0; i < d.t.length; i += step * 2) out.push(d.lon[i].toFixed(7) + ',' + d.lat[i].toFixed(7) + ',' + Math.max(0, d.h[i]).toFixed(1));
      out.push('</coordinates></LineString></Placemark></Folder>');
    }
    if (radar) {
      const matched = new Set(an.matchedIds || []);
      const groups = [['Radar tracks matched to the drone', (tr) => matched.has(tr.id), 'match'], ['Other drone-labelled radar tracks', (tr) => !matched.has(tr.id) && Object.keys(tr.classes).some((c) => A.isDroneLabel(c, S.aopts.labels)), 'label'], ['All other radar tracks', (tr) => !matched.has(tr.id) && !Object.keys(tr.classes).some((c) => A.isDroneLabel(c, S.aopts.labels)), 'other']];
      for (const [name, test, style] of groups) {
        const list = radar.tracks.filter(test);
        if (!list.length) continue;
        out.push('<Folder><name>' + esc(name) + '</name>' + (style === 'other' ? '<visibility>0</visibility>' : ''));
        for (const tr of list) {
          out.push('<Placemark><name>' + esc(tr.id + ' ' + tr.mainClass) + '</name>' + (style === 'other' ? '<visibility>0</visibility>' : '') + '<styleUrl>#' + style + '</styleUrl><gx:Track><altitudeMode>relativeToGround</altitudeMode>');
          for (const t of tr.t) out.push('<when>' + U.fmtISO(t) + '</when>');
          for (let i = 0; i < tr.t.length; i++) out.push('<gx:coord>' + tr.lon[i].toFixed(7) + ' ' + tr.lat[i].toFixed(7) + ' ' + (U.isNum(tr.alt[i]) ? Math.max(0, tr.alt[i] - ground).toFixed(1) : '0') + '</gx:coord>');
          out.push('</gx:Track></Placemark>');
        }
        out.push('</Folder>');
      }
      if (sensor) {
        out.push('<Folder><name>Radar site</name><Placemark><name>' + esc(sensor.name || 'Radar') + '</name><Point><coordinates>' + sensor.lon + ',' + sensor.lat + ',0</coordinates></Point></Placemark>');
        for (const r of [500, 1000, 1500, 2000]) {
          out.push('<Placemark><name>' + r + ' m</name><styleUrl>#ring</styleUrl><LineString><tessellate>1</tessellate><coordinates>');
          for (let k = 0; k <= 72; k++) { const [la, lo] = U.destination(sensor.lat, sensor.lon, k * 5, r); out.push(lo.toFixed(7) + ',' + la.toFixed(7) + ',0'); }
          out.push('</coordinates></LineString></Placemark>');
        }
        out.push('</Folder>');
      }
    }
    out.push('</Document></kml>');
    return out.join('\n');
  };

  // ------------------------------------------------------------------ HTML report
  E.report = function (S, mapPng) {
    const { drone, radar, weather } = S;
    const an = S.analysis || {};
    const sensor = an.sensor;
    const tz = S.tzOffset();
    const tm = (t) => (U.isNum(t) ? U.fmtClock(t, tz) : '–');
    const tzName = S.settings.tz === 'local' ? U.fmtOffset(tz) : 'UTC';
    const kv = (rows) => '<table class="kv">' + rows.filter((r) => r[1] !== undefined && r[1] !== '').map((r) => '<tr><th>' + esc(r[0]) + '</th><td>' + r[1] + '</td></tr>').join('') + '</table>';
    const parts = [];
    const date = drone ? U.fmtDate(drone.data.t[0], tz) : radar ? U.fmtDate(radar.t0, tz) : '';
    parts.push('<header><div class="brand">Orbit flight report</div><h1>' + esc(S.title()) + '</h1><div class="sub">' + esc(date) + ' · times in ' + tzName + ' · generated ' + esc(new Date().toISOString().slice(0, 16).replace('T', ' ')) + ' UTC</div></header>');
    if (S.anySimulated()) parts.push('<p class="warn">This report uses simulated demo data. It does not describe a real flight.</p>');
    if (an.coverage) {
      const k = (v, l, cls) => '<div class="kpi ' + (cls || '') + '"><div class="v">' + v + '</div><div class="l">' + l + '</div></div>';
      const grade = (x) => (x >= 0.9 ? 'good' : x >= 0.6 ? 'mid' : 'poor');
      parts.push('<section><h2>Result</h2><div class="kpis">' +
        k(pct(an.coverage.detected), 'of the airborne time the radar had a track on the drone', grade(an.coverage.detected)) +
        k(pct(an.coverage.classified), 'of the airborne time that track was labelled as a drone', grade(an.coverage.classified)) +
        k(an.firstDetect ? U.f(an.firstDetect.after, 0, 's') : '–', 'from takeoff to first detection' + (an.firstDetect && U.isNum(an.firstDetect.range) ? ' (at ' + U.fmtDist(an.firstDetect.range) + ')' : '')) +
        k(an.firstClass ? U.f(an.firstClass.after, 0, 's') : '–', 'from takeoff to first drone label') +
        k(String(an.breaks.length), 'track breaks longer than ' + S.aopts.breakGap + ' s') +
        k(U.f(an.err.h.median, 1, 'm'), 'median horizontal radar error (95 %: ' + U.f(an.err.h.p95, 0, 'm') + ')') +
        k(U.isNum(an.maxDetectRange) ? U.fmtDist(an.maxDetectRange) : '–', 'longest range with the drone tracked') +
        k(String(an.falseTracks.length), 'drone-labelled tracks that were not the drone') + '</div></section>');
    }
    if (mapPng) parts.push('<section><h2>Map</h2><img class="map" src="' + mapPng + '" alt="Map of the flight"></section>');
    const det = [];
    if (drone) {
      const f = an.flight || {};
      det.push('<div><h3>Drone</h3>' + kv([['Aircraft', esc(drone.product || drone.name)], ['Log', esc(S.files.drone || '')], ['Takeoff – landing', an.window ? tm(an.window.t0) + ' – ' + tm(an.window.t1) : ''],
        ['Airborne', U.fmtDur(f.airborne)], ['Distance flown', U.fmtDist(f.distance)], ['Max height', U.f(f.maxH, 0, 'm')], ['Max speed', U.f(f.maxHs, 1, 'm/s')],
        ['Max range from radar', U.isNum(f.maxRange) && f.maxRange ? U.fmtDist(f.maxRange) : ''], ['Clock offset applied', S.offsetText('drone')]]) + '</div>');
    }
    if (radar) {
      det.push('<div><h3>Radar</h3>' + kv([['Radar', esc(radar.name)], ['File', esc(S.files.radar || '')], ['Software', sensor ? esc(sensor.version || '') : ''], ['Frequency', sensor && U.isNum(sensor.freqMHz) ? sensor.freqMHz + ' MHz' : ''],
        ['Position', sensor ? sensor.lat.toFixed(6) + ', ' + sensor.lon.toFixed(6) : ''], ['Height (antenna / ground)', sensor && U.isNum(sensor.alt) ? U.f(sensor.alt, 1, 'm') + ' / ' + U.f(sensor.ground, 1, 'm') + ' MSL' : ''],
        ['Recording', tm(radar.t0) + ' – ' + tm(radar.t1)], ['Tracks', radar.tracks.length + ' (' + radar.updates + ' updates)'], ['Clock offset applied', S.offsetText('radar')]]) + '</div>');
    }
    if (weather) {
      const ws = an.weather || {};
      det.push('<div><h3>Weather</h3>' + kv([['Station', esc(weather.name)], ['Sensors', esc(weather.sensors || '')], ['Wind during flight', ws.wind && ws.wind.n ? U.f(ws.wind.mean, 1, 'm/s') + ' mean, gusts to ' + U.f(ws.gust.max, 1, 'm/s') : ''],
        ['Wind from', U.isNum(ws.dir) ? ws.dir.toFixed(0) + '°' : ''], ['Temperature', ws.temp && ws.temp.n ? U.f(ws.temp.mean, 1, '°C') : ''], ['Clock offset applied', S.offsetText('weather')]]) + '</div>');
    }
    if (det.length) parts.push('<section><h2>Sources</h2><div class="cols">' + det.join('') + '</div></section>');
    if (an.rangeBins && an.rangeBins.length) {
      parts.push('<section><h2>Detection by range from the radar</h2><table class="grid"><tr><th>Range</th><th>Time in band</th><th>Tracked</th><th>Labelled drone</th></tr>' +
        an.rangeBins.map((b) => '<tr><td>' + b.lo + '–' + b.hi + ' m</td><td>' + U.fmtDur(b.n) + '</td><td>' + pct(b.det / b.n) + '</td><td>' + pct(b.cls / b.n) + '</td></tr>').join('') + '</table></section>');
    }
    if (an.breaks && an.breaks.length) {
      parts.push('<section><h2>Track breaks</h2><table class="grid"><tr><th>From</th><th>To</th><th>Length</th><th>Range</th><th>Drone height</th><th>Note</th></tr>' +
        an.breaks.map((b) => '<tr><td>' + tm(b.t0) + '</td><td>' + tm(b.t1) + '</td><td>' + U.f(b.dur, 1, 's') + '</td><td>' + U.fmtDist(b.range) + '</td><td>' + U.f(b.h, 0, 'm') + '</td><td>' + esc(b.reason) + '</td></tr>').join('') + '</table></section>');
    }
    if (an.trackInfo) {
      const list = [...an.trackInfo.values()].filter((x) => x.matched);
      if (list.length) parts.push('<section><h2>Radar tracks on the drone</h2><table class="grid"><tr><th>Track</th><th>Label</th><th>From</th><th>To</th><th>Updates</th><th>Median error</th></tr>' +
        list.map((x) => '<tr><td>' + esc(x.id) + '</td><td>' + esc(x.cls) + '</td><td>' + tm(x.first) + '</td><td>' + tm(x.last) + '</td><td>' + x.close + '</td><td>' + U.f(x.medErr, 1, 'm') + '</td></tr>').join('') + '</table></section>');
    }
    if (an.falseTracks && an.falseTracks.length) {
      parts.push('<section><h2>Drone-labelled tracks that were not the drone</h2><table class="grid"><tr><th>Track</th><th>Label</th><th>From</th><th>To</th><th>Updates</th><th>Closest to drone</th></tr>' +
        an.falseTracks.map((x) => '<tr><td>' + esc(x.id) + '</td><td>' + esc(x.cls) + '</td><td>' + tm(x.first) + '</td><td>' + tm(x.last) + '</td><td>' + x.n + '</td><td>' + (isFinite(x.minDist) ? U.fmtDist(x.minDist) : 'not in the air at the same time') + '</td></tr>').join('') + '</table></section>');
    }
    parts.push('<section class="small"><h2>Method</h2><p>The drone\'s own flight log is the reference. Each radar update was compared with the drone\'s logged position at the same moment. A radar track counts as the drone when at least half of its updates during the flight are within ' + S.aopts.gate + ' m. At each second of the airborne time, the drone counts as tracked when such an update is within ±' + S.aopts.tol + ' s. Radar labels counted as a drone: ' + esc(S.aopts.labels.join(', ')) + '. Heights above ground are compared with the radar site\'s ground level, which assumes flat terrain.</p></section>');
    const css = 'body{font:13px/1.45 -apple-system,"Segoe UI",system-ui,sans-serif;color:#15191e;margin:0;background:#fff}main{max-width:980px;margin:0 auto;padding:28px 32px}header .brand{color:#ff7a1a;font-weight:700;letter-spacing:.4px;text-transform:uppercase;font-size:11px}h1{font-size:24px;margin:4px 0}h2{font-size:15px;margin:22px 0 8px;border-bottom:2px solid #ff7a1a;padding-bottom:4px}h3{font-size:13px;margin:0 0 6px}.sub{color:#5b6670}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.kpi{border:1px solid #dde2e7;border-radius:10px;padding:10px}.kpi .v{font:600 22px ui-monospace,Menlo,Consolas,monospace}.kpi .l{color:#5b6670;font-size:11.5px}.kpi.good .v{color:#14833f}.kpi.mid .v{color:#a07400}.kpi.poor .v{color:#c62828}img.map{width:100%;border-radius:10px;border:1px solid #dde2e7}.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}table{border-collapse:collapse;width:100%}table.kv th{text-align:left;color:#5b6670;font-weight:500;padding:2px 8px 2px 0;vertical-align:top;white-space:nowrap}table.kv td{padding:2px 0}table.grid th,table.grid td{border-bottom:1px solid #e3e7eb;padding:4px 6px;text-align:left}table.grid th{color:#5b6670;font-weight:600}table.grid td{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}.small{font-size:12px;color:#3c454d}.warn{background:#fff4d6;border:1px solid #f0c24b;padding:8px 10px;border-radius:8px}@media print{main{padding:0}h2{break-after:avoid}section{break-inside:avoid}}@media (max-width:700px){.kpis{grid-template-columns:1fr 1fr}.cols{grid-template-columns:1fr}}';
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(S.title()) + ' – Orbit report</title><style>' + css + '</style></head><body><main>' + parts.join('\n') + '</main></body></html>';
  };

  // ------------------------------------------------------------------ session file
  E.session = function (S) {
    const radarOut = S.radar ? Object.assign({}, S.radar, {
      trackMap: undefined, sensorMap: undefined, classes: [...S.radar.classes],
      tracks: S.radar.tracks.map((tr) => Object.assign({}, tr, { seen: undefined })),
    }) : null;
    return JSON.stringify({ app: 'Orbit', version: 1, saved: new Date().toISOString(), files: S.files, offsets: S.offsets, drone: S.drone, radar: radarOut, weather: S.weather, aopts: S.aopts, meta: S.meta || {}, cwa: S.cwaCfg,
      fleet: S.fleet && S.fleet.length > 1 ? S.fleet : undefined, sel: S.fleet && S.fleet.length > 1 ? S.fleet.indexOf(S.drone) : undefined });
  };
  E.loadSession = function (obj) {
    if (!obj || (obj.app !== 'Orbit' && obj.app !== 'SkyFusion')) throw new Error('Not an Orbit session file.');
    if (obj.radar) {
      const r = obj.radar;
      r.trackMap = new Map(); r.sensorMap = new Map(); r.classes = new Map(r.classes || []);
      for (const tr of r.tracks) { tr.seen = new Set(tr.t); r.trackMap.set(tr.id, tr); }
      for (const s of r.sensors || []) r.sensorMap.set(s.id, s);
      for (const tr of r.tracks) for (const k of ['lat', 'lon', 'alt', 'spd', 'hdg', 'el', 'score', 'rcs', 'mrcs']) tr[k] = tr[k].map((v) => (v === null ? NaN : v));
    }
    const fixNaN = (o) => { if (!o) return; for (const k in o.data) if (Array.isArray(o.data[k])) o.data[k] = o.data[k].map((v) => (v === null ? NaN : v)); };
    fixNaN(obj.drone); fixNaN(obj.weather);
    for (const d of obj.fleet || []) fixNaN(d);
    return obj;
  };
})(typeof window !== 'undefined' ? window : globalThis);
