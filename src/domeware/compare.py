"""Radar tracks vs drone truth: who saw the drone, when, and how accurately.

Association rule (stated in every report): a radar track belongs to the drone if the median 3D distance between its
states and the truth position at the same UTC time is below the gate (default 50 m). Truth is interpolated to each
track state's own time (time of validity). Vertical numbers depend on the radar's altitude datum (unverified until
the radar owner confirms it), so the horizontal error is the primary figure.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from domeware.replay import truth_files  # noqa: E402
from domeware.session import Session  # noqa: E402


def _ns(t: pd.Series) -> np.ndarray:
    """UTC times as int64 nanoseconds. Never .astype('int64') directly: Parquet may hold us or ns units."""
    return pd.to_datetime(t, utc=True).dt.as_unit("ns").astype("int64").to_numpy()


def _interp_truth(truth: pd.DataFrame, t: pd.Series) -> np.ndarray:
    tt = _ns(truth["t_utc"])
    x = _ns(t)
    out = np.column_stack([np.interp(x, tt, truth[c].to_numpy()) for c in ("e_m", "n_m", "u_m")])
    out[(x < tt[0]) | (x > tt[-1])] = np.nan  # no extrapolation outside the truth log
    return out


def _secs(a, b) -> float | None:
    return None if a is None or b is None else round((b - a).total_seconds(), 2)


def compare_one(truth: pd.DataFrame, tracks: pd.DataFrame, gate_m: float, airborne_m: float) -> dict:
    truth = truth.dropna(subset=["e_m", "n_m", "u_m"]).sort_values("t_utc")
    if truth.empty:
        raise ValueError("truth has no positions in the radar frame (no DEM when converted): run dw env, then dw convert")
    air =truth[truth["h_rel_takeoff_m"] > airborne_m]
    t_air = air["t_utc"].iloc[0] if len(air) else truth["t_utc"].iloc[0]
    t_land = air["t_utc"].iloc[-1] if len(air) else truth["t_utc"].iloc[-1]
    tr = tracks.copy()
    tp = _interp_truth(truth, tr["t_utc"])
    d = tr[["e_m", "n_m", "u_m"]].to_numpy() - tp
    tr["err_h_m"], tr["err_v_m"] = np.hypot(d[:, 0], d[:, 1]), d[:, 2]
    tr["err_3d_m"] = np.sqrt(tr["err_h_m"] ** 2 + tr["err_v_m"] ** 2)
    med = tr.groupby("track_id")["err_3d_m"].median()
    on_ids = sorted(int(i) for i in med[med < gate_m].index)
    on = tr[tr["track_id"].isin(on_ids)].sort_values("t_utc")
    in_flight = tr[(tr["t_utc"] >= t_air) & (tr["t_utc"] <= t_land)]
    other_ids = sorted(set(in_flight["track_id"]) - set(on_ids))

    def first(mask):
        m = on[mask]
        return m["t_utc"].iloc[0] if len(m) else None

    t_first = on["t_utc"].iloc[0] if len(on) else None
    t_drone = first(on["class_name"].eq("DRONE"))
    t_alarm = first(on["alarm"].fillna(False).astype(bool))
    # coverage: share of airborne seconds with a drone-track state within 2 s
    secs = pd.date_range(t_air, t_land, freq="1s").as_unit("ns")
    if len(on) and len(secs):
        ot = _ns(on["t_utc"])
        idx = np.searchsorted(ot, secs.asi8)
        near = np.minimum(np.abs(secs.asi8 - ot[np.clip(idx, 0, len(ot) - 1)]),
                          np.abs(secs.asi8 - ot[np.clip(idx - 1, 0, len(ot) - 1)]))
        coverage = float(np.mean(near <= 2e9))
    else:
        coverage = 0.0
    e = on.dropna(subset=["err_h_m"])
    stats = lambda s: {"n": int(len(s)), "median": round(float(s.median()), 1), "p90": round(float(s.quantile(.9)), 1),  # noqa: E731
                       "rms": round(float(np.sqrt((s ** 2).mean())), 1)} if len(s) else None
    return {
        "airborne_utc": str(t_air), "landed_utc": str(t_land), "flight_s": _secs(t_air, t_land),
        "drone_track_ids": on_ids,
        "first_track_utc": str(t_first) if t_first is not None else None,
        "delay_airborne_to_first_track_s": _secs(t_air, t_first),
        "delay_airborne_to_first_DRONE_class_s": _secs(t_air, t_drone),
        "delay_airborne_to_first_alarm_s": _secs(t_air, t_alarm),
        "tracked_share_of_flight": round(coverage, 3),
        "track_breaks": max(0, len(on_ids) - 1),
        "error_horizontal_m": stats(e["err_h_m"]), "error_vertical_m": stats(e["err_v_m"]),
        "error_3d_m": stats(e["err_3d_m"]),
        "range_at_first_track_m": round(float(on["range_m"].iloc[0]), 1) if len(on) else None,
        "max_range_tracked_m": round(float(on["range_m"].max()), 1) if len(on) else None,
        "other_tracks_during_flight": len(other_ids),
        "_on": on, "_truth": truth,
    }


def planned_vs_flown(s: Session, airborne_m: float = 2.0, match_m: float = 30.0) -> list[dict]:
    """Pair each planned Skybrush drone with the flown truth that took off nearest to its planned start, tie show
    time to UTC by the take-off moment, and measure how far the flight was from the plan."""
    plans = {f.stem: pd.read_parquet(f).dropna(subset=["e_m", "n_m", "u_m"]) for f in sorted(s.streams.glob("planned/*.parquet"))}
    flown = {}
    for f in truth_files(s):
        d = pd.read_parquet(f).dropna(subset=["e_m", "n_m", "u_m"])
        if len(d):
            d["t_utc"] = pd.to_datetime(d["t_utc"], utc=True)
            flown[f.stem] = d.sort_values("t_utc")
    out, used = [], set()
    for pname, p in plans.items():
        p = p.sort_values("t_show_s")
        if p.empty:
            continue
        p0 = p[["e_m", "n_m"]].iloc[0].to_numpy()
        cands = sorted((float(np.hypot(*(d[["e_m", "n_m"]].iloc[0].to_numpy() - p0))), k) for k, d in flown.items() if k not in used)
        if not cands or cands[0][0] > match_m:
            out.append({"planned": pname, "flown": None, "note": f"no flown log took off within {match_m:.0f} m of the plan"})
            continue
        dist0, k = cands[0]
        used.add(k)
        d = flown[k]
        p_air = p[p["h_rel_takeoff_m"] > airborne_m]
        d_air = d[d["h_rel_takeoff_m"] > airborne_m]
        if p_air.empty or d_air.empty:
            continue
        show_start = d_air["t_utc"].iloc[0] - pd.Timedelta(seconds=float(p_air["t_show_s"].iloc[0]))
        ts = (d_air["t_utc"] - show_start).dt.total_seconds().to_numpy()
        inside = (ts >= p["t_show_s"].iloc[0]) & (ts <= p["t_show_s"].iloc[-1])
        plan_pos = np.column_stack([np.interp(ts[inside], p["t_show_s"], p[c]) for c in ("e_m", "n_m", "u_m")])
        dev = d_air[["e_m", "n_m", "u_m"]].to_numpy()[inside] - plan_pos
        dh, dv = np.hypot(dev[:, 0], dev[:, 1]), dev[:, 2]
        out.append({"planned": pname, "flown": k, "start_distance_m": round(dist0, 1),
                    "show_start_utc": str(show_start), "show_start_method": "aligned on take-off (first > 2 m)",
                    "samples": int(inside.sum()),
                    "deviation_horizontal_m": {"median": round(float(np.median(dh)), 1), "p90": round(float(np.percentile(dh, 90)), 1),
                                               "max": round(float(dh.max()), 1)} if len(dh) else None,
                    "deviation_vertical_m": {"median": round(float(np.median(dv)), 1), "max_abs": round(float(np.abs(dv).max()), 1)} if len(dv) else None})
    return out


def run(s: Session, gate_m: float = 50.0, airborne_m: float = 2.0) -> list[dict]:
    truths = truth_files(s)
    radars = [s.streams / n / "tracks.parquet" for n in s.manifest["streams"] if (s.streams / n / "tracks.parquet").exists()]
    if not truths:
        raise SystemExit("No drone truth in the session (DJI .SRT, ArduPilot .bin, MAVLink .tlog or RTKLIB .pos).")
    s.views.mkdir(parents=True, exist_ok=True)
    pvf = planned_vs_flown(s, airborne_m) if any(s.streams.glob("planned/*.parquet")) else []
    if pvf:
        (s.views / "planned_vs_flown.json").write_text(json.dumps(pvf, indent=2, default=str), encoding="utf-8")
        s.note(f"compare: planned vs flown for {sum(1 for r in pvf if r.get('flown'))}/{len(pvf)} planned drones")
    if not radars:
        s.save()
        return pvf
    tracks = pd.concat([pd.read_parquet(p) for p in radars], ignore_index=True)
    tracks["t_utc"] = pd.to_datetime(tracks["t_utc"], utc=True)
    s.views.mkdir(parents=True, exist_ok=True)
    results, md = [], [f"# Radar vs drone truth\n\nGate {gate_m:.0f} m (median 3D distance per track), airborne = height above "
                       f"take-off > {airborne_m:.0f} m. Horizontal error is the primary figure; vertical depends on the radar's "
                       f"altitude datum (unverified).\n"]
    for f in truths:
        tr = pd.read_parquet(f)
        tr["t_utc"] = pd.to_datetime(tr["t_utc"], utc=True)
        if "h_rel_takeoff_m" not in tr and "height_rel_takeoff_m" in tr:
            tr["h_rel_takeoff_m"] = tr["height_rel_takeoff_m"]
        r = compare_one(tr, tracks, gate_m, airborne_m)
        on, truth = r.pop("_on"), r.pop("_truth")
        r["truth"] = f.name
        results.append(r)
        md.append(f"## {f.stem}\n")
        md += [f"- {k.replace('_', ' ')}: **{v}**" for k, v in r.items() if k != "truth"]
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.plot(truth["t_utc"], np.hypot(truth["e_m"], truth["n_m"]), color="green", label="drone (truth) range")
        for tid, g in on.groupby("track_id"):
            ax.plot(g["t_utc"], g["range_m"], ".", ms=4, label=f"radar track {tid}")
        al = on[on["alarm"].fillna(False).astype(bool)]
        ax.plot(al["t_utc"], al["range_m"], "r|", ms=10, label="alarm")
        ax.set_ylabel("ground range from radar (m)")
        ax.legend(loc="upper left", fontsize=8)
        ax.set_title(f"{f.stem}: drone vs radar")
        p = s.views / f"compare_{f.stem}.png"
        fig.savefig(p, dpi=110, bbox_inches="tight")
        plt.close(fig)
        md.append(f"\n![{f.stem}]({p.name})\n")
    (s.views / "compare.md").write_text("\n".join(md), encoding="utf-8")
    (s.views / "compare.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    s.manifest["compare"] = {"report": "views/compare.md", "gate_m": gate_m}
    s.note(f"compare: {len(results)} truth stream(s) -> views/compare.md")
    s.save()
    return results
