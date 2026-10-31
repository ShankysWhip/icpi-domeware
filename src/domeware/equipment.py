"""Equipment: everything (and everyone) at the test, placed in the replay: radar, operator, drone take-offs, pilots, weather
station, RF analyser, cameras and later any other sensor or effector.

The list is built from the data where it can be (radar from its own declaration, take-off from the drone log, the
weather station from its export header) and completed by the team in <session>/equipment.csv (an older parties.csv is read too):

  name,kind,lat_deg,lon_deg,height_m,heading_deg,fov_deg,range_m,note
  PR200 (Niklas),rf,65.17649,26.04319,1.5,,,,beside the drone pilot, position not measured
  EO camera,camera,65.1770,26.0441,3,200,30,800,declared by the team

kind: one of KINDS. height_m: above the ground at that point (default per kind, marked as assumed).
heading_deg + fov_deg (+ range_m, default 500) draw a coverage wedge: DECLARED by the team, never measured here.
A team row with the same name as an automatic one replaces it. Every item carries its source.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from domeware.session import Session

KINDS = ("radar", "operator", "drone_home", "pilot", "weather", "rf", "camera", "sensor", "effector", "other")
RGB = {"radar": [235, 110, 0], "operator": [215, 125, 20], "drone_home": [95, 110, 130], "pilot": [190, 100, 25],
       "weather": [30, 140, 220], "rf": [120, 85, 225], "camera": [15, 135, 115], "sensor": [80, 80, 215],
       "effector": [220, 60, 80], "other": [110, 115, 125]}  # mid-tones: readable on dark and light viewer themes
DEFAULT_HEIGHT_M = {"drone_home": 0.0, "pilot": 1.7, "operator": 1.5, "rf": 1.5, "weather": 2.0, "camera": 2.0}
COLUMNS = ["name", "kind", "lat_deg", "lon_deg", "height_m", "heading_deg", "fov_deg", "range_m", "note"]
KIND_LABEL = {"drone_home": "take-off", "rf": "RF analyser", "weather": "weather station"}
LABEL_NEAR_M, LABEL_STEP_M = 140.0, 18.0  # labels closer than this to one already drawn go up by a step each
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
    """Rows from equipment.csv; a bad row is left out with its reason, never half-used."""
    p = next((s.root / n for n in ("equipment.csv", "parties.csv") if (s.root / n).exists()), None)
    if p is None:
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
            problems.append(f"{p.name} row {i + 2}: {why}: left out")
            continue
        rows.append({"name": name, "kind": kind, "lat_deg": lat, "lon_deg": lon,
                     **{c: _num(r.get(c, "")) for c in ("height_m", "heading_deg", "fov_deg", "range_m")},
                     "note": r.get("note", "").strip(), "source": f"{p.name} (team)"})
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
    """Write an empty equipment.csv for the team (header only) if there is none. True if written."""
    p = s.root / "equipment.csv"
    if p.exists() or (s.root / "parties.csv").exists():
        return False
    p.write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    return True


def wind_vectors(speed_ms, dir_from_deg) -> np.ndarray:
    """Arrow along the wind (where it blows TO), length WIND_M_PER_MS per m/s; x east, y north."""
    to = np.radians(np.asarray(dir_from_deg, float) + 180.0)
    sp = np.asarray(speed_ms, float) * WIND_M_PER_MS
    return np.column_stack([sp * np.sin(to), sp * np.cos(to), np.zeros_like(sp)]).astype(np.float32)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "item"


def label_lifts(items: list[tuple[float, float, float, str]], camera=None, fixed=(), fov_deg: float = 55.0,
                view_h_px: float = 560.0) -> list[float]:
    """How high (m) each label goes so that none hides another. With the opening camera (position, target) the names
    are projected to the screen and each, nearest first, rises in steps of about 6 screen pixels until its box is clear
    of the others (`fixed` names stay put: the radar's, the north arrow's). Without a camera: a step up for every
    earlier label within LABEL_NEAR_M. A small view (a laptop) is assumed, so a bigger window only spreads them further."""
    if camera is None:
        placed, out = [(0.0, 0.0)], []
        for e, n, _u, _text in items:
            out.append(LABEL_STEP_M * sum(1 for pe, pn in placed if np.hypot(e - pe, n - pn) < LABEL_NEAR_M))
            placed.append((e, n))
        return out
    pos, target = (np.asarray(v, dtype=float) for v in camera)
    fwd = (target - pos) / np.linalg.norm(target - pos)
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    focal = view_h_px / 2 / np.tan(np.radians(fov_deg) / 2)

    def box(e, n, u, text):
        v = np.array([e, n, u]) - pos
        d = float(v @ fwd)
        if d <= 1.0:
            return None  # behind the camera
        x, y, w = float(v @ right) / d * focal, float(v @ up) / d * focal, 7.5 * len(text) + 12
        return (x - w / 2, x + w / 2, y - 6, y + 16)

    def clear(b, taken):
        return all(b[1] < t[0] or t[1] < b[0] or b[3] < t[2] or t[3] < b[2] for t in taken)

    taken = [b for b in (box(*f) for f in fixed) if b]
    depth = [float((np.array(it[:3]) - pos) @ fwd) for it in items]
    out = [0.0] * len(items)
    for i in sorted(range(len(items)), key=lambda k: depth[k]):
        e, n, u, text = items[i]
        step = 6.0 * max(depth[i], 1.0) / (focal * max(abs(float(up[2])), 0.2))  # metres that rise 6 px on screen
        lift, b = 0.0, None
        for k in range(60):
            lift = k * step
            b = box(e, n, u + lift, text)
            if b is None or clear(b, taken):
                break
        out[i] = float(lift)
        if b is not None:
            taken.append(b)
    return out


def log_equipment(rec, s: Session, site: dict, span: tuple[pd.Timestamp, pd.Timestamp] | None, stats: dict,
                  camera=None) -> list[str]:
    """Draw every item in world/equipment; the on-site wind as an arrow at the weather station. Returns legend lines.
    `camera` (position, target) is where the 3D view opens: the labels are placed so none hides another there."""
    import rerun as rr

    from domeware.replay import enu

    df, problems = load(s)
    for msg in problems:
        s.note(msg)
    if not len(df):
        return []
    ground = _ground_heights(s, site, df)
    rows, notes, assumed = [], [], False
    homes = int((df["kind"] == "drone_home").sum())  # a swarm's take-offs side by side: one label for all of them
    todo = []  # labels: drawn after the loop, once their heights are known
    for r, g in zip(df.itertuples(), ground, strict=True):
        e, n, u = (float(v) for v in enu(r.lat_deg, r.lon_deg, g + r.height_m, site["lat_deg"], site["lon_deg"], site["alt_m"]))
        _, _, u0 = (float(v) for v in enu(r.lat_deg, r.lon_deg, g, site["lat_deg"], site["lon_deg"], site["alt_m"]))
        rows.append(f"| {r.name} | {KIND_LABEL.get(r.kind, r.kind)} | {np.hypot(e, n):.0f} m | "
                    f"{r.height_m:.1f} m{' *' if r.height_assumed else ''} | {_short(r.source)} |")
        assumed |= bool(r.height_assumed)
        if isinstance(r.note, str) and r.note:
            notes.append(f"- **{r.name}**: {r.note}")
        if r.kind == "radar" and abs(e) < 1 and abs(n) < 1:
            continue  # the site radar is already drawn with its mast and range rings
        path, rgb = f"world/equipment/{_slug(r.name)}", RGB[r.kind]
        crowd = r.kind == "drone_home" and homes > 3
        label = f"take-offs ({homes} drones)" if crowd else r.name
        first = not crowd or r.Index == df.index[df["kind"] == "drone_home"][0]
        rec.log(path, rr.Points3D([[e, n, u]], colors=[rgb], radii=2.5), static=True)
        if first:
            todo.append((path, e, n, u, label, rgb))
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
    # each label stands on a thin leader, as high as it needs so that none hides another in the opening view
    ground0 = (site["ground_level_m"] - site["alt_m"]) if site.get("ground_level_m") is not None else 0.0
    fixed = [(0.0, 0.0, 0.0, site.get("name") or "radar"), (0.0, 205.0, ground0, "N")]
    lifts = label_lifts([(e, n, u, label) for _, e, n, u, label, _ in todo], camera, fixed)
    for (path, e, n, u, label, rgb), lift in zip(todo, lifts, strict=True):
        rec.log(f"{path}/label", rr.Points3D([[e, n, u + lift]], colors=[rgb], radii=0.6, labels=[label],
                                             show_labels=True), static=True)
        if lift:
            rec.log(f"{path}/leader", rr.LineStrips3D([[[e, n, u], [e, n, u + lift]]], colors=[[*rgb, 170]], radii=0.12),
                    static=True)
    stats["label_lifts_m"] = {label: lift for (_, _, _, _, label, _), lift in zip(todo, lifts, strict=True) if lift}
    stats["equipment"] = df[["name", "kind", "source"]].to_dict("records")
    return ["", "# Equipment", "", "| Equipment | Type | From radar | Height AGL | Source |", "|---|---|--:|--:|---|", *rows,
            *(["", "\\* height assumed: not stated in any file"] if assumed else []), *(["", *notes] if notes else [])]


def _short(source: str) -> str:
    """'DJI log header (dji_details/details.json)' -> 'DJI log header'; the full source stays in dw equipment."""
    return re.split(r" \(|;", str(source))[0].strip()


def _ground_heights(s: Session, site: dict, df: pd.DataFrame) -> list[float]:
    """Ground at each item in the radar's height frame: DEM + the radar alignment, else the radar's ground level."""
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
    rec.send_columns("world/equipment/wind", indexes=[t],
                     columns=[*rr.Arrows3D.columns(vectors=vec).partition([1] * len(vec)),
                              *rr.Arrows3D.columns(labels=labels)])
    # the numbers stay off the 3D (they sat on the station's name): hover shows them, the HUD and charts list them
    rec.log("world/equipment/wind", rr.Arrows3D.from_fields(origins=[at], colors=[RGB["weather"]], radii=1.2,
                                                          show_labels=False), static=True)
    stats["wind_arrow_minutes"] = len(w)
