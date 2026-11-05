/* Orbit: personal licenses.
   A license is "ORBIT1-" + base64url(JSON) + "." + base64url(signature). It is made with the vendor's
   secret key by tools/license (orbit-license, Orbit License Maker), and Orbit checks it offline with the
   public key below: the ECDSA P-256 signature, the end date and, for a computer-locked license, this
   computer's ID. JSON fields: v (format 1), id, n (name), o (organisation), e (email), i (issued),
   x (valid until, YYYY-MM-DD), m (computer ID). */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const L = SF.license = {};
  const PUBLIC_KEY = 'BIaQL0SSWv2eGHpaovxlHVcGvgrI+bBsKm7o9GWKY2W1Ye+U0UGfkci1pDDmyCUofyhXr/meS7t3ymiHNH0wVGA=';
  const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64u = (s) => { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return b64(s); };
  const today = () => new Date().toISOString().slice(0, 10);

  // The code inside any text (a pasted email, a license file); line breaks inside the code are fine.
  L.parse = function (text) {
    text = String(text || '');
    const i = text.indexOf('ORBIT1-');
    if (i < 0) return null;
    // an ECDSA P-256 signature is always 64 bytes (86 characters), so text right after the code is ignored
    const m = /^ORBIT1-([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{86})/.exec(text.slice(i).replace(/\s+/g, ''));
    if (!m) return null;
    try {
      const payload = b64u(m[1]);
      return { code: 'ORBIT1-' + m[1] + '.' + m[2], payload, sig: b64u(m[2]), lic: JSON.parse(new TextDecoder().decode(payload)) };
    } catch (e) { return null; }
  };
  // This computer's ID as people see it: a hash of the helper's hardware id, never the id itself.
  L.machineCode = async function (raw) {
    if (!raw) return '';
    const h = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw + '|orbit-machine')));
    return [...h.slice(0, 8)].map((b) => b.toString(16).padStart(2, '0')).join('').toUpperCase().match(/.{4}/g).join('-');
  };
  // {ok, lic, code, reason}
  L.check = async function (text, machine, day) {
    const p = L.parse(text);
    if (!p) return { ok: false, reason: 'This is not an Orbit license code.' };
    let good = false;
    try {
      const key = await crypto.subtle.importKey('raw', b64(PUBLIC_KEY), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
      good = await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, p.sig, p.payload);
    } catch (e) { good = false; }
    if (!good || p.lic.v !== 1 || !p.lic.n) return { ok: false, reason: 'This code is not valid: it was changed, or it was not made for this Orbit.' };
    const lic = p.lic;
    if (lic.x && (day || today()) > lic.x) return { ok: false, lic, reason: 'This license ended on ' + lic.x + '. Ask for a new one.' };
    if (lic.m && lic.m !== machine) return { ok: false, lic, reason: 'This license is for another computer (' + lic.m + '); this computer is ' + (machine || 'unknown') + '.' };
    return { ok: true, lic, code: p.code };
  };
  L.who = (lic) => (lic ? lic.n + (lic.o ? ', ' + lic.o : '') : '');
  L.daysLeft = (lic, day) => (lic && lic.x ? Math.round((Date.parse(lic.x) - Date.parse(day || today())) / 86400000) : Infinity);
})(typeof window !== 'undefined' ? window : globalThis);
