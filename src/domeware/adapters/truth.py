"""Drone position ("truth") adapters -> one common table per source.

Common columns (Parquet, one row per fix):
  t_utc (timestamp[ns, UTC]), lat_deg, lon_deg, h_rel_takeoff_m (height above take-off, as logged),
  h_radar_frame_m (height in the radar's altitude frame, DERIVED), e_m, n_m, u_m (ENU about the radar, DERIVED),
  source, time_method
Heights: drone logs give height above take-off. h_radar_frame_m = DEM ground at take-off (N2000) + the session's
terrain alignment (radar ground level - DEM at radar) + h_rel_takeoff_m. Stated in the stream meta.
"""

from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LOCAL_TZ = "Europe/Helsinki"  # DJI SRT and file names use the phone/controller's local time
GPS_UTC_LEAP_S = 18  # GPS - UTC since 2017-01-01; valid until at least 2027-06-28 (IERS Bulletin C)

_SRT_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)")
_NUM = r"(-?\d+(?:\.\d+)?)"
_SRT_FIELDS = {
    "lat_deg": re.compile(r"\[?\s*latitude\s*[:=]\s*" + _NUM, re.I),
    "lon_deg": re.compile(r"\[?\s*longt?itude\s*[:=]\s*" + _NUM, re.I),  # DJI spells it 'longtitude' on some models
    "h_rel_takeoff_m": re.compile(r"rel_alt\s*[:=]\s*" + _NUM, re.I),
    "dji_abs_alt_m": re.compile(r"abs_alt\s*[:=]\s*" + _NUM, re.I),
    "altitude_m": re.compile(r"\[\s*altitude\s*[:=]\s*" + _NUM, re.I),
}


def read_dji_srt(path: Path, tz: str = LOCAL_TZ) -> tuple[pd.DataFrame, dict]:
    """Parse a DJI video caption (.SRT) file. Tolerant to model differences; never guesses a missing field."""
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    rows, skipped = [], 0
    for b in blocks:
        m = _SRT_TIME.search(b)
        vals = {k: (float(r.search(b).group(1)) if r.search(b) else np.nan) for k, r in _SRT_FIELDS.items()}
        if not m or np.isnan(vals["lat_deg"]) or np.isnan(vals["lon_deg"]):
            skipped += int(bool(b.strip()))
            continue
        rows.append({"t_local": m.group(1).replace(",", "."), **vals})
    if not rows:
        raise ValueError(f"{path.name}: no caption block with time + latitude + longitude. "
                         f"Turn on 'Video Captions' in DJI Fly before recording.")
    df = pd.DataFrame(rows)
    df["h_rel_takeoff_m"] = df["h_rel_takeoff_m"].fillna(df["altitude_m"])  # older models: [altitude: x] = above take-off
    t = pd.to_datetime(df["t_local"], format="mixed").dt.tz_localize(ZoneInfo(tz), ambiguous="NaT")
    df["t_utc"] = t.dt.tz_convert("UTC")
    df = df[(df["lat_deg"] != 0) & (df["lon_deg"] != 0)].dropna(subset=["t_utc"])
    df = df.drop_duplicates("t_utc").sort_values("t_utc")
    meta = {"time_method": f"SRT caption local time, zone {tz} (DST-aware)", "rows": len(df), "skipped_blocks": skipped,
            "height_note": "rel_alt = height above take-off (DJI barometric); abs_alt kept as dji_abs_alt_m, unused"}
    return df[["t_utc", "lat_deg", "lon_deg", "h_rel_takeoff_m", "dji_abs_alt_m"]].reset_index(drop=True), meta


def read_ardupilot_bin(path: Path) -> tuple[pd.DataFrame, dict]:
    """ArduPilot DataFlash .bin -> GPS fixes. UTC from GPS week + ms minus leap seconds (per row, not pymavlink's)."""
    from pymavlink import DFReader

    log = DFReader.DFReader_binary(str(path), zero_time_base=True)
    rows, home_alt = [], None
    while (m := log.recv_match(type=["GPS"])) is not None:
        d = m.to_dict()
        if d.get("I", 0) != 0 or d.get("Status", 0) < 3:  # first receiver, 3D fix or better
            continue
        t = pd.Timestamp("1980-01-06", tz="UTC") + pd.Timedelta(weeks=d["GWk"], milliseconds=d["GMS"]) \
            - pd.Timedelta(seconds=GPS_UTC_LEAP_S)
        if home_alt is None:
            home_alt = d["Alt"]
        rows.append({"t_utc": t, "lat_deg": d["Lat"], "lon_deg": d["Lng"], "h_rel_takeoff_m": d["Alt"] - home_alt,
                     "gps_alt_m": d["Alt"], "nsats": d.get("NSats"), "hdop": d.get("HDop")})
    if not rows:
        raise ValueError(f"{path.name}: no GPS rows with a 3D fix")
    df = pd.DataFrame(rows).drop_duplicates("t_utc").sort_values("t_utc").reset_index(drop=True)
    meta = {"time_method": f"GPS week/ms - {GPS_UTC_LEAP_S} s (per fix)", "rows": len(df),
            "height_note": "height above the first 3D fix (GPS.Alt difference); datum of GPS.Alt depends on GPS_DRV_OPTIONS"}
    return df, meta


def to_radar_frame(df: pd.DataFrame, site: dict, ground_at_takeoff_n2000: float | None, alignment_m: float) -> pd.DataFrame:
    from domeware.replay import enu

    df = df.copy()
    if ground_at_takeoff_n2000 is None:
        df["h_radar_frame_m"] = np.nan
        df[["e_m", "n_m", "u_m"]] = np.nan
        return df
    df["h_radar_frame_m"] = ground_at_takeoff_n2000 + alignment_m + df["h_rel_takeoff_m"]
    e, n, u = enu(df["lat_deg"], df["lon_deg"], df["h_radar_frame_m"], site["lat_deg"], site["lon_deg"], site["alt_m"])
    df["e_m"], df["n_m"], df["u_m"] = e, n, u
    return df
