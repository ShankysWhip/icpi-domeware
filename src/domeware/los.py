"""MODELLED radar line of sight: the lowest height above ground at which a target is geometrically visible.

Geometry only (terrain + canopy + buildings as obstacles, earth curvature with k = 4/3). It is NOT a detection
probability: radar cross-section, clutter, multipath and processing are not modelled. Output env/los_min_agl.tif.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from pyproj import Transformer

from domeware.session import Session, now_utc

R_EFF = 4.0 / 3.0 * 6371000.0
LL_TO_TM35 = Transformer.from_crs("EPSG:4326", "EPSG:3067", always_xy=True)


def _surface(s: Session, align_m: float):
    """Obstacle surface (radar frame heights) = DEM + max(canopy, building height), on the DEM grid."""
    with rasterio.open(s.env / "dem.tif") as src:
        dem = src.read(1).astype(np.float64)
        dem[dem == src.nodata] = np.nan
        tr, shape, crs = src.transform, src.shape, src.crs
    dem = np.where(np.isfinite(dem), dem, np.nanmedian(dem)) + align_m
    obst = np.zeros(shape)
    if (s.env / "canopy.tif").exists():
        from rasterio.warp import Resampling, reproject

        c = np.zeros(shape, dtype=np.float32)
        with rasterio.open(s.env / "canopy.tif") as src:
            reproject(rasterio.band(src, 1), c, dst_transform=tr, dst_crs=crs, resampling=Resampling.max)
        obst = np.maximum(obst, np.nan_to_num(c, nan=0.0))
    if (s.env / "features.gpkg").exists():
        import geopandas as gpd
        from rasterio.features import rasterize

        try:
            b = gpd.read_file(s.env / "features.gpkg", layer="buildings")
            if len(b):
                obst = np.maximum(obst, rasterize(zip(b.geometry, b["height_m"], strict=False), out_shape=shape, transform=tr, fill=0))
        except Exception:  # no buildings layer is fine: terrain + canopy still apply
            pass
    return dem, dem + obst, tr


def compute(s: Session, max_range_m: float = 3000.0, n_az: int = 720, step_m: float = 4.0, cap_m: float = 300.0) -> dict:
    from domeware.convert import terrain_alignment_m

    site = s.manifest["site"]
    ground, surf, tr = _surface(s, terrain_alignment_m(s))
    x0, y0 = LL_TO_TM35.transform(site["lon_deg"], site["lat_deg"])
    h_ant = site["alt_m"]
    inv = ~tr
    ranges = np.arange(step_m, max_range_m, step_m)
    drop = ranges ** 2 / (2 * R_EFF)  # curvature drop seen from the antenna
    azs = np.linspace(0, 2 * math.pi, n_az, endpoint=False)
    need = np.full((n_az, len(ranges)), np.nan)  # lowest visible height (radar frame) per ray and range
    for i, az in enumerate(azs):
        c, r = inv * (x0 + ranges * math.sin(az), y0 + ranges * math.cos(az))
        c, r = c.astype(int), r.astype(int)
        ok = (r >= 0) & (r < ground.shape[0]) & (c >= 0) & (c < ground.shape[1])
        if not ok.any():
            continue
        angle = (surf[r[ok], c[ok]] - drop[ok] - h_ant) / ranges[ok]  # elevation angle of each obstacle top
        horizon = np.maximum.accumulate(np.concatenate([[-np.inf], angle[:-1]]))  # highest angle so far
        need[i, ok] = h_ant + horizon * ranges[ok] + drop[ok]
    # every cell takes the nearest ray/range sample, so the whole disc is filled (rays alone leave gaps far out)
    rows, cols = np.indices(ground.shape)
    cx, cy = tr * (cols + 0.5, rows + 0.5)
    dx, dy = cx - x0, cy - y0
    rng = np.hypot(dx, dy)
    ai = np.round((np.arctan2(dx, dy) % (2 * math.pi)) / (2 * math.pi) * n_az).astype(int) % n_az
    ri = np.clip(np.round(rng / step_m).astype(int) - 1, 0, len(ranges) - 1)
    out = np.clip(need[ai, ri] - ground, 0, cap_m)
    out[(rng > max_range_m) | (rng < step_m)] = np.nan
    prof = dict(driver="GTiff", height=out.shape[0], width=out.shape[1], count=1, dtype="float32", crs="EPSG:3067",
                transform=tr, nodata=np.nan, compress="deflate")
    with rasterio.open(s.env / "los_min_agl.tif", "w", **prof) as dst:
        dst.write(out.astype("float32"), 1)
    v = out[np.isfinite(out)]
    info = {"computed_utc": now_utc(), "file": "env/los_min_agl.tif", "label": "MODELLED (geometry only)",
            "k_factor": 4 / 3, "max_range_m": max_range_m, "azimuths": n_az, "step_m": step_m,
            "obstacles": "DEM + canopy + buildings (estimated heights)",
            "share_visible_at_ground": round(float((v < 1).mean()), 3) if len(v) else None,
            "share_visible_below_30m": round(float((v < 30).mean()), 3) if len(v) else None}
    s.manifest["los"] = info
    s.note(f"los: MODELLED min visible height; visible at 30 m AGL over {info['share_visible_below_30m']:.0%} of cells")
    s.save()
    return info
