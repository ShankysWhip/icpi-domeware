"""Numbers for the Blue (radar) and Red (drone) scorecards, per run and phase -> views/scorecard_<run>.json.

The JSON is what "Import domeware values" in ICPI Run Scorecards v2 reads: it fills empty Value and Note fields and
never sets a result. Every value starts with its number (the card compares it with the written target); the note says
how it was measured. A check with nothing to measure from is left out, never guessed.

Phase windows come from runs.csv (`dw runs`). Without phase rows the whole run is used and every note says so.
Association is the same rule as `dw compare`: a radar track is a drone's when its median 3D distance to that drone's
truth in the window is under the gate. Per second, views/association_<run>.parquet records which track was which drone.
"""

from __future__ import annotations

import json
from itertools import combinations

import numpy as np
import pandas as pd

from domeware import runs as R
from domeware.compare import _interp_truth, _ns, assign_tracks, compare_one, load_plots
from domeware.replay import read_truth, truth_files
from domeware.session import Session, now_utc

FORMAT = "domeware-scorecard/1"
PHASE_OF = {"P1": "BL", "P2": "BD", "P3": "BA", "P4": "BH"}


def _load(s: Session) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    truths = {}
    for f in truth_files(s):
        d = read_truth(f)
        d = d.dropna(subset=["e_m", "n_m", "u_m"]) if {"e_m", "n_m", "u_m"} <= set(d.columns) else d.iloc[0:0]
        if len(d):
            truths[f.stem] = d.sort_values("t_utc").reset_index(drop=True)
    radars = [s.streams / n / "tracks.parquet" for n in s.manifest["streams"] if (s.streams / n / "tracks.parquet").exists()]
    tracks = pd.concat([pd.read_parquet(p) for p in radars], ignore_index=True) if radars else pd.DataFrame()
    if len(tracks):
        tracks["t_utc"] = pd.to_datetime(tracks["t_utc"], utc=True)
    return truths, tracks


def _cut(df: pd.DataFrame, a, b) -> pd.DataFrame:
    return df[(df["t_utc"] >= a) & (df["t_utc"] <= b)] if len(df) else df


def _gap(t: pd.Series) -> float:
    t = pd.Series(sorted(pd.to_datetime(t, utc=True)))
    return round(float(t.diff().dt.total_seconds().max()), 1) if len(t) > 1 else 0.0


def _gap_window(t: pd.Series, a, b) -> float | None:
    """Longest silence in [a, b], counting from the window's start and to its end. None with < 2 messages inside."""
    t = t[(t >= a) & (t <= b)]
    if len(t) < 2:
        return None
    return _gap(pd.concat([pd.Series([pd.Timestamp(a)]), t, pd.Series([pd.Timestamp(b)])], ignore_index=True))


def _speed(d: pd.DataFrame) -> pd.Series:
    dt = d["t_utc"].diff().dt.total_seconds()
    return (np.hypot(d["e_m"].diff(), d["n_m"].diff()) / dt).where(dt > 0)


def measure(truths: dict, tracks: pd.DataFrame, a, b, gate_m: float, airborne_m: float = 2.0,
            plots: pd.DataFrame | None = None) -> dict:
    """Per drone, everything the cards ask for in one window [a, b]."""
    tr_w = _cut(tracks, a, b)
    cut = {name: _cut(d, a, b) for name, d in truths.items()}
    cut = {k: d for k, d in cut.items() if len(d) >= 2 and (d["h_rel_takeoff_m"] > airborne_m).any()}
    owner = assign_tracks(cut, tr_w, gate_m) if len(tr_w) else {}
    per = {}
    for name, dw in cut.items():
        r = {"truth": dw, "on": tr_w.iloc[0:0]}
        mine = tr_w[tr_w["track_id"].isin([t for t, n in owner.items() if n == name])] if len(tr_w) else tr_w
        if len(mine):
            try:
                c = compare_one(dw, mine, gate_m, airborne_m, _cut(plots, a, b) if plots is not None else None)
            except ValueError:
                c = None
            if c:
                r.update(c)
                r["on"] = c["_on"]
        per[name] = r
    return {"per": per, "tracks": tr_w}


