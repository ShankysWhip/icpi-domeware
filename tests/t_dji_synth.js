need('util', 'xml', 'aes', 'dji');
done((async () => {
  const F = FIX;
  const log = SF.dji.open(bytesOf(F + 'SYNTHETIC_DJIFlightRecord_v14.txt'));
  const keys = JSON.parse(readFile(F + 'SYNTHETIC_DJI_v14_keys.json'));
  print('version', log.version, 'encrypted', log.encrypted, 'aircraft', log.details.aircraftName, 'start', new Date(log.details.startTime).toISOString());
  const req = SF.dji.keychainsRequest(log);
  print('request', req.version, req.department, req.keychainsArray[0].map((k) => k.featurePoint).join(','));
  const d = await SF.dji.decode(log, keys.keychains);
  const D = d.data;
  print('stats', JSON.stringify(d.stats));
  print('points', D.t.length, 'home', JSON.stringify(d.home));
  let maxErr = 0, maxDt = 0, maxDh = 0;
  for (const [t, lat, lon, h] of keys.truth) {
    const i = D.t.indexOf(t);
    if (i < 0) { print('missing time', t); continue; }
    maxErr = Math.max(maxErr, SF.util.dist(lat, lon, D.lat[i], D.lon[i]));
    maxDh = Math.max(maxDh, Math.abs(Math.round(h * 10) / 10 - D.h[i]));
  }
  print('truth check: max position error', maxErr.toFixed(4), 'm; max height error', maxDh.toFixed(2), 'm');
  print('sample', JSON.stringify({ t: new Date(D.t[150]).toISOString(), yaw: D.yaw[150], gps: D.gps[150], gpsLevel: D.gpsLevel[150], mode: SF.dji.flightModeName(D.mode[150]), motor: D.motor[150], ground: D.ground[150], bat: D.bat[150], volt: D.volt[150], amp: D.amp[150], hs: D.hs[150].toFixed(2), vs: D.vs[150] }));
  print('events', JSON.stringify(d.events));
  // wrong keys must fail clearly
  const bad = JSON.parse(JSON.stringify(keys.keychains));
  bad[0][0].aesKey = SF.util.b64encode(new Uint8Array(32));
  try { const x = await SF.dji.decode(SF.dji.open(bytesOf(F + 'SYNTHETIC_DJIFlightRecord_v14.txt')), bad); print('wrong key -> decrypted', x.stats.decrypted, 'badPad', x.stats.badPad, 'points', x.data.t.length); } catch (e) { print('wrong key ->', e.message); }
})());
