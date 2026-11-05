need('util', 'xml', 'aes', 'dji');
done((async () => {
  const b = bytesOf('/Users/severisalmi/Desktop/OULUZONE DATAT/DJIFlightRecord_2026-10-29_[15-46-35].txt');
  const t0 = Date.now();
  const log = SF.dji.open(b);
  print('open ms', Date.now() - t0, 'looksLike', SF.dji.looksLike(b, 'DJIFlightRecord_2026-10-29_[15-46-35].txt'));
  print('version', log.version, 'recordsStart', log.recordsStart, 'auxVersion', log.auxVersion, 'department', log.department, log.departmentName, 'encrypted', log.encrypted);
  const d = log.details;
  print('details', JSON.stringify({ product: d.product, productType: d.productType, aircraftName: d.aircraftName, start: new Date(d.startTime).toISOString(), totalTime: d.totalTime, totalDistance: d.totalDistance, raw: d.totalDistanceRaw, maxHeight: d.maxHeight, maxH: d.maxHSpeed, lat: d.latitude, lon: d.longitude, takeOffAlt: d.takeOffAltitude, app: d.appVersion, platform: d.platform, place: [d.subStreet, d.street, d.city, d.area] }));
  print('keyGroups', log.keyGroups.length, log.keyGroups.map((g) => g.map((k) => SF.dji.fpName(k.fp) + ':' + k.data.length).join(',')).join(' | '));
  const req = SF.dji.keychainsRequest(log);
  print('request version', req.version, 'department', req.department, 'groups', req.keychainsArray.length, 'first', JSON.stringify(req.keychainsArray[0][0]).slice(0, 120));
  print('keyId', log.keyId);
  const fr = SF.dji.eachRecord(log, () => true);
  print('framing', JSON.stringify(fr));
  try { await SF.dji.decode(log, null); } catch (e) { print('decode without keys ->', e.message); }
  print('summary', JSON.stringify(SF.dji.summary(log)));
})());
