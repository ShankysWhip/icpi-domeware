"""The HUD: domeware's own screen around the 3D replay.

One page, opened as an app window (Microsoft Edge --app, else the default browser) and served from this computer
only (127.0.0.1). The Rerun web viewer draws the 3D scene; domeware's own panels around it (clock and readouts,
atmosphere, equipment with a site scope, a timeline with the screenshots on it) run on one clock that also drives the
viewer, so everything shows the same moment.

  dw hud SESSION              build what is missing, serve, open the window
  dw hud SESSION --play       ... and start playing the activity
  dw hud SESSION --at 13:45:10 --open over,radar    open at that moment (UTC) with those instruments
  dw hud SESSION --no-open    serve only (tests); --port N picks the port

The data (views/hud/data.json and views/hud/screens/) is written by dw replay. The viewer's files come from
domeware-vendor (dw setup, hash-pinned): nothing is fetched from the internet while the HUD runs. The server stops by
itself about 45 s after the window is closed. The window reports its size, screen scale and playback speed; they are
kept in views/hud_client.json on this computer (dw shots uses them) and nowhere else.
"""

from __future__ import annotations

import json
import math
import mimetypes
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

from domeware.session import Session

WEB = Path(__file__).parent / "web"
MARGIN = pd.Timedelta("15min")  # the replay keeps weather this far around the activity
DATA_FILES = {"replay.rrd", "replay_hud.rbl", "report.html", "session.kml"}  # the menu links the report and the KML
AIRBORNE_M = 2.0  # as dw compare: airborne from the first to the last moment above 2 m over take-off
TZ_LOCAL = "Europe/Helsinki"
CLIENT_FILE = "hud_client.json"  # in views/: what the window last reported
CLIENT_KEYS = {"w": (int, 200, 10000), "h": (int, 200, 10000), "dpr": (float, 0.5, 5.0), "fps": (float, 1.0, 500.0)}


def _ms(ts) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


def _vals(a, nd: int = 1) -> list:
    """Numbers for JSON: rounded, and None where there is no value (JSON has no NaN)."""
    out = []
    for v in np.asarray(a, dtype=float):
        out.append(None if not math.isfinite(v) else round(float(v), nd))
    return out


def _segments(t_ms: np.ndarray, ok: np.ndarray, gap_ms: int = 2500) -> list[list[int]]:
    """Runs of consecutive True samples as [start, end] in ms; a pause longer than gap_ms starts a new run."""
    out: list[list[int]] = []
    for t, flag in zip(t_ms, ok, strict=True):
        if not flag:
            continue
        if out and t - out[-1][1] <= gap_ms:
            out[-1][1] = int(t)
        else:
            out.append([int(t), int(t)])
    return out


def _airborne(has_fix: np.ndarray, height: np.ndarray | None) -> np.ndarray:
    """Seconds in flight, as dw compare counts them: from the first to the last second above AIRBORNE_M. A log that
    runs on the ground before take-off or after landing must not count as flying (or as 'radar lost')."""
    if height is None:
        return has_fix
    above = np.flatnonzero(np.nan_to_num(height, nan=-1.0) > AIRBORNE_M)
    out = np.zeros(len(has_fix), dtype=bool)
    if len(above):
        out[above[0]:above[-1] + 1] = True
    return out


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[int(c) for c in rgb[:3]])


def _equipment(s: Session, site: dict) -> list[dict]:
    from domeware.equipment import KIND_LABEL, RGB, _short, _slug, load
    from domeware.replay import enu

    df, _ = load(s)
    out = []
    for r in df.itertuples():
        e, n, _u = (float(v) for v in enu(r.lat_deg, r.lon_deg, site["alt_m"], site["lat_deg"], site["lon_deg"], site["alt_m"]))
        at_radar = r.kind == "radar" and abs(e) < 1 and abs(n) < 1
        out.append({"id": "radar" if at_radar else _slug(r.name), "name": r.name, "kind": r.kind,
                    "type": KIND_LABEL.get(r.kind, r.kind), "lat": round(r.lat_deg, 6), "lon": round(r.lon_deg, 6),
                    "e": round(e, 1), "n": round(n, 1), "range_m": round(math.hypot(e, n), 1),
                    "bearing_deg": round(math.degrees(math.atan2(e, n)) % 360, 1), "height_m": round(r.height_m, 1),
                    "height_assumed": bool(r.height_assumed), "source": str(r.source), "source_short": _short(r.source),
                    "note": r.note if isinstance(r.note, str) else "", "color": _hex(RGB[r.kind]),
                    "entity": "/world/radar" if at_radar else f"/world/equipment/{_slug(r.name)}"})
    return out