def _fmt(x, nd=0, unit=""):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}{unit}"


def _worst(per: dict, fn, pick=min):
    vals = {k: v for k, v in ((k, fn(r)) for k, r in per.items()) if v is not None and not (isinstance(v, float) and np.isnan(v))}
    if not vals:
        return None, ""
    k = pick(vals, key=vals.get)
    return vals[k], "; ".join(f"{n}: {v:.1f}" for n, v in vals.items())


def association_grid(m: dict, a, b) -> pd.DataFrame:
    """One row per drone per airborne second: which radar track (if any) was that drone, and how far off."""
    rows = []
    secs = pd.date_range(pd.Timestamp(a).ceil("1s"), pd.Timestamp(b).floor("1s"), freq="1s").as_unit("ns")
    for name, r in m["per"].items():
        d = r["truth"]
        air = d[d["h_rel_takeoff_m"] > 2.0]
        if air.empty:
            continue
        g = pd.DataFrame({"t_utc": secs[(secs >= air["t_utc"].iloc[0]) & (secs <= air["t_utc"].iloc[-1])]})
        g["drone"] = name
        on = r["on"].sort_values("t_utc")
        if len(on):
            cols = [c for c in ("t_utc", "track_id", "err_h_m", "class_name", "alarm", "range_m") if c in on]
            on = on[cols].copy()
            on["t_utc"] = on["t_utc"].dt.as_unit("ns")
            g = pd.merge_asof(g, on, on="t_utc", direction="nearest", tolerance=pd.Timedelta("2s"))
        rows.append(g)
    if not rows:
        return pd.DataFrame(columns=["t_utc", "drone", "track_id"])
    out = pd.concat(rows, ignore_index=True)
    if "track_id" not in out:
        out["track_id"] = np.nan
    out["detected"] = out["track_id"].notna()
    return out


def merges_swaps(cut: dict[str, pd.DataFrame], tracks: pd.DataFrame, gate_m: float) -> tuple[int, int]:
    """From every track state, not from the one-drone-per-track assignment (which hides both by design).

    Merge: a second in which one track was within the gate of two or more drones and at least one of them had no
    other track near it (the radar saw two drones as one). Swap: a track's nearest drone changes while it is
    tracked (the track moved from one drone to another)."""
    if len(cut) < 2 or tracks.empty:
        return 0, 0
    names = list(cut)
    xyz = tracks[["e_m", "n_m", "u_m"]].to_numpy()
    dist = np.column_stack([np.linalg.norm(xyz - _interp_truth(cut[n], tracks["t_utc"]), axis=1) for n in names])
    dist = np.where(np.isfinite(dist), dist, np.inf)
    near = dist < gate_m
    st = pd.DataFrame(near, columns=names)
    st["sec"], st["tid"] = tracks["t_utc"].dt.floor("1s").to_numpy(), tracks["track_id"].to_numpy()
    merges = 0
    multi_secs = set(st.loc[near.sum(axis=1) >= 2, "sec"])
    for _sec, g in st[st["sec"].isin(multi_secs)].groupby("sec"):
        per_tid = g.groupby("tid")[names].any()
        for tid, row in per_tid.iterrows():
            shared = [n for n in names if row[n]]
            if len(shared) >= 2 and any(not per_tid.drop(index=tid)[n].any() for n in shared):
                merges += 1
                break
    lab = pd.DataFrame({"tid": st["tid"], "t": tracks["t_utc"].to_numpy(),
                        "lab": np.where(near.any(axis=1), dist.argmin(axis=1), -1)})
    lab = lab[lab["lab"] >= 0].sort_values("t")
    swaps = int(sum((g["lab"].diff().fillna(0) != 0).sum() for _, g in lab.groupby("tid")))
    return merges, swaps


