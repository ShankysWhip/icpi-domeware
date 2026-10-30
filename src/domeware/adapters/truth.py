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
        vals = {k: (float(f.group(1)) if (f := r.search(b)) else np.nan) for k, r in _SRT_FIELDS.items()}  # one search each
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


def _close(log) -> None:
    """Close a pymavlink log and its file; the two reader kinds hold different handles open."""
    for obj in (log, getattr(log, "filehandle", None), getattr(log, "f", None)):
        try:
            if obj is not None and callable(getattr(obj, "close", None)):
                obj.close()
        except (OSError, ValueError, AttributeError, BufferError):  # already closed, or a map with views still open
            pass


def read_ardupilot_bin(path: Path) -> tuple[pd.DataFrame, dict]:
    """ArduPilot DataFlash .bin -> GPS fixes. UTC from GPS week + ms minus leap seconds (per row, not pymavlink's)."""
    from pymavlink import DFReader

    log = DFReader.DFReader_binary(str(path), zero_time_base=True)
    rows, home_alt = [], None
    try:
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
    finally:  # an open log stays locked on Windows (found by the converter stress test)
        _close(log)
    if not rows:
        raise ValueError(f"{path.name}: no GPS rows with a 3D fix")
    df = pd.DataFrame(rows).drop_duplicates("t_utc").sort_values("t_utc").reset_index(drop=True)
    meta = {"time_method": f"GPS week/ms - {GPS_UTC_LEAP_S} s (per fix)", "rows": len(df),
            "height_note": "height above the first 3D fix (GPS.Alt difference); datum of GPS.Alt depends on GPS_DRV_OPTIONS"}
    return df, meta


def read_mavlink_tlog(path: Path) -> tuple[pd.DataFrame, dict]:
    """MAVLink .tlog (ground-station recording) -> GLOBAL_POSITION_INT per vehicle (sysid).

    UTC: each vehicle's boot clock is tied to UTC with SYSTEM_TIME (time_unix_usec vs time_boot_ms, median offset).
    Without SYSTEM_TIME the ground station's receive time is used and flagged (it includes link delay).
    """
    from pymavlink import mavutil

    mlog = mavutil.mavlink_connection(str(path), dialect="ardupilotmega")
    pos, sync = [], {}
    try:
        while (m := mlog.recv_match(type=["GLOBAL_POSITION_INT", "SYSTEM_TIME"])) is not None:
            sysid = m.get_srcSystem()
            if m.get_type() == "SYSTEM_TIME":
                if m.time_unix_usec > 1.5e15 and m.time_boot_ms > 0:  # unix time is valid only after GPS time is known
                    sync.setdefault(sysid, []).append(m.time_unix_usec / 1e3 - m.time_boot_ms)
            elif m.lat != 0 or m.lon != 0:
                pos.append((sysid, m.time_boot_ms, getattr(m, "_timestamp", np.nan), m.lat / 1e7, m.lon / 1e7,
                            m.relative_alt / 1e3, m.alt / 1e3))
    finally:
        _close(mlog)
    if not pos:
        raise ValueError(f"{path.name}: no GLOBAL_POSITION_INT with a position")
    df = pd.DataFrame(pos, columns=["vehicle_id", "boot_ms", "rx_unix_s", "lat_deg", "lon_deg", "h_rel_takeoff_m", "amsl_m"])
    method = {}
    t = np.full(len(df), np.nan)
    for vid, idx in df.groupby("vehicle_id").groups.items():
        if sync.get(vid):
            t[idx] = df.loc[idx, "boot_ms"].to_numpy() + np.median(sync[vid])
            method[int(vid)] = f"boot ms + SYSTEM_TIME offset (n={len(sync[vid])})"
        else:
            t[idx] = df.loc[idx, "rx_unix_s"].to_numpy() * 1e3
            method[int(vid)] = "ground-station receive time (includes link delay) [flagged]"
    df["t_utc"] = pd.to_datetime(t, unit="ms", utc=True)
    df = df.drop(columns=["boot_ms", "rx_unix_s"]).drop_duplicates(["vehicle_id", "t_utc"]).sort_values(["vehicle_id", "t_utc"])
    meta = {"time_method": method, "rows": len(df), "vehicles": sorted(int(v) for v in df["vehicle_id"].unique()),
            "height_note": "relative_alt = height above home (EKF); amsl_m kept, unused"}
    return df.reset_index(drop=True), meta


