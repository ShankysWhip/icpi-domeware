/* Orbit: the application — imports, playback, map drawing, panels, settings, export, live feed. */
(function () {
  'use strict';
  const U = SF.util, A = SF.analysis, R = SF.radar;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = U.esc;

  const S = (SF.state = {
    settings: {}, drone: null, djiLog: null, djiSummary: null, radar: null, weather: null, radarRaw: null,
    files: { drone: '', radar: '', weather: '' }, offsets: { drone: 0, radar: 0, weather: 0 },
    t: NaN, range: { t0: NaN, t1: NaN }, playing: false, speed: 1, follow: false, analysis: null, aopts: A.defaults(),
    layers: { pathColor: 'status', dronePath: true, radarLive: true, radarTrails: true, radarAll: false, radarOnlyDrone: false, plots: false, errorLine: true, rings: true, labels: true, curtain: true, terrain: false },
    lanes: {}, selected: null, live: null, hits: [], liveFollow: false, meta: {},
    fleet: [], fleetAn: null, // all drones of the scene; S.drone is the selected one
  });
  S.tzOffset = () => (S.settings.tz === 'local' ? -new Date(U.isNum(S.t) ? S.t : Date.now()).getTimezoneOffset() : 0);
  S.tzLabel = () => (S.settings.tz === 'local' ? U.fmtOffset(S.tzOffset()) : 'UTC');
  S.title = () => {
    if (S.drone) return (S.drone.product || S.drone.name || 'Drone') + ' flight ' + U.fmtDate(S.drone.data.t[0], S.tzOffset());
    if (S.djiSummary) return S.djiSummary.product + ' flight ' + U.fmtDate(S.djiSummary.start, S.tzOffset());
    if (S.radar) return S.radar.name + ' ' + U.fmtDate(S.radar.t0, S.tzOffset());
    return 'Orbit';
  };
  S.offsetText = (k) => (S.offsets[k] ? (S.offsets[k] > 0 ? '+' : '') + (S.offsets[k] / 1000).toFixed(1) + ' s' : 'none');
  S.anySimulated = () => !!((S.drone && S.drone.simulated) || (S.radar && S.radar.simulated) || (S.weather && S.weather.simulated));

  let mv = null, tl = null;
  let isMac = false; // the Mac app: its toolbar doubles as the window's title bar

  // ================================================================== small UI helpers
  const ICON = (id) => '<svg><use href="#i-' + id + '"/></svg>';
  // "12.4 m/s" -> "12.4 <i>m/s</i>": units are set lighter than the numbers
  const uh = (s) => String(s).replace(/(\d)\s(km\/h|m\/s|km|kn|min|dBsm|hPa|mm\/h|°C|m|s|h|V|%)(?![\w/])/g, '$1 <i>$2</i>');
  function toast(msg, kind, ms) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.innerHTML = ICON(kind === 'err' ? 'warn' : kind === 'ok' ? 'check' : 'info') + '<div>' + msg + '</div>';
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), ms || (kind === 'err' ? 9000 : 4500));
    return el;
  }
  // centred loading card over the map; busy(null) hides it
  function busy(msg, frac) {
    const el = $('#busy');
    if (!msg) { el.hidden = true; return; }
    el.querySelector('.msg').textContent = msg;
    const bar = el.querySelector('.progress');
    bar.style.visibility = U.isNum(frac) ? 'visible' : 'hidden';
    if (U.isNum(frac)) bar.querySelector('i').style.width = Math.round(U.clamp(frac, 0, 1) * 100) + '%';
    el.hidden = false;
  }
  function banner(html) {
    const b = $('#banner');
    if (!html) { b.hidden = true; b.innerHTML = ''; return; }
    b.innerHTML = ICON('warn') + html + '<button class="x" data-close title="Close">' + ICON('close') + '</button>';
    b.hidden = false;
    b.querySelector('[data-close]').onclick = () => banner(null);
  }
  const speedUnit = () => S.settings.speedUnit || 'ms';
  function fmtSpeed(v) {
    if (!U.isNum(v)) return '–';
    const u = speedUnit();
    return u === 'kmh' ? (v * 3.6).toFixed(1) + ' km/h' : u === 'kt' ? (v / 0.514444).toFixed(1) + ' kn' : v.toFixed(1) + ' m/s';
  }
  const basename = (p) => String(p).split(/[\\/]/).pop();

  // ================================================================== start-up
  async function init() {
    await SF.native.init();
    isMac = (SF.native.info && SF.native.info.helper === 'swift') || !!(SF.native.params && SF.native.params.get('shell') === 'mac');
    document.documentElement.classList.toggle('mac', isMac);
    $$('kbd.sc').forEach((k) => { if (isMac) k.textContent = k.dataset.mac; });
    $('#welcomeVer').textContent = SF.native.info ? 'Version ' + SF.native.info.version : '';
    S.settings = (await SF.native.loadSettings()) || {};
    applySettings();
    if (S.settings.inspector === false) showInspector(false);
    mv = new SF.MapView($('#map'), $('#overlay'), { basemap: S.settings.basemap || 'satellite', draw: drawOverlay, onReady: () => { if (hasData()) fitAll(false); } });
    // Follow and Fit sit under the zoom buttons, in the same style
    mv.map.addControl({ onAdd: () => { const el = $('#mapCtl'); el.hidden = false; return el; }, onRemove: () => {} }, 'top-right');
    if (wantIntro()) playIntro();
    else {
      $('#intro').hidden = true;
      document.documentElement.classList.remove('intro-run');
      mv.whenReady(() => { if (!hasData()) { const v = introTarget(); mv.map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom }); } });
    }
    $('#basemap').value = S.settings.basemap || 'satellite';
    tl = new SF.Timeline($('#timeline'), { onSeek: (t) => { S.liveFollow = false; setTime(t); } });
    bindUI();
    bindMapPointer();
    updateNet();
    if (SF.native.ok) {
      setInterval(ping, 5000);
      ping();
    } else {
      window.addEventListener('online', updateNet);
      window.addEventListener('offline', updateNet);
    }
    requestAnimationFrame(loop);
    await ensureLicense();
    if (SF.native.ok) {
      setInterval(pollOpenQueue, 1500);
      pollOpenQueue();
    }
    refreshAll();
    if (SF.native.recovered) toast('The Orbit window had a problem and was restarted. Please open your files again.', 'err', 12000);
    const goTo = SF.native.params && Date.parse(SF.native.params.get('t') || '');
    if (goTo) {
      const wait = setInterval(() => {
        if (!S.drone || !S.radar) return;
        clearInterval(wait);
        setTime(goTo);
        if (SF.native.params.get('view') === '3d') set3D(true);
        if (SF.native.params.get('tab')) switchTab(SF.native.params.get('tab'));
        if (SF.native.params.get('pdf')) setTimeout(() => exportPdf(SF.native.params.get('pdf')), 1500);
      }, 250);
    }
  }
  // ---------------------------------------------------------------- 3D intro
  function wantIntro() {
    const q = SF.native.params;
    if (S.settings.intro === false || SF.native.recovered) return false;
    if (q && (q.get('intro') === '0' || q.get('t') || q.get('pdf'))) return false; // test runs
    return !(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  // where the intro flies to: the data, else the last test site, else OuluZone
  function introTarget() {
    const b = hasData() ? dataBounds() : null;
    if (b) return { lon: (b[0] + b[2]) / 2, lat: (b[1] + b[3]) / 2, zoom: 13.5 };
    const v = S.settings.lastView;
    if (v && U.isNum(v.lon) && U.isNum(v.lat)) return { lon: v.lon, lat: v.lat, zoom: 13 };
    return { lon: 26.0439, lat: 65.1766, zoom: 12.5 };
  }
  function playIntro() {
    if (!mv || SF.intro.running) return;
    closeMenus();
    document.documentElement.classList.add('intro-run');
    SF.intro.play({ mv, target: introTarget(), hasData, onEnd: () => document.documentElement.classList.remove('intro-run') });
  }
  function applySettings() {
    const s = S.settings;
    S.meta = Object.assign({ site: s.site || '', org: s.org || '', author: s.author || '' }, S.meta || {});
    S.aopts = Object.assign(A.defaults(), s.aopts || {});
    S.cwaCfg = Object.assign(SF.cwa.defaults(), s.cwa || {});
    if (s.layers) Object.assign(S.layers, s.layers, { terrain: false });
  }
  async function ping() {
    try {
      const r = await SF.native.post('/api/ping', {});
      SF.native.online = r.online !== false;
    } catch (e) { SF.native.online = false; }
    updateNet();
  }
  function updateNet() {
    const b = $('#netBadge');
    const on = SF.native.ok ? SF.native.online : navigator.onLine;
    b.className = 'sbnet ' + (on ? 'online' : 'offline');
    b.textContent = on ? 'Online' : 'Offline, saved maps';
    b.title = !SF.native.ok ? 'Running without the Orbit helper: maps are not saved for offline use.' : on ? 'Map tiles come from the internet and are kept for offline use.' : 'Map tiles come from the maps saved on this computer.';
  }
  async function pollOpenQueue() {
    try {
      const r = await SF.native.get('/api/open-queue');
      for (const p of r.paths || []) await importPath(p);
    } catch (e) { /* helper busy */ }
  }

  // ================================================================== import
  const hasData = () => !!(S.drone || S.radar || S.weather || S.djiSummary);
  async function importFiles(list, hint) {
    for (const f of list) {
      const bytes = new Uint8Array(await f.arrayBuffer());
      await importBytes(f.name, bytes, hint);
    }
  }
  async function importPath(path, hint) {
    try {
      busy('Opening ' + basename(path));
      const bytes = await SF.native.readFile(path);
      const before = { d: S.drone || S.djiSummary, r: S.radar, w: S.weather };
      await importBytes(basename(path), bytes, hint, path);
      const kind = (S.drone || S.djiSummary) !== before.d ? 'drone' : S.radar !== before.r ? 'radar' : S.weather !== before.w ? 'weather' : null;
      if (kind) addRecent(path, kind);
    } catch (e) { toast(esc(e.message), 'err'); } finally { busy(null); }
  }
  function addRecent(path, kind) {
    const list = (S.settings.recent || []).filter((r) => r.path !== path);
    list.unshift({ path, kind, name: basename(path), t: Date.now() });
    S.settings.recent = list.slice(0, 12);
    SF.native.saveSettings({ recent: S.settings.recent });
  }
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  // when a recent file was opened, in this computer's time: "14:05", "Yesterday", "29 Oct"
  function whenText(t) {
    const d = new Date(t), now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const ago = Math.round((day(now) - day(d)) / 86400000);
    if (ago <= 0) return U.pad(d.getHours()) + ':' + U.pad(d.getMinutes());
    if (ago === 1) return 'Yesterday';
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : '');
  }
  const KIND_ICON = { drone: 'drone', radar: 'radar', weather: 'wind' };
  function renderRecent() {
    const el = $('#recent'), list = (S.settings.recent || []).slice(0, 8);
    if (!SF.native.ok) { el.innerHTML = ''; return; }
    if (!list.length) { el.innerHTML = '<h4><span>Recent</span></h4><p class="none">No recent files</p>'; return; }
    const last = {};
    for (const r of S.settings.recent || []) if (!last[r.kind]) last[r.kind] = r;
    const set = Object.values(last);
    const folder = (p) => String(p).split(/[\\/]/).slice(-2, -1)[0] || '';
    el.innerHTML = '<h4><span>Recent</span>' + (set.length > 1 ? '<button class="link" id="btnOpenLast">Open Last ' + set.length + ' Together</button>' : '') + '</h4>' +
      list.map((r, k) => '<button class="rf" data-recent="' + k + '" title="' + esc(r.path) + '">' + ICON(KIND_ICON[r.kind] || 'file') + '<span class="n">' + esc(r.name) + '</span><span class="d">' + esc(whenText(r.t)) + '</span><span class="p">' + esc(folder(r.path)) + '</span></button>').join('');
    el.querySelectorAll('[data-recent]').forEach((b) => (b.onclick = () => { const r = list[+b.dataset.recent]; importPath(r.path, r.kind); }));
    const ol = $('#btnOpenLast');
    if (ol) ol.onclick = async () => { for (const k of ['drone', 'radar', 'weather']) if (last[k]) await importPath(last[k].path, k); };
  }
  function showHelp() {
    $('#helpVersion').textContent = 'Orbit ' + ((SF.native.info && SF.native.info.version) || '') + (SF.native.info ? ' · Data folder: ' + SF.native.info.dataDir : '');
    $('#dlgHelp').showModal();
  }
  async function importBytes(name, bytes, hint, path) {
    try {
      busy('Reading ' + name);
      await U.tick();
      if (/\.orbitlicense$/i.test(name) || (bytes.length < 4096 && /ORBIT1-/.test(U.decodeText(bytes)))) return applyLicense(U.decodeText(bytes));
      if (!S.license) { openLicense('Activate Orbit with your license first.'); return; }
      if (/\.(orbit|skyfusion)\.json$/i.test(name)) return loadSession(JSON.parse(U.decodeText(bytes)), name);
      if (hint !== 'radar' && hint !== 'weather' && SF.ardupilot.isBin(bytes)) return setDrone(SF.ardupilot.binToDrone(bytes, name), name);
      if (hint !== 'radar' && hint !== 'weather' && SF.ardupilot.isTlog(bytes)) return setDrones(SF.ardupilot.tlogToDrones(bytes, name), name);
      if (hint !== 'radar' && hint !== 'weather' && SF.dji.looksLike(bytes, name)) return await loadDJI(name, bytes);
      if (SF.zip.isZip(bytes)) {
        if (/\.kmz$/i.test(name)) {
          const ents = SF.zip.entries(bytes);
          const k = ents.find((e) => /\.kml$/i.test(e.name));
          if (!k) throw new Error('The KMZ has no KML inside.');
          return setDrone(SF.tracks.fromKML(await SF.zip.readText(bytes, k), { name }), name);
        }
        return await loadRows(name, await SF.xlsx(bytes), hint);
      }
      if (U.textShare(bytes) < 0.75) throw new Error('This is not a text file Orbit can read' + (/\.txt$/i.test(name) ? ' (and not a DJI flight record).' : '.'));
      const text = U.decodeText(bytes);
      const head = text.slice(0, 6000).replace(/^﻿/, '').trimStart();
      if (head.startsWith('<')) {
        if (R.isRobin(head)) return await loadRobin(name, text);
        if (/<gpx[\s>]/i.test(head)) return setDrone(SF.tracks.fromGPX(text, { name }), name);
        if (/<kml[\s>]/i.test(head)) return setDrone(SF.tracks.fromKML(text, { name }), name);
        return await loadGenericXML(name, text);
      }
      if (head.startsWith('{') && /"app"\s*:\s*"(Orbit|SkyFusion)"/.test(head)) return loadSession(JSON.parse(text), name);
      if (SF.weather.isWXT(text)) return setWeather(SF.weather.fromWXT(text, { name }), name);
      return await loadRows(name, U.parseCSV(text), hint);
    } catch (e) {
      console.error(e);
      toast('<b>' + esc(name) + '</b>: ' + esc(e.message), 'err');
    } finally { busy(null); }
  }
  async function loadRows(name, rows, hint) {
    const head = rows.slice(0, 60).map((r) => r.join(' ')).join(' ').toLowerCase();
    // a header row with latitude and longitude columns (not "Latitude:" metadata lines)
    const cell = (c) => String(c == null ? '' : c).trim();
    const hasLL = rows.slice(0, 60).some((r) => r.some((c) => /^(osd\.)?lat(itude)?\b/i.test(cell(c)) && !/:$/.test(cell(c))) && r.some((c) => /^(osd\.)?(lon|lng|long(itude)?)\b/i.test(cell(c)) && !/:$/.test(cell(c))));
    let kind = hint;
    if (!kind) {
      if (!hasLL) kind = 'weather';
      else if (/height_above_takeoff|osd\.|battery|flycstate|satellites|flytime|litchi|altitude\(m\)/.test(head)) kind = 'drone';
      else if (/track.?id|\btrack\b|classification|\bclass\b|target|rcs/.test(head)) kind = 'radar';
      else kind = await choose(name);
    }
    if (!kind) return;
    if (kind === 'weather') return setWeather(SF.weather.fromRows(rows, { name }), name);
    if (kind === 'drone') return setDrone(SF.tracks.fromRows(rows, { name }), name);
    const cand = R.analyzeCSV(rows);
    if (!cand) throw new Error('No position columns (lat/lon, range/azimuth or x/y) were found for radar tracks.');
    const res = await mappingDialog(name, [cand]);
    if (!res) return;
    const store = await R.extractCSV(res.cand, res.mapping, res.opts, name);
    return setRadar(store, name);
  }
  function choose(name) {
    const d = $('#dlgChoose');
    $('#chooseName').textContent = name;
    return new Promise((res) => {
      d.onclose = () => res(d.returnValue && d.returnValue !== 'cancel' ? d.returnValue : null);
      d.returnValue = '';
      d.showModal();
    });
  }

  // ---------------------------------------------------------------- DJI
  async function loadDJI(name, bytes) {
    const log = SF.dji.open(bytes);
    S.djiLog = log;
    S.files.drone = name;
    if (!log.encrypted) {
      busy('Decoding ' + name);
      return setDrone(await SF.dji.decode(log), name);
    }
    let kc = null;
    if (SF.native.ok) {
      const c = await SF.native.get('/api/dji/keychains?id=' + encodeURIComponent(log.keyId));
      if (c.ok) kc = c.data;
    }
    if (!kc && S.settings.djiApiKey && SF.native.ok) kc = await fetchKeys(log, false);
    if (kc && (await decodeWithKeys(log, kc, name))) return;
    S.drone = null;
    S.djiSummary = SF.dji.summary(log);
    onDataChanged({ fit: true, toStart: true });
    showLockedBanner();
  }
  async function fetchKeys(log, loud) {
    if (!SF.native.ok) { toast('Unlocking DJI logs needs the Orbit app (the helper), not a plain browser.', 'err'); return null; }
    busy('Asking DJI for the keys of this log…');
    try {
      const r = await SF.native.post('/api/dji/keychains', { request: SF.dji.keychainsRequest(log), id: log.keyId });
      if (r.ok) return r.data;
      toast('Could not unlock the DJI log: ' + esc(r.error || 'unknown error') + (r.detail ? '<br><small>' + esc(r.detail) + '</small>' : ''), 'err', 12000);
      return null;
    } catch (e) {
      if (loud) toast('Could not reach the helper: ' + esc(e.message), 'err');
      return null;
    } finally { busy(null); }
  }
  async function decodeWithKeys(log, kc, name) {
    try {
      busy('Decrypting ' + name);
      const drone = await SF.dji.decode(log, kc);
      if (drone.stats && drone.stats.decrypted === 0) throw new Error('none of the records could be decrypted with these keys');
      S.djiSummary = null;
      setDrone(drone, name);
      banner(null);
      toast('DJI log unlocked: ' + drone.data.t.length + ' positions.', 'ok');
      return true;
    } catch (e) {
      toast('The DJI keys did not work for ' + esc(name) + ': ' + esc(e.message), 'err', 12000);
      return false;
    } finally { busy(null); }
  }
  function showLockedBanner() {
    const s = S.djiSummary;
    if (!s) return;
    const key = !!S.settings.djiApiKey;
    banner('<div><b>Encrypted DJI log</b> (format ' + s.version + '). Only the flight summary is shown. ' +
      (key ? 'Unlocking fetches its keys from DJI and needs internet once.' : 'Add your DJI API key in Settings to see the GPS track.') + '</div>' +
      '<button class="primary small" id="btnUnlock">' + (key ? 'Unlock' : 'Add API Key…') + '</button>');
    $('#btnUnlock').onclick = () => (key ? unlockNow() : openSettings('dji'));
  }
  async function unlockNow() {
    if (!S.djiLog) return;
    const kc = await fetchKeys(S.djiLog, true);
    if (kc) await decodeWithKeys(S.djiLog, kc, S.files.drone);
  }

  // ---------------------------------------------------------------- radar
  async function loadRobin(name, text) {
    const store = await R.parseRobin(text, name.replace(/\.[^.]+$/, ''), (f) => busy('Reading radar messages', f));
    if (!store.tracks.length) throw new Error('No radar tracks were found in the file.');
    S.radarRaw = text;
    setRadar(store, name);
  }
  async function loadGenericXML(name, text) {
    busy('Looking for track records in ' + name);
    const root = SF.xml.parse(text);
    const cands = R.analyzeXML(root);
    if (!cands.length) throw new Error('No repeated records with positions (lat/lon, range/azimuth or x/y) were found in this XML.');
    const res = await mappingDialog(name, cands);
    if (!res) return;
    const store = await R.extractXML(res.cand, res.mapping, res.opts, name.replace(/\.[^.]+$/, ''));
    if (!store.tracks.length) throw new Error('No track updates could be read with those fields.');
    S.radarRaw = null;
    setRadar(store, name);
  }
  function mappingDialog(name, cands) {
    const d = $('#dlgMapping');
    const tagSel = $('#mapTag');
    tagSel.innerHTML = cands.map((c, i) => '<option value="' + i + '">' + esc(c.tag) + ' (' + c.count + ')</option>').join('');
    $('#mapTagWrap').hidden = cands.length < 2 && cands[0].tag === 'CSV rows';
    $('#mapIntro').textContent = 'Orbit found track records in ' + name + '. Check the fields and change any that are wrong.';
    const labels = { time: 'Time', id: 'Track ID', lat: 'Latitude', lon: 'Longitude', alt: 'Altitude / height (m)', cls: 'Classification', spd: 'Speed (m/s)', hdg: 'Heading (°)', range: 'Range (m)', az: 'Azimuth (°)', el: 'Elevation (°)', x: 'East x (m)', y: 'North y (m)', rcs: 'RCS (dBsm)', score: 'Score' };
    const render = () => {
      const c = cands[+tagSel.value];
      const opts = ['<option value="">—</option>'].concat(c.keys.map((k) => '<option value="' + esc(k) + '">' + esc(k) + '</option>')).join('');
      $('#mapFields').innerHTML = R.FIELDS.map((f) => '<label class="field">' + labels[f] + '<select data-f="' + f + '">' + opts + '</select></label>').join('');
      for (const f of R.FIELDS) $('#mapFields select[data-f="' + f + '"]').value = c.mapping[f] || '';
      const sample = c.keys.slice(0, 40).map((k) => k + ' = ' + String(c.sample[k]).slice(0, 60)).join('\n');
      $('#mapPreview').textContent = 'First record:\n' + sample;
      const needOrigin = !(c.mapping.lat && c.mapping.lon);
      $('#mapOrigin').querySelectorAll('input').forEach((i) => (i.placeholder = needOrigin ? 'needed for range/azimuth' : 'optional'));
    };
    tagSel.onchange = render;
    render();
    return new Promise((res) => {
      d.onclose = () => {
        if (d.returnValue !== 'ok') return res(null);
        const c = cands[+tagSel.value];
        const mapping = Object.assign({}, c.mapping);
        for (const f of R.FIELDS) { const v = $('#mapFields select[data-f="' + f + '"]').value; if (v) mapping[f] = v; else delete mapping[f]; }
        mapping.pos = mapping.lat && mapping.lon ? 'latlon' : mapping.range && mapping.az ? 'polar' : mapping.x && mapping.y ? 'xy' : 'latlon';
        const origin = { lat: U.num($('#mapOLat').value), lon: U.num($('#mapOLon').value), alt: U.num($('#mapOAlt').value) };
        const tzv = $('#mapTz').value;
        res({ cand: c, mapping, opts: { origin, tz: tzv === 'local' ? -new Date().getTimezoneOffset() : 0 } });
      };
      d.returnValue = '';
      d.showModal();
    });
  }

  // ---------------------------------------------------------------- datasets
  // force: take every drone's current times as raw (after loading a session); otherwise only new drones
  function keepRaw(kind, force) {
    if (kind === 'drone') {
      for (const d of S.fleet.length ? S.fleet : S.drone ? [S.drone] : []) {
        if (d.data.traw && !force) continue;
        d.data.traw = d.data.t.slice();
        for (const e of d.events) e.traw = e.t;
        for (const p of d.pilot || []) p.traw = p.t;
      }
    } else if (kind === 'radar' && S.radar) {
      for (const tr of S.radar.tracks) { tr.traw = tr.t.slice(); tr.firstRaw = tr.first; tr.lastRaw = tr.last; }
      S.radar.plots.traw = S.radar.plots.t.slice();
      S.radar.snapRaw = (S.radar.snapTimes || []).slice();
      for (const e of S.radar.events) e.traw = e.t;
      S.radar.t0raw = S.radar.t0; S.radar.t1raw = S.radar.t1;
    } else if (kind === 'weather' && S.weather) S.weather.data.traw = S.weather.data.t.slice();
  }
  function applyOffset(kind, ms) {
    S.offsets[kind] = ms;
    if (kind === 'drone') {
      for (const d of S.fleet.length ? S.fleet : S.drone ? [S.drone] : []) {
        for (let i = 0; i < d.data.t.length; i++) d.data.t[i] = d.data.traw[i] + ms;
        for (const e of d.events) e.t = e.traw + ms;
        for (const p of d.pilot || []) p.t = p.traw + ms;
        d.t0 = d.data.t[0]; d.t1 = d.data.t[d.data.t.length - 1];
      }
    } else if (kind === 'radar' && S.radar && !S.live) {
      const r = S.radar;
      for (const tr of r.tracks) { for (let i = 0; i < tr.t.length; i++) tr.t[i] = tr.traw[i] + ms; tr.first = tr.firstRaw + ms; tr.last = tr.lastRaw + ms; }
      for (let i = 0; i < r.plots.t.length; i++) r.plots.t[i] = r.plots.traw[i] + ms;
      if (r.snapRaw) r.snapTimes = r.snapRaw.map((x) => x + ms);
      for (const e of r.events) e.t = e.traw + ms;
      r.t0 = r.t0raw + ms; r.t1 = r.t1raw + ms;
    } else if (kind === 'weather' && S.weather) {
      const w = S.weather.data;
      for (let i = 0; i < w.t.length; i++) w.t[i] = w.traw[i] + ms;
      S.weather.t0 = w.t[0]; S.weather.t1 = w.t[w.t.length - 1];
    }
  }
  // ---------------------------------------------------------------- the drones of one scene
  const FLEET_COLORS = ['#ff7a1a', '#ffd23f', '#9be564', '#c792ea', '#f6a6b2', '#7fdbca', '#e8edf2', '#ffb86b'];
  function labelFleet() {
    const seen = {};
    S.fleet.forEach((d, k) => {
      d.color = FLEET_COLORS[k % FLEET_COLORS.length];
      let s = d.label || (S.fleet.length > 1 ? 'Drone ' + (k + 1) : 'Drone');
      if (seen[s]) s += ' (' + (k + 1) + ')';
      seen[s] = true;
      d.short = s;
    });
  }
  // A drone whose flight overlaps the drones already loaded joins their scene; a separate flight
  // replaces them. Loading the same file again replaces that drone.
  function joinFleet(drone, name) {
    drone.file = name;
    const same = S.fleet.findIndex((d) => d.file === name && (d.label || '') === (drone.label || ''));
    if (same >= 0) S.fleet[same] = drone;
    else if (S.fleet.some((d) => drone.t0 < d.t1 && drone.t1 > d.t0)) S.fleet.push(drone);
    else S.fleet = [drone];
    labelFleet();
  }
  function setDrone(drone, name) { setDrones([drone], name); }
  function setDrones(list, name) {
    for (const d of list) joinFleet(d, name);
    const drone = list[0];
    S.drone = drone;
    S.files.drone = name;
    if (drone.source !== 'dji') { S.djiLog = null; S.djiSummary = null; banner(null); }
    keepRaw('drone');
    if (S.offsets.drone) applyOffset('drone', S.offsets.drone);
    S.droneIdx = null;
    onDataChanged({ fit: true, toStart: true });
    const n = drone.data.t.length;
    toast((list.length > 1 ? list.length + ' drones from ' + esc(name) + ': <b>' + list.map((d) => esc(d.short)).join(', ') + '</b>' : 'Drone: <b>' + esc(drone.product || drone.name) + '</b>' + (drone.label ? ' (' + esc(drone.label) + ')' : '') + ', ' + n + ' points, ' + U.fmtDur((drone.t1 - drone.t0) / 1000)) +
      (S.fleet.length > 1 ? '<br><small>' + S.fleet.length + ' drones in this scene: the radar is compared with all of them.</small>' : '') + (drone.note ? '<br><small>' + esc(drone.note) + '</small>' : ''), 'ok');
  }
  function selectDrone(k) {
    const d = S.fleet[k];
    if (!d || d === S.drone) return;
    S.drone = d; S.files.drone = d.file; S.droneIdx = null;
    onDataChanged({});
  }
  function dropDrone(k) {
    const d = S.fleet[k];
    if (!d) return;
    if (S.fleet.length === 1) return removeData('drone');
    S.fleet.splice(k, 1);
    labelFleet();
    if (S.drone === d) { S.drone = S.fleet[0]; S.files.drone = S.drone.file; S.droneIdx = null; }
    onDataChanged({});
  }
  function setRadar(store, name) {
    if (S.live) stopLive(true);
    S.radar = store;
    S.files.radar = name;
    keepRaw('radar');
    if (S.offsets.radar) applyOffset('radar', S.offsets.radar);
    onDataChanged({ fit: !S.drone, toStart: !S.drone });
    const sensor = R.sensor(store);
    toast('Radar: <b>' + esc(store.name) + '</b>, ' + store.tracks.length + ' tracks, ' + store.updates + ' updates' + (sensor ? '' : '<br><small>No radar position in the file. Set it in the Files tab for range rings and ranges.</small>'), 'ok');
  }
  function setWeather(wx, name) {
    S.weather = wx;
    S.files.weather = name;
    keepRaw('weather');
    if (S.offsets.weather) applyOffset('weather', S.offsets.weather);
    onDataChanged({});
    const cols = wx.columns.map((c) => (SF.weather.KINDS[c.kind] || {}).label || c.kind).join(', ');
    toast('Weather: <b>' + esc(wx.name) + '</b>, ' + wx.data.t.length + ' rows (' + esc(cols) + ')' + (wx.notes.length ? '<br><small>' + esc(wx.notes.join(' ')) + '</small>' : ''), 'ok');
    const r = timeRange();
    if (hasFlight() && (wx.t1 < r.t0 - 3600e3 || wx.t0 > r.t1 + 3600e3)) toast('The weather file does not cover the flight time (' + esc(U.fmtDate(wx.t0) + ' ' + U.fmtClock(wx.t0) + ' – ' + U.fmtClock(wx.t1)) + ' UTC). Check its time zone in the Files tab.', 'err', 12000);
  }
  function removeData(kind) {
    if (kind === 'drone') { S.drone = null; S.fleet = []; S.djiLog = null; S.djiSummary = null; S.files.drone = ''; banner(null); }
    if (kind === 'radar') { if (S.live) stopLive(true); S.radar = null; S.radarRaw = null; S.files.radar = ''; }
    if (kind === 'weather') { S.weather = null; S.files.weather = ''; }
    S.offsets[kind] = 0;
    onDataChanged({});
  }
  function loadSession(obj, name) {
    const s = SF.exporters.loadSession(obj);
    S.fleet = s.fleet && s.fleet.length ? s.fleet : s.drone ? [s.drone] : [];
    labelFleet();
    S.drone = S.fleet[s.sel || 0] || null; S.radar = s.radar; S.weather = s.weather; S.djiLog = null; S.djiSummary = null; S.radarRaw = null;
    S.files = s.files || S.files; S.offsets = { drone: 0, radar: 0, weather: 0 };
    if (s.aopts) S.aopts = Object.assign(A.defaults(), s.aopts);
    if (s.meta) S.meta = Object.assign({}, S.meta, s.meta);
    if (s.cwa) S.cwaCfg = Object.assign(SF.cwa.defaults(), s.cwa); // the scoring the session was made with
    for (const k of ['drone', 'radar', 'weather']) keepRaw(k, true);
    S.droneIdx = null;
    onDataChanged({ fit: true, toStart: true });
    toast('Session opened: ' + esc(name), 'ok');
  }
  function loadDemo() {
    const d = SF.demo();
    S.radarRaw = null; S.djiLog = null; S.djiSummary = null;
    S.drone = d.drone; S.radar = d.radar; S.weather = d.weather;
    S.fleet = [d.drone]; labelFleet();
    S.files = { drone: 'Simulated flight (demo)', radar: 'Simulated radar (demo)', weather: 'Simulated weather (demo)' };
    S.offsets = { drone: 0, radar: 0, weather: 0 };
    for (const k of ['drone', 'radar', 'weather']) keepRaw(k, true);
    S.droneIdx = null;
    onDataChanged({ fit: true, toStart: true });
    banner('<div><b>Simulated demo data.</b> Made-up flight, radar and weather to show how Orbit works.</div>');
  }

  // ================================================================== recompute after changes
  const hasFlight = () => !!(S.drone || S.djiSummary);
  function timeRange() {
    let t0 = Infinity, t1 = -Infinity;
    const add = (a, b) => { if (U.isNum(a) && U.isNum(b)) { t0 = Math.min(t0, a); t1 = Math.max(t1, b); } };
    if (S.drone) for (const d of S.fleet.length ? S.fleet : [S.drone]) add(d.data.t[0], d.data.t[d.data.t.length - 1]);
    else if (S.djiSummary) add(S.djiSummary.start, S.djiSummary.end);
    if (S.radar && isFinite(S.radar.t0)) {
      if (hasFlight()) add(Math.max(S.radar.t0, t0 - 15 * 60e3), Math.min(S.radar.t1, t1 + 15 * 60e3));
      else add(S.radar.t0, S.radar.t1);
    }
    if (!isFinite(t0) && S.weather) add(S.weather.t0, S.weather.t1);
    return { t0, t1 };
  }
  let analysisTimer = 0;
  let syncTimer = 0;
  function runAnalysis() {
    // with several drones in the scene a radar update belongs only to the nearest drone
    const fleet = S.drone && S.fleet.length > 1 ? S.fleet : null;
    const runFor = (d) => A.run(d, S.radar, S.weather, fleet ? Object.assign({}, S.aopts, { others: fleet.filter((o) => o !== d) }) : S.aopts);
    S.analysis = S.drone || S.radar ? runFor(S.drone) : null;
    S.fleetAn = fleet && S.radar ? fleet.map((d) => (d === S.drone ? S.analysis : runFor(d))) : null;
    if (S.fleetAn) {
      // a drone-labelled track that follows another drone of the scene is not a false drone track
      const onSome = new Set(S.fleetAn.flatMap((a) => a.matchedIds || []));
      const recH = (S.radar.t1 - S.radar.t0) / 3600e3;
      for (const a of S.fleetAn) if (a.falseTracks) { a.falseTracks = a.falseTracks.filter((x) => !onSome.has(x.id)); a.falseRate = recH > 0 ? a.falseTracks.length / recH : NaN; }
    }
    // CWA 18150 metrics walk every track each second, so they wait until a live feed stops
    S.cwa = S.analysis && S.analysis.coverage && !S.live ? SF.cwa.run(S.fleetAn ? fleet : S.drone, S.radar, S.fleetAn || S.analysis, S.aopts, S.cwaCfg) : null;
    S.hits = (S.analysis && S.analysis.hits) || [];
    S.hitT = S.hits.map((h) => h.t);
    S.matched = new Set((S.analysis && S.analysis.matchedIds) || []);
    S.sync = null;
    clearTimeout(syncTimer);
    if (S.drone && S.radar && !S.live) syncTimer = setTimeout(() => { S.sync = A.estimateOffset(S.drone, S.radar, { span: 30 }); renderAnalysis(); }, 400);
  }
  function onDataChanged(o) {
    o = o || {};
    if (SF.intro.running) SF.intro.skip(); // data first
    const r = timeRange();
    S.range = r;
    runAnalysis();
    $('#empty').hidden = hasData();
    if (isFinite(r.t0)) {
      if (!U.isNum(S.t) || o.toStart || S.t < r.t0 || S.t > r.t1) {
        const w = S.analysis && S.analysis.window;
        S.t = w ? Math.max(r.t0, w.t0 - 5000) : S.djiSummary ? Math.max(r.t0, S.djiSummary.start - 5000) : r.t0;
      }
      tl.setRange(r.t0, r.t1, !o.toStart && !o.fit);
    } else tl.setRange(NaN, NaN);
    tl.tz = S.tzOffset();
    buildLanes();
    if (o.fit && mv && mv.ready) fitAll(true);
    refreshAll();
  }
  // ---------------------------------------------------------------- map legend
  function renderLegend() {
    const el = $('#legend');
    if (!hasData()) { el.hidden = true; return; }
    const rows = [];
    const row = (sym, text) => rows.push('<div class="lg-row">' + sym + '<span>' + esc(text) + '</span></div>');
    const sec = (t) => rows.push('<div class="lg-sec">' + esc(t) + '</div>');
    if (S.drone) {
      const mode = pathMode();
      sec('Drone path');
      if (mode === 'status') {
        row('<i class="ln" style="background:' + STATUS_COLORS[2] + '"></i>', 'Classified as drone');
        row('<i class="ln" style="background:' + STATUS_COLORS[1] + '"></i>', 'Tracked');
        row('<i class="ln" style="background:' + STATUS_COLORS[0] + '"></i>', 'Not detected');
        row('<i class="ln" style="background:' + STATUS_COLORS[3] + '"></i>', 'Not airborne');
      } else if (mode === 'height') {
        const max = (S.analysis && S.analysis.flight && S.analysis.flight.maxH) || 100;
        rows.push('<div class="lg-row"><i class="ramp" style="background:linear-gradient(90deg,' + HEIGHT_RAMP.join(',') + ')"></i><span>0 – ' + Math.round(max) + ' m</span></div>');
      } else row('<i class="ln" style="background:' + COL.drone + '"></i>', 'Flown so far');
      if (S.fleet.length > 1) for (const d of S.fleet) row('<i class="dot" style="background:' + d.color + ';border-color:' + (d === S.drone ? '#fff' : '#0b0e12') + '"></i>', d.short + (d === S.drone ? ' (selected)' : ''));
      else row('<i class="dot" style="background:' + COL.drone + ';border-color:#fff"></i>', 'Drone position');
    }
    // radar classes and map symbols as compact chips
    const chips = [];
    const chip = (sym, text) => chips.push('<span class="lg-chip">' + sym + esc(text) + '</span>');
    if (S.radar) {
      sec('Radar tracks');
      const classes = [...S.radar.classes.entries()].sort((a, b) => (A.isDroneLabel(b[0], S.aopts.labels) - A.isDroneLabel(a[0], S.aopts.labels)) || b[1] - a[1]).slice(0, 8);
      for (const [c] of classes) chip(A.isDroneLabel(c, S.aopts.labels) ? '<i class="dia" style="background:' + U.classColor(c) + '"></i>' : '<i class="dot" style="background:' + U.classColor(c) + '"></i>', c.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (m) => m.toUpperCase()));
      if (S.drone && S.analysis && S.analysis.matchedIds && S.analysis.matchedIds.length) chip('<i class="ring"></i>', 'On the drone');
      rows.push('<div class="lg-chips">' + chips.splice(0).join('') + '</div>');
    }
    if (S.radar && R.sensor(S.radar)) chip('<i class="tri"></i>', 'Radar');
    if (S.drone || S.djiSummary) chip('<i class="home">H</i>', 'Home');
    if (S.weather && S.weather.station) chip('<i class="wxs"></i>', 'Weather station');
    if (chips.length) { sec('Symbols'); rows.push('<div class="lg-chips">' + chips.join('') + '</div>'); }
    const open = S.settings.legendOpen !== false;
    el.innerHTML = '<button class="lg-head" id="btnLegend" aria-expanded="' + open + '">Legend' + ICON('chev') + '</button>' + (open ? '<div class="lg-body">' + rows.join('') + '</div>' : '');
    el.hidden = false;
    $('#btnLegend').onclick = () => { S.settings.legendOpen = !open; SF.native.saveSettings({ legendOpen: !open }); renderLegend(); };
  }
  function refreshAll() {
    renderChrome();
    renderRecent();
    renderLegend();
    updateImportDots();
    renderFiles();
    renderAnalysis();
    updateNow(true);
    if (mv) mv.draw();
    tl.setTime(S.t);
  }
  // window title, the title in the Mac toolbar, and the status bar
  function renderChrome() {
    const title = hasData() ? (S.meta && S.meta.title) || S.title() : 'Orbit';
    document.title = hasData() ? 'Orbit – ' + title : 'Orbit';
    $('#docName').textContent = title;
    const tz = S.tzOffset(), w = S.analysis && S.analysis.window, sub = [];
    const a = w ? w.t0 : S.range.t0, b = w ? w.t1 : S.range.t1;
    if (hasData() && U.isNum(a) && U.isNum(b)) sub.push(U.fmtClock(a, tz).slice(0, 5) + '–' + U.fmtClock(b, tz).slice(0, 5) + ' ' + S.tzLabel());
    if (hasData() && S.meta && S.meta.site) sub.push(S.meta.site);
    $('#docSub').textContent = sub.join(' · ');
    const items = [];
    const item = (sw, text, file) => items.push('<span title="' + esc(file || text) + '"><i class="sw ' + sw + '"></i>' + esc(text) + '</span>');
    if (S.drone) item('drone', S.fleet.length > 1 ? S.fleet.length + ' drones' : S.drone.product || S.drone.name, S.files.drone);
    else if (S.djiSummary) item('drone', S.djiSummary.product + ' (encrypted log)', S.files.drone);
    const rs = S.radar && R.sensor(S.radar);
    if (S.radar) item('radar', ((rs && rs.name) || S.radar.name) + ' · ' + S.radar.tracks.length + ' tracks', S.files.radar);
    if (S.weather) item('wx', S.weather.name, S.files.weather);
    $('#sbData').innerHTML = items.length ? items.join('') : '<span class="empty">No data loaded</span>';
    $('#sbLicense').textContent = S.license ? 'Licensed to ' + SF.license.who(S.license) : '';
  }
  // the Import menu names the file loaded for each kind
  function updateImportDots() {
    $$('small[data-for]').forEach((d) => {
      const k = d.dataset.for;
      d.textContent = k === 'drone' ? (S.drone || S.djiSummary ? S.files.drone : '') : k === 'radar' ? (S.radar ? S.files.radar : '') : S.weather ? S.files.weather : '';
    });
  }
  function dataBounds() {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
    const add = (lat, lon) => { if (!U.isNum(lat) || !U.isNum(lon)) return; w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat); };
    if (S.drone) for (const dr of S.fleet.length ? S.fleet : [S.drone]) { const d = dr.data; for (let i = 0; i < d.t.length; i += 5) add(d.lat[i], d.lon[i]); }
    if (S.djiSummary && S.djiSummary.home) add(S.djiSummary.home.lat, S.djiSummary.home.lon);
    const sensor = S.radar && R.sensor(S.radar);
    if (sensor) add(sensor.lat, sensor.lon);
    if (S.radar && !S.drone) {
      const la = [], lo = [];
      for (const tr of S.radar.tracks) for (let i = 0; i < tr.t.length; i += 3) { la.push(tr.lat[i]); lo.push(tr.lon[i]); }
      la.sort((a, b) => a - b); lo.sort((a, b) => a - b);
      if (la.length) { add(U.quantile(la, 0.03), U.quantile(lo, 0.03)); add(U.quantile(la, 0.97), U.quantile(lo, 0.97)); }
    }
    if (S.drone && S.matched) for (const id of S.matched) { const tr = S.radar.trackMap.get(id); if (tr) for (let i = 0; i < tr.t.length; i += 4) add(tr.lat[i], tr.lon[i]); }
    if (!isFinite(w) && S.weather && S.weather.station) add(S.weather.station.lat, S.weather.station.lon);
    if (!isFinite(w)) return null;
    if (e - w < 0.002) { w -= 0.004; e += 0.004; }
    if (n - s < 0.001) { s -= 0.002; n += 0.002; }
    return [w, s, e, n];
  }
  function fitAll(animate) {
    const b = dataBounds();
    if (!b) return;
    mv.fit(b, animate);
    // remembered for the intro, which flies to the last test site
    const v = { lon: +((b[0] + b[2]) / 2).toFixed(5), lat: +((b[1] + b[3]) / 2).toFixed(5) }, o = S.settings.lastView;
    if (!o || o.lon !== v.lon || o.lat !== v.lat) { S.settings.lastView = v; SF.native.saveSettings({ lastView: v }); }
  }

  // ================================================================== time and playback
  function setTime(t, fromLoop) {
    if (!U.isNum(t)) return;
    S.t = U.clamp(t, S.range.t0, S.range.t1);
    if (mv) {
      if (S.follow && S.drone) { const p = A.droneAt(S.drone, S.t); if (p) mv.follow(p.lon, p.lat); }
      mv.draw();
    }
    tl.setTime(S.t);
    updateNow(!fromLoop);
  }
  function setPlaying(on) {
    if (on && !isFinite(S.range.t0)) return;
    if (on && S.t >= S.range.t1 - 50 && !S.live) S.t = S.range.t0;
    S.playing = on;
    $('#btnPlay').classList.toggle('on', on);
    $('#btnPlay').setAttribute('aria-label', on ? 'Pause' : 'Play');
  }
  let lastFrame = 0;
  function loop(ts) {
    if (S.live && S.liveFollow) {
      setTime(liveNow(), true);
    } else if (S.playing) {
      const dt = lastFrame ? Math.min(250, ts - lastFrame) : 0;
      let nt = S.t + dt * S.speed;
      if (nt >= S.range.t1) { nt = S.range.t1; if (!S.live) setPlaying(false); }
      setTime(nt, true);
    }
    lastFrame = S.playing ? ts : 0;
    requestAnimationFrame(loop);
  }

  // ================================================================== timeline lanes
  function buildLanes() {
    const lanes = [];
    const d = S.drone, an = S.analysis, sensor = an && an.sensor, radar = S.radar;
    const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : sensor && U.isNum(sensor.alt) ? sensor.alt : NaN;
    // height
    const hs = [];
    if (d) hs.push({ t: d.data.t, v: d.data.h, color: '#ff7a1a', width: 1.7 });
    if (radar && S.hits.length && U.isNum(ground)) {
      const tt = [], vv = [], cc = [];
      for (const h of S.hits) { const tr = radar.trackMap.get(h.id); tt.push(h.t); vv.push(tr.alt[h.i] - ground); cc.push(A.isDroneLabel(h.cls, S.aopts.labels) ? '#ff4fd8' : '#4cc9f0'); }
      hs.push({ t: tt, v: vv, kind: 'dots', colors: cc, color: '#4cc9f0', r: 1.5 });
    }
    if (hs.length) lanes.push({ id: 'height', label: 'Height', unit: 'm', series: hs, min: 0, minSpan: 20, weight: 1.25, readout: (t) => { const p = d && A.droneAt(d, t); return p ? 'height ' + p.h.toFixed(0) + ' m' : ''; } });
    // range from radar
    if (d && sensor) {
      const t = [], v = [];
      const dd = d.data, step = Math.max(1, Math.floor(dd.t.length / 3000));
      for (let i = 0; i < dd.t.length; i += step) { t.push(dd.t[i]); v.push(U.dist(sensor.lat, sensor.lon, dd.lat[i], dd.lon[i])); }
      lanes.push({ id: 'range', label: 'Range', unit: 'm', series: [{ t, v, color: '#ffb070', width: 1.5 }], min: 0, minSpan: 100, weight: 1, readout: (tt) => { const p = A.droneAt(d, tt); return p ? 'range ' + U.fmtDist(U.dist(sensor.lat, sensor.lon, p.lat, p.lon)) : ''; } });
    }
    // radar bands
    if (radar) {
      const rows = [];
      if (an && an.series) {
        const se = an.series, segs = [];
        let cur = null;
        for (let k = 0; k < se.t.length; k++) {
          const st = se.cls[k] ? 2 : se.det[k] ? 1 : 0;
          if (!cur || cur.st !== st) { if (cur) segs.push(cur); cur = { st, t0: se.t[k], t1: se.t[k] + 1000 }; } else cur.t1 = se.t[k] + 1000;
        }
        if (cur) segs.push(cur);
        rows.push({ label: 'drone', segs: segs.map((s) => ({ t0: s.t0, t1: s.t1, color: s.st === 2 ? '#ff4fd8' : s.st === 1 ? '#4cc9f0' : 'rgba(255,93,93,0.75)' })) });
      }
      // drone-labelled tracks (and the matched ones), class colour per update
      const list = radar.tracks.filter((tr) => S.matched.has(tr.id) || Object.keys(tr.classes).some((c) => A.isDroneLabel(c, S.aopts.labels)));
      const rowsTr = [];
      for (const tr of list.slice(0, 40)) {
        const segs = [];
        for (let i = 0; i < tr.t.length; i++) {
          const t0 = tr.t[i], t1 = i + 1 < tr.t.length && tr.t[i + 1] - t0 < 5000 ? tr.t[i + 1] : t0 + 1000;
          const col = S.matched.has(tr.id) ? (A.isDroneLabel(tr.cls[i], S.aopts.labels) ? '#ff4fd8' : '#4cc9f0') : 'rgba(255,79,216,0.45)';
          const last = segs[segs.length - 1];
          if (last && last.color === col && t0 - last.t1 < 5000) last.t1 = t1; else segs.push({ t0, t1, color: col });
        }
        // pack into rows without overlap
        let row = rowsTr.find((r) => r.end < tr.first - 2000);
        if (!row) { row = { segs: [], end: -Infinity }; rowsTr.push(row); }
        row.segs.push(...segs);
        row.end = tr.last;
      }
      rows.push(...rowsTr.slice(0, 5));
      if (rows.length) lanes.push({ id: 'radar', kind: 'bands', label: 'Radar', rows, weight: 0.9, readout: (t) => radarReadout(t) });
    }
    // radar activity: tracks alive per 5 s, all and drone-labelled
    if (radar && radar.tracks.length && isFinite(S.range.t0)) {
      const bin = 5000, n = Math.min(4000, Math.ceil((S.range.t1 - S.range.t0) / bin) + 1);
      const step = Math.max(bin, (S.range.t1 - S.range.t0) / Math.max(1, n - 1));
      const t = [], all = new Array(n).fill(0), dr = new Array(n).fill(0);
      for (let k = 0; k < n; k++) t.push(S.range.t0 + k * step);
      for (const tr of radar.tracks) {
        const a = Math.max(0, Math.floor((tr.first - S.range.t0) / step)), b = Math.min(n - 1, Math.floor((tr.last - S.range.t0) / step));
        const isD = Object.keys(tr.classes).some((c) => A.isDroneLabel(c, S.aopts.labels));
        for (let k = a; k <= b; k++) { all[k]++; if (isD) dr[k]++; }
      }
      lanes.push({ id: 'tracks', label: 'Tracks', series: [{ t, v: all, color: '#9aa4b2', width: 1.3 }, { t, v: dr, color: '#ff4fd8', width: 1.6 }], min: 0, minSpan: 4, weight: 0.7, readout: (tt) => { const k = Math.round((tt - S.range.t0) / step); return k >= 0 && k < n ? all[k] + ' tracks' : ''; } });
    }
    // position error
    if (S.hits.length) {
      lanes.push({ id: 'error', label: 'Error', unit: 'm', series: [{ t: S.hitT, v: S.hits.map((h) => h.eH), kind: 'dots', color: '#4cc9f0', r: 1.6, colors: S.hits.map((h) => (A.isDroneLabel(h.cls, S.aopts.labels) ? '#ff4fd8' : '#4cc9f0')) }], min: 0, minSpan: 10, max: Math.max(20, Math.min(S.aopts.gate * 1.1, U.quantile(S.hits.map((h) => h.eH).sort((a, b) => a - b), 0.99) * 1.2)), guides: [{ v: S.aopts.gate, color: 'rgba(255,93,93,0.5)' }], weight: 0.9, readout: (t) => { const h = nearestHit(t); return h ? 'error ' + h.eH.toFixed(0) + ' m' : ''; } });
    }
    // wind
    if (S.weather) {
      const w = S.weather.data;
      lanes.push({ id: 'wind', label: 'Wind', unit: 'm/s', series: [{ t: w.t, v: w.gust, color: 'rgba(122,167,255,0.45)', width: 1.2, gap: 3 * (S.weather.stepMs || 60000) }, { t: w.t, v: w.wind, color: '#7aa7ff', width: 1.7, gap: 3 * (S.weather.stepMs || 60000) }], min: 0, minSpan: 4, weight: 0.8, readout: (t) => { const v = SF.weather.at(S.weather, 'wind', t); return U.isNum(v) ? 'wind ' + v.toFixed(1) + ' m/s' : ''; } });
    }
    // events
    const ev = allEvents();
    if (ev.length) lanes.push({ id: 'events', kind: 'ticks', label: 'Events', items: ev.map((e) => ({ t: e.t, color: e.kind === 'warn' ? '#ffd60a' : e.kind === 'radar' ? '#ff4fd8' : e.kind === 'mode' ? '#ffb070' : '#8d99a6' })), weight: 0.45 });
    for (const l of lanes) l.hidden = S.lanes[l.id] === false || (S.lanes[l.id] === undefined && l.id === 'tracks' && !!S.drone);
    tl.setLanes(lanes);
    tl.shade = [];
    if (an && an.window) tl.shade.push({ t0: an.window.t0, t1: an.window.t1, color: 'rgba(255,122,26,0.06)' });
    else if (S.djiSummary) tl.shade.push({ t0: S.djiSummary.start, t1: S.djiSummary.end, color: 'rgba(255,122,26,0.08)' });
    const names = { height: 'Height', range: 'Range', radar: 'Radar', tracks: 'Tracks', error: 'Error', wind: 'Wind', events: 'Events' };
    $('#laneToggles').innerHTML = lanes.length ? lanes.map((l) => '<button data-lane="' + l.id + '" class="' + (l.hidden ? '' : 'on') + '">' + ICON('check') + '<span>' + names[l.id] + '</span></button>').join('') : '<button disabled><span class="muted">No data</span></button>';
  }
  function nearestHit(t) {
    if (!S.hits.length) return null;
    const k = U.idxNear(S.hitT, t);
    const h = S.hits[k];
    return h && Math.abs(h.t - t) <= S.aopts.tol * 1000 ? h : null;
  }
  function radarReadout(t) {
    const h = nearestHit(t);
    if (h) return 'track ' + h.id + ' ' + h.cls;
    const w = S.analysis && S.analysis.window;
    return w && t >= w.t0 && t <= w.t1 ? 'not tracked' : '';
  }
  function allEvents() {
    const ev = [];
    if (S.drone) for (const e of S.drone.events) ev.push(e);
    if (S.radar) for (const e of S.radar.events) ev.push(e);
    if (S.analysis && S.analysis.breaks) for (const b of S.analysis.breaks) ev.push({ t: b.t0, kind: 'radar', text: 'Radar lost the drone for ' + b.dur.toFixed(0) + ' s (' + b.reason + ')' });
    if (S.analysis && S.analysis.firstDetect) ev.push({ t: S.analysis.firstDetect.t, kind: 'radar', text: 'Radar first tracks the drone (track ' + S.analysis.firstDetect.id + ')' });
    if (S.analysis && S.analysis.firstClass) ev.push({ t: S.analysis.firstClass.t, kind: 'radar', text: 'Radar first labels the drone as a drone' });
    return ev.filter((e) => U.isNum(e.t)).sort((a, b) => a.t - b.t);
  }

  // ================================================================== "Now" panel
  let lastNow = 0;
  function updateNow(force) {
    const now = performance.now();
    if (!force && now - lastNow < 120) return;
    lastNow = now;
    const t = S.t, tz = S.tzOffset();
    $('#clock').textContent = U.isNum(t) ? U.fmtClock(t, tz, true) : '––:––:––.–';
    $('#clockTz').textContent = S.tzLabel() + (S.live ? (S.liveFollow ? ' · LIVE' : ' · PAUSED') : '');
    $('#clockTz').className = S.live ? 'tz live' : 'tz';
    $('#clockDate').textContent = U.isNum(t) ? U.fmtDate(t, tz) : '';
    const w = S.analysis && S.analysis.window;
    let tplus = '';
    if (w && U.isNum(t)) tplus = 'T' + (t >= w.t0 ? '+' : '−') + U.fmtDur(Math.abs(t - w.t0) / 1000, true);
    else if (S.djiSummary && U.isNum(t)) tplus = 'T' + (t >= S.djiSummary.start ? '+' : '−') + U.fmtDur(Math.abs(t - S.djiSummary.start) / 1000, true);
    $('#tplus').textContent = tplus;
    $('#tplus').title = tplus ? 'Time from takeoff' : '';
    renderDroneNow(t);
    renderRadarNow(t);
    renderWxNow(t);
    renderEvents(t);
  }
  // a typographic minus for negative numbers ("-8.9 m" -> "−8.9 m")
  const minus = (s) => String(s).replace(/(^|[\s(>])-(?=\d)/g, '$1−');
  function kvHtml(rows) { return rows.filter((r) => r && r[1] != null && r[1] !== '').map((r) => '<span>' + r[0] + '</span><b title="' + esc(minus(r[1]).replace(/<[^>]+>/g, '')) + '">' + minus(r[1]) + '</b>').join(''); }
  function renderDroneNow(t) {
    const el = $('#droneNow');
    $('#droneName').textContent = S.drone ? S.drone.product || S.drone.name : S.djiSummary ? S.djiSummary.product + ' (locked)' : '';
    if (S.drone) {
      const p = A.droneAt(S.drone, t);
      if (!p) { el.className = 'kv muted'; el.textContent = U.isNum(t) && t < S.drone.data.t[0] ? 'Before the log starts' : 'After the log ends'; return; }
      const d = S.drone.data, i = p.i, sensor = S.radar && R.sensor(S.radar);
      const mode = S.drone.source === 'dji' ? SF.dji.flightModeName(d.mode[i]) : S.drone.info && S.drone.info.modes && d.mode[i] >= 0 ? S.drone.info.modes[d.mode[i]] : '';
      el.className = 'kv';
      el.innerHTML = kvHtml([
        ['Height', p.h.toFixed(1) + ' <i>m</i>'], ['Speed', uh(fmtSpeed(p.hs))], ['Climb', uh(fmtSpeed(p.vs))], ['Heading', U.isNum(p.yaw) ? p.yaw.toFixed(0) + '°' : '–'],
        sensor ? ['From radar', uh(U.fmtDist(U.dist(sensor.lat, sensor.lon, p.lat, p.lon))) + ' <i>at</i> ' + U.bearing(sensor.lat, sensor.lon, p.lat, p.lon).toFixed(0) + '°'] : null,
        U.isNum(d.bat[i]) ? ['Battery', d.bat[i].toFixed(0) + ' <i>%</i>' + (U.isNum(d.volt && d.volt[i]) ? ' · ' + d.volt[i].toFixed(1) + ' <i>V</i>' : '')] : null,
        U.isNum(d.gps[i]) ? ['Satellites', d.gps[i]] : null, mode ? ['Flight mode', esc(mode)] : null,
        ['Position', p.lat.toFixed(6) + ', ' + p.lon.toFixed(6)],
      ]);
      return;
    }
    if (S.djiSummary) {
      const s = S.djiSummary;
      el.className = 'kv';
      el.innerHTML = kvHtml([['Track', '<span style="color:var(--warn)">Encrypted</span>'], ['Flight', U.fmtClock(s.start, S.tzOffset()) + '–' + U.fmtClock(s.end, S.tzOffset())], ['Duration', U.fmtDur(s.duration)], ['Distance', uh(U.fmtDist(s.distance))], ['Max height', uh(U.f(s.maxHeight, 0, 'm'))], ['Max speed', uh(fmtSpeed(s.maxHSpeed))]]) +
        '<span></span><b><button class="ghost small" id="btnUnlock2">' + (S.settings.djiApiKey ? 'Unlock GPS Track' : 'Add DJI API Key…') + '</button></b>';
      $('#btnUnlock2').onclick = () => (S.settings.djiApiKey ? unlockNow() : openSettings('dji'));
      return;
    }
    el.className = 'kv muted';
    el.textContent = 'No drone log';
  }
  function activeTracks(t, maxAge) {
    const out = [];
    if (!S.radar) return out;
    for (const tr of S.radar.tracks) {
      if (tr.first > t || tr.last < t - maxAge) continue;
      const i = U.idxLE(tr.t, t);
      if (i < 0 || t - tr.t[i] > maxAge) continue;
      out.push({ tr, i });
    }
    return out;
  }
  function renderRadarNow(t) {
    const st = $('#radarStatus'), el = $('#radarNow');
    const rs = S.radar && R.sensor(S.radar);
    $('#radarName').textContent = S.radar ? ((rs && rs.name) || S.radar.name) + (S.radar.simulated ? ' (simulated)' : '') : '';
    if (!S.radar) { st.innerHTML = ''; el.className = 'kv muted'; el.textContent = S.live ? 'Waiting for data…' : 'No radar data'; return; }
    const act = activeTracks(t, 4000);
    const lab = act.filter((a) => A.isDroneLabel(a.tr.cls[a.i], S.aopts.labels));
    const w = S.analysis && S.analysis.window;
    const h = nearestHit(t);
    let status = '';
    if (S.drone && w && t >= w.t0 && t <= w.t1) {
      if (h) status = A.isDroneLabel(h.cls, S.aopts.labels) ? '<div class="status cls">Classified as ' + esc(h.cls) + '</div>' : '<div class="status det">Tracked as ' + esc(h.cls) + '</div>';
      else {
        const k = U.idxLE(S.hitT, t);
        const since = k >= 0 ? (t - S.hitT[k]) / 1000 : NaN;
        status = '<div class="status miss">Not detected' + (U.isNum(since) && since < 3600 ? ', last update ' + since.toFixed(0) + ' s ago' : '') + '</div>';
      }
    } else if (S.drone) status = '<div class="status none">Drone not airborne</div>';
    else status = lab.length ? '<div class="status cls">' + lab.length + ' drone-classified track' + (lab.length > 1 ? 's' : '') + '</div>' : '<div class="status none">No drone-classified tracks</div>';
    st.innerHTML = status;
    const rows = [['Active tracks', act.length + (lab.length ? ' <i>· ' + lab.length + ' drone</i>' : '')]];
    const sel = S.selected ? act.find((a) => a.tr.id === S.selected) : null;
    const ref = h ? { tr: S.radar.trackMap.get(h.id), i: h.i, hit: h } : sel || (lab[0] ? lab[0] : null);
    if (ref) {
      const tr = ref.tr, i = ref.i, sensor = R.sensor(S.radar);
      const ground = sensor && U.isNum(sensor.ground) ? sensor.ground : NaN;
      rows.push(['Track', esc(tr.id)]);
      rows.push(['Class', '<span class="cls" style="--c:' + U.classColor(tr.cls[i]) + '">' + esc(tr.cls[i]) + '</span>']);
      if (U.isNum(tr.score[i])) rows.push(['Score', tr.score[i].toFixed(2)]);
      if (U.isNum(tr.rcs[i])) rows.push(['RCS', tr.rcs[i].toFixed(1) + ' <i>dBsm</i>']);
      if (U.isNum(tr.spd[i])) rows.push(['Speed', uh(fmtSpeed(tr.spd[i]))]);
      if (U.isNum(tr.alt[i]) && U.isNum(ground)) rows.push(['Height', (tr.alt[i] - ground).toFixed(0) + ' <i>m</i>']);
      if (sensor) rows.push(['Range', uh(U.fmtDist(U.dist(sensor.lat, sensor.lon, tr.lat[i], tr.lon[i])))]);
      if (ref.hit) { rows.push(['Horizontal error', ref.hit.eH.toFixed(1) + ' <i>m</i>']); if (U.isNum(ref.hit.eV)) rows.push(['Height error', (ref.hit.eV > 0 ? '+' : '') + ref.hit.eV.toFixed(1) + ' <i>m</i>']); }
      const age = (t - tr.t[i]) / 1000;
      rows.push(['Radar update', age >= 0 ? age.toFixed(1) + ' <i>s ago</i>' : '<i>in</i> ' + (-age).toFixed(1) + ' <i>s</i>']);
    }
    el.className = 'kv';
    el.innerHTML = kvHtml(rows);
  }
  function renderWxNow(t) {
    const el = $('#wxNow'), wx = S.weather;
    $('#wxName').textContent = wx ? wx.name + (wx.simulated ? ' (simulated)' : '') : '';
    const W = (k) => (wx ? SF.weather.at(wx, k, t) : NaN);
    const ws = W('wind'), wg = W('gust'), wd = W('dir');
    drawWindDial(ws, wg, wd);
    if (!wx) { el.className = 'kv muted'; el.textContent = 'No weather data'; return; }
    const rows = [['Wind', uh(fmtSpeed(ws))], ['Gust', uh(fmtSpeed(wg))], ['From', U.isNum(wd) ? wd.toFixed(0) + '°' : '–']];
    if (U.isNum(W('temp'))) rows.push(['Temperature', W('temp').toFixed(1) + ' <i>°C</i>']);
    if (U.isNum(W('rh'))) rows.push(['Humidity', W('rh').toFixed(0) + ' <i>%</i>']);
    if (U.isNum(W('pres'))) rows.push(['Pressure', W('pres').toFixed(1) + ' <i>hPa</i>']);
    const rain = W('rainRate');
    if (U.isNum(rain)) rows.push(['Rain', rain.toFixed(1) + ' <i>mm/h</i>']);
    if (!U.isNum(ws) && !U.isNum(W('temp'))) { el.className = 'kv muted'; el.textContent = 'No weather data at this time'; return; }
    el.className = 'kv';
    el.innerHTML = kvHtml(rows);
  }
  function drawWindDial(ws, wg, wd) {
    const c = $('#windDial'), dpr = window.devicePixelRatio || 1, size = 104;
    if (c.width !== size * dpr) { c.width = size * dpr; c.height = size * dpr; }
    const ctx = c.getContext('2d'), font = getComputedStyle(document.body).fontFamily;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const cx = size / 2, cy = size / 2, r = 39;
    const at = (deg, rad) => [cx + Math.sin(deg * U.D2R) * rad, cy - Math.cos(deg * U.D2R) * rad];
    ctx.strokeStyle = 'rgba(255,255,255,0.13)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
    for (let a = 0; a < 360; a += 15) {
      const major = a % 90 === 0, p = at(a, r - (major ? 6 : a % 45 === 0 ? 4 : 2.5)), q = at(a, r);
      ctx.strokeStyle = major ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.2)';
      ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
    }
    ctx.fillStyle = '#a4a6ac'; ctx.font = '600 9px ' + font; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(([l, a]) => { const p = at(a, r + 7.5); ctx.fillText(l, p[0], p[1]); });
    if (U.isNum(wd)) {
      // the arrow points where the wind blows to; its tail is where it comes from
      const a = wd + 180, tail = at(wd, r - 5), tip = at(a, r - 4), b1 = at(a - 13, r - 15), b2 = at(a + 13, r - 15), neck = at(a, r - 13);
      ctx.strokeStyle = ctx.fillStyle = '#7aa7ff'; ctx.lineWidth = 2; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(tail[0], tail[1]); ctx.lineTo(neck[0], neck[1]); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(tip[0], tip[1]); ctx.lineTo(b1[0], b1[1]); ctx.lineTo(b2[0], b2[1]); ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = '#1c1d20';
    ctx.beginPath(); ctx.arc(cx, cy, 17, 0, 2 * Math.PI); ctx.fill();
    ctx.fillStyle = '#ececee'; ctx.font = '600 14px ' + font;
    const v = U.isNum(ws) ? (speedUnit() === 'kmh' ? ws * 3.6 : speedUnit() === 'kt' ? ws / 0.514444 : ws) : NaN;
    ctx.fillText(U.isNum(v) ? v.toFixed(1) : '–', cx, cy - 2);
    ctx.fillStyle = '#707279'; ctx.font = '9px ' + font;
    ctx.fillText(speedUnit() === 'kmh' ? 'km/h' : speedUnit() === 'kt' ? 'kn' : 'm/s', cx, cy + 10);
  }
  let evCache = null, evKey = '';
  function renderEvents(t) {
    const key = (S.drone ? S.drone.events.length : 0) + ':' + (S.radar ? S.radar.events.length : 0) + ':' + (S.analysis && S.analysis.breaks ? S.analysis.breaks.length : 0) + ':' + S.offsets.drone + ':' + S.offsets.radar + ':' + (S.analysis ? S.analysis.hits && S.analysis.hits.length : 0);
    if (key !== evKey) {
      evKey = key;
      evCache = allEvents().slice(0, 400);
      const el = $('#eventList');
      if (!evCache.length) { el.className = 'events muted'; el.textContent = 'None'; }
      else {
        el.className = 'events';
        el.innerHTML = evCache.map((e, k) => '<div class="ev ' + e.kind + '" data-k="' + k + '"><span class="t">' + U.fmtClock(e.t, S.tzOffset()) + '</span><span class="m">' + esc(e.text) + '</span></div>').join('');
      }
    }
    if (!evCache || !evCache.length) return;
    let k = -1;
    for (let i = 0; i < evCache.length; i++) if (evCache[i].t <= t) k = i;
    $$('#eventList .ev.now').forEach((e) => e.classList.remove('now'));
    if (k >= 0) { const e = $('#eventList .ev[data-k="' + k + '"]'); if (e) e.classList.add('now'); }
  }

  // ================================================================== Files tab
  function offsetHtml(kind) {
    const b = (d, text, tip) => '<button data-off="' + kind + '" data-d="' + d + '" title="' + tip + '">' + text + '</button>';
    return '<div class="offset"><span class="lbl">Clock offset</span><div class="stepper">' + b(-3600000, '−1 h', 'One hour earlier') + b(-1000, '−1 s', 'One second earlier') +
      '<input type="number" step="0.1" data-offin="' + kind + '" value="' + (S.offsets[kind] / 1000).toFixed(1) + '" aria-label="Clock offset in seconds" title="Seconds; a positive offset moves the file later">' +
      b(1000, '+1 s', 'One second later') + b(3600000, '+1 h', 'One hour later') + '</div></div>';
  }
  function metaHtml(rows) { return '<div class="meta">' + rows.filter((r) => r && r[1] !== undefined && r[1] !== '' && r[1] !== null).map((r) => '<span>' + r[0] + '</span><b title="' + esc(String(r[1])) + '">' + esc(String(r[1])) + '</b>').join('') + '</div>'; }
  function fileSec(sw, name, kind, body) {
    return '<section class="sec"><header><i class="sw ' + sw + '"></i><h3 title="' + esc(name) + '">' + esc(name) + '</h3><button class="x" data-remove="' + kind + '" title="Remove">' + ICON('close') + '</button></header>' + body + '</section>';
  }
  function renderFiles() {
    const parts = [];
    const tz = S.tzOffset(), tm = (t) => U.fmtClock(t, tz), dt = (t) => U.fmtDate(t, tz) + ' ' + tm(t);
    if (S.drone && S.fleet.length > 1) {
      parts.push('<section class="sec"><header><i class="sw drone"></i><h3>' + S.fleet.length + ' drones in this scene</h3></header><table class="t fleet">' +
        S.fleet.map((d, k) => '<tr class="click' + (d === S.drone ? ' on' : '') + '" data-drone="' + k + '"><td><i class="fdot" style="background:' + d.color + '"></i>' + esc(d.short) + '</td><td class="muted" title="' + esc(d.file || '') + '">' + esc(d.product || d.name) + '</td><td>' + tm(d.data.t[0]) + '</td><td class="r"><button class="x" data-dropdrone="' + k + '" title="Remove this drone">' + ICON('close') + '</button></td></tr>').join('') +
        '</table><p class="note">Select a drone to show its details here and under Analysis. The radar is compared with every drone; each radar update counts for the nearest one.</p></section>');
    }
    if (S.drone || S.djiSummary) {
      const d = S.drone, s = S.djiSummary;
      let rows;
      if (d) {
        const det = d.details;
        rows = [['Aircraft', d.product || d.name], d.label ? ['Name', d.label] : null, ['Source', d.source === 'dji' ? 'DJI flight record v' + d.version + (det ? ' · ' + (S.djiLog ? S.djiLog.departmentName + ' ' : '') + det.appVersion : '') : SF.tracks.sourceText(d)],
          d.timeSource ? ['Time from', d.timeSource] : null,
          ['Start', dt(d.data.t[0])], ['Length', U.fmtDur((d.data.t[d.data.t.length - 1] - d.data.t[0]) / 1000)], ['Points', d.data.t.length.toLocaleString('en-US')],
          d.home ? ['Home', d.home.lat.toFixed(6) + ', ' + d.home.lon.toFixed(6)] : null, det && det.aircraftSn ? ['Serial', det.aircraftSn] : null];
      } else {
        rows = [['Aircraft', s.product], ['Source', 'DJI flight record v' + s.version + ' · ' + s.app], ['Start', dt(s.start)], ['Length', U.fmtDur(s.duration)], ['Distance', U.fmtDist(s.distance)],
          ['Max height', U.f(s.maxHeight, 0, 'm')], s.home ? ['Home', s.home.lat.toFixed(6) + ', ' + s.home.lon.toFixed(6)] : null];
      }
      parts.push(fileSec('drone', S.files.drone, 'drone', metaHtml(rows) +
        (s ? '<div class="lock">The GPS track is encrypted by DJI. ' + (S.settings.djiApiKey ? '<button class="link" data-unlock>Unlock it</button> (needs internet once).' : '<button class="link" data-settings="dji">Add your DJI API key</button> to unlock it.') + '</div>' : '') +
        (d ? offsetHtml('drone') : '')));
    }
    if (S.radar) {
      const r = S.radar, sen = R.sensor(r);
      const rows = [['Radar', r.name], sen && sen.version ? ['Software', sen.version] : null, sen && U.isNum(sen.freqMHz) ? ['Frequency', sen.freqMHz + ' MHz'] : null,
        ['Recording', isFinite(r.t0) ? dt(r.t0) + '–' + tm(r.t1) : '–'], ['Tracks', r.tracks.length.toLocaleString('en-US') + ' · ' + r.updates.toLocaleString('en-US') + ' updates'], ['Messages', r.messages.toLocaleString('en-US')],
        r.plots.t.length ? ['Drone plots', r.plots.t.length.toLocaleString('en-US')] : null, sen ? ['Position', sen.lat.toFixed(6) + ', ' + sen.lon.toFixed(6)] : null,
        sen && U.isNum(sen.alt) ? ['Antenna, ground', U.f(sen.alt, 1, 'm') + ', ' + U.f(sen.ground, 1, 'm') + ' MSL'] : null,
        ['Classes', [...r.classes.keys()].join(', ')]];
      parts.push(fileSec('radar', S.files.radar || r.name, 'radar', metaHtml(rows) +
        (sen ? '' : '<div class="lock">This file has no radar position. Enter it to compare the radar with the drone.<div class="lockgrid"><input id="setRLat" placeholder="Latitude"><input id="setRLon" placeholder="Longitude"><input id="setRAlt" placeholder="Height (m)"><button class="ghost small" id="btnSetRadarPos">Set Position</button></div></div>') +
        (S.live ? '<p class="note">The clock offset is fixed while the live feed runs.</p>' : offsetHtml('radar'))));
    }
    if (S.weather) {
      const w = S.weather;
      const rows = [['Station', w.name], w.sensors ? ['Sensors', w.sensors] : null, ['Data', dt(w.t0) + '–' + dt(w.t1)], ['Rows', w.data.t.length.toLocaleString('en-US') + (U.isNum(w.stepMs) ? ' · every ' + U.fmtDur(w.stepMs / 1000) : '')],
        ['Columns', w.columns.map((c) => (SF.weather.KINDS[c.kind] || {}).label || c.kind).join(', ')], ['Times read as', w.tzNote], w.station ? ['Position', w.station.lat.toFixed(5) + ', ' + w.station.lon.toFixed(5)] : null];
      parts.push(fileSec('wx', S.files.weather, 'weather', metaHtml(rows) + (w.notes.length ? '<p class="note">' + esc(w.notes.join(' ')) + '</p>' : '') + offsetHtml('weather')));
    }
    if (!parts.length) parts.push('<div class="empty-note">No data loaded. Use Import in the toolbar, or drop files on the window.</div>');
    else parts.push('<p class="note foot">A clock offset shifts a whole file in time, for example when the radar clock or a file in local time is off. A positive offset moves the file later.</p>');
    $('#filesBody').innerHTML = parts.join('');
  }

  // ================================================================== Analysis tab
  function pctTxt(x) { return U.isNum(x) ? (x * 100).toFixed(1) + '%' : '–'; }
  function grade(x) { return x >= 0.9 ? 'good' : x >= 0.6 ? 'mid' : 'poor'; }
  const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
  const pm = (st, d, unit) => (st && st.n ? (st.mean >= 0 ? '+' : '−') + Math.abs(st.mean).toFixed(d) + ' ± ' + st.std.toFixed(d) + (unit === '°' ? '°' : ' ' + unit) : '–');
  const clsHtml = (c) => '<span class="cls" style="--c:' + U.classColor(c) + '">' + esc(c) + '</span>';
  const section = (title, body, aside) => '<section class="sec"><header><h3>' + title + '</h3>' + (aside ? '<span>' + aside + '</span>' : '') + '</header>' + body + '</section>';
  // one result row: label, value, optional small print and bar; the definition shows on hover
  function metric(value, label, sub, tip, opt) {
    opt = opt || {};
    return '<div class="m" title="' + esc(tip || '') + '"><span class="ml">' + label + '</span><b class="mv">' + uh(minus(value)) + '</b>' +
      (sub ? '<small class="ms">' + sub + '</small>' : '') + (opt.bar != null ? '<div class="meter"><i class="' + (opt.grade || '') + '" style="width:' + Math.round(U.clamp(opt.bar, 0, 1) * 100) + '%"></i></div>' : '') + '</div>';
  }
  function renderAnalysis() {
    const el = $('#analysisBody'), an = S.analysis;
    if (!an) { el.className = 'empty-note'; el.innerHTML = S.djiSummary ? 'The DJI log is encrypted. Unlock it to compare the drone with the radar.' : 'Import a drone log and radar data to compare them.'; return; }
    el.className = '';
    const tz = S.tzOffset(), tm = (t) => U.fmtClock(t, tz);
    const parts = [];
    if (S.anySimulated()) parts.push('<p class="warnnote">' + ICON('info') + 'Simulated demo data, not a real flight.</p>');
    if (S.fleetAn) parts.push(fleetHtml());
    if (an.coverage) {
      const c = an.coverage, a = an.acc || {};
      const hl = (v, unit, label, tip) => '<div title="' + esc(tip) + '"><b>' + v + (unit ? '<small>' + unit + '</small>' : '') + '</b><span>' + label + '</span></div>';
      const p1 = (x) => (U.isNum(x) ? (x * 100).toFixed(1) : '–');
      parts.push('<div class="hl">' + hl(p1(c.detected), '%', 'Detected', 'Probability of detection') + hl(p1(c.classified), '%', 'Classified', 'Probability of classification') +
        hl(U.isNum(a.cep50) ? a.cep50.toFixed(1) : '–', 'm', 'CEP50', 'Median horizontal error of the radar') +
        (S.cwa ? hl(S.cwa.overall.toFixed(2), '', 'CWA score', 'Overall CWA 18150 score, from 0 to 1') : hl(an.firstDetect ? an.firstDetect.after.toFixed(0) : '–', 's', 'To detect', 'Seconds from takeoff to the first detection')) + '</div>');
      parts.push(section('Summary', (S.fleetAn ? '<p class="note" style="margin:0 0 6px">Showing <b style="color:' + S.drone.color + '">' + esc(S.drone.short) + '</b>. Select another drone above or on the map; the CWA 18150 scores cover all of them.</p>' : '') +
        '<p class="summary">' + esc(A.summaryText(S.drone, S.radar, an)) + '</p>' +
        '<div class="btnrow"><button class="ghost small" data-export="pdf">' + ICON('report') + 'Export Report…</button><button class="ghost small" id="btnTestDetails">' + ICON('edit') + 'Test Details…</button></div>'));
      parts.push(section('Detection', '<div class="metrics">' +
        metric(pctTxt(c.detected), 'Probability of detection', null, 'The share of 1-second steps between takeoff and landing in which a radar track followed the drone within ' + S.aopts.gate + ' m and ±' + S.aopts.tol + ' s.', { grade: grade(c.detected), bar: c.detected }) +
        metric(an.firstDetect ? an.firstDetect.after.toFixed(0) + ' s' : '–', 'Time to first detection', an.firstDetect && U.isNum(an.firstDetect.range) ? 'After takeoff, ' + U.fmtDist(an.firstDetect.range) + ' from the radar' : 'After takeoff', 'Seconds from takeoff to the first radar update that matched the drone.') +
        metric(U.isNum(an.maxDetectRange) ? U.fmtDist(an.maxDetectRange) : '–', 'Longest range tracked', an.flight && an.flight.maxRange ? 'Of ' + U.fmtDist(an.flight.maxRange) + ' flown' : '', 'The greatest distance from the radar at which a radar update matched the drone.') + '</div>'));
      parts.push(section('Classification', '<div class="metrics">' +
        metric(pctTxt(c.classified), 'Probability of classification', null, 'The share of airborne 1-second steps in which the matching radar track carried a drone label (' + S.aopts.labels.join(', ') + ').', { grade: grade(c.classified), bar: c.classified }) +
        metric(an.firstClass ? an.firstClass.after.toFixed(0) + ' s' : '–', 'Time to classification', an.firstClass && U.isNum(an.firstClass.range) ? 'After takeoff, ' + U.fmtDist(an.firstClass.range) + ' from the radar' : 'After takeoff', 'Seconds from takeoff to the first matching radar update labelled as a drone.') +
        metric(pctTxt(an.labelShare), 'Label consistency', 'Matched updates classified as drone', 'Of all radar updates that matched the drone in the air, the share that carried a drone label.') +
        metric(String(an.falseTracks.length), 'False drone tracks', U.isNum(an.falseRate) ? an.falseRate.toFixed(1) + ' per hour of recording' : '', 'Radar tracks labelled as a drone that never followed the test drone.') + '</div>'));
      parts.push(section('Tracking', '<div class="metrics">' +
        metric(String(an.breaks.length), 'Track breaks', an.breaks.length ? 'Longest ' + an.longestBreak.toFixed(0) + ' s' : 'None longer than ' + S.aopts.breakGap + ' s', 'Gaps longer than ' + S.aopts.breakGap + ' s between matching radar updates while the drone was airborne, including before the first detection and after the last.') +
        metric(String(an.matchedIds.length), 'Track IDs on the drone', an.trackSwitches + ' ID changes in flight', 'How many different radar track IDs followed the drone (track fragmentation).') +
        metric(U.fmtDur(an.longestTrack), 'Longest unbroken track', '', 'The longest stretch of matching radar updates without a break.') +
        metric(U.isNum(an.updateInterval) ? an.updateInterval.toFixed(1) + ' s' : '–', 'Update interval', 'Median while tracked', 'Median time between consecutive matching radar updates.') + '</div>'));
      parts.push(section('Accuracy', '<div class="metrics">' +
        metric(U.f(a.cep50, 1, 'm'), 'CEP50', '', 'Half of the matching radar positions were closer to the logged drone position than this.') +
        metric(U.f(a.cep95, 1, 'm'), 'CEP95', '', '95 % of the matching radar positions were closer to the logged drone position than this.') +
        metric(U.f(a.rmse, 1, 'm'), 'RMSE', '', 'Root mean square of the horizontal miss distance.') +
        metric(U.isNum(a.biasMag) ? U.f(a.biasMag, 1, 'm') : '–', 'Mean offset', U.isNum(a.biasMag) ? 'Toward ' + compass(a.biasDir) + ' (' + a.biasDir.toFixed(0) + '°)' : '', 'The average radar position minus the drone position. A large offset in one direction points to a calibration or alignment error rather than noise.') +
        metric(pm(a.range, 1, 'm'), 'Range error', 'Mean ± SD, along the beam', 'Radar-measured distance minus true distance from the radar.') +
        metric(pm(a.az, 2, '°'), 'Bearing error', 'Mean ± SD, across the beam', 'Radar-measured bearing minus true bearing from the radar (positions within 30 m of the radar left out).') +
        metric(pm(a.height, 1, 'm'), 'Height error', 'Mean ± SD, radar − drone', 'Radar height above the radar site\'s ground minus the drone\'s logged height above takeoff (assumes flat ground).') + '</div>', 'Radar against drone log'));
      if (S.cwa) parts.push(cwaHtml(S.cwa));
      const checks = A.checks(S.drone, S.radar, S.weather, an, S.sync);
      const ci = { ok: 'check', warn: 'warn', info: 'info' };
      parts.push(section('Data checks', '<ul class="checks">' + checks.map((k) => '<li class="' + k.level + '">' + ICON(ci[k.level] || 'info') + '<span>' + esc(k.text) + '</span></li>').join('') + (S.sync ? '' : '<li class="info">' + ICON('info') + '<span>Checking the radar clock…</span></li>') + '</ul>'));
      parts.push(section('Radar clock', '<div class="syncbox"><span>Current offset <b>' + (S.offsets.radar ? esc(S.offsetText('radar')) : 'None') + '</b></span><button class="ghost small" id="btnSync">' + ICON('sync') + 'Find Best Offset</button></div><div id="syncOut" class="note"></div><p class="note">Compares the radar positions with the drone log at different time shifts and picks the shift with the smallest error.</p>'));
      if (an.rangeBins && an.rangeBins.length) {
        const pc = (x, cls) => pctTxt(x) + '<div class="meter"><i class="' + cls + '" style="width:' + Math.round(x * 100) + '%"></i></div>';
        parts.push(section('By range from radar', '<table class="t"><tr><th>Range</th><th class="r">Time</th><th>Tracked</th><th>Classified</th></tr>' +
          an.rangeBins.map((b) => '<tr><td>' + b.lo + '–' + b.hi + ' m</td><td class="r">' + U.fmtDur(b.n, true) + '</td><td>' + pc(b.det / b.n, 'det') + '</td><td>' + pc(b.cls / b.n, 'cls') + '</td></tr>').join('') + '</table>'));
      }
      parts.push(section('Track breaks', an.breaks.length ? '<div class="tablewrap"><table class="t"><tr><th>From</th><th class="r">Length</th><th class="r">Range</th><th class="r">Height</th><th>Cause</th></tr>' +
        an.breaks.map((b) => '<tr class="click" data-seek="' + b.t0 + '"><td>' + tm(b.t0) + '</td><td class="r">' + b.dur.toFixed(0) + ' s</td><td class="r">' + U.fmtDist(b.range) + '</td><td class="r">' + U.f(b.h, 0, 'm') + '</td><td class="l">' + esc(b.reason) + '</td></tr>').join('') + '</table></div>' : '<p class="note">No breaks.</p>'));
      const matched = [...an.trackInfo.values()].filter((x) => x.matched);
      parts.push(section('Radar tracks on the drone', matched.length ? '<div class="tablewrap"><table class="t"><tr><th>Track</th><th>Class</th><th>From</th><th class="r">Updates</th><th class="r">Error</th></tr>' +
        matched.map((x) => '<tr class="click" data-track="' + esc(x.id) + '" data-seek="' + x.first + '"><td>' + esc(x.id) + '</td><td>' + clsHtml(x.cls) + '</td><td>' + tm(x.first) + '</td><td class="r">' + x.close + '</td><td class="r">' + U.f(x.medErr, 1, 'm') + '</td></tr>').join('') + '</table></div>' : '<p class="note">No radar track followed the drone within ' + S.aopts.gate + ' m. Check the clock offsets, or raise the match distance under Settings › Analysis.</p>'));
      if (an.falseTracks.length) parts.push(section('Drone labels on other tracks', '<div class="tablewrap"><table class="t"><tr><th>Track</th><th>Class</th><th>From</th><th class="r">Updates</th><th class="r">Closest</th></tr>' +
        an.falseTracks.map((x) => '<tr class="click" data-track="' + esc(x.id) + '" data-seek="' + x.first + '"><td>' + esc(x.id) + '</td><td>' + clsHtml(x.cls) + '</td><td>' + tm(x.first) + '</td><td class="r">' + x.n + '</td><td class="r">' + (isFinite(x.minDist) ? U.fmtDist(x.minDist) : '–') + '</td></tr>').join('') + '</table></div>'));
    } else if (an.hasDrone && !an.hasRadar) {
      const f = an.flight;
      parts.push(section('Flight', '<div class="kv">' + kvHtml([['Airborne', uh(U.fmtDur(f.airborne))], ['Distance', uh(U.fmtDist(f.distance))], ['Max height', uh(U.f(f.maxH, 0, 'm'))], ['Max speed', uh(fmtSpeed(f.maxHs))], ['Max from home', uh(U.fmtDist(f.maxHome))]]) + '</div><p class="note">Import radar data to see detection results.</p>'));
    }
    if (an.radar) {
      const byc = Object.entries(an.radar.byClass).sort((a, b) => b[1] - a[1]);
      parts.push(section('Radar tracks by class', '<table class="t">' + byc.map(([c, n]) => '<tr><td>' + clsHtml(c) + '</td><td class="r">' + n + '</td></tr>').join('') + '</table>'));
      if (!an.hasDrone && an.labelledTracks.length) {
        parts.push(section('Drone-classified tracks', '<div class="tablewrap"><table class="t"><tr><th>Track</th><th>From</th><th class="r">Length</th><th class="r">Updates</th></tr>' +
          an.labelledTracks.map((tr) => '<tr class="click" data-track="' + esc(tr.id) + '" data-seek="' + tr.first + '"><td>' + esc(tr.id) + '</td><td>' + tm(tr.first) + '</td><td class="r">' + U.fmtDur((tr.last - tr.first) / 1000, true) + '</td><td class="r">' + tr.t.length + '</td></tr>').join('') + '</table></div>'));
      }
    }
    if (an.weather && an.weather.samples) {
      const ws = an.weather;
      parts.push(section('Weather during the flight', '<div class="kv">' + kvHtml([['Mean wind', uh(fmtSpeed(ws.wind.mean))], ['Max gust', uh(fmtSpeed(ws.gust.max))], ['From', U.isNum(ws.dir) ? ws.dir.toFixed(0) + '° (' + compass(ws.dir) + ')' : '–'], ['Temperature', uh(U.f(ws.temp.mean, 1, '°C'))]]) + '</div>'));
    }
    parts.push('<p class="note foot">Match distance ' + S.aopts.gate + ' m, time tolerance ±' + S.aopts.tol + ' s, drone labels ' + esc(S.aopts.labels.join(', ')) + '. Hover a value for its definition. Change these under Settings › Analysis.</p>');
    el.innerHTML = parts.join('');
  }
  // Several drones: one row each, click to select
  function fleetHtml() {
    const rows = S.fleet.map((d, k) => {
      const an = S.fleetAn[k], c = an.coverage || {}, a = an.acc || {};
      return '<tr class="click' + (d === S.drone ? ' on' : '') + '" data-drone="' + k + '"><td><i class="fdot" style="background:' + d.color + '"></i>' + esc(d.short) + '</td><td class="r">' + pctTxt(c.detected) + '</td><td class="r">' + pctTxt(c.classified) + '</td><td class="r">' +
        (an.firstDetect ? an.firstDetect.after.toFixed(0) + ' s' : '–') + '</td><td class="r">' + U.f(a.cep50, 0, 'm') + '</td><td class="r">' + (an.matchedIds ? an.matchedIds.length : '–') + '</td></tr>';
    });
    return section(S.fleet.length + ' drones in this scene', '<div class="tablewrap"><table class="t fleet"><tr><th>Drone</th><th class="r" title="Probability of detection">Pd</th><th class="r" title="Probability of classification">Pc</th><th class="r" title="Time to first detection">Detect</th><th class="r">CEP50</th><th class="r" title="Radar track IDs on the drone">Tracks</th></tr>' + rows.join('') + '</table></div>');
  }
  // CWA 18150 section of the Analysis tab: the function scores, then every metric with its score
  function cwaHtml(cw) {
    const W = SF.cwa, sc = (x) => (U.isNum(x) ? x.toFixed(2) : '–');
    const tip = 'CWA 18150:2024 clause 13.4.6: every metric is scored from 0 (worst) to 1 (best) against its target; the scores are averaged per function, weighted.';
    let h = '<div class="metrics">' + metric(sc(cw.overall), 'Overall score', 'Mean of the three functions', tip, { grade: W.grade(cw.overall), bar: cw.overall }) +
      W.FUNCTIONS.map(([k, name]) => metric(sc(cw.fn[k]), name, null, tip, { grade: W.grade(cw.fn[k]), bar: cw.fn[k] })).join('') + '</div>';
    h += '<table class="t cwa" style="margin-top:10px"><tr><th>Metric</th><th>Value</th><th>Score</th></tr>';
    for (const [k, name] of W.FUNCTIONS) {
      h += '<tr class="grp"><td colspan="3">' + name + '</td></tr>';
      for (const m of W.METRICS.filter((x) => x.fn === k)) {
        const r = cw.metrics[m.id], g = W.grade(r.score);
        const t = m.def + ' Target ' + W.ctxText(m, r.target) + ', worst ' + W.ctxText(m, r.worst) + (r.weight !== 1 ? ', weight ' + r.weight : '') + '.';
        h += '<tr title="' + esc(t) + '"><td class="l">' + esc(m.name) + '</td><td class="l">' + esc(W.valueText(m, r)) + '</td><td class="sc"><span class="scv ' + g + '">' + r.score.toFixed(2) + '</span><div class="meter"><i class="' + g + '" style="width:' + Math.round(r.score * 100) + '%"></i></div></td></tr>';
      }
    }
    return section('CWA 18150', h + '</table><p class="note"><button class="link" id="btnCwaCtx">Scoring Targets…</button></p><details class="more"><summary>How these scores were computed</summary><p class="note">' + esc(W.notesText(cw, S.radar && S.radar.name)) + '</p></details>', 'Scores from 0 to 1');
  }
  function fillCwa(cfg) {
    const W = SF.cwa;
    $('#cwaAoi').value = cfg.aoi > 0 ? cfg.aoi : 0;
    $('#cwaAoiNow').textContent = '0 = just wide enough for the whole flight' + (S.cwa && S.cwa.aoiAuto && U.isNum(S.cwa.aoi) ? ' (now ' + U.fmtDist(S.cwa.aoi) + ')' : '') + '. Tracks and detections outside it are left out.';
    let h = '';
    for (const [fn, name] of W.FUNCTIONS) {
      h += '<tr class="grp"><td colspan="4">' + name + '</td></tr>';
      for (const m of W.METRICS.filter((x) => x.fn === fn)) {
        const c = W.context(cfg, m.id), u = m.kind === 'frac' ? '%' : m.kind === 'num' ? m.unit : '';
        const inp = (k, v) => '<input type="number" step="any" data-cwa="' + m.id + '" data-k="' + k + '" value="' + v + '">';
        h += '<tr title="' + esc(m.def) + '"><td class="l">' + esc(m.name) + '</td><td>' + inp('target', W.toDisplay(m, c.target)) + ' <span class="u">' + u + '</span></td><td>' +
          inp('worst', W.toDisplay(m, c.worst)) + ' <span class="u">' + u + '</span></td><td>' + inp('weight', c.weight) + '</td></tr>';
      }
    }
    $('#cwaRows').innerHTML = h;
  }
  function openCwa() {
    fillCwa(S.cwaCfg || SF.cwa.defaults());
    $('#dlgCwa').showModal();
  }
  function saveCwa() {
    const W = SF.cwa, ctx = {};
    for (const m of W.METRICS) {
      const get = (k) => U.num($('#cwaRows [data-cwa="' + m.id + '"][data-k="' + k + '"]').value);
      let target = W.fromDisplay(m, get('target')), worst = W.fromDisplay(m, get('worst')), weight = get('weight');
      if (!U.isNum(target)) target = m.target;
      if (!U.isNum(worst)) worst = m.worst;
      if (!U.isNum(weight) || weight < 0) weight = 1;
      if (target !== m.target || worst !== m.worst || weight !== 1) ctx[m.id] = { target, worst, weight };
    }
    S.cwaCfg = { aoi: Math.max(0, U.num($('#cwaAoi').value) || 0), ctx };
    S.settings.cwa = S.cwaCfg;
    SF.native.saveSettings({ cwa: S.cwaCfg });
    onDataChanged({});
    toast('CWA 18150 scoring saved.', 'ok');
  }
  async function autoSync() {
    if (!S.drone || !S.radar) return;
    const out = $('#syncOut');
    out.textContent = 'Searching…';
    await U.tick();
    const cur = S.offsets.radar;
    // search around the current offset; the result is relative to the current radar times
    const res = A.estimateOffset(S.drone, S.radar, { span: 60 });
    if (!res) { out.textContent = 'Not enough radar data near the drone to tell.'; return; }
    const best = cur + res.offsetMs;
    out.innerHTML = 'Best ' + (best >= 0 ? '+' : '') + (best / 1000).toFixed(1) + ' s (median error ' + res.medianAtZero.toFixed(1) + ' → ' + res.median.toFixed(1) + ' m) <button class="primary small" id="btnSyncApply">Apply</button>';
    $('#btnSyncApply').onclick = () => { applyOffset('radar', best); onDataChanged({}); toast('Radar clock offset set to ' + (best / 1000).toFixed(1) + ' s.', 'ok'); };
  }

  // ================================================================== map overlay drawing
  const COL = { drone: '#ff7a1a', droneFuture: 'rgba(255,255,255,0.32)', shadow: 'rgba(0,0,0,0.45)', ring: 'rgba(255,255,255,0.28)', text: '#e8edf2', match: '#4cc9f0' };
  let hitList = [];
  function label(ctx, text, x, y, color, align) {
    if (!S.layers.labels) return;
    ctx.font = '600 11px ' + getComputedStyle(document.body).fontFamily;
    ctx.textAlign = align || 'left';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = 'rgba(8,10,13,0.85)';
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color || COL.text;
    ctx.fillText(text, x, y);
  }
  function radarGround() {
    const s = S.radar && R.sensor(S.radar);
    return s ? (U.isNum(s.ground) ? s.ground : U.isNum(s.alt) ? s.alt : 0) : 0;
  }
  // radar status of the drone at t: 2 labelled drone, 1 tracked, 0 missed, 3 not airborne
  const STATUS_COLORS = { 2: '#ff4fd8', 1: '#4cc9f0', 0: '#ff4b4b', 3: 'rgba(255,255,255,0.75)' };
  function statusAt(t) {
    const se = S.analysis && S.analysis.series;
    if (!se || !se.t.length || t < se.t[0] - 500 || t > se.t[se.t.length - 1] + se.step) return 3;
    const k = U.clamp(Math.round((t - se.t[0]) / se.step), 0, se.t.length - 1);
    return se.cls[k] ? 2 : se.det[k] ? 1 : 0;
  }
  const HEIGHT_RAMP = ['#4cc9f0', '#3ddc84', '#ffd60a', '#ff7a1a', '#ff4b6e'];
  function heightColor(h) {
    const max = (S.analysis && S.analysis.flight && S.analysis.flight.maxH) || 100;
    const f = U.clamp((U.isNum(h) ? h : 0) / Math.max(1, max), 0, 1);
    return HEIGHT_RAMP[Math.min(HEIGHT_RAMP.length - 1, Math.floor(f * HEIGHT_RAMP.length))];
  }
  function pathMode() {
    const m = S.layers.pathColor || 'status';
    return m === 'status' && !(S.analysis && S.analysis.series) ? 'solid' : m;
  }
  function droneIdx() {
    if (S.droneIdx) return S.droneIdx;
    const d = S.drone.data, n = d.t.length, step = Math.max(1, Math.floor(n / 4000));
    const idx = [];
    for (let i = 0; i < n; i += step) idx.push(i);
    if (idx[idx.length - 1] !== n - 1) idx.push(n - 1);
    return (S.droneIdx = idx);
  }
  function drawOverlay(ctx, mv) {
    hitList = [];
    const t = S.t;
    if (!U.isNum(t) && !hasData()) return;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const sensor = S.radar && R.sensor(S.radar);
    const g0 = radarGround();
    if (mv.terrain && S.drone) mv.updateGround(S.drone.home ? S.drone.home.lon : S.drone.data.lon[0], S.drone.home ? S.drone.home.lat : S.drone.data.lat[0]);
    const P = (lon, lat, h) => mv.project(lon, lat, mv.z(h));

    // range rings and the radar's look direction
    if (sensor && S.layers.rings) {
      const maxR = Math.max(1000, (S.analysis && S.analysis.flight && S.analysis.flight.maxRange) || 0);
      const step = maxR > 4000 ? 1000 : 500;
      ctx.strokeStyle = COL.ring; ctx.lineWidth = 1; ctx.setLineDash([4, 5]);
      for (let r = step; r <= maxR + step; r += step) {
        ctx.beginPath();
        let pen = false, top = null;
        for (let k = 0; k <= 96; k++) {
          const [la, lo] = U.destination(sensor.lat, sensor.lon, (k / 96) * 360, r);
          const p = P(lo, la, 0);
          if (!p) { pen = false; continue; }
          if (k === 0) top = p;
          if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; }
        }
        ctx.stroke();
        if (top) { ctx.setLineDash([]); label(ctx, r >= 1000 ? r / 1000 + ' km' : r + ' m', top[0] + 4, top[1] - 7, 'rgba(232,237,242,0.75)'); ctx.setLineDash([4, 5]); }
      }
      ctx.setLineDash([]);
      for (const b of sensor.blanking || []) {
        if (!b.enabled || !b.span) continue;
        ctx.fillStyle = 'rgba(255,93,93,0.12)';
        ctx.beginPath();
        const c = P(sensor.lon, sensor.lat, 0);
        if (!c) continue;
        ctx.moveTo(c[0], c[1]);
        for (let k = 0; k <= 24; k++) { const [la, lo] = U.destination(sensor.lat, sensor.lon, b.angle - b.span / 2 + (b.span * k) / 24, maxR + step); const p = P(lo, la, 0); if (p) ctx.lineTo(p[0], p[1]); }
        ctx.closePath(); ctx.fill();
      }
    }

    // all radar tracks (faint history)
    if (S.radar && S.layers.radarAll) {
      ctx.lineWidth = 1;
      for (const tr of S.radar.tracks) {
        if (S.layers.radarOnlyDrone && !S.matched.has(tr.id) && !Object.keys(tr.classes).some((c) => A.isDroneLabel(c, S.aopts.labels))) continue;
        ctx.strokeStyle = U.classColor(tr.mainClass);
        ctx.globalAlpha = S.matched.has(tr.id) ? 0.55 : 0.28;
        ctx.beginPath();
        let pen = false;
        for (let i = 0; i < tr.t.length; i++) {
          const p = P(tr.lon[i], tr.lat[i], tr.alt[i] - g0);
          if (!p || (i && tr.t[i] - tr.t[i - 1] > 6000)) { pen = false; if (!p) continue; }
          if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; }
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // the other drones of the scene: path and position in their own colour (click one to select it)
    if (S.drone && S.fleet.length > 1) {
      for (const od of S.fleet) {
        if (od === S.drone) continue;
        const d = od.data, every = Math.max(1, Math.round(d.t.length / 1500));
        if (S.layers.dronePath) {
          ctx.strokeStyle = od.color; ctx.lineWidth = 1.8; ctx.globalAlpha = 0.75;
          ctx.beginPath();
          let pen = false;
          for (let i = 0; i < d.t.length; i += every) { const p = P(d.lon[i], d.lat[i], d.h[i]); if (!p) { pen = false; continue; } if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; } }
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
        const q = A.droneAt(od, t), qp = q && P(q.lon, q.lat, q.h);
        if (qp) {
          ctx.fillStyle = od.color; ctx.strokeStyle = '#0b0e12'; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(qp[0], qp[1], 6, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
          label(ctx, od.short + ' ' + q.h.toFixed(0) + ' m', qp[0] + 10, qp[1] - 10, od.color);
          hitList.push({ x: qp[0], y: qp[1], r: 9, kind: 'fleet', k: S.fleet.indexOf(od) });
        }
      }
    }

    // drone path
    let droneP = null, dronePos = null;
    const dc = S.drone && S.fleet.length > 1 && S.drone.color ? S.drone.color : COL.drone;
    if (S.drone && S.layers.dronePath) {
      const d = S.drone.data, idx = droneIdx();
      const pts = idx.map((i) => P(d.lon[i], d.lat[i], d.h[i]));
      if (mv.mode3d) {
        // ground shadow and drop lines give the height a sense of depth
        ctx.strokeStyle = COL.shadow; ctx.lineWidth = 2;
        ctx.beginPath();
        let pen = false;
        for (const i of idx) { const p = P(d.lon[i], d.lat[i], 0); if (!p) { pen = false; continue; } if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; } }
        ctx.stroke();
        if (S.layers.curtain) {
          ctx.strokeStyle = 'rgba(255,122,26,0.22)'; ctx.lineWidth = 1;
          ctx.beginPath();
          const every = Math.max(1, Math.round(idx.length / 140));
          for (let k = 0; k < idx.length; k += every) { const i = idx[k], a = pts[k], b = P(d.lon[i], d.lat[i], 0); if (a && b && d.h[i] > 1) { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); } }
          ctx.stroke();
        }
      }
      // future part, then flown part
      const split = U.idxLE(idx.map((i) => d.t[i]), t);
      const stroke = (from, to, color, width) => {
        ctx.strokeStyle = color; ctx.lineWidth = width;
        ctx.beginPath();
        let pen = false;
        for (let k = from; k <= to; k++) { const p = pts[k]; if (!p) { pen = false; continue; } if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; } }
        ctx.stroke();
      };
      const mode = pathMode();
      if (mode === 'solid') {
        if (split < idx.length - 1) stroke(Math.max(0, split), idx.length - 1, COL.droneFuture, 1.6);
        if (split >= 0) { stroke(0, split, 'rgba(0,0,0,0.5)', 5); stroke(0, split, dc, 2.6); }
      } else {
        // coloured path: runs of segments with the same colour are stroked together;
        // the part not yet flown is drawn fainter
        const colorAt = mode === 'status' ? (k) => STATUS_COLORS[statusAt(d.t[idx[k]])] : (k) => heightColor(d.h[idx[k]]);
        if (split >= 0) stroke(0, split, 'rgba(0,0,0,0.5)', 5);
        for (const part of [[0, Math.max(0, split), 1, 2.8], [Math.max(0, split), idx.length - 1, 0.38, 2]]) {
          ctx.globalAlpha = part[2];
          let runStart = part[0], runColor = colorAt(part[0]);
          for (let k = part[0] + 1; k <= part[1]; k++) {
            const c = colorAt(k);
            if (c !== runColor || k === part[1]) { stroke(runStart, k, runColor, part[3]); runStart = k; runColor = c; }
          }
        }
        ctx.globalAlpha = 1;
      }
      for (let k = 0; k < pts.length; k += 3) if (pts[k]) hitList.push({ x: pts[k][0], y: pts[k][1], r: 7, kind: 'path', t: d.t[idx[k]] });
      dronePos = A.droneAt(S.drone, t);
      if (dronePos) droneP = P(dronePos.lon, dronePos.lat, dronePos.h);
    } else if (S.drone) {
      dronePos = A.droneAt(S.drone, t);
      if (dronePos) droneP = P(dronePos.lon, dronePos.lat, dronePos.h);
    }

    // home point and pilot
    const home = S.drone ? S.drone.home : S.djiSummary ? S.djiSummary.home : null;
    if (home) {
      const p = P(home.lon, home.lat, 0);
      if (p) {
        ctx.fillStyle = 'rgba(8,10,13,0.8)'; ctx.strokeStyle = COL.drone; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(p[0], p[1], 8, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        ctx.fillStyle = COL.drone; ctx.font = '700 10px ' + getComputedStyle(document.body).fontFamily; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('H', p[0], p[1] + 0.5);
        if (S.djiSummary && !S.drone) label(ctx, 'DJI home point (track encrypted)', p[0] + 12, p[1], COL.drone);
      }
    }
    if (S.drone && S.drone.pilot && S.drone.pilot.length) {
      const k = Math.max(0, U.idxLE(S.drone.pilot.map((x) => x.t), t));
      const pl = S.drone.pilot[k];
      const p = pl && P(pl.lon, pl.lat, 0);
      if (p) {
        ctx.fillStyle = '#e8edf2'; ctx.strokeStyle = 'rgba(8,10,13,0.9)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(p[0], p[1] - 3, 3, 0, 2 * Math.PI); ctx.moveTo(p[0] - 4.5, p[1] + 5); ctx.lineTo(p[0], p[1] - 0.5); ctx.lineTo(p[0] + 4.5, p[1] + 5); ctx.closePath(); ctx.stroke(); ctx.fill();
        if (S.layers.labels && mv.map.getZoom() > 14) label(ctx, 'pilot', p[0] + 8, p[1], 'rgba(232,237,242,0.8)');
      }
    }

    // weather station and wind arrow
    if (S.weather && S.weather.station) {
      const st = S.weather.station, p = P(st.lon, st.lat, 0);
      if (p) {
        ctx.fillStyle = '#7aa7ff'; ctx.strokeStyle = 'rgba(8,10,13,0.9)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(p[0], p[1] - 6); ctx.lineTo(p[0] + 6, p[1]); ctx.lineTo(p[0], p[1] + 6); ctx.lineTo(p[0] - 6, p[1]); ctx.closePath(); ctx.stroke(); ctx.fill();
        const ws = SF.weather.at(S.weather, 'wind', t), wd = SF.weather.at(S.weather, 'dir', t);
        if (U.isNum(wd)) {
          const [la, lo] = U.destination(st.lat, st.lon, wd + 180, 60 + 20 * (U.isNum(ws) ? ws : 3));
          const q = P(lo, la, 0);
          if (q) {
            ctx.strokeStyle = '#7aa7ff'; ctx.lineWidth = 2.2;
            ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
            const a = Math.atan2(q[1] - p[1], q[0] - p[0]);
            ctx.beginPath(); ctx.moveTo(q[0], q[1]); ctx.lineTo(q[0] - 9 * Math.cos(a - 0.45), q[1] - 9 * Math.sin(a - 0.45)); ctx.lineTo(q[0] - 9 * Math.cos(a + 0.45), q[1] - 9 * Math.sin(a + 0.45)); ctx.closePath(); ctx.fillStyle = '#7aa7ff'; ctx.fill();
          }
        }
        label(ctx, (st.name || 'Weather') + (U.isNum(ws) ? ' · ' + ws.toFixed(1) + ' m/s' : ''), p[0] + 10, p[1] + 1, '#a9c4ff');
      }
    }

    // radar plots (drone candidates) of the last 5 s
    if (S.radar && S.layers.plots && S.radar.plots.t.length) {
      const pl = S.radar.plots;
      const i1 = U.idxLE(pl.t, t);
      ctx.fillStyle = 'rgba(255,214,10,0.85)';
      for (let i = i1; i >= 0 && pl.t[i] >= t - 5000; i--) { const p = P(pl.lon[i], pl.lat[i], pl.alt[i] - g0); if (p) ctx.fillRect(p[0] - 2, p[1] - 2, 4, 4); }
    }

    // radar tracks now: trails and markers
    if (S.radar && S.layers.radarLive) {
      const act = activeTracks(t, 4000);
      for (const a of act) {
        const tr = a.tr;
        const isDrone = A.isDroneLabel(tr.cls[a.i], S.aopts.labels), matched = S.matched.has(tr.id), sel = S.selected === tr.id;
        if (S.layers.radarOnlyDrone && !isDrone && !matched && !sel) continue;
        const col = U.classColor(tr.cls[a.i]);
        if (S.layers.radarTrails) {
          ctx.strokeStyle = col; ctx.lineWidth = matched || sel ? 2.2 : 1.4; ctx.globalAlpha = 0.75;
          ctx.beginPath();
          let pen = false;
          for (let i = a.i; i >= 0 && tr.t[i] >= t - 30000; i--) {
            const p = P(tr.lon[i], tr.lat[i], tr.alt[i] - g0);
            if (!p) { pen = false; continue; }
            if (pen) ctx.lineTo(p[0], p[1]); else { ctx.moveTo(p[0], p[1]); pen = true; }
          }
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
        const p = P(tr.lon[a.i], tr.lat[a.i], tr.alt[a.i] - g0);
        if (!p) continue;
        if (mv.mode3d) {
          const gp = P(tr.lon[a.i], tr.lat[a.i], 0);
          if (gp) { ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(gp[0], gp[1]); ctx.stroke(); }
        }
        const r = isDrone ? 6 : 4.5;
        const fade = 1 - Math.min(0.6, (t - tr.t[a.i]) / 6000);
        ctx.globalAlpha = fade;
        if (matched || sel) { ctx.strokeStyle = sel ? '#ffffff' : COL.match; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(p[0], p[1], r + 4, 0, 2 * Math.PI); ctx.stroke(); }
        ctx.fillStyle = col; ctx.strokeStyle = 'rgba(8,10,13,0.9)'; ctx.lineWidth = 1.5;
        ctx.beginPath();
        if (isDrone) { ctx.moveTo(p[0], p[1] - r); ctx.lineTo(p[0] + r, p[1]); ctx.lineTo(p[0], p[1] + r); ctx.lineTo(p[0] - r, p[1]); ctx.closePath(); }
        else ctx.arc(p[0], p[1], r, 0, 2 * Math.PI);
        ctx.fill(); ctx.stroke();
        ctx.globalAlpha = 1;
        if (isDrone || matched || sel) label(ctx, tr.id + ' ' + tr.cls[a.i], p[0] + r + 5, p[1], col);
        hitList.push({ x: p[0], y: p[1], r: r + 5, kind: 'track', id: tr.id, i: a.i });
      }
    }

    // radar error line (drone truth to the matched radar position)
    if (droneP && S.layers.errorLine && S.radar) {
      const h = nearestHit(t);
      if (h) {
        const tr = S.radar.trackMap.get(h.id);
        const q = P(tr.lon[h.i], tr.lat[h.i], tr.alt[h.i] - g0);
        if (q) {
          ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 1.3; ctx.setLineDash([3, 3]);
          ctx.beginPath(); ctx.moveTo(droneP[0], droneP[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); ctx.setLineDash([]);
          if (Math.hypot(q[0] - droneP[0], q[1] - droneP[1]) > 26) label(ctx, h.eH.toFixed(0) + ' m', (droneP[0] + q[0]) / 2 + 6, (droneP[1] + q[1]) / 2, '#ffffff');
        }
      }
    }

    // the drone
    if (droneP && dronePos) {
      if (mv.mode3d) {
        const gp = P(dronePos.lon, dronePos.lat, 0);
        if (gp) {
          ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.moveTo(droneP[0], droneP[1]); ctx.lineTo(gp[0], gp[1]); ctx.stroke();
          ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.beginPath(); ctx.ellipse(gp[0], gp[1], 6, 3, 0, 0, 2 * Math.PI); ctx.fill();
        }
      }
      if (U.isNum(dronePos.yaw)) {
        const [la, lo] = U.destination(dronePos.lat, dronePos.lon, dronePos.yaw, 15);
        const q = P(lo, la, dronePos.h);
        if (q) {
          const a = Math.atan2(q[1] - droneP[1], q[0] - droneP[0]);
          ctx.fillStyle = dc;
          ctx.beginPath(); ctx.moveTo(droneP[0] + Math.cos(a) * 15, droneP[1] + Math.sin(a) * 15); ctx.lineTo(droneP[0] + Math.cos(a + 2.4) * 8, droneP[1] + Math.sin(a + 2.4) * 8); ctx.lineTo(droneP[0] + Math.cos(a - 2.4) * 8, droneP[1] + Math.sin(a - 2.4) * 8); ctx.closePath(); ctx.fill();
        }
      }
      ctx.fillStyle = dc; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2.2;
      ctx.beginPath(); ctx.arc(droneP[0], droneP[1], 7, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
      label(ctx, (S.drone.simulated ? 'Drone (simulated) ' : S.fleet.length > 1 ? S.drone.short + ' ' : 'Drone ') + dronePos.h.toFixed(0) + ' m', droneP[0] - 12, droneP[1] - 12, S.fleet.length > 1 ? dc : '#ffd0a8', 'right');
      hitList.push({ x: droneP[0], y: droneP[1], r: 10, kind: 'drone' });
    }

    // the radar
    if (sensor) {
      const p = P(sensor.lon, sensor.lat, 0);
      if (p) {
        if (U.isNum(sensor.azimuth) && sensor.type !== '3DSearchRadar') {
          const [la, lo] = U.destination(sensor.lat, sensor.lon, sensor.azimuth, 120);
          const q = P(lo, la, 0);
          if (q) { ctx.strokeStyle = 'rgba(255,79,216,0.7)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); }
        }
        ctx.fillStyle = '#ffffff'; ctx.strokeStyle = '#ff4fd8'; ctx.lineWidth = 2.2;
        ctx.beginPath(); ctx.moveTo(p[0], p[1] - 9); ctx.lineTo(p[0] + 8, p[1] + 6); ctx.lineTo(p[0] - 8, p[1] + 6); ctx.closePath(); ctx.fill(); ctx.stroke();
        label(ctx, sensor.name || 'Radar', p[0] + 12, p[1] + 1, '#ffc2f1');
        hitList.push({ x: p[0], y: p[1], r: 10, kind: 'radar' });
      }
    }
  }

  // hover and click on the map
  function bindMapPointer() {
    const tip = $('#tip');
    const near = (pt) => {
      let best = null, bd = Infinity;
      for (const h of hitList) {
        const d = Math.hypot(h.x - pt.x, h.y - pt.y);
        if (d <= h.r && (d < bd || (best && best.kind === 'path' && h.kind !== 'path'))) { bd = d; best = h; }
      }
      return best;
    };
    mv.map.on('mousemove', (e) => {
      const h = near(e.point);
      mv.map.getCanvas().style.cursor = h ? 'pointer' : '';
      if (!h) { tip.hidden = true; return; }
      let html = '';
      if (h.kind === 'track') {
        const tr = S.radar.trackMap.get(h.id), i = h.i, g0 = radarGround();
        html = '<b>Track ' + esc(tr.id) + '</b> · <span style="color:' + U.classColor(tr.cls[i]) + '">' + esc(tr.cls[i]) + '</span><br>' +
          (U.isNum(tr.alt[i]) ? 'height ' + (tr.alt[i] - g0).toFixed(0) + ' m · ' : '') + (U.isNum(tr.spd[i]) ? fmtSpeed(tr.spd[i]) : '') +
          (U.isNum(tr.rcs[i]) ? ' · RCS ' + tr.rcs[i].toFixed(1) + ' dBsm' : '') + (U.isNum(tr.score[i]) ? ' · score ' + tr.score[i].toFixed(2) : '') +
          '<br><span class="muted">' + U.fmtDur((tr.last - tr.first) / 1000, true) + ' long, ' + tr.t.length + ' updates' + (S.matched.has(tr.id) ? ' · on the drone' : '') + '</span>';
      } else if (h.kind === 'path') html = 'Drone at ' + U.fmtClock(h.t, S.tzOffset()) + ' <span class="muted">(click to jump)</span>';
      else if (h.kind === 'drone') { const p = A.droneAt(S.drone, S.t); html = '<b>' + esc(S.fleet.length > 1 ? S.drone.short : 'Drone') + '</b> ' + (p ? p.h.toFixed(1) + ' m · ' + fmtSpeed(p.hs) : ''); }
      else if (h.kind === 'fleet') { const d = S.fleet[h.k], p = A.droneAt(d, S.t); html = '<b style="color:' + d.color + '">' + esc(d.short) + '</b> ' + (p ? p.h.toFixed(1) + ' m · ' + fmtSpeed(p.hs) : '') + '<br><span class="muted">' + esc(d.product || '') + ' · click to select</span>'; }
      else if (h.kind === 'radar') { const s = R.sensor(S.radar); html = '<b>' + esc(s.name) + '</b><br>' + esc(s.type || '') + (U.isNum(s.freqMHz) ? ' · ' + s.freqMHz + ' MHz' : '') + (U.isNum(s.alt) ? '<br>antenna ' + s.alt.toFixed(1) + ' m MSL' : ''); }
      tip.innerHTML = html;
      tip.hidden = false;
      const wrap = $('#mapWrap').getBoundingClientRect();
      let x = e.point.x + 14, y = e.point.y + 14;
      if (x + tip.offsetWidth > wrap.width - 8) x = e.point.x - tip.offsetWidth - 14;
      if (y + tip.offsetHeight > wrap.height - 8) y = e.point.y - tip.offsetHeight - 14;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    mv.map.on('mouseout', () => { tip.hidden = true; });
    mv.map.on('click', (e) => {
      const h = near(e.point);
      if (!h) { if (S.selected) { S.selected = null; mv.draw(); updateNow(true); } return; }
      if (h.kind === 'path') { S.liveFollow = false; setTime(h.t); }
      else if (h.kind === 'track') { S.selected = h.id; mv.draw(); updateNow(true); switchTab('now'); }
      else if (h.kind === 'fleet') selectDrone(h.k);
    });
  }

  // ================================================================== settings dialog
  function openSettings(focus) {
    const s = S.settings, a = S.aopts;
    $('#setDjiKey').value = s.djiApiKey || '';
    $('#setDjiKey').type = 'password';
    $('#btnShowKey').textContent = 'Show';
    $('#setGate').value = a.gate; $('#setTol').value = a.tol; $('#setBreak').value = a.breakGap; $('#setBins').value = a.rangeStep;
    $('#setLabels').value = a.labels.join(', ');
    $('#setTz').value = s.tz || 'utc';
    $('#setSpeedUnit').value = s.speedUnit || 'ms';
    $('#setIntro').checked = s.intro !== false;
    $('#btnPrefetch').disabled = !SF.native.ok;
    $('#btnClearCache').disabled = !SF.native.ok;
    const L = S.license;
    $('#aboutLicense').innerHTML = L ? '<span>Licensed to</span><b>' + esc(SF.license.who(L)) + '</b>' + (L.e ? '<span>Email</span><span>' + esc(L.e) + '</span>' : '') +
      '<span>Valid until</span><span>' + (L.x ? esc(L.x) : 'No end date') + '</span><span>Computer</span><span>' + (L.m ? 'This computer only' : 'Any computer') + '</span><span>License ID</span><span>' + esc(L.id) + '</span>' +
      '<div class="act"><button type="button" class="ghost small" id="btnLicChange">Change License…</button></div>' : '';
    $('#aboutInfo').textContent = 'Orbit ' + (SF.native.info ? SF.native.info.version + ' · Data folder: ' + SF.native.info.dataDir : '(without the helper: maps are not saved offline, and DJI unlocking and the live feed are off)');
    refreshCacheStats();
    selectPane(focus || 'general');
    $('#dlgSettings').showModal();
    if (focus === 'dji') setTimeout(() => $('#setDjiKey').focus(), 50);
  }
  function selectPane(id) {
    $$('#dlgSettings [data-ptab]').forEach((b) => b.classList.toggle('on', b.dataset.ptab === id));
    $$('#dlgSettings [data-pane]').forEach((p) => p.classList.toggle('on', p.dataset.pane === id));
  }
  async function refreshCacheStats() {
    if (!SF.native.ok) { $('#cacheStats').textContent = 'Not available'; return; }
    try {
      const st = await SF.native.get('/api/tiles/stats');
      $('#cacheStats').textContent = st.count.toLocaleString('en-US') + ' tiles, ' + (st.bytes / 1048576).toFixed(1) + ' MB';
    } catch (e) { /* ignore */ }
  }
  async function saveSettings() {
    const prevKey = S.settings.djiApiKey || '';
    const labels = $('#setLabels').value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean);
    const aopts = {
      gate: U.clamp(U.num($('#setGate').value) || 75, 5, 1000), tol: U.clamp(U.num($('#setTol').value) || 3, 0.5, 30),
      breakGap: U.clamp(U.num($('#setBreak').value) || 5, 1, 120), rangeStep: U.clamp(U.num($('#setBins').value) || 250, 50, 2000), labels: labels.length ? labels : A.defaults().labels,
    };
    const key = $('#setDjiKey').value.trim();
    const patch = { djiApiKey: key || null, aopts, tz: $('#setTz').value, speedUnit: $('#setSpeedUnit').value, intro: $('#setIntro').checked };
    S.settings = Object.assign(S.settings, patch);
    if (!key) delete S.settings.djiApiKey;
    S.aopts = Object.assign(A.defaults(), aopts);
    await SF.native.saveSettings(patch);
    onDataChanged({});
    if (key && key !== prevKey && S.djiSummary && S.djiLog) { banner(null); await unlockNow(); }
    else if (S.djiSummary) showLockedBanner();
  }
  let prefetchTimer = 0;
  async function startPrefetch() {
    const zmax = +$('#setPrefetchZoom').value;
    const zmin = Math.max(8, Math.min(zmax - 1, Math.floor(mv.map.getZoom()) - 3));
    const sources = (SF.BASEMAPS[S.settings.basemap || 'satellite'] || ['esri-imagery']).slice();
    if (!sources.length) sources.push('esri-imagery');
    if ($('#setPrefetchLabels').checked && sources.indexOf('esri-labels') < 0) sources.push('esri-labels');
    const r = await SF.native.post('/api/tiles/prefetch', { sources, bbox: mv.visibleBounds(), minZoom: zmin, maxZoom: zmax });
    if (!r.ok) { $('#prefetchStatus').textContent = r.error || 'Could not start.'; return; }
    $('#btnPrefetchCancel').hidden = false;
    clearInterval(prefetchTimer);
    prefetchTimer = setInterval(async () => {
      const st = await SF.native.get('/api/tiles/prefetch');
      $('#prefetchStatus').innerHTML = (st.running ? 'Saving map tiles: ' : 'Done: ') + st.done.toLocaleString('en-US') + ' of ' + st.total.toLocaleString('en-US') + (st.failed ? ', ' + st.failed + ' failed' : '') + ', ' + (st.bytes / 1048576).toFixed(1) + ' MB new' + '<div class="progress"><i style="width:' + (st.total ? Math.round((100 * st.done) / st.total) : 0) + '%"></i></div>';
      if (!st.running) { clearInterval(prefetchTimer); $('#btnPrefetchCancel').hidden = true; refreshCacheStats(); }
    }, 700);
  }

  // ================================================================== live radar feed
  function liveNow() {
    if (!S.radar || !isFinite(S.radar.t1)) return S.t;
    return S.radar.t1;
  }
  function openLive() {
    const d = $('#dlgLive');
    const m = S.settings.live || {};
    $('#liveMode').value = S.live ? S.live.mode : m.mode || 'tcp-client';
    $('#liveHost').value = m.host || ''; $('#livePort').value = m.port || ''; $('#livePath').value = m.path || ''; $('#liveFromStart').checked = !!m.fromStart;
    syncLiveForm();
    liveStatusLine();
    d.showModal();
  }
  function syncLiveForm() {
    const mode = $('#liveMode').value;
    $('#liveNet').hidden = mode === 'file' || mode === 'demo';
    $$('#liveNet .hostrow').forEach((e) => (e.hidden = mode !== 'tcp-client'));
    $('#liveFileWrap').hidden = mode !== 'file';
    $('#liveDemoWrap').hidden = mode !== 'demo';
    $('#btnLiveStart').disabled = mode === 'demo' ? !S.radarRaw : !SF.native.ok;
    if (mode === 'demo' && !S.radarRaw) $('#liveStatusLine').textContent = 'Import a Robin radar file first to replay it as live.';
    else if (mode !== 'demo' && !SF.native.ok) $('#liveStatusLine').textContent = 'The live feed needs the Orbit app, not a plain browser.';
    else liveStatusLine();
  }
  function liveStatusLine(st) {
    const el = $('#liveStatusLine');
    if (!S.live) { el.textContent = ''; return; }
    st = st || S.live.status || {};
    el.innerHTML = '<b>' + esc(st.state || 'running') + '</b> · ' + esc(st.message || '') + ' · ' + (S.live.messages || 0) + ' messages' + (st.record ? '<br><span class="muted">Recording to ' + esc(st.record) + '</span>' : '');
  }
  // the Live button and the status bar while a feed runs
  function liveUi() {
    const L = S.live;
    $('#liveLabel').textContent = L ? 'Live' : 'Live Radar';
    $('#sbLive').hidden = !L;
    if (L) $('#sbLive').textContent = 'Live feed ' + ((L.status && L.status.state) || 'running') + ' · ' + L.messages.toLocaleString('en-US') + ' messages';
  }
  async function startLive() {
    const mode = $('#liveMode').value;
    const cfg = { mode, host: $('#liveHost').value.trim(), port: +$('#livePort').value || 0, path: $('#livePath').value.trim(), fromStart: $('#liveFromStart').checked };
    if ((mode === 'tcp-client' && (!cfg.host || !cfg.port)) || ((mode === 'tcp-server' || mode === 'udp') && !cfg.port) || (mode === 'file' && !cfg.path)) { $('#liveStatusLine').textContent = 'Fill in the address, port or file first.'; return; }
    if (S.live) await stopLive(true);
    S.settings.live = { mode, host: cfg.host, port: cfg.port, path: cfg.path, fromStart: cfg.fromStart };
    SF.native.saveSettings({ live: S.settings.live });
    const name = mode === 'demo' ? 'Live replay of ' + (S.files.radar || 'radar file') : mode === 'file' ? 'Live: ' + basename(cfg.path) : 'Live: ' + (cfg.host || 'port') + ':' + cfg.port;
    const store = R.newStore('robin', name);
    store.live = true;
    const raw = S.radarRaw;
    S.radar = store; S.files.radar = name; S.offsets.radar = 0; S.radarRaw = mode === 'demo' ? raw : null;
    S.live = { mode, cfg, seq: 0, messages: 0, store, generic: null, status: { state: 'starting' }, started: Date.now() };
    S.liveFollow = true;
    $('#btnLive').classList.add('on');
    liveUi();
    if (mode === 'demo') startDemoFeed(raw);
    else {
      const r = await SF.native.post('/api/live/start', cfg);
      if (!r.ok) { toast('Live feed: ' + esc(r.error || 'could not start'), 'err'); await stopLive(true); return; }
      S.live.timer = setInterval(pollLive, 500);
    }
    $('#dlgLive').close();
    onDataChanged({});
    toast('Live feed started: ' + esc(name), 'ok');
  }
  function ingestLive(xml) {
    const L = S.live, store = L.store;
    if (/^<Robin[\s>]/.test(xml)) R.ingestRobinText(store, xml);
    else {
      if (!L.generic) {
        L.pending = (L.pending || []).concat(xml);
        if (L.pending.length >= 5) {
          const cands = R.analyzeXML(SF.xml.parse(L.pending.join('\n')));
          if (cands.length) { L.generic = { tag: cands[0].tag, mapping: cands[0].mapping, opts: {} }; for (const x of L.pending) R.ingestGeneric(store, x, L.generic); }
          L.pending = [];
        }
      } else R.ingestGeneric(store, xml, L.generic);
    }
    L.messages++;
  }
  let liveRedraw = 0;
  async function pollLive() {
    const L = S.live;
    if (!L || L.polling) return;
    L.polling = true;
    try {
      const r = await SF.native.get('/api/live/poll?since=' + L.seq);
      if (!S.live) return;
      L.status = r.status || {};
      for (const it of r.items || []) { ingestLive(it.xml); L.seq = it.seq; }
      if ((r.items || []).length) liveDataChanged();
      liveUi();
      if ($('#dlgLive').open) liveStatusLine(L.status);
    } catch (e) { /* helper hiccup */ } finally { if (L) L.polling = false; }
  }
  function liveDataChanged() {
    R.finalize(S.live.store);
    const now = performance.now();
    if (now - liveRedraw < 900) { mv.draw(); return; }
    liveRedraw = now;
    S.range = timeRange();
    if (isFinite(S.range.t0)) {
      tl.setRange(S.range.t0, S.range.t1, true);
      if (S.liveFollow) tl.setView(Math.max(S.range.t0, S.range.t1 - 10 * 60e3), S.range.t1 + 5000);
    }
    runAnalysis();
    buildLanes();
    $('#empty').hidden = true;
    renderFiles();
    updateImportDots();
    if (!S.live.fitted && S.live.store.tracks.length) { S.live.fitted = true; fitAll(true); }
    mv.draw();
  }
  function startDemoFeed(raw) {
    const msgs = [];
    let pos = 0;
    for (;;) {
      const s = raw.indexOf('<Robin', pos);
      if (s < 0) break;
      const e = raw.indexOf('</Robin>', s);
      if (e < 0) break;
      const xml = raw.slice(s, e + 8);
      const m = /timestamp="([^"]+)"|Timestamp="([^"]+)"|<Timestamp>([^<]+)</.exec(xml);
      msgs.push({ xml, t: m ? U.parseTime(m[1] || m[2] || m[3]) : NaN });
      pos = e + 8;
    }
    let t0 = msgs.find((m) => U.isNum(m.t) && m.t > 1e12);
    t0 = t0 ? t0.t : 0;
    const wall0 = performance.now();
    let k = 0;
    S.live.timer = setInterval(() => {
      if (!S.live) return;
      const speed = Math.max(1, S.speed);
      const simT = t0 + (performance.now() - wall0) * speed;
      let n = 0;
      while (k < msgs.length && (!U.isNum(msgs[k].t) || msgs[k].t <= simT || msgs[k].t < 1e12)) { ingestLive(msgs[k].xml); k++; n++; }
      S.live.status = { state: k < msgs.length ? 'replaying' : 'finished', message: k + ' / ' + msgs.length + ' messages' };
      liveUi();
      if (n) liveDataChanged();
      if (k >= msgs.length) { clearInterval(S.live.timer); S.live.timer = 0; }
    }, 250);
  }
  async function stopLive(quiet) {
    const L = S.live;
    if (!L) return;
    clearInterval(L.timer);
    S.live = null;
    S.liveFollow = false;
    let rec = L.status && L.status.record;
    if (L.mode !== 'demo' && SF.native.ok) { try { await SF.native.post('/api/live/stop', {}); } catch (e) { /* ignore */ } }
    $('#btnLive').classList.remove('on');
    liveUi();
    if (S.radar === L.store) { R.finalize(L.store); keepRaw('radar'); }
    if (!quiet) {
      onDataChanged({});
      toast('Live feed stopped. ' + L.messages + ' messages kept for analysis.' + (rec ? '<br><small>Recorded to ' + esc(rec) + '</small>' : ''), 'ok', 9000);
    }
  }

  // ================================================================== export
  // ---------------------------------------------------------------- license
  // Orbit opens only with a valid personal license, checked offline; until then its window stays open.
  let licenseDone = null;
  async function ensureLicense() {
    S.machine = await SF.license.machineCode(SF.native.info && SF.native.info.machine);
    const r = S.settings.license ? await SF.license.check(S.settings.license, S.machine) : null;
    if (r && r.ok) {
      S.license = r.lic;
      const left = SF.license.daysLeft(r.lic);
      if (left <= 30) toast('Your Orbit license ends on ' + esc(r.lic.x) + ' (in ' + left + ' days). Ask for a new one in time.', 'err', 12000);
      return;
    }
    S.license = null;
    await new Promise((resolve) => openLicense(r ? r.reason : '', resolve));
  }
  function openLicense(reason, done) {
    if (done) licenseDone = done;
    $('#licMachine').textContent = S.machine || 'Unknown (the Orbit helper is not running)';
    $('#licCode').value = '';
    $('#licMsg').textContent = reason || '';
    $('#licMsg').className = reason ? 'err' : '';
    $('#licCurrent').textContent = S.license ? 'Currently licensed to ' + SF.license.who(S.license) + (S.license.x ? ', valid until ' + S.license.x : '') + '.' : '';
    $('#btnLicCancel').hidden = !S.license;
    if (!$('#dlgLicense').open) $('#dlgLicense').showModal();
  }
  async function applyLicense(text) {
    const r = await SF.license.check(text, S.machine);
    if (!r.ok) { openLicense(r.reason); return false; }
    S.license = r.lic;
    S.settings.license = r.code;
    SF.native.saveSettings({ license: r.code });
    if ($('#dlgLicense').open) $('#dlgLicense').close();
    renderChrome();
    toast('Orbit is licensed to <b>' + esc(SF.license.who(r.lic)) + '</b>' + (r.lic.x ? ', valid until ' + esc(r.lic.x) : '') + '.', 'ok');
    if (licenseDone) { const f = licenseDone; licenseDone = null; f(); }
    return true;
  }

  // ---------------------------------------------------------------- test details
  function openDetails() {
    const m = S.meta || {};
    $('#mdTitle').value = m.title || '';
    $('#mdTitle').placeholder = S.title();
    $('#mdSite').value = m.site || ''; $('#mdOrg').value = m.org || ''; $('#mdAuthor').value = m.author || ''; $('#mdNotes').value = m.notes || '';
    $('#dlgDetails').showModal();
  }
  function saveDetails() {
    S.meta = { title: $('#mdTitle').value.trim(), site: $('#mdSite').value.trim(), org: $('#mdOrg').value.trim(), author: $('#mdAuthor').value.trim(), notes: $('#mdNotes').value.trim() };
    SF.native.saveSettings({ site: S.meta.site, org: S.meta.org, author: S.meta.author });
    S.settings.site = S.meta.site; S.settings.org = S.meta.org; S.settings.author = S.meta.author;
    toast('Test details saved.', 'ok');
  }

  // ---------------------------------------------------------------- report
  // A clean whole-flight 2D map for the report: fitted to the data, path coloured by radar
  // status, playhead at landing. The user's view is put back afterwards.
  async function reportMapImage() {
    const m = mv.map;
    const saved = { center: m.getCenter(), zoom: m.getZoom(), pitch: m.getPitch(), bearing: m.getBearing(), mode3d: mv.mode3d, t: S.t, pathColor: S.layers.pathColor, follow: S.follow, selected: S.selected };
    try {
      mv.mode3d = false; S.follow = false; S.selected = null;
      if (S.analysis && S.analysis.series) S.layers.pathColor = 'status';
      if (S.analysis && S.analysis.window) S.t = S.analysis.window.t1;
      const b = dataBounds();
      if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 46, duration: 0, pitch: 0, bearing: 0, maxZoom: 17.5 });
      await new Promise((r) => { let fin = false; const go = () => { if (!fin) { fin = true; r(); } }; m.once('idle', go); m.triggerRepaint(); setTimeout(go, 9000); });
      // draw the new view right now, so the snapshot never catches an older frame
      if (m.redraw) m.redraw();
      mv.draw();
      return mv.snapshot().toDataURL('image/jpeg', 0.9);
    } catch (e) { console.error(e); return null; }
    finally {
      mv.mode3d = saved.mode3d; S.follow = saved.follow; S.selected = saved.selected; S.layers.pathColor = saved.pathColor; S.t = saved.t;
      m.jumpTo({ center: saved.center, zoom: saved.zoom, pitch: saved.pitch, bearing: saved.bearing });
      mv.draw();
    }
  }
  async function buildReport() {
    busy('Preparing the report…');
    await U.tick();
    const map = await reportMapImage();
    busy('Laying out the pages…');
    return SF.report.build(S, { map });
  }
  function stampName() {
    const base = (S.meta && S.meta.title) || S.title();
    return 'Orbit report ' + base.replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  async function exportPdf(path) {
    const rep = await buildReport();
    busy('Making the PDF…');
    const res = await SF.native.pdf(stampName() + '.pdf', rep.printHtml, rep.pages, path);
    busy(null);
    if (res.ok) {
      toast('Report saved (' + rep.pages + ' pages): ' + esc(res.path) + '<br><button class="link" data-openfile="' + esc(res.path) + '">Open</button> · <button class="link" data-reveal="' + esc(res.path) + '">' + (isMac ? 'Show in Finder' : 'Show in Folder') + '</button>', 'ok', 12000);
      return res;
    }
    if (res.cancelled) return res;
    toast('A PDF could not be made here (' + esc(res.error || 'no helper') + '). The report is saved as a web page instead; open it and print it to PDF.', 'err', 12000);
    await SF.native.save(stampName() + '.html', rep.html, 'text/html');
    return res;
  }
  async function doExport(kind) {
    closeMenus();
    try {
      const stamp = (S.drone ? U.fmtDate(S.drone.data.t[0]) : U.fmtDate(Date.now())) + '_' + (U.isNum(S.t) ? U.fmtClock(S.t).replace(/:/g, '') : '');
      let res;
      if (kind === 'details') return openDetails();
      if (!hasData()) return toast('Import some data first.', 'err');
      if (kind === 'pdf') return await exportPdf();
      if (kind === 'report') {
        const rep = await buildReport();
        busy(null);
        res = await SF.native.save(stampName() + '.html', rep.html, 'text/html');
      } else if (kind === 'csv') res = await SF.native.save('Orbit_timeline_' + stamp + '.csv', SF.exporters.csv(S), 'text/csv');
      else if (kind === 'kml') res = await SF.native.save('Orbit_tracks_' + stamp + '.kml', SF.exporters.kml(S), 'application/vnd.google-earth.kml+xml');
      else if (kind === 'png') {
        const blob = await new Promise((r) => mv.snapshot().toBlob(r, 'image/png'));
        res = await SF.native.save('Orbit_map_' + stamp + '.png', blob, 'image/png');
      } else if (kind === 'session') res = await SF.native.save('Orbit_' + stamp + '.orbit.json', SF.exporters.session(S), 'application/json');
      if (res && res.ok) toast(res.path ? 'Saved to ' + esc(res.path) + ' <button class="link" data-reveal="' + esc(res.path) + '">' + (isMac ? 'Show in Finder' : 'Show in Folder') + '</button>' : 'Downloaded.', 'ok', 7000);
    } catch (e) { toast('Export failed: ' + esc(e.message), 'err'); console.error(e); } finally { busy(null); }
  }

  // ================================================================== UI wiring
  function switchTab(id) {
    $$('#side [data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === id));
    $$('.tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + id));
  }
  function showInspector(on) {
    $('#main').classList.toggle('noside', !on);
    $('#btnInspector').classList.toggle('on', on);
  }
  // toolbar and transport pop-up menus: one open at a time
  const MENUS = { btnImport: 'menuImport', btnLayers: 'menuLayers', btnExport: 'menuExport', btnLanes: 'laneToggles' };
  function closeMenus(except) {
    for (const [b, m] of Object.entries(MENUS)) if (m !== except) { $('#' + m).hidden = true; $('#' + b).classList.remove('open'); }
  }
  function toggleMenu(btn) {
    const m = MENUS[btn], open = $('#' + m).hidden;
    closeMenus(m);
    $('#' + m).hidden = !open;
    $('#' + btn).classList.toggle('open', open);
  }
  // Mac app: the toolbar is the title bar, so the window moves and zooms from it
  function toWindow(type) { try { window.webkit.messageHandlers.orbit.postMessage({ type }); } catch (e) { /* not in the Mac app */ } }
  // from the Mac app: title bar height, where the window buttons end, full screen
  function setChrome(c) {
    const r = document.documentElement;
    if (c.titlebar) r.style.setProperty('--tbh', c.titlebar + 'px');
    if (c.inset) r.style.setProperty('--tbl', c.inset + 'px');
    r.classList.toggle('fullscreen', !!c.fullscreen);
  }
  let pendingHint = null;
  async function pick(hint) {
    if (SF.native.ok && SF.native.info && SF.native.info.helper === 'swift') {
      const titles = { drone: 'Choose a drone flight log', radar: 'Choose a radar track file', weather: 'Choose a weather file' };
      const r = await SF.native.post('/api/open-dialog', { title: titles[hint] || 'Open', multiple: true });
      if (r && r.ok) { for (const p of r.paths || []) await importPath(p, hint); return; }
    }
    const input = $('#fileInput');
    pendingHint = hint;
    input.accept = hint === 'drone' ? '.txt,.bin,.tlog,.csv,.gpx,.kml,.kmz,.json' : hint === 'radar' ? '.xml,.csv,.txt,.json' : hint === 'weather' ? '.csv,.txt,.xlsx,.tsv,.dat' : '';
    input.value = '';
    input.click();
  }
  function bindUI() {
    const input = $('#fileInput');
    document.addEventListener('click', (e) => {
      const imp = e.target.closest('.imp');
      if (imp) { closeMenus(); return pick(imp.dataset.kind); }
      const rm = e.target.closest('[data-remove]');
      if (rm) return removeData(rm.dataset.remove);
      const off = e.target.closest('[data-off]');
      if (off) { applyOffset(off.dataset.off, S.offsets[off.dataset.off] + +off.dataset.d); return onDataChanged({}); }
      const sk = e.target.closest('[data-seek]');
      if (sk) { S.liveFollow = false; if (sk.dataset.track) S.selected = sk.dataset.track; setTime(+sk.dataset.seek); return; }
      const ev = e.target.closest('.ev[data-k]');
      if (ev && evCache) { S.liveFollow = false; setTime(evCache[+ev.dataset.k].t); return; }
      if (e.target.closest('[data-unlock]')) return unlockNow();
      const st = e.target.closest('[data-settings]');
      if (st) return openSettings(st.dataset.settings);
      const rv = e.target.closest('[data-reveal]');
      if (rv) return SF.native.reveal(rv.dataset.reveal);
      const of = e.target.closest('[data-openfile]');
      if (of) return SF.native.openFile(of.dataset.openfile);
      const ex = e.target.closest('[data-export]');
      if (ex) return doExport(ex.dataset.export);
      if (e.target.closest('#btnTestDetails')) return openDetails();
      if (e.target.closest('#btnCwaCtx')) return openCwa();
      if (e.target.closest('#btnLicChange')) { $('#dlgSettings').close(); return openLicense(''); }
      const dd = e.target.closest('[data-dropdrone]');
      if (dd) return dropDrone(+dd.dataset.dropdrone);
      const dr = e.target.closest('[data-drone]');
      if (dr) return selectDrone(+dr.dataset.drone);
      const ln = e.target.closest('[data-lane]');
      if (ln) { S.lanes[ln.dataset.lane] = !ln.classList.contains('on'); buildLanes(); return; }
      if (e.target.closest('#btnSync')) return autoSync();
      if (e.target.closest('#btnSetRadarPos')) {
        const lat = U.num($('#setRLat').value), lon = U.num($('#setRLon').value), alt = U.num($('#setRAlt').value);
        if (!U.isNum(lat) || !U.isNum(lon)) return toast('Enter the radar latitude and longitude.', 'err');
        R.setOrigin(S.radar, { lat, lon, alt: U.isNum(alt) ? alt : 0, ground: 0 }, S.radar.name);
        return onDataChanged({});
      }
      if (!e.target.closest('.menuwrap')) closeMenus();
    });
    $('#top').addEventListener('mousedown', (e) => { if (isMac && e.button === 0 && e.detail === 1 && !e.target.closest('button, select, input, a, .menu')) toWindow('drag'); });
    $('#top').addEventListener('dblclick', (e) => { if (isMac && !e.target.closest('button, select, input, a, .menu')) toWindow('zoom'); });
    document.addEventListener('change', (e) => {
      const oi = e.target.closest('[data-offin]');
      if (oi) { applyOffset(oi.dataset.offin, Math.round(U.num(oi.value) * 1000) || 0); onDataChanged({}); }
    });
    input.addEventListener('change', () => { if (input.files.length) importFiles([...input.files], pendingHint); });
    // drag and drop
    let dragDepth = 0;
    window.addEventListener('dragenter', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { dragDepth++; $('#drop').hidden = false; e.preventDefault(); } });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('#drop').hidden = true; });
    window.addEventListener('drop', (e) => { e.preventDefault(); dragDepth = 0; $('#drop').hidden = true; if (e.dataTransfer.files.length) importFiles([...e.dataTransfer.files], null); });

    $('#btnDemo').onclick = loadDemo;
    $('#btnHelp').onclick = showHelp;
    $('#btnTlFlight').onclick = () => {
      const w = S.analysis && S.analysis.window;
      const a = w ? w.t0 : S.djiSummary ? S.djiSummary.start : S.range.t0, b = w ? w.t1 : S.djiSummary ? S.djiSummary.end : S.range.t1;
      if (U.isNum(a) && U.isNum(b)) tl.setView(a - 20000, b + 20000);
    };
    $('#btnPlay').onclick = () => { if (S.live && S.liveFollow) { S.liveFollow = false; return setPlaying(false); } setPlaying(!S.playing); };
    $('#btnStart').onclick = () => { S.liveFollow = false; setTime(S.analysis && S.analysis.window ? S.analysis.window.t0 - 5000 : S.range.t0); };
    $('#btnBack10').onclick = () => { S.liveFollow = false; setTime(S.t - 10000); };
    $('#btnFwd10').onclick = () => setTime(S.t + 10000);
    $('#speed').onchange = (e) => { S.speed = +e.target.value; };
    $$('#side [data-tab]').forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
    $$('[data-view]').forEach((b) => (b.onclick = () => set3D(b.dataset.view === '3d')));
    $('#basemap').onchange = (e) => { mv.setBasemap(e.target.value); S.settings.basemap = e.target.value; SF.native.saveSettings({ basemap: e.target.value }); };
    $('#exag').onchange = (e) => { mv.exag = +e.target.value; mv.draw(); };
    $('#btnLayers').onclick = () => toggleMenu('btnLayers');
    $('#btnImport').onclick = () => toggleMenu('btnImport');
    $('#btnLanes').onclick = () => toggleMenu('btnLanes');
    $('#btnInspector').onclick = () => { const on = $('#main').classList.contains('noside'); showInspector(on); S.settings.inspector = on; SF.native.saveSettings({ inspector: on }); };
    $$('#dlgSettings [data-ptab]').forEach((b) => (b.onclick = () => selectPane(b.dataset.ptab)));
    $('#btnLive2').onclick = openLive;
    $('#btnIntroNow').onclick = () => { $('#dlgSettings').close(); playIntro(); };
    $('#pathColor').value = S.layers.pathColor || 'status';
    $('#pathColor').onchange = (e) => {
      S.layers.pathColor = e.target.value;
      const keep = Object.assign({}, S.layers); delete keep.terrain;
      SF.native.saveSettings({ layers: keep });
      renderLegend();
      mv.draw();
    };
    $$('#menuLayers [data-layer]').forEach((c) => {
      c.checked = !!S.layers[c.dataset.layer];
      c.onchange = () => {
        S.layers[c.dataset.layer] = c.checked;
        if (c.dataset.layer === 'terrain') mv.setTerrain(c.checked);
        const keep = Object.assign({}, S.layers); delete keep.terrain;
        SF.native.saveSettings({ layers: keep });
        mv.draw();
      };
    });
    $('#btnFollow').onclick = () => { S.follow = !S.follow; $('#btnFollow').classList.toggle('on', S.follow); if (S.follow) setTime(S.t); };
    $('#btnFit').onclick = () => fitAll(true);
    $('#btnExport').onclick = () => toggleMenu('btnExport');
    $('#btnDetailsSave').onclick = () => saveDetails();
    $('#btnCwaSave').onclick = () => saveCwa();
    $('#btnLicOk').onclick = () => applyLicense($('#licCode').value);
    $('#btnLicCancel').onclick = () => { if (S.license) $('#dlgLicense').close(); };
    $('#btnLicFile').onclick = () => { $('#licFile').value = ''; $('#licFile').click(); };
    $('#licFile').onchange = async () => { const f = $('#licFile').files[0]; if (f) applyLicense(await f.text()); };
    $('#btnLicCopy').onclick = () => { if (navigator.clipboard) navigator.clipboard.writeText(S.machine || '').then(() => toast('Computer ID copied.', 'ok')); };
    // without a license the window cannot be closed: Esc puts it back
    $('#dlgLicense').addEventListener('cancel', (e) => { if (!S.license) e.preventDefault(); });
    $('#dlgLicense').addEventListener('close', () => { if (!S.license) setTimeout(() => $('#dlgLicense').showModal(), 0); });
    $('#btnCwaReset').onclick = () => fillCwa(SF.cwa.defaults());
    $('#btnSettings').onclick = () => openSettings();
    $('#btnShowKey').onclick = () => { const i = $('#setDjiKey'); i.type = i.type === 'password' ? 'text' : 'password'; $('#btnShowKey').textContent = i.type === 'password' ? 'Show' : 'Hide'; };
    $('#btnSaveSettings').onclick = (e) => { e.preventDefault(); saveSettings(); $('#dlgSettings').close(); };
    $('#btnPrefetch').onclick = startPrefetch;
    $('#btnPrefetchCancel').onclick = () => SF.native.post('/api/tiles/prefetch/cancel', {});
    $('#btnClearCache').onclick = async () => { if (!confirm('Delete all saved map tiles? Maps of places you have not viewed will not work offline until you view them online again.')) return; await SF.native.post('/api/tiles/clear', {}); refreshCacheStats(); };
    $('#btnLive').onclick = openLive;
    $('#liveMode').onchange = syncLiveForm;
    $('#btnLiveStart').onclick = startLive;
    $('#btnLiveStop').onclick = () => { stopLive(); $('#dlgLive').close(); };
    $('#btnLiveBrowse').onclick = async () => {
      const r = await SF.native.post('/api/open-dialog', { title: 'File the radar software writes', filters: [['XML', '*.xml'], ['All files', '*.*']] });
      if (r.ok && r.paths && r.paths[0]) $('#livePath').value = r.paths[0];
    };
    window.addEventListener('keydown', (e) => {
      // Windows: Ctrl shortcuts (the Mac app's menu bar has its own ⌘ shortcuts)
      if (!isMac && (e.ctrlKey || e.metaKey) && !e.altKey && !document.querySelector('dialog[open]')) {
        const f = { o: () => pick(null), e: () => doExport('pdf'), i: () => openDetails(), ',': () => openSettings() }[e.key.toLowerCase()];
        if (f) { e.preventDefault(); return f(); }
      }
      if (e.target.closest('input, select, textarea, dialog')) return;
      const k = e.key;
      if (k === ' ') { e.preventDefault(); $('#btnPlay').click(); }
      else if (k === 'ArrowRight') { e.preventDefault(); setTime(S.t + (e.shiftKey ? 10000 : 1000)); }
      else if (k === 'ArrowLeft') { e.preventDefault(); S.liveFollow = false; setTime(S.t - (e.shiftKey ? 10000 : 1000)); }
      else if (k === 'Home') { e.preventDefault(); $('#btnStart').click(); }
      else if (k === 'End') { e.preventDefault(); if (S.live) S.liveFollow = true; else setTime(S.range.t1); }
      else if (k === 'f' || k === 'F') $('#btnFollow').click();
      else if (k === '2') set3D(false);
      else if (k === '3') set3D(true);
      else if (k === '+' || k === '=') stepSpeed(1);
      else if (k === '-') stepSpeed(-1);
      else if (k === 'Escape') closeMenus();
      else if (k === '?') showHelp();
    });
  }
  function stepSpeed(d) {
    const sel = $('#speed');
    sel.selectedIndex = U.clamp(sel.selectedIndex + d, 0, sel.options.length - 1);
    S.speed = +sel.value;
  }
  function set3D(on) {
    $$('[data-view]').forEach((b) => b.classList.toggle('on', (b.dataset.view === '3d') === on));
    mv.set3D(on);
    if (!on && S.layers.terrain) { S.layers.terrain = false; mv.setTerrain(false); $('#menuLayers [data-layer="terrain"]').checked = false; }
  }

  SF.app = { importBytes, importPath, loadDemo, setTime, doExport, exportPdf, openDetails, showHelp, pick, openSettings, openLive, switchTab, set3D, setChrome, playIntro,
    toggleInspector: () => $('#btnInspector').click(), state: S, get map() { return mv; }, get timeline() { return tl; } };
  window.addEventListener('DOMContentLoaded', init);
})();
