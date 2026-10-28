"""Track-to-truth metrics from Stone Soup (Dstl, open source): SIAP, the Single Integrated Air Picture measures.

Why next to our own numbers: these are an independent, published implementation that test communities (CWA 18150,
NATO trials) already use, so a disputed figure can be shown with a method nobody here wrote.

How the data goes in: Stone Soup needs every track and truth on the same instants, and radar updates are
irregular. So everything is put on one 1 s grid: each drone's truth over its flight (missed seconds count against
completeness), each radar track only between its own updates (linear, never beyond its first or last update). A
track with a gap over 2 s is split there, so a silent radar is never filled in; that split counts as a track
number change, which a 2 s gap in a track is. Association: Stone Soup TrackToTruth, nearest truth within the gate for `confirm` consecutive updates,
ended after `misses` consecutive misses. Frame: ENU metres around the radar.

Metrics (Stone Soup's definitions, time-averaged over the window):
  completeness  share of drones that had a track          (target 1)
  ambiguity     tracks per tracked drone                  (target 1)
  spuriousness  share of tracks that were no drone        (target 0)
  position accuracy  mean 3D error of associated tracks, m (target 0)
  velocity accuracy  mean velocity error, m/s             (target 0)
  track number changes  per drone per update              (target 0)
  longest segment  share of the flight in one track       (target 1)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from domeware.compare import _interp_truth, _ns

SEC = 1_000_000_000
MAX_TRACK_GAP_S = 2.0
TITLES = {"SIAP Completeness": "completeness", "SIAP Ambiguity": "ambiguity", "SIAP Spuriousness": "spuriousness",
          "SIAP Position Accuracy": "position_accuracy_m", "SIAP Velocity Accuracy": "velocity_accuracy_mps",
          "SIAP Rate of Track Number Change": "track_number_changes", "SIAP Longest Track Segment": "longest_segment"}


def _naive(t: pd.Series) -> list:
    return list(pd.to_datetime(t, utc=True).dt.tz_convert(None).dt.to_pydatetime())


def _velocity(t_ns: np.ndarray, x: np.ndarray) -> np.ndarray:
    if len(t_ns) < 2:
        return np.zeros_like(x)
    return np.gradient(x, t_ns / 1e9, axis=0)


def siap(truths: dict[str, pd.DataFrame], tracks: pd.DataFrame, gate_m: float = 50.0, confirm: int = 3,
         misses: int = 2, airborne_m: float = 2.0) -> dict | None:
    """SIAP metrics for one window. None when there is no truth or no track to compare."""
    from stonesoup.dataassociator.tracktotrack import TrackToTruth
    from stonesoup.measures import Euclidean
    from stonesoup.metricgenerator.manager import MultiManager
    from stonesoup.metricgenerator.tracktotruthmetrics import SIAPMetrics
    from stonesoup.types.groundtruth import GroundTruthPath, GroundTruthState
    from stonesoup.types.state import State
    from stonesoup.types.track import Track

    if tracks.empty or not truths:
        return None
    tr = tracks.dropna(subset=["e_m", "n_m", "u_m"]).sort_values("t_utc")
    paths = []
    for name, d in truths.items():
        air = d[d["h_rel_takeoff_m"] > airborne_m]
        if len(air) < 2:
            continue
        a, b = _ns(air["t_utc"])[[0, -1]]
        times = np.arange(-(-a // SEC) * SEC, b + 1, SEC, dtype=np.int64)  # whole UTC seconds
        pos = _interp_truth(d, pd.Series(pd.to_datetime(times, utc=True)))
        vel = _velocity(times, pos)
        sv = np.column_stack([pos[:, 0], vel[:, 0], pos[:, 1], vel[:, 1], pos[:, 2], vel[:, 2]])
        stamps = _naive(pd.Series(pd.to_datetime(times, utc=True)))
        paths.append(GroundTruthPath([GroundTruthState(v.reshape(-1, 1), timestamp=t) for v, t in zip(sv, stamps, strict=True)],
                                     id=name))
    if not paths:
        return None
    stone_tracks = []
    for tid, g in tr.groupby("track_id"):
        t_ns = _ns(g["t_utc"])
        seg = np.r_[0, np.cumsum(np.diff(t_ns) > MAX_TRACK_GAP_S * SEC)]
        for k in np.unique(seg):
            ts, gg = t_ns[seg == k], g[seg == k]
            times = np.arange(-(-ts[0] // SEC) * SEC, ts[-1] + 1, SEC, dtype=np.int64)
            if len(times) == 0:
                continue  # a segment shorter than one whole second
            pos = np.column_stack([np.interp(times, ts, gg[c].to_numpy(float)) for c in ("e_m", "n_m", "u_m")])
            vcols = ["v_e_mps", "v_n_mps", "v_u_mps"]
            if set(vcols) <= set(gg.columns) and gg[vcols].notna().all().all():
                vel = np.column_stack([np.interp(times, ts, gg[c].to_numpy(float)) for c in vcols])
            else:
                vel = _velocity(times, pos)
            sv = np.column_stack([pos[:, 0], vel[:, 0], pos[:, 1], vel[:, 1], pos[:, 2], vel[:, 2]])
            stamps = _naive(pd.Series(pd.to_datetime(times, utc=True)))
            stone_tracks.append(Track([State(v.reshape(-1, 1), timestamp=t) for v, t in zip(sv, stamps, strict=True)],
                                      id=f"{tid}" if k == 0 else f"{tid}.{k}"))
    gen = SIAPMetrics(position_measure=Euclidean((0, 2, 4)), velocity_measure=Euclidean((1, 3, 5)))
    manager = MultiManager([gen], associator=TrackToTruth(association_threshold=gate_m, consec_pairs_confirm=confirm,
                                                          consec_misses_end=misses))
    manager.add_data({"groundtruth_paths": paths, "tracks": stone_tracks})
    metrics = manager.generate_metrics()
    out = {"method": f"Stone Soup SIAPMetrics + TrackToTruth on a 1 s grid (gate {gate_m:g} m, confirm {confirm}, "
                     f"end after {misses} misses; tracks split at gaps over {MAX_TRACK_GAP_S:g} s)",
           "drones": len(paths), "tracks": len(stone_tracks)}
    for m in metrics[gen.generator_name].values():
        key = TITLES.get(m.title)
        if key:
            v = m.value
            out[key] = None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 3)
    return out
