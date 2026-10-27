"""Buildings, power lines and forest for any site in Finland, fetched automatically for the env bounds.

Sources (all CC BY 4.0, no key):
- NLS topographic database (Maastotietokanta) via the Funet mirror: building footprints, power lines.
- Finnish Forest Centre (Metsäkeskus) canopy height model, newest year per map sheet, read as windows (COG).
Heights that the data does not give are ESTIMATED and say so: building height from the floor class,
power-line height from the line class.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.merge import merge
from shapely.geometry import box

from domeware.env import CACHE, _download, _ortho_index
from domeware.session import Session, now_utc

MTK_YEARS = (2026, 2025, 2024)
MTK_PATH = "mml/maastotietokanta/{y}/shp/{a}/{b}/{sheet}{half}.shp.zip"
CHM_URL = "https://avoin.metsakeskus.fi/aineistot/Latvusmalli/Karttalehti/{y}/CHM_{sheet}_{y}.tif"
CHM_YEARS = range(2026, 2014, -1)
# MTK building classes end in 1 (1-2 floors) or 2 (3+ floors); heights are estimates
BUILDING_HEIGHT_M = {1: 6.0, 2: 12.0}
POWERLINE_HEIGHT_M = {22311: 25.0, 22312: 12.0}  # high-voltage / distribution line, ASSUMED conductor height


def _mtk_zip(sheet5: str, half: str) -> Path | None:
    for y in MTK_YEARS:
        try:
            return _download(MTK_PATH.format(y=y, a=sheet5[:2], b=sheet5[:3], sheet=sheet5, half=half))
        except Exception:
            continue
    return None


def _read_zip_layers(zp: Path, prefix: str) -> list[gpd.GeoDataFrame]:
    with zipfile.ZipFile(zp) as z:
        names = [n for n in z.namelist() if n.endswith(".shp") and Path(n).name.startswith(prefix)]
    return [gpd.read_file(f"zip://{zp.as_posix()}!{n}") for n in names]


def _chm(sheets: list[str], bounds) -> tuple[Path | None, dict]:
    srcs, used = [], {}
    for sh in sheets:
        for y in CHM_YEARS:
            try:
                srcs.append(rasterio.open(f"/vsicurl/{CHM_URL.format(y=y, sheet=sh)}"))
                used[sh] = y
                break
            except rasterio.errors.RasterioIOError:
                continue
    if not srcs:
        return None, used
    arr, tr = merge(srcs, bounds=bounds, res=1.0)  # native 1 m; 'max' is not allowed while reading
    prof = srcs[0].profile.copy()
    for s in srcs:
        s.close()
    a = arr[0].astype("float32")
    a[a == (prof.get("nodata") if prof.get("nodata") is not None else -9999)] = np.nan
    hh, ww = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    arr = np.nanmax(a[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2), axis=(1, 3))[None]  # 2 m cells keep the tallest tree
    tr = tr * tr.scale(2, 2)
    prof["nodata"] = np.nan
    prof.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=tr, count=1, dtype="float32",
                compress="deflate", tiled=True, blockxsize=512, blockysize=512)
    out = CACHE / "chm_last.tif"
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr[:1].astype("float32"))
    return out, used


def fetch(s: Session) -> dict:
    env = s.manifest.get("env")
    if not env:
        raise SystemExit("Run dw env first (features use the same area).")
    bounds = tuple(env["bounds_epsg3067"])
    idx = _ortho_index()
    sheets6 = sorted({Path(p).stem for p in idx[idx.intersects(box(*bounds))]["path"]})
    sheets5 = sorted({sh[:5] for sh in sheets6})
    bld, pwr = [], []
    for sh in sheets5:
        for half in ("L", "R"):
            zp = _mtk_zip(sh, half)
            if zp is None:
                continue
            bld += [g[(g["LUOKKA"] // 1000) == 42] for g in _read_zip_layers(zp, "r_") if g.geom_type.iloc[0:1].str.contains("Polygon").any()]
            pwr += [g[g["LUOKKA"].isin(POWERLINE_HEIGHT_M)] for g in _read_zip_layers(zp, "j_") if len(g)]
    aoi = box(*bounds)
    info: dict = {"fetched_utc": now_utc(), "attribution": "NLS Maastotietokanta and Metsäkeskus canopy height model, CC BY 4.0"}
    if bld:
        b = gpd.GeoDataFrame(pd.concat(bld, ignore_index=True), crs="EPSG:3067").clip(aoi)
        b["height_m"] = (b["LUOKKA"] % 10).map(BUILDING_HEIGHT_M).fillna(6.0)
        b["height_source"] = "ESTIMATED from MTK floor class"
        b[["LUOKKA", "height_m", "height_source", "geometry"]].to_file(s.env / "features.gpkg", layer="buildings")
        info["buildings"] = int(len(b))
    if pwr:
        p = gpd.GeoDataFrame(pd.concat(pwr, ignore_index=True), crs="EPSG:3067").clip(aoi)
        p["height_m"] = p["LUOKKA"].map(POWERLINE_HEIGHT_M)
        p["height_source"] = "ASSUMED from MTK line class"
        p[["LUOKKA", "height_m", "height_source", "geometry"]].to_file(s.env / "features.gpkg", layer="power_lines")
        info["power_lines"] = int(len(p))
    chm, years = _chm(sheets6, bounds)
    if chm is not None:
        (s.env / "canopy.tif").write_bytes(chm.read_bytes())
        info["canopy"] = {"file": "env/canopy.tif", "res_m": 2.0, "aggregation": "max of 1 m cells", "years": years}
    s.manifest["features"] = info
    s.note(f"features: {info.get('buildings', 0)} buildings, {info.get('power_lines', 0)} power-line segments, "
           f"canopy years {sorted(set(years.values())) if years else 'none'}")
    s.save()
    return info


def canopy_points(s: Session, spacing_m: float = 8.0, min_h: float = 3.0):
    """Tree tops as TM35 points (x, y, canopy height) on a regular grid; None if no canopy."""
    p = s.env / "canopy.tif"
    if not p.exists():
        return None
    with rasterio.open(p) as src:
        step = max(1, int(round(spacing_m / src.res[0])))
        a = src.read(1).astype("float32")
        b = src.bounds
    hh, ww = a.shape[0] // step * step, a.shape[1] // step * step
    h = np.nanmax(a[:hh, :ww].reshape(hh // step, step, ww // step, step), axis=(1, 3))  # tallest tree per cell
    b = rasterio.coords.BoundingBox(b.left, b.top - hh * (b.top - b.bottom) / a.shape[0],
                                    b.left + ww * (b.right - b.left) / a.shape[1], b.top)
    rows, cols = np.nonzero(np.isfinite(h) & (h >= min_h) & (h < 60))
    x = b.left + (cols + 0.5) * (b.right - b.left) / h.shape[1]
    y = b.top - (rows + 0.5) * (b.top - b.bottom) / h.shape[0]
    return x, y, h[rows, cols]
