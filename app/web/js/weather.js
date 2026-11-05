/* Orbit: weather station readers.
   CSV/TSV/xlsx exports such as Vaisala's observation export (metadata rows, then a
   "Timestamp" header), plain logger CSVs, and raw Vaisala WXT ASCII lines (0R0,Dm=…,Sm=…). */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const W = SF.weather = {};

  W.KINDS = {
    wind: { label: 'Wind', unit: 'm/s' }, gust: { label: 'Gust', unit: 'm/s' }, windMin: { label: 'Wind min', unit: 'm/s' },
    dir: { label: 'Wind dir', unit: '°' }, gustDir: { label: 'Gust dir', unit: '°' }, dirMin: { label: 'Dir min', unit: '°' }, dirMax: { label: 'Dir max', unit: '°' },
    temp: { label: 'Temp', unit: '°C' }, dew: { label: 'Dew point', unit: '°C' }, rh: { label: 'Humidity', unit: '%' },
    pres: { label: 'Pressure', unit: 'hPa' }, rainRate: { label: 'Rain', unit: 'mm/h' }, rain: { label: 'Rain sum', unit: 'mm' },
    rain1h: { label: 'Rain 1 h', unit: 'mm' }, rain10m: { label: 'Rain 10 min', unit: 'mm' }, rain24h: { label: 'Rain 24 h', unit: 'mm' },
    vis: { label: 'Visibility', unit: 'm' }, hail: { label: 'Hail', unit: 'hits/cm²' },
  };

  // Column name -> {kind, factor} (factor converts to the unit in KINDS).
  function classify(name) {
    const n = name.toLowerCase();
    const unit = (/[([]\s*([^)\]]+)\s*[)\]]/.exec(name) || [])[1] || '';
    const u = unit.toLowerCase();
    let kind = null;
    const isDir = /dir|suunta|riktning|\bd[mnx]\b|\bwd\b|bearing|°\s*\)?$/.test(n) && !/dew/.test(n) || /\(\s*°\s*\)/.test(n) && /wind|tuul/.test(n);
    if (/gust|puuska|by\b|vindby/.test(n)) kind = isDir ? 'gustDir' : 'gust';
    else if (/wind|tuul|vind|\bws\b|\bwspd\b|\bs[mnx]\b/.test(n)) {
      if (isDir) kind = /max|\bdx\b/.test(n) ? 'dirMax' : /min|\bdn\b/.test(n) ? 'dirMin' : 'dir';
      else kind = /max|\bsx\b/.test(n) ? 'gust' : /min|\bsn\b/.test(n) ? 'windMin' : 'wind';
    } else if (/dew|kaste|dagg|\btd\b/.test(n)) kind = 'dew';
    else if (/temp|lämpö|lampo|\bta\b|air t|\bt2m?\b|°c|°f/.test(n)) kind = 'temp';
    else if (/humid|kosteus|fukt|\brh\b|\bua\b/.test(n)) kind = 'rh';
    else if (/press|paine|tryck|baro|\bpa\b|qnh|qfe|\bhpa\b/.test(n)) kind = 'pres';
    else if (/intens|rate|\bri\b|mm\/h/.test(n) && /rain|precip|sade|neder|\bri\b|mm\/h/.test(n)) kind = 'rainRate';
    else if (/rain|precip|sade|neder|\brc\b/.test(n)) kind = /24\s*h|pt24h/.test(n) ? 'rain24h' : /1\s*h\b|pt1h/.test(n) ? 'rain1h' : /10\s*m|pt10m/.test(n) ? 'rain10m' : 'rain';
    else if (/visib|näky|sikt/.test(n)) kind = 'vis';
    else if (/hail|rae|\bhc\b/.test(n)) kind = 'hail';
    if (!kind) return null;
    let factor = 1, offset = 0;
    if (/km\/h|kmh|kph/.test(u)) factor = 1 / 3.6;
    else if (/^(kt|kn|knots?)$/.test(u)) factor = 0.514444;
    else if (/mph/.test(u)) factor = 0.44704;
    else if (/°f|degf|\bf\b/.test(u) && (kind === 'temp' || kind === 'dew')) { factor = 5 / 9; offset = -32; }
    else if (/kpa/.test(u)) factor = 10;
    else if (/inhg/.test(u)) factor = 33.8639;
    else if (/^pa$/.test(u)) factor = 0.01;
    else if (/^in(ch)?$/.test(u)) factor = 25.4;
    return { kind, factor, offset, unit };
  }

  function emptySet() {
    const s = { t: [] };
    for (const k in W.KINDS) s[k] = [];
    return s;
  }

  /* rows: array of string arrays (from CSV or xlsx). Returns a weather dataset or throws. */
  W.fromRows = function (rows, opts) {
    opts = opts || {};
    const meta = {};
    let hi = -1;
    for (let i = 0; i < Math.min(rows.length, 200); i++) {
      const r = rows[i].map((c) => (c == null ? '' : String(c).trim()));
      if (!r.some(Boolean)) continue;
      // metadata rows like  "Latitude:", "65.17", "Longitude:", "26.04"
      if (r.some((c) => /:$/.test(c)) && r.filter(Boolean).length <= 10) {
        for (let j = 0; j < r.length - 1; j++) if (/:$/.test(r[j]) && r[j + 1]) meta[r[j].replace(/:$/, '').trim()] = r[j + 1];
        continue;
      }
      if (r.length >= 2 && /time|date|aika|pvm|tid|datum|utc|stamp/i.test(r[0]) && r.slice(1).some((c) => classify(c))) { hi = i; break; }
      if (r.length >= 3 && r.filter((c) => classify(c)).length >= 2) { hi = i; break; }
    }
    if (hi < 0) throw new Error('No header row with weather columns (wind, temperature, …) was found.');
    const header = rows[hi].map((c) => String(c == null ? '' : c).trim());
    let tcol = header.findIndex((h) => /time|date|aika|pvm|tid|datum|stamp|utc/i.test(h));
    if (tcol < 0) tcol = 0;
    // a separate time-of-day column next to a date column
    const tcol2 = header.findIndex((h, j) => j !== tcol && /^(time|aika|klo|tid|hh:mm(:ss)?)\b/i.test(h) && !/stamp|date/i.test(h));
    const cols = [];
    header.forEach((h, j) => {
      if (j === tcol || j === tcol2) return;
      const c = classify(h);
      if (c && !cols.some((x) => x.kind === c.kind)) cols.push(Object.assign({ j, name: h }, c));
    });
    let tz = 0, tzNote = 'UTC';
    const tzMeta = meta['Time Zone'] || meta['Timezone'] || meta['Aikavyöhyke'] || '';
    if (U.isNum(opts.tz)) { tz = opts.tz; tzNote = U.fmtOffset(tz) + ' (set)'; }
    else if (tzMeta && !/utc|gmt|z$/i.test(tzMeta)) {
      const m = /([+-]\d{1,2})(?::?(\d{2}))?/.exec(tzMeta);
      if (m) { tz = parseInt(m[1], 10) * 60 + Math.sign(parseInt(m[1], 10)) * parseInt(m[2] || '0', 10); tzNote = tzMeta; }
      else tzNote = tzMeta + ' (read as UTC)';
    } else if (/local|paikall/i.test(header[tcol])) tzNote = 'local time (read as UTC, adjust the offset)';
    const ds = emptySet();
    const notes = [];
    let restored = 0, bad = 0;
    for (let i = hi + 1; i < rows.length; i++) {
      const r = rows[i];
      if (!r || !r[tcol]) continue;
      let ts = r[tcol];
      if (tcol2 >= 0 && r[tcol2]) ts = String(ts).trim() + ' ' + String(r[tcol2]).trim();
      const t = typeof ts === 'number' ? U.epochGuess(ts) : U.parseTime(ts, { tz, dayFirst: opts.dayFirst });
      if (!U.isNum(t)) { bad++; continue; }
      ds.t.push(t);
      for (const k in W.KINDS) ds[k].push(NaN);
      const at = ds.t.length - 1;
      for (const c of cols) {
        let v = r[c.j];
        let x = typeof v === 'number' ? v : U.num(v);
        if (!U.isNum(x)) continue;
        // A spreadsheet app may have turned "4.07" into the date 4 July (serial 46207). Undo it.
        if (x > 30000 && x < 80000 && c.kind !== 'pres' && c.kind !== 'vis') {
          const d = new Date(Math.round((x - 25569) * 86400000));
          x = d.getUTCDate() + (d.getUTCMonth() + 1) / 100;
          restored++;
        }
        ds[c.kind][at] = x * c.factor + c.offset * c.factor;
      }
    }
    if (!ds.t.length) throw new Error('No rows with a readable time were found.');
    // sort by time (some exports are newest first)
    if (ds.t.some((t, i) => i && t < ds.t[i - 1])) {
      const order = ds.t.map((t, i) => i).sort((a, b) => ds.t[a] - ds.t[b]);
      for (const k in ds) ds[k] = order.map((i) => ds[k][i]);
    }
    if (restored) notes.push(restored + ' cells had been turned into dates by a spreadsheet app and were restored (for example 46207 → 4.07).');
    if (bad) notes.push(bad + ' rows had no readable time and were skipped.');
    const lat = U.num(meta.Latitude || meta.Lat || meta.Leveysaste), lon = U.num(meta.Longitude || meta.Lon || meta.Pituusaste);
    const step = ds.t.length > 2 ? U.stats(ds.t.slice(1).map((t, i) => t - ds.t[i])).median : NaN;
    return {
      kind: 'weather', name: meta.Device || meta.Station || opts.name || 'Weather', meta, columns: cols, data: ds,
      station: U.isNum(lat) && U.isNum(lon) ? { lat, lon, alt: U.num(meta.Altitude), name: meta.Device || meta.Station || '' } : null,
      t0: ds.t[0], t1: ds.t[ds.t.length - 1], stepMs: step, tzNote, notes, sensors: meta['Sensor(s)'] || meta.Sensors || '',
    };
  };

  // Raw Vaisala WXT5xx ASCII messages: "0R1,Dn=236D,Dm=283D,Dx=031D,Sn=0.0M,Sm=1.0M,Sx=2.2M" etc.
  const WXT = { Dm: 'dir', Dn: 'dirMin', Dx: 'dirMax', Sm: 'wind', Sn: 'windMin', Sx: 'gust', Ta: 'temp', Ua: 'rh', Pa: 'pres', Rc: 'rain', Ri: 'rainRate', Hc: 'hail', Tp: null, Ta2: null };
  W.isWXT = (text) => /\b\dR[0-9C],/.test(text.slice(0, 4000)) && /\b(Dm|Sm|Ta|Pa)=/.test(text.slice(0, 4000));
  W.fromWXT = function (text, opts) {
    opts = opts || {};
    const ds = emptySet();
    const byTime = new Map();
    for (const line of text.split(/\r?\n/)) {
      const m = /(\dR[0-9C]),(.*)$/.exec(line);
      if (!m) continue;
      const pre = line.slice(0, m.index).replace(/[,;\s]+$/, '');
      const t = U.parseTime(pre, { tz: opts.tz || 0 });
      if (!U.isNum(t)) continue;
      const tk = Math.round(t / 1000) * 1000;
      let rec = byTime.get(tk);
      if (!rec) byTime.set(tk, (rec = {}));
      for (const kv of m[2].split(',')) {
        const mm = /^(\w\w)=(-?[\d.]+)([A-Z#])?$/.exec(kv.trim());
        if (!mm || !WXT[mm[1]]) continue;
        let x = parseFloat(mm[2]);
        const u = mm[3];
        if (u === 'K') x /= 3.6; else if (u === 'S') x *= 1.15078 * 0.44704; else if (u === 'N') x *= 0.514444;
        else if (u === 'F') x = (x - 32) * 5 / 9; else if (u === 'I' && WXT[mm[1]] === 'pres') x *= 33.8639;
        if (u === '#') continue;
        rec[WXT[mm[1]]] = x;
      }
    }
    const times = [...byTime.keys()].sort((a, b) => a - b);
    if (!times.length) throw new Error('No time-stamped WXT messages found. Lines need a time before "0R0,…".');
    for (const t of times) {
      const rec = byTime.get(t);
      ds.t.push(t);
      for (const k in W.KINDS) ds[k].push(U.isNum(rec[k]) ? rec[k] : NaN);
    }
    const cols = Object.keys(W.KINDS).filter((k) => ds[k].some(U.isNum)).map((k) => ({ kind: k, name: k, factor: 1 }));
    return { kind: 'weather', name: opts.name || 'Vaisala WXT', meta: {}, columns: cols, data: ds, station: null, t0: times[0], t1: times[times.length - 1], stepMs: NaN, tzNote: 'UTC', notes: [], sensors: 'WXT' };
  };

  W.fromText = function (text, opts) {
    if (W.isWXT(text)) return W.fromWXT(text, opts);
    return W.fromRows(U.parseCSV(text), opts);
  };

  // Value of a weather kind at time t (nearest sample within maxAge ms, else NaN).
  W.at = function (wx, kind, t, maxAge) {
    const d = wx.data, arr = d[kind];
    if (!arr) return NaN;
    const i = U.idxNear(d.t, t);
    if (i < 0) return NaN;
    if (Math.abs(d.t[i] - t) > (maxAge || Math.max(2 * (wx.stepMs || 60000), 120000))) return NaN;
    if (U.isNum(arr[i])) return arr[i];
    for (let k = 1; k < 4; k++) {
      if (U.isNum(arr[i - k])) return arr[i - k];
      if (U.isNum(arr[i + k])) return arr[i + k];
    }
    return NaN;
  };
})(typeof window !== 'undefined' ? window : globalThis);
