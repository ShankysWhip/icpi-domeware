"""Stress test: SIMULATED swarms at a saved site through the whole chain, every step timed; then the viewer loads the
replay without a window, plays it and is screenshotted at five moments. Nothing here is real data.

  uv run python scripts/stress_test.py OUT_DIR ouluzone 7 20     (sessions and results go under OUT_DIR)

Results: OUT_DIR/stress_results.json and a table on screen.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

from domeware import cli, sites
from domeware.session import Session
from domeware.viewer import screenshots

STEPS = ["replay", "compare", "performance", "kml", "timeline", "scorecard", "courageous", "report"]


def run_one(out: Path, site: str, drones: int) -> dict:
    root = out / f"STRESS_{site}_{drones}drones"
    res = {"drones": drones, "steps_s": {}}
    t0 = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):
        sites.simulate(site, root, drones=drones, seed=drones)
    res["steps_s"]["simulate"] = round(time.monotonic() - t0, 1)
    for step in STEPS:
        t0 = time.monotonic()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main([step, str(root)])
            res["steps_s"][step] = round(time.monotonic() - t0, 1)
        except Exception as e:  # noqa: BLE001 - a failing step is a finding, not a crash of the test
            res["steps_s"][step] = f"FAILED: {e}"
    s = Session(root)
    stats = json.loads((s.views / "replay_stats.json").read_text(encoding="utf-8"))
    res["tracks"] = sum(v for k, v in stats.items() if k.endswith("_tracks") and isinstance(v, int))
    res["truth_frames"] = sum(v for k, v in stats.items() if k.startswith("truth_") and isinstance(v, int))
    res["viewer"] = screenshots(s, out / f"shots_{drones}", moments=[0.0, 0.25, 0.5, 0.75, 1.0], play_s=5.0)
    return res


def main(argv: list[str]) -> int:
    out, site, counts = Path(argv[0]), argv[1], [int(n) for n in argv[2:]]
    out.mkdir(parents=True, exist_ok=True)
    results = [run_one(out, site, n) for n in counts]
    (out / "stress_results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    for r in results:
        total = sum(v for v in r["steps_s"].values() if isinstance(v, (int, float)))
        print(f"{r['drones']:>3} drones: {r['truth_frames']} drone fixes, {r['tracks']} radar tracks, "
              f"chain {total:.0f} s, replay {r['viewer']['rrd_mb']} MB, viewer load {r['viewer']['load_s']} s")
        print("     " + ", ".join(f"{k} {v}" for k, v in r["steps_s"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
