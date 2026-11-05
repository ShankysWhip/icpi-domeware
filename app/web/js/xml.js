/* Orbit: a small, forgiving XML parser.
   It runs anywhere (no DOMParser), accepts several root elements in one text (radar logs are
   often concatenated messages), drops namespace prefixes and keeps going past most damage. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const X = SF.xml = {};
  const EMPTY = Object.freeze({});
  const ENT = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };

  function decode(s) {
    if (s.indexOf('&') < 0) return s;
    return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
      if (e[0] === '#') {
        const c = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return isFinite(c) ? String.fromCodePoint(c) : m;
      }
      return ENT[e] != null ? ENT[e] : m;
    });
  }
  X.decode = decode;
  const local = (n) => { const i = n.indexOf(':'); return i < 0 ? n : n.slice(i + 1); };
  const RE_ATTR = /([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g;
  function parseAttrs(s) {
    let out = null, m;
    RE_ATTR.lastIndex = 0;
    while ((m = RE_ATTR.exec(s))) {
      const k = m[1];
      if (k === 'xmlns' || k.startsWith('xmlns:')) continue;
      (out || (out = {}))[local(k)] = decode(m[2] != null ? m[2] : m[3] != null ? m[3] : m[4]);
    }
    return out || EMPTY;
  }
  function tagEnd(text, from) {
    let gt = text.indexOf('>', from);
    if (gt < 0) return -1;
    const q1 = text.indexOf('"', from), q2 = text.indexOf("'", from);
    if ((q1 < 0 || q1 > gt) && (q2 < 0 || q2 > gt)) return gt;
    let q = 0;
    for (let i = from; i < text.length; i++) {
      const c = text.charCodeAt(i);
      if (q) { if (c === q) q = 0; }
      else if (c === 34 || c === 39) q = c;
      else if (c === 62) return i;
    }
    return -1;
  }

  /* Parse text into {name, attrs, children, text}. The returned root is a synthetic '#root'
     whose children are the document's top-level elements. */
  X.parse = function (text) {
    const root = { name: '#root', attrs: EMPTY, children: [], text: '' };
    const stack = [root];
    let top = root, i = 0;
    const n = text.length;
    while (i < n) {
      const lt = text.indexOf('<', i);
      if (lt < 0) break;
      if (lt > i && top !== root) {
        const raw = text.slice(i, lt);
        if (/\S/.test(raw)) top.text += decode(raw);
      }
      const c = text.charCodeAt(lt + 1);
      if (c === 33) { // !
        if (text.startsWith('<!--', lt)) { const e = text.indexOf('-->', lt + 4); i = e < 0 ? n : e + 3; continue; }
        if (text.startsWith('<![CDATA[', lt)) {
          const e = text.indexOf(']]>', lt + 9);
          if (top !== root) top.text += text.slice(lt + 9, e < 0 ? n : e);
          i = e < 0 ? n : e + 3;
          continue;
        }
        const e = text.indexOf('>', lt); i = e < 0 ? n : e + 1; continue;
      }
      if (c === 63) { const e = text.indexOf('?>', lt); i = e < 0 ? n : e + 2; continue; }
      const gt = tagEnd(text, lt + 1);
      if (gt < 0) break;
      if (c === 47) { // closing tag
        const name = local(text.slice(lt + 2, gt).trim());
        for (let k = stack.length - 1; k > 0; k--) {
          if (stack[k].name === name) { stack.length = k; top = stack[k - 1]; break; }
        }
        i = gt + 1;
        continue;
      }
      let body = text.slice(lt + 1, gt);
      const selfClose = body.charCodeAt(body.length - 1) === 47;
      if (selfClose) body = body.slice(0, -1);
      let sp = 0;
      while (sp < body.length && body.charCodeAt(sp) > 32) sp++;
      const node = { name: local(body.slice(0, sp)), attrs: sp < body.length ? parseAttrs(body.slice(sp)) : EMPTY, children: [], text: '' };
      top.children.push(node);
      if (!selfClose) { stack.push(node); top = node; }
      i = gt + 1;
    }
    return root;
  };

  // ------------------------------------------------------------------ navigation
  X.child = function (node, name) {
    if (!node) return null;
    const ch = node.children;
    for (let i = 0; i < ch.length; i++) if (ch[i].name === name) return ch[i];
    return null;
  };
  X.children = (node, name) => (node ? node.children.filter((c) => c.name === name) : []);
  // 'Position/Latitude' or 'Position/@lat'
  X.get = function (node, path) {
    const parts = path.split('/');
    let cur = node;
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (!cur) return null;
      if (p[0] === '@') return cur.attrs[p.slice(1)] != null ? cur.attrs[p.slice(1)] : null;
      cur = X.child(cur, p);
    }
    return cur ? cur.text.trim() : null;
  };
  X.num = function (node, path) {
    const v = X.get(node, path);
    return v == null || v === '' ? NaN : parseFloat(v);
  };
  X.walk = function (node, fn, depth = 0, parent = null) {
    if (fn(node, depth, parent) === false) return;
    for (const c of node.children) X.walk(c, fn, depth + 1, node);
  };
})(typeof window !== 'undefined' ? window : globalThis);
