"""`dw check SESSION`: is the data complete, before anyone leaves the field? Offline, a few seconds.

It converts what it can (no downloads) and then answers, file by file:
  - did the radar record the whole time the drones flew, without long silences?
  - does every drone log have positions, and no long gaps while airborne?
  - do drone and radar times overlap at all? (no overlap = a clock or time-zone mistake, fixable only now)
  - do the run and phase times in runs.csv sit inside the radar data?
  - can every video be placed on the clock, and is anything unreadable or unknown?
Every line is OK, WARN (usable, look at it) or FAIL (fix it in the field: copy again, re-export, write the time down).
"""

from __future__ import annotations

import pandas as pd

from domeware.session import Session


def run(s: Session, radar_gap_s: float = 10.0, log_gap_s: float = 3.0) -> int:
    from domeware import convert

    convert.run(s)
    if s.manifest.get("site") and (s.env / "dem.tif").exists():
        convert.convert_truth(s)
    else:
        _truth_without_heights(s)
    out: list[tuple[str, str]] = []
    ok = lambda m: out.append(("OK  ", m))  # noqa: E731
    warn = lambda m: out.append(("WARN", m))  # noqa: E731
    fail = lambda m: out.append(("FAIL", m))  # noqa: E731

    roles: dict[str, int] = {}
    for r in s.manifest["raw"]:
        roles[r["role"]] = roles.get(r["role"], 0) + 1
    ok("files: " + ", ".join(f"{k} {v}" for k, v in sorted(roles.items())))
    if roles.get("unknown"):
        warn(f"{roles['unknown']} file(s) of unknown type kept but not used: "
             + ", ".join(r["source_name"] for r in s.manifest["raw"] if r["role"] == "unknown")[:300])
    if roles.get("dji_flightrecord_encrypted"):
        warn("DJI flight records are encrypted: also save the .SRT captions (or a CSV export) from every DJI flight")

    # radar
    radar = []
    for name in s.manifest["streams"]:
        for table in ("status", "tracks"):
            p = s.streams / name / f"{table}.parquet"
            if p.exists():
                t = pd.to_datetime(pd.read_parquet(p, columns=["t_utc"])["t_utc"], utc=True)
                radar.append((name, table, t.sort_values()))
                break
    if not radar:
        fail("no radar data (no Robin XML in the files)") if not roles.get("robin_xml") else fail("radar file could not be read")
    span = None
    for name, table, t in radar:
        if t.empty:
            fail(f"radar {name}: the file has no {table} rows (radar off, or the export stopped)")
            continue
        a, b = t.iloc[0], t.iloc[-1]
        span = (a, b) if span is None else (min(span[0], a), max(span[1], b))
        gaps = t.diff().dt.total_seconds()
        big = gaps[gaps > radar_gap_s]
        msg = f"radar {name}: {a:%H:%M:%S}-{b:%H:%M:%S} UTC ({(b - a).total_seconds() / 60:.0f} min, {table} messages)"
        if len(big):
            i = big.idxmax()
            warn(f"{msg}; {len(big)} silence(s) over {radar_gap_s:.0f} s, longest {big.max():.0f} s ending {t.loc[i]:%H:%M:%S}")
        else:
            ok(msg)

    # the radar's height system, from its own status: settled before any height is compared with GPS
    from domeware.datum import robin_height_datum

    hd = robin_height_datum(s)
    if hd:
        nums = f"radar ground level {hd['radar_ground_m']:.1f} m"
        if hd["map_ground_m"] is not None:
            nums += f", map (N2000) {hd['map_ground_m']:.1f} m, difference {hd['difference_m']:+.1f} m"
        if hd["geoid_m"] is not None:
            nums += f", geoid here {hd['geoid_m']:.1f} m"
        if hd["verdict"] == "orthometric":
            ok(f"radar heights are orthometric (N2000 / sea level): {nums}")
        elif hd["verdict"] == "ellipsoidal":
            warn(f"radar heights are ellipsoidal (WGS84), not sea level: {nums}; the geoid height must come off first")
        else:
            warn(f"radar height system unclear ({hd['why']}): {nums}; ask for the radar's export settings")
        site = s.manifest.get("site")
        if site is not None:
            site["alt_datum_check"] = {**hd, "checked_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
            s.save()

    # drones
    from domeware.replay import truth_files

    files = truth_files(s)
    if not files:
        fail("no drone positions: save the drone logs (.bin / .tlog / .SRT / DJI CSV / RTKLIB .pos)")
    for f in files:
        from domeware.replay import read_truth

        d = read_truth(f).sort_values("t_utc")
        h = d.get("h_rel_takeoff_m")
        air = d[h > 2.0] if h is not None else d.iloc[0:0]
        if d.empty:
            fail(f"drone {f.stem}: no positions")
            continue
        a, b = (air["t_utc"].iloc[0], air["t_utc"].iloc[-1]) if len(air) else (d["t_utc"].iloc[0], d["t_utc"].iloc[-1])
        gaps = air["t_utc"].diff().dt.total_seconds() if len(air) else pd.Series(dtype=float)
        msg = f"drone {f.stem}: airborne {a:%H:%M:%S}-{b:%H:%M:%S} UTC" if len(air) else f"drone {f.stem}: never above 2 m"
        if len(air) and gaps.max() > log_gap_s:
            warn(f"{msg}; log gap {gaps.max():.1f} s while airborne")
        elif len(air):
            ok(msg)
        else:
            warn(msg)
        if span and len(air):
            cover = max(0.0, (min(b, span[1]) - max(a, span[0])).total_seconds()) / max(1.0, (b - a).total_seconds())
            if cover == 0:
                off = (a - span[0]).total_seconds() / 3600
                fail(f"drone {f.stem} flew outside the radar data ({off:+.1f} h from radar start): check the drone or "
                     "radar clock / time zone now and write the right time down")
            elif cover < 0.95:
                warn(f"radar data covers {cover:.0%} of drone {f.stem}'s flight")
            else:
                ok(f"radar data covers drone {f.stem}'s whole flight")

    # runs and phases
    if (s.root / "runs.csv").exists() and span:
        from domeware import runs as R

        for r in R.load(s).itertuples():
            inside = r.start_utc >= span[0] - pd.Timedelta(seconds=5) and r.end_utc <= span[1] + pd.Timedelta(seconds=5)
            (ok if inside else warn)(f"{r.run_id} {r.part} {r.start_utc:%H:%M:%S}-{r.end_utc:%H:%M:%S}: "
                                    + ("inside the radar data" if inside else "partly outside the radar data"))

    # video
    from domeware.video import clips

    for c in clips(s):
        (ok if c["start_utc"] is not None else fail)(
            f"video {c['file'].name}: " + (f"starts {c['start_utc']:%H:%M:%S} UTC ({c['method']})" if c["start_utc"] is not None
                                           else "no time: copy its .SRT too, or add it to video_offsets.csv"))

    from domeware.images import pictures

    pics = pictures(s)
    if pics:
        timed = [p for p in pics if p["t_utc"] is not None]
        groups = sorted({p["group"] for p in pics if p["t_utc"] is None})
        (ok if len(timed) == len(pics) else warn)(
            f"pictures: {len(timed)}/{len(pics)} on the clock" + (f"; declare the clock for {', '.join(groups)} in image_clocks.csv" if groups else ""))

    for line in s.manifest["log"][-30:]:
        if "NOT converted" in line["msg"] or "not read" in line["msg"]:
            fail(line["msg"])
    print(f"dw check {s.root.name}")
    for level, m in out:
        print(f"  {level}  {m}")
    n_fail = sum(1 for lv, _ in out if lv == "FAIL")
    n_warn = sum(1 for lv, _ in out if lv == "WARN")
    print("All good: the data is complete." if not (n_fail or n_warn) else f"{n_fail} FAIL, {n_warn} WARN.")
    return 1 if n_fail else 0


def _truth_without_heights(s: Session) -> None:
    """Before the site and terrain are known, still read drone logs for times and heights above take-off."""
    from domeware.adapters import truth as T

    jobs = ([(f, T.read_dji_srt) for f in s.raw_files("dji_srt")] + [(f, T.read_ardupilot_bin) for f in s.raw_files("ardupilot_bin")]
            + [(f, T.read_rtklib_pos) for f in s.raw_files("rtklib_pos")] + [(f, T.read_mavlink_tlog) for f in s.raw_files("mavlink_tlog")])
    out = s.streams / "truth"
    for f, reader in jobs:
        try:
            df, _ = reader(f)
        except Exception as e:  # reported by the check, never fatal
            s.note(f"truth: {f.name}: NOT converted: {e}")
            continue
        out.mkdir(parents=True, exist_ok=True)
        from domeware.replay import unique_stem

        base = unique_stem(f, [j[0] for j in jobs])
        for vid, g in (df.groupby("vehicle_id") if "vehicle_id" in df else [(None, df)]):
            stem = base if vid is None else f"{base}_sys{int(vid)}"
            if not (out / f"{stem}.parquet").exists():  # never overwrite a full conversion with this partial one
                g.reset_index(drop=True).to_parquet(out / f"{stem}.parquet", index=False)
