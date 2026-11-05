/* Orbit: simulated demo data (a made-up flight, radar and weather station) so the app can
   be tried without files. Clearly labelled as simulated everywhere it appears. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const U = SF.util;

  function rng(seed) {
    let s = seed >>> 0;
    return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }
  function gauss(r) { let u = 0, v = 0; while (!u) u = r(); while (!v) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

  SF.demo = function () {
    const r = rng(42);
    const lat0 = 64.8705, lon0 = 25.7470; // countryside south-east of Oulu, away from airports
    const f = U.frame(lat0, lon0);
    const start = Date.UTC(2026, 9, 29, 10, 0, 0);
    // drone: takeoff 300 m south-east of the radar, two laps of a 1.4 km racetrack, landing
    const D = { t: [], lat: [], lon: [], h: [], alt: [], hs: [], vs: [], yaw: [], bat: [], volt: [], gps: [], mode: [], motor: [], ground: [] };
    const hx = 220, hy = -200;
    const dur = 600, hz = 10;
    let px = hx, py = hy;
    for (let i = 0; i <= dur * hz; i++) {
      const s = i / hz;
      let x, y, h;
      if (s < 25) { x = hx; y = hy; h = s < 5 ? 0 : Math.min(60, (s - 5) * 3); }
      else if (s > dur - 30) { const k = (s - (dur - 30)) / 30; x = hx; y = hy; h = Math.max(0, 60 * (1 - k)); }
      else {
        const k = ((s - 25) / (dur - 55)) * 2 * Math.PI * 2;
        x = hx - 700 + 700 * Math.cos(k); y = hy + 380 * Math.sin(k) + 250 * Math.sin(k) * Math.cos(k);
        const blend = Math.min(1, (s - 25) / 20, (dur - 30 - s) / 20);
        x = hx + (x - hx) * blend; y = hy + (y - hy) * blend;
        h = 60 + 50 * Math.sin(k / 2) ** 2 - (s > 330 && s < 380 ? 45 * Math.sin(((s - 330) / 50) * Math.PI) : 0);
      }
      const t = start + s * 1000;
      const dx = x - px, dy = y - py;
      D.t.push(t); D.lat.push(f.lat(y)); D.lon.push(f.lon(x)); D.h.push(h); D.alt.push(12 + h);
      D.hs.push(Math.hypot(dx, dy) * hz); D.vs.push(i ? (h - D.h[i - 1]) * hz : 0);
      D.yaw.push(Math.hypot(dx, dy) > 0.01 ? (Math.atan2(dx, dy) / U.D2R + 360) % 360 : (D.yaw[i - 1] || 0));
      D.bat.push(Math.round(98 - (s / dur) * 52)); D.volt.push(25.2 - (s / dur) * 3.1); D.gps.push(22); D.mode.push(h > 0.5 ? 6 : 4);
      D.motor.push(s >= 3 && s <= dur - 2 ? 1 : 0); D.ground.push(h < 0.3 ? 1 : 0);
      px = x; py = y;
    }
    const drone = {
      kind: 'drone', source: 'demo', name: 'Simulated quadcopter', product: 'Simulated quadcopter', data: D,
      events: [{ t: start + 3000, kind: 'mode', text: 'Motors started' }, { t: start + 5000, kind: 'mode', text: 'Takeoff' }, { t: start + 335000, kind: 'tip', text: 'Low pass behind the tree line' }, { t: start + (dur - 30) * 1000, kind: 'mode', text: 'Return and land' }],
      pilot: [{ t: start, lat: f.lat(hy - 25), lon: f.lon(hx + 10), src: 'app' }], homes: [{ t: start, lat: f.lat(hy), lon: f.lon(hx), alt: 12 }],
      home: { lat: f.lat(hy), lon: f.lon(hx), alt: 12 }, details: null, info: {}, t0: D.t[0], t1: D.t[D.t.length - 1], simulated: true,
    };

    // radar: updates about once a second with ~8 m noise; loses the drone in the low pass
    const radar = SF.radar.newStore('demo', 'Simulated radar');
    radar.sensors.push({ id: '0', name: 'Simulated radar', lat: lat0, lon: lon0, alt: 22, ground: 10, azimuth: 0, type: '3D search radar (simulated)', state: 'operational', blanking: [] });
    let id = 1000, firstLabel = start + 70000;
    for (let s = 12; s < dur - 8; s += 0.9 + r() * 0.3) {
      const t = start + s * 1000;
      const p = SF.analysis.droneAt(drone, t);
      if (!p || p.h < 4) continue;
      if (s > 335 && s < 371) continue; // low pass: no detection
      if (s > 205 && s < 214) continue; // short drop-out
      if (s >= 214 && id === 1000) id = 1001;
      if (s >= 371 && id === 1001) id = 1002;
      const e = 8 + 0.004 * U.dist(lat0, lon0, p.lat, p.lon);
      const cls = t < firstLabel ? 'UNCLASSIFIED' : id === 1002 && s < 400 ? 'UNCLASSIFIED' : 'DRONE';
      SF.radar.addUpdate(radar, String(id), {
        t: t + 400, lat: p.lat + (gauss(r) * e) / f.ky, lon: p.lon + (gauss(r) * e) / f.kx, alt: 10 + p.h + gauss(r) * 6,
        spd: p.hs + gauss(r), hdg: p.yaw + gauss(r) * 5, cls, score: cls === 'DRONE' ? 0.8 + r() * 0.15 : 0.4 + r() * 0.2, rcs: -18 + gauss(r) * 3, mrcs: -18, alarm: cls === 'DRONE',
      });
    }
    // birds and a car
    const others = [
      { id: '2001', cls: 'BIRD', x: -600, y: 500, vx: 6, vy: -2, h: 40, t0: 40, t1: 220 },
      { id: '2002', cls: 'BIRD', x: 900, y: 900, vx: -4, vy: -5, h: 70, t0: 150, t1: 330 },
      { id: '2003', cls: 'VEHICLE', x: -1200, y: -150, vx: 14, vy: 1, h: 1, t0: 260, t1: 420 },
      { id: '2004', cls: 'DRONE', x: 1500, y: -800, vx: -1, vy: 1.5, h: 30, t0: 480, t1: 505 },
    ];
    for (const o of others) {
      for (let s = o.t0; s < o.t1; s += 1.1) {
        const k = s - o.t0;
        const x = o.x + o.vx * k + 30 * Math.sin(k / 9), y = o.y + o.vy * k;
        SF.radar.addUpdate(radar, o.id, { t: start + s * 1000, lat: f.lat(y + gauss(r) * 6), lon: f.lon(x + gauss(r) * 6), alt: 10 + o.h, spd: Math.hypot(o.vx, o.vy), hdg: (Math.atan2(o.vx, o.vy) / U.D2R + 360) % 360, cls: o.cls, score: 0.7, rcs: o.cls === 'VEHICLE' ? 5 : -25 });
      }
    }
    SF.radar.finalize(radar);
    radar.simulated = true;

    // weather: 1-minute means, south-west wind about 5 m/s
    const wxd = { t: [] };
    for (const k in SF.weather.KINDS) wxd[k] = [];
    for (let m = -20; m <= 30; m++) {
      wxd.t.push(start + m * 60000);
      for (const k in SF.weather.KINDS) wxd[k].push(NaN);
      const i = wxd.t.length - 1;
      wxd.wind[i] = 4.6 + 1.2 * Math.sin(m / 5) + gauss(r) * 0.4; wxd.gust[i] = wxd.wind[i] + 2.5 + r() * 1.5;
      wxd.dir[i] = 225 + 15 * Math.sin(m / 7) + gauss(r) * 5; wxd.temp[i] = 6.2 + m * 0.02; wxd.rh[i] = 84 - m * 0.1; wxd.pres[i] = 1009.5 - m * 0.01;
      wxd.dew[i] = 3.6; wxd.rainRate[i] = m > 18 && m < 24 ? 0.4 : 0;
    }
    const weather = {
      kind: 'weather', name: 'Simulated weather station', meta: {}, columns: [], data: wxd, station: { lat: f.lat(-60), lon: f.lon(-90), alt: 12, name: 'Simulated' },
      t0: wxd.t[0], t1: wxd.t[wxd.t.length - 1], stepMs: 60000, tzNote: 'UTC', notes: ['Simulated data.'], sensors: 'simulated', simulated: true,
    };
    return { drone, radar, weather };
  };
})(typeof window !== 'undefined' ? window : globalThis);
