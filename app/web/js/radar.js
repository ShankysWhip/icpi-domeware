/* Orbit: radar track readers.
   - Robin Radar IRIS XML (concatenated <Robin> messages: Tracks, PotentialDronePlots,
     SystemStatus, RadarImageMeta, RadarMotion), from a file or a live stream.
   - Any other radar XML or CSV: the fields are detected by name and can be remapped. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util, X = SF.xml;
  const R = SF.radar = {};

  R.newStore = function (format, name) {
    return {
      kind: 'radar', format, name: name || 'Radar', sensors: [], sensorMap: new Map(), tracks: [], trackMap: new Map(),
      plots: { t: [], lat: [], lon: [], alt: [], rcs: [] }, status: [], events: [], classes: new Map(), snapTimes: [],
      t0: Infinity, t1: -Infinity, messages: 0, snapshots: 0, updates: 0, alarms: 0, unknown: {}, version: 0,
    };
  };
  function track(store, id) {
    let tr = store.trackMap.get(id);
    if (!tr) {
      tr = { id, t: [], lat: [], lon: [], alt: [], spd: [], hdg: [], el: [], cls: [], score: [], rcs: [], mrcs: [], alarm: [], seen: new Set(), classes: {}, mainClass: '', first: Infinity, last: -Infinity };
      store.trackMap.set(id, tr);
      store.tracks.push(tr);
    }
    return tr;
  }
  // Add one track update; duplicates (same id and timestamp) are skipped.
  R.addUpdate = function (store, id, u) {
    if (!U.isNum(u.t) || !U.isNum(u.lat) || !U.isNum(u.lon)) return false;
    const tr = track(store, id);
    const key = u.t;
    if (tr.seen.has(key)) return false;
    tr.seen.add(key);
    let i = tr.t.length;
    if (i && tr.t[i - 1] > u.t) { // out of order: find the slot
      i = U.idxLE(tr.t, u.t) + 1;
      for (const k of ['t', 'lat', 'lon', 'alt', 'spd', 'hdg', 'el', 'cls', 'score', 'rcs', 'mrcs', 'alarm']) tr[k].splice(i, 0, null);
    }
    tr.t[i] = u.t; tr.lat[i] = u.lat; tr.lon[i] = u.lon; tr.alt[i] = U.isNum(u.alt) ? u.alt : NaN;
    tr.spd[i] = U.isNum(u.spd) ? u.spd : NaN; tr.hdg[i] = U.isNum(u.hdg) ? u.hdg : NaN; tr.el[i] = U.isNum(u.el) ? u.el : NaN;
    const cls = u.cls ? String(u.cls).trim().toUpperCase() : 'UNCLASSIFIED';
    tr.cls[i] = cls; tr.score[i] = U.isNum(u.score) ? u.score : NaN; tr.rcs[i] = U.isNum(u.rcs) ? u.rcs : NaN;
    tr.mrcs[i] = U.isNum(u.mrcs) ? u.mrcs : NaN; tr.alarm[i] = !!u.alarm;
    tr.classes[cls] = (tr.classes[cls] || 0) + 1;
    store.classes.set(cls, (store.classes.get(cls) || 0) + 1);
    if (u.t < tr.first) tr.first = u.t;
    if (u.t > tr.last) tr.last = u.t;
    if (u.t < store.t0) store.t0 = u.t;
    if (u.t > store.t1) store.t1 = u.t;
    if (u.alarm) store.alarms++;
    store.updates++;
    store.version++;
    return true;
  };
  R.finalize = function (store) {
    for (const tr of store.tracks) {
      let best = '', bn = -1;
      for (const k in tr.classes) {
        // a drone label wins over the generic ones even when it is not the most frequent
        const w = tr.classes[k] + (/DRONE|UAV|UAS/.test(k) ? 1e6 : 0) + (k === 'UNCLASSIFIED' ? -1e6 : 0);
        if (w > bn) { bn = w; best = k; }
      }
      tr.mainClass = best || 'UNCLASSIFIED';
      tr.n = tr.t.length;
    }
    store.tracks.sort((a, b) => a.first - b.first);
    return store;
  };
  R.sensor = (store) => store.sensors.find((s) => U.isNum(s.lat)) || null;

  // ------------------------------------------------------------------ Robin IRIS
  R.isRobin = (text) => /<Robin[\s>]/.test(text.slice(0, 4096));
  function robinTracks(store, node) {
    store.snapshots++;
    const st = U.parseTime(node.attrs.timestamp);
    if (U.isNum(st)) store.snapTimes.push(st);
    for (const t of node.children) {
      if (t.name !== 'Track') continue;
      const id = t.attrs.id || X.get(t, 'Id') || X.get(t, 'ID');
      const ts = U.parseTime(X.get(t, 'Timestamp') || node.attrs.timestamp);
      const clsNode = X.child(t, 'Classification');
      R.addUpdate(store, String(id), {
        t: ts, lat: X.num(t, 'Position/Latitude'), lon: X.num(t, 'Position/Longitude'), alt: X.num(t, 'Position/Altitude'),
        hdg: X.num(t, 'Velocity/Azimuth'), el: X.num(t, 'Velocity/Elevation'), spd: X.num(t, 'Velocity/Speed'),
        cls: clsNode ? clsNode.text.trim() : '', score: X.num(t, 'Score'), mrcs: X.num(t, 'MedianRCS'), rcs: X.num(t, 'CurrentRCS'),
        alarm: X.get(t, 'Alarm') === 'true',
      });
    }
  }
  function robinPlots(store, node) {
    for (const p of node.children) {
      if (p.name !== 'plot' && p.name !== 'Plot') continue;
      const pos = X.child(p, 'position') || X.child(p, 'Position');
      if (!pos) continue;
      const t = U.parseTime(p.attrs.timestamp || node.attrs.timestamp);
      const lat = parseFloat(pos.attrs.latitude), lon = parseFloat(pos.attrs.longitude);
      if (!U.isNum(t) || !U.isNum(lat)) continue;
      const P = store.plots;
      P.t.push(t); P.lat.push(lat); P.lon.push(lon); P.alt.push(parseFloat(pos.attrs.altitude)); P.rcs.push(parseFloat(p.attrs.rcs));
      if (t < store.t0) store.t0 = t;
      if (t > store.t1) store.t1 = t;
    }
  }
  function robinStatus(store, node) {
    const t = U.parseTime(node.attrs.timestamp);
    const sensors = X.child(node, 'Sensors');
    const list = sensors ? X.children(sensors, 'SensorStatus') : [];
    for (const s of list) {
      const comps = X.child(s, 'Components');
      const tr = comps && X.child(comps, 'Transceiver'), gps = comps && X.child(comps, 'GPS');
      const blank = X.children(s, 'BlankingSector').map((b) => ({ angle: X.num(b, 'Angle'), span: X.num(b, 'Span'), enabled: X.get(b, 'Enabled') === 'true' }));
      const info = {
        id: s.attrs.id || '0', name: X.get(s, 'Name') || X.get(node, 'Name') || 'Radar', version: X.get(s, 'Version') || '',
        state: X.get(s, 'OperationalState') || X.get(node, 'OperationalState') || '',
        lat: X.num(s, 'Position/Latitude'), lon: X.num(s, 'Position/Longitude'), alt: X.num(s, 'Position/Altitude'),
        ground: X.num(s, 'GroundLevel'), azimuth: X.num(s, 'Orientation/Azimuth'), type: X.get(s, 'SensorType') || '',
        mode: X.get(s, 'Processing/OperatingModeId') || '', sensitivity: X.num(s, 'Processing/Sensitivity'),
        imageQuality: X.get(s, 'Processing/ImageQuality') || '',
        freqMHz: tr ? parseFloat(tr.attrs.CarrierFrequencyMhz) : NaN, serial: tr ? tr.attrs.SerialNumber || '' : '',
        hdop: gps ? parseFloat(gps.attrs.HDOP) : NaN, gpsLock: gps ? gps.attrs.Lock === 'true' : null, blanking: blank,
      };
      if (!U.isNum(info.lat) || (info.lat === 0 && info.lon === 0)) { info.lat = NaN; info.lon = NaN; }
      const prev = store.sensorMap.get(info.id);
      if (!prev) { store.sensorMap.set(info.id, info); store.sensors.push(info); }
      else Object.assign(prev, info);
      if (!prev || prev.lastState !== info.state) {
        store.status.push({ t, sensor: info.name, state: info.state });
        if (prev) store.events.push({ t, kind: 'radar', text: info.name + ' state: ' + info.state });
      }
      (prev || info).lastState = info.state;
    }
    if (!store.name || store.name === 'Radar') store.name = X.get(node, 'Name') || (list[0] && X.get(list[0], 'Name')) || store.name;
  }
  function robinImageMeta(store, node) {
    const t = U.parseTime(node.attrs.Timestamp || node.attrs.timestamp);
    X.walk(node, (n) => {
      if (n.name === 'Lines' && n.text.trim()) {
        const az = (n.text.match(/-?\d+(\.\d+)?/g) || []).map(Number);
        if (az.length) store.events.push({ t, kind: 'radar', text: (n.attrs.Type || 'Line') + ' at ' + az.map((a) => a.toFixed(0) + '°').join(', '), az, type: n.attrs.Type || '' });
      }
    });
  }
  R.ingestRobinNode = function (store, msg) {
    store.messages++;
    for (const ch of msg.children) {
      switch (ch.name) {
        case 'Tracks': robinTracks(store, ch); break;
        case 'PotentialDronePlots': robinPlots(store, ch); break;
        case 'SystemStatus': robinStatus(store, ch); break;
        case 'RadarImageMeta': robinImageMeta(store, ch); break;
        case 'RadarMotion': break;
        default: store.unknown[ch.name] = (store.unknown[ch.name] || 0) + 1;
      }
    }
  };
  // Feed one or more complete <Robin>…</Robin> messages (used by the live feed too).
  R.ingestRobinText = function (store, text) {
    const root = X.parse(text);
    for (const msg of root.children) if (msg.name === 'Robin') R.ingestRobinNode(store, msg);
  };
  R.parseRobin = async function (text, name, onProgress) {
    const store = R.newStore('robin', name);
    let pos = 0, count = 0;
    for (;;) {
      const s = text.indexOf('<Robin', pos);
      if (s < 0) break;
      let e = text.indexOf('</Robin>', s);
      if (e < 0) break;
      e += 8;
      R.ingestRobinText(store, text.slice(s, e));
      pos = e;
      if (++count % 500 === 0) { if (onProgress) onProgress(pos / text.length); await U.tick(); }
    }
    store.fileMessages = count;
    return R.finalize(store);
  };

  // ------------------------------------------------------------------ generic XML / CSV
  const RX = {
    lat: /(^|[/@_.\s])(lat|latitude|lat_deg|latitude_deg)$/i,
    lon: /(^|[/@_.\s])(lon|lng|long|longitude|lon_deg|longitude_deg)$/i,
    alt: /(^|[/@_.\s])(alt|altitude|alt_msl|height|hae|msl|z|elevation_m|alt_m|height_m)$/i,
    time: /(^|[/@_.\s])(time|timestamp|datetime|date_time|ts|utc|time_utc|timeutc|updatetime|lastupdate|t)$/i,
    id: /(^|[/@_.\s])(id|trackid|track_id|track|targetid|target_id|uid|tracknumber|track_number|tn|objectid|object_id)$/i,
    cls: /(^|[/@_.\s])(class|classification|type|category|label|identity|objecttype|object_type|targettype)$/i,
    spd: /(^|[/@_.\s])(speed|spd|groundspeed|ground_speed|velocity|vel)$/i,
    hdg: /(^|[/@_.\s])(heading|course|hdg|cog|track_angle|direction)$/i,
    range: /(^|[/@_.\s])(range|range_m|distance|dist|slant_range)$/i,
    az: /(^|[/@_.\s])(azimuth|bearing|az|azimuth_deg)$/i,
    el: /(^|[/@_.\s])(elevation|el|elev|elevation_deg)$/i,
    x: /(^|[/@_.\s])(x|east|easting|x_m)$/i,
    y: /(^|[/@_.\s])(y|north|northing|y_m)$/i,
    rcs: /(^|[/@_.\s])(rcs|rcs_dbsm|currentrcs|current_rcs)$/i,
    score: /(^|[/@_.\s])(score|confidence|probability|prob)$/i,
  };
  R.FIELDS = ['time', 'id', 'lat', 'lon', 'alt', 'cls', 'spd', 'hdg', 'range', 'az', 'el', 'x', 'y', 'rcs', 'score'];

  function flatten(node, prefix, out, depth) {
    for (const k in node.attrs) out[prefix + '@' + k] = node.attrs[k];
    const seen = {};
    for (const c of node.children) {
      if (seen[c.name]) continue;
      seen[c.name] = 1;
      if (!c.children.length) {
        const v = c.text.trim();
        if (v) out[prefix + c.name] = v;
        for (const a in c.attrs) out[prefix + c.name + '/@' + a] = c.attrs[a];
      } else if (depth < 3) flatten(c, prefix + c.name + '/', out, depth + 1);
    }
    return out;
  }
  // Pick the best key in rec for a field: name match first, then a sanity check on the value.
  function pickKey(keys, sample, field, exclude) {
    const cands = keys.filter((k) => RX[field].test(k) && !exclude.has(k));
    const ok = cands.filter((k) => {
      const v = sample[k];
      if (field === 'time') return U.isNum(U.parseTime(v));
      if (field === 'cls' || field === 'id') return v != null && v !== '';
      const x = U.num(v);
      if (!U.isNum(x)) return false;
      if (field === 'lat') return Math.abs(x) <= 90;
      if (field === 'lon') return Math.abs(x) <= 180;
      return true;
    });
    if (field === 'hdg' || field === 'el') ok.sort((a, b) => (/veloc/i.test(b) ? 1 : 0) - (/veloc/i.test(a) ? 1 : 0));
    if (field === 'az') ok.sort((a, b) => (/veloc/i.test(a) ? 1 : 0) - (/veloc/i.test(b) ? 1 : 0));
    if (field === 'id') ok.sort((a, b) => a.length - b.length);
    if (field === 'cls') ok.sort((a, b) => (U.isNum(U.num(sample[a])) ? 1 : 0) - (U.isNum(U.num(sample[b])) ? 1 : 0));
    return ok[0] || null;
  }
  function guessMapping(keys, sample) {
    const used = new Set(), m = {};
    for (const f of ['lat', 'lon', 'time', 'alt', 'spd', 'hdg', 'el', 'cls', 'rcs', 'score', 'range', 'az', 'x', 'y', 'id']) {
      const k = pickKey(keys, sample, f, used);
      if (k) { m[f] = k; used.add(k); }
    }
    m.pos = m.lat && m.lon ? 'latlon' : m.range && m.az ? 'polar' : m.x && m.y ? 'xy' : 'latlon';
    return m;
  }

  /* Look through a parsed XML tree for repeated elements that carry positions.
     Returns candidates sorted best first: {tag, count, keys, sample, mapping}. */
  R.analyzeXML = function (root) {
    const byTag = new Map(), parents = new Map();
    X.walk(root, (n, d, p) => {
      if (p) parents.set(n, p);
      if (n.name === '#root') return;
      let e = byTag.get(n.name);
      if (!e) byTag.set(n.name, (e = { tag: n.name, nodes: [] }));
      e.nodes.push(n);
    });
    const out = [];
    for (const e of byTag.values()) {
      if (e.nodes.length < 2 || !e.nodes[0].children.length && !Object.keys(e.nodes[0].attrs).length) continue;
      const sample = {};
      for (const n of e.nodes.slice(0, 30)) {
        const rec = flatten(n, '', {}, 0);
        // ancestors' attributes and leaves can hold the time (e.g. <Tracks timestamp=…>)
        let p = parents.get(n), lvl = 1;
        while (p && p.name !== '#root' && lvl <= 2) {
          for (const k in p.attrs) rec['^' + lvl + ':@' + k] = p.attrs[k];
          for (const c of p.children) if (!c.children.length && c.text.trim() && c !== n) rec['^' + lvl + ':' + c.name] = c.text.trim();
          p = parents.get(p); lvl++;
        }
        for (const k in rec) if (sample[k] == null) sample[k] = rec[k];
      }
      const keys = Object.keys(sample);
      const mapping = guessMapping(keys, sample);
      const hasPos = (mapping.lat && mapping.lon) || (mapping.range && mapping.az) || (mapping.x && mapping.y);
      if (!hasPos) continue;
      let score = e.nodes.length * (mapping.time ? 3 : 1) * (mapping.id ? 2 : 1);
      if (mapping.time && mapping.time.startsWith('^')) score *= 0.9;
      out.push({ tag: e.tag, count: e.nodes.length, keys, sample, mapping, score, nodes: e.nodes });
    }
    out.sort((a, b) => b.score - a.score);
    R._parents = parents;
    return out;
  };
  function readRec(rec, m, opts) {
    const g = (f) => (m[f] ? rec[m[f]] : undefined);
    let lat = U.num(g('lat')), lon = U.num(g('lon')), alt = U.num(g('alt'));
    const o = opts.origin;
    if (m.pos === 'polar' && o && U.isNum(o.lat)) {
      const rng = U.num(g('range')), az = U.num(g('az')), el = U.num(g('el'));
      if (U.isNum(rng) && U.isNum(az)) {
        const ground = U.isNum(el) ? rng * Math.cos(el * U.D2R) : rng;
        [lat, lon] = U.destination(o.lat, o.lon, az, ground);
        if (!U.isNum(alt) && U.isNum(el)) alt = (o.alt || 0) + rng * Math.sin(el * U.D2R);
      }
    } else if (m.pos === 'xy' && o && U.isNum(o.lat)) {
      const f = U.frame(o.lat, o.lon);
      const x = U.num(g('x')), y = U.num(g('y'));
      if (U.isNum(x) && U.isNum(y)) { lat = f.lat(y); lon = f.lon(x); }
    }
    return {
      t: U.parseTime(g('time'), { tz: opts.tz || 0 }), lat, lon, alt, cls: g('cls'), spd: U.num(g('spd')),
      hdg: m.pos === 'latlon' ? U.num(g('hdg')) : U.num(g('hdg')), el: m.pos === 'latlon' ? U.num(g('el')) : NaN,
      rcs: U.num(g('rcs')), score: U.num(g('score')),
    };
  }
  function recordOf(n, parents) {
    const rec = flatten(n, '', {}, 0);
    let p = parents.get(n), lvl = 1;
    while (p && p.name !== '#root' && lvl <= 2) {
      for (const k in p.attrs) rec['^' + lvl + ':@' + k] = p.attrs[k];
      for (const c of p.children) if (!c.children.length && c.text.trim() && c !== n) rec['^' + lvl + ':' + c.name] = c.text.trim();
      p = parents.get(p); lvl++;
    }
    return rec;
  }
  R.setOrigin = function (store, origin, name) {
    if (!origin || !U.isNum(origin.lat) || !U.isNum(origin.lon)) return;
    const s = { id: '0', name: name || store.name || 'Radar', lat: origin.lat, lon: origin.lon, alt: U.isNum(origin.alt) ? origin.alt : 0, ground: U.isNum(origin.ground) ? origin.ground : U.isNum(origin.alt) ? origin.alt : 0, state: '', blanking: [] };
    store.sensors = [s].concat(store.sensors.filter((x) => x.id !== '0'));
    store.sensorMap.set('0', s);
  };
  R.extractXML = async function (cand, mapping, opts, name) {
    opts = opts || {};
    const store = R.newStore('xml', name);
    const parents = R._parents || new Map();
    let i = 0;
    for (const n of cand.nodes) {
      const rec = recordOf(n, parents);
      const u = readRec(rec, mapping, opts);
      if (!U.isNum(u.t)) u.t = (opts.baseTime || Date.UTC(2000, 0, 1)) + i * 1000;
      R.addUpdate(store, mapping.id ? String(rec[mapping.id]) : 'track', u);
      if (++i % 2000 === 0) await U.tick();
    }
    store.messages = cand.count;
    R.setOrigin(store, opts.origin, name);
    store.mapping = { tag: cand.tag, mapping, opts };
    return R.finalize(store);
  };
  // Live feed for non-Robin XML: apply a mapping found earlier to one more message.
  R.ingestGeneric = function (store, xmlText, live) {
    const root = X.parse(xmlText);
    const parents = new Map();
    const nodes = [];
    X.walk(root, (n, d, p) => { if (p) parents.set(n, p); if (n.name === live.tag) nodes.push(n); });
    for (const n of nodes) {
      const rec = recordOf(n, parents);
      const u = readRec(rec, live.mapping, live.opts || {});
      if (!U.isNum(u.t)) u.t = Date.now();
      R.addUpdate(store, live.mapping.id ? String(rec[live.mapping.id]) : 'track', u);
    }
    store.messages++;
  };

  // CSV radar tracks: header row + rows, same field detection on the column names.
  R.analyzeCSV = function (rows) {
    let hi = rows.findIndex((r) => r.length >= 3 && r.filter((c) => /[A-Za-z]/.test(c)).length >= 3);
    if (hi < 0) return null;
    const header = rows[hi].map((h) => h.trim());
    const body = rows.slice(hi + 1).filter((r) => r.length >= header.length - 1 && r.some((c) => c.trim()));
    const sample = {};
    for (const r of body.slice(0, 30)) header.forEach((h, j) => { if (sample[h] == null && r[j] != null && r[j].trim() !== '') sample[h] = r[j].trim(); });
    const keys = header.filter(Boolean);
    const mapping = guessMapping(keys.map((k) => k.replace(/\s*[[(].*?[\])]\s*$/, '')), Object.fromEntries(keys.map((k) => [k.replace(/\s*[[(].*?[\])]\s*$/, ''), sample[k]])));
    // map the cleaned names back to the real headers
    const clean = Object.fromEntries(keys.map((k) => [k.replace(/\s*[[(].*?[\])]\s*$/, ''), k]));
    for (const f of R.FIELDS) if (mapping[f]) mapping[f] = clean[mapping[f]] || mapping[f];
    const hasPos = (mapping.lat && mapping.lon) || (mapping.range && mapping.az) || (mapping.x && mapping.y);
    return hasPos ? { tag: 'CSV rows', count: body.length, keys, sample, mapping, header, body } : null;
  };
  R.extractCSV = async function (cand, mapping, opts, name) {
    opts = opts || {};
    const store = R.newStore('csv', name);
    const idx = Object.fromEntries(cand.header.map((h, j) => [h, j]));
    let i = 0;
    for (const r of cand.body) {
      const rec = {};
      for (const f of R.FIELDS) if (mapping[f] && idx[mapping[f]] != null) rec[mapping[f]] = r[idx[mapping[f]]];
      const u = readRec(rec, mapping, opts);
      if (!U.isNum(u.t)) continue;
      R.addUpdate(store, mapping.id ? String(rec[mapping.id]) : 'track', u);
      if (++i % 4000 === 0) await U.tick();
    }
    store.messages = cand.count;
    R.setOrigin(store, opts.origin, name);
    return R.finalize(store);
  };
})(typeof window !== 'undefined' ? window : globalThis);
