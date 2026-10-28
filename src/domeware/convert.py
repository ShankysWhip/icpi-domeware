"""Run every adapter that has input in the session. Each writes Parquet under streams/<source>/."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from domeware.session import Session


def _pick_focus_track(tracks_parquet: Path) -> int | None:
    """The track the replay centres on: most DRONE-class states, else most alarm states. None if neither."""
    t = pd.read_parquet(tracks_parquet, columns=["track_id", "class_name", "alarm"])
    for mask in (t["class_name"].eq("DRONE"), t["alarm"].fillna(False).astype(bool)):
        counts = t[mask].groupby("track_id").size()
        if len(counts):
            return int(counts.idxmax())
    return None


def convert_robin(s: Session) -> None:
    from domeware.adapters import robin_xml as R

    xmls = s.raw_files("robin_xml")
    if not xmls:
        return
    cfg_files = s.raw_files("robin_cfg")
    cfg_dir = next((p.parent for p in cfg_files if p.name.lower() == "irisradar.config"), None)
    raw = s.raw_files("robin_raw")
    sha = {s.root / r["path"]: r["sha256"] for r in s.manifest["raw"]}
    for xml in xmls:
        out = s.streams / ("robin" if len(xmls) == 1 else f"robin_{xml.stem}")
        prev = s.manifest["streams"].get(out.name) or {}
        if (prev.get("source_sha256") == sha.get(xml) and prev.get("adapter") == f"{R.ADAPTER} {R.ADAPTER_VERSION}"
                and (out / "tracks.parquet").exists()):
            print(f"robin: {xml.name}: unchanged, kept")  # a full day of radar takes minutes; never redo it for nothing
            continue
        rep = R.convert(xml, out, cfg_dir, raw, quicklook=False, hash_raw=False)
        focus = _pick_focus_track(out / "tracks.parquet") if (out / "tracks.parquet").exists() else None
        if (out / "tracks.parquet").exists():  # quicklooks from the written tables: no second parse of the XML
            try:
                site_j = json.loads((out / "site.json").read_text(encoding="utf-8")).get("radar") or {}
                p = site_j.get("position") or {}
                site_pos = {"lat_deg": p["lat_deg"], "lon_deg": p["lon_deg"], "alt_m": p.get("alt_m"),
                            "ground_level_m": site_j.get("ground_level_m")} if p.get("lat_deg") is not None else None
                tables = {n: pd.read_parquet(out / f"{n}.parquet") for n in ("tracks", "track_summary", "plots")
                          if (out / f"{n}.parquet").exists()}
                R.make_quicklooks(tables, site_pos, out, focus)
            except Exception as e:  # pictures are optional; the data is already written
                s.note(f"robin: quicklooks not drawn ({e})")
        t = rep["tracks"]
        s.manifest["streams"][out.name] = {"adapter": f"{R.ADAPTER} {R.ADAPTER_VERSION}", "source": xml.name,
                                           "source_sha256": sha.get(xml),
                                           "focus_track": focus, "tracks_kept": t.get("rows_kept"),
                                           "duplicates_dropped": t.get("duplicates_dropped"),
                                           "unique_tracks": t.get("unique_tracks"),
                                           "secrets_check_passed": rep["secrets_check"]["passed"]}
        site = json.loads((out / "site.json").read_text(encoding="utf-8"))
        radar = site.get("radar") or {}
        pos = radar.get("position") or {}
        if pos.get("lat_deg") is not None and not s.manifest.get("site"):
            s.manifest["site"] = {"name": radar.get("name"), "lat_deg": pos["lat_deg"], "lon_deg": pos["lon_deg"],
                                  "alt_m": pos.get("alt_m"), "alt_datum": pos.get("alt_datum"),
                                  "ground_level_m": radar.get("ground_level_m"),
                                  "azimuth_deg": (radar.get("orientation") or {}).get("azimuth_deg"),
                                  "source": f"{out.name}/site.json (declared by the radar)"}
        s.note(f"robin: {xml.name}: {t.get('rows_kept')} track states, {t.get('unique_tracks')} tracks, "
               f"focus track {focus}")


def convert_dji_csv(s: Session) -> None:
    files = s.raw_files("dji_csv")
    if not files:
        return
    from domeware.adapters import dji as D

    site = s.manifest.get("site")
    ds = D.Site(site["lat_deg"], site["lon_deg"], site["alt_m"], name=site.get("name") or "site",
                note="radar-declared position, altitude datum unverified") if site else None
    for f in files:
        df = pd.read_csv(f, low_memory=False)
        ground = _dem_height_at_home(s, df)
        opts = D.ConvertOptions(site=ds, takeoff_ground_n2000_m=ground, source_sha256=D.sha256_file(str(f)),
                                session_id=s.manifest["session_id"])
        osd, events, meta = D.convert(df, opts)
        out = s.streams / "dji"
        out.mkdir(parents=True, exist_ok=True)
        from domeware.replay import unique_stem

        name = unique_stem(f, s.raw_files("dji_csv"))
        pq.write_table(osd, out / f"{name}_osd.parquet")
        pq.write_table(events, out / f"{name}_events.parquet")
        (out / f"{f.stem}_meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        s.manifest["streams"][f"dji/{f.stem}"] = {"adapter": f"dji {D.CONVERTER_VERSION}", "rows": osd.num_rows,
                                                  "ground_from_dem": ground is not None}
        s.note(f"dji: {f.name}: {osd.num_rows} frames, {events.num_rows} events")


def _dem_height_at_home(s: Session, df: pd.DataFrame) -> float | None:
    dem = s.env / "dem.tif"
    if not dem.exists():
        return None
    lat = pd.to_numeric(df.get("HOME.latitude", df.get("OSD.latitude")), errors="coerce").dropna()
    lon = pd.to_numeric(df.get("HOME.longitude", df.get("OSD.longitude")), errors="coerce").dropna()
    lat, lon = lat[lat != 0], lon[lon != 0]
    if lat.empty or lon.empty:
        return None
    from domeware.env import sample_dem

    return sample_dem(dem, float(lat.iloc[0]), float(lon.iloc[0]))


def terrain_alignment_m(s: Session) -> float:
    """Radar's declared ground level minus NLS DEM at the radar (0 if unknown). Used for terrain and truth heights."""
    site, dem = s.manifest.get("site") or {}, s.env / "dem.tif"
    if not dem.exists() or site.get("ground_level_m") is None:
        return 0.0
    from domeware.env import sample_dem

    g = sample_dem(dem, site["lat_deg"], site["lon_deg"])
    return 0.0 if g is None else site["ground_level_m"] - g


