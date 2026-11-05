/* Orbit: ArduPilot flight logs.
   - DataFlash .BIN, the autopilot's own log on its SD card. Every record type is described by an
     FMT record in the file. Positions come from POS (the EKF solution) or GPS, and the GPS week and
     milliseconds put the boot clock on UTC.
   - MAVLink telemetry .tlog, saved by Mission Planner and QGroundControl. Every packet carries the
     ground station's UTC time, and one file can hold several vehicles: one drone per system id. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const AP = SF.ardupilot = {};
  const GPS_EPOCH = Date.UTC(1980, 0, 6), LEAP_MS = 18000; // GPS time runs 18 s ahead of UTC since 2017
  const COPTER_MODES = {
    0: 'Stabilize', 1: 'Acro', 2: 'AltHold', 3: 'Auto', 4: 'Guided', 5: 'Loiter', 6: 'RTL', 7: 'Circle', 9: 'Land', 11: 'Drift', 13: 'Sport', 14: 'Flip',
    15: 'AutoTune', 16: 'PosHold', 17: 'Brake', 18: 'Throw', 19: 'Avoid_ADSB', 20: 'Guided_NoGPS', 21: 'Smart_RTL', 22: 'FlowHold', 23: 'Follow', 24: 'ZigZag',
    25: 'SystemID', 26: 'Heli_Autorotate', 27: 'Auto RTL', 28: 'Turtle',
  };
  const PLANE_MODES = {
    0: 'Manual', 1: 'Circle', 2: 'Stabilize', 3: 'Training', 4: 'Acro', 5: 'FBWA', 6: 'FBWB', 7: 'Cruise', 8: 'Autotune', 10: 'Auto', 11: 'RTL', 12: 'Loiter',
    13: 'Takeoff', 14: 'Avoid_ADSB', 15: 'Guided', 17: 'QStabilize', 18: 'QHover', 19: 'QLoiter', 20: 'QLand', 21: 'QRTL', 22: 'QAutotune', 23: 'QAcro', 24: 'Thermal', 25: 'Loiter to QLand',
  };
  const modeName = (plane, num) => (plane ? PLANE_MODES : COPTER_MODES)[num] || String(num);
  const FIRMWARE = /^(ArduCopter|ArduPlane|ArduRover|ArduSub|Blimp|AntennaTracker)\s+V?(\d+\.\d+\.\d+\S*)/;
  const newData = () => ({ t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [], bat: [], volt: [], gps: [], mode: [], motor: [], ground: [] });
  function str(b, o, n) {
    let s = '';
    for (let k = 0; k < n && b[o + k]; k++) s += String.fromCharCode(b[o + k]);
    return s.trim();
  }
  // nearest record at or around t in a time-sorted list, within maxDt ms
  function nearest(ts, t, maxDt) {
    const i = U.idxNear(ts, t);
    return i >= 0 && Math.abs(ts[i] - t) <= maxDt ? i : -1;
  }

  AP.isBin = (b) => b.length > 3 && b[0] === 0xa3 && b[1] === 0x95 && b[2] === 0x80;
  AP.isTlog = function (b) {
    if (b.length < 24 || (b[8] !== 0xfe && b[8] !== 0xfd)) return false;
    const s = ((b[0] * 16777216 + (b[1] << 16) + (b[2] << 8) + b[3]) * 4294967296 + (b[4] * 16777216 + (b[5] << 16) + (b[6] << 8) + b[7])) / 1e6;
    return s > 946684800 && s < 4102444800; // a UNIX time between 2000 and 2100
  };

  // ------------------------------------------------------------------ DataFlash .BIN
  const SIZE = { a: 64, b: 1, B: 1, M: 1, h: 2, H: 2, c: 2, C: 2, i: 4, I: 4, e: 4, E: 4, L: 4, f: 4, n: 4, d: 8, q: 8, Q: 8, N: 16, Z: 64 };
  function compile(f) {
    f.offs = [];
    let o = 0;
    for (const ch of f.format) { f.offs.push(o); o += SIZE[ch] || 0; }
    f.ok = o === f.len - 3 && [...f.format].every((ch) => SIZE[ch]);
    return f;
  }
  function field(dv, b, o, ch) {
    switch (ch) {
      case 'b': return dv.getInt8(o);
      case 'B': case 'M': return b[o];
      case 'h': return dv.getInt16(o, true);
      case 'H': return dv.getUint16(o, true);
      case 'c': return dv.getInt16(o, true) / 100;
      case 'C': return dv.getUint16(o, true) / 100;
      case 'i': return dv.getInt32(o, true);
      case 'I': return dv.getUint32(o, true);
      case 'e': return dv.getInt32(o, true) / 100;
      case 'E': return dv.getUint32(o, true) / 100;
      case 'L': return dv.getInt32(o, true) / 1e7;
      case 'f': return dv.getFloat32(o, true);
      case 'd': return dv.getFloat64(o, true);
      case 'q': return dv.getInt32(o + 4, true) * 4294967296 + dv.getUint32(o, true);
      case 'Q': return dv.getUint32(o + 4, true) * 4294967296 + dv.getUint32(o, true);
      case 'n': return str(b, o, 4);
      case 'N': return str(b, o, 16);
      case 'Z': return str(b, o, 64);
      default: return null;
    }
  }
  const WANT = new Set(['GPS', 'GPS2', 'POS', 'ATT', 'MODE', 'EV', 'ARM', 'MSG', 'PARM', 'BAT']);
  // All wanted records by name: {GPS: [{TimeUS, Status, ...}], ...}
  AP.parseBin = function (bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const fmts = new Map([[128, compile({ name: 'FMT', len: 89, format: 'BBnNZ', cols: [] })]]);
    const msgs = {};
    let i = 0, skipped = 0;
    const n = bytes.length;
    while (i + 3 <= n) {
      if (bytes[i] !== 0xa3 || bytes[i + 1] !== 0x95 || !fmts.has(bytes[i + 2])) { i++; skipped++; continue; }
      const type = bytes[i + 2], f = fmts.get(type);
      if (i + f.len > n) break;
      if (type === 128) {
        const o = i + 3;
        fmts.set(bytes[o], compile({ name: str(bytes, o + 2, 4), len: bytes[o + 1], format: str(bytes, o + 6, 16), cols: str(bytes, o + 22, 64).split(',') }));
      } else if (f.ok && WANT.has(f.name)) {
        const rec = {};
        for (let k = 0; k < f.format.length; k++) rec[f.cols[k]] = field(dv, bytes, i + 3 + f.offs[k], f.format[k]);
        (msgs[f.name] || (msgs[f.name] = [])).push(rec);
      }
      i += Math.max(f.len, 3);
    }
    return { msgs, skipped };
  };

  AP.binToDrone = function (bytes, name) {
    const { msgs } = AP.parseBin(bytes);
    const gps = (msgs.GPS || []).filter((g) => !U.isNum(g.I) || g.I === 0);
    // the boot clock (TimeUS) on UTC, from the GPS's own week and milliseconds (median of all fixes)
    const offs = [];
    for (const g of gps) if (g.Status >= 3 && g.GWk > 0 && U.isNum(g.GMS)) offs.push(GPS_EPOCH + g.GWk * 604800000 + g.GMS - LEAP_MS - g.TimeUS / 1000);
    if (!offs.length) throw new Error('This ArduPilot log has no GPS time (no 3D fix was logged), so it cannot be placed in UTC.');
    offs.sort((a, b) => a - b);
    const off = offs[offs.length >> 1];
    const utc = (us) => off + us / 1000;
    // positions: the EKF solution (POS) when it is logged, else the GPS fixes
    let pts = (msgs.POS || []).filter((p) => p.Lat && p.Lng);
    const fromPos = pts.length >= 10;
    if (!fromPos) pts = gps.filter((g) => g.Status >= 3 && g.Lat && g.Lng);
    if (pts.length < 2) throw new Error('This ArduPilot log has no positions (no POS or GPS records with a fix).');
    let product = '', frame = '';
    for (const m of msgs.MSG || []) {
      const fw = FIRMWARE.exec(m.Message), fr = /^Frame:\s*(.+)/.exec(m.Message);
      if (fw && !product) product = fw[1] + ' ' + fw[2];
      if (fr && !frame) frame = fr[1];
    }
    const plane = /^ArduPlane/.test(product);
    const gT = gps.map((g) => utc(g.TimeUS));
    const att = msgs.ATT || [], aT = att.map((a) => utc(a.TimeUS));
    const modes = (msgs.MODE || []).map((m) => ({ t: utc(m.TimeUS), name: modeName(plane, U.isNum(m.ModeNum) ? m.ModeNum : m.Mode) }));
    const D = newData();
    let mi = -1;
    for (const p of pts) {
      const t = utc(p.TimeUS);
      while (mi + 1 < modes.length && modes[mi + 1].t <= t) mi++;
      const gi = nearest(gT, t, 1000), ai = nearest(aT, t, 500);
      D.t.push(t); D.lat.push(p.Lat); D.lon.push(p.Lng); D.alt.push(p.Alt);
      D.h.push(fromPos && U.isNum(p.RelHomeAlt) ? p.RelHomeAlt : NaN);
      D.hs.push(gi >= 0 ? gps[gi].Spd : NaN);
      D.vs.push(gi >= 0 && U.isNum(gps[gi].VZ) ? -gps[gi].VZ : NaN); // VZ points down
      D.yaw.push(ai >= 0 && U.isNum(att[ai].Yaw) ? ((att[ai].Yaw % 360) + 360) % 360 : NaN);
      D.gps.push(gi >= 0 ? gps[gi].NSats : NaN);
      D.mode.push(mi >= 0 ? modes[mi].name : '');
      D.bat.push(NaN); D.volt.push(NaN); D.motor.push(NaN); D.ground.push(NaN);
    }
    // events: flight modes, arming, landing and the autopilot's own messages
    const events = [];
    for (let k = 0; k < modes.length; k++) if (!k || modes[k].name !== modes[k - 1].name) events.push({ t: modes[k].t, kind: 'mode', text: 'Flight mode: ' + modes[k].name });
    if (msgs.ARM) for (const a of msgs.ARM) events.push({ t: utc(a.TimeUS), kind: 'tip', text: a.ArmState ? 'Armed' : 'Disarmed' });
    for (const e of msgs.EV || []) {
      const txt = { 10: msgs.ARM ? '' : 'Armed', 11: msgs.ARM ? '' : 'Disarmed', 18: 'Landed', 25: 'Home set' }[e.Id];
      if (txt) events.push({ t: utc(e.TimeUS), kind: 'tip', text: txt });
    }
    for (const m of msgs.MSG || []) {
      if (events.length < 400) events.push({ t: utc(m.TimeUS), kind: /fail|error|crash|bad|lost|low|critical/i.test(m.Message) ? 'warn' : 'tip', text: m.Message });
    }
    events.sort((a, b) => a.t - b.t);
    const sysid = ((msgs.PARM || []).find((p) => p.Name === 'SYSID_THISMAV') || {}).Value;
    const drone = SF.tracks.finish(D, events, { name: name || 'ArduPilot log', product: product || 'ArduPilot vehicle' }, { format: 'ardupilot', modes: [...new Set(modes.map((m) => m.name))] });
    drone.label = U.isNum(sysid) ? 'sysid ' + sysid : '';
    drone.sysid = U.isNum(sysid) ? sysid : null;
    drone.version = 'DataFlash' + (frame ? ' · ' + frame : '');
    drone.timeSource = 'GPS time in the log (week ' + (gps.find((g) => g.GWk > 0) || {}).GWk + ')';
    drone.note = fromPos ? '' : 'Positions from the GPS: the log has no EKF positions (POS).';
    return drone;
  };

  // ------------------------------------------------------------------ MAVLink .tlog
  // CRC_EXTRA of the messages Orbit reads; packets of other kinds are skipped unchecked
  const EXTRA = { 0: 50, 24: 24, 33: 104, 253: 83 };
  const NEED = { 0: 9, 24: 30, 33: 28, 253: 51 };
  function crc(bytes, from, to, extra) {
    let c = 0xffff;
    const acc = (x) => { let t = x ^ (c & 0xff); t = (t ^ (t << 4)) & 0xff; c = ((c >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xffff; };
    for (let k = from; k < to; k++) acc(bytes[k]);
    acc(extra);
    return c;
  }
  AP.parseTlog = function (bytes) {
    const n = bytes.length, vehicles = new Map();
    const veh = (id) => vehicles.get(id) || vehicles.set(id, { sysid: id, pos: [], hb: [], gps: [], txt: [] }).get(id);
    let i = 0, bad = 0;
    while (i + 8 + 8 <= n) {
      const s = i + 8, mag = bytes[s];
      if (mag !== 0xfe && mag !== 0xfd) { i++; bad++; continue; }
      const len = bytes[s + 1], v2 = mag === 0xfd;
      const hdr = v2 ? 10 : 6, total = hdr + len + 2 + (v2 && bytes[s + 2] & 1 ? 13 : 0);
      if (s + total > n) break;
      const sys = bytes[s + (v2 ? 5 : 3)], comp = bytes[s + (v2 ? 6 : 4)];
      const id = v2 ? bytes[s + 7] | (bytes[s + 8] << 8) | (bytes[s + 9] << 16) : bytes[s + 5];
      const tus = (bytes[i] * 16777216 + (bytes[i + 1] << 16) + (bytes[i + 2] << 8) + bytes[i + 3]) * 4294967296 + (bytes[i + 4] * 16777216 + (bytes[i + 5] << 16) + (bytes[i + 6] << 8) + bytes[i + 7]);
      const t = tus / 1000;
      if (!(t > 946684800e3 && t < 4102444800e3)) { i++; bad++; continue; }
      if (id in EXTRA) {
        const c = bytes[s + hdr + len] | (bytes[s + hdr + len + 1] << 8);
        if (c !== crc(bytes, s + 1, s + hdr + len, EXTRA[id])) { i++; bad++; continue; }
        if (comp === 1 && sys !== 255) {
          // MAVLink 2 drops trailing zero bytes: pad the payload back to its full length
          const p = new Uint8Array(NEED[id]);
          p.set(bytes.subarray(s + hdr, s + hdr + Math.min(len, NEED[id])));
          const dv = new DataView(p.buffer);
          if (id === 33) {
            const hdg = dv.getUint16(26, true);
            veh(sys).pos.push({ t, lat: dv.getInt32(4, true) / 1e7, lon: dv.getInt32(8, true) / 1e7, alt: dv.getInt32(12, true) / 1000, rel: dv.getInt32(16, true) / 1000, vx: dv.getInt16(20, true) / 100, vy: dv.getInt16(22, true) / 100, vz: dv.getInt16(24, true) / 100, hdg: hdg === 65535 ? NaN : hdg / 100 });
          } else if (id === 0) {
            if (p[4] !== 6 && p[5] !== 8) veh(sys).hb.push({ t, mode: dv.getUint32(0, true), type: p[4], armed: !!(p[6] & 128) });
          } else if (id === 24) veh(sys).gps.push({ t, fix: p[28], sats: p[29] });
          else if (id === 253) veh(sys).txt.push({ t, sev: p[0], text: str(p, 1, 50) });
        }
      }
      i = s + total;
    }
    return { vehicles: [...vehicles.values()], bad };
  };

  // One drone per vehicle (system id) in the file, in system-id order.
  AP.tlogToDrones = function (bytes, name) {
    const { vehicles } = AP.parseTlog(bytes);
    const out = [];
    for (const v of vehicles.sort((a, b) => a.sysid - b.sysid)) {
      const pos = v.pos.filter((p) => p.lat || p.lon).sort((a, b) => a.t - b.t);
      if (pos.length < 10) continue;
      const hbT = v.hb.map((h) => h.t), gT = v.gps.map((g) => g.t);
      // fixed-wing and VTOL types number their modes the ArduPlane way
      const plane = v.hb.some((h) => h.type === 1 || (h.type >= 19 && h.type <= 22));
      const mname = (num) => modeName(plane, num);
      const D = newData();
      for (const p of pos) {
        const hi = nearest(hbT, p.t, 3000), gi = nearest(gT, p.t, 2000);
        D.t.push(p.t); D.lat.push(p.lat); D.lon.push(p.lon); D.alt.push(p.alt); D.h.push(p.rel);
        D.hs.push(Math.hypot(p.vx, p.vy)); D.vs.push(-p.vz); D.yaw.push(p.hdg);
        D.gps.push(gi >= 0 ? v.gps[gi].sats : NaN);
        D.mode.push(hi >= 0 ? mname(v.hb[hi].mode) : '');
        D.bat.push(NaN); D.volt.push(NaN); D.motor.push(NaN); D.ground.push(NaN);
      }
      const events = [];
      for (let k = 0; k < v.hb.length; k++) {
        const h = v.hb[k], p = v.hb[k - 1];
        if (!p || p.armed !== h.armed) events.push({ t: h.t, kind: 'tip', text: h.armed ? 'Armed' : 'Disarmed' });
        if (!p || p.mode !== h.mode) events.push({ t: h.t, kind: 'mode', text: 'Flight mode: ' + mname(h.mode) });
      }
      let product = '';
      for (const x of v.txt) {
        const fw = FIRMWARE.exec(x.text);
        if (fw && !product) product = fw[1] + ' ' + fw[2];
        if (events.length < 400) events.push({ t: x.t, kind: x.sev <= 4 ? 'warn' : 'tip', text: x.text });
      }
      events.sort((a, b) => a.t - b.t);
      const drone = SF.tracks.finish(D, events, { name: (name || 'Telemetry log') + (vehicles.length > 1 ? ' · sysid ' + v.sysid : ''), product: product || 'ArduPilot vehicle' }, { format: 'tlog', modes: [...new Set(D.mode.filter(Boolean))] });
      drone.label = 'sysid ' + v.sysid;
      drone.sysid = v.sysid;
      drone.version = 'MAVLink telemetry';
      drone.timeSource = 'ground station clock (the time each packet was received)';
      out.push(drone);
    }
    if (!out.length) throw new Error('No vehicle positions (GLOBAL_POSITION_INT) were found in this telemetry log.');
    return out;
  };
})(typeof window !== 'undefined' ? window : globalThis);
