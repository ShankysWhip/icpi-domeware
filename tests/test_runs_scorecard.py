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
    assert it["BD9"]["value"] == "0"  # UNKNOWN -> DRONE is a gain, not a drop
    assert it["BA6"]["value"] == "30.0"  # alarm at 09:01:20, closest approach inside P3 at 09:01:50
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


def test_timeline_one_row_per_second_with_each_drone(tmp_path):
    from domeware import performance

    s = _session(tmp_path)
    t = performance.timeline(s)
    assert len(t) == 121 and t["t_utc"].diff().dropna().dt.total_seconds().eq(1).all()
    assert t["A_SIMULATED.radar_track_id"].dropna().eq(1).all() and t["B_SIMULATED.radar_track_id"].dropna().eq(2).all()
    assert (s.views / "timeline_1s.csv").exists()


def test_run_validity_lists_every_reason(tmp_path):
    s = _session(tmp_path)
    runs.ensure(s)
    [card] = scorecard.run(s)
    assert card["validity"]["valid"] and card["validity"]["drone_seconds"] > 30
    d = pd.read_parquet(s.streams / "truth" / "B_SIMULATED.parquet")
    d.drop(index=range(40, 50)).to_parquet(s.streams / "truth" / "B_SIMULATED.parquet")  # 11 s with no truth in the air
    [card] = scorecard.run(s)
    assert not card["validity"]["valid"]
    assert any("B_SIMULATED: truth lost for 11.0 s" in r for r in card["validity"]["reasons"])


def test_notes_per_author_merge_in_time_order(tmp_path):
    from domeware import notes

    s = _session(tmp_path)
    runs.ensure(s)
    notes.add(s, "bird flock east of the radar", at="09:01:10", who="Aino")
    notes.add(s, "drone B battery swap", at="09:00:30", who="Niklas")
    df = notes.load(s)
    assert df["author"].tolist() == ["Niklas", "Aino"]
    assert df["t_utc"].iloc[0] == pd.Timestamp("2026-10-28 09:00:30", tz="UTC")
    assert sorted(p.name for p in (s.root / "notes").glob("*.jsonl")) == ["Aino.jsonl", "Niklas.jsonl"]


def test_seal_sync_never_deletes_and_merges_notes(tmp_path):
    from domeware import custody, notes

    src = tmp_path / "in"
    src.mkdir()
    (src / "a_SIMULATED.SRT").write_text("x")
    s = Session(tmp_path / "S1")
    s.ingest([src])
    notes.add(s, "first", at="2026-10-28T09:00:00Z", who="Aino")
    custody.seal(s)
    ssd = tmp_path / "ssd"
    assert custody.sync(s, ssd) == []
    (ssd / "S1" / "keep_me.txt").write_text("someone else's file")
    notes.add(s, "second", at="2026-10-28T09:05:00Z", who="Aino")
    custody.seal(s)
    assert custody.sync(s, ssd) == []
    assert (ssd / "S1" / "keep_me.txt").exists()  # never deletes at the destination
    assert len((ssd / "S1" / "notes" / "Aino.jsonl").read_text().splitlines()) == 2
    raw = next((ssd / "S1" / "raw").rglob("*.SRT"))
    raw.chmod(0o666)
    raw.write_text("tampered")
    assert any(p.startswith("changed: raw/") for p in custody.check_seal(ssd / "S1"))


def test_team_scorecard_kept_and_shown_next_to_measured(tmp_path):
    from domeware import report

    s = _session(tmp_path)
    card = tmp_path / "SIM-1.json"
    card.write_text(json.dumps({"format": "icpi-run-scorecard/2", "run": {"run_id": "SIM-1", "date": "2026-10-28"},
                                "phases": {"P2": {"used": "Included", "start": "09:00:40", "end": "09:01:20"}},
                                "items": {"BD1": {"result": "Fail", "value": "900", "note": "seen late", "target": "≥ 1500"},
                                          "R1": {"result": "Pass", "value": "20"}}}))
    runs.import_card(s, card)
    assert (s.root / "cards" / "SIM-1.json").exists()
    scorecard.run(s)
    text = report.build(s)
    assert "## Team scorecard: SIM-1" in text and "Red team wins from Blue fails: **1**" in text
    assert "| BD1 | 900 | seen late |" in text  # the fail and its reason
    assert "| BD1 | Fail | 900 |" in text  # team entry next to the measured value


def test_review_fixes_hold(tmp_path):
    """Second review: each case below failed before its fix."""
    from domeware import custody, images
    from domeware.replay import unique_stem

    # DJI cards share DCIM/100MEDIA: the drone folder must survive in the name
    a = tmp_path / "S" / "raw" / "dji_srt" / "droneA" / "DCIM" / "100MEDIA" / "DJI_0001.SRT"
    b = tmp_path / "S" / "raw" / "dji_srt" / "droneB" / "DCIM" / "100MEDIA" / "DJI_0001.SRT"
    assert unique_stem(a, [a, b]) != unique_stem(b, [a, b])
    # a radar that tracked nothing, with no status stream: a 0 % run, not a void one
    s = _session(tmp_path)
    tr = pd.read_parquet(s.streams / "radar" / "tracks.parquet")
    tr.assign(t_utc=tr["t_utc"] + pd.Timedelta(hours=1)).to_parquet(s.streams / "radar" / "tracks.parquet")  # nothing in the run
    runs.ensure(s)
    [card] = scorecard.run(s)
    assert card["validity"]["valid"] and any("liveness unknown" in w for w in card["validity"]["warnings"])
    # a hand-written part survives the next card import
    (s.root / "runs.csv").write_text(s.root.joinpath("runs.csv").read_text() + "R1,SETUP,2026-10-28T08:59:00Z,2026-10-28T09:00:00Z,\n")
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"run": {"run_id": "R1", "date": "2026-10-28"},
                             "phases": {"P1": {"start": "09:00:10", "end": "09:00:40"}}}))
    runs.import_card(s, c)
    assert "SETUP" in runs.load(s)["part"].tolist()
    # sync keeps the destination's runs.csv rows and its own card
    ssd = tmp_path / "ssd"
    custody.sync(s, ssd)
    with open(ssd / s.root.name / "runs.csv", "a") as f:
        f.write("R1,P4,2026-10-28T09:01:50Z,2026-10-28T09:02:00Z,added on the shared disk\n")
    (ssd / s.root.name / "cards" / "R1.json").write_text("{}")
    problems = custody.sync(s, ssd)
    assert "P4" in pd.read_csv(ssd / s.root.name / "runs.csv")["part"].tolist()
    assert (ssd / s.root.name / "cards" / "R1.json").read_text() == "{}"
    assert any("cards/R1.json" in p for p in problems)
    # a typo in a clock name leaves the picture out instead of crashing
    (s.raw / "image" / "PR200").mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (4, 4)).save(s.raw / "image" / "PR200" / "Screenshot_2026-10-28_11-00-00.png")
    s.manifest["raw"].append({"path": "raw/image/PR200/Screenshot_2026-10-28_11-00-00.png", "role": "image", "sha256": "x", "size": 1})
    (s.root / "image_clocks.csv").write_text("match,clock\nPR200,Europe/Helsinky\n")
    [p] = images.pictures(s)
    assert p["t_utc"] is None and p["method"].startswith("left out")
