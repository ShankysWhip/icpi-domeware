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
from domeware.stats import clopper_pearson, cluster_bootstrap_share  # noqa: E402


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


def flight_table(s: Session, sec: pd.DataFrame) -> pd.DataFrame:
    """One row per flight (drone x run from runs.csv; drone x session without runs): seconds and seconds tracked."""
    from domeware import runs as R

    sec = sec.assign(run="session")
    df = R.load(s) if (s.root / "runs.csv").exists() else pd.DataFrame()
    for r in df[df["part"] == "RUN"].itertuples() if len(df) else []:
        sec.loc[(sec["t_utc"] >= r.start_utc) & (sec["t_utc"] <= r.end_utc), "run"] = r.run_id
    return sec.groupby(["drone", "run"])["detected"].agg(seconds="size", detected="sum").reset_index()


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
        ci = [clopper_pearson(int(k), int(n)) for k, n in zip(t["detected"], t["seconds"], strict=True)]
        t["ci95_low"], t["ci95_high"] = [c[0] for c in ci], [c[1] for c in ci]
        t["bin"] = t["bin_lo"].map(lambda v, st=step: f"{v:.0f}-{v + st:.0f}")
        t[["bin", "seconds", "detected", "share", "ci95_low", "ci95_high"]].to_csv(s.views / f"performance_{col}.csv", index=False)
        out[col] = t
        bars = ax.bar(t["bin_lo"] + step / 2, t["share"] * 100, width=step * 0.85,
                      color=[(0.14, 0.34, 0.65, 0.35 if n < 10 else 0.9) for n in t["seconds"]])
        ax.errorbar(t["bin_lo"] + step / 2, t["share"] * 100, yerr=[(t["share"] - t["ci95_low"]) * 100,
                    (t["ci95_high"] - t["share"]) * 100], fmt="none", ecolor="#1d232a", elinewidth=0.8, capsize=2)
        for bar, n in zip(bars, t["seconds"], strict=True):
            ax.annotate(f"{n} s", (bar.get_x() + bar.get_width() / 2, bar.get_height()), ha="center", va="bottom", fontsize=7)
        ax.set_ylim(0, 110)
        ax.set_xlabel(label)
        ax.set_ylabel("airborne seconds with a radar track (%)")
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"Radar detection share by range and height ({sec['drone'].nunique()} drone(s), {len(sec)} drone-seconds; "
                 "pale bars rest on < 10 s; whiskers: 95 % Clopper-Pearson per bin, optimistic: seconds are not independent)",
                 fontsize=9)
    fig.savefig(s.views / "performance.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    by_drone = sec.groupby("drone")["detected"].agg(["size", "mean"]).rename(columns={"size": "seconds", "mean": "share"})
    # Honest intervals: a flight (drone x run) is the independent unit, not a second
    flights = flight_table(s, sec)
    per_flight = clopper_pearson(int((flights["detected"] > 0).sum()), len(flights))
    boot = cluster_bootstrap_share(flights["detected"].tolist(), flights["seconds"].tolist())
    s.manifest["performance"] = {"png": "views/performance.png", "drone_seconds": int(len(sec)),
                                 "share_overall": round(float(sec["detected"].mean()), 3),
                                 "flights": int(len(flights)), "flights_with_track": int((flights["detected"] > 0).sum()),
                                 "ci95_flights_with_track": [round(per_flight[0], 3), round(per_flight[1], 3)],
                                 "ci95_overall": None if boot is None else [round(boot[0], 3), round(boot[1], 3)],
                                 "ci_method": "share: cluster bootstrap over flights (drone x run); flights with a track: "
                                              "Clopper-Pearson",
                                 "share_by_drone": {k: round(float(v), 3) for k, v in by_drone["share"].items()}}
    s.note(f"performance: {len(sec)} drone-seconds, tracked {sec['detected'].mean():.0%} -> views/performance.png")
    s.save()
    return out


def timeline(s: Session, gate_m: float = 50.0) -> pd.DataFrame | None:
    """views/timeline_1s.csv: one row per second, one column group per drone, plus weather and drone events.
    Opens in Excel or PlotJuggler; the quickest way to line things up by eye."""
    from domeware.compare import _ns
    from domeware.scorecard import _load, association_grid, measure

    truths, tracks = _load(s)
    if not truths:
        return None
    a = min(d["t_utc"].iloc[0] for d in truths.values()).floor("1s")
    b = max(d["t_utc"].iloc[-1] for d in truths.values()).ceil("1s")
    out = pd.DataFrame({"t_utc": pd.date_range(a, b, freq="1s").as_unit("ns")})
    grid = association_grid(measure(truths, tracks, a, b, gate_m), a, b) if len(tracks) else pd.DataFrame()
    x = _ns(out["t_utc"])
    for name, d in truths.items():
        tt = _ns(d["t_utc"])
        inside = (x >= tt[0]) & (x <= tt[-1])
        for col, src in (("height_m", "h_rel_takeoff_m"), ("e_m", "e_m"), ("n_m", "n_m")):
            out[f"{name}.{col}"] = np.where(inside, np.interp(x, tt, d[src].to_numpy()), np.nan).round(1)
        out[f"{name}.ground_range_m"] = np.hypot(out[f"{name}.e_m"], out[f"{name}.n_m"]).round(1)
        if len(grid):
            mine = grid[grid["drone"] == name].assign(t_utc=lambda f: f["t_utc"].dt.as_unit("ns")).set_index("t_utc")
            for col in ("track_id", "err_h_m", "class_name", "alarm"):
                if col in mine:
                    v = out["t_utc"].map(mine[col])
                    out[f"{name}.radar_{col}"] = (v.round(1) if col == "err_h_m" else v.astype("Int64") if col == "track_id" else v)
    wx = s.streams / "weather" / "fmi_nearest.parquet"
    if wx.exists():
        w = pd.read_parquet(wx)
        w["t_utc"] = pd.to_datetime(w["t_utc"], utc=True).dt.as_unit("ns")
        keep = [c for c in ("wind_ms", "gust_ms", "temp_c", "visibility_m") if c in w]
        if keep:
            out = pd.merge_asof(out, w[["t_utc", *keep]].sort_values("t_utc"), on="t_utc", direction="backward",
                                tolerance=pd.Timedelta("20min")).rename(columns={c: f"weather.{c}" for c in keep})
    on = s.streams / "weather" / "onsite_vaisala.parquet"
    if on.exists():
        w = pd.read_parquet(on)
        w["t_utc"] = pd.to_datetime(w["t_utc"], utc=True).dt.as_unit("ns")
        keep = [c for c in ("wind_ms", "gust_ms", "wind_dir_deg", "temp_c", "pressure_hpa", "rh_pct", "precip_mmh") if c in w]
        out = pd.merge_asof(out, w[["t_utc", *keep]].sort_values("t_utc"), on="t_utc", direction="backward",
                            tolerance=pd.Timedelta("2min")).rename(columns={c: f"onsite.{c}" for c in keep})
    de = s.streams / "events" / "drone_events.parquet"
    if de.exists():
        ev = pd.read_parquet(de)
        ev["sec"] = pd.to_datetime(ev["t_utc"], utc=True).dt.floor("1s").dt.as_unit("ns")
        ev["txt"] = ev["drone"] + ": " + ev["kind"] + " " + ev["detail"].astype(str)
        out["events"] = out["t_utc"].map(ev.groupby("sec")["txt"].agg("; ".join))
    s.views.mkdir(parents=True, exist_ok=True)
    out.assign(t_utc=out["t_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_csv(s.views / "timeline_1s.csv", index=False)
    s.note(f"timeline: views/timeline_1s.csv ({len(out)} seconds, {len(out.columns)} columns)")
    return out
