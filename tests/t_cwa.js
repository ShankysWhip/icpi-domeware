// CWA 18150 metrics on a made-up flight whose answers are worked out by hand.
//   jsc tests/jsc_prelude.js tests/t_cwa.js
// The drone flies east from 200 m to 1200 m of the radar at 10 m/s, 50 m up, for 100 s. Track A
// follows it 5 m north from 10 s to 60 s (12 m/s, alarm from 20 s), track B 5 m south from 47 s to
// 100 s (10 m/s, alarm all along), track C is a bird 600 m north with a false alarm from 30 s to 40 s.
// 50 plots sit 10 m north of the drone (10-59 s), 10 plots sit 500 m north of the radar and one
// clutter plot sits 40 m from the drone at 20.2 s.
need('util', 'radar', 'analysis', 'cwa');
done((async () => {
  const U = SF.util, A = SF.analysis, C = SF.cwa;
  const F = U.frame(65, 25), T0 = Date.UTC(2026, 9, 29, 12, 0, 0);
  const xAt = (s) => 200 + 10 * U.clamp(s, 0, 100);
  const d = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [] };
  for (let s = -5; s <= 105; s += 0.5) {
    d.t.push(T0 + s * 1000); d.lat.push(F.lat(0)); d.lon.push(F.lon(xAt(s)));
    const air = s >= 0 && s <= 100;
    d.h.push(air ? 50 : 0); d.alt.push(air ? 50 : 0); d.hs.push(air ? 10 : 0); d.vs.push(0); d.yaw.push(90);
  }
  const drone = { name: 'synthetic', product: 'Test drone', data: d };

  function radar(withFlags) {
    const st = SF.radar.newStore('robin', 'Test radar');
    const sensor = { id: '0', name: 'Test radar', lat: F.lat(0), lon: F.lon(0), ground: 0, alt: 0 };
    st.sensors.push(sensor); st.sensorMap.set('0', sensor);
    const up = (id, s, x, y, spd, cls, alarm) => SF.radar.addUpdate(st, id, { t: T0 + s * 1000, lat: F.lat(y), lon: F.lon(x), alt: 50, spd, hdg: 90, el: 0, cls, alarm: withFlags && alarm });
    for (let s = 10; s <= 60; s++) up('A', s, xAt(s), 5, 12, 'DRONE', s >= 20);
    for (let s = 47; s <= 100; s++) up('B', s, xAt(s), -5, 10, 'DRONE', true);
    for (let s = 0; s <= 100; s++) up('C', s, 0, 600, 0, 'BIRD', s >= 30 && s <= 40);
    const P = st.plots;
    for (let s = 10; s <= 59; s++) { P.t.push(T0 + s * 1000); P.lat.push(F.lat(10)); P.lon.push(F.lon(xAt(s))); P.alt.push(50); P.rcs.push(-20); }
    for (let s = 10; s <= 19; s++) { P.t.push(T0 + s * 1000); P.lat.push(F.lat(500)); P.lon.push(F.lon(0)); P.alt.push(50); P.rcs.push(-20); }
    // clutter 40 m from the drone, 0.2 s after a plot on it: inside the gate, but the nearer plot wins
    P.t.push(T0 + 20200); P.lat.push(F.lat(40)); P.lon.push(F.lon(xAt(20.2))); P.alt.push(50); P.rcs.push(-20);
    return SF.radar.finalize(st);
  }

  let n = 0, fails = 0;
  const near = (name, got, want, rel) => {
    n++;
    const ok = U.isNum(got) && Math.abs(got - want) <= Math.max(1e-9, Math.abs(want) * (rel || 1e-9));
    if (!ok) fails++;
    print((ok ? 'PASS ' : 'FAIL ') + name + ': ' + got + (ok ? '' : ' (want ' + want + ')'));
  };

  const opts = A.defaults();
  const st = radar(true);
  const an = A.run(drone, st, null, opts);
  const cw = C.run(drone, st, an, opts, C.defaults());
  const M = cw.metrics;
  near('steps in the flight', cw.steps, 101);
  near('auto AoI = farthest range + gate', cw.aoi, 1275, 0.01);
  print('     detections from ' + cw.detSource + ', alarms from ' + cw.alarmSource);

  // tracks: A is live in steps 7-63 (nearest update within 3 s), B in 44-100
  near('track completeness 94/101', M.completeness.value, 94 / 101);
  near('track continuity', M.continuity.value, 2);
  near('track ambiguity 114/94', M.ambiguity.value, 114 / 94);
  near('track spuriousness (the bird)', M.spurious.value, 1);
  // errors are taken at each update's own time, so the tracks are 5 m off throughout
  near('track positional accuracy (RMS)', M.posAcc.value, 5, 0.006);
  near('track velocity accuracy (RMS)', M.velAcc.value, Math.sqrt((57 * 4 + 25) / 114), 0.006);
  near('longest track segment 57/101', M.longest.value, 57 / 101);
  near('tracking immediateness = first update on the drone', M.immediate.value, 10);

  // alarms: A rightful in steps 20-63 (44), B in 44-100 (57), the bird false in 30-40 (11)
  near('FAR 11/112', M.far.value, 11 / 112);
  near('POD 81/101', M.pod.value, 81 / 101);
  near('MAR 20/101', M.mar.value, 20 / 101);
  const P = 101 / 112, R = 81 / 101;
  near('F1', M.f1.value, (2 * P * R) / (P + R));

  // detections: 50 of the 61 plots are on the drone, 10 m off; the clutter plot loses to the nearer one
  near('precision 50/61', M.precision.value, 50 / 61);
  near('location accuracy 10 m (clutter left out)', M.locAcc.value, 10, 0.006);
  const max = Math.hypot(790, 10) / 1200, min = Math.hypot(300, 10) / 200;
  near('range ratio max', M.rangeRatio.max, max, 0.006);
  near('range ratio min', M.rangeRatio.min, min, 0.006);
  near('range ratio value', M.rangeRatio.value, (max + 1 / min) / 2, 0.006);

  // scores with the default targets
  near('score: location accuracy at its target', M.locAcc.score, 1, 0.006);
  near('score: continuity (2 tracks, 1 best, 10 worst)', M.continuity.score, 8 / 9);
  near('score: ambiguity', M.ambiguity.score, 2 - 114 / 94);
  near('score: immediateness 10 s (5 best, 60 worst)', M.immediate.score, 50 / 55);
  near('score: FAR', M.far.score, 1 - 11 / 112);
  near('detection function = mean of its 3 scores', cw.fn.det, (M.locAcc.score + M.rangeRatio.score + M.precision.score) / 3, 1e-9);
  near('overall = mean of the 3 functions', cw.overall, (cw.fn.det + cw.fn.trk + cw.fn.id) / 3, 1e-9);
  near('defaults reported as defaults', cw.isDefault ? 1 : 0, 1);

  // a smaller AoI (700 m): the drone is inside in steps 0-50, tracked in 7-50
  const cw7 = C.run(drone, st, an, opts, { aoi: 700, ctx: { locAcc: { target: 5, worst: 15 }, precision: { weight: 0 } } });
  near('AoI 700 m: completeness 44/51', cw7.metrics.completeness.value, 44 / 51);
  near('own target: location accuracy 10 m on 5..15 m scores 0.5', cw7.metrics.locAcc.score, 0.5, 0.01);
  near('weight 0 drops precision from detection', cw7.fn.det, (cw7.metrics.locAcc.score + cw7.metrics.rangeRatio.score) / 2, 1e-9);
  near('changed targets reported', cw7.isDefault ? 1 : 0, 0);

  // a radar without alarm flags: drone labels are the alarms, so the bird raises none
  const st2 = radar(false);
  const an2 = A.run(drone, st2, null, opts);
  const cw2 = C.run(drone, st2, an2, opts, C.defaults());
  near('no flags: alarms from labels', cw2.alarmSource === 'labels' ? 1 : 0, 1);
  near('no flags: FAR 0', cw2.metrics.far.value, 0);
  near('no flags: POD = completeness', cw2.metrics.pod.value, 94 / 101);

  // hovering 40 m from the radar at 16 m for 30 s: plots 2 m off the drone in 20 of the 30 seconds,
  // and every second a clutter plot 8 m from the radar, inside the 75 m gate but nearer the radar
  const h = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [] };
  for (let s = -2; s <= 32; s += 0.5) {
    const air = s >= 0 && s <= 30;
    h.t.push(T0 + s * 1000); h.lat.push(F.lat(0)); h.lon.push(F.lon(40)); h.h.push(air ? 16 : 0); h.alt.push(air ? 16 : 0); h.hs.push(0); h.vs.push(0); h.yaw.push(0);
  }
  const hover = { name: 'hover', data: h };
  const st3 = SF.radar.newStore('robin', 'Test radar');
  const s3 = { id: '0', name: 'Test radar', lat: F.lat(0), lon: F.lon(0), ground: 0, alt: 0 };
  st3.sensors.push(s3); st3.sensorMap.set('0', s3);
  for (let s = 0; s <= 30; s++) SF.radar.addUpdate(st3, 'D', { t: T0 + s * 1000, lat: F.lat(3), lon: F.lon(40), alt: 16, cls: 'DRONE', alarm: true });
  const P3 = st3.plots;
  const plot = (s, x, y, a) => { P3.t.push(T0 + s * 1000); P3.lat.push(F.lat(y)); P3.lon.push(F.lon(x)); P3.alt.push(a); P3.rcs.push(-20); };
  for (let s = 0; s < 30; s++) { if (s % 3) plot(s, 40, 2, 16); plot(s + 0.4, 8, 0, 2); }
  SF.radar.finalize(st3);
  const an3 = A.run(hover, st3, null, opts), cw3 = C.run(hover, st3, an3, opts, C.defaults());
  near('near the radar: clutter is never the drone (precision 20/50)', cw3.metrics.precision.value, 20 / 50);
  near('near the radar: location accuracy 2 m', cw3.metrics.locAcc.value, 2, 0.006);
  near('near the radar: min range ratio about 1', cw3.metrics.rangeRatio.min, Math.hypot(40, 2) / 40, 0.006);

  print('\n' + (n - fails) + '/' + n + ' passed');
  if (fails) throw new Error(fails + ' failed');
})());
