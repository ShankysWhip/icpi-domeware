"""DJI flight-record frames -> domeware Parquet (prototype, 2026-10-23).

Input: a pandas DataFrame of *normalized frames* with the dotted column names used by
dji-log-parser / pydjirecord CSV ("CUSTOM.dateTime", "OSD.flyTime", "OSD.latitude", ...).
Produce it with dji_decode.py (pydjirecord frames, full float64 precision) or read a
dji-log-parser --csv file. Do NOT feed pydjirecord's --csv output: it formats floats with
"%g" (6 significant digits), which rounds 65.176667 to 65.1767 (~10 m error).

Output tables (one session/run):
  dji_osd      one row per OSD frame (~10 Hz), raw values as given + DERIVED columns
  dji_events   app tips/warnings and state transitions (take-off, landing, mode, GPS)
  meta (dict)  aircraft/app/decoder provenance, time model, datum notes -> manifest

Rules followed (domeware-decision.md D3/D4/D17):
  - every source value kept as given, next to the derived one; no clamping
  - heights carry a named reference; DJI "altitude" is kept but never used for geometry
  - t_utc is derived, with its method per row and a declared clock offset
  - provenance class per column in the Parquet schema metadata
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

CONVERTER_VERSION = "0.1.0-proto"

# qa_flags bits
QA_GPS_INVALID = 1        # OSD.isGPSUsed false, gpsLevel < 3, or lat/lon == 0
QA_LATLON_ZERO = 2
QA_T_INTERP = 4           # t_utc from interpolated app clock, not from flyTime
QA_T_APP_STALE = 8        # no fresh CUSTOM.dateTime within 2 s
QA_T_NONMONOTONIC = 16
QA_NO_DEM_GROUND = 32     # takeoff ground height not supplied -> no N2000/ENU heights

COLUMN_META: dict[str, dict[str, str]] = {
    "t_utc": {"prov": "DERIVED", "doc": "corrected UTC of the frame; see t_utc_method and meta.time_model"},
    "t_utc_method": {"prov": "DERIVED", "doc": "flytime_anchor | frame_index_from_anchor | app_clock_interp | flytime_extrapolated | missing"},
    "t_app_utc": {"prov": "MEASURED", "doc": "CUSTOM.dateTime as given (app/phone clock, epoch ms, UTC), forward-filled by the parser"},
    "t_app_fresh": {"prov": "DERIVED", "doc": "true where CUSTOM.dateTime changed on this frame"},
    "fly_time_s": {"prov": "MEASURED", "doc": "OSD.flyTime, s since motor start, 0.1 s resolution"},
    "lat_deg": {"prov": "MEASURED", "datum": "WGS84 (GNSS standalone, realisation of date) [declared]"},
    "lon_deg": {"prov": "MEASURED", "datum": "WGS84 (GNSS standalone, realisation of date) [declared]"},
    "height_rel_takeoff_m": {"prov": "MEASURED", "datum": "relative to take-off point (DJI fused baro/IMU), OSD.height"},
    "vps_height_m": {"prov": "MEASURED", "datum": "above surface below aircraft (downward vision/ToF); valid only near ground"},
    "dji_altitude_m": {"prov": "MEASURED", "datum": "DJI OSD.altitude = OSD.height + HOME.altitude; reference UNSPECIFIED, known unreliable; display only"},
    "home_alt_dji_m": {"prov": "MEASURED", "datum": "HOME.altitude, reference UNSPECIFIED"},
    "takeoff_ground_n2000_m": {"prov": "DERIVED", "datum": "N2000; NLS DEM sampled at home point (input parameter)"},
    "h_n2000_m": {"prov": "DERIVED", "datum": "N2000 = takeoff_ground_n2000_m + height_rel_takeoff_m (+ declared takeoff offset)"},
    "h_ellip_m": {"prov": "DERIVED", "datum": "ETRS89/EUREF-FIN ellipsoidal via FIN2023N2000 grid"},
    "e_m": {"prov": "DERIVED", "datum": "ENU east about site origin"},
    "n_m": {"prov": "DERIVED", "datum": "ENU north about site origin"},
    "u_m": {"prov": "DERIVED", "datum": "ENU up about site origin"},
    "slant_range_m": {"prov": "DERIVED", "doc": "3D distance from site origin (radar antenna)"},
    "azimuth_deg": {"prov": "DERIVED", "doc": "from site origin, true north, 0..360"},
    "elevation_deg": {"prov": "DERIVED", "doc": "from site origin"},
    "dji_vx_mps": {"prov": "MEASURED", "doc": "OSD.xSpeed as given; DJI convention north [unverified until first decoded log]"},
    "dji_vy_mps": {"prov": "MEASURED", "doc": "OSD.ySpeed as given; DJI convention east [unverified]"},
    "dji_vz_mps": {"prov": "MEASURED", "doc": "OSD.zSpeed as given; DJI convention down-positive [unverified]"},
    "h_speed_mps": {"prov": "DERIVED", "doc": "hypot(dji_vx, dji_vy)"},
    "yaw_deg": {"prov": "MEASURED", "doc": "aircraft yaw, -180..180, DJI says true north [unverified]"},
}


@dataclass
class Site:
    """ENU origin (normally the radar antenna). h_ellip_m must already be ellipsoidal."""
    lat_deg: float
    lon_deg: float
    h_ellip_m: float
    name: str = "site"
    note: str = ""


@dataclass
class ConvertOptions:
    clock_offset_s: float = 0.0               # added to t_utc; positive = phone clock was behind UTC
    clock_model: str = "phone_clock_unverified"
    takeoff_ground_n2000_m: float | None = None  # from NLS 2 m DEM at the home point
    takeoff_height_offset_m: float = 0.0      # e.g. take-off from a table/vehicle roof
    site: Site | None = None
    source_sha256: str = ""
    session_id: str = ""
    run_id: str = ""
    extra_meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers

def _col(df: pd.DataFrame, name: str, default: Any = np.nan) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)


def _num(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(_col(df, name), errors="coerce")


def _bool(df: pd.DataFrame, name: str) -> pd.Series:
    s = _col(df, name, default=None)
    return s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v))
                 else (v if isinstance(v, bool) else str(v).strip().lower() == "true")).astype("boolean")


def _str(df: pd.DataFrame, name: str) -> pd.Series:
    s = _col(df, name, default=None)
    return s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) or str(v) == "" else str(v))


def parse_app_time(series: pd.Series) -> pd.Series:
    t = pd.to_datetime(series, utc=True, errors="coerce", format="ISO8601")
    t = t.where(t > pd.Timestamp("2011-01-01", tz="UTC"))  # epoch-0 defaults = missing
    return t


# --------------------------------------------------------------------------- time model

def derive_time(df: pd.DataFrame, opts: ConvertOptions) -> tuple[pd.DataFrame, dict]:
    n = len(df)
    idx = np.arange(n)
    fly = _num(df, "OSD.flyTime").to_numpy(dtype=float)
    t_app = parse_app_time(_col(df, "CUSTOM.dateTime", None))
    t_app_ns = np.array([v.value if pd.notna(v) else np.nan for v in t_app], dtype=float)
    fresh = np.zeros(n, dtype=bool)
    prev = np.nan
    for i, v in enumerate(t_app_ns):
        if not np.isnan(v) and v != prev:
            fresh[i] = True
        if not np.isnan(v):
            prev = v

    # flight segments: flyTime resets (new take-off in same file) start a new segment
    seg = np.zeros(n, dtype=int)
    for i in range(1, n):
        seg[i] = seg[i - 1] + (1 if fly[i] < fly[i - 1] - 1.0 else 0)
    dfly = np.diff(fly, prepend=np.nan)
    flying = (fly > 0) & ((dfly > 0) | np.roll((dfly > 0), -1))

    t_out = np.full(n, np.nan)
    method = np.array(["app_clock_interp"] * n, dtype=object)
    stats: dict[str, Any] = {"segments": []}
    for s in np.unique(seg):
        m = seg == s
        fm = m & flying & fresh & ~np.isnan(t_app_ns)
        seg_info: dict[str, Any] = {"segment": int(s), "rows": int(m.sum()), "anchor_pairs": int(fm.sum())}
        if fm.sum() >= 3:
            anchors = t_app_ns[fm] - fly[fm] * 1e9
            anchor = float(np.median(anchors))
            resid = (anchors - anchor) / 1e9
            seg_info.update(anchor_utc=str(pd.Timestamp(int(anchor), tz="UTC")),
                            resid_abs_p50_s=float(np.median(np.abs(resid))),
                            resid_abs_p95_s=float(np.quantile(np.abs(resid), 0.95)))
            use = m & flying
            t_out[use] = anchor + fly[use] * 1e9
            method[use] = "flytime_anchor"
        stats["segments"].append(seg_info)

    # frame period measured from flyTime during flight (DJI says 10 Hz; verify, don't assume)
    dfl = dfly[flying & (dfly > 0) & (dfly < 1.0)]
    period_s = float(np.median(dfl)) if dfl.size >= 10 else 0.1
    stats["frame_period_s_est"] = period_s

    # rows on the ground before take-off / after landing: step from the nearest anchored row
    # by frame index (keeps t_utc monotonic and free of app-clock jitter)
    for s in np.unique(seg):
        m = seg == s
        anchored = np.where(m & ~np.isnan(t_out))[0]
        if anchored.size == 0:
            continue
        a0, a1 = anchored.min(), anchored.max()
        pre = np.where(m & np.isnan(t_out) & (idx < a0))[0]
        post = np.where(m & np.isnan(t_out) & (idx > a1))[0]
        t_out[pre] = t_out[a0] - (a0 - pre) * period_s * 1e9
        t_out[post] = t_out[a1] + (post - a1) * period_s * 1e9
        method[pre] = "frame_index_from_anchor"
        method[post] = "frame_index_from_anchor"

    # anything left (no flight in the file): interpolate fresh app-clock samples over frame index
    fr = fresh & ~np.isnan(t_app_ns)
    rest = np.isnan(t_out)
    if rest.any() and fr.sum() >= 2:
        t_out[rest] = np.interp(idx[rest], idx[fr], t_app_ns[fr])
        # np.interp clamps at the ends; extrapolate at 10 Hz instead (DJI FR is 10 Hz)
        lo, hi = idx[fr].min(), idx[fr].max()
        before, after = rest & (idx < lo), rest & (idx > hi)
        t_out[before] = t_app_ns[lo] - (lo - idx[before]) * 1e8
        t_out[after] = t_app_ns[hi] + (idx[after] - hi) * 1e8
        method[before | after] = "flytime_extrapolated"
    elif rest.any():
        method[rest] = "missing"

    t_out = t_out + opts.clock_offset_s * 1e9
    qa = np.zeros(n, dtype=np.int32)
    qa[method != "flytime_anchor"] |= QA_T_INTERP
    # stale app clock: > 2 s since last fresh sample (by frame count at 10 Hz)
    last = -10**9
    for i in range(n):
        if fr[i]:
            last = i
        if i - last > 20:
            qa[i] |= QA_T_APP_STALE
    d = np.diff(t_out)
    nonmono = np.concatenate([[False], ~(d > 0)]) & ~np.isnan(t_out)
    qa[nonmono] |= QA_T_NONMONOTONIC

    out = pd.DataFrame({
        "t_utc": pd.to_datetime(pd.Series(t_out), utc=True, unit="ns"),
        "t_utc_method": method,
        "t_app_utc": t_app.dt.floor("ms") if hasattr(t_app, "dt") else t_app,
        "t_app_fresh": fresh,
        "fly_time_s": fly.astype(np.float32),
        "flight_segment": seg.astype(np.int16),
    })
    stats.update(clock_offset_s=opts.clock_offset_s, clock_model=opts.clock_model,
                 rows_flytime_anchor=int((method == "flytime_anchor").sum()),
                 rows_interp=int((method == "app_clock_interp").sum()),
                 rows_extrapolated=int((method == "flytime_extrapolated").sum()),
                 rows_frame_index=int((method == "frame_index_from_anchor").sum()),
                 nonmonotonic_rows=int(nonmono.sum()),
                 rule="t_utc = median(appTime - flyTime) + flyTime during flight; app-clock interpolation otherwise; + declared clock offset")
    return out, {"time_model": stats, "_qa": qa}


# --------------------------------------------------------------------------- geometry

def n2000_to_ellipsoidal(lat: np.ndarray, lon: np.ndarray, h_n2000: np.ndarray,
                         proj_dir: str = str(Path.home() / "Work" / "domeware-vendor" / "proj")) -> tuple[np.ndarray, str]:
    """N2000 -> ETRS89 ellipsoidal using the vendored FIN2023N2000 grid. Fails hard on ballpark."""
    import pyproj.datadir
    from pyproj import CRS, Transformer
    if proj_dir and proj_dir not in pyproj.datadir.get_data_dir():
        pyproj.datadir.append_data_dir(proj_dir)
    tr = Transformer.from_crs(CRS("EPSG:4258+3900"), CRS("EPSG:4937"), always_xy=True, only_best=True)
    _, _, h = tr.transform(lon, lat, h_n2000)
    op = tr.get_last_used_operation()
    desc = f"{op.description} | {op.to_proj4()}"
    if "ballpark" in desc.lower() or "fin2023n2000" not in desc.lower():
        raise RuntimeError(f"ballpark vertical transform refused: {desc}")
    return np.asarray(h, dtype=float), desc


def enu_from_geodetic(lat, lon, h_ellip, site: Site) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    x, y, z = tr.transform(lon, lat, h_ellip)
    x0, y0, z0 = tr.transform(site.lon_deg, site.lat_deg, site.h_ellip_m)
    la, lo = np.radians(site.lat_deg), np.radians(site.lon_deg)
    dx, dy, dz = np.asarray(x) - x0, np.asarray(y) - y0, np.asarray(z) - z0
    e = -np.sin(lo) * dx + np.cos(lo) * dy
    n = -np.sin(la) * np.cos(lo) * dx - np.sin(la) * np.sin(lo) * dy + np.cos(la) * dz
    u = np.cos(la) * np.cos(lo) * dx + np.cos(la) * np.sin(lo) * dy + np.sin(la) * dz
    return e, n, u


# --------------------------------------------------------------------------- main conversion

def convert(df: pd.DataFrame, opts: ConvertOptions, details: dict | None = None) -> tuple[pa.Table, pa.Table, dict]:
    details = details or {}
    df = df.reset_index(drop=True)
    n = len(df)
    tdf, tinfo = derive_time(df, opts)
    qa = tinfo.pop("_qa")

    lat = _num(df, "OSD.latitude").to_numpy(float)
    lon = _num(df, "OSD.longitude").to_numpy(float)
    gps_used = _bool(df, "OSD.isGPSUsed").fillna(False).to_numpy(bool)
    gps_level = _num(df, "OSD.gpsLevel").fillna(0).to_numpy()
    zero = (np.abs(lat) < 1e-9) & (np.abs(lon) < 1e-9)
    gps_valid = gps_used & (gps_level >= 3) & ~zero
    qa[~gps_valid] |= QA_GPS_INVALID
    qa[zero] |= QA_LATLON_ZERO

    height = _num(df, "OSD.height").to_numpy(float)
    vx, vy, vz = (_num(df, c).to_numpy(float) for c in ("OSD.xSpeed", "OSD.ySpeed", "OSD.zSpeed"))

    out = tdf.copy()
    out.insert(0, "frame_idx", np.arange(n, dtype=np.int32))
    sha12 = (opts.source_sha256 or "nosha")[:12]
    out["raw_ref"] = [f"{sha12}#frame={i}" for i in range(n)]
    out["lat_deg"] = np.where(zero, np.nan, lat)
    out["lon_deg"] = np.where(zero, np.nan, lon)
    out["gps_valid"] = gps_valid
    out["gps_num"] = _num(df, "OSD.gpsNum").astype("UInt8")
    out["gps_level"] = _num(df, "OSD.gpsLevel").astype("UInt8")
    out["height_rel_takeoff_m"] = height.astype(np.float32)
    out["vps_height_m"] = _num(df, "OSD.vpsHeight").astype("float32")
    out["dji_altitude_m"] = _num(df, "OSD.altitude").astype("float32")
    out["home_lat_deg"] = _num(df, "HOME.latitude")
    out["home_lon_deg"] = _num(df, "HOME.longitude")
    out["home_alt_dji_m"] = _num(df, "HOME.altitude").astype("float32")

    geo_note = "no DEM ground height supplied: N2000/ellipsoidal/ENU heights left null"
    if opts.takeoff_ground_n2000_m is not None:
        g = opts.takeoff_ground_n2000_m + opts.takeoff_height_offset_m
        h_n2000 = g + height
        out["takeoff_ground_n2000_m"] = np.float32(opts.takeoff_ground_n2000_m)
        out["h_n2000_m"] = h_n2000.astype(np.float32)
        ok = ~np.isnan(out["lat_deg"].to_numpy()) & ~np.isnan(h_n2000)
        h_ell = np.full(n, np.nan)
        if ok.any():
            h_ell[ok], geo_note = n2000_to_ellipsoidal(out["lat_deg"].to_numpy()[ok], out["lon_deg"].to_numpy()[ok], h_n2000[ok])
        out["h_ellip_m"] = h_ell
        if opts.site is not None and ok.any():
            e = np.full(n, np.nan); nn = np.full(n, np.nan); u = np.full(n, np.nan)
            e[ok], nn[ok], u[ok] = enu_from_geodetic(out["lat_deg"].to_numpy()[ok], out["lon_deg"].to_numpy()[ok], h_ell[ok], opts.site)
            out["e_m"], out["n_m"], out["u_m"] = e.astype(np.float32), nn.astype(np.float32), u.astype(np.float32)
            rng = np.sqrt(e**2 + nn**2 + u**2)
            out["slant_range_m"] = rng.astype(np.float32)
            out["azimuth_deg"] = (np.degrees(np.arctan2(e, nn)) % 360).astype(np.float32)
            out["elevation_deg"] = np.degrees(np.arctan2(u, np.hypot(e, nn))).astype(np.float32)
    else:
        qa |= QA_NO_DEM_GROUND

    out["dji_vx_mps"], out["dji_vy_mps"], out["dji_vz_mps"] = vx.astype(np.float32), vy.astype(np.float32), vz.astype(np.float32)
    out["h_speed_mps"] = np.hypot(vx, vy).astype(np.float32)
    for src, dst in (("OSD.pitch", "pitch_deg"), ("OSD.roll", "roll_deg"), ("OSD.yaw", "yaw_deg"),
                     ("GIMBAL.pitch", "gimbal_pitch_deg"), ("GIMBAL.roll", "gimbal_roll_deg"), ("GIMBAL.yaw", "gimbal_yaw_deg"),
                     ("BATTERY.voltage", "battery_v"), ("BATTERY.current", "battery_a"), ("BATTERY.temperature", "battery_temp_c")):
        out[dst] = _num(df, src).astype("float32")
    for src, dst in (("BATTERY.chargeLevel", "battery_pct"), ("RC.uplinkSignal", "rc_uplink_pct"), ("RC.downlinkSignal", "rc_downlink_pct")):
        out[dst] = _num(df, src).astype("UInt8")
    for src, dst in (("RC.aileron", "rc_aileron"), ("RC.elevator", "rc_elevator"), ("RC.throttle", "rc_throttle"), ("RC.rudder", "rc_rudder")):
        out[dst] = _num(df, src).astype("UInt16")
    for src, dst in (("OSD.flycState", "flyc_state"), ("OSD.flightAction", "flight_action"), ("OSD.goHomeStatus", "go_home_status"),
                     ("GIMBAL.mode", "gimbal_mode"), ("APP.tip", "app_tip"), ("APP.warn", "app_warn")):
        out[dst] = _str(df, src)
    for src, dst in (("OSD.isOnGround", "is_on_ground"), ("OSD.isMotorOn", "is_motor_on"), ("OSD.isVisionUsed", "is_vision_used"),
                     ("CAMERA.isVideo", "camera_is_video")):
        out[dst] = _bool(df, src)
    out["qa_flags"] = qa

    events = build_events(out)

    meta = {
        "stream": "dji_flight_record",
        "converter": f"dji_to_domeware {CONVERTER_VERSION}",
        "session_id": opts.session_id, "run_id": opts.run_id,
        "source_sha256": opts.source_sha256,
        "rows": n,
        "time_model": tinfo["time_model"],
        "geometry": {
            "horizontal": "lat/lon as logged (DJI GNSS, WGS84 realisation of date); ~0.9 m from EUREF-FIN, below DJI standalone accuracy",
            "vertical_chain": "h_n2000 = DEM(NLS, N2000) at home point + takeoff offset + OSD.height; h_ellip via FIN2023N2000",
            "vertical_note": geo_note,
            "site": opts.site.__dict__ if opts.site else None,
            "dji_altitude": "kept as given, reference unspecified (dji-log-parser issue #32: offsets with RTK); never used",
        },
        "details": details,
        "qa_bits": {"GPS_INVALID": QA_GPS_INVALID, "LATLON_ZERO": QA_LATLON_ZERO, "T_INTERP": QA_T_INTERP,
                    "T_APP_STALE": QA_T_APP_STALE, "T_NONMONOTONIC": QA_T_NONMONOTONIC, "NO_DEM_GROUND": QA_NO_DEM_GROUND},
        **opts.extra_meta,
    }
    table = pa.Table.from_pandas(out, preserve_index=False)
    table = table.replace_schema_metadata({
        b"domeware.stream": b"dji_osd",
        b"domeware.columns": json.dumps(COLUMN_META).encode(),
        b"domeware.meta": json.dumps(meta, default=str).encode(),
    })
    ev = pa.Table.from_pandas(events, preserve_index=False)
    return table, ev, meta


def build_events(o: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    states = (("is_motor_on", "motors"), ("is_on_ground", "ground"), ("flyc_state", "flight_mode"),
              ("gps_valid", "gps_valid"), ("go_home_status", "go_home"), ("camera_is_video", "video"))
    # whole columns as plain lists, read once: one pandas lookup per cell made an hour of log take seconds
    col = {c: [None if v is pd.NA else v for v in o[c].astype(object).tolist()]
           for c in [*(s for s, _ in states), "app_tip", "app_warn"]}
    t_utc, fly, frame = o["t_utc"].tolist(), o["fly_time_s"].tolist(), o["frame_idx"].tolist()

    def add(i: int, kind: str, text: str) -> None:
        rows.append({"t_utc": t_utc[i], "fly_time_s": float(fly[i]), "frame_idx": int(frame[i]), "kind": kind, "text": text})

    prev: dict[str, Any] = {}
    for i in range(len(o)):
        for c, kind in states:
            v = col[c][i]
            if c in prev and v != prev[c] and v is not None:
                label = {("ground", False): "take-off", ("ground", True): "landing"}.get((kind, v), f"{kind} -> {v}")
                add(i, "state", label)
            prev[c] = v
        for c in ("app_tip", "app_warn"):
            v = col[c][i]
            if isinstance(v, str) and v:
                add(i, c, v)
    return pd.DataFrame(rows, columns=["t_utc", "fly_time_s", "frame_idx", "kind", "text"])


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
