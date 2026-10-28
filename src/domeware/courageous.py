"""The radar's output in the COURAGEOUS data format (CWA 18150 Annex H, JSON Schema draft 07, schema 0.4.1).

This is the format the EU C-UAS test community (COURAGEOUS, COURAGEOUS2) uses for a system's detection, tracking
and identification output, so a session can be handed to evaluators and opened with their tools (track2kml).
One file per radar stream and run: views/courageous_<radar>_<run>.json.

Mapping from Robin IRIS (written into the sidecar note, too):
  track             -> Track, uas_id = radar track id
  track state       -> TrackingRecord: time (Unix ms, UTC), Position3d, velocity ENU, alarm, classification
  DRONE             -> "UAV"; SUSPECTED_DRONE and UNCLASSIFIED -> "Unknown"; anything else -> "Other"
                       (Robin's own class is kept in `identification`)
  alarm             -> active = Robin's alarm flag; certainty = 1 when active, 0 when not (IRIS gives no alarm
                       certainty; its score is a classification score and is not used for this)
  potential drone plots -> one Detection set, classification "Unknown", no alarm
  heights           -> the radar's own altitude values. The schema asks for EGM96 heights; the radar's altitude datum
                       is still UNVERIFIED (see the session report), so heights are passed through, not converted.
The schema file is not in the repo (it has no licence); `dw setup` fetches it for validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from domeware.session import Session

SCHEMA_VERSION = "0.4.1"
SCHEMA_PATH = Path.home() / "Work" / "domeware-vendor" / "courageous" / "courageous.schema.json"
CLASS_MAP = {"DRONE": "UAV", "SUSPECTED_DRONE": "Unknown", "UNCLASSIFIED": "Unknown"}


def _ms(t: pd.Series) -> list[int]:
    from domeware.compare import _ns

    return [int(v // 1_000_000) for v in _ns(t)]


def document(tracks: pd.DataFrame, plots: pd.DataFrame | None, site: dict, system_name: str) -> dict:
    n = 0
    out_tracks = []
    for tid, g in tracks.sort_values("t_utc").groupby("track_id"):
        recs = []
        vel = {"v_e_mps", "v_n_mps", "v_u_mps"} <= set(g.columns)
        for i, (ms, r) in enumerate(zip(_ms(g["t_utc"]), g.itertuples(), strict=True)):
            cls = str(getattr(r, "class_name", "") or "UNCLASSIFIED")
            active = bool(getattr(r, "alarm", False)) if pd.notna(getattr(r, "alarm", None)) else False
            rec = {"record_number": n + i, "time": ms, "classification": CLASS_MAP.get(cls, "Other"),
                   "identification": f"Robin class {cls}",
                   "alarm": {"active": active, "certainty": 1.0 if active else 0.0},
                   "location": {"$type": "Position3d", "lat": float(r.lat_deg), "lon": float(r.lon_deg),
                                "height_amsl": float(r.alt_m)}}
            if vel and pd.notna(r.v_e_mps) and pd.notna(r.v_n_mps) and pd.notna(r.v_u_mps):
                rec["velocity"] = {"east": float(r.v_e_mps), "north": float(r.v_n_mps), "up": float(r.v_u_mps)}
            recs.append(rec)
        n += len(recs)
        out_tracks.append({"uas_id": int(tid), "name": f"IRIS track {tid}", "records": recs})
    detection = []
    if plots is not None and len(plots) and {"lat_deg", "lon_deg", "alt_m"} <= set(plots.columns):
        p = plots.dropna(subset=["lat_deg", "lon_deg", "alt_m"]).sort_values("t_utc")
        recs = [{"record_number": n + i, "time": ms, "classification": "Unknown",
                 "location": {"$type": "Position3d", "lat": float(r.lat_deg), "lon": float(r.lon_deg), "height_amsl": float(r.alt_m)}}
                for i, (ms, r) in enumerate(zip(_ms(p["t_utc"]), p.itertuples(), strict=True))]
        if recs:
            detection.append({"name": "IRIS potential drone plots", "uas_id": None, "records": recs})
    return {"version": SCHEMA_VERSION, "vendor_name": "Robin Radar Systems", "system_name": system_name,
            "static_cuas_location": {"lat": float(site["lat_deg"]), "lon": float(site["lon_deg"]), "height_amsl": float(site["alt_m"])},
            "detection": detection, "tracks": out_tracks}


def validate(doc: dict) -> list[str]:
    """Schema problems (empty = valid). Says so when the schema is not on this laptop (run dw setup)."""
    if not SCHEMA_PATH.exists():
        return [f"schema not found at {SCHEMA_PATH}: run dw setup"]
    import jsonschema

    v = jsonschema.Draft7Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in list(v.iter_errors(doc))[:20]]


def export(s: Session) -> list[Path]:
    from domeware import runs as R

    site = s.manifest.get("site")
    if not site:
        raise SystemExit("No radar site in the session: convert the radar data first (dw convert).")
    windows = {"session": (None, None)}
    if (s.root / "runs.csv").exists():
        df = R.load(s)
        windows = {r.run_id: (r.start_utc, r.end_utc) for r in df[df["part"] == "RUN"].itertuples()} or windows
    written = []
    s.views.mkdir(parents=True, exist_ok=True)
    for name in s.manifest["streams"]:
        tp = s.streams / name / "tracks.parquet"
        if not tp.exists():
            continue
        tr = pd.read_parquet(tp)
        tr["t_utc"] = pd.to_datetime(tr["t_utc"], utc=True)
        pp = s.streams / name / "plots.parquet"
        pl = pd.read_parquet(pp) if pp.exists() else None
        if pl is not None:
            pl["t_utc"] = pd.to_datetime(pl["t_utc"], utc=True)
        for run_id, (a, b) in windows.items():
            cut = tr if a is None else tr[(tr["t_utc"] >= a) & (tr["t_utc"] <= b)]
            pcut = pl if pl is None or a is None else pl[(pl["t_utc"] >= a) & (pl["t_utc"] <= b)]
            if cut.empty and (pcut is None or pcut.empty):
                continue
            doc = document(cut.dropna(subset=["lat_deg", "lon_deg", "alt_m"]), pcut, site,
                           f"Robin IRIS at {site.get('name') or 'site'}")
            out = s.views / f"courageous_{name}_{run_id}.json"
            out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            problems = validate(doc)
            note = {"file": out.name, "schema": f"COURAGEOUS {SCHEMA_VERSION}", "valid": not problems, "problems": problems,
                    "mapping": __doc__.split("Mapping from Robin IRIS")[1].split("The schema file")[0].strip()}
            out.with_suffix(".notes.json").write_text(json.dumps(note, indent=1, ensure_ascii=False), encoding="utf-8")
            s.note(f"courageous: {out.name}: {len(doc['tracks'])} tracks, "
                   f"{sum(len(t['records']) for t in doc['tracks'])} records; schema check: {'OK' if not problems else problems[0]}")
            written.append(out)
    return written
