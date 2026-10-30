"""Everyone at the test, in the replay. All positions and weather here are SIMULATED, made in the test."""

import json

import numpy as np
import pandas as pd
import pytest

from domeware import equipment, sites
from domeware.session import Session

SITE = {"name": "SIM radar", "lat_deg": 65.0, "lon_deg": 26.0, "alt_m": 100.0, "ground_level_m": 85.0,
        "source": "SIMULATED"}


def _session(tmp_path) -> Session:
    s = Session(tmp_path / "S")
    s.manifest["site"] = dict(SITE)
    s.manifest["streams"]["weather/onsite_vaisala/SIM_WXT"] = {"device": "SIMWXT", "lat_deg": "65.0003", "lon_deg": "26.0002"}
    (s.streams / "dji_details").mkdir(parents=True)
    (s.streams / "dji_details" / "details.json").write_text(json.dumps({"aircraft_name": "SIM M400", "latitude": 65.0001,
                                                                        "longitude": 25.9995}))
    s.save()
    return s


def test_parties_come_from_the_data_and_the_team_wins(tmp_path):
    s = _session(tmp_path)
    df, problems = equipment.load(s)
    got = dict(zip(df["name"], df["kind"], strict=True))
    assert got == {"SIM radar": "radar", "SIM M400 take-off": "drone_home", "Weather SIMWXT": "weather"}
    radar = df[df["kind"] == "radar"].iloc[0]
    assert radar["height_m"] == pytest.approx(15.0) and not radar["height_assumed"]  # alt 100 - ground 85
    assert df[df["kind"] == "weather"].iloc[0]["height_assumed"]  # the export does not state the sensor height
    (s.root / "equipment.csv").write_text(
        "name,kind,lat_deg,lon_deg,height_m,heading_deg,fov_deg,range_m,note\n"
        "Weather SIMWXT,weather,65.0004,26.0002,3,,,,mast measured by the team\n"
        "EO camera,camera,65.0002,26.0001,4,200,30,800,\n"
        "Bad kind,tank,65,26,,,,,\n"
        "No place,pilot,,26,,,,,\n", encoding="utf-8")
    df, problems = equipment.load(s)
    w = df[df["name"] == "Weather SIMWXT"].iloc[0]
    assert w["height_m"] == 3 and w["source"] == "equipment.csv (team)" and not w["height_assumed"]
    assert "EO camera" in set(df["name"]) and len(problems) == 2
    assert any("row 4" in p and "kind 'tank'" in p for p in problems) and any("row 5" in p for p in problems)
    assert not equipment.template(s)  # never overwrites the team's file


def test_wind_arrow_points_where_the_wind_blows():
    v = equipment.wind_vectors([5.0, 2.0], [180.0, 270.0])  # from the south, from the west
    assert np.allclose(v[0], [0, 50, 0], atol=1e-4) and np.allclose(v[1], [20, 0, 0], atol=1e-4)


def test_site_profile_until_the_radar_data_arrives(tmp_path, monkeypatch):
    monkeypatch.setattr(sites, "SITES", tmp_path / "sites")
    (tmp_path / "sites" / "simsite").mkdir(parents=True)
    (tmp_path / "sites" / "simsite" / "site.json").write_text(json.dumps(SITE | {"azimuth_deg": 0, "alt_datum": "x"}))
    s = Session(tmp_path / "P")
    s.save()
    assert sites.use(s, "simsite")["from_profile"] == "simsite"
    r = Session(tmp_path / "R")
    r.manifest["site"] = dict(SITE)  # declared by the radar
    r.save()
    with pytest.raises(SystemExit, match="declared by its radar"):
        sites.use(r, "simsite")


def test_parties_and_wind_are_drawn_in_the_replay(tmp_path):
    import rerun as rr

    s = _session(tmp_path)
    (s.root / "equipment.csv").write_text("name,kind,lat_deg,lon_deg,height_m,heading_deg,fov_deg,range_m,note\n"
                                        "EO camera,camera,65.0002,26.0001,4,200,30,800,\n", encoding="utf-8")
    t = pd.date_range("2026-10-28 09:00", periods=10, freq="1min", tz="UTC")
    (s.streams / "weather").mkdir(parents=True)
    pd.DataFrame({"t_utc": t, "wind_ms": 4.0, "wind_dir_deg": 190.0, "gust_ms": 7.5}).to_parquet(
        s.streams / "weather" / "onsite_vaisala.parquet")
    rec = rr.RecordingStream("domeware-test")
    rec.save(str(tmp_path / "t.rrd"))
    stats: dict = {}
    legend = equipment.log_equipment(rec, s, SITE, (t[2], t[4]), stats)
    rec.flush()
    assert {p["name"] for p in stats["equipment"]} == {"SIM radar", "SIM M400 take-off", "Weather SIMWXT", "EO camera"}
    assert stats["wind_arrow_minutes"] == 5  # 09:00 .. 09:04: from 2 min before the activity to its end
    assert any(line.startswith("| EO camera | camera |") for line in legend)


def test_google_earth_file_shows_the_parties_before_any_drone_data(tmp_path):
    import xml.dom.minidom

    from domeware import kml

    s = _session(tmp_path)
    k = kml.build(s)
    xml.dom.minidom.parseString(k)
    assert "<name>Equipment</name>" in k and "SIM M400 take-off" in k and "Weather SIMWXT" in k
    assert k.count("<name>500 m</name>") == 1 and "<gx:Track>" not in k


def test_tails_stop_at_a_pause_and_keep_the_last_state():
    """SIMULATED: a track updating every second, silent for 10 s, then back."""
    from domeware.replay import _windows

    t = np.array([0, 1, 2, 3, 13, 14], dtype=np.int64) * 1_000_000_000
    pos = np.arange(6, dtype=np.float32)[:, None].repeat(3, axis=1)
    keep, segs = _windows(t, pos, window_s=20, hz=5, hold_s=5)
    assert list(keep) == [0, 1, 2, 3, 4, 5]
    assert segs[3][:, 0].tolist() == [0, 1, 2, 3]  # whole history before the pause
    assert segs[5][:, 0].tolist() == [4, 5]  # never drawn across the 10 s silence
