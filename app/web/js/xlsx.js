/* Orbit: just enough of .xlsx (and .zip) to read the first sheet as rows of strings. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const Z = SF.zip = {};

  Z.isZip = (b) => b[0] === 0x50 && b[1] === 0x4b && b[2] === 0x03 && b[3] === 0x04;
  Z.entries = function (bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let eocd = -1;
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 70000); i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('Not a zip file (no end record).');
    const n = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const out = [];
    for (let k = 0; k < n; k++) {
      if (dv.getUint32(p, true) !== 0x02014b50) break;
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
      const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const off = dv.getUint32(p + 42, true);
      const name = SF.util.utf8(bytes, p + 46, p + 46 + nlen);
      out.push({ name, method, csize, usize, off });
      p += 46 + nlen + xlen + clen;
    }
    return out;
  };
  async function inflateRaw(data) {
    if (typeof DecompressionStream === 'undefined') throw new Error('This viewer cannot unzip files (no DecompressionStream).');
    const ds = new DecompressionStream('deflate-raw');
    const stream = new Blob([data]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  Z.read = async function (bytes, entry) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const p = entry.off;
    const start = p + 30 + dv.getUint16(p + 26, true) + dv.getUint16(p + 28, true);
    const data = bytes.subarray(start, start + entry.csize);
    if (entry.method === 0) return data;
    if (entry.method === 8) return inflateRaw(data);
    throw new Error('Unsupported zip compression method ' + entry.method);
  };
  Z.readText = async (bytes, entry) => SF.util.decodeText(await Z.read(bytes, entry));

  // First worksheet of an .xlsx as rows of strings (numbers stay as their stored text).
  SF.xlsx = async function (bytes) {
    const ents = Z.entries(bytes);
    const find = (re) => ents.find((e) => re.test(e.name));
    const X = SF.xml;
    let shared = [];
    const ss = find(/^xl\/sharedStrings\.xml$/i);
    if (ss) {
      const root = X.parse(await Z.readText(bytes, ss));
      const sst = root.children.find((c) => c.name === 'sst');
      if (sst) shared = sst.children.filter((c) => c.name === 'si').map((si) => {
        let s = '';
        X.walk(si, (n) => { if (n.name === 't') s += n.text; });
        return s;
      });
    }
    const sheet = find(/^xl\/worksheets\/sheet1\.xml$/i) || find(/^xl\/worksheets\/.*\.xml$/i);
    if (!sheet) throw new Error('The workbook has no worksheet.');
    const root = X.parse(await Z.readText(bytes, sheet));
    const rows = [];
    X.walk(root, (n) => {
      if (n.name !== 'row') return;
      const vals = [];
      for (const c of n.children) {
        if (c.name !== 'c') continue;
        const ref = /^([A-Z]+)/.exec(c.attrs.r || '');
        let col = vals.length;
        if (ref) { col = 0; for (const ch of ref[1]) col = col * 26 + (ch.charCodeAt(0) - 64); col -= 1; }
        let v = '';
        const vn = X.child(c, 'v');
        if (c.attrs.t === 's' && vn) v = shared[parseInt(vn.text, 10)] || '';
        else if (c.attrs.t === 'inlineStr') { const is = X.child(c, 'is'); X.walk(is || c, (k) => { if (k.name === 't') v += k.text; }); }
        else if (vn) v = vn.text;
        vals[col] = v;
      }
      const r = rows.length;
      const idx = parseInt(n.attrs.r || '0', 10);
      while (idx && rows.length < idx - 1) rows.push([]);
      rows.push(Array.from(vals, (x) => (x == null ? '' : x)));
      return false;
    });
    return rows;
  };
})(typeof window !== 'undefined' ? window : globalThis);
