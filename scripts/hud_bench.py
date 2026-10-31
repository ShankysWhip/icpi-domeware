"""Playback speed of the HUD, measured in a hidden Edge: frames per second and slow frames while it plays.

  uv run python scripts/hud_bench.py SESSION [--size 1720x988] [--seconds 6]

Runs a few set-ups (glass on/off, instruments, map only, fast playback) and prints one line each. The page runs in its
quiet bench mode: nothing is saved or reported, the glass never switches itself off. Compare before and after a change.
Headless numbers are not the user's screen: views/hud_client.json has the fps his own window reported.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from domeware import hud, shots
from domeware.cli import _session_path
from domeware.session import Session

MEASURE = """(async () => {
  const ts = []; let last = performance.now(); const end = last + %d;
  await new Promise((done) => { const f = (now) => { ts.push(now - last); last = now; if (now < end) requestAnimationFrame(f); else done(); };
    requestAnimationFrame(f); });
  ts.shift(); ts.sort((a, b) => a - b);
  const avg = ts.reduce((s, x) => s + x, 0) / ts.length;
  return { fps: 1000 / avg, p50: ts[Math.floor(ts.length * 0.5)], p95: ts[Math.floor(ts.length * 0.95)], max: ts[ts.length - 1],
    slow: ts.filter((x) => x > 34).length, frames: ts.length };
})()"""

SETUPS = [  # name, instruments, glass, panels shown, speed
    ("overview + weather, glass", "over,atmo", True, True, 1),
    ("overview + weather, solid", "over,atmo", False, True, 1),
    ("all six instruments, glass", "over,drones,radar,log,atmo,equip", True, True, 1),
    ("map only", "over,atmo", True, False, 1),
    ("overview + weather, glass, 10x", "over,atmo", True, True, 10),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("--size", default=None, help="1720x988 (default: the window the HUD last reported)")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--uncapped", action="store_true", help="no frame-rate limit: shows the headroom, not the screen")
    a = ap.parse_args()
    s = Session(_session_path(a.session))
    w, h, scale = shots.parse_size(a.size) if a.size else shots.last_size(s)
    edge = hud.edge_path()
    if not edge:
        sys.exit("Microsoft Edge was not found")
    hud.ensure_built(s)
    server, url, _ = hud.make_server(s)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = Path(tempfile.mkdtemp(prefix="dw-bench-"))
    proc = subprocess.Popen([edge, "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-port=0",
                             "--no-first-run", "--disable-extensions", "--mute-audio", f"--window-size={w},{h}",
                             *(["--disable-frame-rate-limit", "--disable-gpu-vsync"] if a.uncapped else []),
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tab = shots._Tab(shots._devtools(profile, proc))
        tab.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=scale, mobile=False)
        tab.call("Performance.enable")
        print(f"{s.root.name} at {w}x{h} @{scale:g}, {a.seconds:g} s each")
        for name, panels, glass, shown, speed in SETUPS:
            tab.call("Page.navigate", url=url + f"?bench=1&open={panels}" + ("" if shown else "&panels=0"))
            end = time.monotonic() + 60
            while time.monotonic() < end and not tab.js("!!(window.__hud && window.__hud.V && window.__hud.V.ready)"):
                time.sleep(0.4)
            tab.js(f"(() => {{ const H = window.__hud; H.R.setGlass({str(glass).lower()}, false); H.S.speed = {speed}; "
                   f"H.S.t = H.S.act[0]; H.R.setPlaying(true); }})()")
            time.sleep(1.0)  # past the first frames after the start
            m0 = {m["name"]: m["value"] for m in tab.call("Performance.getMetrics")["metrics"]}
            r = tab.call("Runtime.evaluate", expression=MEASURE % (a.seconds * 1000), awaitPromise=True,
                         returnByValue=True)["result"]["value"]
            m1 = {m["name"]: m["value"] for m in tab.call("Performance.getMetrics")["metrics"]}
            tab.js("window.__hud.R.setPlaying(false)")
            busy = (m1["TaskDuration"] - m0["TaskDuration"]) / a.seconds * 100
            script = (m1["ScriptDuration"] - m0["ScriptDuration"]) / a.seconds * 100
            style = (m1["RecalcStyleDuration"] + m1["LayoutDuration"] - m0["RecalcStyleDuration"] - m0["LayoutDuration"]) / a.seconds * 100
            print(f"  {name:34s} {r['fps']:5.1f} fps   median {r['p50']:5.1f} ms   95 % {r['p95']:5.1f} ms   "
                  f"slow {r['slow']}/{r['frames']}   main thread {busy:4.1f} % (script {script:4.1f}, style+layout {style:4.1f})")
        tab.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
