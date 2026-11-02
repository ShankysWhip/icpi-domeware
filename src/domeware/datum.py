"""ETRS89 (NLS maps) vs GPS (ITRF / WGS84) in Finland.

NLS terrain, aerial photos, buildings and power lines are in ETRS89 (EUREF-FIN), which is fixed to the Eurasian plate.
GPS positions (radar, drones) are in the current ITRF/WGS84 frame, in which Finland moves ~2.5 cm a year to the
north-east. By 2026 the same point differs by ~0.9 m between the two. pyproj's default WGS84 <-> ETRS-TM35FIN path
ignores this, so the replay shifts the NLS layers into the GPS frame with the offset below.

The offset uses ITRF2020 -> ETRF2000 (EPSG Helmert, time-dependent) at the given epoch. EUREF-FIN is an ETRF96
realisation; the difference to ETRF2000 is a few centimetres, far below what radar comparisons need.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from pyproj import Transformer

_GEO = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)  # WGS84 3D -> geocentric
_ITRF_TO_ETRF = Transformer.from_crs("EPSG:9988", "EPSG:7930", always_xy=True)  # ITRF2020 -> ETRF2000, geocentric


def decimal_year(t: datetime | None = None) -> float:
    t = t or datetime.now(UTC)
    start = datetime(t.year, 1, 1, tzinfo=UTC)
    return t.year + (t - start).total_seconds() / (datetime(t.year + 1, 1, 1, tzinfo=UTC) - start).total_seconds()


def etrs89_minus_gps_enu(lat: float, lon: float, epoch: float | None = None) -> np.ndarray:
    """(east, north, up) metres: where ETRS89 puts a point minus where GPS puts it, at `epoch` (decimal year)."""
    x, y, z = _GEO.transform(lon, lat, 0.0)
    X, Y, Z, _ = _ITRF_TO_ETRF.transform(x, y, z, epoch or decimal_year())
    d = np.array([X - x, Y - y, Z - z])
    la, lo = np.radians(lat), np.radians(lon)
    rot = np.array([[-np.sin(lo), np.cos(lo), 0],
                    [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)],
                    [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]])
    return rot @ d


def nls_layer_shift(lat: float, lon: float, epoch: float | None = None) -> list[float]:
    """Translation that moves NLS (ETRS89) layers onto GPS positions. Horizontal only: heights are aligned separately."""
    e, n, _ = etrs89_minus_gps_enu(lat, lon, epoch)
    return [float(-e), float(-n), 0.0]


SAME_M = 3.0  # map heights and the radar's own may differ this much at the mast (2 m DEM, a building, a pad)


def robin_height_datum(s) -> dict | None:
    """Which height system the radar's own altitudes use, read from its status messages: its declared ground level
    against the NLS map (N2000) at its position. About equal: orthometric (N2000, sea level). Higher by the geoid
    height (about 17-18 m in Finland): ellipsoidal (WGS84). Anything else: unclear. None without radar status."""
    import pandas as pd
    import pyarrow.parquet as pq

    cols = ["s_position_latitude", "s_position_longitude", "s_position_altitude", "s_ground_level"]
    found = [f for f in sorted(s.streams.glob("**/status.parquet")) if set(cols) <= set(pq.read_schema(f).names)]
    if not found:
        return None
    st = pd.concat([pd.read_parquet(f, columns=cols) for f in found]).dropna()
    if st.empty:
        return None
    lat, lon = float(st["s_position_latitude"].median()), float(st["s_position_longitude"].median())
    ground, alt = float(st["s_ground_level"].median()), float(st["s_position_altitude"].median())
    out = {"radar_ground_m": round(ground, 2), "radar_altitude_m": round(alt, 2), "mast_m": round(alt - ground, 2),
           "map_ground_m": None, "difference_m": None, "geoid_m": None, "verdict": "unclear",
           "why": "no map heights here (dw env)"}
    if not (s.env / "dem.tif").exists():
        return out
    from domeware.env import sample_dem

    g = sample_dem(s.env / "dem.tif", lat, lon)
    if g is None:
        out["why"] = "the radar is outside the map heights"
        return out
    d = ground - g
    out.update(map_ground_m=round(float(g), 2), difference_m=round(d, 2))
    try:
        from domeware.adapters.truth import ellipsoidal_to_n2000

        out["geoid_m"] = round(100.0 - float(np.ravel(ellipsoidal_to_n2000([lat], [lon], [100.0]))[0]), 2)
    except RuntimeError:  # no geoid grid on this laptop: the ellipsoid case cannot be told, the sea-level one can
        pass
    if abs(d) <= SAME_M:
        out.update(verdict="orthometric", why="the radar's ground level matches the map (N2000)")
    elif out["geoid_m"] is not None and abs(d - out["geoid_m"]) <= SAME_M:
        out.update(verdict="ellipsoidal", why="the radar's ground level is above the map by the geoid height")
    else:
        out["why"] = "neither the map nor the map plus the geoid height matches"
    return out
