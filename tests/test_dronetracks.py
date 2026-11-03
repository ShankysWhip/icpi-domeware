"""The radar's own drone tracks, chained. All data here is SIMULATED."""
import pandas as pd

from domeware import dronetracks


def _tracks(spec):
    rows = []
    for tid, cls, a, b in spec:
        for t in pd.date_range(f"2026-10-29 {a}", f"2026-10-29 {b}", freq="1s", tz="UTC"):
            rows.append({"t_utc": t, "track_id": tid, "class_name": cls})
    return pd.DataFrame(rows)


def test_three_laps_come_back_as_one_chain_with_the_breaks_between():
    tr = _tracks([(1, "DRONE", "13:47:00", "13:51:00"), (2, "DRONE", "13:51:20", "13:55:00"),
                  (3, "UNCLASSIFIED", "13:50:00", "13:58:00"), (4, "DRONE", "13:55:10", "13:59:00")])
    flight = (pd.Timestamp("2026-10-29 13:46:00", tz="UTC"), pd.Timestamp("2026-10-29 14:00:00", tz="UTC"))
    c = dronetracks.chain(tr, flight)
    assert [r["track_id"] for r in c["tracks"]] == [1, 2, 4]  # the unclassified track is not the drone
    assert [r["gap_s"] for r in c["tracks"]] == [None, 20.0, 10.0]
    assert c["held_s"] == 240 + 220 + 230 and c["share"] == round(690 / 840, 3) and c["share_pct"] == 82
    assert "82 % of the logged flight; picked up again after 20 s, 10 s" in dronetracks.describe(c)


def test_overlapping_drone_tracks_count_once_and_no_drone_means_no_chain():
    c = dronetracks.chain(_tracks([(1, "DRONE", "12:00:00", "12:01:00"), (2, "DRONE", "12:00:30", "12:01:30")]))
    assert c["held_s"] == 90 and c["tracks"][1]["gap_s"] == 0.0 and c["share"] is None
    assert dronetracks.chain(_tracks([(5, "OTHER", "12:00:00", "12:00:10")])) is None