def items_for(part: str, m: dict, grid: pd.DataFrame, whole_run: bool, a, b, extra: dict) -> dict:
    per, items = m["per"], {}
    where = "whole run (no phase times in runs.csv)" if whole_run else f"{part} {a:%H:%M:%S}-{b:%H:%M:%S} UTC"
    n = len(per)

    def put(cid, value, note):
        if value is not None:
            items[cid] = {"value": value, "note": f"{note} [{where}]"}

    det = {k: r for k, r in per.items() if r.get("drone_track_ids")}
    first_rng, detail = _worst(det, lambda r: r.get("range_at_first_track_m"))
    gap_on = lambda r: _gap(r["on"]["t_utc"]) if len(r["on"]) > 1 else None  # noqa: E731
    p = PHASE_OF.get(part)
    if p:
        if p in ("BL", "BD"):
            put({"BL": "BL2", "BD": "BD1"}[p], _fmt(first_rng, 0), f"range at first radar track, worst drone; {detail}")
        breaks = sum(r.get("track_breaks", 0) for r in det.values())
        longest, _ = _worst(det, gap_on, max)
        if p in ("BL", "BA"):
            put(f"{p}3", f"{breaks} / {_fmt(longest, 1) or 0}" if det else None,
                "track breaks (extra track ids) / longest gap between drone-track updates (s)")
        if p in ("BD", "BA"):
            put("BD4" if p == "BD" else "BA4", f"{len(det)} / {n}" + (f", {max(0.0, *(r['delay_airborne_to_first_track_s'] or 0 for r in det.values())):.0f}" if p == "BD" and det else ""),
                "drones with their own radar track / drones flying" + (", seconds from airborne until all had one" if p == "BD" else ""))
        if p == "BL":
            low, d2 = _worst(det, lambda r: float(np.interp(_ns(r["on"]["t_utc"]), _ns(r["truth"]["t_utc"]),
                                                           r["truth"]["h_rel_takeoff_m"]).min()) if len(r["on"]) else None)
            put("BL1", _fmt(low, 1), f"lowest drone height above take-off while it had a radar track; {d2}")
            cls, d3 = _worst(det, lambda r: None if r.get("delay_airborne_to_first_DRONE_class_s") is None else
                             r["delay_airborne_to_first_DRONE_class_s"] - r["delay_airborne_to_first_track_s"], max)
            put("BL4", _fmt(cls, 1), f"first track to first DRONE class, slowest drone; {d3}" if cls is not None else "")
            ev, d4 = _worst(det, lambda r: abs((r.get("error_vertical_m") or {}).get("median") or 0), max)
            put("BL6", _fmt(ev, 1), f"median |vertical error|, worst drone (radar altitude datum unconfirmed); {d4}")
            pt, d4b = _worst(det, lambda r: None if r.get("delay_airborne_to_first_plot_s") is None
                             or r.get("delay_airborne_to_first_track_s") is None else
                             max(0.0, r["delay_airborne_to_first_track_s"] - r["delay_airborne_to_first_plot_s"]), max)
            put("BL7", _fmt(pt, 1), f"first radar plot near the drone to its first track, slowest drone; {d4b}")
        if p == "BD":
            eh, dh = _worst(det, lambda r: (r.get("error_horizontal_m") or {}).get("median"), max)
            ev, _ = _worst(det, lambda r: abs((r.get("error_vertical_m") or {}).get("median") or 0), max)
            put("BD2", f"{_fmt(eh, 1)} / {_fmt(ev, 1)}" if eh is not None else None,
                f"median horizontal / vertical error, worst drone; horizontal per drone {dh}")
            def sp_err(r):
                on, t = r["on"], r["truth"]
                if "speed_mps" not in on or len(on) < 2:
                    return None
                ts = _speed(t)
                v = np.interp(_ns(on["t_utc"]), _ns(t["t_utc"])[1:], ts.to_numpy()[1:])
                return float(np.nanmedian(np.abs(on["speed_mps"].to_numpy() - v)))
            se, d5 = _worst(det, sp_err, max)
            put("BD3", _fmt(se, 1), f"median |radar speed - GPS ground speed|, worst drone; {d5}")
            def class_drops(r):
                if "class_name" not in r["on"] or r["on"].empty:
                    return None
                total = 0
                for _, g in r["on"].sort_values("t_utc").groupby("track_id"):  # a new track is not a lost class
                    is_d = g["class_name"].eq("DRONE").to_numpy()
                    total += int(np.sum(is_d[:-1] & ~is_d[1:]))
                return float(total)
            cd, dcd = _worst(det, class_drops, max)
            put("BD9", _fmt(cd, 0), f"times a drone track lost its DRONE class, worst drone; {dcd}")
            mg, sw = merges_swaps({k: r["truth"] for k, r in per.items()}, m["tracks"], extra.get("gate_m", 50.0))
            put("BD5", f"{mg} / {sw}", "seconds the radar showed two drones as one track / times a track moved from one "
                "drone to another (every track update, nearest drone within the gate)")
            def near_far(r):
                e = r["on"].dropna(subset=["err_h_m"])
                if len(e) < 4:
                    return None
                cut = e["range_m"].median()
                return (float(e[e["range_m"] < cut]["err_h_m"].median()), float(e[e["range_m"] >= cut]["err_h_m"].median()))
            nf = {k: v for k, v in ((k, near_far(r)) for k, r in det.items()) if v}
            if nf:
                k = max(nf, key=lambda x: nf[x][1])
                put("BD6", f"{nf[k][0]:.1f} / {nf[k][1]:.1f}", f"median horizontal error below / above the median range, drone {k}")
            mx, d6 = _worst(det, lambda r: r.get("max_range_tracked_m"), min)
            put("BD7", _fmt(mx, 0), f"longest range with a drone track, shortest of the drones; {d6}")
            owned = {t for r in det.values() for t in r.get("drone_track_ids", [])}
            other = set(m["tracks"]["track_id"].unique()) - owned if len(m["tracks"]) else set()
            put("BD8", str(len(other)), "radar tracks in the window that were none of our drones (birds, cars, other aircraft)")
        if p == "BA":
            fa, d7 = _worst(det, lambda r: float(r["on"][r["on"]["alarm"].fillna(False).astype(bool)]["range_m"].iloc[0])
                            if r["on"]["alarm"].fillna(False).astype(bool).any() else None)
            put("BA1", _fmt(fa, 0), f"radar range at first alarm, worst drone; {d7}")
            da, d8 = _worst(det, lambda r: None if r.get("delay_airborne_to_first_alarm_s") is None else
                            r["delay_airborne_to_first_alarm_s"] - r["delay_airborne_to_first_track_s"], max)
            put("BA2", _fmt(da, 1), f"first track to first alarm, slowest drone; {d8}")
            def dropped(r):
                on = r["on"]
                if not on["alarm"].fillna(False).astype(bool).any():
                    return None
                total = 0.0
                for _, g in on.sort_values("t_utc").groupby("track_id"):
                    al = g["alarm"].fillna(False).astype(bool).to_numpy()
                    if al.any():
                        k = int(np.argmax(al))
                        dt = g["t_utc"].diff().dt.total_seconds().shift(-1).fillna(0).to_numpy()
                        total += float(dt[k:][~al[k:]].sum())
                return total
            dr, d9 = _worst(det, dropped, max)
            put("BA5", _fmt(dr, 1), f"seconds without alarm after the first alarm, worst drone; {d9}")
            def warning(r):
                on, tr_ = r["on"], r["truth"][r["truth"]["h_rel_takeoff_m"] > 2.0]
                al = on[on["alarm"].fillna(False).astype(bool)]
                if al.empty or tr_.empty:
                    return None
                close = tr_.loc[np.sqrt(tr_["e_m"] ** 2 + tr_["n_m"] ** 2 + tr_["u_m"] ** 2).idxmin(), "t_utc"]
                return (close - al["t_utc"].iloc[0]).total_seconds()
            wt, dwt = _worst(det, warning, min)
            put("BA6", _fmt(wt, 1), f"first alarm to the drone's closest approach to the radar, shortest warning; {dwt}")
        if p == "BH":
            put("BH1", _fmt(longest, 1) if det else None, "longest gap between drone-track updates while hovering")
            eh, dh = _worst(det, lambda r: (r.get("error_horizontal_m") or {}).get("median"), max)
            put("BH3", _fmt(eh, 1), f"median horizontal error while hovering, worst drone; {dh}")

    # Red: what the drones did, from their own logs
    air = {k: r["truth"][r["truth"]["h_rel_takeoff_m"] > 2.0] for k, r in per.items()}
    air = {k: d for k, d in air.items() if len(d) > 1}
    if part == "RUN":
        g, dg = _worst(per, lambda r: _gap(r["truth"]["t_utc"]), max)
        put("R12", _fmt(g, 1), f"longest time between fixes in a drone log; {dg}")
        if extra.get("radar_gap") is not None:
            put("B1", _fmt(extra["radar_gap"], 1), extra["radar_gap_note"])
        sh = {k: r["radar_time_shift_s"] for k, r in per.items() if r.get("radar_time_shift_s") is not None}
        if sh:
            k = max(sh, key=lambda x: abs(sh[x]))
            put("B7", f"{abs(sh[k]):.1f}", f"radar positions match GPS best when radar times are shifted {sh[k]:+.1f} s "
                f"(negative = stamped late: latency or a fast radar clock); drone {k}: {per[k]['radar_time_shift_note']}")
        if extra.get("phases_found"):
            put("B5", extra["phases_found"], "phase windows in runs.csv that hold radar data")
    if part == "P1" and air:
        lo = min(float(d["h_rel_takeoff_m"].min()) for d in air.values())
        hi = max(float(d["h_rel_takeoff_m"].max()) for d in air.values())
        put("L2", f"{lo:.1f}-{hi:.1f}", "height above take-off while airborne, all drones")
        sats = [float(d["nsats"].min()) for d in air.values() if "nsats" in d and d["nsats"].notna().any()]
        if sats:
            put("L4", f"{min(sats):.0f}", "fewest GPS satellites in any drone log")
        dist, dd = _worst(air, lambda d: float(np.hypot(d["e_m"], d["n_m"]).min()))
        put("L5", _fmt(dist, 0), f"closest ground distance to the radar; {dd}")
    if part == "P2" and air:
        d1, dd = _worst(air, lambda d: float(np.hypot(d["e_m"].iloc[0], d["n_m"].iloc[0])), min)
        put("D1", _fmt(d1, 0), f"ground distance to the radar at the start of the phase, nearest drone; {dd}")
        sp, ds = _worst(air, lambda d: float(_speed(d).median()), min)
        put("D5", _fmt(sp, 1), f"median ground speed, slowest drone; {ds}")
    if part == "P3" and air:
        cd, dd = _worst(air, lambda d: float(np.sqrt(d["e_m"] ** 2 + d["n_m"] ** 2 + d["u_m"] ** 2).min()))
        put("A2", _fmt(cd, 0), f"closest 3D distance to the radar (if the target is elsewhere, measure to it by hand); {dd}")
        sp, ds = _worst(air, lambda d: float(_speed(d).median()), min)
        put("A3", _fmt(sp, 1), f"median ground speed, slowest drone; {ds}")
        if len(air) > 1:
            secs = pd.date_range(pd.Timestamp(a).ceil("1s"), pd.Timestamp(b).floor("1s"), freq="1s")
            pos = {k: _interp_truth(d, pd.Series(secs)) for k, d in air.items()}
            gaps = [float(np.nanmin(dd)) for x, y in combinations(pos, 2)
                    if np.isfinite(dd := np.linalg.norm(pos[x] - pos[y], axis=1)).any()]
            if gaps:
                put("A5", _fmt(min(gaps), 1), "smallest 3D distance between any two drones (1 s grid)")
    if part == "P4" and air:
        def hover(d):
            v = _speed(d)
            h = d[v < 1.0]
            return h
        ht, dh = _worst(air, lambda d: float(len(hover(d))) * float(d["t_utc"].diff().dt.total_seconds().median()), min)
        put("H1", _fmt(ht, 0), f"seconds with ground speed under 1 m/s, shortest drone; {dh}")
        dr, dd = _worst(air, lambda d: float(np.hypot(hover(d)["e_m"] - hover(d)["e_m"].median(),
                                                       hover(d)["n_m"] - hover(d)["n_m"].median()).max()) if len(hover(d)) else None, max)
        put("H2", _fmt(dr, 1), f"largest horizontal distance from the median hover point; {dd}")
        hc, dc = _worst(air, lambda d: float(hover(d)["u_m"].max() - hover(d)["u_m"].min()) if len(hover(d)) else None, max)
        put("H3", _fmt(hc, 1), f"height change while hovering; {dc}")
    return items


