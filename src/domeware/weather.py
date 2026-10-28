"""Weather for any session in Finland: FMI open-data observations around the site, for the run's time window.

Source: Finnish Meteorological Institute open data WFS (no key), CC BY 4.0. Plain requests (no GPL client).
For each parameter the nearest station that actually has data is chosen; all stations are kept too.
"""

from __future__ import annotations

import math
import time

import pandas as pd
import requests
from defusedxml.ElementTree import fromstring as safe_fromstring

from domeware.session import Session, now_utc

WFS = "https://opendata.fmi.fi/wfs"
UA = {"User-Agent": "domeware/0.1 (ICPI internal test-and-evaluation tool)"}
ATTRIBUTION = "Finnish Meteorological Institute open data, CC BY 4.0"
# FMI parameter -> (column name, unit)
PARAMS = {
    "t2m": ("temp_c", "degC"), "ws_10min": ("wind_ms", "m/s"), "wg_10min": ("gust_ms", "m/s"),
    "wd_10min": ("wind_dir_deg", "deg from"), "rh": ("rh_pct", "%"), "td": ("dewpoint_c", "degC"),
    "ri_10min": ("precip_mmh", "mm/h"), "snow_aws": ("snow_depth_cm", "cm"), "p_sea": ("pressure_hpa", "hPa"),
    "vis": ("visibility_m", "m"), "n_man": ("cloud_okta", "okta"), "wawa": ("present_weather_wawa", "WMO code"),
}
NS = {"omso": "http://inspire.ec.europa.eu/schemas/omso/3.0", "gml": "http://www.opengis.net/gml/3.2",
      "wml2": "http://www.opengis.net/waterml/2.0", "sams": "http://www.opengis.net/samplingSpatial/2.0",
      "target": "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1"}


def _parse(xml: bytes) -> pd.DataFrame:
    rows = []
    for obs in safe_fromstring(xml).iter(f"{{{NS['omso']}}}PointTimeSeriesObservation"):
        loc = obs.find(".//target:Location", NS)
        fmisid = loc.find("gml:identifier", NS).text.strip() if loc is not None else None
        name = next((n.text for n in (loc.findall("gml:name", NS) if loc is not None else [])
                     if n.get("codeSpace", "").endswith("/name")), None)
        pos = obs.find(".//sams:shape//gml:pos", NS)
        lat, lon = (float(v) for v in pos.text.split()[:2]) if pos is not None else (math.nan, math.nan)
        ts = obs.find(".//wml2:MeasurementTimeseries", NS)
        param = ts.get(f"{{{NS['gml']}}}id").split("-")[-1]
        for pt in ts.findall(".//wml2:MeasurementTVP", NS):
            try:
                v = float(pt.find("wml2:value", NS).text)
            except (TypeError, ValueError):
                v = math.nan
            rows.append((pt.find("wml2:time", NS).text, fmisid, name, lat, lon, param, v))
    df = pd.DataFrame(rows, columns=["t_utc", "fmisid", "station", "lat", "lon", "param", "value"])
    df["t_utc"] = pd.to_datetime(df["t_utc"], utc=True)
    return df


def _km(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def run_window(s: Session, margin_min: int = 30) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Earliest..latest time in any converted stream, plus a margin."""
    ts = []
    for p in list(s.streams.glob("*/tracks.parquet")) + list(s.streams.glob("truth/*.parquet")):
        t = pd.read_parquet(p, columns=["t_utc"])["t_utc"]
        ts += [pd.to_datetime(t.min(), utc=True), pd.to_datetime(t.max(), utc=True)]
    if not ts:
        return None
    m = pd.Timedelta(minutes=margin_min)
    return (min(ts) - m).floor("10min"), (max(ts) + m).ceil("10min")


def fetch(s: Session, radius_deg: float = 0.6) -> dict | None:
    site, win = s.manifest.get("site"), run_window(s)
    if not site or not win:
        s.note("weather: skipped (needs site and converted data with times)")
        return None
    lat, lon = site["lat_deg"], site["lon_deg"]
    bbox = f"{lon - 2 * radius_deg:.3f},{lat - radius_deg:.3f},{lon + 2 * radius_deg:.3f},{lat + radius_deg:.3f}"
    q = {"service": "WFS", "version": "2.0.0", "request": "getFeature",
         "storedquery_id": "fmi::observations::weather::timevaluepair", "bbox": bbox,
         "starttime": win[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "endtime": win[1].strftime("%Y-%m-%dT%H:%M:%SZ"),
         "parameters": ",".join(PARAMS), "timestep": "10"}
    r = requests.get(WFS, params=q, headers=UA, timeout=120)
    r.raise_for_status()
    out = s.streams / "weather"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fmi_raw.xml").write_bytes(r.content)
    df = _parse(r.content)
    if df.empty:
        s.note("weather: FMI returned no stations for this area/time")
        return None
    df["dist_km"] = [_km(lat, lon, a, b) for a, b in zip(df["lat"], df["lon"], strict=False)]
    df.to_parquet(out / "fmi_all_stations.parquet", index=False)
    chosen, cols = {}, []
    for p, (col, unit) in PARAMS.items():
        d = df[(df["param"] == p) & df["value"].notna()]
        if d.empty:
            continue
        near = d.loc[d["dist_km"].idxmin()]
        series = d[d["fmisid"] == near["fmisid"]].set_index("t_utc")["value"].rename(col)
        cols.append(series)
        chosen[col] = {"unit": unit, "station": near["station"], "fmisid": near["fmisid"], "dist_km": round(near["dist_km"], 1)}
        time.sleep(0)  # single request; nothing to throttle
    wide = pd.concat(cols, axis=1).sort_index().reset_index() if cols else pd.DataFrame()
    wide.to_parquet(out / "fmi_nearest.parquet", index=False)
    info = {"fetched_utc": now_utc(), "window_utc": [str(win[0]), str(win[1])], "source": WFS,
            "attribution": ATTRIBUTION, "parameters": chosen,
            "note": "10-min observations; nearest station with data per parameter. Not on-site measurements."}
    s.manifest["streams"]["weather"] = info
    s.note(f"weather: {len(chosen)} parameters from {len({c['station'] for c in chosen.values()})} FMI station(s), "
           f"nearest {min(c['dist_km'] for c in chosen.values())} km")
    s.save()
    return info
