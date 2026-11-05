/* Orbit: the 3D intro played when Orbit starts. The Orbit mark turns into place in 3D: the ring swings
   open round the radar while the drone draws it, and radar pings spread in the ring's plane. Then the window
   opens on the globe and the camera flies down to the test site. A click or a key skips it. */
(function (G) {
  'use strict';
  const SF = G.SF || (G.SF = {});
  const I = (SF.intro = { running: false });
  const D2R = Math.PI / 180;
  const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
  const part = (t, a, b) => clamp01((t - a) / (b - a));
  const lerp = (a, b, f) => a + (b - a) * f;
  const out3 = (x) => 1 - Math.pow(1 - x, 3);
  const inOut3 = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  const outBack = (x) => 1 + 2.4 * Math.pow(x - 1, 3) + 1.4 * Math.pow(x - 1, 2);

  // At rest the mark matches the app icon: a 22 × 9.5 ellipse turned 24° anticlockwise, the drone 30° below its right end.
  const REST_TILT = Math.asin(9.5 / 22), REST_TURN = -24 * D2R, DRONE = -30 * D2R;
  const OPEN = 2.2; // s: the window starts to open
  const FLY = 3.4; // s: the camera's flight from the globe down to the site

  // One frame of the mark at time t (seconds), in CSS pixels on a W × H canvas.
  function paint(ctx, W, H, t, font) {
    ctx.clearRect(0, 0, W, H);
    const R = Math.max(46, Math.min(W, H) * 0.105), cx = W / 2, cy = H / 2 - R * 0.42;
    const tilt = lerp(1.2 * D2R, REST_TILT, out3(part(t, 0.08, 1.25)));
    const turn = lerp(42 * D2R, REST_TURN, inOut3(part(t, 0, 1.35)));
    const lap = out3(part(t, 0.1, 1.4));
    const head = DRONE - 2 * Math.PI * (1 - lap);
    const leave = inOut3(part(t, OPEN - 0.25, OPEN + 0.45));
    const scale = 1 - 0.08 * leave;
    const st = Math.sin(tilt), ct = Math.cos(tilt), sr = Math.sin(turn), cr = Math.cos(turn);
    // a point on a ring of radius r in the mark's plane, seen in perspective; the far side is up
    const P = (th, r) => {
      const x = r * Math.cos(th), z = r * Math.sin(th);
      const y1 = -z * st, z1 = z * ct;
      const k = (scale * 6) / (6 + z1 / R);
      return [cx + (x * cr - y1 * sr) * k, cy + (x * sr + y1 * cr) * k];
    };
    ctx.save();
    ctx.globalAlpha = 1 - leave;
    // radar pings spreading in the plane of the ring
    for (const t0 of [0.42, 1.05]) {
      const f = part(t, t0, t0 + 1.45);
      if (f <= 0 || f >= 1) continue;
      ctx.strokeStyle = 'rgba(255,255,255,' + (0.32 * (1 - f) * (1 - f)).toFixed(3) + ')';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      for (let i = 0; i <= 96; i++) {
        const p = P((i / 96) * 2 * Math.PI, R * (0.25 + 1.65 * out3(f)));
        if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]);
      }
      ctx.stroke();
    }
    // the part of the ring the drone has drawn: the far half dim, the near half bright
    const lw = Math.max(2, R * 0.06) * scale, from = DRONE - 2 * Math.PI, n = Math.max(2, Math.ceil(200 * lap));
    const segs = [];
    for (let i = 0; i < n; i++) {
      const a = from + ((head - from) * i) / n, b = from + ((head - from) * (i + 1)) / n;
      segs.push([P(a, R), P(b, R), Math.sin((a + b) / 2)]);
    }
    ctx.lineCap = 'butt';
    const ring = (near) => {
      for (const [p, q, d] of segs) {
        if (d < 0 !== near) continue;
        const f = (1 - d) / 2; // 1 nearest … 0 farthest
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.3 + 0.7 * f).toFixed(3) + ')';
        ctx.lineWidth = lw * (0.85 + 0.25 * f);
        ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
      }
    };
    ring(false);
    // the radar, between the far and the near half of the ring
    const c = outBack(part(t, 0.22, 0.65));
    if (c > 0) { ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(cx, cy, R * 0.15 * c * scale, 0, 2 * Math.PI); ctx.fill(); }
    ring(true);
    // the drone, with a gap cut round it as in the icon
    if (lap > 0) {
      const d = P(head, R), r = R * 0.2 * scale;
      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath(); ctx.arc(d[0], d[1], r * 1.5, 0, 2 * Math.PI); ctx.fill();
      ctx.globalCompositeOperation = 'source-over';
      const glow = ctx.createRadialGradient(d[0], d[1], 0, d[0], d[1], r * 4.5);
      glow.addColorStop(0, 'rgba(255,122,26,0.3)'); glow.addColorStop(1, 'rgba(255,122,26,0)');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(d[0], d[1], r * 4.5, 0, 2 * Math.PI); ctx.fill();
      ctx.fillStyle = '#ff7a1a'; ctx.beginPath(); ctx.arc(d[0], d[1], r, 0, 2 * Math.PI); ctx.fill();
    }
    // the name
    const w = out3(part(t, 1.0, 1.6));
    if (w > 0) {
      ctx.globalAlpha = (1 - leave) * w;
      ctx.fillStyle = '#ececee';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.font = '600 ' + Math.round(R * 0.42 * scale) + 'px ' + font;
      ctx.fillText('Orbit', cx, cy + R * 1.6 * scale + (1 - w) * 10);
    }
    ctx.restore();
  }
  I.paint = paint;

  let run = null;
  // opts: {mv: the MapView, target: {lon, lat, zoom}, hasData(), onEnd(skipped)}
  I.play = function (opts) {
    if (run) return;
    const el = document.getElementById('intro'), cv = document.getElementById('introCanvas');
    const font = getComputedStyle(document.body).fontFamily, ctx = cv.getContext('2d');
    run = { t0: performance.now(), timers: [], raf: 0, globe: false, opened: false, opts };
    I.running = I.played = true;
    I.startedAt = performance.now();
    el.style.transitionDuration = '';
    el.classList.remove('out');
    el.hidden = false;
    const tick = () => {
      if (!run) return;
      const t = (performance.now() - run.t0) / 1000, dpr = window.devicePixelRatio || 1;
      if (cv.width !== Math.round(innerWidth * dpr) || cv.height !== Math.round(innerHeight * dpr)) { cv.width = Math.round(innerWidth * dpr); cv.height = Math.round(innerHeight * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint(ctx, innerWidth, innerHeight, t, font);
      if (t < OPEN + 0.8) run.raf = requestAnimationFrame(tick);
    };
    run.raf = requestAnimationFrame(tick);
    // the globe turns behind the mark until the window opens, then the camera flies down
    opts.mv.whenReady(() => { if (run && !run.opened) startGlobe(run); });
    run.timers.push(setTimeout(() => { if (run) el.classList.add('out'); }, (OPEN - 0.1) * 1000));
    run.timers.push(setTimeout(() => { if (!run) return; run.opened = true; if (run.globe) flyIn(run); else end(false); }, OPEN * 1000));
    window.addEventListener('pointerdown', onSkip, true);
    window.addEventListener('keydown', onSkip, true);
  };
  function startGlobe(r) {
    const mv = r.opts.mv, m = mv.map, tg = r.opts.target;
    if (!mv.setGlobe(true)) return;
    r.globe = true;
    const el = m.getContainer(), h = Math.max(200, Math.min(el.clientWidth, el.clientHeight));
    const zoom = Math.log2((0.8 * h * Math.PI) / 512); // the whole globe, 80 % of the map's height across
    const lat = Math.max(-45, Math.min(45, tg.lat * 0.5));
    m.jumpTo({ center: [tg.lon - 85, lat], zoom, pitch: 0, bearing: 0 });
    m.easeTo({ center: [tg.lon - 38, lat + 6], duration: OPEN * 1000, easing: (x) => x, essential: true });
  }
  function flyIn(r) {
    const m = r.opts.mv.map, tg = r.opts.target;
    m.flyTo({ center: [tg.lon, tg.lat], zoom: tg.zoom, pitch: 55, bearing: -20, duration: FLY * 1000, curve: 1.5, easing: inOut3, essential: true });
    r.timers.push(setTimeout(() => {
      if (run !== r) return;
      // at this zoom the globe is already flat, so switching back to the flat map shows no jump
      m.stop();
      r.opts.mv.setGlobe(false);
      m.jumpTo({ center: [tg.lon, tg.lat], zoom: tg.zoom, pitch: 55, bearing: -20 });
      m.easeTo({ pitch: 0, bearing: 0, duration: 1100, easing: out3 });
      end(false);
    }, FLY * 1000 + 60));
  }
  function end(skipped) {
    const r = run;
    if (!r) return;
    run = null;
    I.running = false;
    cancelAnimationFrame(r.raf);
    r.timers.forEach(clearTimeout);
    window.removeEventListener('pointerdown', onSkip, true);
    window.removeEventListener('keydown', onSkip, true);
    const el = document.getElementById('intro'), mv = r.opts.mv, tg = r.opts.target;
    if (skipped) {
      el.style.transitionDuration = '.25s';
      // straight to where the flight would have ended, unless data has arrived and the app shows it
      mv.whenReady(() => {
        if (run) return;
        mv.map.stop();
        mv.setGlobe(false);
        if (!(r.opts.hasData && r.opts.hasData())) mv.map.jumpTo({ center: [tg.lon, tg.lat], zoom: tg.zoom, pitch: 0, bearing: 0 });
      });
    }
    el.classList.add('out');
    setTimeout(() => { if (!run) { el.hidden = true; el.classList.remove('out'); el.style.transitionDuration = ''; } }, skipped ? 300 : 800);
    if (r.opts.onEnd) r.opts.onEnd(skipped);
  }
  function onSkip(e) {
    if (e.type === 'keydown') {
      if (e.metaKey || e.ctrlKey || e.altKey) return; // menu shortcuts still work
      e.preventDefault();
      e.stopPropagation();
    }
    end(true);
  }
  I.skip = () => end(true);
  // The page starts behind the intro's dark screen. If Orbit fails to start, never leave that screen up.
  setTimeout(() => {
    if (I.running || I.played) return;
    const el = document.getElementById('intro');
    if (el) el.hidden = true;
    document.documentElement.classList.remove('intro-run');
  }, 6000);
})(typeof window !== 'undefined' ? window : globalThis);
