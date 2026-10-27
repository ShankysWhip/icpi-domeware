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


def terrain_glb(s: Session, site: dict, max_grid: int = 700, max_tex: int = 4096):
    """DEM -> textured mesh in ENU. Heights are aligned to the radar's own declared ground level."""
    dem_p, ortho_p = s.env / "dem.tif", s.env / "ortho.tif"
    if not dem_p.exists():
        return None, {"terrain": "none (run dw env)"}
    with rasterio.open(dem_p) as src:
        step = max(1, math.ceil(max(src.width, src.height) / max_grid))
        h, w = math.ceil(src.height / step), math.ceil(src.width / step)
        z = src.read(1, out_shape=(h, w), resampling=Resampling.bilinear).astype(np.float64)
        z[z == src.nodata] = np.nan
        b = src.bounds
    z = np.where(np.isfinite(z), z, np.nanmedian(z))
    from domeware.convert import terrain_alignment_m

    offset = terrain_alignment_m(s)
    xs = np.linspace(b.left, b.right, w)
    ys = np.linspace(b.top, b.bottom, h)  # row 0 = north
    X, Y = np.meshgrid(xs, ys)
    lon, lat = TM35_TO_LL.transform(X.ravel(), Y.ravel())
    e, n, u = enu(lat, lon, z.ravel() + offset, site["lat_deg"], site["lon_deg"], site["alt_m"])
    verts = np.column_stack([e, n, u]).astype(np.float32)
    idx = np.arange(h * w).reshape(h, w)
    a, bb, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    tris = np.concatenate([np.column_stack([a, c, d]), np.column_stack([a, d, bb])]).astype(np.uint32)
    uv = np.column_stack([((X - b.left) / (b.right - b.left)).ravel(), ((b.top - Y) / (b.top - b.bottom)).ravel()])
    if ortho_p.exists():
        with rasterio.open(ortho_p) as src:
            sc = max(1.0, max(src.width, src.height) / max_tex)
            rgb = src.read([1, 2, 3], out_shape=(3, int(src.height / sc), int(src.width / sc)),
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
    info = {"terrain_vertices": int(len(verts)), "terrain_step_m": 2.0 * step,
            "vertical_alignment_m": round(offset, 2),
            "vertical_alignment_note": "terrain shifted so DEM at the radar equals the radar's declared ground level"}
    return mesh.export(file_type="glb", include_normals=True), info


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

    glb, info = terrain_glb(s, site)
    stats.update(info)
    if glb is not None:
        rec.log("world/terrain", rr.Asset3D(contents=glb, media_type="model/gltf-binary"), static=True)

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
    for name, info_s in s.manifest["streams"].items():
        tp = s.streams / name / "tracks.parquet"
        if not tp.exists():
            continue
        tr = pd.read_parquet(tp)
        tr["t_utc"] = pd.to_datetime(tr["t_utc"], utc=True)
        focus = info_s.get("focus_track")
        if focus is not None:
            focus_ids.append(focus)
            events += events_for(tr, focus)
        for tid, g in tr.sort_values("t_utc").groupby("track_id"):
            classes = set(g["class_name"].dropna())
            best = max(classes, key=lambda c: CLASS_RANK.index(c) if c in CLASS_RANK else -1) if classes else "UNCLASSIFIED"
            alarm = bool(g["alarm"].fillna(False).astype(bool).any())
            color = ALARM_RGB if alarm else CLASS_RGB.get(best, [200, 200, 200])
            path = f"world/tracks/{'focus_' if tid == focus else ''}{tid}"
            t = rr.TimeColumn("utc", timestamp=_ts(g["t_utc"]))
            pos = g[["e_m", "n_m", "u_m"]].to_numpy(np.float32)
            labels = [f"{tid} {c}{' ALARM' if a else ''}" for c, a in zip(g["class_name"], g["alarm"].fillna(False))]
            rec.send_columns(path, indexes=[t], columns=[*rr.Points3D.columns(positions=pos).partition([1] * len(pos)),
                                                         *rr.Points3D.columns(labels=labels)])
            rec.log(path, rr.Points3D.from_fields(colors=[color], radii=3.0 if tid == focus else 1.8,
                                                  show_labels=alarm or tid == focus), static=True)
            rec.send_columns(f"plots/range_m/{tid}", indexes=[t], columns=rr.Scalars.columns(scalars=g["range_m"].to_numpy()))
            rec.send_columns(f"plots/height_above_radar_ground_m/{tid}", indexes=[t],
                             columns=rr.Scalars.columns(scalars=g["h_above_site_ground_m"].to_numpy()))
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

    for f in truth_files(s):
        d = pd.read_parquet(f).dropna(subset=["t_utc", "e_m", "n_m", "u_m"]).sort_values("t_utc")
        if d.empty:
            continue
        pos = d[["e_m", "n_m", "u_m"]].to_numpy(np.float32)
        t = rr.TimeColumn("utc", timestamp=_ts(d["t_utc"]))
        rec.send_columns(f"world/truth/{f.stem}", indexes=[t], columns=rr.Points3D.columns(positions=pos).partition([1] * len(pos)))
        rec.log(f"world/truth/{f.stem}", rr.Points3D.from_fields(colors=[[0, 230, 90]], radii=2.5), static=True)
        rec.log(f"world/truth/{f.stem}_path", rr.LineStrips3D([pos], colors=[[0, 200, 80]], radii=0.4), static=True)
        stats[f"truth_{f.stem}_frames"] = len(d)

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
            rrb.Vertical(rrb.Spatial3DView(name="Site", origin="/world", time_ranges=[trail]), *chase),
            rrb.Vertical(rrb.TimeSeriesView(name="Range from radar (m)", origin="/plots/range_m"),
                         rrb.TimeSeriesView(name="Height above radar ground (m)", origin="/plots/height_above_radar_ground_m"),
                         rrb.TextLogView(name="Events", origin="/events")),
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
    return out