# Validity rules (decision doc, Phase 3). A void run is kept and counted, never deleted; the reason travels with it.
MAX_TRUTH_GAP_S = 3.0
MAX_RADAR_SILENCE_S = 10.0
MIN_DRONE_SECONDS = 30


def validity(m: dict, grid: pd.DataFrame, extra: dict) -> dict:
    """Can this run's numbers be used? Every rule that fails is listed; warnings do not void the run."""
    reasons, warnings = [], []
    flying = {k: r["truth"] for k, r in m["per"].items()}
    if not flying:
        reasons.append("no drone flew in the run window")
    for k, d in flying.items():
        air = d[d["h_rel_takeoff_m"] > 2.0]
        g = air["t_utc"].diff().dt.total_seconds().max() if len(air) > 1 else 0.0
        if g > MAX_TRUTH_GAP_S:
            reasons.append(f"drone {k}: truth lost for {g:.1f} s (limit {MAX_TRUTH_GAP_S:.0f} s)")
    gap, live = extra.get("radar_gap"), extra.get("radar_liveness")
    if live == "status":  # status messages prove the radar was on: silence there voids the run
        if gap is None:
            reasons.append("no radar status messages in the run window")
        elif gap > MAX_RADAR_SILENCE_S:
            reasons.append(f"radar silent for {gap:.0f} s (limit {MAX_RADAR_SILENCE_S:.0f} s)")
    elif extra.get("radar_outside"):
        reasons.append("no radar data in the session")
    else:  # only track updates: a quiet sky looks the same as a radar that was off
        warnings.append("radar liveness unknown (no status stream): missed drones count as not detected")
    n = len(grid)
    if n < MIN_DRONE_SECONDS:
        warnings.append(f"only {n} airborne drone-seconds: shares from this run say little")
    return {"valid": not reasons, "reasons": reasons, "warnings": warnings, "drone_seconds": n,
            "rules": {"max_truth_gap_s": MAX_TRUTH_GAP_S, "max_radar_silence_s": MAX_RADAR_SILENCE_S,
                      "min_drone_seconds": MIN_DRONE_SECONDS}}


