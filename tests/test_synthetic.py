"""Synthetic acceptance (decision doc, Phase 2): inject one known error at a time into SIMULATED radar output and
check that domeware recovers it within a tolerance set here, before looking at the result."""

import numpy as np
import pytest

from domeware.compare import best_time_shift, compare_one
from domeware.synthetic import radar_from_truth, truth_path

TOL_SHIFT_S = 0.1  # the search step
TOL_BIAS_M = 1.0


@pytest.mark.parametrize("latency", [0.3, 0.8, 1.5])
def test_latency_is_recovered(latency):
    truth = truth_path()
    r = best_time_shift(truth, radar_from_truth(truth, latency_s=latency, noise_m=2.0, seed=1))
    assert abs(r["shift_s"] + latency) <= TOL_SHIFT_S


def test_latency_and_clock_offset_add_up_and_are_reported_as_one_shift():
    truth = truth_path()
    r = best_time_shift(truth, radar_from_truth(truth, latency_s=0.5, clock_offset_s=-1.2, noise_m=1.0, seed=2))
    assert abs(r["shift_s"] - 0.7) <= TOL_SHIFT_S  # -(0.5 - 1.2): positions alone cannot split the two


def test_fixed_bias_and_latency_are_both_recovered_when_the_drone_turns():
    truth = truth_path()
    r = best_time_shift(truth, radar_from_truth(truth, latency_s=0.6, bias_enu_m=(6.0, -4.0, 0.0), noise_m=2.0, seed=3))
    assert r["method"].startswith("bias-free")
    assert abs(r["shift_s"] + 0.6) <= TOL_SHIFT_S
    assert np.allclose(r["bias_en_m"], [6.0, -4.0], atol=TOL_BIAS_M)


def test_hovering_drone_gives_no_time_shift():
    truth = truth_path(hover=True)
    assert best_time_shift(truth, radar_from_truth(truth, latency_s=0.8, seed=4)) is None  # not identifiable


def test_noise_and_missed_updates_keep_the_error_honest():
    truth = truth_path()
    radar = radar_from_truth(truth, noise_m=5.0, miss_share=0.3, seed=5)
    r = compare_one(truth, radar, gate_m=50.0, airborne_m=2.0)
    assert 4.0 < r["error_horizontal_m"]["median"] < 9.0  # median of |2-D normal, sigma 5| is ~5.9 m
    assert 0.9 < r["tracked_share_of_flight"] <= 1.0  # a 30 % miss rate at 1 Hz still leaves no 2 s hole most of the time
    assert all("SIMULATED" in s for s in radar["source"])
