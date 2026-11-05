/* Orbit: CWA 18150 performance evaluation.
   CWA 18150:2024 "Unmanned aircraft systems - Counter UAS - Testing methodology" (CEN Workshop
   Agreement), clause 13.4.7, defines detection metrics (Table 65), tracking metrics (Table 66) and
   identification metrics (Table 67). Clause 13.4.6 turns every measured metric into a score from
   0 (worst) to 1 (best) with a scoring context the end user agrees, and averages the scores per
   function, optionally weighted. Here the drone's own log is the true object and the area of
   interest (AoI) is a circle round the radar. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const C = SF.cwa = {};

  // kind: 'frac' (shown as %), 'num' (with unit), 'ratio', 'f1'. target scores 1, worst scores 0.
  C.METRICS = [
    { id: 'locAcc', fn: 'det', name: 'Location accuracy', kind: 'num', unit: 'm', d: 1, target: 10, worst: 75,
      def: 'Mean horizontal distance between the detections that represent the drone and the drone (3D in brackets).' },
    { id: 'rangeRatio', fn: 'det', name: 'Range ratio', kind: 'ratio', target: 1, worst: 0,
      def: 'Farthest and nearest detection of the drone, divided by the drone\'s farthest and nearest distance from the radar in the AoI. Scored on the mean of the max ratio and 1 / min ratio, each capped at 1.' },
    { id: 'precision', fn: 'det', name: 'Precision', kind: 'frac', target: 1, worst: 0,
      def: 'Share of all detections in the AoI that represent the drone.' },
    { id: 'completeness', fn: 'trk', name: 'Track completeness', kind: 'frac', target: 1, worst: 0,
      def: 'Share of the time in the AoI during which at least one track represents the drone.' },
    { id: 'continuity', fn: 'trk', name: 'Track continuity', kind: 'num', unit: 'tracks', d: 0, target: 1, worst: 10,
      def: 'Number of different tracks that represented the drone.' },
    { id: 'ambiguity', fn: 'trk', name: 'Track ambiguity', kind: 'num', unit: 'tracks', d: 2, target: 1, worst: 2,
      def: 'Time-weighted average number of tracks on the drone while it has at least one.' },
    { id: 'spurious', fn: 'trk', name: 'Track spuriousness', kind: 'num', unit: 'tracks', d: 2, target: 0, worst: 10,
      def: 'Time-weighted average number of tracks in the AoI that do not represent the drone (birds and clutter count too).' },
    { id: 'velAcc', fn: 'trk', name: 'Track velocity accuracy', kind: 'num', unit: 'm/s', d: 1, target: 1, worst: 10,
      def: 'RMS difference between the horizontal velocity of the tracks on the drone and the drone\'s own.' },
    { id: 'posAcc', fn: 'trk', name: 'Track positional accuracy', kind: 'num', unit: 'm', d: 1, target: 10, worst: 75,
      def: 'RMS horizontal distance between the tracks on the drone and the drone.' },
    { id: 'longest', fn: 'trk', name: 'Longest track segment', kind: 'frac', target: 1, worst: 0,
      def: 'Largest share of the time in the AoI during which one and the same track represented the drone.' },
    { id: 'immediate', fn: 'trk', name: 'Tracking immediateness', kind: 'num', unit: 's', d: 0, target: 5, worst: 60,
      def: 'Time from the drone entering the AoI to its first track.' },
    { id: 'f1', fn: 'id', name: 'F1 score', kind: 'f1', target: 1, worst: 0,
      def: 'Harmonic mean of the alarm precision (1 − FAR) and the POD.' },
    { id: 'far', fn: 'id', name: 'False alarm rate (FAR)', kind: 'frac', target: 0, worst: 1,
      def: 'Share of the alarms that were false: alarms on tracks in the AoI that did not represent the drone.' },
    { id: 'mar', fn: 'id', name: 'Missed alarm rate (MAR)', kind: 'frac', target: 0, worst: 1,
      def: 'Share of the time in the AoI without a rightful alarm on the drone.' },
    { id: 'pod', fn: 'id', name: 'Probability of detection (POD)', kind: 'frac', target: 1, worst: 0,
      def: 'Share of the time in the AoI with a rightful alarm on the drone.' },
  ];
  C.FUNCTIONS = [['det', 'Detection'], ['trk', 'Tracking'], ['id', 'Identification']];
  C.byId = (id) => C.METRICS.find((m) => m.id === id);

  // aoi: radius round the radar in m (0 = just wide enough for the whole flight);
  // ctx: per-metric scoring context {target, worst, weight} where it differs from the defaults
  C.defaults = () => ({ aoi: 0, ctx: {} });
  C.context = function (cfg, id) {
    const m = C.byId(id), o = (cfg && cfg.ctx && cfg.ctx[id]) || {};
    return { target: U.isNum(o.target) ? o.target : m.target, worst: U.isNum(o.worst) ? o.worst : m.worst, weight: U.isNum(o.weight) ? o.weight : 1 };
  };
  C.isDefault = (cfg) => !(cfg && cfg.aoi > 0) && C.METRICS.every((m) => {
    const c = C.context(cfg, m.id);
    return c.target === m.target && c.worst === m.worst && c.weight === 1;
  });
  // Linear between the worst value (score 0) and the target (score 1), for either direction.
  C.score = function (v, target, worst) {
    if (!U.isNum(v)) return 0;
    if (target === worst) return (target > 0 ? v >= target : v <= target) ? 1 : 0;
    return U.clamp((v - worst) / (target - worst), 0, 1);
  };
  // Scoring-context values are entered and shown in the metric's display units (% for shares).
  C.toDisplay = (m, x) => (m.kind === 'frac' ? +(x * 100).toFixed(4) : x);
  C.fromDisplay = (m, x) => (m.kind === 'frac' ? x / 100 : x);
  const withUnit = (m, s) => s + ' ' + (m.unit === 'tracks' && s === '1' ? 'track' : m.unit);
  C.ctxText = function (m, x) {
    if (!U.isNum(x)) return '–';
    if (m.kind === 'frac') return +(x * 100).toFixed(1) + ' %';
    if (m.kind === 'num') return withUnit(m, String(+x.toFixed(2)));
    return x.toFixed(2);
  };
  C.valueText = function (m, r) {
    if (!r || r.undef) return 'undefined' + (r && r.why ? ' (' + r.why + ')' : '');
    if (m.kind === 'frac') return (r.value * 100).toFixed(1) + ' %' + (r.extra || '');
    if (m.kind === 'f1') return r.value.toFixed(2);
    if (m.kind === 'ratio') return 'max ' + r.max.toFixed(2) + ' · min ' + r.min.toFixed(2) + (r.extra || '');
    return withUnit(m, r.value.toFixed(U.isNum(r.d) ? r.d : m.d)) + (r.extra || '');
  };
  C.grade = (x) => (x >= 0.8 ? 'good' : x >= 0.5 ? 'mid' : 'poor');

  // horizontal velocity (m/s east, north) of the true drone, from its positions 1 s apart
  function trueVel(drone, t) {
    const a = SF.analysis.droneAt(drone, t - 500), b = SF.analysis.droneAt(drone, t + 500);
    if (!a || !b) return null;
    const f = U.frame(a.lat, a.lon);
    return { e: f.x(b.lon), n: f.y(b.lat) };
  }
  // horizontal velocity of a track: the radar's own when the file has it, else from neighbouring updates
  function trackVel(tr, i) {
    if (tr.spd && U.isNum(tr.spd[i]) && U.isNum(tr.hdg[i])) {
      const hs = tr.spd[i] * Math.cos((U.isNum(tr.el[i]) ? tr.el[i] : 0) * U.D2R);
      return { e: hs * Math.sin(tr.hdg[i] * U.D2R), n: hs * Math.cos(tr.hdg[i] * U.D2R) };
    }
    const a = Math.max(0, i - 1), b = Math.min(tr.t.length - 1, i + 1), dt = (tr.t[b] - tr.t[a]) / 1000;
    if (b === a || !(dt > 0) || dt > 10) return null;
    const f = U.frame(tr.lat[a], tr.lon[a]);
    return { e: f.x(tr.lon[b]) / dt, n: f.y(tr.lat[b]) / dt };
  }

  /* All CWA 18150 metrics for one flight. drones is one drone or a list of drones flying together, and
     ans their results of SF.analysis.run, whose tracks that follow a drone are the ones that can
     represent it. With several drones every drone is a true object and the metrics are pooled over all
     of them; a track or a detection represents at most one drone, the nearest. Null without truth and radar. */
  C.run = function (drones, radar, ans, opts, cfg) {
    if (!Array.isArray(drones)) { drones = drones ? [drones] : []; ans = ans ? [ans] : []; }
    if (!radar || !drones.length || drones.some((d, k) => !d || !ans[k] || !ans[k].window || !ans[k].trackInfo || !ans[k].series)) return null;
    const A = SF.analysis;
    cfg = Object.assign(C.defaults(), cfg || {});
    const sensor = ans[0].sensor, gate = opts.gate, tol = opts.tol * 1000, multi = drones.length > 1;
    const W0 = Math.min(...ans.map((an) => an.window.t0)), W1 = Math.max(...ans.map((an) => an.window.t1));
    const step = Math.max(1000, Math.ceil((W1 - W0) / 20000 / 1000) * 1000); // as in SF.analysis.run
    const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : sensor && U.isNum(sensor.alt) ? sensor.alt : NaN;
    const maxRange = Math.max(...ans.map((an) => (an.flight && an.flight.maxRange) || 0));
    const aoi = sensor ? (cfg.aoi > 0 ? cfg.aoi : maxRange + gate) : NaN;
    const rangeOf = (lat, lon) => (sensor ? U.dist(sensor.lat, sensor.lon, lat, lon) : NaN);
    const inAoi = (lat, lon) => !sensor || rangeOf(lat, lon) <= aoi;
    const matched = ans.map((an) => new Set(an.matchedIds));
    // an alarm is the radar's own alarm flag when the file has one (Robin), else a drone label
    const flag = radar.alarms > 0;
    const alarmed = (tr, i) => (flag ? !!(tr.alarm && tr.alarm[i]) : A.isDroneLabel(tr.cls[i], opts.labels));
    const tracks = radar.tracks.filter((tr) => tr.last >= W0 - tol && tr.first <= W1 + tol);
    // the drones in the air at t: their positions, null for a drone on the ground or outside its log
    const airborne = (t) => drones.map((d, k) => (t >= ans[k].window.t0 && t <= ans[k].window.t1 ? A.droneAt(d, t) : null));
    const O = drones.map(() => ({ inSteps: 0, tracked: 0, repSum: 0, rightful: 0, tIn: NaN, tFirst: Infinity, rMin: Infinity, rMax: 0, ids: new Map(), nDet: 0, dMin: Infinity, dMax: 0 }));

    // ---- tracks and alarms, second by second over the airborne time. A track represents a drone when it
    // is one of the tracks that follow that drone and its nearest update (within ±tol) is within the gate
    // of the drone at that update's own time; every other live track in the AoI is spurious.
    let steps = 0, spurSum = 0, alarmTP = 0, alarmFP = 0;
    const posErr = [], velErr = [];
    for (let t = W0; t <= W1; t += step) {
      const P = airborne(t);
      if (!P.some(Boolean)) continue; // no drone in the air, or a gap in the logs: nothing to compare with
      steps++;
      const here = P.map((p) => !!p && inAoi(p.lat, p.lon));
      for (let k = 0; k < drones.length; k++) {
        if (!here[k]) continue;
        const o = O[k], r = rangeOf(P[k].lat, P[k].lon);
        o.inSteps++;
        if (!U.isNum(o.tIn)) o.tIn = t;
        if (r < o.rMin) o.rMin = r;
        if (r > o.rMax) o.rMax = r;
      }
      const nRep = drones.map(() => 0), ok = drones.map(() => false);
      let nSpur = 0;
      for (const tr of tracks) {
        if (t < tr.first - tol || t > tr.last + tol) continue;
        const i = U.idxNear(tr.t, t);
        if (i < 0 || Math.abs(tr.t[i] - t) > tol || !inAoi(tr.lat[i], tr.lon[i])) continue;
        let k = -1, d = Infinity;
        for (let j = 0; j < drones.length; j++) {
          if (!here[j] || !matched[j].has(tr.id)) continue;
          const pu = A.droneAt(drones[j], tr.t[i]);
          const dj = pu ? U.dist(pu.lat, pu.lon, tr.lat[i], tr.lon[i]) : Infinity;
          if (dj <= gate && dj < d) { d = dj; k = j; }
        }
        const al = alarmed(tr, i);
        if (k >= 0) {
          const o = O[k];
          nRep[k]++;
          o.ids.set(tr.id, (o.ids.get(tr.id) || 0) + 1);
          posErr.push(d);
          if (tr.t[i] < o.tFirst) o.tFirst = tr.t[i];
          const vT = trueVel(drones[k], tr.t[i]), vR = vT ? trackVel(tr, i) : null;
          if (vR) velErr.push(Math.hypot(vR.e - vT.e, vR.n - vT.n));
          if (al) { alarmTP++; ok[k] = true; }
        } else {
          nSpur++;
          if (al) alarmFP++;
        }
      }
      spurSum += nSpur;
      for (let k = 0; k < drones.length; k++) {
        if (nRep[k]) { O[k].tracked++; O[k].repSum += nRep[k]; }
        if (ok[k]) O[k].rightful++;
      }
    }

    // ---- detections: the radar's own plots when the file has them for the flight, else every track update
    const PL = radar.plots || { t: [] }, det = [];
    for (let k = 0; k < PL.t.length; k++) if (PL.t[k] >= W0 && PL.t[k] <= W1) det.push({ t: PL.t[k], lat: PL.lat[k], lon: PL.lon[k], alt: PL.alt[k] });
    const fromPlots = det.length > 0;
    if (!fromPlots) {
      for (const tr of tracks) {
        for (let i = 0; i < tr.t.length; i++) if (tr.t[i] >= W0 && tr.t[i] <= W1) det.push({ t: tr.t[i], lat: tr.lat[i], lon: tr.lon[i], alt: tr.alt[i], id: tr.id });
      }
    }
    // With a drone near the radar the radar's own clutter falls inside the gate too: a plot nearer the
    // radar than the drone (in 3D when the heights are known) is clutter, not the drone.
    const antenna = sensor && U.isNum(sensor.alt) ? sensor.alt : ground;
    const nearerRadar = (x, p, d) => {
      if (!sensor) return false;
      let toDrone = d, toRadar = rangeOf(x.lat, x.lon);
      if (U.isNum(x.alt) && U.isNum(ground) && U.isNum(antenna)) {
        toDrone = Math.hypot(toDrone, x.alt - ground - p.h);
        toRadar = Math.hypot(toRadar, x.alt - antenna);
      }
      return toRadar < toDrone;
    };
    const cand = [];
    for (const x of det) {
      if (!inAoi(x.lat, x.lon)) continue;
      const P = airborne(x.t);
      if (!P.some(Boolean)) continue;
      x.k = -1; x.d = Infinity;
      for (let j = 0; j < drones.length; j++) {
        const p = P[j];
        // a track update can be a drone only on a track that follows it (as for the tracking metrics)
        if (!p || !inAoi(p.lat, p.lon) || (!fromPlots && !matched[j].has(x.id))) continue;
        const d = U.dist(p.lat, p.lon, x.lat, x.lon);
        if (d <= gate && d < x.d && !(fromPlots && nearerRadar(x, p, d))) { x.k = j; x.d = d; x.p = p; }
      }
      cand.push(x);
    }
    // Plots have no track to tell them apart. As in one radar scan, only the plot nearest a drone
    // within ±0.5 s can be that drone.
    if (fromPlots) {
      cand.sort((a, b) => a.t - b.t);
      for (let k = 0; k < cand.length; k++) {
        const c = cand[k];
        if (c.k < 0) continue;
        for (let j = k - 1; j >= 0 && c.t - cand[j].t <= 500; j--) if (cand[j].k === c.k && cand[j].d < c.d) { c.lost = true; break; }
        for (let j = k + 1; !c.lost && j < cand.length && cand[j].t - c.t <= 500; j++) if (cand[j].k === c.k && cand[j].d < c.d) c.lost = true;
      }
    }
    const nDet = cand.length, d2 = [], d3 = [];
    for (const x of cand) {
      if (x.k < 0 || x.lost) continue;
      d2.push(x.d);
      if (U.isNum(ground) && U.isNum(x.alt)) d3.push(Math.hypot(x.d, x.alt - ground - x.p.h));
      const o = O[x.k], r = rangeOf(x.lat, x.lon);
      o.nDet++;
      if (r < o.dMin) o.dMin = r;
      if (r > o.dMax) o.dMax = r;
    }

    // ---- metric values, pooled over the drones (time-weighted where the metric is a share of time)
    const V = {}, undef = (why) => ({ undef: true, why });
    const sum = (f) => O.reduce((s, o) => s + f(o), 0), mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;
    const inSteps = sum((o) => o.inSteps), tracked = sum((o) => o.tracked), repSum = sum((o) => o.repSum), rightful = sum((o) => o.rightful);
    const objs = O.filter((o) => o.inSteps > 0), seen = objs.filter((o) => o.ids.size), each = multi ? ' (mean of ' + objs.length + ' drones)' : '';
    const s2 = U.stats(d2), s3 = U.stats(d3), sv = U.stats(velErr), sp = U.stats(posErr);
    V.locAcc = s2.n ? { value: s2.mean, extra: s3.n ? ' (3D ' + s3.mean.toFixed(1) + ' m)' : '', n: s2.n } : undef('no detection of the drone');
    const ranged = objs.filter((o) => o.nDet && o.rMax > 0);
    if (!sensor) V.rangeRatio = undef('radar position unknown');
    else if (!ranged.length) V.rangeRatio = undef('no detection of the drone');
    else {
      const ratio = (o) => [o.dMax / o.rMax, o.dMin / Math.max(o.rMin, 1)];
      const cover = (o) => { if (!(o.nDet && o.rMax > 0)) return 0; const [mx, mn] = ratio(o); return (Math.min(1, mx) + Math.min(1, 1 / mn)) / 2; };
      V.rangeRatio = { value: mean(objs.map(cover)), max: mean(ranged.map((o) => ratio(o)[0])), min: mean(ranged.map((o) => ratio(o)[1])), extra: each };
    }
    V.precision = nDet ? { value: s2.n / nDet, n: nDet } : undef('no detections');
    V.completeness = inSteps ? { value: tracked / inSteps } : undef('drone never in the AoI');
    V.continuity = seen.length ? { value: mean(seen.map((o) => o.ids.size)), d: multi ? 1 : 0, extra: multi ? ' per drone' : '' } : undef('no track on the drone');
    V.ambiguity = tracked ? { value: repSum / tracked } : undef('no track on the drone');
    V.spurious = steps ? { value: spurSum / steps } : undef('no data');
    V.velAcc = sv.n ? { value: sv.rms, n: sv.n } : undef('no track on the drone');
    V.posAcc = sp.n ? { value: sp.rms, n: sp.n } : undef('no track on the drone');
    const most = (o) => Math.max(0, ...o.ids.values());
    V.longest = seen.length ? { value: mean(objs.map((o) => most(o) / o.inSteps)), extra: each } : undef('no track on the drone');
    V.immediate = seen.length && seen.every((o) => isFinite(o.tFirst)) ? { value: mean(seen.map((o) => Math.max(0, o.tFirst - o.tIn) / 1000)), extra: multi ? ' (mean of the tracked drones)' : '' } : undef('never tracked');
    const nAl = alarmTP + alarmFP;
    V.far = nAl ? { value: alarmFP / nAl, n: nAl } : undef('no alarms');
    V.pod = inSteps ? { value: rightful / inSteps } : undef('drone never in the AoI');
    V.mar = inSteps ? { value: 1 - rightful / inSteps } : undef('drone never in the AoI');
    const prec = nAl ? alarmTP / nAl : NaN, rec = inSteps ? rightful / inSteps : NaN;
    V.f1 = !U.isNum(rec) ? undef('drone never in the AoI') : { value: U.isNum(prec) && prec + rec > 0 ? (2 * prec * rec) / (prec + rec) : 0 };

    // ---- scores: undefined metrics score 0; functions are weighted means, the overall score their mean
    const res = {
      metrics: {}, fn: {}, overall: NaN, aoi, aoiAuto: !(cfg.aoi > 0), detSource: fromPlots ? 'plots' : 'updates', detections: nDet,
      alarmSource: flag ? 'flag' : 'labels', alarms: nAl, steps, aoiSteps: inSteps, step, gate: opts.gate, tol: opts.tol, isDefault: C.isDefault(cfg), drones: drones.length,
      // per drone, for fleet tables
      objects: O.map((o) => ({ completeness: o.inSteps ? o.tracked / o.inSteps : NaN, pod: o.inSteps ? o.rightful / o.inSteps : NaN, tracks: o.ids.size, immediate: isFinite(o.tFirst) ? Math.max(0, o.tFirst - o.tIn) / 1000 : NaN, detections: o.nDet })),
    };
    for (const m of C.METRICS) {
      const v = V[m.id], c = C.context(cfg, m.id);
      res.metrics[m.id] = Object.assign({}, v, { target: c.target, worst: c.worst, weight: c.weight, score: v.undef ? 0 : C.score(v.value, c.target, c.worst) });
    }
    const fs = [];
    for (const [fn] of C.FUNCTIONS) {
      let sw = 0, s = 0;
      for (const m of C.METRICS) if (m.fn === fn) { const r = res.metrics[m.id]; sw += r.weight; s += r.weight * r.score; }
      res.fn[fn] = sw > 0 ? s / sw : NaN;
      if (U.isNum(res.fn[fn])) fs.push(res.fn[fn]);
    }
    res.overall = fs.length ? fs.reduce((a, b) => a + b, 0) / fs.length : NaN;
    return res;
  };

  // One paragraph on how the numbers were made, for the Analysis tab and the report.
  C.notesText = function (cw, radarName) {
    const parts = [];
    parts.push('Area of interest: ' + (U.isNum(cw.aoi) ? 'within ' + U.fmtDist(cw.aoi) + ' of the radar' + (cw.aoiAuto ? ' (just wide enough for the whole flight)' : '') : 'the whole flight (radar position unknown)') + '.');
    if (cw.drones > 1) parts.push(cw.drones + ' drones flew together; each is a true object and the metrics are pooled over all of them. A track or a detection represents at most one drone, the nearest.');
    parts.push('A track represents the drone in a given second when it follows the drone and its nearest update within ±' + cw.tol + ' s lies within ' + cw.gate + ' m of the drone\'s logged position at that update\'s time; every other track in the area counts as spurious.');
    parts.push('Detections: ' + cw.detections + ' ' + (cw.detSource === 'plots' ? 'plots from the radar file (' + (radarName || 'radar') + ' potential-drone plots); within ±0.5 s only the plot nearest the drone can represent it, and a plot nearer the radar than the drone counts as the radar\'s own clutter' : 'track updates (the file has no separate detections)') + '.');
    parts.push('Alarms: ' + (cw.alarmSource === 'flag' ? 'the radar\'s own alarm flag' : 'updates carrying a drone label') + ', counted per track and second.');
    parts.push(cw.isDefault ? 'Scoring targets are Orbit\'s defaults, not part of CWA 18150: agree them with the end user before the test.' : 'Scoring targets were set for this test.');
    return parts.join(' ');
  };
})(typeof window !== 'undefined' ? window : globalThis);
