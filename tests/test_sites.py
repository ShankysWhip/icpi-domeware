"""Site profiles, SIMULATED phase runs at a site, merge/swap hysteresis, and repeatable conversion."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from domeware import scorecard, sites
from domeware.session import Session


def test_profile_saved_once_and_simulated_run_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(sites, "SITES", tmp_path / "sites")
    src = Session(tmp_path / "REAL")
    src.manifest["site"] = {"name": "SIM", "lat_deg": 65.17, "lon_deg": 26.04, "alt_m": 97.5, "alt_datum": "?",
                            "ground_level_m": 83.4, "azimuth_deg": 0.0}
    src.save()
    sites.save(src, "testsite")
    with pytest.raises(SystemExit):
        sites.save(src, "testsite")  # never overwritten
    s = sites.simulate("testsite", tmp_path / "SIM", drones=2, latency_s=0.5, noise_m=2.0, fetch_env=False)
    assert json.loads((tmp_path / "SIM" / "manifest.json").read_text())["simulated"]["site"] == "testsite"
    [card] = scorecard.run(s)
    it = card["items"]
    assert card["validity"]["valid"] and it["B7"]["value"] == "0.5" and it["BD5"]["value"] == "0 / 0"
    assert 12 < float(it["D5"]["value"]) < 20  # small-drone speeds, not 60 m/s


def test_noise_between_close_drones_is_not_a_swap():
    t = pd.date_range("2026-10-28 09:00:00", periods=60, freq="1s", tz="UTC")
    a = pd.DataFrame({"t_utc": t, "e_m": np.linspace(0, 600, 60), "n_m": 0.0, "u_m": 30.0, "h_rel_takeoff_m": 30.0})
    b = a.assign(n_m=12.0)
    jitter = np.where(np.arange(60) % 2, 7.0, 1.0)  # every other update nearer to B: noise, not a real change
    tr = pd.DataFrame({"t_utc": t, "track_id": 1, "e_m": a["e_m"], "n_m": jitter, "u_m": 30.0})
    assert scorecard.merges_swaps({"A": a, "B": b}, tr, 50.0)[1] == 0
    held = tr.assign(n_m=np.where(np.arange(60) >= 30, 11.0, 1.0))  # the track really moves to B and stays
    assert scorecard.merges_swaps({"A": a, "B": b}, held, 50.0)[1] == 1


SAMPLE = Path.home() / "Work" / "domeware-data" / "sessions" / "TEST_simulated_truth" / "raw"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample session not on this laptop")
def test_converting_twice_gives_the_same_rows(tmp_path):
    from domeware import convert

    tables = []
    for k in ("A", "B"):
        s = Session(tmp_path / k)
        s.ingest([SAMPLE])
        convert.run(s)
        tables.append({p.relative_to(s.streams).as_posix(): pd.util.hash_pandas_object(pd.read_parquet(p).astype(str), index=False).sum()
                       for p in s.streams.rglob("*.parquet")})
    assert tables[0] and tables[0] == tables[1]
