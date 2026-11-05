// ArduPilot readers on the made-up logs from make_ardupilot.py (the flights are described there).
//   jsc tests/jsc_prelude.js tests/t_ardupilot.js
need('util', 'xml', 'tracks', 'ardupilot', 'analysis');
done((async () => {
  const U = SF.util, AP = SF.ardupilot, A = SF.analysis;
  const F = U.frame(65, 25), BOOT = Date.UTC(2026, 9, 29, 11, 59, 30), T0 = Date.UTC(2026, 9, 29, 12, 0, 0);
  let n = 0, fails = 0;
  const within = (name, got, want, tol) => {
    n++;
    const ok = (typeof want === 'number' ? U.isNum(got) && Math.abs(got - want) <= tol : got === want);
    if (!ok) fails++;
    print((ok ? 'PASS ' : 'FAIL ') + name + ': ' + got + (ok ? '' : ' (want ' + want + ')'));
  };
  const hasEvent = (d, text, at) => d.events.some((e) => e.text === text && Math.abs(e.t - at) <= 600);

  // ---- DataFlash .BIN with EKF positions
  const bin = bytesOf(FIX + 'SYNTHETIC_ArduCopter_sysid2.BIN');
  within('.BIN recognised', AP.isBin(bin) && !AP.isTlog(bin), true);
  const d = AP.binToDrone(bin, 'SYNTHETIC_ArduCopter_sysid2.BIN');
  within('first position on UTC from GPS week + ms (boot +20 s)', d.data.t[0], BOOT + 20000, 1);
  within('POS at 10 Hz from 20 s to 150 s', d.data.t.length, 1301, 0);
  within('firmware from MSG', d.product, 'ArduCopter 4.6.2');
  within('drone named by SYSID_THISMAV', d.label, 'sysid 2');
  const p60 = A.droneAt(d, BOOT + 60000);
  within('east at +60 s (100 m into the first leg)', F.x(p60.lon), 100, 0.2);
  within('north at +60 s', F.y(p60.lat), 0, 0.2);
  within('height above home (RelHomeAlt)', p60.h, 30, 0.01);
  within('heading from ATT', p60.yaw, 90, 0.5);
  within('ground speed from GPS', p60.hs, 10, 0.05);
  const p100 = A.droneAt(d, BOOT + 100000);
  within('west leg at +100 s: east 100 m', F.x(p100.lon), 100, 0.2);
  within('west leg at +100 s: north 200 m', F.y(p100.lat), 200, 0.2);
  within('flight mode at +60 s', d.data.mode[U.idxNear(d.data.t, BOOT + 60000)], 'Auto');
  const w = A.flightWindow(d, 2);
  within('airborne from about +40.7 s', (w.t0 - BOOT) / 1000, 40.7, 0.15);
  within('airborne until about +139.4 s', (w.t1 - BOOT) / 1000, 139.4, 0.15);
  within('event: armed at +40 s', hasEvent(d, 'Armed', BOOT + 40000), true);
  within('event: Auto at +41 s', hasEvent(d, 'Flight mode: Auto', BOOT + 41000), true);
  within('event: landed at +140 s', hasEvent(d, 'Landed', BOOT + 140000), true);
  within('event: disarmed at +145 s', hasEvent(d, 'Disarmed', BOOT + 145000), true);
  within('no duplicate arming from EV records', d.events.filter((e) => e.text === 'Armed').length, 1, 0);

  // ---- the same flight without POS: the GPS fixes are the positions
  const g = AP.binToDrone(bytesOf(FIX + 'SYNTHETIC_ArduCopter_sysid2_noPOS.BIN'), 'noPOS.BIN');
  within('GPS fallback: 3D fixes at 5 Hz from 10 s', g.data.t.length, 701, 0);
  within('GPS fallback says so', /GPS/.test(g.note), true);
  within('GPS fallback: height from altitude above the first fix', A.droneAt(g, BOOT + 60000).h, 30, 0.02);

  // ---- telemetry .tlog with two vehicles (MAVLink 2 and MAVLink 1), the GCS, junk and a bad CRC
  const tl = bytesOf(FIX + 'SYNTHETIC_fleet_sysid3_4.tlog');
  within('.tlog recognised', AP.isTlog(tl) && !AP.isBin(tl), true);
  const fleet = AP.tlogToDrones(tl, 'fleet.tlog');
  within('two drones, not the ground station', fleet.map((x) => x.label).join(', '), 'sysid 3, sysid 4');
  within('sysid 3: 401 positions (bad CRC packet dropped)', fleet[0].data.t.length, 401, 0);
  within('sysid 4 (MAVLink 1): 401 positions', fleet[1].data.t.length, 401, 0);
  const a = A.droneAt(fleet[0], T0 + 40000), b = A.droneAt(fleet[1], T0 + 40000);
  within('sysid 3 east at +40 s', F.x(a.lon), 300, 0.2);
  within('sysid 4 is 60 m north', F.y(b.lat), 60, 0.2);
  within('sysid 3 height', a.h, 25, 0.01);
  within('sysid 4 height', b.h, 40, 0.01);
  within('speed from vx/vy', a.hs, 10, 0.01);
  within('heading from hdg', a.yaw, 90, 0.01);
  within('trimmed MAVLink 2 payload: heading 0 while still', fleet[0].data.yaw[0], 0, 0);
  within('mode from heartbeat', fleet[0].data.mode[U.idxNear(fleet[0].data.t, T0 + 40000)], 'Auto');
  within('event: armed at +8 s', hasEvent(fleet[0], 'Armed', T0 + 8050), true);
  within('event: disarmed at +72 s', hasEvent(fleet[1], 'Disarmed', T0 + 72050), true);
  within('firmware from STATUSTEXT', fleet[0].product, 'ArduCopter 4.6.2');
  within('warning text kept as a warning', fleet[0].events.some((e) => e.kind === 'warn' && e.text === 'Radio failsafe test'), true);

  print('\n' + (n - fails) + '/' + n + ' passed');
  if (fails) throw new Error(fails + ' failed');
})());
