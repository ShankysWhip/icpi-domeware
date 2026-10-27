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
