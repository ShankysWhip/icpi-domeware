"""Runs, drone events and scorecard values on a two-drone session. All data here is SIMULATED, built in the test."""

import json
import struct

import numpy as np
import pandas as pd

from domeware import events, runs, scorecard
from domeware.session import Session

T0 = pd.Timestamp("2026-10-28 09:00:00", tz="UTC")


def _session(tmp_path) -> Session:
    """Two drones fly the same line 20 m apart for 120 s; the radar tracks drone A 8 m off, drone B from t=30 s."""
    s = Session(tmp_path / "SIM")
    (s.streams / "truth").mkdir(parents=True)
    (s.streams / "radar").mkdir(parents=True)
    t = pd.date_range(T0, periods=121, freq="1s")
    h = np.r_[np.linspace(0, 30, 10), np.full(101, 30.0), np.linspace(30, 0, 10)]
    e = np.linspace(2000, 100, 121)
    for name, n in (("A_SIMULATED", 0.0), ("B_SIMULATED", 20.0)):
        pd.DataFrame({"t_utc": t, "e_m": e, "n_m": n, "u_m": h, "h_rel_takeoff_m": h,
                      "nsats": 14}).to_parquet(s.streams / "truth" / f"{name}.parquet")
    a = pd.DataFrame({"t_utc": t[10:110], "track_id": 1, "e_m": e[10:110], "n_m": 8.0, "u_m": h[10:110]})
    b = pd.DataFrame({"t_utc": t[30:110], "track_id": 2, "e_m": e[30:110], "n_m": 20.0, "u_m": h[30:110]})
    bird = pd.DataFrame({"t_utc": t[50:60], "track_id": 9, "e_m": 500.0, "n_m": 900.0, "u_m": 50.0})
    tr = pd.concat([a, b, bird], ignore_index=True)
    tr["range_m"] = np.hypot(tr["e_m"], tr["n_m"])
    tr["class_name"] = np.where(tr["t_utc"] >= t[15], "DRONE", "UNKNOWN")
    tr["alarm"] = tr["t_utc"] >= t[80]
    tr["speed_mps"] = 15.8
    tr.to_parquet(s.streams / "radar" / "tracks.parquet")
    s.manifest["streams"]["radar"] = {}
    s.save()
    return s


def test_runs_from_takeoffs_then_card_phases_replace_auto_run(tmp_path):
    s = _session(tmp_path)
    df = runs.ensure(s)
    assert df[["run_id", "part"]].values.tolist() == [["R1", "RUN"]]
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"run": {"run_id": "SIM-1", "date": "2026-10-28"}, "phases": {
        "P1": {"used": "Included", "start": "09:00:10", "end": "09:00:40"},
        "P2": {"used": "Included", "start": "09:00:40", "end": "09:01:20"},
        "P3": {"used": "Included", "start": "09:01:20", "end": "09:01:50"},
        "P4": {"used": "Not used", "start": "09:01:50", "end": "09:02:00"}}}))
    df = runs.import_card(s, card)
    assert sorted(df["run_id"].unique()) == ["SIM-1"]  # the overlapping auto-detected R1 is gone
    assert sorted(df["part"]) == ["P1", "P2", "P3", "RUN"]  # a phase marked Not used is not imported
    w = runs.windows(df, "SIM-1")
    assert w["RUN"] == (pd.Timestamp("2026-10-28 09:00:10", tz="UTC"), pd.Timestamp("2026-10-28 09:01:50", tz="UTC"))


def test_scorecard_values_match_the_simulation(tmp_path):
    s = _session(tmp_path)
    runs.ensure(s)
    (s.root / "runs.csv").write_text(
        "run_id,part,start_utc,end_utc,notes\n"
        "R1,RUN,2026-10-28T09:00:00Z,2026-10-28T09:02:00Z,\n"
        "R1,P2,2026-10-28T09:00:40Z,2026-10-28T09:01:20Z,\n"
        "R1,P3,2026-10-28T09:01:20Z,2026-10-28T09:01:50Z,\n")
    [card] = scorecard.run(s)
    it = card["items"]
    assert card["phases"]["P2"] == {"start": "09:00:40", "end": "09:01:20"}
    assert it["BD2"]["value"].startswith("8.0 / 0.0")  # drone A's 8 m offset is the worst drone
    assert it["BD4"]["value"].startswith("2 / 2")
    assert it["BD5"]["value"] == "0 / 0"
    assert it["BD8"]["value"] == "1"  # the bird
    assert it["A5"]["value"] == "20.0"  # the drones flew 20 m apart
    assert it["BA2"]["value"] == "0.0"  # alarm came on before P3, so it is on at the phase's first update
    assert "BL2" not in it  # no P1 window: no P1 values
    assert (s.views / "association_R1.parquet").exists()
    grid = pd.read_parquet(s.views / "association_R1.parquet")
    assert grid[grid["drone"] == "B_SIMULATED"]["detected"].sum() < grid[grid["drone"] == "A_SIMULATED"]["detected"].sum()


def test_events_from_truth(tmp_path):
    s = _session(tmp_path)
    df = events.build(s)
    kinds = df.groupby("drone")["kind"].apply(list).to_dict()
    assert kinds["A_SIMULATED"] == ["take-off", "landing"]
    assert df[df["kind"] == "take-off"]["t_utc"].iloc[0] == T0 + pd.Timedelta(seconds=1)


