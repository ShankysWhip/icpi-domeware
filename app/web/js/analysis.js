/* Orbit: drone-truth versus radar analysis.
   The drone's own log is ground truth. Each radar update is compared with the drone's
   position at the same moment; tracks that follow the drone are "matched". From that come
   detection coverage, drone-classification coverage, first detection, track breaks, position
   error, coverage by range and false drone tracks. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const A = SF.analysis = {};

  A.defaults = () => ({ gate: 75, tol: 3, breakGap: 5, minHeight: 2, labels: ['DRONE', 'SUSPECTED_DRONE', 'UAV', 'UAS'], rangeStep: 250 });

  // Interpolated drone state at t (NaN outside the log or across >5 s gaps).
  A.droneAt = function (drone, t) {
    const d = drone.data;
    if (!d.t.length || t < d.t[0] - 1000 || t > d.t[d.t.length - 1] + 1000) return null;
    const g = 5000;
    const lat = U.interp(d.t, d.lat, t, g);
    if (!U.isNum(lat)) return null;
    return {
      t, lat, lon: U.interp(d.t, d.lon, t, g), h: U.interp(d.t, d.h, t, g), alt: U.interp(d.t, d.alt, t, g),
      hs: U.interp(d.t, d.hs, t, g), vs: U.interp(d.t, d.vs, t, g), yaw: U.interpAngle(d.t, d.yaw, t, g),
      i: U.idxNear(d.t, t),
    };
  };

  // Airborne window: first and last moment above minHeight (or flagged in the air).
  A.flightWindow = function (drone, minH) {
    const d = drone.data, n = d.t.length;
    let a = -1, b = -1;
    for (let i = 0; i < n; i++) {
      const air = (U.isNum(d.ground && d.ground[i]) ? d.ground[i] === 0 : true) && d.h[i] > minH;
      if (air) { if (a < 0) a = i; b = i; }
    }
    if (a < 0) return { t0: d.t[0], t1: d.t[n - 1], airborne: false };
    return { t0: d.t[a], t1: d.t[b], airborne: true };
  };

  A.isDroneLabel = (cls, labels) => !!cls && labels.indexOf(String(cls).toUpperCase()) >= 0;

  /* Full analysis. Any of drone / radar may be missing; the result says what it could do. */
  A.run = function (drone, radar, wx, opts) {
    opts = Object.assign(A.defaults(), opts || {});
    const out = { opts, hasDrone: !!drone, hasRadar: !!radar, hasWx: !!wx };
    const sensor = radar ? SF.radar.sensor(radar) : null;
    out.sensor = sensor;
    const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : sensor && U.isNum(sensor.alt) ? sensor.alt : NaN;
    if (drone) {
      const d = drone.data, w = A.flightWindow(drone, opts.minHeight);
      out.window = w;
      let dist = 0, maxH = -Infinity, maxHs = 0, maxR = 0, maxHome = 0;
      const home = drone.home || { lat: d.lat[0], lon: d.lon[0] };
      for (let i = 0; i < d.t.length; i++) {
        if (i) dist += U.dist(d.lat[i - 1], d.lon[i - 1], d.lat[i], d.lon[i]);
        if (d.h[i] > maxH) maxH = d.h[i];
        if (d.hs[i] > maxHs) maxHs = d.hs[i];
        if (sensor) maxR = Math.max(maxR, U.dist(sensor.lat, sensor.lon, d.lat[i], d.lon[i]));
        maxHome = Math.max(maxHome, U.dist(home.lat, home.lon, d.lat[i], d.lon[i]));
      }
      out.flight = { duration: (d.t[d.t.length - 1] - d.t[0]) / 1000, airborne: (w.t1 - w.t0) / 1000, distance: dist, maxH, maxHs, maxRange: maxR, maxHome };
    }
    if (radar) {
      const byClass = {};
      for (const tr of radar.tracks) byClass[tr.mainClass] = (byClass[tr.mainClass] || 0) + 1;
      out.radar = { tracks: radar.tracks.length, updates: radar.updates, byClass, t0: radar.t0, t1: radar.t1 };
      out.labelledTracks = radar.tracks.filter((tr) => Object.keys(tr.classes).some((c) => A.isDroneLabel(c, opts.labels)));
    }
    if (wx && drone && out.window) {
      const W = SF.weather, ws = [], gs = [], ts = [], ds = [];
      const d0 = out.window.t0 - 60000, d1 = out.window.t1 + 60000;
      for (let i = 0; i < wx.data.t.length; i++) {
        const t = wx.data.t[i];
        if (t < d0 || t > d1) continue;
        if (U.isNum(wx.data.wind[i])) ws.push(wx.data.wind[i]);
        if (U.isNum(wx.data.gust[i])) gs.push(wx.data.gust[i]);
        if (U.isNum(wx.data.temp[i])) ts.push(wx.data.temp[i]);
        if (U.isNum(wx.data.dir[i])) ds.push(wx.data.dir[i]);
      }
      const meanDir = ds.length ? (Math.atan2(ds.reduce((s, x) => s + Math.sin(x * U.D2R), 0), ds.reduce((s, x) => s + Math.cos(x * U.D2R), 0)) / U.D2R + 360) % 360 : NaN;
      out.weather = { wind: U.stats(ws), gust: U.stats(gs), temp: U.stats(ts), dir: meanDir, samples: ws.length || ts.length, wxNow: W };
    }
    if (!drone || !radar) return out;

    // ---------------------------------------------------------------- compare radar to truth
    const w = out.window;
    const hits = [], trackInfo = new Map();
    for (const tr of radar.tracks) {
      const info = { id: tr.id, n: tr.t.length, overlap: 0, close: 0, errs: [], cls: tr.mainClass, first: tr.first, last: tr.last, matched: false, minDist: Infinity };
      for (let i = 0; i < tr.t.length; i++) {
        const t = tr.t[i];
        if (t < w.t0 - 2000 || t > w.t1 + 2000) continue;
        const p = A.droneAt(drone, t);
        if (!p) continue;
        info.overlap++;
        const eH = U.dist(p.lat, p.lon, tr.lat[i], tr.lon[i]);
        if (eH < info.minDist) info.minDist = eH;
        // with other drones in the air (opts.others) an update belongs only to the nearest of them
        const theirs = eH <= opts.gate && opts.others && opts.others.some((o) => { const q = A.droneAt(o, t); return q && U.dist(q.lat, q.lon, tr.lat[i], tr.lon[i]) < eH; });
        if (eH <= opts.gate && !theirs) {
          info.close++;
          info.errs.push(eH);
          const f = U.frame(p.lat, p.lon);
          const hit = {
            t, id: tr.id, i, cls: tr.cls[i], eH, eV: U.isNum(ground) && U.isNum(tr.alt[i]) ? tr.alt[i] - ground - p.h : NaN,
            range: sensor ? U.dist(sensor.lat, sensor.lon, p.lat, p.lon) : NaN, h: p.h, score: tr.score[i], rcs: tr.rcs[i],
            east: f.x(tr.lon[i]), north: f.y(tr.lat[i]), eR: NaN, eAz: NaN,
          };
          if (sensor) {
            // error seen from the radar: along the beam (range) and across it (bearing)
            hit.eR = U.dist(sensor.lat, sensor.lon, tr.lat[i], tr.lon[i]) - hit.range;
            hit.eAz = U.angDiff(U.bearing(sensor.lat, sensor.lon, tr.lat[i], tr.lon[i]), U.bearing(sensor.lat, sensor.lon, p.lat, p.lon));
          }
          hits.push(hit);
        }
      }
      info.matched = info.close >= 2 && info.close >= 0.5 * info.overlap;
      info.medErr = U.stats(info.errs).median;
      trackInfo.set(tr.id, info);
    }
    const matchedHits = hits.filter((h) => trackInfo.get(h.id).matched).sort((a, b) => a.t - b.t);
    out.hits = matchedHits;
    out.trackInfo = trackInfo;
    out.matchedIds = [...trackInfo.values()].filter((x) => x.matched).sort((a, b) => a.first - b.first).map((x) => x.id);

    // detection timeline at 1 s steps over the airborne window (coarser for very long windows,
    // so a broken clock can never make this loop run away)
    const step = Math.max(1000, Math.ceil((w.t1 - w.t0) / 20000 / 1000) * 1000), times = [], det = [], cls = [], err = [];
    const ht = matchedHits.map((h) => h.t);
    for (let t = w.t0; t <= w.t1; t += step) {
      times.push(t);
      let lo = U.idxLE(ht, t + opts.tol * 1000), best = null, bestDt = Infinity, anyDrone = false;
      for (let k = lo; k >= 0 && ht[k] >= t - opts.tol * 1000; k--) {
        const dt = Math.abs(ht[k] - t);
        if (dt < bestDt) { bestDt = dt; best = matchedHits[k]; }
        if (A.isDroneLabel(matchedHits[k].cls, opts.labels)) anyDrone = true;
      }
      det.push(best ? 1 : 0);
      cls.push(best && anyDrone ? 1 : 0);
      err.push(best ? best.eH : NaN);
    }
    const nS = times.length || 1;
    out.series = { t: times, det, cls, err, step };
    out.coverage = { detected: det.reduce((a, b) => a + b, 0) / nS, classified: cls.reduce((a, b) => a + b, 0) / nS, samples: times.length };

    const first = matchedHits.find((h) => h.t >= w.t0 - opts.tol * 1000);
    const firstC = matchedHits.find((h) => h.t >= w.t0 - opts.tol * 1000 && A.isDroneLabel(h.cls, opts.labels));
    const at = (h) => (h ? { t: h.t, after: (h.t - w.t0) / 1000, range: h.range, h: h.h, id: h.id } : null);
    out.firstDetect = at(first);
    out.firstClass = at(firstC);

    // breaks: gaps between consecutive matched updates longer than breakGap while airborne
    const breaks = [];
    const inAir = matchedHits.filter((h) => h.t >= w.t0 && h.t <= w.t1);
    const pushBreak = (a, b, reason) => {
      const mid = A.droneAt(drone, a);
      breaks.push({ t0: a, t1: b, dur: (b - a) / 1000, range: mid && sensor ? U.dist(sensor.lat, sensor.lon, mid.lat, mid.lon) : NaN, h: mid ? mid.h : NaN, reason });
    };
    if (inAir.length) {
      if ((inAir[0].t - w.t0) / 1000 > opts.breakGap) pushBreak(w.t0, inAir[0].t, 'before first detection');
      for (let k = 1; k < inAir.length; k++) if ((inAir[k].t - inAir[k - 1].t) / 1000 > opts.breakGap) pushBreak(inAir[k - 1].t, inAir[k].t, inAir[k].id !== inAir[k - 1].id ? 'new track ' + inAir[k].id : 'same track');
      if ((w.t1 - inAir[inAir.length - 1].t) / 1000 > opts.breakGap) pushBreak(inAir[inAir.length - 1].t, w.t1, 'until landing');
    } else pushBreak(w.t0, w.t1, 'never detected');
    out.breaks = breaks;
    let switches = 0;
    for (let k = 1; k < inAir.length; k++) if (inAir[k].id !== inAir[k - 1].id) switches++;
    out.trackSwitches = switches;

    out.err = { h: U.stats(matchedHits.map((h) => h.eH)), v: U.stats(matchedHits.map((h) => h.eV)) };
    // accuracy the way radar testers report it: CEP50/CEP95 (median and 95th percentile of the
    // horizontal miss distance), RMSE, the mean offset (bias) and errors split along and across the beam
    const near = matchedHits.filter((h) => h.range > 30); // bearing is meaningless right next to the radar
    out.acc = {
      cep50: out.err.h.median, cep95: out.err.h.p95, rmse: out.err.h.rms, n: matchedHits.length,
      biasE: U.stats(matchedHits.map((h) => h.east)).mean, biasN: U.stats(matchedHits.map((h) => h.north)).mean,
      range: U.stats(matchedHits.map((h) => h.eR)), az: U.stats(near.map((h) => h.eAz)), height: out.err.v,
    };
    out.acc.biasMag = Math.hypot(out.acc.biasE, out.acc.biasN);
    out.acc.biasDir = (Math.atan2(out.acc.biasE, out.acc.biasN) / U.D2R + 360) % 360;
    const iv = U.stats(inAir.slice(1).map((h, k) => (h.t - inAir[k].t) / 1000).filter((x) => x < opts.breakGap));
    out.updateInterval = iv.median;
    // track continuity: longest stretch without a break, total time lost
    let longest = 0, segStart = inAir.length ? inAir[0].t : NaN;
    for (let k = 1; k <= inAir.length; k++) {
      if (k === inAir.length || (inAir[k].t - inAir[k - 1].t) / 1000 > opts.breakGap) {
        longest = Math.max(longest, (inAir[k - 1].t - segStart) / 1000);
        if (k < inAir.length) segStart = inAir[k].t;
      }
    }
    out.longestTrack = longest;
    out.longestBreak = breaks.reduce((m, b) => Math.max(m, b.dur), 0);
    out.labelShare = inAir.length ? inAir.filter((h) => A.isDroneLabel(h.cls, opts.labels)).length / inAir.length : NaN;

    // coverage by range from the radar
    if (sensor) {
      const bins = new Map();
      for (let k = 0; k < times.length; k++) {
        const p = A.droneAt(drone, times[k]);
        if (!p) continue;
        const r = U.dist(sensor.lat, sensor.lon, p.lat, p.lon);
        const lo = Math.floor(r / opts.rangeStep) * opts.rangeStep;
        let b = bins.get(lo);
        if (!b) bins.set(lo, (b = { lo, hi: lo + opts.rangeStep, n: 0, det: 0, cls: 0 }));
        const sec = step / 1000; // n, det and cls are in seconds
        b.n += sec; b.det += det[k] * sec; b.cls += cls[k] * sec;
      }
      out.rangeBins = [...bins.values()].sort((a, b) => a.lo - b.lo);
      let maxDet = 0;
      for (const h of matchedHits) if (h.range > maxDet) maxDet = h.range;
      out.maxDetectRange = maxDet;
    }

    // drone-labelled tracks that are not the drone
    out.falseTracks = radar.tracks
      .filter((tr) => !trackInfo.get(tr.id).matched && Object.keys(tr.classes).some((c) => A.isDroneLabel(c, opts.labels)))
      .map((tr) => ({ id: tr.id, cls: tr.mainClass, first: tr.first, last: tr.last, n: tr.t.length, minDist: trackInfo.get(tr.id).minDist }));
    const recH = (radar.t1 - radar.t0) / 3600e3;
    out.falseRate = recH > 0 ? out.falseTracks.length / recH : NaN;
    // radar outages: stretches of the flight with no radar message at all (not a missed detection)
    out.outages = [];
    const snaps = radar.snapTimes && radar.snapTimes.length ? radar.snapTimes : null;
    if (snaps) {
      let prev = null;
      for (const s of snaps) {
        if (s < w.t0) { prev = s; continue; }
        if (s > w.t1) break;
        if (prev != null && s - prev > 5000) out.outages.push({ t0: prev, t1: s, dur: (s - prev) / 1000 });
        prev = s;
      }
    }
    return out;
  };

  /* Automatic data checks, as a list of {level: 'ok'|'warn'|'info', text}. sync is the result of
     estimateOffset (optional). */
  A.checks = function (drone, radar, wx, an, sync) {
    const out = [], ok = (text) => out.push({ level: 'ok', text }), warn = (text) => out.push({ level: 'warn', text }), info = (text) => out.push({ level: 'info', text });
    const w = an && an.window;
    if (drone) {
      const d = drone.data;
      if (drone.info && drone.info.timesRebuilt) warn('The drone log\'s clock records could not be read; times were spread evenly over the logged flight.');
      else if (drone.source === 'dji') ok('All ' + d.t.length + ' drone positions decoded with the log\'s own UTC clock (DJI format ' + drone.version + ').');
      else if (drone.source === 'ardupilot') ok('All ' + d.t.length + ' drone positions placed on UTC by the GPS week and milliseconds in the ArduPilot log.');
      else ok(d.t.length + ' drone positions read from the ' + (SF.tracks ? SF.tracks.sourceText(drone) : String(drone.source).toUpperCase()) + '.');
      if (drone.source === 'tlog') warn('Drone times are when the ground station received each packet (.tlog), which can lag by a few hundred milliseconds: at 15 m/s, 0.3 s is 4.5 m of position error. The drone\'s own .BIN log is more exact.');
      if (w) {
        const sats = [];
        let gaps = 0;
        for (let i = 0; i < d.t.length; i++) {
          if (d.t[i] < w.t0 || d.t[i] > w.t1) continue;
          if (U.isNum(d.gps[i])) sats.push(d.gps[i]);
          if (i && d.t[i] - d.t[i - 1] > 2000) gaps++;
        }
        const st = U.stats(sats);
        if (st.n) (st.min >= 10 ? ok : warn)('GPS: ' + st.min + '–' + st.max + ' satellites during the flight' + (st.min < 10 ? ' (low counts make the reference less exact)' : '') + '.');
        if (gaps) warn('The drone log has ' + gaps + ' gaps longer than 2 s during the flight.');
      }
    } else info('No drone log: detection results need the drone\'s own log as the reference.');
    if (radar) {
      const sensor = SF.radar.sensor(radar);
      sensor ? ok('Radar position known: ' + sensor.lat.toFixed(5) + ', ' + sensor.lon.toFixed(5) + (U.isNum(sensor.ground) ? ', ground ' + sensor.ground.toFixed(1) + ' m MSL' : '') + '.') : warn('The radar file has no radar position; ranges and range rings are not available.');
      if (w) {
        if (radar.t0 > w.t0 + 5000) warn('The radar recording starts ' + U.fmtDur((radar.t0 - w.t0) / 1000) + ' after takeoff.');
        if (radar.t1 < w.t1 - 5000) warn('The radar recording ends ' + U.fmtDur((w.t1 - radar.t1) / 1000) + ' before landing.');
        if (radar.t0 <= w.t0 + 5000 && radar.t1 >= w.t1 - 5000) ok('The radar recorded the whole flight.');
        const bad = (radar.status || []).filter((s) => s.t >= w.t0 && s.t <= w.t1 && s.state && s.state !== 'operational');
        if (bad.length) warn('Radar reported a non-operational state during the flight (' + bad.map((b) => b.state).join(', ') + ').');
      }
      if (an && an.outages && an.outages.length) warn('The radar sent no data for ' + an.outages.map((o) => o.dur.toFixed(0) + ' s').join(', ') + ' during the flight; those breaks are radar outages, not missed detections.');
      else if (an && an.coverage) ok('No radar data outages during the flight.');
    }
    if (drone && radar && an && an.coverage) {
      if (!an.matchedIds.length) warn('No radar track followed the drone within ' + an.opts.gate + ' m. Check the clock offsets or raise the match distance.');
      if (sync) {
        const s = sync.offsetMs / 1000;
        Math.abs(s) <= 0.5 ? ok('Radar and drone clocks agree (best offset ' + (s >= 0 ? '+' : '') + s.toFixed(1) + ' s).') : warn('The radar clock looks ' + Math.abs(s).toFixed(1) + ' s ' + (s > 0 ? 'behind' : 'ahead') + '; aligning it would cut the median error from ' + sync.medianAtZero.toFixed(0) + ' to ' + sync.median.toFixed(0) + ' m.');
      }
    }
    if (wx) {
      if (w) {
        const n = wx.data.t.filter((t) => t >= w.t0 - 120e3 && t <= w.t1 + 120e3).length;
        n ? ok('Weather: ' + n + ' records during the flight' + (U.isNum(wx.stepMs) ? ' (every ' + U.fmtDur(wx.stepMs / 1000) + ')' : '') + '.') : warn('The weather file has no data during the flight. Check its time zone (Files tab).');
      }
    } else info('No weather file.');
    return out;
  };

  // One plain-language paragraph for the report and the Analysis tab.
  A.summaryText = function (drone, radar, an) {
    if (!an || !an.coverage) return '';
    const f = an.flight || {}, p = (x) => (x * 100).toFixed(1) + ' %';
    const sensor = radar && SF.radar.sensor(radar);
    const name = drone && (drone.product || drone.name) ? 'the ' + (drone.product || drone.name) : 'the drone';
    const rname = (sensor && sensor.name) || (radar && radar.name) || 'the radar';
    let s = 'During the ' + U.fmtDur(f.airborne) + ' flight of ' + name + ' (up to ' + U.f(f.maxH, 0) + ' m high' + (f.maxRange ? ' and ' + U.fmtDist(f.maxRange) + ' from the radar' : '') + '), ' + rname + ' tracked the drone ' + p(an.coverage.detected) + ' of the airborne time and labelled it as a drone ' + p(an.coverage.classified) + ' of the time. ';
    if (an.firstDetect) s += 'The first detection came ' + an.firstDetect.after.toFixed(0) + ' s after takeoff' + (U.isNum(an.firstDetect.range) ? ' at ' + U.fmtDist(an.firstDetect.range) : '') + (an.firstClass ? ' and the first drone label ' + an.firstClass.after.toFixed(0) + ' s after takeoff' : '') + '. ';
    else s += 'The radar never tracked the drone. ';
    if (an.breaks.length) s += 'The track broke ' + an.breaks.length + ' time' + (an.breaks.length > 1 ? 's' : '') + ' (longest ' + an.longestBreak.toFixed(0) + ' s) and used ' + an.matchedIds.length + ' track ID' + (an.matchedIds.length > 1 ? 's' : '') + '. ';
    if (an.acc && an.acc.n) s += 'Position accuracy was ' + U.f(an.acc.cep50, 0) + ' m CEP50 and ' + U.f(an.acc.cep95, 0) + ' m CEP95. ';
    s += an.falseTracks.length ? an.falseTracks.length + ' other track' + (an.falseTracks.length > 1 ? 's were' : ' was') + ' labelled as a drone.' : 'No other track was labelled as a drone.';
    return s;
  };

  /* Find the clock offset (ms, added to radar times) that best lines the radar up with the
     drone. Uses tracks that come within 400 m of the drone; minimises the median error. */
  A.estimateOffset = function (drone, radar, opts) {
    opts = Object.assign({ span: 60, coarse: 1, fine: 0.1 }, opts || {});
    const w = A.flightWindow(drone, 2);
    const pts = [];
    for (const tr of radar.tracks) {
      if (tr.last < w.t0 - opts.span * 1000 || tr.first > w.t1 + opts.span * 1000) continue;
      let near = 0, tot = 0;
      for (let i = 0; i < tr.t.length; i += Math.max(1, Math.floor(tr.t.length / 20))) {
        const p = A.droneAt(drone, tr.t[i]);
        if (!p) continue;
        tot++;
        if (U.dist(p.lat, p.lon, tr.lat[i], tr.lon[i]) < 400) near++;
      }
      if (tot && near / tot > 0.3) for (let i = 0; i < tr.t.length; i++) pts.push({ t: tr.t[i], lat: tr.lat[i], lon: tr.lon[i] });
    }
    if (pts.length < 10) return null;
    const med = (off) => {
      const e = [];
      for (const p of pts) {
        const q = A.droneAt(drone, p.t + off);
        if (q) e.push(U.dist(q.lat, q.lon, p.lat, p.lon));
      }
      return e.length > pts.length * 0.3 ? U.stats(e).median : Infinity;
    };
    const curve = [];
    let best = 0, bestE = Infinity;
    for (let s = -opts.span; s <= opts.span; s += opts.coarse) {
      const e = med(s * 1000);
      curve.push([s, e]);
      if (e < bestE) { bestE = e; best = s; }
    }
    let fineBest = best;
    for (let s = best - opts.coarse; s <= best + opts.coarse + 1e-9; s += opts.fine) {
      const e = med(s * 1000);
      if (e < bestE) { bestE = e; fineBest = s; }
    }
    return { offsetMs: Math.round(fineBest * 1000), median: bestE, medianAtZero: med(0), points: pts.length, curve };
  };
})(typeof window !== 'undefined' ? window : globalThis);
