/* Orbit: talks to the local helper (Swift on Mac, Python on Windows) when there is one.
   Without a helper (the page opened as a plain file) the app still works: map tiles come
   straight from the internet without the offline cache, and DJI unlocking and the live feed
   are unavailable. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const N = SF.native = { ok: false, token: '', info: null, online: typeof navigator !== 'undefined' ? navigator.onLine : true };

  const DIRECT = {
    'esri-imagery': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    'esri-labels': 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    'esri-topo': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    osm: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    opentopo: 'https://tile.opentopomap.org/{z}/{x}/{y}.png',
    terrain: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
  };

  N.init = async function () {
    let token = '';
    try {
      const q = new URLSearchParams(location.search);
      token = q.get('token') || sessionStorage.getItem('sf-token') || '';
      N.recovered = q.get('recovered') === '1';
      N.params = q; // ?t=<ISO time>&view=3d moves the playhead once data is in (automated screenshots)
    } catch (e) { /* storage blocked */ }
    if (!/^https?:$/.test(location.protocol)) return false;
    try {
      const r = await fetch('/api/info', { headers: { 'X-SF-Token': token }, cache: 'no-store' });
      if (r.ok) {
        N.info = await r.json();
        N.ok = true;
        N.token = token;
        N.online = N.info.online !== false;
        try { sessionStorage.setItem('sf-token', token); } catch (e) { /* ignore */ }
        if (location.search) history.replaceState(null, '', location.pathname);
      }
    } catch (e) { N.ok = false; }
    return N.ok;
  };
  const H = () => ({ 'X-SF-Token': N.token });
  N.get = async function (path) {
    const r = await fetch(path, { headers: H(), cache: 'no-store' });
    const j = await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
    if (!r.ok && j.ok === undefined) j.ok = false;
    j.httpStatus = r.status;
    return j;
  };
  N.post = async function (path, body) {
    const r = await fetch(path, { method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, H()), body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
    j.httpStatus = r.status;
    return j;
  };
  N.readFile = async function (path) {
    const r = await fetch('/api/file?path=' + encodeURIComponent(path), { headers: H() });
    if (!r.ok) throw new Error('Could not read ' + path);
    return new Uint8Array(await r.arrayBuffer());
  };
  N.tileUrl = function (src) {
    return N.ok ? location.origin + '/tiles/' + src + '/{z}/{x}/{y}' : DIRECT[src];
  };

  // Settings live in the helper's data folder; localStorage is the fallback.
  N.loadSettings = async function () {
    if (N.ok) { try { return await N.get('/api/settings'); } catch (e) { /* fall through */ } }
    try { return JSON.parse(localStorage.getItem('sf-settings') || '{}'); } catch (e) { return {}; }
  };
  N.saveSettings = async function (patch) {
    if (N.ok) return N.post('/api/settings', patch);
    let cur = {};
    try { cur = JSON.parse(localStorage.getItem('sf-settings') || '{}'); } catch (e) { /* ignore */ }
    Object.assign(cur, patch);
    try { localStorage.setItem('sf-settings', JSON.stringify(cur)); } catch (e) { /* ignore */ }
    return cur;
  };

  /* Save bytes: the helper shows the system Save dialog; without it the browser downloads. */
  N.save = async function (name, data, mime) {
    const blob = data instanceof Blob ? data : new Blob([data], { type: mime || 'application/octet-stream' });
    if (N.ok) {
      try {
        const r = await fetch('/api/save?name=' + encodeURIComponent(name), { method: 'POST', headers: H(), body: blob });
        const j = await r.json();
        if (j.ok) return { ok: true, path: j.path };
        if (j.cancelled) return { ok: false, cancelled: true };
      } catch (e) { /* fall back to a download */ }
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
    return { ok: true, path: null, downloaded: true };
  };
  N.reveal = (path) => (N.ok && path ? N.post('/api/reveal', { path }) : null);
  N.openFile = (path) => (N.ok && path ? N.post('/api/open-file', { path }) : null);
  /* HTML report to PDF through the helper (WebKit on Mac, Edge/Chrome on Windows). Shows the
     system Save dialog unless `path` is given. Returns {ok, path} or {ok:false, error|cancelled}. */
  N.pdf = async function (name, html, pages, path) {
    if (!N.ok) return { ok: false, error: 'no helper' };
    const q = '/api/pdf?name=' + encodeURIComponent(name) + '&pages=' + pages + (path ? '&path=' + encodeURIComponent(path) : '');
    try {
      const r = await fetch(q, { method: 'POST', headers: Object.assign({ 'Content-Type': 'text/html; charset=utf-8' }, H()), body: html });
      return await r.json();
    } catch (e) { return { ok: false, error: String(e.message || e) }; }
  };
})(typeof window !== 'undefined' ? window : globalThis);