def _weather(s: Session, window) -> dict | None:
    for name, src, cols in (("onsite_vaisala.parquet", "Vaisala on-site",
                             {"wind": "wind_ms", "gust": "gust_ms", "dir": "wind_dir_deg", "temp": "temp_c",
                              "rh": "rh_pct", "pressure": "pressure_hpa", "rain": "precip_mmh"}),
                            ("fmi_nearest.parquet", "FMI nearest station",
                             {"wind": "wind_ms", "gust": "gust_ms", "dir": "wind_dir_deg", "temp": "temp_c",
                              "rh": "rh_pct", "pressure": "pressure_hpa", "visibility": "visibility_m"})):
        p = s.streams / "weather" / name
        if not p.exists():
            continue
        w = pd.read_parquet(p)
        w["t_utc"] = pd.to_datetime(w["t_utc"], utc=True)
        if window is not None:
            w = w[(w["t_utc"] >= window[0]) & (w["t_utc"] <= window[1])]
        w = w.sort_values("t_utc")
        if not len(w):
            continue
        out = {"source": src, "t": [_ms(t) for t in w["t_utc"]]}
        for key, col in cols.items():
            if col in w and pd.to_numeric(w[col], errors="coerce").notna().any():
                out[key] = _vals(pd.to_numeric(w[col], errors="coerce"), 0 if key == "dir" else 1)
        return out
    return None


def _screens(s: Session, out: Path) -> list[dict]:
    """Every timed picture as a small thumbnail (timeline) and a large copy (viewer); the raw file stays untouched."""
    from PIL import Image, ImageOps

    from domeware.equipment import _slug
    from domeware.images import pictures

    shots = sorted((p for p in pictures(s) if p["t_utc"] is not None), key=lambda p: p["t_utc"])
    out.mkdir(parents=True, exist_ok=True)
    res = []
    for i, p in enumerate(shots):
        stem = f"{i:03d}_{_slug(p['group'])}"
        try:
            with Image.open(p["file"]) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")  # a portrait phone photo stays upright
                big = im.copy()
                big.thumbnail((1600, 1200))
                big.save(out / f"{stem}.jpg", quality=86)
                im.thumbnail((200, 120))
                im.save(out / f"{stem}_t.jpg", quality=80)
        except OSError as e:  # a broken picture is skipped with its reason, never fatal
            s.note(f"hud: {p['file'].name}: not shown ({e})")
            continue
        res.append({"t": _ms(p["t_utc"]), "group": p["group"], "name": p["file"].name,
                    "thumb": f"hud/screens/{stem}_t.jpg", "full": f"hud/screens/{stem}.jpg"})
    return res


