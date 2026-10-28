"""Truth adapters and radar-vs-truth comparison. All data here is SIMULATED."""

from pathlib import Path

import numpy as np
import pandas as pd

from domeware.adapters.truth import read_dji_srt
from domeware.compare import compare_one

FIX = Path(__file__).parent / "fixtures"


def test_srt_local_time_to_utc_dst_aware(tmp_path):
    p = tmp_path / "a.SRT"
    # 21 Oct = summer time (UTC+3); 28 Oct = winter time (UTC+2). Old-model spelling 'longtitude' + [altitude].
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\n2026-10-21 13:29:35.500\n[latitude: 65.1 ] [longtitude: 26.0] "
                 "[altitude: 12.5]\n\n2\n00:00:02,000 --> 00:00:03,000\n2026-10-28 13:29:35.500\n[latitude: 65.1] "
                 "[longitude: 26.0] [rel_alt: 20.0 abs_alt: 110.0]\n")
    df, meta = read_dji_srt(p)
    assert list(df["t_utc"].dt.strftime("%H:%M:%S.%f")) == ["10:29:35.500000", "11:29:35.500000"]
    assert df["h_rel_takeoff_m"].tolist() == [12.5, 20.0]


def test_srt_without_position_fails_loudly(tmp_path):
    p = tmp_path / "b.SRT"
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\n2026-10-21 13:29:35\n[iso : 100]\n")
    try:
        read_dji_srt(p)
    except ValueError as e:
        assert "Video Captions" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_compare_recovers_known_offset_with_mixed_time_units():
    t = pd.date_range("2026-10-21 10:00:00", periods=60, freq="1s", tz="UTC")
    e = np.linspace(-500, 500, 60)
    truth = pd.DataFrame({"t_utc": t.as_unit("us"), "e_m": e, "n_m": 0.0, "u_m": 30.0, "h_rel_takeoff_m": 30.0})
    tracks = pd.DataFrame({"t_utc": t[5:].as_unit("ns"), "track_id": 1, "e_m": e[5:], "n_m": 8.0, "u_m": 30.0,
                           "class_name": "DRONE", "alarm": True, "range_m": np.abs(e[5:])})
    r = compare_one(truth, tracks, gate_m=50.0, airborne_m=2.0)
    assert r["drone_track_ids"] == [1]
    assert r["error_horizontal_m"]["median"] == 8.0
    assert r["delay_airborne_to_first_track_s"] == 5.0


def test_simulated_fixture_is_labelled():
    assert all("SIMULATED" in p.name for p in FIX.glob("*"))


def test_rtklib_pos_gpst_and_n2000(tmp_path):
    import pytest

    from domeware.adapters import truth as T

    if not (T.PROJ_DIR / "fi_nls_fin2023n2000.tif").exists():
        pytest.skip("vendored N2000 grid not present")
    p = tmp_path / "x.pos"
    p.write_text("% program   : RTKLIB\n%  GPST                  latitude(deg) longitude(deg)  height(m)   Q  ns\n"
                 "2026/10/21 10:30:18.000   65.176667000   26.043982000   115.000   1  14\n")
    df, meta = T.read_rtklib_pos(p)
    assert str(df["t_utc"].iloc[0]) == "2026-10-21 10:30:00+00:00"  # GPST - 18 s
    assert 15 < 115.0 - df["h_n2000_m"].iloc[0] < 20  # geoid undulation in northern Finland is ~17-18 m


def test_nls_layers_move_about_0_9_m_north_east_in_oulu():
    from domeware.datum import nls_layer_shift

    e, n, u = nls_layer_shift(65.1767, 26.044, 2026.8)
    assert 0.6 < e < 0.75 and 0.5 < n < 0.65 and u == 0.0  # ~2.5 cm/yr since 1989, towards north-east


def test_radar_time_shift_and_first_plot_found():
    from domeware.compare import best_time_shift, first_plot

    t = pd.date_range("2026-10-28 09:00:00", periods=121, freq="1s", tz="UTC")
    e = np.linspace(-900, 900, 121)  # 15 m/s
    truth = pd.DataFrame({"t_utc": t, "e_m": e, "n_m": 0.0, "u_m": 40.0, "h_rel_takeoff_m": 40.0})
    # radar reports the drone's position 0.8 s after it was there (stamped late)
    tr = pd.DataFrame({"t_utc": t[10:110] + pd.Timedelta(seconds=0.8), "e_m": e[10:110], "n_m": 0.0, "u_m": 40.0})
    r = best_time_shift(truth, tr)
    assert r["shift_s"] == -0.8 and r["error_h_at_shift_m"] < 0.5 < r["error_h_at_zero_m"]
    plots = pd.DataFrame({"t_utc": t[[3, 6]], "e_m": [5000.0, e[6]], "n_m": 0.0, "u_m": 40.0, "range_m": [5000.0, 812.0]})
    when, rng = first_plot(truth, plots, 50.0, t[0])
    assert when == t[6] and rng == 812.0  # the far plot at t[3] is something else
    slow = truth.assign(e_m=np.linspace(0, 10, 121))
    assert best_time_shift(slow, tr.assign(e_m=np.linspace(0, 10, 100))) is None  # hovering: time cannot be measured
