"""dw validate: does a session follow the data contract (docs/DATA.md)? For every tool that writes sessions.

  dw validate SESSION     OK / WARN / FAIL per table: required columns, types, UTC times, sane values

CONTRACT below is the machine-readable form of docs/DATA.md (a test keeps the two in step). Extra columns and extra
tables are fine: readers ignore what they do not know. A missing required column, a time without UTC or a latitude
outside the globe is a FAIL; the exit code is 1 then, so a tool or a test can stop on it.
"""

from __future__ import annotations

import pandas as pd

from domeware.session import Session

CONTRACT: dict[str, list[str]] = {
    "robin/tracks.parquet": ["t_utc", "track_id", "lat_deg", "lon_deg", "alt_m", "e_m", "n_m", "u_m", "range_m",
                             "az_deg", "el_deg", "h_above_site_ground_m", "speed_mps", "v_e_mps", "v_n_mps", "v_u_mps",
                             "class_name", "class_code", "score", "alarm", "median_rcs_dbsm", "current_rcs_dbsm",
                             "latency_s", "raw_ref", "msg_index", "qflags"],
    "robin/track_summary.parquet": ["track_id", "t_first", "t_last", "n_states", "duration_s", "alarm_any",
                                    "t_first_alarm_state", "t_first_drone_state", "max_score", "min_range_m",
                                    "alt_min_m", "alt_max_m", "median_speed_mps", "median_rcs_dbsm", "classes",
                                    "last_class"],
    "robin/plots.parquet": ["plot_id", "t_utc", "lat_deg", "lon_deg", "alt_m", "e_m", "n_m", "u_m", "range_m",
                            "az_deg", "el_deg", "rcs_dbsm", "latency_s", "raw_ref", "msg_index", "qflags"],
    "robin/status.parquet": ["t_utc", "sys_version", "s_operational_state", "s_position_latitude",
                             "s_position_longitude", "s_position_altitude", "s_ground_level", "s_orientation_azimuth",
                             "s_processing_operating_mode_id", "s_processing_sensitivity"],
    "radar/tracks.parquet": ["t_utc", "track_id", "e_m", "n_m", "u_m", "range_m", "lat_deg", "lon_deg", "alt_m",
                             "h_above_site_ground_m", "class_name", "class_code", "alarm", "score", "speed_mps",
                             "median_rcs_dbsm", "source"],
    "radar/status.parquet": ["t_utc"],
    "truth/*.parquet": ["t_utc", "lat_deg", "lon_deg", "e_m", "n_m", "u_m", "h_rel_takeoff_m", "source"],
    "planned/*.parquet": ["drone", "t_show_s", "lat_deg", "lon_deg", "h_rel_takeoff_m", "h_radar_frame_m", "e_m",
                          "n_m", "u_m"],
    "weather/onsite_vaisala.parquet": ["t_utc", "wind_ms", "wind_dir_deg", "gust_ms", "temp_c", "rh_pct",
                                       "pressure_hpa", "source"],
    "events/*.parquet": ["t_utc", "drone", "kind", "detail", "source", "time_method"],
}
TIMES = ("t_utc", "t_first", "t_last")  # these must be datetimes in UTC; t_utc may never be empty
RANGES = {"lat_deg": (-90, 90), "lon_deg": (-180, 180), "s_position_latitude": (-90, 90),
          "s_position_longitude": (-180, 180), "wind_dir_deg": (0, 360), "rh_pct": (0, 100)}


def _table(rel: str, df: pd.DataFrame, need: list[str]) -> list[tuple[str, str]]:
    out = []
    missing = [c for c in need if c not in df.columns]
    if missing:
        out.append(("FAIL", f"{rel}: missing {', '.join(missing)}"))
    for c in need:
        if c not in df.columns:
            continue
        col = df[c]
        if c in TIMES or c.startswith("t_first_"):
            if not isinstance(col.dtype, pd.DatetimeTZDtype) or str(col.dt.tz) != "UTC":
                out.append(("FAIL", f"{rel}: {c} is {col.dtype}, not a UTC time"))
            elif c == "t_utc" and col.isna().any():
                out.append(("FAIL", f"{rel}: {int(col.isna().sum())} rows without t_utc"))
        elif c in RANGES:
            lo, hi = RANGES[c]
            v = pd.to_numeric(col, errors="coerce")
            bad = int(((v < lo) | (v > hi)).sum())
            if bad:
                out.append(("FAIL", f"{rel}: {bad} values of {c} outside {lo}..{hi}"))
        elif c.endswith(("_m", "_deg", "_mps", "_s", "_dbsm")) and not pd.api.types.is_numeric_dtype(col):
            out.append(("FAIL", f"{rel}: {c} is {col.dtype}, not a number"))
    if not out:
        out.append(("OK  " if len(df) else "WARN", f"{rel}: {len(df)} rows" + ("" if len(df) else " (empty)")))
    return out


def run(s: Session) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    site = s.manifest.get("site")
    if not site or any(site.get(k) is None for k in ("lat_deg", "lon_deg", "alt_m")):
        out.append(("FAIL" if any(s.streams.glob("**/*.parquet")) else "WARN",
                    "manifest: no site position (lat_deg, lon_deg, alt_m): positions cannot be placed"))
    elif str(site.get("alt_datum", "")).upper().startswith("UNVERIFIED") and not site.get("alt_datum_check"):
        out.append(("WARN", "manifest: the radar's height datum is unverified (dw check settles it from the status)"))
    seen = set()
    for pattern, need in CONTRACT.items():
        for f in sorted(s.streams.glob(pattern)):
            seen.add(f)
            out += _table(f.relative_to(s.streams).as_posix(), pd.read_parquet(f), need)
    extra = [f.relative_to(s.streams).as_posix() for f in sorted(s.streams.glob("**/*.parquet")) if f not in seen]
    if extra:
        out.append(("OK  ", f"not in the contract (readers ignore them): {', '.join(extra)}"))
    return out


def main(s: Session) -> int:
    lines = run(s)
    for level, msg in lines:
        print(f"{level}  {msg}")
    fails = sum(1 for lv, _ in lines if lv == "FAIL")
    print("The session follows the data contract." if not fails else f"{fails} FAIL: fix the writer, see docs/DATA.md")
    return 1 if fails else 0
