"""How well the radar saw the drones, as the classic C-UAS curves -> views/performance.png + performance_*.csv.

Detection share here = the share of airborne seconds in which the drone had its own radar track (1 s grid, one
drone per track, same gate as `dw compare`). It is binned by the drone's ground range from the radar and by its
height above take-off. Every bin shows how many drone-seconds it rests on: a bin with few seconds says little.
This is a measured share for these flights, not a probability of detection for the radar in general.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from domeware.session import Session  # noqa: E402


def seconds_table(s: Session, gate_m: float = 50.0) -> pd.DataFrame:
    """One row per drone per airborne second: range, height, detected. Uses the whole session span."""
    from domeware.compare import _ns
    from domeware.scorecard import _load, association_grid, measure

    truths, tracks = _load(s)
    if not truths:
        return pd.DataFrame()
    a = min(d["t_utc"].iloc[0] for d in truths.values())
    b = max(d["t_utc"].iloc[-1] for d in truths.values())
    m = measure(truths, tracks, a, b, gate_m) if len(tracks) else {"per": {k: {"truth": d, "on": d.iloc[0:0]} for k, d in truths.items()}}
    g = association_grid(m, a, b)
    if g.empty:
        return g
    rows = []
    for name, part in g.groupby("drone"):
        d = truths[name]
        tt, x = _ns(d["t_utc"]), _ns(part["t_utc"])  # Parquet may hold us or ns: never astype("int64") directly
        e, n = (np.interp(x, tt, d[c].to_numpy()) for c in ("e_m", "n_m"))
        h = np.interp(x, tt, d["h_rel_takeoff_m"].to_numpy())
        rows.append(pd.DataFrame({"t_utc": part["t_utc"].to_numpy(), "drone": name, "ground_range_m": np.hypot(e, n),
                                  "height_m": h, "detected": part["detected"].to_numpy()}))
    return pd.concat(rows, ignore_index=True)


def _bins(x: pd.Series, step: float) -> pd.Series:
    lo = np.floor(x / step) * step
    return lo.map(lambda v: f"{v:.0f}-{v + step:.0f}")


def build(s: Session, gate_m: float = 50.0) -> dict | None:
    sec = seconds_table(s, gate_m)
    if sec.empty:
        return None
    s.views.mkdir(parents=True, exist_ok=True)
    rmax = float(sec["ground_range_m"].max())
    rstep = 100.0 if rmax <= 1500 else 250.0 if rmax <= 4000 else 500.0
    hstep = 5.0 if sec["height_m"].max() <= 60 else 20.0
    out = {}
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    for ax, col, step, label in ((axes[0], "ground_range_m", rstep, "ground range from radar (m)"),
                                 (axes[1], "height_m", hstep, "drone height above take-off (m)")):
        lo = np.floor(sec[col].clip(lower=0) / step) * step
        t = sec.assign(bin_lo=lo).groupby("bin_lo").agg(seconds=("detected", "size"), detected=("detected", "sum")).reset_index()
        t["share"] = t["detected"] / t["seconds"]
        t["bin"] = t["bin_lo"].map(lambda v, st=step: f"{v:.0f}-{v + st:.0f}")
        t[["bin", "seconds", "detected", "share"]].to_csv(s.views / f"performance_{col}.csv", index=False)
        out[col] = t
        bars = ax.bar(t["bin_lo"] + step / 2, t["share"] * 100, width=step * 0.85,
                      color=[(0.14, 0.34, 0.65, 0.35 if n < 10 else 0.9) for n in t["seconds"]])
        for bar, n in zip(bars, t["seconds"], strict=True):
            ax.annotate(f"{n} s", (bar.get_x() + bar.get_width() / 2, bar.get_height()), ha="center", va="bottom", fontsize=7)
        ax.set_ylim(0, 110)
        ax.set_xlabel(label)
        ax.set_ylabel("airborne seconds with a radar track (%)")
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"Radar detection share by range and height ({sec['drone'].nunique()} drone(s), {len(sec)} drone-seconds; "
                 "pale bars rest on < 10 s)", fontsize=10)
    fig.savefig(s.views / "performance.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    by_drone = sec.groupby("drone")["detected"].agg(["size", "mean"]).rename(columns={"size": "seconds", "mean": "share"})
    s.manifest["performance"] = {"png": "views/performance.png", "drone_seconds": int(len(sec)),
                                 "share_overall": round(float(sec["detected"].mean()), 3),
                                 "share_by_drone": {k: round(float(v), 3) for k, v in by_drone["share"].items()}}
    s.note(f"performance: {len(sec)} drone-seconds, tracked {sec['detected'].mean():.0%} -> views/performance.png")
    s.save()
    return out
