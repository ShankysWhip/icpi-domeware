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


def assign_tracks(truths: dict[str, pd.DataFrame], tracks: pd.DataFrame, gate_m: float) -> dict[int, str]:
    """Each radar track belongs to at most one drone: the one with the smallest median 3D distance, if under the gate.
    Without this, two drones flying close together would both claim the same track."""
    best: dict[int, tuple[float, str]] = {}
    xyz = tracks[["e_m", "n_m", "u_m"]].to_numpy()
    for name, truth in truths.items():
        truth = truth.dropna(subset=["e_m", "n_m", "u_m"]).sort_values("t_utc")
        if truth.empty:
            continue
        err = pd.Series(np.linalg.norm(xyz - _interp_truth(truth, tracks["t_utc"]), axis=1), index=tracks.index)
        for tid, med in err.groupby(tracks["track_id"]).median().items():
            if med < gate_m and med < best.get(int(tid), (np.inf, ""))[0]:
                best[int(tid)] = (float(med), name)
    return {tid: name for tid, (_, name) in best.items()}


def load_plots(s: Session) -> pd.DataFrame | None:
    """Every radar's plots (detections before tracking), or None when the radar export has none."""
    ps = [s.streams / n / "plots.parquet" for n in s.manifest["streams"] if (s.streams / n / "plots.parquet").exists()]
    if not ps:
        return None
    p = pd.concat([pd.read_parquet(x, columns=["t_utc", "e_m", "n_m", "u_m", "range_m"]) for x in ps], ignore_index=True)
    p["t_utc"] = pd.to_datetime(p["t_utc"], utc=True)
    return p.dropna(subset=["e_m", "n_m", "u_m"]).sort_values("t_utc")


def best_time_shift(truth: pd.DataFrame, on: pd.DataFrame, max_s: float = 5.0, step_s: float = 0.1,
                    min_speed_mps: float = 2.0) -> dict | None:
    """Seconds to add to the radar's times so its positions match the drone best (horizontal median error).

    Negative = the radar stamps a position later than the drone was there (processing latency, or a radar clock ahead
    of GPS). The two causes cannot be told apart from positions alone. None when it cannot be measured: too few
    updates, or the drone moved too slowly for time to show up in position.
    """
    if len(on) < 10:
        return None
    tt, x = _ns(truth["t_utc"]), _ns(on["t_utc"])
    dt = np.diff(tt) / 1e9
    speed = np.hypot(np.diff(truth["e_m"].to_numpy()), np.diff(truth["n_m"].to_numpy())) / np.where(dt > 0, dt, np.nan)
    if not np.nanmedian(speed) >= min_speed_mps:
        return None
    lags = np.round(np.arange(-max_s, max_s + step_s / 2, step_s), 3)
    xy = on[["e_m", "n_m"]].to_numpy()
    meds, spreads, biases = [], [], []
    for lag in lags:
        q = x + lag * 1e9
        inside = (q >= tt[0]) & (q <= tt[-1])
        if inside.sum() < 10:
            meds.append(np.inf)
            spreads.append(np.inf)
            biases.append((np.nan, np.nan))
            continue
        p = np.column_stack([np.interp(q[inside], tt, truth[c].to_numpy()) for c in ("e_m", "n_m")])
        err = xy[inside] - p
        b = np.median(err, axis=0)
        meds.append(float(np.median(np.hypot(*err.T))))
        spreads.append(float(np.median(np.hypot(*(err - b).T))))  # what is left once a fixed position bias is removed
        biases.append((float(b[0]), float(b[1])))
    i = int(np.argmin(meds))
    if not np.isfinite(meds[i]):
        return None
    zero = meds[int(np.argmin(np.abs(lags)))]
    out = {"shift_s": float(lags[i]), "at_edge": i in (0, len(lags) - 1),
           "error_h_at_shift_m": round(meds[i], 1), "error_h_at_zero_m": round(zero, 1),
           "method": "smallest median error (assumes no fixed position bias)", "bias_en_m": None}
    # With a fixed bias, time and position can only be told apart when the drone turns: then the bias-free spread
    # has a clear minimum. On a straight leg it is flat, and a time shift looks exactly like an along-track bias.
    j = int(np.argmin(spreads))
    near = [spreads[k] for k in (j - int(1 / step_s), j + int(1 / step_s)) if 0 <= k < len(spreads)]
    if np.isfinite(spreads[j]) and near and min(near) >= 1.2 * spreads[j] + 0.2:
        out.update(shift_s=float(lags[j]), at_edge=j in (0, len(lags) - 1), method="bias-free fit (the drone turned)",
                   bias_en_m=[round(biases[j][0], 1), round(biases[j][1], 1)])
    return out