def convert_truth(s: Session) -> None:
    from domeware.adapters import truth as T

    jobs = ([(f, T.read_dji_srt) for f in s.raw_files("dji_srt")]
            + [(f, T.read_ardupilot_bin) for f in s.raw_files("ardupilot_bin")]
            + [(f, T.read_rtklib_pos) for f in s.raw_files("rtklib_pos")]
            + [(f, T.read_mavlink_tlog) for f in s.raw_files("mavlink_tlog")])
    if not jobs:
        return
    site = s.manifest.get("site")
    align = terrain_alignment_m(s)
    out = s.streams / "truth"
    out.mkdir(parents=True, exist_ok=True)
    for f, reader in jobs:
        try:
            df_all, meta = reader(f)
        except Exception as e:  # one bad file must not stop the others; the reason is recorded
            s.note(f"truth: {f.name}: NOT converted: {e}")
            continue
        # one stream per vehicle: a ground-station log can hold the whole swarm
        groups = df_all.groupby("vehicle_id") if "vehicle_id" in df_all else [(None, df_all)]
        from domeware.replay import unique_stem

        base = unique_stem(f, [j[0] for j in jobs])
        for vid, df in groups:
            df = df.reset_index(drop=True)
            stem = base if vid is None else f"{base}_sys{int(vid)}"
            ground = None
            if site and (s.env / "dem.tif").exists():
                from domeware.env import sample_dem

                ground = sample_dem(s.env / "dem.tif", float(df["lat_deg"].iloc[0]), float(df["lon_deg"].iloc[0]))
            df = T.to_radar_frame(df, site, ground, align) if site else df
            df["source"] = f.name
            df.to_parquet(out / f"{stem}.parquet", index=False)
            m = dict(meta, rows=len(df), ground_at_takeoff_n2000_m=ground, terrain_alignment_m=round(align, 2), file=f.name)
            s.manifest["streams"][f"truth/{stem}"] = m
            s.note(f"truth: {stem}: {len(df)} fixes, {df['t_utc'].iloc[0]} .. {df['t_utc'].iloc[-1]}"
                   + ("" if ground is not None else " (no DEM yet: run dw env, then dw convert, for heights)"))


def convert_planned(s: Session) -> None:
    """Skybrush shows -> streams/planned/<show>_<drone>.parquet (show time; heights above the show origin ground)."""
    files = s.raw_files("skybrush_skyc")
    if not files:
        return
    from domeware.adapters import skybrush as K
    from domeware.adapters import truth as T

    site, align = s.manifest.get("site"), terrain_alignment_m(s)
    out = s.streams / "planned"
    out.mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            df_all, meta = K.read_skyc(f)
        except Exception as e:
            s.note(f"planned: {f.name}: NOT converted: {e}")
            continue
        ground = None
        if (s.env / "dem.tif").exists():
            from domeware.env import sample_dem

            ground = sample_dem(s.env / "dem.tif", *meta["origin"])
        for drone, df in df_all.groupby("drone"):
            df = T.to_radar_frame(df.reset_index(drop=True), site, ground, align) if site else df
            df.to_parquet(out / f"{f.stem}_{drone}.parquet", index=False)
        s.manifest["streams"][f"planned/{f.stem}"] = dict(meta, ground_at_origin_n2000_m=ground)
        s.note(f"planned: {f.name}: {meta['drones']} drones, {meta['duration_s']:.0f} s show")


def report_unhandled(s: Session) -> None:
    hints = {
        "dji_flightrecord_encrypted": "DJI log v13+ is encrypted. Decode it with a DJI developer API key "
                                      "(python -m domeware.adapters.dji_decode) or export CSV from Airdata/DJI, "
                                      "then 'dw ingest' the CSV.",
        "unknown": "Unknown file type: stored raw only.",
    }
    for role, hint in hints.items():
        files = s.raw_files(role)
        if files:
            s.note(f"{role}: {len(files)} file(s). {hint}")


def run(s: Session) -> None:
    convert_robin(s)
    convert_dji_csv(s)
    convert_truth(s)
    convert_planned(s)
    report_unhandled(s)
    s.save()
