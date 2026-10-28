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
