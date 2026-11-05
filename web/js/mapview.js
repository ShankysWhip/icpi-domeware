/* Orbit: the map. MapLibre draws the base map (2D, or tilted 3D with optional terrain).
   All flight data is drawn on a canvas on top, projected with MapLibre's own camera matrix,
   so tracks sit at their real height in 3D and stay locked to the map while it moves. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;
  const D2R = Math.PI / 180, CIRC = 2 * Math.PI * 6371008.8;

  const ATTR = {
    'esri-imagery': 'Imagery © Esri, Maxar, Earthstar Geographics',
    'esri-labels': 'Labels © Esri',
    'esri-topo': '© Esri',
    osm: '© OpenStreetMap contributors',
    opentopo: '© OpenTopoMap (CC-BY-SA), © OpenStreetMap contributors',
  };
  const MAXZ = { 'esri-imagery': 19, 'esri-labels': 19, 'esri-topo': 19, osm: 19, opentopo: 17, terrain: 15 };
  const BASEMAPS = {
    satellite: ['esri-imagery'],
    hybrid: ['esri-imagery', 'esri-labels'],
    streets: ['osm'],
    topo: ['opentopo'],
    none: [],
  };
  SF.BASEMAPS = BASEMAPS;

  function MapView(el, canvas, opts) {
    this.el = el;
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts || {};
    this.mode3d = false;
    this.exag = 3;
    this.matrix = null;
    this.baseLayers = [];
    this.w = el.clientWidth;
    this.h = el.clientHeight;
    this.ready = false;
    this.groundZ = 0;
    const map = (this.map = new maplibregl.Map({
      container: el,
      style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#101113' } }] },
      center: [25.5, 64.9], zoom: 4, maxPitch: 80, attributionControl: false,
      // keep the last frame so map images can be exported
      canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true },
      fadeDuration: 0, dragRotate: true, pitchWithRotate: true, maxTileCacheSize: 600,
    }));
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 120 }), 'bottom-right');
    map.on('load', () => {
      if (map.setSky) { try { map.setSky({ 'sky-color': '#14171b', 'horizon-color': '#2a2e33', 'fog-color': '#1d2024', 'sky-horizon-blend': 0.6, 'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.85 }); } catch (e) { /* older style spec */ } }
      map.addLayer(this.overlayLayer());
      this.setBasemap(this.opts.basemap || 'satellite');
      this.ready = true;
      if (this.opts.onReady) this.opts.onReady(this);
    });
    map.on('error', () => { /* missing tiles offline are expected; keep quiet */ });
    new ResizeObserver(() => { this.map.resize(); this.draw(); }).observe(el);
  }

  MapView.prototype.overlayLayer = function () {
    const self = this;
    return {
      id: 'sf-overlay', type: 'custom', renderingMode: '3d',
      onAdd() {},
      render(gl, args) {
        let m = null;
        if (args && args.defaultProjectionData && args.defaultProjectionData.mainMatrix) m = args.defaultProjectionData.mainMatrix;
        else if (args && args.length === 16) m = args;
        self.matrix = m;
        self.draw();
      },
    };
  };

  MapView.prototype.setBasemap = function (id) {
    const map = this.map;
    for (const sid of this.baseLayers) {
      if (map.getLayer(sid)) map.removeLayer(sid);
      if (map.getSource(sid)) map.removeSource(sid);
    }
    this.baseLayers = [];
    (BASEMAPS[id] || BASEMAPS.satellite).forEach((src, i) => {
      const sid = 'base-' + src;
      map.addSource(sid, { type: 'raster', tiles: [SF.native.tileUrl(src)], tileSize: 256, maxzoom: MAXZ[src] || 19, attribution: ATTR[src] || '' });
      map.addLayer({ id: sid, type: 'raster', source: sid, paint: { 'raster-fade-duration': 0, 'raster-opacity': src === 'esri-labels' ? 0.9 : 1 } }, 'sf-overlay');
      this.baseLayers.push(sid);
    });
    this.basemap = id;
    this.grid = id === 'none';
    this.draw();
  };

  MapView.prototype.setTerrain = function (on) {
    const map = this.map;
    if (on) {
      if (!map.getSource('dem')) map.addSource('dem', { type: 'raster-dem', tiles: [SF.native.tileUrl('terrain')], tileSize: 256, encoding: 'terrarium', maxzoom: 15 });
      map.setTerrain({ source: 'dem', exaggeration: 1 });
    } else if (map.getTerrain && map.getTerrain()) map.setTerrain(null);
    this.terrain = !!on;
  };

  // The 3D intro starts from the whole globe; data is always shown on the flat (Mercator) map.
  MapView.prototype.setGlobe = function (on) {
    if (!this.map.setProjection) return false;
    try { this.map.setProjection({ type: on ? 'globe' : 'mercator' }); } catch (e) { return false; }
    this.globe = !!on;
    this.draw();
    return true;
  };
  MapView.prototype.whenReady = function (fn) {
    if (this.ready) fn(); else this.map.once('load', () => setTimeout(fn, 0));
  };

  MapView.prototype.set3D = function (on) {
    this.mode3d = on;
    const m = this.map;
    const go = () => (on ? m.easeTo({ pitch: Math.max(55, m.getPitch()), duration: 700 }) : m.easeTo({ pitch: 0, bearing: 0, duration: 600 }));
    // tilting during a zoom-to-data animation would cancel the zoom, so wait for it to finish
    if (m.isMoving()) m.once('moveend', go); else go();
    this.draw();
  };

  // Screen position (CSS px) of lon/lat at `alt` metres above the map plane; null if behind the camera.
  MapView.prototype.project = function (lon, lat, alt) {
    const m = this.matrix;
    if (!m) return null;
    const x = (lon + 180) / 360;
    const s = Math.sin(lat * D2R);
    const y = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
    const z = (alt || 0) / (CIRC * Math.cos(lat * D2R));
    const W = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (W <= 1e-9) return null;
    const X = (m[0] * x + m[4] * y + m[8] * z + m[12]) / W;
    const Y = (m[1] * x + m[5] * y + m[9] * z + m[13]) / W;
    return [(X + 1) * 0.5 * this.w, (1 - Y) * 0.5 * this.h];
  };
  // Height used for drawing a point h metres above ground (0 in 2D).
  MapView.prototype.z = function (h) {
    if (!this.mode3d || !U.isNum(h)) return this.mode3d && this.terrain ? this.groundZ : 0;
    return Math.max(0, h) * this.exag + (this.terrain ? this.groundZ : 0);
  };
  MapView.prototype.updateGround = function (lon, lat) {
    if (!this.terrain || !this.map.queryTerrainElevation) { this.groundZ = 0; return; }
    const e = this.map.queryTerrainElevation([lon, lat]);
    this.groundZ = U.isNum(e) ? e : 0;
  };

  MapView.prototype.draw = function () {
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    this.w = w; this.h = h;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // the overlay is projected for the flat map; the globe is only used by the intro
    if (!this.matrix || this.globe) return;
    if (this.grid) this.drawGrid(ctx);
    if (this.opts.draw) this.opts.draw(ctx, this);
  };

  // Plain metric grid for the "no map" (offline) view.
  MapView.prototype.drawGrid = function (ctx) {
    const map = this.map, b = map.getBounds(), z = map.getZoom();
    const center = map.getCenter();
    const mPerPx = (CIRC * Math.cos(center.lat * D2R)) / (512 * Math.pow(2, z));
    const targetPx = 90, raw = mPerPx * targetPx;
    const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
    const step = steps.find((s) => s >= raw) || 100000;
    const f = U.frame(center.lat, center.lng);
    const x0 = f.x(b.getWest()), x1 = f.x(b.getEast()), y0 = f.y(b.getSouth()), y1 = f.y(b.getNorth());
    const pad = Math.max(x1 - x0, y1 - y0);
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.floor((x0 - pad) / step) * step; x <= x1 + pad; x += step) {
      const a = this.project(f.lon(x), f.lat(y0 - pad), 0), c = this.project(f.lon(x), f.lat(y1 + pad), 0);
      if (a && c) { ctx.moveTo(a[0], a[1]); ctx.lineTo(c[0], c[1]); }
    }
    for (let y = Math.floor((y0 - pad) / step) * step; y <= y1 + pad; y += step) {
      const a = this.project(f.lon(x0 - pad), f.lat(y), 0), c = this.project(f.lon(x1 + pad), f.lat(y), 0);
      if (a && c) { ctx.moveTo(a[0], a[1]); ctx.lineTo(c[0], c[1]); }
    }
    ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,0.35)';
    ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
    ctx.fillText('grid ' + (step >= 1000 ? step / 1000 + ' km' : step + ' m'), 12, this.h - 40);
    ctx.restore();
  };

  MapView.prototype.fit = function (bounds, animate = true) {
    if (!bounds) return;
    const [w, s, e, n] = bounds;
    if (!isFinite(w) || !isFinite(n)) return;
    const pad = Math.min(80, Math.max(30, Math.min(this.w, this.h) * 0.1));
    this.map.fitBounds([[w, s], [e, n]], { padding: pad, duration: animate ? 700 : 0, maxZoom: 17.5, pitch: this.mode3d ? this.map.getPitch() : 0, bearing: this.mode3d ? this.map.getBearing() : 0 });
  };
  MapView.prototype.follow = function (lon, lat) {
    const p = this.project(lon, lat, 0);
    if (!p) return this.map.jumpTo({ center: [lon, lat] });
    const mx = this.w * 0.25, my = this.h * 0.25;
    if (p[0] < mx || p[0] > this.w - mx || p[1] < my || p[1] > this.h - my) this.map.easeTo({ center: [lon, lat], duration: 400 });
  };
  MapView.prototype.visibleBounds = function () {
    const b = this.map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  };
  MapView.prototype.snapshot = function () {
    // composite the WebGL map and the overlay into one PNG
    const mapCanvas = this.map.getCanvas();
    const out = document.createElement('canvas');
    out.width = mapCanvas.width; out.height = mapCanvas.height;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#101113';
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(mapCanvas, 0, 0);
    ctx.drawImage(this.canvas, 0, 0, out.width, out.height);
    return out;
  };
  SF.MapView = MapView;
})(typeof window !== 'undefined' ? window : globalThis);
