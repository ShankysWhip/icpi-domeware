"""Vaisala on-site weather (WXT536 export, as from VTT OuluZone) -> one table, one row per minute.

Layout of the real export (2026-10-29): a header block (Generated, Time Zone, Device, position, serials), a row of
column names "<quantity> <statistic> PT1M 0.0 m (<unit>) - <sensor>", then data rows separated by empty rows.
Values are text with two decimals.

Excel damage, repaired: a value such as 8.10 was turned into the date "8 October" when the file passed through a
spreadsheet with a day.month locale. With two decimals in the source this is reversible exactly: day + month / 100.
Every repaired cell is counted in the meta, never silently.

Time: the export states its time zone in the header; a file without it is refused rather than guessed. The minute
stamps are kept as given (start or end of the 1-minute mean is not stated in the file) [unverified].
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd

NAMES = {  # quantity text in the export -> our column
    "Air pressure mean": "pressure_hpa", "Air temperature mean": "temp_c", "Dew point mean": "dewpoint_c",
    "Relative humidity mean": "rh_pct", "Wind speed mean": "wind_ms", "Wind speed min.": "wind_min_ms",
    "Wind gust PT1M": "gust_ms", "Wind direction mean": "wind_dir_deg", "Wind direction min.": "wind_dir_min_deg",
    "Wind direction max.": "wind_dir_max_deg", "Wind gust direction": "gust_dir_deg",
    "Precipitation intensity": "precip_mmh", "Precipitation accumulation sum PT10M": "precip_10min_mm",
    "Precipitation accumulation sum PT1H": "precip_1h_mm", "Precipitation accumulation sum PT24H": "precip_24h_mm",
}


def _column(name: str) -> str | None:
    for key, col in NAMES.items():
        if name.startswith(key) or (key.endswith("PT1M") and name.startswith(key.split(" PT1M")[0]) and "PT1M" in name):
            return col
    return None


def _value(v):
    """Text or number as given; a date made by a spreadsheet goes back to day.month (8 Oct -> 8.10)."""
    if isinstance(v, (dt.datetime, dt.date, pd.Timestamp)):
        return v.day + v.month / 100.0, True
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None, False
    return float(str(v).replace(",", ".")), False


def read_wxt_excel(path: Path) -> tuple[pd.DataFrame, dict]:
    try:
        raw = pd.read_excel(path, header=None, dtype=object)
    except Exception as e:  # noqa: BLE001 - a damaged or cut copy: one clear message instead of a zip/xml traceback
        raise ValueError(f"{Path(path).name}: not a readable Excel export ({type(e).__name__}); copy the file again") from e
    head = {str(raw.iat[r, c]).strip().rstrip(":"): raw.iat[r, c + 1] for r in range(min(12, len(raw)))
            for c in range(raw.shape[1] - 1) if isinstance(raw.iat[r, c], str) and raw.iat[r, c].strip().endswith(":")}
    tz = str(head.get("Time Zone", "")).strip()
    if not tz:
        raise ValueError(f"{Path(path).name}: no 'Time Zone' in the header; refusing to guess the clock")
    hdr = next((r for r in range(len(raw)) if str(raw.iat[r, 0]).strip() == "Timestamp"), None)
    if hdr is None:
        raise ValueError(f"{Path(path).name}: no 'Timestamp' column row; columns found: {list(raw.iloc[:15, 0])}")
    names = [str(x) for x in raw.iloc[hdr]]
    cols = {i: _column(n) for i, n in enumerate(names) if i > 0}
    unknown = [names[i] for i, c in cols.items() if c is None]
    rows, repaired, read = [], 0, 0
    for r in range(hdr + 1, len(raw)):
        ts = raw.iat[r, 0]
        if ts is None or (isinstance(ts, float) and pd.isna(ts)):
            continue  # the export leaves an empty row between data rows
        read += 1
        rec = {"t_local": ts}
        for i, c in cols.items():
            if c:
                v, fixed = _value(raw.iat[r, i])
                rec[c] = v
                repaired += fixed
        rows.append(rec)
    df = pd.DataFrame(rows)
    t = pd.to_datetime(df.pop("t_local").astype(str), format="%d-%m-%Y %H:%M", errors="coerce")
    if t.isna().any():
        bad = int(t.isna().sum())
        raise ValueError(f"{Path(path).name}: {bad} timestamps not in dd-mm-yyyy HH:MM")
    df.insert(0, "t_utc", t.dt.tz_localize(tz).dt.tz_convert("UTC") if tz.upper() != "UTC" else t.dt.tz_localize("UTC"))
    meta = {"device": head.get("Device"), "serial": head.get("Serial"), "sensors": head.get("Sensor(s)"),
            "lat_deg": head.get("Latitude"), "lon_deg": head.get("Longitude"), "alt_m": head.get("Altitude"),
            "time_zone_in_file": tz, "generated": str(head.get("Generated (dd-MM-yyyy HH:mm:ss)") or ""),
            "averaging": "1-minute statistics (PT1M) as exported", "rows_read": read, "rows": len(df),
            "cells_repaired_from_spreadsheet_dates": int(repaired), "columns_not_used": unknown,
            "time_note": "minute stamps kept as exported; start or end of the averaging minute not stated [unverified]"}
    import numpy as np

    if {"temp_c", "dewpoint_c", "rh_pct"} <= set(df.columns):  # RH from T and Td (Magnus, WMO) must match measured RH
        es = lambda x: 6.112 * np.exp(17.62 * x / (243.12 + x))  # noqa: E731
        diff = (100 * es(df["dewpoint_c"]) / es(df["temp_c"]) - df["rh_pct"]).abs()
        meta["check_rh_max_diff_pct"] = round(float(diff.max()), 2) if diff.notna().any() else None
    if {"wind_min_ms", "wind_ms", "gust_ms"} <= set(df.columns):
        bad = (df["wind_min_ms"] > df["wind_ms"] + 0.01) | (df["wind_ms"] > df["gust_ms"] + 0.01)
        meta["check_wind_order_violations"] = int(bad.sum())
    if re.search(r"[^\x00-\x7f]", "".join(unknown)):
        meta["columns_not_used"] = [u.encode("ascii", "replace").decode() for u in unknown]
    return df, meta