def test_tlog_mode_arm_and_warnings(tmp_path):
    from pymavlink.dialects.v20 import ardupilotmega as mav

    m = mav.MAVLink(None, srcSystem=3, srcComponent=1)
    buf = bytearray()
    t0_us = int(T0.timestamp() * 1e6)
    msgs = [mav.MAVLink_heartbeat_message(2, 3, 81, 0, 4, 3),  # copter, STABILIZE, disarmed
            mav.MAVLink_heartbeat_message(2, 3, 81 | 128, 5, 4, 3),  # LOITER, armed
            mav.MAVLink_statustext_message(4, b"Bad compass health"),
            mav.MAVLink_statustext_message(6, b"info only")]
    for k, msg in enumerate(msgs):
        buf += struct.pack(">Q", t0_us + k * 1_000_000) + msg.pack(m)
    p = tmp_path / "gcs.tlog"
    p.write_bytes(bytes(buf))
    evs, method = events.from_mavlink_tlog(p)
    got = [(e["kind"], e["detail"]) for e in evs]
    assert got == [("flight mode", "STABILIZE"), ("flight mode", "LOITER"), ("event", "armed"),
                   ("autopilot message", "Bad compass health")]
    assert "receive time" in method


def test_check_fails_when_a_drone_flew_outside_the_radar_data(tmp_path, capsys):
    from domeware import check

    s = _session(tmp_path)
    late = pd.read_parquet(s.streams / "truth" / "B_SIMULATED.parquet")
    late["t_utc"] = late["t_utc"] + pd.Timedelta(hours=2)  # e.g. a drone clock left on local time
    late.to_parquet(s.streams / "truth" / "B_SIMULATED.parquet")
    assert check.run(s) == 1
    out = capsys.readouterr().out
    assert "FAIL  drone B_SIMULATED flew outside the radar data (+2.0 h" in out
    assert "A_SIMULATED flew outside" not in out  # A only starts before the first radar track: a WARN


def test_html_report_keeps_tables_lists_and_escapes(tmp_path):
    from domeware.report import to_html

    h = to_html("# Run <1>\n\n| a | b |\n|---|---|\n| **x** | `y` |\n\n- one\n- two\n\n![p](missing.png)\n", tmp_path)
    assert "<h1>Run &lt;1&gt;</h1>" in h and "<td><b>x</b></td><td><code>y</code></td>" in h
    assert "<li>one</li><li>two</li>" in h and "<img" not in h  # a missing picture is skipped, not broken


def test_merges_and_swaps_seen_from_track_states():
    from domeware.scorecard import merges_swaps

    t = pd.date_range(T0, periods=60, freq="1s")
    a = pd.DataFrame({"t_utc": t, "e_m": np.linspace(0, 600, 60), "n_m": 0.0, "u_m": 30.0, "h_rel_takeoff_m": 30.0})
    b = a.assign(n_m=200.0)
    # one radar track follows drone A for 30 s, then jumps to drone B: a swap
    tr = pd.DataFrame({"t_utc": t, "track_id": 1, "e_m": a["e_m"], "n_m": np.r_[np.zeros(30), np.full(30, 200.0)], "u_m": 30.0})
    assert merges_swaps({"A": a, "B": b}, tr, 50.0) == (0, 1)
    # the drones fly 10 m apart and the radar shows one track between them: a merge every second
    close = a.assign(n_m=10.0)
    one = pd.DataFrame({"t_utc": t, "track_id": 7, "e_m": a["e_m"], "n_m": 5.0, "u_m": 30.0})
    assert merges_swaps({"A": a, "B": close}, one, 50.0)[0] == 60


def test_dji_csv_height_column_and_same_file_names(tmp_path):
    from domeware.replay import unique_stem

    s = _session(tmp_path)
    d = pd.read_parquet(s.streams / "truth" / "A_SIMULATED.parquet").rename(columns={"h_rel_takeoff_m": "height_rel_takeoff_m"})
    (s.streams / "dji").mkdir()
    d.to_parquet(s.streams / "dji" / "C_SIMULATED_osd.parquet")
    assert len(runs.airborne_spans(s)) == 3  # the DJI CSV stream counts too
    assert "C_SIMULATED_osd" in set(events.build(s)["drone"])
    files = [tmp_path / "drone1" / "DJI_0001.SRT", tmp_path / "drone2" / "DJI_0001.SRT", tmp_path / "drone2" / "DJI_0002.SRT"]
    assert [unique_stem(f, files) for f in files] == ["drone1_DJI_0001", "drone2_DJI_0001", "DJI_0002"]


def test_no_radar_in_window_is_not_zero_seconds():
    from domeware.scorecard import _gap_window

    t = pd.Series(pd.date_range(T0, periods=5, freq="2s"))
    assert _gap_window(t, T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=2)) is None
    assert _gap_window(t, T0 - pd.Timedelta(seconds=30), T0 + pd.Timedelta(seconds=8)) == 30.0  # silence before data


def test_detection_share_by_range_and_kml(tmp_path):
    import xml.dom.minidom

    from domeware import kml, performance

    s = _session(tmp_path)
    for f in (s.streams / "truth").glob("*.parquet"):
        d = pd.read_parquet(f)
        d["lat_deg"], d["lon_deg"] = 65.17 + d["n_m"] / 111_000, 26.04 + d["e_m"] / 46_600
        d.to_parquet(f)
    tr = pd.read_parquet(s.streams / "radar" / "tracks.parquet")
    tr["lat_deg"], tr["lon_deg"], tr["alt_m"] = 65.17 + tr["n_m"] / 111_000, 26.04 + tr["e_m"] / 46_600, 100.0
    tr.to_parquet(s.streams / "radar" / "tracks.parquet")
    out = performance.build(s)
    sec = out["ground_range_m"]
    assert sec["seconds"].sum() > 200 and 0 < sec["share"].max() <= 1
    assert (s.views / "performance.png").exists()
    k = kml.build(s)
    xml.dom.minidom.parseString(k)  # valid XML
    assert k.count("<gx:Track>") >= 4 and "drone A_SIMULATED" in k
