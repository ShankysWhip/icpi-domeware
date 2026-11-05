// Several drones against one radar: each update goes to the nearest drone, CWA 18150 pools them.
//   jsc tests/jsc_prelude.js tests/t_fleet.js
// Two drones fly east side by side for 100 s, 50 m up: D1 on y = 0 from 200 m, D2 on y = 40 m (inside
// the 75 m gate of each other). Track A follows D1 5 m off (whole flight, alarm), track B follows D2
// 5 m off but only from 50 s (alarm), track C is a bird 600 m north (no alarm). No plots, so the track
// updates are the detections.
need('util', 'radar', 'analysis', 'cwa');
done((async () => {
  const U = SF.util, A = SF.analysis, C = SF.cwa;
  const F = U.frame(65, 25), T0 = Date.UTC(2026, 9, 29, 12, 0, 0);
  const xAt = (s) => 200 + 10 * U.clamp(s, 0, 100);
  const drone = (y, name) => {
    const d = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [] };
    for (let s = -5; s <= 105; s += 0.5) {
      const air = s >= 0 && s <= 100;
      d.t.push(T0 + s * 1000); d.lat.push(F.lat(y)); d.lon.push(F.lon(xAt(s))); d.h.push(air ? 50 : 0); d.alt.push(air ? 50 : 0); d.hs.push(air ? 10 : 0); d.vs.push(0); d.yaw.push(90);
    }
    return { name, data: d };
  };
  const D1 = drone(0, 'D1'), D2 = drone(40, 'D2');
  const st = SF.radar.newStore('robin', 'Test radar');
  const sensor = { id: '0', name: 'Test radar', lat: F.lat(0), lon: F.lon(0), ground: 0, alt: 0 };
  st.sensors.push(sensor); st.sensorMap.set('0', sensor);
  const up = (id, s, x, y, cls, alarm) => SF.radar.addUpdate(st, id, { t: T0 + s * 1000, lat: F.lat(y), lon: F.lon(x), alt: 50, spd: 10, hdg: 90, el: 0, cls, alarm });
  for (let s = 0; s <= 100; s++) up('A', s, xAt(s), 5, 'DRONE', true);
  for (let s = 50; s <= 100; s++) up('B', s, xAt(s), 35, 'DRONE', true);
  for (let s = 0; s <= 100; s++) up('C', s, 0, 600, 'BIRD', false);
  SF.radar.finalize(st);

  let n = 0, fails = 0;
  const near = (name, got, want, tol) => {
    n++;
    const ok = U.isNum(got) && Math.abs(got - want) <= (tol || 1e-9);
    if (!ok) fails++;
    print((ok ? 'PASS ' : 'FAIL ') + name + ': ' + got + (ok ? '' : ' (want ' + want + ')'));
  };
  const opts = A.defaults();
  // alone, D1's analysis would also claim track B (35 m from it); with D2 in the air B is D2's
  const lone = A.run(D1, st, null, opts);
  near('alone: D1 claims both A and B', lone.matchedIds.length, 2);
  const a1 = A.run(D1, st, null, Object.assign({}, opts, { others: [D2] }));
  const a2 = A.run(D2, st, null, Object.assign({}, opts, { others: [D1] }));
  near('fleet: D1 keeps only track A', a1.matchedIds.length === 1 && a1.matchedIds[0] === 'A' ? 1 : 0, 1);
  near('fleet: D2 gets track B', a2.matchedIds.length === 1 && a2.matchedIds[0] === 'B' ? 1 : 0, 1);
  near('fleet: D1 tracked the whole flight', a1.coverage.detected, 1);
  near('fleet: D2 tracked from 47 s (B from 50 s, ±3 s)', a2.coverage.detected, 54 / 101);

  const cw = C.run([D1, D2], st, [a1, a2], opts, C.defaults());
  const M = cw.metrics;
  near('two true objects', cw.drones, 2);
  near('completeness pooled over both: (101 + 54) / 202', M.completeness.value, 155 / 202);
  near('continuity: one track per drone', M.continuity.value, 1);
  near('ambiguity: never two tracks on one drone', M.ambiguity.value, 1);
  near('spuriousness: only the bird', M.spurious.value, 1);
  near('positional accuracy 5 m', M.posAcc.value, 5, 0.03);
  near('POD pooled (alarms on both)', M.pod.value, 155 / 202);
  near('FAR 0 (the bird raised none)', M.far.value, 0);
  near('immediateness: mean of 0 s and 47 s... first update at 0 s and 50 s', M.immediate.value, 25);
  near('longest segment: mean of 101/101 and 51/101', M.longest.value, (1 + 54 / 101) / 2);
  near('per-drone completeness D2', cw.objects[1].completeness, 54 / 101);
  near('precision: updates of A and B are drones, the bird is not', M.precision.value, (101 + 51) / (101 + 51 + 101));

  print('\n' + (n - fails) + '/' + n + ' passed');
  if (fails) throw new Error(fails + ' failed');
})());
