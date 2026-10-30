"""Everyone and everything at the test, placed in the replay: radar, operator, drone take-offs, pilots, weather
station, RF analyser, cameras and later any other sensor or effector.

The list is built from the data where it can be (radar from its own declaration, take-off from the drone log, the
weather station from its export header) and completed by the team in <session>/parties.csv:

  name,kind,lat_deg,lon_deg,height_m,heading_deg,fov_deg,range_m,note
  PR200 (Niklas),rf,65.17649,26.04319,1.5,,,,beside the drone pilot, position not measured
  EO camera,camera,65.1770,26.0441,3,200,30,800,declared by the team

kind: one of KINDS. height_m: above the ground at that point (default per kind, marked as assumed).
heading_deg + fov_deg (+ range_m, default 500) draw a coverage wedge: DECLARED by the team, never measured here.
A team row with the same name as an automatic one replaces it. Every party carries its source.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from domeware.session import Session

KINDS = ("radar", "operator", "drone_home", "pilot", "weather", "rf", "camera", "sensor", "effector", "other")
RGB = {"radar": [255, 120, 0], "operator": [255, 160, 60], "drone_home": [235, 235, 235], "pilot": [255, 190, 140],
       "weather": [70, 160, 255], "rf": [210, 170, 255], "camera": [255, 235, 170], "sensor": [180, 180, 255],
       "effector": [255, 80, 80], "other": [200, 200, 200]}
DEFAULT_HEIGHT_M = {"drone_home": 0.0, "pilot": 1.7, "operator": 1.5, "rf": 1.5, "weather": 2.0, "camera": 2.0}
COLUMNS = ["name", "kind", "lat_deg", "lon_deg", "height_m", "heading_deg", "fov_deg", "range_m", "note"]
WIND_M_PER_MS = 10.0  # arrow length in the replay: 10 m per 1 m/s of wind


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "."))
    except ValueError:
        return None
    return f if np.isfinite(f) else None


def auto(s: Session) -> list[dict]:
    """Parties the data itself declares. Nothing here is guessed: what a file does not state stays empty."""
    out = []
    site = s.manifest.get("site")
    if site:
        h = site["alt_m"] - site["ground_level_m"] if site.get("ground_level_m") is not None else None
        out.append({"name": site.get("name") or "radar", "kind": "radar", "lat_deg": site["lat_deg"],
                    "lon_deg": site["lon_deg"], "height_m": h, "source": site.get("source") or "session site"})
    for f in sorted(s.streams.glob("truth/*.parquet")):  # first fix of each drone = its take-off
        d = pd.read_parquet(f)
        if {"t_utc", "lat_deg", "lon_deg"} <= set(d.columns) and len(d := d.dropna(subset=["lat_deg", "lon_deg"])):
            first = d.sort_values("t_utc").iloc[0]
            out.append({"name": f"{f.stem} take-off", "kind": "drone_home", "lat_deg": float(first["lat_deg"]),
                        "lon_deg": float(first["lon_deg"]), "height_m": 0.0, "source": f"first fix in truth/{f.name}"})
    for f in sorted(s.streams.glob("dji_details/*.json")):  # header of an encrypted DJI log: readable without a key
        d = json.loads(f.read_text(encoding="utf-8"))
        if _num(d.get("latitude")) and _num(d.get("longitude")):
            out.append({"name": f"{d.get('aircraft_name') or 'DJI'} take-off", "kind": "drone_home",
                        "lat_deg": float(d["latitude"]), "lon_deg": float(d["longitude"]), "height_m": 0.0,
                        "source": f"DJI log header ({f.parent.name}/{f.name})"})
    for key, meta in s.manifest.get("streams", {}).items():
        if key.startswith("weather/onsite_vaisala/") and _num(meta.get("lat_deg")) and _num(meta.get("lon_deg")):
            out.append({"name": f"Weather {meta.get('device') or 'station'}".strip(), "kind": "weather",
                        "lat_deg": _num(meta["lat_deg"]), "lon_deg": _num(meta["lon_deg"]), "height_m": None,
                        "source": f"export header ({key.rsplit('/', 1)[1]}); sensor height not in the file"})
    return out


def team(s: Session) -> tuple[list[dict], list[str]]:
    """Rows from parties.csv; a bad row is left out with its reason, never half-used."""
    p = s.root / "parties.csv"
    if not p.exists():
        return [], []
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    rows, problems = [], []
    for i, r in df.iterrows():
        name, kind = r.get("name", "").strip(), r.get("kind", "").strip().lower()
        lat, lon = _num(r.get("lat_deg", "")), _num(r.get("lon_deg", ""))
        why = ("no name" if not name else f"kind '{kind}' not in {', '.join(KINDS)}" if kind not in KINDS
               else "lat_deg/lon_deg missing or out of range" if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180
               else None)
        if why:
            problems.append(f"parties.csv row {i + 2}: {why}: left out")
            continue
        rows.append({"name": name, "kind": kind, "lat_deg": lat, "lon_deg": lon,
                     **{c: _num(r.get(c, "")) for c in ("height_m", "heading_deg", "fov_deg", "range_m")},
                     "note": r.get("note", "").strip(), "source": "parties.csv (team)"})
    return rows, problems


def load(s: Session) -> tuple[pd.DataFrame, list[str]]:
    rows, problems = team(s)
    by_name = {r["name"].lower(): r for r in auto(s)}
    for r in rows:
        by_name[r["name"].lower()] = r  # the team's word wins over an automatic row of the same name
    df = pd.DataFrame(list(by_name.values()), columns=[*COLUMNS, "source"])
    for c in ("lat_deg", "lon_deg", "height_m", "heading_deg", "fov_deg", "range_m"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["height_assumed"] = df["height_m"].isna()
    df["height_m"] = df["height_m"].fillna(df["kind"].map(DEFAULT_HEIGHT_M)).fillna(2.0)
    return df, problems


def template(s: Session) -> bool:
    """Write an empty parties.csv for the team (header only) if there is none. True if written."""
    p = s.root / "parties.csv"
    if p.exists():
        return False
    p.write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    return True


def wind_vectors(speed_ms, dir_from_deg) -> np.ndarray:
    """Arrow along the wind (where it blows TO), length WIND_M_PER_MS per m/s; x east, y north."""
    to = np.radians(np.asarray(dir_from_deg, float) + 180.0)
    sp = np.asarray(speed_ms, float) * WIND_M_PER_MS
    return np.column_stack([sp * np.sin(to), sp * np.cos(to), np.zeros_like(sp)]).astype(np.float32)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "party"


def log_parties(rec, s: Session, site: dict, span: tuple[pd.Timestamp, pd.Timestamp] | None, stats: dict) -> list[str]:
    """Draw every party in world/parties; the on-site wind as an arrow at the weather station. Returns legend lines."""
    import rerun as rr

    from domeware.replay import enu

    df, problems = load(s)
    for msg in problems:
        s.note(msg)
    if not len(df):
        return []
    ground = _ground_heights(s, site, df)
    legend = ["", "# Parties", "", "Where everyone was. Source in brackets; *assumed* = height not stated anywhere."]
    for r, g in zip(df.itertuples(), ground, strict=True):
        e, n, u = (float(v) for v in enu(r.lat_deg, r.lon_deg, g + r.height_m, site["lat_deg"], site["lon_deg"], site["alt_m"]))
        _, _, u0 = (float(v) for v in enu(r.lat_deg, r.lon_deg, g, site["lat_deg"], site["lon_deg"], site["alt_m"]))
        legend.append(f"- **{r.name}** ({r.kind}): {np.hypot(e, n):.0f} m from the radar, "
                      f"{r.height_m:.1f} m above ground{' *assumed*' if r.height_assumed else ''} [{r.source}]"
                      + (f". {r.note}" if isinstance(r.note, str) and r.note else ""))
        if r.kind == "radar" and abs(e) < 1 and abs(n) < 1:
            continue  # the site radar is already drawn with its mast and range rings
        path, rgb = f"world/parties/{_slug(r.name)}", RGB[r.kind]
        rec.log(path, rr.Points3D([[e, n, u]], colors=[rgb], radii=2.5, labels=[r.name], show_labels=True), static=True)
        th = np.linspace(0, 2 * np.pi, 49)
        ring = np.column_stack([e + 8 * np.sin(th), n + 8 * np.cos(th), np.full_like(th, u0 + 0.3)])
        strips = [ring, [[e, n, u0], [e, n, u]]] if r.height_m > 0.3 else [ring]
        rec.log(f"{path}/mark", rr.LineStrips3D(strips, colors=[rgb], radii=0.4), static=True)
        if r.heading_deg is not None and r.fov_deg is not None and np.isfinite(r.heading_deg) and np.isfinite(r.fov_deg):
            reach = r.range_m if r.range_m is not None and np.isfinite(r.range_m) else 500.0
            a = np.radians(np.linspace(r.heading_deg - r.fov_deg / 2, r.heading_deg + r.fov_deg / 2, 33))
            arc = np.column_stack([e + reach * np.sin(a), n + reach * np.cos(a), np.full_like(a, u)])
            wedge = np.vstack([[e, n, u], arc, [e, n, u]])
            rec.log(f"{path}/coverage_DECLARED", rr.LineStrips3D([wedge], colors=[rgb], radii=0.5,
                                                                 labels=[f"{r.name}: declared {r.fov_deg:.0f} deg, {reach:.0f} m"]),
                    static=True)
        if r.kind == "weather":
            _log_wind(rec, s, [e, n, u + 1.0], span, stats)
    stats["parties"] = df[["name", "kind", "source"]].to_dict("records")
    return legend


def _ground_heights(s: Session, site: dict, df: pd.DataFrame) -> list[float]:
    """Ground at each party in the radar's height frame: DEM + the radar alignment, else the radar's ground level."""
    fallback = site.get("ground_level_m") if site.get("ground_level_m") is not None else site["alt_m"]
    if not (s.env / "dem.tif").exists():
        return [fallback] * len(df)
    from domeware.convert import terrain_alignment_m
    from domeware.env import sample_dem

    align = terrain_alignment_m(s)
    out = []
    for r in df.itertuples():
        g = sample_dem(s.env / "dem.tif", r.lat_deg, r.lon_deg)
        out.append(fallback if g is None else g + align)
    return out


def _log_wind(rec, s: Session, at: list[float], span, stats: dict) -> None:
    """On-site wind as an arrow at the station, one state per measured minute (the viewer holds it until the next)."""
    import rerun as rr

    from domeware.replay import _ts

    p = s.streams / "weather" / "onsite_vaisala.parquet"
    if not p.exists():
        return
    w = pd.read_parquet(p).dropna(subset=["wind_ms", "wind_dir_deg"]).sort_values("t_utc")
    w["t_utc"] = pd.to_datetime(w["t_utc"], utc=True).dt.as_unit("ns")  # Parquet may hold us
    if span is not None:  # from the minute before the activity to its end
        w = w[(w["t_utc"] >= span[0] - pd.Timedelta("2min")) & (w["t_utc"] <= span[1])]
    if not len(w):
        return
    gust = w["gust_ms"] if "gust_ms" in w else pd.Series(np.nan, index=w.index)
    labels = [f"wind {v:.1f} m/s from {d:.0f} deg" + (f", gust {q:.1f}" if np.isfinite(q) else "")
              for v, d, q in zip(w["wind_ms"], w["wind_dir_deg"], gust, strict=True)]
    t = rr.TimeColumn("utc", timestamp=_ts(w["t_utc"]))
    vec = wind_vectors(w["wind_ms"], w["wind_dir_deg"])
    rec.send_columns("world/parties/wind", indexes=[t],
                     columns=[*rr.Arrows3D.columns(vectors=vec).partition([1] * len(vec)),
                              *rr.Arrows3D.columns(labels=labels)])
    rec.log("world/parties/wind", rr.Arrows3D.from_fields(origins=[at], colors=[RGB["weather"]], radii=1.2,
                                                          show_labels=True), static=True)
    stats["wind_arrow_minutes"] = len(w)
