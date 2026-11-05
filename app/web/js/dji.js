/* Orbit: DJI flight record reader (DJIFlightRecord_*.txt, all format versions).
   Format notes follow the open-source dji-log-parser (MIT, lvauvillier):
     v1–6  plain records · v7–12 records XOR-scrambled · v13+ XOR + AES-256-CBC.
   For v13+ the AES keys ("keychains") come from DJI's server: the app sends the log's
   KeyStorage blobs with a DJI developer API key, once per log, then caches the answer. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const D = SF.dji = {};

  // ------------------------------------------------------------------ CRC-64 XOR key
  const MASK64 = 0xffffffffffffffffn;
  const CRC_TABLE = (() => {
    const t = [];
    for (let i = 0; i < 256; i++) {
      let c = BigInt(i);
      for (let k = 0; k < 8; k++) c = c & 1n ? (c >> 1n) ^ 0x95ac9329ac4bc9b5n : c >> 1n;
      t.push(c);
    }
    return t;
  })();
  const keyCache = new Map();
  function xorKey(type, seed) {
    const id = (type << 8) | seed;
    let key = keyCache.get(id);
    if (key) return key;
    let c = BigInt((seed + type) & 0xff);
    const m = (0x123456789abcdef0n * BigInt(seed)) & MASK64;
    for (let i = 0; i < 8; i++) {
      const b = (m >> BigInt(8 * i)) & 0xffn;
      c = CRC_TABLE[Number((c ^ b) & 0xffn)] ^ (c >> 8n);
    }
    key = new Uint8Array(8);
    for (let i = 0; i < 8; i++) key[i] = Number((c >> BigInt(8 * i)) & 0xffn);
    keyCache.set(id, key);
    return key;
  }
  // data[0] is the seed; returns data[1..] unscrambled.
  function unxor(type, data) {
    const k = xorKey(type, data[0]);
    const out = new Uint8Array(data.length - 1);
    for (let i = 0; i < out.length; i++) out[i] = data[i + 1] ^ k[i & 7];
    return out;
  }

  // ------------------------------------------------------------------ feature points (v13+)
  const FP_NAMES = ['', 'Base', 'Vision', 'Waypoint', 'Agriculture', 'AirLink', 'AfterSales', 'DJIFlyCustom', 'Plaintext', 'FlightHub', 'Gimbal', 'RC', 'Camera', 'Battery', 'FlySafe', 'Security'];
  D.fpName = (n) => 'FR_Standardization_Feature_' + FP_NAMES[n] + '_' + n;
  D.fpFromName = (s) => { const m = /_(\d+)$/.exec(s || ''); return m ? +m[1] : 0; };
  function featurePoint(type, v) {
    const v13 = v === 13;
    switch (type) {
      case 1: case 2: return 1;
      case 3: return v13 ? 1 : 10;
      case 4: return v13 ? 1 : 11;
      case 5: return 7;
      case 6: return 1;
      case 7: case 8: return v13 ? 1 : 13;
      case 9: case 10: return 7;
      case 11: return v13 ? 1 : 11;
      case 12: return 6;
      case 13: case 14: case 15: return 1;
      case 16: return 6;
      case 17: case 18: return 2;
      case 19: return 6;
      case 20: return 7;
      case 21: return 4;
      case 22: return v13 ? 6 : 13;
      case 24: return 7;
      case 25: return v13 ? 1 : 12;
      case 26: case 27: return 6;
      case 28: return v13 ? 6 : 14;
      case 29: return v13 ? 1 : 11;
      case 30: return 7;
      case 31: case 32: return 3;
      case 33: return v13 ? 1 : 11;
      case 34: case 35: case 36: case 38: case 39: return 3;
      case 40: return 1;
      case 41: case 43: case 44: case 45: case 46: case 47: case 48: return 4;
      case 49: return 5;
      case 50: return 8;
      case 51: case 52: return v13 ? 6 : 14;
      case 53: return v13 ? 6 : 9;
      case 54: return 7;
      case 55: return 15;
      case 56: return 8;
      case 58: case 59: return 1;
      case 62: return 11;
      case 63: return 1;
      default: return 0; // not in the table: found by trial at decode time (see calibrate)
    }
  }
  // Key chain of a record type: the table, then what calibrate() found, else plain (8).
  function fpOf(type, v, assign) {
    if (assign && assign[type] !== undefined) return assign[type];
    const fp = featurePoint(type, v);
    return fp === 0 ? 8 : fp;
  }

  // ------------------------------------------------------------------ little-endian reader (zeros past the end)
  function rd(b) {
    const dv = new DataView(b.buffer, b.byteOffset, b.byteLength), n = b.length;
    return {
      n,
      u8: (o) => (o < n ? b[o] : 0),
      i8: (o) => (o < n ? (b[o] << 24) >> 24 : 0),
      u16: (o) => (o + 2 <= n ? dv.getUint16(o, true) : 0),
      i16: (o) => (o + 2 <= n ? dv.getInt16(o, true) : 0),
      u32: (o) => (o + 4 <= n ? dv.getUint32(o, true) : 0),
      i32: (o) => (o + 4 <= n ? dv.getInt32(o, true) : 0),
      i64: (o) => (o + 8 <= n ? Number(dv.getBigInt64(o, true)) : 0),
      f32: (o) => (o + 4 <= n ? dv.getFloat32(o, true) : 0),
      f64: (o) => (o + 8 <= n ? dv.getFloat64(o, true) : 0),
      str: (o, len) => { const e = Math.min(n, o + len); let z = o; while (z < e && b[z]) z++; return o < n ? U.utf8(b, o, z).trim() : ''; },
    };
  }
  const R2D = 180 / Math.PI;
  const bits = (x, mask) => { x &= mask; while (mask && !(mask & 1)) { x >>= 1; mask >>= 1; } return x; };

  // ------------------------------------------------------------------ names
  const PRODUCT = { 1: 'Inspire 1', 2: 'Phantom 3 Standard', 3: 'Phantom 3 Advanced', 4: 'Phantom 3 Pro', 5: 'Osmo', 6: 'Matrice 100', 7: 'Phantom 4', 8: 'LB2', 9: 'Inspire 1 Pro', 10: 'A3', 11: 'Matrice 600', 12: 'Phantom 3 4K', 13: 'Mavic Pro', 14: 'Zenmuse XT', 15: 'Inspire 1 RAW', 16: 'A2', 17: 'Inspire 2', 21: 'Mavic', 24: 'Phantom 4 Pro', 25: 'N3', 26: 'Spark', 27: 'Matrice 600 Pro', 28: 'Phantom 4 Advanced', 29: 'Phantom 3 SE', 30: 'AG405', 31: 'Matrice 200', 33: 'Matrice 210', 34: 'Matrice 210 RTK', 38: 'Mavic Air', 42: 'Mavic 2', 44: 'Phantom 4 Pro V2', 46: 'Phantom 4 RTK', 57: 'Phantom 4 Multispectral', 58: 'Mavic 2 Enterprise', 59: 'Mavic Mini', 60: 'Matrice 200 V2', 61: 'Matrice 210 V2', 62: 'Matrice 210 RTK V2', 67: 'Mavic Air 2', 70: 'Matrice 300 RTK', 73: 'DJI FPV', 75: 'Air 2S', 76: 'Mini 2', 77: 'Mavic 3', 78: 'Mini SE', 79: 'Mini 3 Pro', 80: 'Mavic 3 Pro', 81: 'Mini 2 SE', 82: 'Matrice 30', 83: 'Mavic 3 Enterprise', 84: 'Avata', 85: 'Mini 4 Pro', 86: 'Avata 2', 87: 'Matrice 350 RTK' };
  const FLIGHT_MODE = { 0: 'Manual', 1: 'Atti', 2: 'Atti course lock', 3: 'Atti hover', 4: 'Hover', 5: 'GPS brake', 6: 'GPS (P)', 7: 'GPS course lock', 8: 'GPS home lock', 9: 'GPS hotpoint', 10: 'Assisted takeoff', 11: 'Auto takeoff', 12: 'Auto landing', 13: 'Atti landing', 14: 'Waypoint', 15: 'Go home', 16: 'Click go', 17: 'Joystick', 18: 'GPS wristband', 19: 'Cinematic', 23: 'Atti limited', 24: 'Draw', 25: 'Follow me', 26: 'ActiveTrack', 27: 'TapFly', 28: 'Pano', 29: 'Farming', 30: 'FPV', 31: 'Sport', 32: 'Novice', 33: 'Confirm landing', 35: 'Terrain tracking', 36: 'Advanced go home', 37: 'Advanced landing', 38: 'Tripod', 39: 'Track headlock', 41: 'Engine start', 43: 'Gentle' };
  const DEPARTMENT = { 1: 'SDK', 2: 'DJI GO', 3: 'DJI Fly', 4: 'Agriculture', 5: 'Terra', 6: 'DJI Glasses', 7: 'DJI Pilot', 8: 'GS Pro' };
  const PLATFORM = { 1: 'iOS', 2: 'Android', 6: 'DJI Fly', 10: 'Windows', 11: 'Mac', 12: 'Linux' };
  D.flightModeName = (n) => FLIGHT_MODE[n] || 'Mode ' + n;
  D._unxor = unxor; // for tests and diagnostics

  // ------------------------------------------------------------------ open
  D.looksLike = function (bytes, name) {
    if (bytes.length < 120) return false;
    const v = bytes[10];
    if (/DJIFlightRecord/i.test(name || '')) return true;
    if (!/\.txt$/i.test(name || '') || v < 1 || v > 30) return false;
    return U.textShare(bytes, 2048) < 0.85;
  };

  function readDetails(b, version) {
    const r = rd(b);
    const d = {
      subStreet: r.str(0, 20), street: r.str(20, 20), city: r.str(40, 20), area: r.str(60, 20),
      recordLineCount: r.i32(83), startTime: r.i64(91), longitude: r.f64(99), latitude: r.f64(107),
      totalDistanceRaw: r.f32(115), totalTime: r.i32(119) / 1000, maxHeight: r.f32(123), maxHSpeed: r.f32(127), maxVSpeed: r.f32(131),
      captureNum: r.i32(135), videoTime: r.i64(139),
    };
    if (version <= 5) {
      Object.assign(d, { takeOffAltitude: r.f32(352), productType: r.u8(277), aircraftName: r.str(278, 24), aircraftSn: r.str(267, 10), cameraSn: r.str(318, 10), rcSn: r.str(328, 10), batterySn: r.str(338, 10), appPlatform: r.u8(348), appVersion: r.u8(349) + '.' + r.u8(350) + '.' + r.u8(351) });
    } else {
      Object.assign(d, { takeOffAltitude: r.f32(267), productType: r.u8(271), aircraftName: r.str(280, 32), aircraftSn: r.str(312, 16), cameraSn: r.str(328, 16), rcSn: r.str(344, 16), batterySn: r.str(360, 16), appPlatform: r.u8(376), appVersion: r.u8(377) + '.' + r.u8(378) + '.' + r.u8(379) });
    }
    // The distance is stored in km by some apps and in m by others: pick the plausible one.
    const km = d.totalDistanceRaw * 1000;
    d.totalDistance = d.totalTime > 60 && d.totalDistanceRaw < 1000 && km / d.totalTime < 45 ? km : d.totalDistanceRaw;
    d.product = PRODUCT[d.productType] || (d.aircraftName || 'DJI aircraft');
    d.platform = PLATFORM[d.appPlatform] || '';
    return d;
  }

  /* Parse the header and summary. Returns a log object used by the other calls. */
  D.open = function (bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const version = bytes[10];
    const detailOffset = Number(dv.getBigUint64(0, true));
    const detailLength = dv.getUint16(8, true);
    if (version < 1 || version > 30) throw new Error('Not a DJI flight record (unknown format version ' + version + ').');
    const log = { bytes, version, detailOffset, detailLength, recordsStart: 0, recordsEnd: bytes.length, details: null, auxVersion: null, department: 0 };
    if (version < 6) log.recordsStart = 12;
    else if (version < 12) log.recordsStart = 100;
    else if (version === 12) log.recordsStart = 100 + 436;
    else log.recordsStart = detailOffset;
    if (version < 12) log.recordsEnd = Math.min(detailOffset, bytes.length);
    if (version < 13) {
      const off = version < 12 ? detailOffset : 100;
      const len = Math.max(detailLength, 400);
      const b = new Uint8Array(len);
      b.set(bytes.subarray(off, Math.min(bytes.length, off + len)));
      log.details = readDetails(b, version);
    } else {
      // Auxiliary Info (type 0, XOR-scrambled) then Auxiliary Version (type 1, plain)
      let p = 100;
      if (bytes[p] !== 0) throw new Error('DJI log: the summary block is missing.');
      const n0 = dv.getUint16(p + 1, true);
      const info = unxor(0, bytes.subarray(p + 3, p + 3 + n0));
      const infoLen = info[1] | (info[2] << 8);
      const db = new Uint8Array(Math.max(400, infoLen));
      db.set(info.subarray(3, 3 + infoLen));
      log.details = readDetails(db, version);
      p += 3 + n0;
      if (bytes[p] === 1) {
        const n1 = dv.getUint16(p + 1, true);
        log.auxVersion = dv.getUint16(p + 3, true);
        log.department = bytes[p + 5];
        p += 3 + n1;
      }
      if (!log.recordsStart) log.recordsStart = p;
    }
    log.departmentName = DEPARTMENT[log.department] || 'unknown';
    log.encrypted = version >= 13;
    if (log.encrypted) {
      log.keyGroups = D.keyStorage(log);
      const all = [];
      for (const g of log.keyGroups) for (const k of g) all.push(...k.data);
      log.keyId = U.fnv64(Uint8Array.from(all)) + '-' + bytes.length.toString(16);
    }
    return log;
  };

  // Walk the record framing. fn(type, data, offset) for each record; JPEG blobs are skipped.
  function eachRecord(log, fn) {
    const b = log.bytes, v = log.version, end = log.recordsEnd;
    let p = log.recordsStart, count = 0, bad = 0;
    const lenBytes = v <= 12 ? 1 : 2;
    while (p + 1 + lenBytes < end) {
      const type = b[p];
      if (type === 0xff && b[p + 1] === 0xd8) { // embedded JPEG
        let q = p + 2;
        while (q + 1 < end && !(b[q] === 0xff && b[q + 1] === 0xd9)) q++;
        p = q + 2;
        continue;
      }
      const len = lenBytes === 1 ? b[p + 1] : b[p + 1] | (b[p + 2] << 8);
      const ds = p + 1 + lenBytes, de = ds + len;
      if (de >= end + 1 || b[de] !== 0xff) {
        bad++;
        let q = p + 1;
        while (q < end && b[q] !== 0xff) q++;
        p = q + 1;
        if (bad > 10000) break;
        continue;
      }
      if (fn(type, b.subarray(ds, de), p) === false) break;
      count++;
      p = de + 1;
    }
    return { count, bad };
  }
  D.eachRecord = eachRecord;

  // KeyStorage (56) blobs, grouped by KeyStorageRecover (50) markers.
  D.keyStorage = function (log) {
    const groups = [[]];
    eachRecord(log, (type, data) => {
      if (type === 56) {
        const x = unxor(56, data);
        const fp = x[0] | (x[1] << 8), n = x[2] | (x[3] << 8);
        groups[groups.length - 1].push({ fp, data: x.subarray(4, 4 + n) });
      } else if (type === 50) groups.push([]);
    });
    return groups;
  };
  // Body for POST https://dev.dji.com/openapi/v1/flight-records/keychains (header Api-Key).
  D.keychainsRequest = function (log) {
    return {
      version: log.auxVersion || 0,
      department: DEPARTMENT[log.department] ? log.department : 3,
      keychainsArray: log.keyGroups.map((g) => g.map((k) => ({ featurePoint: D.fpName(k.fp), aesCiphertext: U.b64encode(k.data) }))),
    };
  };

  // ------------------------------------------------------------------ record decoding
  function makeKeychain(entries) {
    const m = new Map();
    for (const e of entries || []) {
      const fp = typeof e.featurePoint === 'number' ? e.featurePoint : D.fpFromName(e.featurePoint);
      const key = U.b64decode(e.aesKey), iv = U.b64decode(e.aesIv);
      if (fp && key.length === 32 && iv.length === 16) m.set(fp, { dk: SF.aes.decKey(key), iv });
    }
    return m;
  }
  function plain(log, type, data, kc, stats) {
    const v = log.version;
    if (v <= 6) return data;
    if (data.length < 2) return new Uint8Array(0);
    if (v <= 12) return unxor(type, data);
    const fp = fpOf(type, v, log.fpAssign);
    if (fp === 8) return unxor(type, data);
    const k = kc && kc.get(fp);
    if (!k) { stats.locked++; return null; }
    const x = unxor(type, data);
    const ct = x.subarray(0, data.length - 2);
    if (!ct.length || ct.length % 16) { stats.badLen++; return new Uint8Array(0); }
    const out = SF.aes.cbcDecrypt(k.dk, k.iv, ct);
    k.iv = ct.slice(ct.length - 16);
    if (!out) { stats.badPad++; return new Uint8Array(0); }
    stats.decrypted++;
    return out;
  }

  /* Newer DJI apps add record types the format table doesn't list. In a DJI Pilot 2 v14 log
     (M400, 2026-10) types 253/254 turned out to share the key chain of the custom/time records
     and type 57 the radio-link chain; treating them as plain broke the AES IV chain, so every
     timestamp came out as a random year. Each unknown type is therefore tried on every key
     chain: with the right key nearly all its records have valid PKCS#7 padding, with a wrong
     one almost none. Only the first records are used, so this takes a fraction of a second. */
  function trial(log, keychains, assign, type, fp, maxRecords) {
    const groups = keychains.map(makeKeychain);
    let kc = groups.shift() || new Map(), ok = 0, n = 0, seen = 0;
    eachRecord(log, (t, data) => {
      if (++seen > maxRecords) return false;
      if (t === 50) { kc = groups.shift() || new Map(); return; }
      if (fpOf(t, log.version, assign) !== fp) return;
      const k = kc.get(fp);
      if (!k || data.length < 2) return;
      const ct = unxor(t, data).subarray(0, data.length - 2);
      if (!ct.length || ct.length % 16) return;
      const pt = SF.aes.cbcDecrypt(k.dk, k.iv, ct);
      k.iv = ct.slice(ct.length - 16);
      if (t === type) { n++; if (pt) ok++; }
    });
    return n ? ok / n : 0;
  }
  D.calibrate = function (log, keychains) {
    const assign = {};
    if (!log.encrypted || !keychains || !keychains.length) return assign;
    const counts = new Map();
    eachRecord(log, (t) => { if (t !== 50 && t !== 56 && featurePoint(t, log.version) === 0) counts.set(t, (counts.get(t) || 0) + 1); });
    const fps = [...new Set(keychains.flat().map((e) => (typeof e.featurePoint === 'number' ? e.featurePoint : D.fpFromName(e.featurePoint))))].filter((f) => f && f !== 8);
    const types = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
    const maxRecords = 20000;
    for (const t of types) {
      let best = 8, bestRate = 0.6;
      for (const fp of fps) {
        const r = trial(log, keychains, Object.assign({}, assign, { [t]: fp }), t, fp, maxRecords);
        if (r > bestRate) { bestRate = r; best = fp; }
      }
      assign[t] = best;
    }
    return assign;
  };

  /* Decode all records into a drone track. keychains: the DJI API answer (array of arrays of
     {featurePoint, aesKey, aesIv}); not needed below format 13. */
  D.decode = async function (log, keychains, onProgress) {
    if (log.encrypted && !keychains) throw new Error('This log is encrypted (format ' + log.version + '); it needs keys from DJI.');
    if (log.encrypted && !log.fpAssign) log.fpAssign = D.calibrate(log, keychains);
    const groups = (keychains || []).map(makeKeychain);
    let kc = groups.shift() || new Map();
    const stats = { decrypted: 0, locked: 0, badLen: 0, badPad: 0, records: 0 };
    const T = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], vx: [], vy: [], yaw: [], pitch: [], roll: [], gps: [], gpsLevel: [], bat: [], volt: [], amp: [], mode: [], motor: [], ground: [], flyTime: [], vps: [], sig: [], gimbalPitch: [] };
    const events = [], pilot = [], homes = [];
    let customT = NaN, lastCustomAt = -1, home = null, mode = -1, motor = null, battery = { pct: NaN, volt: NaN, amp: NaN }, sigUp = NaN, gimbalPitch = NaN;
    const info = { firmware: [], serials: {}, recover: null, product: log.details.product, fpAssign: log.fpAssign || {} };
    // a record time is believed only inside the flight the summary describes (with an hour of slack)
    const st0 = log.details.startTime, st1 = st0 + Math.max(0, log.details.totalTime) * 1000;
    const haveSpan = st0 > 1.2e12 && st0 < 4.1e12 && log.details.totalTime > 0;
    const timeOk = (ts) => (haveSpan ? ts >= st0 - 3600e3 && ts <= st1 + 3600e3 : ts > 1.26e12 && ts < 4.1e12);
    let lastTime = NaN;
    const pushEvent = (kind, text) => { if (text) events.push({ t: U.isNum(customT) ? customT : lastTime, kind, text: text.replace(/\s+/g, ' ').trim() }); };
    let n = 0;
    const total = log.recordsEnd - log.recordsStart;
    const doRecord = (type, data, off) => {
      stats.records++;
      if (type === 50) { kc = groups.shift() || new Map(); return; }
      if (type === 56) return;
      const known = type === 1 || type === 2 || type === 3 || type === 4 || type === 5 || type === 7 || type === 8 || type === 9 || type === 10 || type === 11 || type === 13 || type === 14 || type === 15 || type === 22 || type === 24 || type === 40 || type === 49 || type === 62;
      if (!known) {
        // unknown records still advance the AES chain of their feature point
        if (log.encrypted) plain(log, type, data, kc, stats);
        return;
      }
      const pt = plain(log, type, data, kc, stats);
      if (!pt || !pt.length) return;
      const r = rd(pt);
      switch (type) {
        case 1: { // OSD
          const lon = r.f64(0) * R2D, lat = r.f64(8) * R2D;
          const h = r.i16(16) / 10, vx = r.i16(18) / 10, vy = r.i16(20) / 10, vz = r.i16(22) / 10;
          const bp1 = r.u8(30), bp2 = r.u8(32), bp4 = r.u8(34);
          const fm = bits(bp1, 0x7f);
          if (fm !== mode) { if (mode >= 0) pushEvent('mode', 'Flight mode: ' + D.flightModeName(fm)); mode = fm; }
          const motorUp = bits(bp2, 0x08) === 1;
          if (motor !== null && motorUp !== motor) pushEvent('mode', motorUp ? 'Motors started' : 'Motors stopped');
          motor = motorUp;
          let t = customT;
          if (!U.isNum(t)) t = log.details.startTime + T.t.length * 100;
          lastTime = t;
          T.t.push(t); T.lat.push(lat); T.lon.push(lon); T.h.push(h);
          T.alt.push(h + (home ? home.alt : 0)); T.vx.push(vx); T.vy.push(vy); T.vs.push(-vz); T.hs.push(Math.hypot(vx, vy));
          T.pitch.push(r.i16(24) / 10); T.roll.push(r.i16(26) / 10); T.yaw.push(((r.i16(28) / 10) + 360) % 360);
          T.gps.push(r.u8(36)); T.gpsLevel.push(bits(bp4, 0x3c)); T.mode.push(fm); T.motor.push(motorUp ? 1 : 0);
          T.ground.push(bits(bp2, 0x06) <= 1 ? 1 : 0); T.flyTime.push(r.u16(42) / 10); T.vps.push(r.u8(41) / 10);
          T.bat.push(U.isNum(battery.pct) ? battery.pct : r.u8(40)); T.volt.push(battery.volt); T.amp.push(battery.amp);
          T.sig.push(sigUp); T.gimbalPitch.push(gimbalPitch);
          break;
        }
        case 2: { // home point
          const lon = r.f64(0) * R2D, lat = r.f64(8) * R2D, alt = r.f32(16) / 10;
          if (Math.abs(lat) > 0.0001 || Math.abs(lon) > 0.0001) {
            if (!home || Math.abs(home.lat - lat) > 1e-6 || Math.abs(home.lon - lon) > 1e-6) homes.push({ t: lastTime, lat, lon, alt });
            home = { lat, lon, alt, goHomeHeight: r.u16(22), maxHeight: log.version >= 8 ? r.f32(37) : NaN };
          }
          break;
        }
        case 3: gimbalPitch = r.i16(0) / 10; break;
        case 5: { // custom: UTC timestamp
          const ts = r.i64(10);
          if (timeOk(ts)) { customT = ts; lastCustomAt = T.t.length; }
          break;
        }
        case 7: battery = { pct: r.u8(0), volt: r.u16(1) / 1000, amp: Math.abs(r.i16(14) / 1000) }; break;
        case 8: battery = { pct: r.u8(26), volt: r.u16(24) / 1000, amp: battery.amp }; break;
        case 9: pushEvent('tip', r.str(0, data.length)); break;
        case 10: pushEvent('warn', r.str(0, data.length)); break;
        case 24: pushEvent('warn', r.str(0, data.length)); break;
        case 11: { // RC GPS (1e-7 degrees)
          const lat = r.i32(7) / 1e7, lon = r.i32(11) / 1e7;
          if (Math.abs(lat) > 0.001 && Math.abs(lat) <= 90 && Math.abs(lon) <= 180) pilot.push({ t: lastTime, lat, lon, src: 'rc' });
          break;
        }
        case 14: { // app (pilot device) location
          let lon = r.f64(0), lat = r.f64(8);
          if (Math.abs(lat) < 1.6 && Math.abs(lon) < 3.2 && (Math.abs(lat) > 1e-4)) { lat *= R2D; lon *= R2D; }
          if (Math.abs(lat) > 0.001 && Math.abs(lat) <= 90 && Math.abs(lon) <= 180) pilot.push({ t: lastTime, lat, lon, src: 'app' });
          break;
        }
        case 13: info.recover = { product: r.u8(0), appVersion: r.u8(2) + '.' + r.u8(3) + '.' + r.u8(4), aircraftSn: r.str(5, log.version <= 7 ? 10 : 16), aircraftName: r.str(log.version <= 7 ? 15 : 21, 32) }; break;
        case 15: info.firmware.push({ sender: r.u8(0), sub: r.u8(1), version: r.u8(2) + '.' + r.u8(3) + '.' + r.u8(4) }); break;
        case 22: {
          const kind = r.u8(0);
          if (kind === 2) { // dynamic battery state; index 1 holds the main values on multi-battery aircraft
            const idx = r.u8(1);
            if (!battery.idx || idx === battery.idx || idx === 1) battery = { idx, volt: r.i32(2) / 1000, amp: Math.abs(r.i32(6) / 1000), pct: r.u8(21), temp: r.i16(18) / 10 };
          }
          break;
        }
        case 40: { const ct = r.u16(0) & 0xff, len = r.u8(2); info.serials[ct] = r.str(3, len); break; }
        case 49: { const bp = r.u8(0); if (bits(bp, 0x80) === 1) sigUp = bits(bp, 0x7f); break; }
        case 62: break;
      }
    };
    const res = eachRecord(log, (type, data, off) => {
      doRecord(type, data, off);
      return true;
    });
    if (onProgress) onProgress(1);
    if (!T.t.length) {
      if (log.encrypted && (stats.badPad || !stats.decrypted)) throw new Error('the keys did not decrypt the position records (' + (stats.badPad || stats.locked) + ' records failed). The keys may belong to another log.');
      throw new Error('The log holds no position records.');
    }
    // Several OSD records can share one custom timestamp: spread them evenly in between.
    fixTimes(T.t);
    // Last line of defence: times that run backwards or far beyond the logged flight length are
    // rebuilt evenly from the summary's start and length, so nothing downstream sees them.
    let back = 0;
    for (let i = 1; i < T.t.length; i++) if (T.t[i] < T.t[i - 1] - 2000) back++;
    const span = T.t[T.t.length - 1] - T.t[0];
    const expect = haveSpan ? st1 - st0 : T.t.length * 100;
    if (!T.t.every(Number.isFinite) || back > 3 || span > Math.max(2 * expect, expect + 600e3) || span < 0) {
      const t0 = haveSpan ? st0 : Number.isFinite(T.t[0]) ? T.t[0] : Date.UTC(2000, 0, 1);
      const dt = (haveSpan ? expect : T.t.length * 100) / Math.max(1, T.t.length - 1);
      for (let i = 0; i < T.t.length; i++) T.t[i] = t0 + i * dt;
      for (const e of events) if (!timeOk(e.t)) e.t = t0;
      info.timesRebuilt = true;
    }
    // Home altitude may arrive after the first OSD records
    if (home) for (let i = 0; i < T.alt.length; i++) if (T.alt[i] === T.h[i]) T.alt[i] = T.h[i] + home.alt;
    // Drop records before GPS was valid (lat/lon 0)
    const keep = T.lat.map((la, i) => Math.abs(la) > 1e-6 || Math.abs(T.lon[i]) > 1e-6);
    if (keep.some((k) => !k)) for (const k in T) T[k] = T[k].filter((_, i) => keep[i]);
    return {
      kind: 'drone', source: 'dji', name: log.details.aircraftName || log.details.product, product: log.details.product, data: T, events, pilot, homes,
      home: home || (U.isNum(log.details.latitude) ? { lat: log.details.latitude, lon: log.details.longitude, alt: log.details.takeOffAltitude } : null),
      details: log.details, info, stats: Object.assign(stats, { framing: res }), version: log.version,
      note: info.timesRebuilt ? 'The log\'s clock records could not be read, so times were spread evenly over the logged flight.' : '',
      t0: T.t[0], t1: T.t[T.t.length - 1],
    };
  };
  function fixTimes(t) {
    let i = 0;
    while (i < t.length) {
      let j = i;
      while (j + 1 < t.length && t[j + 1] === t[i]) j++;
      if (j > i) {
        const next = j + 1 < t.length ? t[j + 1] : t[i] + 100 * (j - i + 1);
        const step = (next - t[i]) / (j - i + 1);
        for (let k = i + 1; k <= j; k++) t[k] = t[i] + step * (k - i);
      }
      i = j + 1;
    }
  }

  // Summary-only view (encrypted log without keys): the summary has the home point and stats.
  D.summary = function (log) {
    const d = log.details;
    return {
      product: d.product, aircraftName: d.aircraftName, start: d.startTime, end: d.startTime + d.totalTime * 1000,
      duration: d.totalTime, distance: d.totalDistance, maxHeight: d.maxHeight, maxHSpeed: d.maxHSpeed, maxVSpeed: d.maxVSpeed,
      home: U.isNum(d.latitude) && (d.latitude || d.longitude) ? { lat: d.latitude, lon: d.longitude, alt: d.takeOffAltitude } : null,
      version: log.version, app: (log.departmentName || '') + ' ' + (d.appVersion || ''), place: [d.subStreet, d.street, d.city, d.area].filter(Boolean).join(', '),
      encrypted: log.encrypted, records: d.recordLineCount,
    };
  };
})(typeof window !== 'undefined' ? window : globalThis);
