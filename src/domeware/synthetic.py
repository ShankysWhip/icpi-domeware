"""SIMULATED radar output from a drone truth path, with known errors, for acceptance tests (decision doc, Phase 2).

One unknown at a time: processing latency, radar clock offset, a fixed position bias, random noise, missed updates.
Everything made here is labelled SIMULATED and carries its seed, so it can never pass as measured data.

Latency and clock offset both move the radar's time stamps. From positions alone they show up as one time shift and
cannot be told apart; separating them needs the radar's send time and a synced clock, or a timing manoeuvre.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def radar_from_truth(truth: pd.DataFrame, *, update_s: float = 1.0, latency_s: float = 0.0, clock_offset_s: float = 0.0,
                     bias_enu_m: tuple[float, float, float] = (0.0, 0.0, 0.0), noise_m: float = 0.0,
                     miss_share: float = 0.0, track_id: int = 1, seed: int = 0) -> pd.DataFrame:
    """Radar track states for one drone. A state measured at true time t is stamped t + latency + clock offset."""
    rng = np.random.default_rng(seed)
    t0, t1 = truth["t_utc"].iloc[0], truth["t_utc"].iloc[-1]
    t_true = pd.date_range(t0, t1, freq=pd.Timedelta(seconds=update_s))
    tt = pd.to_datetime(truth["t_utc"], utc=True).dt.as_unit("ns").astype("int64").to_numpy()
    x = t_true.as_unit("ns").asi8
    pos = np.column_stack([np.interp(x, tt, truth[c].to_numpy()) for c in ("e_m", "n_m", "u_m")])
    pos = pos + np.asarray(bias_enu_m) + rng.normal(0.0, noise_m, pos.shape) * (noise_m > 0)
    keep = rng.random(len(x)) >= miss_share
    out = pd.DataFrame({"t_utc": (t_true + pd.Timedelta(seconds=latency_s + clock_offset_s))[keep],
                        "track_id": track_id, "e_m": pos[keep, 0], "n_m": pos[keep, 1], "u_m": pos[keep, 2]})
    out["range_m"] = np.hypot(out["e_m"], out["n_m"])
    out["class_name"], out["alarm"] = "DRONE", True
    out["source"] = f"SIMULATED radar (seed {seed}, latency {latency_s} s, clock offset {clock_offset_s} s, bias {bias_enu_m} m, noise {noise_m} m)"
    return out.reset_index(drop=True)


def truth_path(seconds: int = 180, speed_mps: float = 15.0, height_m: float = 40.0, hover: bool = False) -> pd.DataFrame:
    """A SIMULATED drone flying a 600 m circle around a point 1.5 km from the radar (or hovering there)."""
    t = pd.date_range("2026-10-28 09:00:00", periods=seconds * 10 + 1, freq="100ms", tz="UTC")
    s = np.arange(len(t)) / 10.0
    ang = 0.0 * s if hover else speed_mps * s / 600.0
    return pd.DataFrame({"t_utc": t, "e_m": 1500 + 600 * np.cos(ang), "n_m": 600 * np.sin(ang),
                         "u_m": height_m, "h_rel_takeoff_m": height_m})
