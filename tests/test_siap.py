"""Stone Soup SIAP cross-check on SIMULATED tracks with known faults."""

import numpy as np
import pandas as pd

from domeware.siap import siap
from domeware.synthetic import radar_from_truth, truth_path


def test_perfect_radar_scores_perfect():
    truth = truth_path(seconds=60)
    r = siap({"A": truth}, radar_from_truth(truth, seed=1))
    assert r["completeness"] == 1.0 and r["spuriousness"] == 0.0 and r["ambiguity"] == 1.0
    assert r["position_accuracy_m"] < 0.5 and r["longest_segment"] == 1.0


def test_missed_drone_and_clutter_show_up():
    a = truth_path(seconds=60)
    b = a.assign(n_m=a["n_m"] + 2000.0)  # a second drone far away that the radar never tracks
    tracks = radar_from_truth(a, noise_m=2.0, seed=2)
    clutter = pd.DataFrame({"t_utc": tracks["t_utc"], "track_id": 99, "e_m": -3000.0, "n_m": 0.0, "u_m": 10.0})
    r = siap({"A": a, "B": b}, pd.concat([tracks, clutter], ignore_index=True))
    assert 0.4 < r["completeness"] < 0.6  # one of two drones
    assert 0.4 < r["spuriousness"] < 0.6  # one of two tracks is no drone


def test_track_number_change_is_counted():
    truth = truth_path(seconds=60)
    tracks = radar_from_truth(truth, seed=3)
    tracks.loc[tracks.index >= 30, "track_id"] = 2  # the radar drops track 1 and starts track 2 on the same drone
    r = siap({"A": truth}, tracks)
    assert r["track_number_changes"] > 0 and r["longest_segment"] < 0.8


def test_nothing_to_compare():
    assert siap({}, pd.DataFrame()) is None
    assert np.isfinite(siap({"A": truth_path(seconds=30)}, radar_from_truth(truth_path(seconds=30)))["position_accuracy_m"])
