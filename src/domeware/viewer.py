"""Open a replay in the Rerun viewer, on this computer only (127.0.0.1), optionally playing the activity from its
start; and take screenshots of a replay without a window, at chosen moments (for checks and the stress test).

The viewer is started here with --bind 127.0.0.1, never through Rerun's own spawn: a viewer listening on all
interfaces makes Windows ask about (or block) it in the firewall. Analytics are turned off before every start.
Remote control uses Rerun's ViewerClient, marked experimental in rerun-sdk 0.40 [may change with an upgrade].
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from domeware.session import Session


def _exe() -> Path:
    return Path(sys.executable).with_name("rerun.exe" if sys.platform == "win32" else "rerun")


def _free_port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def activity(s: Session) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The activity window the replay was built around (views/replay_stats.json), or None."""
    p = s.views / "replay_stats.json"
    span = json.loads(p.read_text(encoding="utf-8")).get("activity_utc") if p.exists() else None
    return (pd.Timestamp(span[0]), pd.Timestamp(span[1])) if span else None


def _launch(rrd: Path, port: int, headless: bool = False, size: str | None = None) -> subprocess.Popen:
    subprocess.run([str(_exe()), "analytics", "disable"], capture_output=True)
    args = [str(_exe()), "--bind", "127.0.0.1", "--port", str(port), *(["--headless"] if headless else []),
            *(["--window-size", size] if size else []), str(rrd)]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _connect(port: int, timeout_s: float = 60.0):
    """Wait until the viewer answers and has the recording loaded; returns the client and the seconds it took."""
    from rerun.experimental import ViewerClient

    t0 = time.monotonic()
    while True:
        try:
            client = ViewerClient.connect(f"rerun+http://127.0.0.1:{port}/proxy")
            if client.viewer_state().recordings:
                return client, time.monotonic() - t0
        except Exception:  # noqa: BLE001 - not up yet
            pass
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"the viewer on 127.0.0.1:{port} did not load the replay in {timeout_s:.0f} s")
        time.sleep(0.5)


def _seek(client, at: pd.Timestamp, play: bool) -> None:
    client.set_time("utc", timestamp=at.tz_convert("UTC").tz_localize(None).to_datetime64(), play=play)


def open_replay(s: Session, start: bool = False) -> str:
    """Open the replay in a window. start=True: jump to the start of the activity and play it (it loops)."""
    rrd = s.views / "replay.rrd"
    if not rrd.exists():
        raise SystemExit(f"No replay yet: run dw replay {s.root}")
    port = _free_port()
    _launch(rrd, port)
    if not start:
        return f"opening {rrd}"
    span = activity(s)
    if span is None:
        return f"opening {rrd} (no activity window known: press play in the viewer)"
    client, _ = _connect(port)
    _seek(client, span[0], play=True)
    return f"playing {rrd} from {span[0]:%H:%M:%S} UTC"


def screenshots(s: Session, out_dir: Path, moments: list[float] = (0.0, 0.5, 1.0), play_s: float = 0.0,
                size: str = "1600x900") -> dict:
    """Screenshots without a window at fractions of the activity (0 = start, 1 = end). play_s > 0: play that long
    from the first moment before its picture (checks that Start works). Returns timings and file names."""
    rrd = s.views / "replay.rrd"
    span = activity(s)
    out_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    proc = _launch(rrd, port, headless=True, size=size)
    report = {"rrd_mb": round(rrd.stat().st_size / 1e6, 1), "shots": []}
    try:
        client, report["load_s"] = _connect(port)
        report["load_s"] = round(report["load_s"], 1)
        for i, f in enumerate(moments):
            if span is not None:
                at = span[0] + (span[1] - span[0]) * f
                _seek(client, at, play=play_s > 0 and i == 0)
                if play_s > 0 and i == 0:
                    time.sleep(play_s)
            time.sleep(1.0)  # let the frame settle
            path = out_dir / f"shot_{i}_{int(f * 100):03d}.png"
            t0 = time.monotonic()
            client.save_screenshot(str(path))
            report["shots"].append({"at": f, "file": path.name, "screenshot_s": round(time.monotonic() - t0, 1)})
    finally:
        proc.kill()
    return report
