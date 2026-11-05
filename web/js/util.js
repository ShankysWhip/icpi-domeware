/* Orbit: shared helpers for time, geodesy, text, CSV and numbers.
   Every time value in the app is milliseconds since 1970-01-01 UTC. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util = {};

  U.pad = (n, w = 2) => String(n).padStart(w, '0');
  U.clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
  U.isNum = (x) => typeof x === 'number' && isFinite(x);
  U.tick = () => new Promise((r) => setTimeout(r, 0));

  // ------------------------------------------------------------------ formatting
  U.f = function (x, d = 1, unit = '') {
    if (!U.isNum(x)) return '–';
    let s = x.toFixed(d);
    if (/^-0(\.0+)?$/.test(s)) s = s.slice(1);
    return s + (unit ? ' ' + unit : '');
  };
  U.fmtClock = function (ms, offsetMin = 0, tenths = false) {
    if (!U.isNum(ms)) return '––:––:––';
    const d = new Date(ms + offsetMin * 60000);
    let s = U.pad(d.getUTCHours()) + ':' + U.pad(d.getUTCMinutes()) + ':' + U.pad(d.getUTCSeconds());
    if (tenths) s += '.' + Math.floor(d.getUTCMilliseconds() / 100);
    return s;
  };
  U.fmtDate = function (ms, offsetMin = 0) {
    if (!U.isNum(ms)) return '';
    const d = new Date(ms + offsetMin * 60000);
    return d.getUTCFullYear() + '-' + U.pad(d.getUTCMonth() + 1) + '-' + U.pad(d.getUTCDate());
  };
  U.fmtISO = (ms) => (U.isNum(ms) ? new Date(ms).toISOString() : '');
  U.fmtDur = function (s, short = false) {
    if (!U.isNum(s)) return '–';
    const neg = s < 0; s = Math.abs(s);
    if (short || s >= 10) s = Math.round(s); // round first, so 239.96 s reads 4 min, not 3 min 60 s
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    let out;
    if (short) out = (h ? h + ':' + U.pad(m) : m) + ':' + U.pad(sec);
    else if (h) out = h + ' h' + (m ? ' ' + m + ' min' : '');
    else if (m) out = m + ' min' + (sec ? ' ' + sec + ' s' : '');
    else out = (sec < 10 ? sec.toFixed(1) : sec) + ' s';
    return (neg ? '−' : '') + out;
  };
  U.fmtOffset = function (min) {
    const sign = min < 0 ? '−' : '+';
    min = Math.abs(min);
    return 'UTC' + sign + Math.floor(min / 60) + (min % 60 ? ':' + U.pad(min % 60) : '');
  };
  U.fmtDist = function (m) {
    if (!U.isNum(m)) return '–';
    return Math.abs(m) >= 10000 ? (m / 1000).toFixed(1) + ' km' : Math.abs(m) >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m';
  };
  U.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ------------------------------------------------------------------ time parsing
  const MON = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  function zoneMin(z) {
    if (!z) return null;
    z = z.trim().toUpperCase();
    if (z === 'Z' || z === 'UTC' || z === 'GMT') return 0;
    const m = /^(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})?$/.exec(z);
    if (!m) return null;
    return (m[1] === '-' ? -1 : 1) * (parseInt(m[2], 10) * 60 + parseInt(m[3] || '0', 10));
  }
  function build(y, mo, d, h, mi, s, frac, ampm, zone, tzOffsetMin) {
    h = +h || 0; mi = +mi || 0; s = +s || 0;
    if (ampm) {
      ampm = ampm.toUpperCase();
      if (ampm === 'PM' && h < 12) h += 12;
      if (ampm === 'AM' && h === 12) h = 0;
    }
    if (y < 100) y += 2000;
    if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 24 || mi > 59 || s > 61) return NaN;
    const ms = frac ? Math.round(parseFloat('0.' + frac) * 1000) : 0;
    const z = zoneMin(zone);
    const off = z == null ? (tzOffsetMin || 0) : z;
    return Date.UTC(y, mo - 1, d, h, mi, s, ms) - off * 60000;
  }
  const RE_ISO = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ]+(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/i;
  const RE_YMD_SLASH = /^(\d{4})[./](\d{1,2})[./](\d{1,2})(?:[T ]+(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/i;
  const RE_DMY = /^(\d{1,2})([-./])(\d{1,2})\2(\d{2,4})(?:[T ,]+(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?\s*(AM|PM)?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/i;
  const RE_TEXTMON = /^(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -](\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/;
  const RE_TIME = /^(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?\s*(AM|PM)?$/i;

  /* Parse a date/time string into UTC ms. opts.tz = minutes east of UTC used when the string
     carries no zone. opts.dayFirst = true|false|null (null: guess). opts.baseDate = UTC ms of
     the day for time-only strings. Returns NaN when the text is not a time. */
  U.parseTime = function (str, opts) {
    opts = opts || {};
    if (str == null) return NaN;
    if (typeof str === 'number') return U.epochGuess(str);
    let s = String(str).trim().replace(/^"|"$/g, '');
    if (!s) return NaN;
    if (/^-?\d+(\.\d+)?$/.test(s)) return U.epochGuess(parseFloat(s));
    let m = RE_ISO.exec(s);
    if (m) return build(+m[1], +m[2], +m[3], m[4], m[5], m[6], m[7], null, m[8], opts.tz);
    m = RE_YMD_SLASH.exec(s);
    if (m) return build(+m[1], +m[2], +m[3], m[4], m[5], m[6], m[7], null, m[8], opts.tz);
    m = RE_DMY.exec(s);
    if (m) {
      let a = +m[1], b = +m[3];
      let dayFirst = opts.dayFirst;
      if (dayFirst == null) dayFirst = a > 12 ? true : b > 12 ? false : m[2] !== '/';
      const d = dayFirst ? a : b, mo = dayFirst ? b : a;
      return build(+m[4], mo, d, m[5], m[6], m[7], m[8], m[9], m[10], opts.tz);
    }
    m = RE_TEXTMON.exec(s);
    if (m && MON[m[2].toLowerCase()]) return build(+m[3], MON[m[2].toLowerCase()], +m[1], m[4], m[5], m[6], m[7], null, m[8], opts.tz);
    m = RE_TIME.exec(s);
    if (m && U.isNum(opts.baseDate)) {
      const d = new Date(opts.baseDate);
      return build(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(), m[1], m[2], m[3], m[4], m[5], null, opts.tz);
    }
    const t = Date.parse(s);
    return isNaN(t) ? NaN : t;
  };
  // A bare number: Unix seconds, Unix milliseconds or an Excel/Lotus day serial.
  U.epochGuess = function (x) {
    if (!U.isNum(x)) return NaN;
    if (x > 1e11 && x < 1e14) return Math.round(x);          // ms
    if (x > 1e8 && x < 1e11) return Math.round(x * 1000);    // s
    if (x > 20000 && x < 80000) return Math.round((x - 25569) * 86400000); // Excel serial
    return NaN;
  };

  // ------------------------------------------------------------------ geodesy
  const D2R = Math.PI / 180, R_EARTH = 6371008.8;
  U.D2R = D2R;
  U.dist = function (lat1, lon1, lat2, lon2) {
    const dLat = (lat2 - lat1) * D2R, dLon = (lon2 - lon1) * D2R;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * D2R) * Math.cos(lat2 * D2R) * Math.sin(dLon / 2) ** 2;
    return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(a)));
  };
  U.bearing = function (lat1, lon1, lat2, lon2) {
    const p1 = lat1 * D2R, p2 = lat2 * D2R, dl = (lon2 - lon1) * D2R;
    const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return (Math.atan2(y, x) / D2R + 360) % 360;
  };
  U.destination = function (lat, lon, brgDeg, distM) {
    const d = distM / R_EARTH, b = brgDeg * D2R, p1 = lat * D2R, l1 = lon * D2R;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return [p2 / D2R, ((l2 / D2R + 540) % 360) - 180];
  };
  // Local east/north metres around an origin (fine for the few km a test range covers).
  U.frame = function (lat0, lon0) {
    const ky = 111132.92 - 559.82 * Math.cos(2 * lat0 * D2R) + 1.175 * Math.cos(4 * lat0 * D2R);
    const kx = 111412.84 * Math.cos(lat0 * D2R) - 93.5 * Math.cos(3 * lat0 * D2R);
    return {
      lat0, lon0, kx, ky,
      x: (lon) => (lon - lon0) * kx,
      y: (lat) => (lat - lat0) * ky,
      lon: (x) => lon0 + x / kx,
      lat: (y) => lat0 + y / ky,
    };
  };
  U.angDiff = (a, b) => ((((a - b) % 360) + 540) % 360) - 180;

  // ------------------------------------------------------------------ arrays and interpolation
  // Index of the last element <= x (-1 if none). arr must be sorted ascending.
  U.idxLE = function (arr, x) {
    let lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] <= x) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  };
  // Index of the element nearest to x.
  U.idxNear = function (arr, x) {
    if (!arr.length) return -1;
    const i = U.idxLE(arr, x);
    if (i < 0) return 0;
    if (i >= arr.length - 1) return arr.length - 1;
    return x - arr[i] <= arr[i + 1] - x ? i : i + 1;
  };
  // Linear interpolation of vs over ts at t; NaN outside the data or across gaps > maxGap ms.
  U.interp = function (ts, vs, t, maxGap) {
    const i = U.idxLE(ts, t);
    if (i < 0) return NaN;
    if (i === ts.length - 1) return t - ts[i] <= (maxGap || 0) ? vs[i] : NaN;
    const t0 = ts[i], t1 = ts[i + 1];
    if (maxGap && t1 - t0 > maxGap) return t - t0 <= maxGap / 2 ? vs[i] : t1 - t <= maxGap / 2 ? vs[i + 1] : NaN;
    const v0 = vs[i], v1 = vs[i + 1];
    if (!U.isNum(v0)) return v1;
    if (!U.isNum(v1)) return v0;
    return t1 === t0 ? v0 : v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  };
  U.interpAngle = function (ts, vs, t, maxGap) {
    const i = U.idxLE(ts, t);
    if (i < 0 || i >= ts.length - 1) return i === ts.length - 1 && t - ts[i] <= (maxGap || 0) ? vs[i] : NaN;
    const t0 = ts[i], t1 = ts[i + 1];
    if (maxGap && t1 - t0 > maxGap) return NaN;
    const d = U.angDiff(vs[i + 1], vs[i]);
    return (vs[i] + (t1 === t0 ? 0 : (d * (t - t0)) / (t1 - t0)) + 360) % 360;
  };
  U.quantile = function (sorted, q) {
    if (!sorted.length) return NaN;
    const p = (sorted.length - 1) * q, i = Math.floor(p), f = p - i;
    return i + 1 < sorted.length ? sorted[i] * (1 - f) + sorted[i + 1] * f : sorted[i];
  };
  U.stats = function (arr) {
    const v = arr.filter(U.isNum).sort((a, b) => a - b);
    if (!v.length) return { n: 0, mean: NaN, median: NaN, p95: NaN, max: NaN, min: NaN, rms: NaN, std: NaN };
    let s = 0, s2 = 0;
    for (const x of v) { s += x; s2 += x * x; }
    const mean = s / v.length;
    return { n: v.length, mean, median: U.quantile(v, 0.5), p95: U.quantile(v, 0.95), max: v[v.length - 1], min: v[0], rms: Math.sqrt(s2 / v.length), std: Math.sqrt(Math.max(0, s2 / v.length - mean * mean)) };
  };

  // ------------------------------------------------------------------ text
  function utf8Decode(bytes, start, end) {
    let out = '', i = start;
    const chunk = [];
    while (i < end) {
      let c = bytes[i++];
      if (c >= 0x80) {
        if (c >= 0xf0 && i + 2 < end + 1) { c = ((c & 7) << 18) | ((bytes[i] & 63) << 12) | ((bytes[i + 1] & 63) << 6) | (bytes[i + 2] & 63); i += 3; }
        else if (c >= 0xe0) { c = ((c & 15) << 12) | ((bytes[i] & 63) << 6) | (bytes[i + 1] & 63); i += 2; }
        else if (c >= 0xc0) { c = ((c & 31) << 6) | (bytes[i] & 63); i += 1; }
        else c = 0xfffd;
      }
      if (c > 0xffff) { c -= 0x10000; chunk.push(0xd800 + (c >> 10), 0xdc00 + (c & 1023)); } else chunk.push(c);
      if (chunk.length > 8000) { out += String.fromCharCode.apply(null, chunk); chunk.length = 0; }
    }
    return out + String.fromCharCode.apply(null, chunk);
  }
  function looksUtf8(bytes, n) {
    for (let i = 0; i < n; i++) {
      const c = bytes[i];
      if (c < 0x80) continue;
      let k = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : -1;
      if (k < 0) return false;
      for (let j = 1; j <= k; j++) if (i + j < n && (bytes[i + j] & 0xc0) !== 0x80) return false;
      i += k;
    }
    return true;
  }
  const CP1252 = { 128: 8364, 130: 8218, 131: 402, 132: 8222, 133: 8230, 134: 8224, 135: 8225, 136: 710, 137: 8240, 138: 352, 139: 8249, 140: 338, 142: 381, 145: 8216, 146: 8217, 147: 8220, 148: 8221, 149: 8226, 150: 8211, 151: 8212, 152: 732, 153: 8482, 154: 353, 155: 8250, 156: 339, 158: 382, 159: 376 };
  // Bytes to text: UTF-8 (with or without BOM), UTF-16 with BOM, else Windows-1252.
  U.decodeText = function (buf) {
    const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    if (bytes[0] === 0xff && bytes[1] === 0xfe || bytes[0] === 0xfe && bytes[1] === 0xff) {
      const le = bytes[0] === 0xff;
      if (typeof TextDecoder !== 'undefined') return new TextDecoder(le ? 'utf-16le' : 'utf-16be').decode(bytes.subarray(2));
      let s = '';
      for (let i = 2; i + 1 < bytes.length; i += 2) s += String.fromCharCode(le ? bytes[i] | (bytes[i + 1] << 8) : (bytes[i] << 8) | bytes[i + 1]);
      return s;
    }
    const start = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf ? 3 : 0;
    if (looksUtf8(bytes, Math.min(bytes.length, 1 << 20))) {
      if (typeof TextDecoder !== 'undefined') return new TextDecoder('utf-8').decode(bytes.subarray(start));
      return utf8Decode(bytes, start, bytes.length);
    }
    let s = '';
    const chunk = [];
    for (let i = 0; i < bytes.length; i++) {
      const c = bytes[i];
      chunk.push(CP1252[c] || c);
      if (chunk.length > 8000) { s += String.fromCharCode.apply(null, chunk); chunk.length = 0; }
    }
    return s + String.fromCharCode.apply(null, chunk);
  };
  U.utf8 = (bytes, start = 0, end = bytes.length) => utf8Decode(bytes, start, end);
  // Share of bytes that are printable ASCII or common whitespace (text files are close to 1).
  U.textShare = function (bytes, n = 4096) {
    n = Math.min(n, bytes.length);
    let ok = 0;
    for (let i = 0; i < n; i++) { const c = bytes[i]; if ((c >= 32 && c < 127) || c === 9 || c === 10 || c === 13 || c >= 0xc0) ok++; }
    return n ? ok / n : 0;
  };

  // ------------------------------------------------------------------ numbers and CSV
  U.num = function (s) {
    if (typeof s === 'number') return s;
    if (s == null) return NaN;
    s = String(s).trim();
    if (!s) return NaN;
    if (/^[-+]?\d+,\d+$/.test(s)) s = s.replace(',', '.');
    else if (/^[-+]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');
    const m = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?/.exec(s);
    if (!m) return NaN;
    const rest = s.slice(m[0].length).trim();
    if (rest && !/^[A-Za-z°%/µ.\s²³-]*$/.test(rest)) return NaN;
    return parseFloat(m[0]);
  };
  U.sniffDelimiter = function (text) {
    const lines = text.split(/\r?\n/).filter((l) => l.trim()).slice(0, 40);
    let best = ',', bestScore = -1;
    for (const d of [',', ';', '\t', '|']) {
      const counts = lines.map((l) => l.split(d).length - 1).filter((c) => c > 0);
      if (!counts.length) continue;
      counts.sort((a, b) => a - b);
      const med = counts[counts.length >> 1];
      const consistent = counts.filter((c) => c === med).length;
      const score = med * consistent;
      if (score > bestScore) { bestScore = score; best = d; }
    }
    return best;
  };
  U.parseCSV = function (text, delim) {
    delim = delim || U.sniffDelimiter(text);
    const rows = [];
    let row = [], field = '', i = 0, inQ = false;
    const n = text.length, dc = delim.charCodeAt(0);
    while (i < n) {
      const c = text.charCodeAt(i);
      if (inQ) {
        if (c === 34) {
          if (text.charCodeAt(i + 1) === 34) { field += '"'; i += 2; continue; }
          inQ = false; i++; continue;
        }
        field += text[i++];
        continue;
      }
      if (c === 34 && field === '') { inQ = true; i++; continue; }
      if (c === dc) { row.push(field); field = ''; i++; continue; }
      if (c === 10 || c === 13) {
        row.push(field); field = '';
        rows.push(row); row = [];
        if (c === 13 && text.charCodeAt(i + 1) === 10) i++;
        i++;
        continue;
      }
      field += text[i++];
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    return rows;
  };
  U.toCSV = function (header, rows) {
    const q = (v) => {
      if (v == null || (typeof v === 'number' && !isFinite(v))) return '';
      const s = String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [header.map(q).join(',')].concat(rows.map((r) => r.map(q).join(','))).join('\n') + '\n';
  };

  // ------------------------------------------------------------------ bytes
  const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  U.b64encode = function (bytes) {
    let s = '', i = 0;
    for (; i + 2 < bytes.length; i += 3) {
      const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      s += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
    }
    if (i < bytes.length) {
      const n = (bytes[i] << 16) | ((bytes[i + 1] || 0) << 8);
      s += B64[n >> 18] + B64[(n >> 12) & 63] + (i + 1 < bytes.length ? B64[(n >> 6) & 63] : '=') + '=';
    }
    return s;
  };
  U.b64decode = function (s) {
    s = String(s).replace(/[^A-Za-z0-9+/]/g, '');
    const out = new Uint8Array(Math.floor((s.length * 3) / 4));
    let o = 0;
    for (let i = 0; i < s.length; i += 4) {
      const n = (B64.indexOf(s[i]) << 18) | (B64.indexOf(s[i + 1]) << 12) | ((s[i + 2] ? B64.indexOf(s[i + 2]) : 0) << 6) | (s[i + 3] ? B64.indexOf(s[i + 3]) : 0);
      out[o++] = (n >> 16) & 255;
      if (s[i + 2]) out[o++] = (n >> 8) & 255;
      if (s[i + 3]) out[o++] = n & 255;
    }
    return out.subarray(0, o);
  };
  // 64-bit FNV-1a as hex; used as a stable id for a log's key material.
  U.fnv64 = function (bytes) {
    let h = 0xcbf29ce484222325n;
    const p = 0x100000001b3n, mask = 0xffffffffffffffffn;
    for (let i = 0; i < bytes.length; i++) { h ^= BigInt(bytes[i]); h = (h * p) & mask; }
    return h.toString(16).padStart(16, '0');
  };

  // ------------------------------------------------------------------ colours
  U.CLASS_COLORS = {
    DRONE: '#ff4fd8', SUSPECTED_DRONE: '#c38bff', UAV: '#ff4fd8', UAS: '#ff4fd8',
    BIRD: '#3ddc84', BIRDS: '#3ddc84', OTHER: '#9aa4b2', UNCLASSIFIED: '#5ac8fa', UNKNOWN: '#5ac8fa',
    PLANE: '#ffd60a', AIRCRAFT: '#ffd60a', HELICOPTER: '#ffd60a', MANNED_AIRCRAFT: '#ffd60a',
    VEHICLE: '#d0a77b', CAR: '#d0a77b', PERSON: '#ff9f80', CLUTTER: '#6b7380',
  };
  U.classColor = function (cls) {
    if (!cls) return '#5ac8fa';
    const k = String(cls).toUpperCase().replace(/[\s-]+/g, '_');
    if (U.CLASS_COLORS[k]) return U.CLASS_COLORS[k];
    if (/DRONE|UAV|UAS/.test(k)) return '#ff4fd8';
    if (/BIRD/.test(k)) return '#3ddc84';
    if (/PLANE|AIR|HELI/.test(k)) return '#ffd60a';
    let h = 0;
    for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return 'hsl(' + (h % 360) + ',55%,62%)';
  };
  U.DRONE_COLOR = '#ff7a1a';
})(typeof window !== 'undefined' ? window : globalThis);