PROJ_DIR = Path.home() / "Work" / "domeware-vendor" / "proj"  # vendored FIN2023N2000 + NKG grids


def ellipsoidal_to_n2000(lat, lon, h_ell) -> np.ndarray:
    """EUREF-FIN ellipsoidal height -> N2000 with the FIN2023N2000 geoid. Fails loudly without the grid."""
    import pyproj
    from pyproj.transformer import TransformerGroup

    pyproj.network.set_network_enabled(False)
    if PROJ_DIR.exists():
        pyproj.datadir.append_data_dir(str(PROJ_DIR))
    tg = TransformerGroup("EPSG:4937", "EPSG:4258+3900", always_xy=True)
    best = tg.transformers[0] if tg.transformers else None
    if best is None or "allpark" in best.description:
        raise RuntimeError(f"N2000 geoid grid missing (would be ~18 m wrong). Put fi_nls_fin2023n2000.tif in {PROJ_DIR}")
    return best.transform(np.asarray(lon), np.asarray(lat), np.asarray(h_ell))[2]


def read_rtklib_pos(path: Path) -> tuple[pd.DataFrame, dict]:
    """RTKLIB .pos (lat/lon/height output). Time scale from the header (GPST or UTC); height ellipsoidal."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    head = [ln for ln in lines if ln.startswith("%")]
    gpst = not any("UTC" in ln for ln in head[-1:])
    if any("geoid" in ln.lower() for ln in head):
        raise ValueError(f"{path.name}: geoid heights in .pos; export ellipsoidal heights (RTKLIB default)")
    rows, bad = [], 0
    for ln in lines:
        if ln.startswith("%") or not ln.strip():
            continue
        p = ln.split()
        try:  # a line cut short (copy stopped, card full) is counted and skipped, never fatal
            rows.append({"t": f"{p[0]} {p[1]}", "lat_deg": float(p[2]), "lon_deg": float(p[3]), "h_ell_m": float(p[4]),
                         "rtk_q": int(p[5]), "ns": int(p[6])})
        except (IndexError, ValueError):
            bad += 1
    if not rows:
        raise ValueError(f"{path.name}: no solution lines")
    df = pd.DataFrame(rows)
    df["t_utc"] = pd.to_datetime(df.pop("t"), format="%Y/%m/%d %H:%M:%S.%f", utc=True, errors="coerce")
    bad += int(df["t_utc"].isna().sum())
    df = df.dropna(subset=["t_utc"]).reset_index(drop=True)
    if not len(df):
        raise ValueError(f"{path.name}: no solution lines with a valid time")
    if gpst:
        df["t_utc"] -= pd.Timedelta(seconds=GPS_UTC_LEAP_S)
    df["h_n2000_m"] = ellipsoidal_to_n2000(df["lat_deg"], df["lon_deg"], df["h_ell_m"])
    df["h_rel_takeoff_m"] = df["h_n2000_m"] - df["h_n2000_m"].iloc[:10].median()
    meta = {"time_method": "GPST - 18 s" if gpst else "UTC as in file", "rows": len(df), "skipped_lines": bad,
            "fix_share": round(float((df["rtk_q"] == 1).mean()), 3),
            "height_note": "ellipsoidal -> N2000 via FIN2023N2000; rtk_q 1 = fix, 2 = float"}
    return df, meta


def to_radar_frame(df: pd.DataFrame, site: dict, ground_at_takeoff_n2000: float | None, alignment_m: float) -> pd.DataFrame:
    from domeware.replay import enu

    df = df.copy()
    if "h_n2000_m" in df:  # absolute heights (RTK): no take-off ground needed
        df["h_radar_frame_m"] = df["h_n2000_m"] + alignment_m
        e, n, u = enu(df["lat_deg"], df["lon_deg"], df["h_radar_frame_m"], site["lat_deg"], site["lon_deg"], site["alt_m"])
        df["e_m"], df["n_m"], df["u_m"] = e, n, u
        return df
    if ground_at_takeoff_n2000 is None:
        df["h_radar_frame_m"] = np.nan
        df[["e_m", "n_m", "u_m"]] = np.nan
        return df
    df["h_radar_frame_m"] = ground_at_takeoff_n2000 + alignment_m + df["h_rel_takeoff_m"]
    e, n, u = enu(df["lat_deg"], df["lon_deg"], df["h_radar_frame_m"], site["lat_deg"], site["lon_deg"], site["alt_m"])
    df["e_m"], df["n_m"], df["u_m"] = e, n, u
    return df
