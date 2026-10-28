"""Build the replay of a session: real terrain + imagery, radar, all tracks, drone truth, on one UTC timeline.

Frame: local ENU (x=east, y=north, z=up, metres) centred on the radar position declared in the data.
Writes views/replay.rrd (+ .rbl blueprint) and quicklook PNGs. Never starts a viewer or a server.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import rerun as rr  # noqa: E402
import rerun.blueprint as rrb  # noqa: E402
import trimesh  # noqa: E402
from PIL import Image  # noqa: E402
from pyproj import Transformer  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402

from domeware.session import Session  # noqa: E402

TM35_TO_LL = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
LL_TO_TM35 = Transformer.from_crs("EPSG:4326", "EPSG:3067", always_xy=True)
CLASS_RANK = ["UNCLASSIFIED", "OTHER", "VEHICLE", "SUSPECTED_DRONE", "DRONE"]
CLASS_RGB = {"DRONE": [30, 120, 255], "SUSPECTED_DRONE": [255, 140, 0], "VEHICLE": [0, 170, 120],
             "OTHER": [120, 120, 120], "UNCLASSIFIED": [180, 180, 180]}
ALARM_RGB = [230, 40, 40]
# one colour per drone, kept apart from the radar class colours above; a radar track that was a drone wears its colour
DRONE_RGB = [[0, 230, 90], [255, 60, 200], [0, 210, 230], [240, 220, 40], [160, 90, 255], [150, 255, 60],
             [255, 150, 190], [0, 160, 140], [255, 255, 255], [200, 120, 60]]
DRONE_COLOUR_NAMES = ["green", "magenta", "cyan", "yellow", "purple", "lime", "pink", "teal", "white", "brown"]


def enu(lat, lon, h, lat0, lon0, h0):
    """Geodetic -> local ENU via ECEF (WGS84/GRS80 ellipsoid)."""
    a, f = 6378137.0, 1 / 298.257222101
    e2 = f * (2 - f)

    def ecef(la, lo, hh):
        la, lo = np.radians(la), np.radians(lo)
        n = a / np.sqrt(1 - e2 * np.sin(la) ** 2)
        return ((n + hh) * np.cos(la) * np.cos(lo), (n + hh) * np.cos(la) * np.sin(lo), (n * (1 - e2) + hh) * np.sin(la))

    x, y, z = ecef(np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float))
    x0, y0, z0 = ecef(lat0, lon0, h0)
    dx, dy, dz = x - x0, y - y0, z - z0
    la0, lo0 = math.radians(lat0), math.radians(lon0)
    e = -math.sin(lo0) * dx + math.cos(lo0) * dy
    nn = -math.sin(la0) * math.cos(lo0) * dx - math.sin(la0) * math.sin(lo0) * dy + math.cos(la0) * dz
    u = math.cos(la0) * math.cos(lo0) * dx + math.cos(la0) * math.sin(lo0) * dy + math.sin(la0) * dz
    return e, nn, u


def terrain_glb(s: Session, site: dict, inner_half_m: float = 1200.0, inner_step_m: float = 4.0,
                outer_step_m: float = 10.0):
    """Two-level terrain: a fine tile around the radar (where the action is) inside a coarser outer tile.

    Returns ([(name, glb_bytes), ...], info). Heights are aligned to the radar's own declared ground level.
    """
    if not (s.env / "dem.tif").exists():
        return [], {"terrain": "none (run dw env)"}
    from domeware.convert import terrain_alignment_m

    offset = terrain_alignment_m(s)
    with rasterio.open(s.env / "dem.tif") as src:
        full = tuple(src.bounds)
    x0, y0 = LL_TO_TM35.transform(site["lon_deg"], site["lat_deg"])
    inner = (max(full[0], x0 - inner_half_m), max(full[1], y0 - inner_half_m),
             min(full[2], x0 + inner_half_m), min(full[3], y0 + inner_half_m))
    tiles, info = [], {"vertical_alignment_m": round(offset, 2),
                       "vertical_alignment_note": "terrain shifted so DEM at the radar equals the radar's declared ground level"}
    for name, bounds, step, hole, tex in (("outer", full, outer_step_m, inner, 4096), ("inner", inner, inner_step_m, None, 4096)):
        glb, n = _terrain_tile(s, site, bounds, step, tex, hole, offset)
        tiles.append((name, glb))
        info[f"terrain_{name}"] = {"vertices": n, "step_m": step, "bounds_epsg3067": [round(v) for v in bounds]}
    return tiles, info


def _terrain_tile(s: Session, site: dict, bounds, step_m: float, max_tex: int, hole, offset: float):
    from rasterio.windows import from_bounds

    with rasterio.open(s.env / "dem.tif") as src:
        h, w = max(2, math.ceil((bounds[3] - bounds[1]) / step_m)), max(2, math.ceil((bounds[2] - bounds[0]) / step_m))
        z = src.read(1, window=from_bounds(*bounds, src.transform), out_shape=(h, w),
                     resampling=Resampling.bilinear).astype(np.float64)
        z[z == src.nodata] = np.nan
    z = np.where(np.isfinite(z), z, np.nanmedian(z))
    b = rasterio.coords.BoundingBox(*bounds)
    X, Y = np.meshgrid(np.linspace(b.left, b.right, w), np.linspace(b.top, b.bottom, h))  # row 0 = north
    lon, lat = TM35_TO_LL.transform(X.ravel(), Y.ravel())
    e, n, u = enu(lat, lon, z.ravel() + offset, site["lat_deg"], site["lon_deg"], site["alt_m"])
    verts = np.column_stack([e, n, u]).astype(np.float32)
    idx = np.arange(h * w).reshape(h, w)
    a, bb, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    if hole is not None:  # drop outer cells that the fine inner tile covers
        cx, cy = X[:-1, :-1].ravel() + step_m / 2, Y[:-1, :-1].ravel() - step_m / 2
        keep = ~((cx > hole[0]) & (cx < hole[2]) & (cy > hole[1]) & (cy < hole[3]))
        a, bb, c, d = a[keep], bb[keep], c[keep], d[keep]
    tris = np.concatenate([np.column_stack([a, c, d]), np.column_stack([a, d, bb])]).astype(np.uint32)
    uv = np.column_stack([((X - b.left) / (b.right - b.left)).ravel(), ((b.top - Y) / (b.top - b.bottom)).ravel()])
    ortho_p = s.env / "ortho.tif"
    if ortho_p.exists():
        with rasterio.open(ortho_p) as src:
            win = from_bounds(*bounds, src.transform)
            sc = max(1.0, max(win.width, win.height) / max_tex)
            rgb = src.read([1, 2, 3], window=win, out_shape=(3, int(win.height / sc), int(win.width / sc)),
                           resampling=Resampling.average)
        img = Image.fromarray(np.moveaxis(rgb, 0, -1))
    else:
        img = Image.new("RGB", (8, 8), (120, 130, 110))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.open(io.BytesIO(buf.getvalue())),
                                              baseColorFactor=[255, 255, 255, 255], metallicFactor=0.0,
                                              roughnessFactor=1.0, doubleSided=True)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False,
                           visual=trimesh.visual.TextureVisuals(uv=uv.astype(np.float32), material=mat))
    return mesh.export(file_type="glb", include_normals=True), int(len(verts))


def _dem_lookup(s: Session, align_m: float):
    """Vectorised ground height (radar frame) at TM35 x, y from env/dem.tif (nearest cell)."""
    with rasterio.open(s.env / "dem.tif") as src:
        z, b, (rx, ry) = src.read(1).astype(np.float64), src.bounds, src.res
        z[z == src.nodata] = np.nan
    z = np.where(np.isfinite(z), z, np.nanmedian(z)) + align_m

    def f(x, y):
        c = np.clip(((np.asarray(x) - b.left) / rx).astype(int), 0, z.shape[1] - 1)
        r = np.clip(((b.top - np.asarray(y)) / ry).astype(int), 0, z.shape[0] - 1)
        return z[r, c]

    return f


def _tm_to_enu(x, y, h, site):
    lon, lat = TM35_TO_LL.transform(np.asarray(x, float), np.asarray(y, float))
    e, n, u = enu(lat, lon, h, site["lat_deg"], site["lon_deg"], site["alt_m"])
    return np.column_stack([e, n, u]).astype(np.float32)


def log_features(rec, s: Session, site: dict, stats: dict) -> None:
    """Buildings (extruded), power lines and tree tops from env/features.gpkg + env/canopy.tif."""
    if not (s.env / "dem.tif").exists():
        return
    import geopandas as gpd

    from domeware.convert import terrain_alignment_m
    from domeware.features import canopy_points

    ground = _dem_lookup(s, terrain_alignment_m(s))
    fg = s.env / "features.gpkg"
    layers = {n for n, _ in __import__("pyogrio").list_layers(fg)} if fg.exists() else set()
    if "buildings" in layers:
        meshes = []
        for geom, h in zip(*(lambda g: (g.geometry, g["height_m"]))(gpd.read_file(fg, layer="buildings").explode(index_parts=False)), strict=False):
            if geom.is_empty or geom.area < 4:
                continue
            m = trimesh.creation.extrude_polygon(geom, float(h))
            base = float(ground([geom.centroid.x], [geom.centroid.y])[0])
            m.vertices = _tm_to_enu(m.vertices[:, 0], m.vertices[:, 1], m.vertices[:, 2] + base, site)
            meshes.append(m)
        if meshes:
            allm = trimesh.util.concatenate(meshes)
            rec.log("world/buildings", rr.Mesh3D(vertex_positions=allm.vertices.astype(np.float32),
                                                 triangle_indices=allm.faces.astype(np.uint32),
                                                 vertex_normals=allm.vertex_normals.astype(np.float32),
                                                 albedo_factor=[215, 205, 190, 255]), static=True)
            stats["buildings"] = len(meshes)
    if "power_lines" in layers:
        strips = []
        for geom, h in zip(*(lambda g: (g.geometry, g["height_m"]))(gpd.read_file(fg, layer="power_lines").explode(index_parts=False)), strict=False):
            seg = geom.segmentize(25.0)  # follow the terrain between mapped vertices
            xy = np.asarray(seg.coords)[:, :2]
            strips.append(_tm_to_enu(xy[:, 0], xy[:, 1], ground(xy[:, 0], xy[:, 1]) + float(h), site))
        if strips:
            rec.log("world/power_lines", rr.LineStrips3D(strips, colors=[[250, 220, 60]], radii=0.5), static=True)
            stats["power_line_strips"] = len(strips)
    pts = canopy_points(s)
    if pts is not None and len(pts[0]):
        x, y, h = pts
        pos = _tm_to_enu(x, y, ground(x, y) + h, site)
        t = np.clip(h / 30.0, 0, 1)[:, None]
        colors = (np.array([[110, 160, 70]]) * (1 - t) + np.array([[25, 75, 35]]) * t).astype(np.uint8)
        rec.log("world/trees", rr.Points3D(pos, colors=colors, radii=2.2), static=True)
        stats["tree_points"] = int(len(pos))


def log_los(rec, s: Session, site: dict, stats: dict, spacing_m: float = 20.0) -> None:
    """MODELLED line of sight as coloured ground dots: green = visible near the ground, red = needs > 50 m."""
    p = s.env / "los_min_agl.tif"
    if not p.exists():
        return
    from domeware.convert import terrain_alignment_m

    with rasterio.open(p) as src:
        step = max(1, int(spacing_m / src.res[0]))
        a = src.read(1)[::step, ::step]
        b, (rx, ry) = src.bounds, src.res
    rows, cols = np.nonzero(np.isfinite(a))
    x = b.left + (cols * step + 0.5) * rx
    y = b.top - (rows * step + 0.5) * ry
    v = a[rows, cols]
    ground = _dem_lookup(s, terrain_alignment_m(s))
    pos = _tm_to_enu(x, y, ground(x, y) + 0.5, site)
    t = np.clip(v / 50.0, 0, 1)[:, None]
    colors = (np.array([[40, 200, 90]]) * (1 - t) + np.array([[220, 40, 40]]) * t).astype(np.uint8)
    rec.log("world/los_MODELLED", rr.Points3D(pos, colors=colors, radii=3.0), static=True)
    rec.log("world/los_MODELLED", rr.TextDocument("MODELLED geometric line of sight (terrain + canopy + buildings, k=4/3). "
                                                  "Green: visible near the ground. Red: target must be 50+ m above "
                                                  "ground to be in sight. Not a detection probability."), static=True)
    stats["los_points"] = int(len(pos))


def truth_files(s: Session) -> list[Path]:
    """Every drone-position stream: streams/truth/*.parquet (SRT, ArduPilot) and streams/dji/*_osd.parquet."""
    return sorted(s.streams.glob("truth/*.parquet")) + sorted(s.streams.glob("dji/*_osd.parquet"))


def _ts(series: pd.Series) -> np.ndarray:
    return pd.to_datetime(series, utc=True).dt.tz_convert(None).to_numpy().astype("datetime64[ns]")


def events_for(tr: pd.DataFrame, track_id: int) -> list[tuple[pd.Timestamp, str]]:
    d = tr[tr["track_id"] == track_id].sort_values("t_utc")
    out = [(d["t_utc"].iloc[0], f"track {track_id} first seen ({d['class_name'].iloc[0]})")]
    for cls in ("SUSPECTED_DRONE", "DRONE"):
        m = d[d["class_name"] == cls]
        if len(m):
            out.append((m["t_utc"].iloc[0], f"track {track_id} first classified {cls}"))
    m = d[d["alarm"].fillna(False).astype(bool)]
    if len(m):
        out.append((m["t_utc"].iloc[0], f"track {track_id} first ALARM"))
    out.append((d["t_utc"].iloc[-1], f"track {track_id} last update"))
    return sorted(out)


def build(s: Session) -> dict:
    site = s.manifest.get("site")
    if not site:
        raise SystemExit("No site in the session. Run dw convert first.")
    s.views.mkdir(parents=True, exist_ok=True)
    stats: dict = {}
    rec = rr.RecordingStream("domeware-replay", recording_id=s.manifest["session_id"])
    rrd = s.views / "replay.rrd"
    rec.save(str(rrd))
    rec.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    tiles, info = terrain_glb(s, site)
    stats.update(info)
    for name, glb in tiles:
        rec.log(f"world/terrain/{name}", rr.Asset3D(contents=glb, media_type="model/gltf-binary"), static=True)
    log_features(rec, s, site, stats)
    for f in sorted(s.streams.glob("planned/*.parquet")):
        d = pd.read_parquet(f).dropna(subset=["e_m", "n_m", "u_m"]).sort_values("t_show_s")
        if len(d):
            rec.log(f"world/planned/{f.stem}", rr.LineStrips3D([d[["e_m", "n_m", "u_m"]].to_numpy(np.float32)],
                                                               colors=[[240, 240, 240]], radii=0.3,
                                                               labels=[f"PLANNED {f.stem}"]), static=True)
            stats.setdefault("planned_paths", []).append(f.stem)
    log_los(rec, s, site, stats)
    from domeware.datum import nls_layer_shift

    shift = nls_layer_shift(site["lat_deg"], site["lon_deg"])  # NLS maps are ETRS89; radar and drones are GPS
    for layer in ("world/terrain", "world/buildings", "world/power_lines", "world/trees", "world/los_MODELLED"):
        rec.log(layer, rr.Transform3D(translation=shift), static=True)
    stats["nls_layers_shifted_m"] = [round(v, 2) for v in shift]

    ground_z = (site["ground_level_m"] - site["alt_m"]) if site.get("ground_level_m") else 0.0
    rec.log("world/radar", rr.Points3D([[0, 0, 0]], colors=[[255, 120, 0]], radii=4.0,
                                       labels=[site.get("name") or "radar"], show_labels=True), static=True)
    rec.log("world/radar/mast", rr.LineStrips3D([[[0, 0, ground_z], [0, 0, 0]]], colors=[[255, 120, 0]], radii=0.8),
            static=True)
    th = np.linspace(0, 2 * np.pi, 181)
    rings = [np.column_stack([r * np.sin(th), r * np.cos(th), np.full_like(th, ground_z)]) for r in (500, 1000, 2000, 3000)]
    rec.log("world/radar/range_rings", rr.LineStrips3D(rings, colors=[[255, 160, 60]], radii=0.6,
                                                       labels=["500 m", "1 km", "2 km", "3 km"]), static=True)

    events: list[tuple[pd.Timestamp, str]] = []
    focus_ids = []
    truths = {}
    for f in truth_files(s):
        d = pd.read_parquet(f).dropna(subset=["t_utc", "e_m", "n_m", "u_m"]).sort_values("t_utc")
        if len(d):
            d["t_utc"] = pd.to_datetime(d["t_utc"], utc=True)
            truths[f.stem] = d
    drone_rgb = {k: DRONE_RGB[i % len(DRONE_RGB)] for i, k in enumerate(truths)}
    owner: dict[int, str] = {}  # radar track id -> drone it was (nearest drone, gate 50 m; same rule as dw compare)
    all_tracks = []
    for name, info_s in s.manifest["streams"].items():
        tp = s.streams / name / "tracks.parquet"
        if not tp.exists():
            continue
        tr = pd.read_parquet(tp)
        tr["t_utc"] = pd.to_datetime(tr["t_utc"], utc=True)
        if truths:
            from domeware.compare import assign_tracks

            owner.update(assign_tracks(truths, tr, 50.0))
        all_tracks.append(tr)
        focus = info_s.get("focus_track")
        if focus is not None:
            focus_ids.append(focus)
            events += events_for(tr, focus)
        for tid, g in tr.sort_values("t_utc").groupby("track_id"):
            classes = set(g["class_name"].dropna())
            best = max(classes, key=lambda c: CLASS_RANK.index(c) if c in CLASS_RANK else -1) if classes else "UNCLASSIFIED"
            alarm = bool(g["alarm"].fillna(False).astype(bool).any())
            mine = owner.get(int(tid))
            color = drone_rgb[mine] if mine else ALARM_RGB if alarm else CLASS_RGB.get(best, [200, 200, 200])
            path = f"world/tracks/{'focus_' if tid == focus else ''}{tid}"
            t = rr.TimeColumn("utc", timestamp=_ts(g["t_utc"]))
            pos = g[["e_m", "n_m", "u_m"]].to_numpy(np.float32)
            labels = [f"{tid} {c}{' ALARM' if a else ''}{' = ' + mine if mine else ''}"
                      for c, a in zip(g["class_name"], g["alarm"].fillna(False), strict=False)]
            rec.send_columns(path, indexes=[t], columns=[*rr.Points3D.columns(positions=pos).partition([1] * len(pos)),
                                                         *rr.Points3D.columns(labels=labels)])
            rec.log(path, rr.Points3D.from_fields(colors=[color], radii=3.0 if tid == focus or mine else 1.8,
                                                  show_labels=alarm or tid == focus or bool(mine)), static=True)
            rec.send_columns(f"plots/range_m/{tid}", indexes=[t], columns=rr.Scalars.columns(scalars=g["range_m"].to_numpy()))
            rec.send_columns(f"plots/height_above_radar_ground_m/{tid}", indexes=[t],
                             columns=rr.Scalars.columns(scalars=g["h_above_site_ground_m"].to_numpy()))
            if tid == focus:  # how the radar judged the drone over time
                for col, lane in (("class_code", "class (0 uncl .. 4 DRONE)"), ("score", "score"),
                                  ("median_rcs_dbsm", "median RCS (dBsm)")):
                    if col in g:
                        rec.send_columns(f"focus/{lane}", indexes=[t],
                                         columns=rr.Scalars.columns(scalars=pd.to_numeric(g[col], errors="coerce").to_numpy(np.float64)))
                rec.send_columns("focus/alarm (0/1)", indexes=[t],
                                 columns=rr.Scalars.columns(scalars=g["alarm"].fillna(False).astype(float).to_numpy()))
        stats[f"{name}_tracks"] = int(tr["track_id"].nunique())
        pp = s.streams / name / "plots.parquet"
        if pp.exists():
            pl = pd.read_parquet(pp)
            if len(pl):
                t = rr.TimeColumn("utc", timestamp=_ts(pl["t_utc"]))
                rec.send_columns("world/potential_drone_plots", indexes=[t],
                                 columns=rr.Points3D.columns(positions=pl[["e_m", "n_m", "u_m"]].to_numpy(np.float32))
                                 .partition([1] * len(pl)))
                rec.log("world/potential_drone_plots", rr.Points3D.from_fields(colors=[[170, 80, 255]], radii=1.5), static=True)

    tracks_all = pd.concat(all_tracks, ignore_index=True) if all_tracks else pd.DataFrame()
    legend = ["# Drones", "", "Each drone has its own colour. A radar track that was that drone (nearest drone, "
              "within 50 m) wears the same colour and its label ends with `= <drone>`. Other tracks keep the radar's "
              "class colours (blue DRONE, orange SUSPECTED, red ALARM, grey other).", ""]
    for name, d in truths.items():
        rgb = drone_rgb[name]
        pos = d[["e_m", "n_m", "u_m"]].to_numpy(np.float32)
        t = rr.TimeColumn("utc", timestamp=_ts(d["t_utc"]))
        rec.send_columns(f"world/truth/{name}", indexes=[t], columns=rr.Points3D.columns(positions=pos).partition([1] * len(pos)))
        rec.log(f"world/truth/{name}", rr.Points3D.from_fields(colors=[rgb], radii=2.5, labels=[name], show_labels=True), static=True)
        rec.log(f"world/truth/{name}_path", rr.LineStrips3D([pos], colors=[rgb], radii=0.4), static=True)
        for lane, v in ((f"plots/range_m/drone {name}", np.hypot(d["e_m"], d["n_m"]).to_numpy()),
                        (f"plots/drone_height_m/{name}", d["h_rel_takeoff_m"].to_numpy(np.float64) if "h_rel_takeoff_m" in d else None)):
            if v is not None:
                rec.send_columns(lane, indexes=[t], columns=rr.Scalars.columns(scalars=v))
                rec.log(lane, rr.SeriesLines(colors=[rgb], names=[name]), static=True)
        stats[f"truth_{name}_frames"] = len(d)
        mine = sorted(k for k, v in owner.items() if v == name)
        legend.append(f"- **{name}**: {DRONE_COLOUR_NAMES[list(truths).index(name) % len(DRONE_COLOUR_NAMES)]}, "
                      f"radar tracks {', '.join(map(str, mine)) or 'none'}")
        if mine and len(tracks_all):  # error vector: radar track state -> true drone position at the same instant
            from domeware.compare import _interp_truth

            g = tracks_all[tracks_all["track_id"].isin(mine)].sort_values("t_utc")
            tp = _interp_truth(d, g["t_utc"])
            tk = g[["e_m", "n_m", "u_m"]].to_numpy()
            ok = np.isfinite(tp).all(axis=1)
            if ok.any():
                te = rr.TimeColumn("utc", timestamp=_ts(g["t_utc"][ok]))
                strips = np.stack([tk[ok], tp[ok]], axis=1).astype(np.float32)
                rec.send_columns(f"world/error/{name}", indexes=[te],
                                 columns=rr.LineStrips3D.columns(strips=list(strips)).partition([1] * len(strips)))
                rec.log(f"world/error/{name}", rr.LineStrips3D.from_fields(colors=[rgb], radii=0.6), static=True)
                eh = np.hypot(*(tk[ok] - tp[ok])[:, :2].T)
                rec.send_columns(f"plots/horizontal_error_m/{name}", indexes=[te], columns=rr.Scalars.columns(scalars=eh))
                rec.log(f"plots/horizontal_error_m/{name}", rr.SeriesLines(colors=[rgb], names=[name]), static=True)
            stats[f"error_vectors_{name}"] = int(ok.sum())
    if truths:
        rec.log("legend", rr.TextDocument("\n".join(legend), media_type=rr.MediaType.MARKDOWN), static=True)
    stats["track_owner"] = {str(k): v for k, v in owner.items()}

    wx = s.streams / "weather" / "fmi_nearest.parquet"
    weather_cols = []
    if wx.exists():
        w = pd.read_parquet(wx)
        if len(w):
            t = rr.TimeColumn("utc", timestamp=_ts(w["t_utc"]))
            for col in w.columns.drop("t_utc"):
                v = pd.to_numeric(w[col], errors="coerce").to_numpy(np.float64)
                if np.isfinite(v).any():
                    rec.send_columns(f"weather/{col}", indexes=[t], columns=rr.Scalars.columns(scalars=v))
                    weather_cols.append(col)
            stats["weather_lanes"] = weather_cols

    de = s.streams / "events" / "drone_events.parquet"
    if de.exists():  # drone side: take-offs, landings, modes, failsafes
        events += [(pd.Timestamp(r.t_utc), f"{r.drone}: {r.kind} {r.detail}") for r in pd.read_parquet(de).itertuples()]
    if (s.root / "runs.csv").exists():
        from domeware import runs as R

        for r in R.load(s).itertuples():
            events += [(r.start_utc, f"{r.run_id} {r.part} start"), (r.end_utc, f"{r.run_id} {r.part} end")]
    from domeware.video import log_videos

    video_paths = log_videos(rec, s)
    stats["videos"] = video_paths
    events.sort(key=lambda e: e[0])
    for ts, msg in events:
        rec.set_time("utc", timestamp=ts.to_datetime64())
        rec.log("events", rr.TextLog(msg))
    stats["events"] = [f"{t.isoformat()} {m}" for t, m in events]

    trail = rrb.VisibleTimeRange("utc", start=rrb.TimeRangeBoundary.cursor_relative(seconds=-20.0),
                                 end=rrb.TimeRangeBoundary.cursor_relative())
    chase = [rrb.Spatial3DView(name=f"Chase track {fid}", origin="/world", time_ranges=[trail],
                               eye_controls=rrb.EyeControls3D(kind=rrb.Eye3DKind.Orbital,
                                                              tracking_entity=f"/world/tracks/focus_{fid}"))
             for fid in focus_ids[:1]]
    bp = rrb.Blueprint(
        rrb.Horizontal(
            rrb.Vertical(rrb.Spatial3DView(name="Site", origin="/world", time_ranges=[trail]), *chase,
                         *[rrb.Spatial2DView(name=p.split("/", 1)[1], origin=f"/{p}") for p in video_paths]),
            rrb.Vertical(rrb.TimeSeriesView(name="Range from radar (m)", origin="/plots/range_m"),
                         rrb.TimeSeriesView(name="Height above radar ground (m)", origin="/plots/height_above_radar_ground_m"),
                         *([rrb.TimeSeriesView(name="Wind (m/s, FMI nearest)", origin="/weather",
                                               contents=["/weather/wind_ms", "/weather/gust_ms"]),
                            rrb.TimeSeriesView(name="Temp (C) / visibility (m), FMI", origin="/weather",
                                               contents=["/weather/temp_c", "/weather/visibility_m"])]
                           if weather_cols else []),
                         *([rrb.TimeSeriesView(name="Drone height above take-off (m)", origin="/plots/drone_height_m"),
                            rrb.TimeSeriesView(name="Radar horizontal error per drone (m)", origin="/plots/horizontal_error_m")]
                           if truths else []),
                         rrb.TimeSeriesView(name="Focus track: radar's judgement", origin="/focus"),
                         rrb.TextLogView(name="Events", origin="/events"),
                         *([rrb.TextDocumentView(name="Legend", origin="/legend")] if truths else [])),
            column_shares=[3, 1]),
        rrb.TimePanel(timeline="utc", state="expanded"), collapse_panels=True)
    rec.send_blueprint(bp)
    rec.flush()
    rec.disconnect()
    bp.save("domeware-replay", str(s.views / "replay.rbl"))
    stats["rrd_mb"] = round(rrd.stat().st_size / 1e6, 1)
    stats["quicklooks"] = quicklooks(s, site, focus_ids)
    (s.views / "replay_stats.json").write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    s.manifest["views"] = {"replay": "views/replay.rrd", "stats": "views/replay_stats.json"}
    s.note(f"replay: views/replay.rrd ({stats['rrd_mb']} MB)")
    s.save()
    return stats


def quicklooks(s: Session, site: dict, focus_ids: list) -> list[str]:
    """Map over the orthophoto (TM35) with all tracks, plus range/height over time for the focus track."""
    out = []
    fig, ax = plt.subplots(figsize=(10, 10))
    if (s.env / "ortho.tif").exists():
        with rasterio.open(s.env / "ortho.tif") as src:
            sc = max(1.0, max(src.width, src.height) / 2500)
            img = src.read([1, 2, 3], out_shape=(3, int(src.height / sc), int(src.width / sc)))
            b = src.bounds
        ax.imshow(np.moveaxis(img, 0, -1), extent=(b.left, b.right, b.bottom, b.top))
    e0, n0 = LL_TO_TM35.transform(site["lon_deg"], site["lat_deg"])
    for name in s.manifest["streams"]:
        tp = s.streams / name / "tracks.parquet"
        if tp.exists():
            tr = pd.read_parquet(tp)
            for tid, g in tr.groupby("track_id"):
                x, y = LL_TO_TM35.transform(g["lon_deg"].to_numpy(), g["lat_deg"].to_numpy())
                alarm = bool(g["alarm"].fillna(False).astype(bool).any())
                ax.plot(x, y, "-", lw=2.2 if tid in focus_ids else 1, color="red" if alarm else "cyan")
    for f in truth_files(s):
        d = pd.read_parquet(f, columns=["lat_deg", "lon_deg"]).dropna()
        x, y = LL_TO_TM35.transform(d["lon_deg"].to_numpy(), d["lat_deg"].to_numpy())
        ax.plot(x, y, "-", lw=1.5, color="lime")
    ax.plot([e0], [n0], "^", color="orange", ms=12)
    ax.set_title(f"{site.get('name') or 'site'}: tracks (cyan, alarm = red), drone log (green), radar (orange)")
    ax.set_xlabel("E (m, EPSG:3067)")
    ax.set_ylabel("N (m, EPSG:3067)")
    p = s.views / "quicklook_map.png"
    fig.savefig(p, dpi=110, bbox_inches="tight")
    plt.close(fig)
    out.append(p.name)
    if (s.env / "los_min_agl.tif").exists():
        with rasterio.open(s.env / "los_min_agl.tif") as src:
            a, b = src.read(1), src.bounds
        fig, ax = plt.subplots(figsize=(10, 9))
        im = ax.imshow(a, extent=(b.left, b.right, b.bottom, b.top), cmap="RdYlGn_r", vmin=0, vmax=100)
        fig.colorbar(im, ax=ax, label="min. height above ground for line of sight (m)  [MODELLED]")
        ax.plot([e0], [n0], "^", color="black", ms=10)
        ax.set_title("MODELLED geometric line of sight from the radar (terrain + canopy + buildings, k=4/3)")
        p = s.views / "quicklook_los_MODELLED.png"
        fig.savefig(p, dpi=100, bbox_inches="tight")
        plt.close(fig)
        out.append(p.name)
    return out