def run(s: Session, gate_m: float = 50.0, only: str | None = None) -> list[dict]:
    truths, tracks = _load(s)
    plots = load_plots(s)
    if not truths:
        raise SystemExit("No drone truth with positions in the session: nothing to score (run dw env and dw convert first).")
    df = R.ensure(s)
    if df.empty:
        raise SystemExit("No runs: no drone was airborne in the truth logs, and runs.csv is empty.")
    status = [s.streams / n / "status.parquet" for n in s.manifest["streams"] if (s.streams / n / "status.parquet").exists()]
    s.views.mkdir(parents=True, exist_ok=True)
    if only is None:  # derived files: a renamed or removed run must not leave its old values behind
        for old in [*s.views.glob("scorecard_*.json"), *s.views.glob("association_*.parquet")]:
            old.unlink()
    out, md = [], ["# Scorecard values from domeware", "",
                   f"Gate {gate_m:.0f} m. Values fill empty fields in ICPI Run Scorecards v2 (Import domeware values). "
                   "Results stay with the reviewers.", ""]
    for run_id in [r for r in dict.fromkeys(df["run_id"]) if only in (None, r)]:
        w = R.windows(df, run_id)
        if "RUN" not in w:
            continue
        a, b = w["RUN"]
        m_run = measure(truths, tracks, a, b, gate_m, plots=plots)
        grid = association_grid(m_run, a, b)
        grid.to_parquet(s.views / f"association_{run_id}.parquet", index=False)
        extra = {"gate_m": gate_m}
        if status:
            st = pd.concat([pd.read_parquet(p, columns=["t_utc"]) for p in status])
            extra["radar_gap"] = _gap_window(pd.to_datetime(st["t_utc"], utc=True), a, b)
            extra["radar_gap_note"] = "longest time between radar status messages in the run"
            extra["radar_liveness"] = "status"
        elif len(tracks):
            extra["radar_gap"] = _gap_window(tracks["t_utc"], a, b)
            extra["radar_gap_note"] = "longest time between radar track updates in the run (no status stream; quiet sky also counts)"
            extra["radar_liveness"] = "tracks"
        else:
            extra["radar_outside"] = True  # no radar output in the session at all
        phases = [p for p in ("P1", "P2", "P3", "P4") if p in w]
        if phases and len(tracks):
            extra["phases_found"] = f"{sum(1 for p in phases if len(_cut(tracks, *w[p])))}/{len(phases)}"
        items = items_for("RUN", m_run, grid, False, a, b, extra)
        for part in phases:  # phase checks only from real phase windows: whole-run numbers on a phase row mislead
            pa, pb = w[part]
            gp = grid[(grid["t_utc"] >= pa) & (grid["t_utc"] <= pb)]
            for k, v in items_for(part, measure(truths, tracks, pa, pb, gate_m, plots=plots), gp, False, pa, pb, {"gate_m": gate_m}).items():
                items.setdefault(k, v)
        # whole-run detection numbers for the report; not imported into phase checks
        whole = items_for("P2", m_run, grid, True, a, b, {"gate_m": gate_m})
        card = {"format": FORMAT, "source": f"domeware session {s.root.name}", "run_id": run_id, "generated_utc": now_utc(),
                "gate_m": gate_m, "drones": sorted(m_run["per"]),
                "phases": {p: {"start": w[p][0].strftime("%H:%M:%S"), "end": w[p][1].strftime("%H:%M:%S")} for p in phases},
                "items": dict(sorted(items.items())),
                "whole_run": {k: v for k, v in whole.items() if k.startswith("BD")}}
        card["validity"] = validity(m_run, grid, extra)
        if not phases:
            s.note(f"scorecard: {run_id}: no phase times, so only run-level checks are filled. Add them with "
                   f"dw runs {s.root.name} --card <scorecard export>.json, or P1-P4 rows in runs.csv")
        (s.views / f"scorecard_{run_id}.json").write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
        out.append(card)
        md += [f"## {run_id}  {a:%Y-%m-%d %H:%M:%S}-{b:%H:%M:%S} UTC", "",
               f"Drones: {', '.join(card['drones']) or 'none in this window'}. Phases: {', '.join(phases) or 'none in runs.csv (whole run used)'}.", "",
               f"**Run {'VALID' if card['validity']['valid'] else 'NOT VALID'}**"
               + (": " + "; ".join(card["validity"]["reasons"]) if card["validity"]["reasons"] else "") + "", "",
               "| check | value | how |", "|---|---|---|"]
        md += [f"| {k} | {v['value']} | {v['note']} |" for k, v in card["items"].items()]
        detected = grid.groupby("drone")["detected"].mean() if len(grid) else pd.Series(dtype=float)
        if len(detected):
            md += ["", "Share of airborne seconds with a radar track: " + ", ".join(f"{k} {v:.0%}" for k, v in detected.items())]
        md.append("")
    (s.views / "scorecard.md").write_text("\n".join(md), encoding="utf-8")
    s.manifest["scorecard"] = {"runs": [c["run_id"] for c in out], "gate_m": gate_m, "files": [f"views/scorecard_{c['run_id']}.json" for c in out]}
    s.note(f"scorecard: {len(out)} run(s) -> views/scorecard_<run>.json (import into the Run Scorecards page)")
    s.save()
    return out
