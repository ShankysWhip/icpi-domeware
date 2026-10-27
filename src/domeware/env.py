"""Real environment for any site in Finland: NLS 2 m elevation model + newest orthophoto.

Source: National Land Survey of Finland open data (CC BY 4.0) via the Funet geodata mirror,
which needs no API key. Map-sheet tiles are cached once and reused by every session.
"""

from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.merge import merge
from shapely.geometry import box

from domeware.session import Session, now_utc

FUNET = "https://www.nic.funet.fi/index/geodata/"
ORTHO_INDEX = "mml/ortho_all"  # every year; fetch() keeps the newest colour photo per map sheet
DEM_PATH = "mml/dem2m/2008_latest/{a}/{b}/{sheet}.tif"
ATTRIBUTION = "Contains data from the National Land Survey of Finland (Maanmittauslaitos), CC BY 4.0"
CACHE = Path(os.environ.get("DOMEWARE_CACHE", Path.home() / "Work" / "domeware-data" / "cache"))
TO_TM35 = Transformer.from_crs("EPSG:4326", "EPSG:3067", always_xy=True)


def _download(rel: str) -> Path:
    dst = CACHE / "funet" / rel
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with requests.get(FUNET + rel, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(tmp, dst)
    print(f"  downloaded {rel} ({dst.stat().st_size / 1e6:.1f} MB)")
    return dst


def _ortho_index() -> gpd.GeoDataFrame:
    for ext in ("shp", "shx", "dbf", "prj", "cpg"):
        _download(f"{ORTHO_INDEX}.{ext}")
    return gpd.read_file(CACHE / "funet" / f"{ORTHO_INDEX}.shp")


def _mosaic(paths: list[Path], bounds, res: float, out: Path, extra: dict, resampling=Resampling.bilinear) -> None:
    srcs = [rasterio.open(p) for p in paths]
    try:
        arr, tr = merge(srcs, bounds=bounds, res=res, resampling=resampling)
        prof = srcs[0].profile.copy()
    finally:
        for s in srcs:
            s.close()
    prof.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=tr, crs="EPSG:3067",
                tiled=True, blockxsize=512, blockysize=512, **extra)
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr)


def fetch(s: Session, radius_m: float = 3000.0, ortho_res_m: float = 1.0) -> dict:
    site = s.manifest.get("site")
    if not site:
        raise SystemExit("No site position yet. Convert radar data first (dw convert), or set the site in manifest.json.")
    e0, n0 = TO_TM35.transform(site["lon_deg"], site["lat_deg"])
    bounds = (e0 - radius_m, n0 - radius_m, e0 + radius_m, n0 + radius_m)
    idx = _ortho_index()
    sheets = idx[idx.intersects(box(*bounds)) & idx["path"].str.contains("normal_color")].copy()
    sheets["sheet"] = sheets["path"].map(lambda p: Path(p).stem)
    sheets["year"] = sheets["label"].str.extract(r"\((\d{4})\)", expand=False).astype(float)
    sheets = sheets.sort_values("year").groupby("sheet").tail(1)
    if sheets.empty:
        raise SystemExit("No NLS map sheets here: the site is outside Finland. Other countries are not supported yet.")
    s.env.mkdir(parents=True, exist_ok=True)
    ortho_paths = [_download(p) for p in sheets["path"]]
    dem_paths = []
    for p in sheets["path"]:
        sheet = Path(p).stem
        try:
            dem_paths.append(_download(DEM_PATH.format(a=sheet[:2], b=sheet[:3], sheet=sheet)))
        except requests.HTTPError as e:
            print(f"  no DEM for sheet {sheet}: {e}")
    _mosaic(dem_paths, bounds, 2.0, s.env / "dem.tif",
            dict(dtype="float32", nodata=-9999.0, compress="deflate", predictor=3))
    _mosaic(ortho_paths, bounds, ortho_res_m, s.env / "ortho.tif",
            dict(dtype="uint8", count=3, nodata=None, compress="jpeg", photometric="ycbcr", jpeg_quality=90),
            resampling=Resampling.average)
    info = {"fetched_utc": now_utc(), "bounds_epsg3067": bounds, "radius_m": radius_m,
            "dem": {"file": "env/dem.tif", "res_m": 2.0, "vertical_datum": "N2000", "sheets": len(dem_paths)},
            "ortho": {"file": "env/ortho.tif", "res_m": ortho_res_m, "labels": list(sheets["label"])},
            "source": FUNET, "attribution": ATTRIBUTION}
    s.manifest["env"] = info
    s.note(f"env: DEM {len(dem_paths)} sheet(s), ortho {', '.join(sheets['label'])}, radius {radius_m:.0f} m")
    s.save()
    return info


def sample_dem(dem: Path, lat: float, lon: float) -> float | None:
    e, n = TO_TM35.transform(lon, lat)
    with rasterio.open(dem) as src:
        v = next(src.sample([(e, n)]))[0]
        return None if v == src.nodata or not np.isfinite(v) else float(v)
