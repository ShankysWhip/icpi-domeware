"""End-to-end smoke test: a SIMULATED 5-drone session in real terrain through every step after conversion.

Skips when the example session's environment (DEM, ortho) is not on this laptop. The drones, radar tracks and
clutter are made here from domeware.synthetic and labelled SIMULATED.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from domeware import compare, events, kml, performance, replay, report, runs, scorecard
from domeware.session import Session
from domeware.synthetic import radar_from_truth

SRC = Path.home() / "Work" / "domeware-data" / "sessions" / "TEST_simulated_truth"
T0 = pd.Timestamp("2026-10-28 09:00:00", tz="UTC")


def _latlon(site, e, n):
    lat = site["lat_deg"] + np.asarray(n) / 111_320.0
    return lat, site["lon_deg"] + np.asarray(e) / (111_320.0 * np.cos(np.radians(site["lat_deg"])))


@pytest.fixture(scope="module")
def swarm(tmp_path_factory):
    if not (SRC / "env" / "dem.tif").exists():
        pytest.skip("example environment not on this laptop")
    root = tmp_path_factory.mktemp("swarm") / "SWARM_SIMULATED"
    shutil.copytree(SRC / "env", root / "env")
    s = Session(root)
    s.manifest["site"] = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))["site"]
    s.manifest["env"] = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))["env"]
    site = s.manifest["site"]
    (s.streams / "truth").mkdir(parents=True)
    (s.streams / "radar").mkdir(parents=True)
    t = pd.date_range(T0, periods=2401, freq="100ms")  # 240 s at 10 Hz
    sec = np.arange(len(t)) / 10.0
    h = np.clip(np.minimum(sec, 240 - sec) * 2.0, 0, 30.0)  # climb, cruise at 30 m, land
    tracks = []
    for i in range(5):
        ang = 0.04 * sec + i * 2 * np.pi / 5
        e, n = 800 * np.cos(ang) + 50 * i, 800 * np.sin(ang)
        lat, lon = _latlon(site, e, n)
        d = pd.DataFrame({"t_utc": t, "e_m": e, "n_m": n, "u_m": h, "h_rel_takeoff_m": h, "lat_deg": lat, "lon_deg": lon,
                          "nsats": 14})
        d.to_parquet(s.streams / "truth" / f"D{i + 1}_SIMULATED.parquet")
        if i < 4:  # drone 5 is never seen by the radar
            r = radar_from_truth(d[d["h_rel_takeoff_m"] > 5], latency_s=0.4, noise_m=4.0, track_id=100 + i, seed=i)
            tracks.append(r)
    clutter = pd.DataFrame({"t_utc": pd.date_range(T0 + pd.Timedelta(seconds=60), periods=20, freq="1s"), "track_id": 999,
                            "e_m": 2500.0, "n_m": -900.0, "u_m": 15.0, "class_name": "OTHER", "alarm": False})
    tr = pd.concat([*tracks, clutter], ignore_index=True)
    tr["range_m"] = np.hypot(tr["e_m"], tr["n_m"])
    tr["lat_deg"], tr["lon_deg"] = _latlon(site, tr["e_m"], tr["n_m"])
    tr["alt_m"] = site["alt_m"] + tr["u_m"]
    tr["h_above_site_ground_m"] = tr["u_m"] + site["alt_m"] - site["ground_level_m"]
    tr["class_code"], tr["score"], tr["median_rcs_dbsm"], tr["speed_mps"] = 4, 0.9, -20.0, 20.0
    tr.drop(columns=["source"]).to_parquet(s.streams / "radar" / "tracks.parquet")
    pd.DataFrame({"t_utc": pd.date_range(T0, periods=81, freq="3s")}).to_parquet(s.streams / "radar" / "status.parquet")
    s.manifest["streams"]["radar"] = {"focus_track": 100, "note": "SIMULATED"}
    s.save()
    return s


def test_whole_chain_runs_for_a_swarm(swarm):
    s = swarm
    assert len(events.build(s)) >= 10  # take-off and landing for 5 drones
    df = runs.ensure(s)
    assert df["run_id"].tolist() == ["R1"]
    res = compare.run(s)
    assert len(res) == 5
    seen = {r["truth"]: r["drone_track_ids"] for r in res}
    assert seen["D5_SIMULATED.parquet"] == [] and seen["D1_SIMULATED.parquet"] == [100]
    shift = [r["radar_time_shift_s"] for r in res if r["radar_time_shift_s"] is not None]
    assert shift and all(abs(x + 0.4) <= 0.1 for x in shift)
    [card] = scorecard.run(s)
    assert card["validity"]["valid"] and card["items"]["B7"]["value"] == "0.4"
    stats = replay.build(s)
    assert len(stats["track_owner"]) == 4 and (s.views / "replay.rrd").exists()
    hud = json.loads((s.views / "hud" / "data.json").read_text(encoding="utf-8"))  # the HUD gets every drone
    assert len(hud["drones"]) == 5 and sum(1 for d in hud["drones"] if d["tracked"]) == 4
    assert (s.views / "replay_hud.rbl").exists() and (s.views / "replay.rbl").exists()
    assert performance.build(s) is not None and performance.timeline(s) is not None
    assert kml.build(s).count("<gx:Track>") >= 9
    text = report.build(s)
    assert "Detection by range and height" in text and "Run VALID" in text