def first_plot(truth: pd.DataFrame, plots: pd.DataFrame | None, gate_m: float, t_air, t_land=None) -> tuple:
    """First radar plot (before any track) within the gate of the drone, after take-off: (time, range) or (None, None)."""
    if plots is None or plots.empty:
        return None, None
    p = plots[(plots["t_utc"] >= t_air) & ((plots["t_utc"] <= t_land) if t_land is not None else True)]
    if p.empty:
        return None, None
    d = np.linalg.norm(p[["e_m", "n_m", "u_m"]].to_numpy() - _interp_truth(truth, p["t_utc"]), axis=1)
    hit = p[d < gate_m]
    return (hit["t_utc"].iloc[0], round(float(hit["range_m"].iloc[0]), 1)) if len(hit) else (None, None)


def compare_one(truth: pd.DataFrame, tracks: pd.DataFrame, gate_m: float, airborne_m: float,
                plots: pd.DataFrame | None = None) -> dict:
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

    flying = on["t_utc"] >= t_air  # a radar can track a drone standing on the ground; delays count from take-off
    t_first = on.loc[flying, "t_utc"].iloc[0] if flying.any() else None
    t_drone = first(on["class_name"].eq("DRONE") & flying)
    t_alarm = first(on["alarm"].fillna(False).astype(bool) & flying)
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
    t_plot, r_plot = first_plot(truth, plots, gate_m, t_air, t_land)
    shift = best_time_shift(air if len(air) > 10 else truth, on[(on["t_utc"] >= t_air) & (on["t_utc"] <= t_land)])
    e = on.dropna(subset=["err_h_m"])
    stats = lambda s: {"n": int(len(s)), "median": round(float(s.median()), 1), "p90": round(float(s.quantile(.9)), 1),  # noqa: E731
                       "rms": round(float(np.sqrt((s ** 2).mean())), 1)} if len(s) else None
    return {
        "airborne_utc": str(t_air), "landed_utc": str(t_land), "flight_s": _secs(t_air, t_land),
        "drone_track_ids": on_ids,
        "delay_airborne_to_first_plot_s": _secs(t_air, t_plot), "range_at_first_plot_m": r_plot,
        "radar_time_shift_s": None if shift is None else shift["shift_s"],
        "radar_time_shift_note": None if shift is None else (
            f"{shift['method']}; horizontal error {shift['error_h_at_zero_m']} m as stamped, {shift['error_h_at_shift_m']} m when shifted"
            + (f"; fixed position bias east {shift['bias_en_m'][0]} m, north {shift['bias_en_m'][1]} m" if shift["bias_en_m"] else "")
            + (" (at the search limit: the real offset may be larger)" if shift["at_edge"] else "")),
        "first_track_utc": str(t_first) if t_first is not None else None,
        "delay_airborne_to_first_track_s": _secs(t_air, t_first),
        "delay_airborne_to_first_DRONE_class_s": _secs(t_air, t_drone),
        "delay_airborne_to_first_alarm_s": _secs(t_air, t_alarm),
        "tracked_share_of_flight": round(coverage, 3),
        "track_breaks": max(0, len(on_ids) - 1),
        "error_horizontal_m": stats(e["err_h_m"]), "error_vertical_m": stats(e["err_v_m"]),
        "error_3d_m": stats(e["err_3d_m"]),
        "range_at_first_track_m": round(float(on.loc[flying, "range_m"].iloc[0]), 1) if flying.any() else None,
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
    loaded = {}
    for f in truths:
        tr = pd.read_parquet(f)
        tr["t_utc"] = pd.to_datetime(tr["t_utc"], utc=True)
        if "h_rel_takeoff_m" not in tr and "height_rel_takeoff_m" in tr:
            tr["h_rel_takeoff_m"] = tr["height_rel_takeoff_m"]
        loaded[f] = tr
    owner = assign_tracks({f.stem: d for f, d in loaded.items() if {"e_m", "n_m", "u_m"} <= set(d.columns)}, tracks, gate_m)
    plots = load_plots(s)
    for f, tr in loaded.items():
        mine = [t for t, n in owner.items() if n == f.stem]
        try:
            r = compare_one(tr, tracks[tracks["track_id"].isin(mine)], gate_m, airborne_m, plots)
        except (ValueError, KeyError) as e:  # one log without positions must not stop the others
            s.note(f"compare: {f.name}: skipped ({e})")
            continue
        on, truth = r.pop("_on"), r.pop("_truth")
        flying = tracks[(tracks["t_utc"] >= pd.Timestamp(r["airborne_utc"])) & (tracks["t_utc"] <= pd.Timestamp(r["landed_utc"]))]
        r["other_tracks_during_flight"] = len(set(flying["track_id"]) - set(owner))  # none of our drones
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