def export(s: Session, *, site: dict, span, truths: dict, errs: dict, tracks: pd.DataFrame, owner: dict,
           colours: dict, events: list) -> dict:
    """views/hud/data.json for the HUD, from what dw replay has just computed (nothing is worked out twice)."""
    from domeware.replay import drone_frames

    out = s.views / "hud"
    out.mkdir(parents=True, exist_ok=True)
    window = (span[0] - MARGIN, span[1] + MARGIN) if span else None
    drones = []
    if truths and span is not None:
        grid = pd.Series(pd.date_range(span[0].floor("1s"), span[1].ceil("1s"), freq="1s", unit="ns"))
        t_ms = (grid.astype("int64") // 1_000_000).to_numpy()
        for name, k in drone_frames(grid, truths, errs).items():
            fly = _airborne(k["e_m"].notna().to_numpy(),
                            k["h_rel_takeoff_m"].to_numpy(float) if "h_rel_takeoff_m" in k else None)
            err = k["err_m"].to_numpy(float) if "err_m" in k else np.full(len(k), np.nan)
            seen = np.isfinite(err) & fly
            mine = sorted(int(tid) for tid, d in owner.items() if d == name)
            drones.append({
                "name": name, "color": _hex(colours.get(name, [200, 200, 200])), "entity": f"/world/truth/{name}",
                "series": {"t": [int(v) for v in t_ms], "e": _vals(k["e_m"]), "n": _vals(k["n_m"]),
                           "range": _vals(np.hypot(k["e_m"], k["n_m"]), 0),
                           "height": _vals(k["h_rel_takeoff_m"]) if "h_rel_takeoff_m" in k else None,
                           "speed": _vals(k["speed"]), "err": _vals(err)},
                "flying": _segments(t_ms, fly), "tracked": _segments(t_ms, seen), "tracks": mine,
                "tracked_share": round(float(seen.sum() / fly.sum()), 3) if fly.any() and len(tracks) else None,
                # over every radar update, as dw compare and the report do (not the 1 s grid): one number everywhere
                "err_median_m": (round(float(errs[name]["err_m"].median()), 1) if name in errs and len(errs[name])
                                 else None)})
    radar = None
    if len(tracks) and window is not None:
        tt = pd.to_datetime(tracks["t_utc"], utc=True)
        per_s = tracks.assign(sec=tt.dt.floor("1s")).groupby("sec")["track_id"].nunique()
        grid = pd.date_range(window[0].floor("1s"), window[1], freq="1s")
        alive = per_s.reindex(grid, fill_value=0)
        radar = {"t0": _ms(grid[0]), "step_ms": 1000, "alive": [int(v) for v in alive.to_numpy()],
                 "tracks": int(tracks["track_id"].nunique())}
    runs = []
    if (s.root / "runs.csv").exists():
        from domeware import runs as R

        runs = [{"id": r.run_id, "part": r.part, "start": _ms(r.start_utc), "end": _ms(r.end_utc)}
                for r in R.load(s).itertuples() if pd.notna(r.start_utc) and pd.notna(r.end_utc)]
    headers = []  # an encrypted DJI log still states when and how far it flew: shown until the path is decoded
    for f in sorted(s.streams.glob("dji_details/*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("start_time") and d.get("total_time"):
            t0 = pd.Timestamp(d["start_time"]).tz_convert("UTC")
            headers.append({"name": d.get("aircraft_name") or "DJI", "start": _ms(t0),
                            "end": _ms(t0 + pd.Timedelta(seconds=float(d["total_time"]))),
                            "distance_m": d.get("total_distance"), "max_height_m": d.get("max_height"),
                            "max_speed_ms": d.get("max_horizontal_speed")})
    screens = _screens(s, out / "screens")
    weather = _weather(s, window)
    data = {
        "version": 1, "session": s.root.name, "generated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "app_id": "domeware-replay", "recording_id": s.manifest["session_id"], "timeline": "utc",
        "tz_local": TZ_LOCAL,
        "site": {"name": site.get("name") or "site", "lat": site["lat_deg"], "lon": site["lon_deg"],
                 "source": site.get("source") or ""},
        "activity": [_ms(span[0]), _ms(span[1])] if span else None,
        "window": [_ms(window[0]), _ms(window[1])] if window else None,
        "status": {"radar_tracks": int(tracks["track_id"].nunique()) if len(tracks) else 0, "drones": len(drones),
                   "drone_logs_encrypted": len(s.raw_files("dji_flightrecord_encrypted")) if not drones else 0,
                   "weather": weather["source"] if weather else None, "screens": len(screens),
                   "simulated": bool(s.manifest.get("simulated"))},
        "equipment": _equipment(s, site), "weather": weather, "drones": drones, "radar": radar,
        "events": [{"t": _ms(t), "text": m} for t, m in events], "runs": runs, "screens": screens,
        "flights_header": headers,
    }
    (out / "data.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return {"file": "views/hud/data.json", "drones": len(drones), "screens": len(screens),
            "equipment": len(data["equipment"])}


# --- serving -------------------------------------------------------------------------------------------------------

def _web_viewer() -> Path:
    from domeware.overview import WEB_VIEWER

    pkg = WEB_VIEWER / "package.json"
    if not (WEB_VIEWER / "re_viewer_bg.wasm").exists():
        raise SystemExit("The 3D web viewer is missing: run  uv run dw setup  (or copy domeware-vendor from a co-worker)")
    import rerun

    got = json.loads(pkg.read_text(encoding="utf-8")).get("version")
    if got != rerun.__version__:
        raise SystemExit(f"web viewer {got} does not match rerun-sdk {rerun.__version__}: run  uv run dw setup")
    return WEB_VIEWER


def _code_stamp() -> tuple[int, int]:
    """Newest change (ms) of the page files and of domeware's Python code: an open window offers a reload when the
    page changed, and asks for a restart when the server's own code did."""
    web = max((f.stat().st_mtime_ns for f in WEB.rglob("*") if f.is_file()), default=0)
    py = max((f.stat().st_mtime_ns for f in Path(__file__).parent.rglob("*.py")), default=0)
    return web // 1_000_000, py // 1_000_000


def _note_client(s: Session, state: dict, query: dict) -> None:
    """Keep what the window reports (size, screen scale, playback fps, glass) in views/hud_client.json: numbers only,
    clamped, on this computer. Prints a line when the window size changes or the glass turns solid."""
    got = {}
    for k, (cast, lo, hi) in CLIENT_KEYS.items():
        try:
            v = cast(query[k][0])
        except (KeyError, IndexError, ValueError):
            continue
        if math.isfinite(v):
            got[k] = min(hi, max(lo, v))
    if "glass" in query:
        got["glass"] = query["glass"][0] == "1"
    if not got:
        return
    old = state["client"]
    new = {**old, **got}
    if "fps" in new:
        new["fps"] = round(new["fps"] / 5) * 5  # steadier file: playback speed to the nearest 5 fps
    view = [new.get(k) for k in ("w", "h", "dpr")]
    if view != [old.get(k) for k in ("w", "h", "dpr")] and new.get("w"):
        print(f"HUD window {new['w']}x{new['h']} at {round(new.get('dpr', 1) * 100)} % screen scale", flush=True)
    if old.get("glass") and new.get("glass") is False:
        print(f"HUD glass panels turned solid (playback {new.get('fps', '?')} fps)", flush=True)
    state["client"] = new
    if new == old and time.monotonic() - state.get("client_saved", 0) < 60:
        return
    out = {"window": [new.get("w"), new.get("h")], "scale": new.get("dpr"), "playback_fps": new.get("fps"),
           "glass": new.get("glass"), "updated_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
    try:
        (s.views / CLIENT_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
        state["client_saved"] = time.monotonic()
    except OSError:  # a full or locked disk must not stop the HUD
        pass


def make_server(s: Session, port: int = 0) -> tuple[ThreadingHTTPServer, str, dict]:
    """A server for one session on 127.0.0.1. Serves the page, the viewer files and this session's replay only."""
    viewer = _web_viewer()
    roots = {"/app/": WEB.resolve(), "/viewer/": viewer.resolve(), "/data/": s.views.resolve()}
    state = {"last": time.monotonic(), "pinged": False, "bye": None, "client": {}, "py0": _code_stamp()[1]}
    data_ok = {roots["/data/"] / f for f in DATA_FILES}
    types = {".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
             ".html": "text/html; charset=utf-8", ".json": "application/json", ".rrd": "application/octet-stream",
             ".rbl": "application/octet-stream", ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml",
             ".kml": "application/vnd.google-earth.kml+xml"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: the terminal is for the user
            pass

        def _send(self, f: Path, cache: bool) -> None:
            self.send_response(200)
            self.send_header("Content-Type", types.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0]
                             or "application/octet-stream")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            if self.command == "HEAD":  # the menu asks whether a file exists without fetching it
                return
            with f.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)

        def do_POST(self):  # noqa: N802
            if urllib.parse.urlsplit(self.path).path == "/api/bye":  # the window is closing (or reloading)
                state["bye"] = time.monotonic()
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

        def do_GET(self):  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(WEB / "index.html", False)
                if path == "/api/ping":
                    state["last"], state["pinged"] = time.monotonic(), True
                    _note_client(s, state, urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))
                    web, py = _code_stamp()
                    body = json.dumps({"web": web, "restart": py > state["py0"]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                    return None
                for prefix, base in roots.items():
                    if not path.startswith(prefix):
                        continue
                    rel = urllib.parse.unquote(path[len(prefix):])
                    if "\\" in rel or ":" in rel or rel.startswith("/") or "\x00" in rel:
                        break  # never let a request name a drive, a share or an absolute path
                    f = (base / rel).resolve()
                    if prefix == "/data/" and not (f in data_ok or f.is_relative_to(roots["/data/"] / "hud")):
                        break  # checked after resolving: hud/..%2f must not reach the rest of views/
                    if prefix == "/viewer/" and not f.exists() and f.with_name(f.name + ".js").exists():
                        f = f.with_name(f.name + ".js")  # the viewer imports "./re_viewer" without the extension
                    if f.is_relative_to(base) and f.is_file():
                        return self._send(f, prefix == "/viewer/")
                    break
            except (ConnectionError, OSError):  # the page went away mid-file
                return None
            self.send_error(404)
            return None

        do_HEAD = do_GET  # noqa: N815 - same gate, headers only

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_address[1]}/", state


def edge_path() -> str | None:
    """Microsoft Edge (it ships with Windows), or None."""
    import os

    for edge in (shutil.which("msedge"), os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                 os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")):
        if edge and Path(edge).exists():
            return edge
    return None


def _open_window(url: str) -> None:
    import webbrowser

    edge = edge_path()
    if edge:
        subprocess.Popen([edge, f"--app={url}", "--window-size=1720,1020"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return
    webbrowser.open(url)


def view_query(*, play: bool = False, at: str | None = None, panels: str | None = None, hide: bool = False,
               menu: bool = False, shot: bool = False) -> str:
    """The page address for a view: ?t=13:45:10&open=over,radar ... (the page reads the same names)."""
    q = {k: v for k, v in (("play", "1" if play else None), ("t", at), ("open", panels), ("panels", "0" if hide else None),
                           ("menu", "1" if menu else None), ("shot", "1" if shot else None)) if v is not None}
    return "?" + urllib.parse.urlencode(q, safe=":,") if q else ""


def ensure_built(s: Session) -> None:
    if not all((s.views / f).exists() for f in ("replay.rrd", "replay_hud.rbl", "hud/data.json")):
        from domeware import replay

        print("building the replay first ...")
        replay.build(s)


def serve(s: Session, port: int = 0, open_window: bool = True, play: bool = False, at: str | None = None,
          panels: str | None = None, hide: bool = False) -> int:
    ensure_built(s)
    server, url, state = make_server(s, port)
    url += view_query(play=play, at=at, panels=panels, hide=hide)

    def watchdog():
        """Stop 15 s after the window says bye (unless it was a reload), 3 min after the last ping (a hidden or
        minimised window still pings about once a minute), or 5 min after start if no window ever came."""
        while True:
            time.sleep(5)
            now = time.monotonic()
            bye = state["bye"]
            if bye is not None and now - bye > 15 and state["last"] < bye:
                break
            if now - state["last"] > (180 if state["pinged"] else 300):
                break
        server.shutdown()

    threading.Thread(target=watchdog, daemon=True).start()
    print(f"HUD for {s.root.name}: {url}  (this computer only; stops by itself when the window is closed)")
    if open_window:
        _open_window(url)
    server.serve_forever()
    return 0
