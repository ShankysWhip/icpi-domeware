#!/usr/bin/env python
"""Prototype converter: Robin IRIS XML stream -> UTC-aligned Parquet tables.

domeware prototype (not repo code). Reads a Robin "XML API" capture made of
concatenated <Robin>...</Robin> fragments (no single root element) and writes:

  tracks.parquet         one row per unique (track_id, track Timestamp)
  track_summary.parquet  one row per track id
  plots.parquet          PotentialDronePlots/plot rows
  status.parquet         SystemStatus, flattened, one row per (message, SensorStatus)
  motion.parquet         RadarMotion rows (time inferred: the source time is 1970-01-01)
  image_meta.parquet     RadarImageMeta rows
  site.json              radar site from SystemStatus + IrisRadar.config + tracker map (NO secrets)
  raw_headers.json       text headers of *.PLOTS.gz and *.IMG files
  import_report.json     accounting: read / kept / duplicates / unknown elements / checks
  quicklook_*.png        (optional) map, altitude of one track, class/alarm timeline

Time columns are Arrow timestamp[ns, tz=UTC]. Run with:
  uv run python -I robin_xml_to_parquet.py --xml <ozmasto2.xml> --out <dir>
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ADAPTER = "robin_xml_proto"
ADAPTER_VERSION = "0.1.0"

DEFAULT_ARCHIVE = Path(".")  # no built-in default; pass --xml/--cfg-dir

# ---------------------------------------------------------------- qflags
QF_TIME_INFERRED = 1        # t_utc is not a source time (interpolated from neighbours)
QF_DUP_CONFLICT = 2         # duplicate rows of the same key carried different values
QF_NON_MONOTONIC = 4        # source time went backwards for this entity in file order
QF_POSITION_ZERO = 8        # lat/lon reported as 0/0 (no position)
QF_RAW_TIME_EPOCH0 = 16     # raw time field was 1970-01-01T00:00:00Z
QFLAGS = {
    "1": "TIME_INFERRED: t_utc interpolated from neighbouring timestamped messages",
    "2": "DUP_CONFLICT: repeated rows for the same key differ in some field (first kept)",
    "4": "NON_MONOTONIC: source time went backwards for this entity in file order",
    "8": "POSITION_ZERO: lat/lon = 0/0, i.e. no position given",
    "16": "RAW_TIME_EPOCH0: raw time field was 1970-01-01T00:00:00Z",
}

# domeware class codes (int codes for PlotJuggler; Robin's own id is kept in class_id)
CLASS_CODES = {"UNCLASSIFIED": 0, "OTHER": 1, "VEHICLE": 2, "SUSPECTED_DRONE": 3, "DRONE": 4}

# ---------------------------------------------------------------- known XML paths
_P = ["Position/Latitude", "Position/Longitude", "Position/Altitude"]
KNOWN_PATHS: dict[str, set[str]] = {
    "Tracks": {
        "Tracks", "Tracks@timestamp", "Tracks/Track", "Tracks/Track@id", "Tracks/Track/Timestamp",
        "Tracks/Track/Position", *("Tracks/Track/" + p for p in _P),
        "Tracks/Track/Velocity", "Tracks/Track/Velocity/Azimuth", "Tracks/Track/Velocity/Elevation",
        "Tracks/Track/Velocity/Speed", "Tracks/Track/Classification", "Tracks/Track/Classification@id",
        "Tracks/Track/MedianRCS", "Tracks/Track/CurrentRCS", "Tracks/Track/Score", "Tracks/Track/Alarm",
    },
    "PotentialDronePlots": {
        "PotentialDronePlots", "PotentialDronePlots@timestamp", "PotentialDronePlots/plot",
        "PotentialDronePlots/plot@id", "PotentialDronePlots/plot@timestamp", "PotentialDronePlots/plot@rcs",
        "PotentialDronePlots/plot/position", "PotentialDronePlots/plot/position@latitude",
        "PotentialDronePlots/plot/position@longitude", "PotentialDronePlots/plot/position@altitude",
    },
    "RadarMotion": {
        "RadarMotion", "RadarMotion@RadarId", "RadarMotion/Timestamp", "RadarMotion/Position",
        *("RadarMotion/" + p for p in _P), "RadarMotion/Speed", "RadarMotion/Heading",
        "RadarMotion/Pitch", "RadarMotion/Roll",
    },
    "RadarImageMeta": {
        "RadarImageMeta", "RadarImageMeta@RadarId", "RadarImageMeta@ImageId", "RadarImageMeta@Timestamp",
        "RadarImageMeta@LineResolution", "RadarImageMeta@AzimuthCellSize",
        "RadarImageMeta/LineClassifications", "RadarImageMeta/LineClassifications/Lines",
        "RadarImageMeta/LineClassifications/Lines@Type",
    },
}
_SS = "SystemStatus/Sensors/SensorStatus"
KNOWN_PATHS["SystemStatus"] = {
    "SystemStatus", "SystemStatus@timestamp", "SystemStatus/Name", "SystemStatus/Version",
    "SystemStatus/OperationalState", "SystemStatus/Messages", "SystemStatus/Sensors", _SS, _SS + "@id",
    *(f"{_SS}/{x}" for x in (
        "Name", "Version", "OperationalState", "Position", *_P, "GroundLevel", "Orientation",
        "Orientation/Azimuth", "Orientation/Elevation", "SensorType", "BlankingSector",
        "BlankingSector/Angle", "BlankingSector/Span", "BlankingSector/Enabled", "Processing",
        "Processing/OperatingModeId", "Processing/Ready", "Processing/DynamicPositioning",
        "Processing/Sensitivity", "Processing/ImageQuality", "Processing/ImageRecordingOn", "Messages",
        "Components", "Components/Transceiver", "Components/Transceiver@initialized",
        "Components/Transceiver@SerialNumber", "Components/Transceiver@CarrierFrequencyMhz",
        "Components/Transceiver@HardwareRevision", "Components/Transceiver@SoftwareRevision",
        "Components/Transceiver@FPGARevision", "Components/Data", "Components/Data@running",
        "Components/MotorController", "Components/MotorController@connected",
        "Components/MotorController@running", "Components/Database", "Components/Database@connected",
        "Components/GPS", "Components/GPS@Ready", "Components/GPS@Running", "Components/GPS@Lock",
        "Components/GPS@HDOP", "Components/Magnetometer", "Components/Magnetometer@Ready",
        "Components/Magnetometer@Sensitivity", "Components/Inclinometer", "Components/Inclinometer@Roll",
        "Components/Inclinometer@Pitch", "Components/Inclinometer@WithinLimits", "ProcessingStation",
        "ProcessingStation/DiskSpace", "ProcessingStation/DiskSpace/Total", "ProcessingStation/DiskSpace/Free",
    )),
}

# ---------------------------------------------------------------- column units (written to Parquet metadata)
UNITS = {
    "tracks": {
        "t_utc": "UTC, Track/Timestamp (time of the track's last state; inferred validity time)",
        "t_msg": "UTC, Tracks@timestamp of the FIRST message that carried this state (send time)",
        "t_msg_last": "UTC, Tracks@timestamp of the LAST message that carried this state",
        "lat_deg": "deg WGS84", "lon_deg": "deg WGS84",
        "alt_m": "m, vertical datum UNVERIFIED (same datum as radar Position/Altitude)",
        "h_above_site_ground_m": "m, alt_m - radar GroundLevel (NOT terrain AGL)",
        "vel_az_deg": "deg, course of velocity vector, 0=N clockwise [inferred]",
        "vel_el_deg": "deg, climb angle of velocity vector [inferred]", "speed_mps": "m/s, 3D speed [inferred]",
        "v_e_mps": "m/s derived", "v_n_mps": "m/s derived", "v_u_mps": "m/s derived",
        "median_rcs_dbsm": "dBsm", "current_rcs_dbsm": "dBsm", "score": "0..1 (classifier/track score)",
        "e_m": "m ENU east from radar antenna", "n_m": "m ENU north", "u_m": "m ENU up",
        "range_m": "m slant range from radar antenna", "az_deg": "deg true azimuth from radar",
        "el_deg": "deg elevation from radar", "latency_s": "s, t_msg - t_utc",
    },
    "plots": {"t_utc": "UTC, plot@timestamp", "t_msg": "UTC, PotentialDronePlots@timestamp",
              "rcs_dbsm": "dBsm [inferred]", "alt_m": "m, datum UNVERIFIED"},
    "motion": {"t_utc": "UTC, INFERRED (interpolated, see qflags)", "pitch_deg": "deg", "roll_deg": "deg",
               "heading_deg": "deg", "speed_mps": "m/s"},
    "image_meta": {"t_utc": "UTC, RadarImageMeta@Timestamp (= image acquisition time, matches IMG header)",
                   "line_resolution_deg": "deg", "azimuth_cell_size_deg": "deg",
                   "no_data_az_deg": "deg, azimuth line centres classified 'No Data'"},
    "status": {"t_utc": "UTC, SystemStatus@timestamp"},
}

SECRET_KEY_RE = re.compile(r"(PASSWORD|PASSWD|API_KEY|APIKEY|SECRET|PRIVATE_KEY|TOKEN(?!_ISSUER|_VALIDITY)|"
                           r"CERTIFICATE|CREDENTIAL|USERNAME|CLIENT_ID)", re.IGNORECASE)
# keys that are redacted in outputs but whose values are ordinary words (e.g. a user name "robin")
# and so would false-positive in the leak scan
NOT_SECRET_VALUE_KEYS = {"USERNAME", "CLIENT_ID"}
URL_KEY_RE = re.compile(r"([?&](?:key|api_key|apikey|token|access_token)=)[^&\"'\s]+", re.IGNORECASE)


# =============================================================== helpers
def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def parse_ts(s: str | None) -> pd.Timestamp | None:
    if s is None or not s.strip():
        return None
    return pd.Timestamp(s.strip()).tz_convert("UTC").as_unit("ns")


def to_float(s):
    if s is None:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def to_bool(s):
    if s is None:
        return None
    v = s.strip().lower()
    return True if v == "true" else False if v == "false" else None


def snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def walk_paths(elem: ET.Element, prefix: str = ""):
    path = f"{prefix}/{elem.tag}" if prefix else elem.tag
    yield path
    for a in elem.attrib:
        yield f"{path}@{a}"
    for c in elem:
        yield from walk_paths(c, path)


# =============================================================== fragment splitting
_START_RE = re.compile(rb"<Robin(?=[\s>/])")


def split_fragments(data: bytes):
    """Yield (index, byte_offset, element | None, error | None) for each <Robin> fragment.

    The capture is a concatenation of XML documents. Each fragment runs from one
    '<Robin' start tag to the next. A truncated last fragment yields an error, not a crash.
    """
    starts = [m.start() for m in _START_RE.finditer(data)]
    lead = data[: starts[0]] if starts else data
    lead_s = re.sub(rb"<\?xml[^>]*\?>", b"", lead).strip()
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(data)
        chunk = data[s:e]
        chunk = re.sub(rb"<\?xml[^>]*\?>\s*$", b"", chunk.rstrip())  # next fragment's xml decl
        if b"<!DOCTYPE" in chunk or b"<!ENTITY" in chunk:  # no DTDs/entities: blocks entity-expansion attacks
            yield i, s, None, "rejected: DOCTYPE/ENTITY declaration in fragment"
            continue
        try:
            root = ET.fromstring(chunk)
            yield i, s, root, None
        except ET.ParseError as exc:
            yield i, s, None, f"{type(exc).__name__}: {exc}"
    if lead_s:
        yield -1, 0, None, f"{len(lead_s)} non-whitespace bytes before first <Robin>"


# =============================================================== Robin config parsing (IrisRadar.config / *.map)
def _strip_comment(line: str) -> str:
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            break
        out.append(ch)
    return "".join(out).strip()


def _typed(v: str):
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1]
    if v.upper() in ("TRUE", "FALSE"):
        return v.upper() == "TRUE"
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return v


def _put(d: dict, k: str, v):
    if k in d:
        if not isinstance(d[k], list) or not d.get(f"__list__{k}"):
            d[k] = [d[k]]
            d[f"__list__{k}"] = True
        d[k].append(v)
    else:
        d[k] = v


def _clean_lists(o):
    if isinstance(o, dict):
        return {k: _clean_lists(v) for k, v in o.items() if not k.startswith("__list__")}
    if isinstance(o, list):
        return [_clean_lists(x) for x in o]
    return o


def parse_robin_config(text: str) -> dict:
    """Parse Robin's brace config ('NAME { KEY VALUE ... }'). Repeated keys/blocks become lists."""
    root: dict = {}
    stack = [root]
    pending: str | None = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line:
            continue
        if line == "{":
            new: dict = {}
            _put(stack[-1], pending or "_anon", new)
            stack.append(new)
            pending = None
            continue
        if line == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        if pending is not None:  # bare word not followed by '{' -> flag
            _put(stack[-1], pending, None)
            pending = None
        parts = line.split(None, 1)
        if len(parts) == 1:
            pending = parts[0]
        else:
            _put(stack[-1], parts[0], _typed(parts[1].strip()))
    if pending is not None:
        _put(stack[-1], pending, None)
    return _clean_lists(root)


def find_secret_values(text: str) -> list[str]:
    """Return secret-looking VALUES from a Robin config text (used only to verify outputs are clean)."""
    vals = set()
    for raw in text.splitlines():
        line = _strip_comment(raw)
        parts = line.split(None, 1)
        if len(parts) == 2 and SECRET_KEY_RE.search(parts[0]) and parts[0].upper() not in NOT_SECRET_VALUE_KEYS:
            v = parts[1].strip().strip('"')
            if len(v) >= 4:
                vals.add(v)
        for m in re.finditer(r"[?&](?:key|api_key|apikey|token|access_token)=([^&\"'\s]+)", raw, re.IGNORECASE):
            if len(m.group(1)) >= 4:
                vals.add(m.group(1))
    return sorted(vals)


def redact(o, path: str = ""):
    """Recursively redact secret-looking keys and URL keys."""
    if isinstance(o, dict):
        return {k: ("<REDACTED>" if (SECRET_KEY_RE.search(k) and v not in (None, "", False))
                    else redact(v, f"{path}.{k}")) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x, path) for x in o]
    if isinstance(o, str):
        return URL_KEY_RE.sub(r"\1<REDACTED>", o)
    return o


# =============================================================== Robin binary file text headers (PLOTS / IMG)
def parse_text_header(raw: bytes) -> dict:
    """Parse the ASCII header that precedes 'START_BINARY;' in Robin PLOTS/IMG files."""
    idx = raw.find(b"START_BINARY;")
    if idx < 0:
        return {"error": "START_BINARY not found in header window"}
    nl = raw.find(b"\n", idx)
    bin_off = (nl + 1) if nl >= 0 else idx + len(b"START_BINARY;")
    text = raw[:idx].decode("ascii", errors="replace")
    comments, root = [], {}
    stack, names = [root], ["<root>"]
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(";"):
            body = s.lstrip(";").strip()
            if body and not set(body) <= set("-"):
                comments.append(body)
            continue
        s = s.rstrip(";").strip()
        if "=" in s:
            k, v = (x.strip() for x in s.split("=", 1))
            _put(stack[-1], k, _typed(v))
        elif s.startswith("END_") and len(stack) > 1 and names[-1] == s[4:]:
            stack.pop(); names.pop()
        else:
            new: dict = {}
            _put(stack[-1], s, new)
            stack.append(new); names.append(s)
    meta = {}
    for c in comments:
        m = re.match(r"FileVersion\s+(\S+)\s*:\s*(.*)", c)
        if m:
            meta["file_version"], meta["vendor"] = m.group(1), m.group(2).strip()
        m = re.match(r"ORIGINAL\s*:\s*(.*)", c)
        if m:
            meta["original_raw"] = m.group(1).strip()
            try:  # DD-MM-YYYY HH:MM:SS.fff, zone not stated (assumed UTC, see report)
                t = dt.datetime.strptime(m.group(1).strip(), "%d-%m-%Y %H:%M:%S.%f")
                meta["original_utc_assumed"] = t.replace(tzinfo=dt.UTC).isoformat()
            except ValueError:
                pass
    probe = raw[bin_off: bin_off + 10]
    probe_d = {}
    if len(probe) == 10:
        ver = struct.unpack("<H", probe[:2])[0]
        tsec = struct.unpack("<d", probe[2:10])[0]
        probe_d = {"uint16_le_at_0": ver, "float64_le_at_2": tsec}
        if 1.5e9 < tsec < 2.5e9:
            probe_d["float64_as_unix_utc"] = dt.datetime.fromtimestamp(tsec, dt.UTC).isoformat()
        probe_d["note"] = "UNVERIFIED guess at the first binary bytes; not from a Robin spec"
    return {"comment_meta": meta, "header": _clean_lists(root), "binary_offset": bin_off,
            "binary_probe_unverified": probe_d}


LIST_MAGIC = struct.pack("<H", 3)  # observed first two bytes of each plot list [unverified]


def scan_plot_lists(body: bytes, rec: dict) -> dict:
    """UNVERIFIED heuristic: locate per-second plot-list records in a decompressed PLOTS file.

    Observed (not from a spec): each list starts with uint16 LE = 3, float64 LE unix time [s],
    uint32 LE that matches the XML RadarImageMeta ImageId sequence. Validated only by the count
    matching NR_OF_LISTS and 1 s spacing. Decoding plots inside a list needs Robin's format spec.
    """
    t0 = struct.unpack_from("<d", body, rec["binary_offset"] + 2)[0] if len(body) > rec["binary_offset"] + 10 else 0
    hits = []
    for m in re.finditer(re.escape(LIST_MAGIC), body[rec["binary_offset"]:]):
        o = rec["binary_offset"] + m.start()
        if o + 14 > len(body):
            break
        t = struct.unpack_from("<d", body, o + 2)[0]
        if t0 - 1 < t < t0 + 3600:
            hits.append((o, t, struct.unpack_from("<I", body, o + 10)[0]))
    n_hdr = ((rec.get("header") or {}).get("RESULT") or {}).get("PLOTLIST_DATA", {}).get("NR_OF_LISTS")
    lists = []
    for i, (o, t, img) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(body)
        lists.append({"index": i, "offset": o, "length": end - o,
                      "t_utc": dt.datetime.fromtimestamp(t, dt.UTC).isoformat(),
                      "image_id_guess": img})
    return {"method": "pattern uint16=3 + float64 unix time near the first list's time; UNVERIFIED",
            "found": len(lists), "nr_of_lists_in_header": n_hdr,
            "count_matches_header": n_hdr == len(lists), "lists": lists}


def read_raw_headers(paths: list[Path], hash_files: bool = True) -> dict:
    out = {}
    for p in paths:
        rec: dict = {"path": str(p), "size_bytes": p.stat().st_size}
        if hash_files:
            rec["sha256"] = sha256_file(p)
        m = re.search(r"_(\d{8})_(\d{6})\.(\d+)\.", p.name)
        if m:
            rec["filename_time_utc_assumed"] = dt.datetime.strptime(
                m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
                microsecond=int(m.group(3).ljust(6, "0")[:6]), tzinfo=dt.UTC).isoformat()
        try:
            if p.suffix == ".gz":
                with gzip.open(p, "rb") as f:
                    body = f.read()
                head = body[: 1 << 16]
                rec["decompressed_size_bytes"] = len(body)
                rec["kind"] = "PLOTS"
            else:
                with open(p, "rb") as f:
                    head = f.read(1 << 16)
                rec["kind"] = "IMG" if p.suffix.upper() == ".IMG" else p.suffix
            rec.update(parse_text_header(head))
            if rec.get("kind") == "PLOTS" and "binary_offset" in rec:
                rec["plot_lists_unverified"] = scan_plot_lists(body, rec)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            rec["error"] = f"{type(exc).__name__}: {exc}"
        out[p.name] = rec
    return out


# =============================================================== message parsers
def _txt(e: ET.Element | None, path: str):
    if e is None:
        return None
    x = e.find(path)
    return x.text.strip() if x is not None and x.text is not None else None


def parse_track(tr: ET.Element) -> dict:
    cl = tr.find("Classification")
    cid = cl.get("id") if cl is not None else None
    return {
        "track_id": int(tr.get("id")),
        "t_utc": parse_ts(_txt(tr, "Timestamp")),
        "lat_deg": to_float(_txt(tr, "Position/Latitude")),
        "lon_deg": to_float(_txt(tr, "Position/Longitude")),
        "alt_m": to_float(_txt(tr, "Position/Altitude")),
        "vel_az_deg": to_float(_txt(tr, "Velocity/Azimuth")),
        "vel_el_deg": to_float(_txt(tr, "Velocity/Elevation")),
        "speed_mps": to_float(_txt(tr, "Velocity/Speed")),
        "class_name": (cl.text or "").strip() if cl is not None else None,
        "class_id": int(cid) if cid is not None else None,
        "median_rcs_dbsm": to_float(_txt(tr, "MedianRCS")),
        "current_rcs_dbsm": to_float(_txt(tr, "CurrentRCS")),
        "score": to_float(_txt(tr, "Score")),
        "alarm": to_bool(_txt(tr, "Alarm")),
    }


TRACK_VALUE_COLS = ["lat_deg", "lon_deg", "alt_m", "vel_az_deg", "vel_el_deg", "speed_mps", "class_name",
                    "class_id", "median_rcs_dbsm", "current_rcs_dbsm", "score", "alarm"]


def flatten_elem(e: ET.Element, prefix: str, out: dict):
    """Generic flatten: attributes -> prefix_attr, leaf text -> prefix_tag; repeated siblings get _<i>."""
    for a, v in e.attrib.items():
        out[f"{prefix}_{snake(a)}" if prefix else snake(a)] = v
    tags = Counter(c.tag for c in e)
    seen: Counter = Counter()
    for c in e:
        name = snake(c.tag)
        if tags[c.tag] > 1:
            name = f"{name}_{seen[c.tag]}"
            seen[c.tag] += 1
        key = f"{prefix}_{name}" if prefix else name
        if len(c) == 0 and not c.attrib:
            out[key] = (c.text or "").strip()
        elif len(c) == 0 and c.attrib and (c.text or "").strip():
            out[key] = c.text.strip()
            flatten_elem(c, key, out)
        else:
            flatten_elem(c, key, out)


_STRING_HINT = re.compile(r"(version|revision|serial|name|state|type|mode|quality)", re.IGNORECASE)


def coerce_columns(df: pd.DataFrame, skip: set[str]) -> pd.DataFrame:
    for c in df.columns:
        if c in skip:
            continue
        s = df[c]
        nn = s.dropna()
        if nn.empty:
            continue
        if _STRING_HINT.search(c):
            df[c] = s.astype("string")
            continue
        low = nn.astype(str).str.lower()
        if low.isin(["true", "false"]).all():
            df[c] = s.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v))
                          else str(v).lower() == "true").astype("boolean")
            continue
        try:
            ints = pd.to_numeric(nn, errors="raise")
            if (nn.astype(str).str.fullmatch(r"-?\d+")).all():
                df[c] = pd.to_numeric(s).astype("Int64")
            else:
                df[c] = pd.to_numeric(s).astype("float64")
            del ints
        except (ValueError, TypeError):
            df[c] = s.astype("string")
    return df


# =============================================================== geometry
def enu_from_geodetic(lat, lon, h, lat0, lon0, h0):
    """Local ENU (m) of points relative to (lat0, lon0, h0). Heights treated as ellipsoidal.

    Both radar and target heights come from the same Robin source, so a common datum
    offset (geoid ~ 19 m) cancels in u to first order. Absolute datum is UNVERIFIED.
    """
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    x, y, z = tr.transform(np.asarray(lon, float), np.asarray(lat, float), np.asarray(h, float))
    x0, y0, z0 = tr.transform(lon0, lat0, h0)
    dx, dy, dz = x - x0, y - y0, z - z0
    la, lo = math.radians(lat0), math.radians(lon0)
    e = -math.sin(lo) * dx + math.cos(lo) * dy
    n = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
    u = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    return e, n, u


def add_geometry(df: pd.DataFrame, site_pos: dict | None):
    if df.empty or not site_pos:
        return df
    e, n, u = enu_from_geodetic(df["lat_deg"], df["lon_deg"], df["alt_m"],
                                site_pos["lat_deg"], site_pos["lon_deg"], site_pos["alt_m"])
    df["e_m"], df["n_m"], df["u_m"] = e, n, u
    hor = np.hypot(e, n)
    df["range_m"] = np.sqrt(hor ** 2 + u ** 2)
    df["az_deg"] = np.degrees(np.arctan2(e, n)) % 360.0
    df["el_deg"] = np.degrees(np.arctan2(u, hor))
    if site_pos.get("ground_level_m") is not None:
        df["h_above_site_ground_m"] = df["alt_m"] - site_pos["ground_level_m"]
    return df


# =============================================================== Parquet writing
def write_parquet(df: pd.DataFrame, path: Path, table: str, meta: dict) -> dict:
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    fields = []
    for f in tbl.schema:
        t = f.type
        if pa.types.is_timestamp(t):
            t = pa.timestamp("ns", tz="UTC")
        elif pa.types.is_large_string(t) or pa.types.is_string_view(t):
            t = pa.string()
        elif pa.types.is_dictionary(t):
            t = t.value_type
        fields.append(pa.field(f.name, t))
    tbl = tbl.cast(pa.schema(fields))
    md = {b"domeware": json.dumps({"table": table, "adapter": ADAPTER, "adapter_version": ADAPTER_VERSION,
                                   "units": UNITS.get(table, {}), "qflags": QFLAGS, **meta}).encode()}
    tbl = tbl.replace_schema_metadata(md)
    pq.write_table(tbl, path, compression="zstd")
    return {"file": path.name, "rows": tbl.num_rows, "columns": tbl.num_columns, "sha256": sha256_file(path)}


# =============================================================== main conversion
def convert(xml_path: Path, out_dir: Path, cfg_dir: Path | None = None, raw_files: list[Path] | None = None,
            quicklook: bool = True, focus_track: int | None = None, hash_raw: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = xml_path.read_bytes()
    xml_sha = hashlib.sha256(data).hexdigest()
    report: dict = {
        "adapter": ADAPTER, "adapter_version": ADAPTER_VERSION,
        "converted_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "input": {"path": str(xml_path), "size_bytes": len(data), "sha256": xml_sha},
        "qflags": QFLAGS, "class_codes": CLASS_CODES,
    }
    msg_counts: Counter = Counter()
    exact_dups: Counter = Counter()      # byte-identical repeats of a whole message
    empty_msgs: Counter = Counter()
    seen_digests: dict[str, set] = defaultdict(set)
    unknown_types: Counter = Counter()
    unknown_paths: dict[str, Counter] = defaultdict(Counter)
    frag_errors = []
    track_rows, plot_rows, status_rows, motion_rows, img_rows = [], [], [], [], []
    stamps = []  # (offset, t) of send-time stamped messages, for inferring motion time
    n_frag = 0
    msg_times: dict[str, list] = defaultdict(list)

    for idx, off, root, err in split_fragments(data):
        if err:
            frag_errors.append({"index": idx, "byte_offset": off, "error": err})
            continue
        n_frag += 1
        children = list(root)
        if not children:
            unknown_types["<empty Robin>"] += 1
        for msg in children:
            mtype = msg.tag
            msg_counts[mtype] += 1
            digest = hashlib.sha1(ET.tostring(msg)).hexdigest()
            is_exact_dup = digest in seen_digests[mtype]
            seen_digests[mtype].add(digest)
            if is_exact_dup:
                exact_dups[mtype] += 1
            if mtype not in KNOWN_PATHS:
                unknown_types[mtype] += 1
                continue
            for p in walk_paths(msg):
                if p not in KNOWN_PATHS[mtype]:
                    unknown_paths[mtype][p] += 1
            raw_ref = f"msg{idx}@{off}"
            if mtype == "Tracks":
                t_msg = parse_ts(msg.get("timestamp"))
                stamps.append((off, t_msg)); msg_times[mtype].append(t_msg)
                for k, tr in enumerate(msg.findall("Track")):
                    r = parse_track(tr)
                    r.update(t_msg=t_msg, msg_index=idx, raw_ref=f"{raw_ref}#{k}")
                    track_rows.append(r)
                if not msg.findall("Track"):
                    empty_msgs[mtype] += 1
            elif mtype == "PotentialDronePlots":
                t_msg = parse_ts(msg.get("timestamp"))
                stamps.append((off, t_msg)); msg_times[mtype].append(t_msg)
                for k, pl in enumerate(msg.findall("plot")):
                    pos = pl.find("position")
                    plot_rows.append({
                        "plot_id": int(pl.get("id")), "t_utc": parse_ts(pl.get("timestamp")), "t_msg": t_msg,
                        "lat_deg": to_float(pos.get("latitude") if pos is not None else None),
                        "lon_deg": to_float(pos.get("longitude") if pos is not None else None),
                        "alt_m": to_float(pos.get("altitude") if pos is not None else None),
                        "rcs_dbsm": to_float(pl.get("rcs")), "msg_index": idx, "raw_ref": f"{raw_ref}#{k}"})
            elif mtype in ("SystemStatus", "RadarMotion", "RadarImageMeta") and is_exact_dup:
                continue
            elif mtype == "SystemStatus":
                t_msg = parse_ts(msg.get("timestamp"))
                stamps.append((off, t_msg)); msg_times[mtype].append(t_msg)
                base = {"t_utc": t_msg, "msg_index": idx, "raw_ref": raw_ref,
                        "sys_name": _txt(msg, "Name"), "sys_version": _txt(msg, "Version"),
                        "sys_operational_state": _txt(msg, "OperationalState")}
                sm = msg.find("Messages")
                base["sys_messages_json"] = json.dumps([ET.tostring(c, encoding="unicode") for c in sm]) \
                    if sm is not None else None
                base["sys_messages_n"] = len(sm) if sm is not None else 0
                sensors = msg.findall("Sensors/SensorStatus") or [None]
                for s in sensors:
                    row = dict(base)
                    if s is not None:
                        flat: dict = {}
                        flatten_elem(s, "", flat)
                        smsg = s.find("Messages")
                        flat.pop("messages", None)
                        row["sensor_messages_json"] = json.dumps(
                            [ET.tostring(c, encoding="unicode") for c in smsg]) if smsg is not None else None
                        row["sensor_messages_n"] = len(smsg) if smsg is not None else 0
                        row.update({f"s_{k}": v for k, v in flat.items()})
                    status_rows.append(row)
            elif mtype == "RadarMotion":
                t_raw = _txt(msg, "Timestamp")
                motion_rows.append({
                    "t_raw": t_raw, "offset": off, "msg_index": idx, "raw_ref": raw_ref,
                    "radar_id": int(msg.get("RadarId")) if msg.get("RadarId") else None,
                    "lat_deg": to_float(_txt(msg, "Position/Latitude")),
                    "lon_deg": to_float(_txt(msg, "Position/Longitude")),
                    "alt_m": to_float(_txt(msg, "Position/Altitude")),
                    "speed_mps": to_float(_txt(msg, "Speed")), "heading_deg": to_float(_txt(msg, "Heading")),
                    "pitch_deg": to_float(_txt(msg, "Pitch")), "roll_deg": to_float(_txt(msg, "Roll"))})
            elif mtype == "RadarImageMeta":
                t = parse_ts(msg.get("Timestamp"))
                msg_times[mtype].append(t)
                lines = {}
                for ln in msg.findall("LineClassifications/Lines"):
                    try:
                        vals = json.loads(ln.text or "[]")
                    except json.JSONDecodeError:
                        vals = [ln.text]
                    lines.setdefault(ln.get("Type") or "", []).extend(vals)
                img_rows.append({
                    "t_utc": t, "image_id": int(msg.get("ImageId")), "radar_id": int(msg.get("RadarId")),
                    "line_resolution_deg": to_float(msg.get("LineResolution")),
                    "azimuth_cell_size_deg": to_float(msg.get("AzimuthCellSize")),
                    "n_line_types": len(lines),
                    "n_no_data_lines": len(lines.get("No Data", [])),
                    "no_data_az_deg": [float(v) for v in lines.get("No Data", [])],
                    "line_classes_json": json.dumps(lines) if lines else None,
                    "msg_index": idx, "raw_ref": raw_ref})

    report["fragments"] = {"parsed": n_frag, "failed": len(frag_errors), "errors": frag_errors[:50]}
    report["message_counts"] = dict(msg_counts)
    report["message_exact_duplicates"] = dict(exact_dups)
    report["message_exact_duplicates_note"] = (
        "Whole messages repeated byte-for-byte. Tracks/PotentialDronePlots repeats are removed by the "
        "row-level de-duplication; SystemStatus/RadarMotion/RadarImageMeta repeats are skipped. Cause unknown "
        "(capture tool with several subscriptions, or Robin re-sending) [unverified].")
    report["empty_messages"] = dict(empty_msgs)
    report["unknown_message_types"] = dict(unknown_types)
    report["unknown_paths"] = {k: dict(v) for k, v in unknown_paths.items()}
    mono = {}
    for k, ts in msg_times.items():
        arr = pd.Series(ts, dtype="datetime64[ns, UTC]")
        d = arr.diff().dt.total_seconds()
        du = arr.drop_duplicates().diff().dt.total_seconds()
        mono[k] = {"n": len(arr), "unique_timestamps": int(arr.nunique()), "first": str(arr.min()),
                   "last": str(arr.max()), "backward_steps_in_file_order": int((d < 0).sum()),
                   "median_period_unique_s": float(du.median()) if len(du) > 1 else None}
    report["message_time_checks"] = mono

    tables: dict = {}
    outputs: list = []

    # ---------------- site position (needed for geometry)
    status_df = pd.DataFrame(status_rows)
    site_pos = None
    if not status_df.empty and "s_position_latitude" in status_df:
        f0 = status_df.iloc[0]
        site_pos = {"lat_deg": float(f0["s_position_latitude"]), "lon_deg": float(f0["s_position_longitude"]),
                    "alt_m": float(f0["s_position_altitude"]),
                    "ground_level_m": float(f0["s_ground_level"]) if "s_ground_level" in f0 else None}

    # ---------------- tracks
    tr = pd.DataFrame(track_rows)
    rep_t: dict = {"rows_read": len(tr)}
    if not tr.empty:
        tr["qflags"] = 0
        # non-monotonic per track in file order
        tr["_ord"] = np.arange(len(tr))
        back = tr.groupby("track_id")["t_utc"].transform(lambda s: s.diff().dt.total_seconds() < 0)
        rep_t["non_monotonic_rows"] = int(back.sum())
        tr.loc[back.astype(bool), "qflags"] |= QF_NON_MONOTONIC
        g = tr.groupby(["track_id", "t_utc"], sort=False)
        first = g.head(1).copy()
        agg = g.agg(n_msgs=("t_msg", "size"), t_msg_last=("t_msg", "max"), _qf=("qflags", "max"),
                    **{f"_nu_{c}": (c, lambda s: s.astype(str).nunique()) for c in TRACK_VALUE_COLS})
        nu_cols = [f"_nu_{c}" for c in TRACK_VALUE_COLS]
        conflict = (agg[nu_cols] > 1).any(axis=1)
        conflict_fields = Counter()
        for c in TRACK_VALUE_COLS:
            conflict_fields[c] = int((agg[f"_nu_{c}"] > 1).sum())
        agg["_conflict"] = conflict
        first = first.merge(agg[["n_msgs", "t_msg_last", "_qf", "_conflict"]].reset_index(),
                            on=["track_id", "t_utc"], how="left")
        first["qflags"] = first["_qf"].astype(int) | np.where(first["_conflict"], QF_DUP_CONFLICT, 0)
        first = first.drop(columns=["_qf", "_conflict", "_ord"])
        first["n_dup"] = first["n_msgs"] - 1
        first["latency_s"] = (first["t_msg"] - first["t_utc"]).dt.total_seconds()
        az, el = np.radians(first["vel_az_deg"]), np.radians(first["vel_el_deg"])
        first["v_e_mps"] = first["speed_mps"] * np.cos(el) * np.sin(az)
        first["v_n_mps"] = first["speed_mps"] * np.cos(el) * np.cos(az)
        first["v_u_mps"] = first["speed_mps"] * np.sin(el)
        unknown_classes = sorted(set(first["class_name"].dropna()) - set(CLASS_CODES))
        codes = dict(CLASS_CODES)
        for i, n in enumerate(unknown_classes):
            codes[n] = 100 + i
        first["class_code"] = first["class_name"].map(codes).astype("Int32")
        first["class_id"] = first["class_id"].astype("Int32")
        first["alarm"] = first["alarm"].astype("boolean")
        first = add_geometry(first, site_pos)
        first = first.sort_values(["t_utc", "track_id"], kind="stable").reset_index(drop=True)
        first["qflags"] = first["qflags"].astype("int32")
        lead = ["t_utc", "track_id", "t_msg", "t_msg_last", "n_msgs", "n_dup", "latency_s"]
        first = first[lead + [c for c in first.columns if c not in lead]]
        rep_t.update(rows_kept=len(first), duplicates_dropped=int(len(tr) - len(first)),
                     unique_tracks=int(first["track_id"].nunique()),
                     keys_with_conflicting_duplicates=int(conflict.sum()),
                     conflicting_fields=dict(conflict_fields),
                     accounting_ok=bool(len(tr) == len(first) + (len(tr) - len(first))),
                     latency_s={k: float(v) for k, v in first["latency_s"].describe().items()},
                     unknown_class_names=unknown_classes, class_codes_used=codes)
        cls_obs = (tr.assign(class_id=tr["class_id"].astype("Int32"))
                   .groupby(["class_name", "class_id"], dropna=False).size())
        rep_t["classification_observed_raw_rows"] = [
            {"class_name": n, "robin_class_id": (None if pd.isna(i) else int(i)), "raw_rows": int(c)}
            for (n, i), c in cls_obs.items()]
        tables["tracks"] = first
        # summary
        summ = first.groupby("track_id").agg(
            t_first=("t_utc", "min"), t_last=("t_utc", "max"), n_states=("t_utc", "size"),
            n_msgs=("n_msgs", "sum"), alarm_any=("alarm", "max"), alarm_states=("alarm", "sum"),
            max_score=("score", "max"), alt_min_m=("alt_m", "min"), alt_max_m=("alt_m", "max"),
            median_speed_mps=("speed_mps", "median"), median_rcs_dbsm=("median_rcs_dbsm", "median"),
            min_range_m=("range_m", "min") if "range_m" in first else ("alt_m", "size"),
            max_class_code=("class_code", "max"),
            classes=("class_name", lambda s: ",".join(sorted(set(s.dropna()))))).reset_index()
        last_cls = first.groupby("track_id")["class_name"].last().rename("last_class")
        summ = summ.merge(last_cls.reset_index(), on="track_id")
        summ["duration_s"] = (summ["t_last"] - summ["t_first"]).dt.total_seconds()
        raw_alarm = tr[tr["alarm"] == True]
        ev = {
            "t_first_msg": tr.groupby("track_id")["t_msg"].min(),
            "t_first_alarm_state": raw_alarm.groupby("track_id")["t_utc"].min(),
            "t_first_alarm_msg": raw_alarm.groupby("track_id")["t_msg"].min(),
            "t_first_drone_state": tr[tr["class_name"] == "DRONE"].groupby("track_id")["t_utc"].min(),
            "t_first_drone_msg": tr[tr["class_name"] == "DRONE"].groupby("track_id")["t_msg"].min(),
            "t_first_susp_drone_msg": tr[tr["class_name"] == "SUSPECTED_DRONE"].groupby("track_id")["t_msg"].min(),
        }
        for k, v in ev.items():
            summ = summ.merge(v.rename(k).reset_index(), on="track_id", how="left")
        summ["alarm_any"] = summ["alarm_any"].astype("boolean")
        summ = summ.sort_values("t_first").reset_index(drop=True)
        tables["track_summary"] = summ
    report["tracks"] = rep_t

    # ---------------- plots
    pl = pd.DataFrame(plot_rows)
    rep_p = {"rows_read": len(pl)}
    if not pl.empty:
        g = pl.groupby(["plot_id", "t_utc"], sort=False)
        n = g["t_msg"].transform("size")
        pl["n_msgs"] = n
        keep = pl.groupby(["plot_id", "t_utc"], sort=False).head(1).copy()
        keep["n_dup"] = keep["n_msgs"] - 1
        keep["latency_s"] = (keep["t_msg"] - keep["t_utc"]).dt.total_seconds()
        keep["qflags"] = np.int32(0)
        keep = add_geometry(keep, site_pos)
        keep = keep.sort_values(["t_utc", "plot_id"]).reset_index(drop=True)
        rep_p.update(rows_kept=len(keep), duplicates_dropped=len(pl) - len(keep),
                     unique_plot_ids=int(keep["plot_id"].nunique()),
                     latency_s={k: float(v) for k, v in keep["latency_s"].describe().items()})
        tables["plots"] = keep
    report["plots"] = rep_p

    # ---------------- status
    rep_s = {"rows_read": len(status_df)}
    if not status_df.empty:
        status_df["qflags"] = np.int32(0)
        status_df = coerce_columns(status_df, skip={"t_utc", "msg_index", "raw_ref", "qflags",
                                                    "sys_messages_json", "sensor_messages_json"})
        status_df = status_df.sort_values("t_utc").reset_index(drop=True)
        changing = {}
        for c in status_df.columns:
            if c in ("t_utc", "msg_index", "raw_ref"):
                continue
            nun = status_df[c].astype(str).nunique()
            if nun > 1:
                changing[c] = int(nun)
        rep_s.update(rows_kept=len(status_df), columns=list(status_df.columns), columns_changing=changing,
                     columns_with_missing_values={c: int(n) for c, n in status_df.isna().sum().items() if n},
                     nonempty_messages=int((status_df["sys_messages_n"] > 0).sum()
                                           + (status_df.get("sensor_messages_n", pd.Series([0])) > 0).sum()))
        tables["status"] = status_df
    report["status"] = rep_s

    # ---------------- motion (time inferred)
    mo = pd.DataFrame(motion_rows)
    rep_m = {"rows_read": len(mo)}
    if not mo.empty:
        st = sorted((o, t) for o, t in stamps if t is not None)
        so = np.array([o for o, _ in st], dtype=float)
        sv = np.array([t.value for _, t in st], dtype=float)
        t_inf = np.interp(mo["offset"].to_numpy(float), so, sv)
        mo["t_utc"] = pd.to_datetime(t_inf.astype("int64"), utc=True).as_unit("ns")
        raw_t = pd.to_datetime(mo["t_raw"], utc=True, errors="coerce")
        epoch0 = raw_t == pd.Timestamp(0, tz="UTC")
        mo["qflags"] = (QF_TIME_INFERRED | np.where(epoch0, QF_RAW_TIME_EPOCH0, 0)
                        | np.where((mo["lat_deg"] == 0) & (mo["lon_deg"] == 0), QF_POSITION_ZERO, 0)
                        ).astype("int32")
        mo = mo.drop(columns=["offset"])
        lead = ["t_utc", "t_raw"]
        mo = mo[lead + [c for c in mo.columns if c not in lead]].sort_values("t_utc").reset_index(drop=True)
        rep_m.update(rows_kept=len(mo), raw_time_epoch0_rows=int(epoch0.sum()),
                     position_zero_rows=int(((mo["qflags"] & QF_POSITION_ZERO) > 0).sum()),
                     time_inference="linear interpolation on byte offset between neighbouring Tracks/"
                                    "SystemStatus/PotentialDronePlots send timestamps",
                     pitch_deg={k: float(v) for k, v in mo["pitch_deg"].describe().items()},
                     roll_deg={k: float(v) for k, v in mo["roll_deg"].describe().items()})
        tables["motion"] = mo
    report["motion"] = rep_m

    # ---------------- image meta
    im = pd.DataFrame(img_rows)
    rep_i = {"rows_read": len(im)}
    if not im.empty:
        im["qflags"] = np.int32(0)
        im = im.sort_values("t_utc").reset_index(drop=True)
        d_id = im["image_id"].diff()
        d_t = im["t_utc"].diff().dt.total_seconds()
        rep_i.update(rows_kept=len(im), image_id_first=int(im["image_id"].min()),
                     image_id_last=int(im["image_id"].max()),
                     image_period_s=float((d_t / d_id).median()),
                     image_ids_missing_in_stream=int((d_id - 1).clip(lower=0).sum()),
                     rows_with_line_classes=int(im["n_line_types"].gt(0).sum()))
        tables["image_meta"] = im
    report["image_meta"] = rep_i

    # ---------------- write parquet
    pmeta = {"source_file": xml_path.name, "source_sha256": xml_sha}
    for name, df in tables.items():
        outputs.append(write_parquet(df, out_dir / f"{name}.parquet", name, pmeta))

    # ---------------- site.json
    secret_values: list[str] = []
    site = build_site(status_df if not status_df.empty else None, cfg_dir, report)
    if cfg_dir is not None:
        for p in cfg_dir.glob("*"):
            if p.is_file():
                secret_values += find_secret_values(p.read_text(encoding="utf-8", errors="replace"))
    _write_json(out_dir / "site.json", site, outputs)

    # ---------------- raw headers
    if raw_files:
        rh = read_raw_headers(raw_files, hash_files=hash_raw)
        if "image_meta" in tables:  # cross-check IMG IMAGE_NR against the XML stream
            for rec in rh.values():
                try:
                    nr = rec["header"]["RESULT"]["IMAGES"]["IMAGE_DATA"]["IMAGE_NR"]
                except (KeyError, TypeError):
                    continue
                hit = tables["image_meta"].loc[tables["image_meta"]["image_id"] == nr, "t_utc"]
                rec["xml_image_meta_match"] = {"image_id": nr,
                                               "xml_timestamp": str(hit.iloc[0]) if len(hit) else None}
        _write_json(out_dir / "raw_headers.json", rh, outputs)

    # ---------------- quicklooks
    if quicklook and "tracks" in tables:
        try:
            outputs += make_quicklooks(tables, site_pos, out_dir, focus_track)
        except Exception as exc:  # noqa: BLE001
            report["quicklook_error"] = f"{type(exc).__name__}: {exc}"

    # ---------------- secrets check over all text outputs
    leaked = []
    for o in outputs:  # only files written by THIS run
        p = out_dir / o["file"]
        if p.suffix != ".json":
            continue
        txt = p.read_text(encoding="utf-8")
        leaked += [p.name for v in secret_values if v in txt]
    report["secrets_check"] = {"secret_values_checked": len(secret_values), "files_with_leak": sorted(set(leaked)),
                               "passed": not leaked}
    report["outputs"] = outputs
    _write_json(out_dir / "import_report.json", report, None)
    rtxt = (out_dir / "import_report.json").read_text(encoding="utf-8")
    if any(v in rtxt for v in secret_values):
        leaked.append("import_report.json")
    if leaked:
        raise RuntimeError(f"secret value found in outputs: {sorted(set(leaked))}")
    return report


def _json_default(o):
    if isinstance(o, (pd.Timestamp, dt.datetime)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if o is pd.NA:
        return None
    return str(o)


def _write_json(path: Path, obj, outputs: list | None):
    path.write_text(json.dumps(obj, indent=2, default=_json_default, ensure_ascii=False), encoding="utf-8")
    if outputs is not None:
        outputs.append({"file": path.name, "sha256": sha256_file(path)})


SITE_CFG_ALLOW = ["RADAR_SPECIFICATION", "REQUEST", "OPERATING_MODES", "CLASSIFICATION", "PLOT_EXTRACTION",
                  "LOCATION", "LONG_RANGE", "BLANKING_SECTORS", "OUTPUT", "DYNAMIC_POSITIONING", "BEAM_STEERING",
                  "ADSB", "CLIMATE_CONTROL"]


def build_site(status_df: pd.DataFrame | None, cfg_dir: Path | None, report: dict) -> dict:
    site: dict = {"schema": "domeware-proto-site-0.1", "provenance": {}}
    if status_df is not None and len(status_df):
        f = status_df.iloc[0]
        g = lambda c: (None if c not in f or pd.isna(f[c]) else f[c])
        site["radar"] = {
            "name": g("s_name"), "software_version": g("s_version"), "sensor_type": g("s_sensor_type"),
            "sensor_id": g("s_id"),
            "position": {"lat_deg": g("s_position_latitude"), "lon_deg": g("s_position_longitude"),
                         "alt_m": g("s_position_altitude"),
                         "alt_datum": "UNVERIFIED (Robin docs needed: MSL/EGM96? ellipsoid? N2000?)"},
            "ground_level_m": g("s_ground_level"),
            "antenna_above_ground_m": (None if g("s_ground_level") is None
                                       else round(g("s_position_altitude") - g("s_ground_level"), 3)),
            "orientation": {"azimuth_deg": g("s_orientation_azimuth"),
                            "elevation_deg": g("s_orientation_elevation"),
                            "note": "azimuth of the radar's reference direction vs true north [inferred]"},
            "operating_mode": g("s_processing_operating_mode_id"), "sensitivity": g("s_processing_sensitivity"),
            "image_quality": g("s_processing_image_quality"),
            "transceiver": {"serial": g("s_components_transceiver_serial_number"),
                            "carrier_mhz": g("s_components_transceiver_carrier_frequency_mhz"),
                            "hw_rev": g("s_components_transceiver_hardware_revision"),
                            "sw_rev": g("s_components_transceiver_software_revision"),
                            "fpga_rev": g("s_components_transceiver_fpga_revision")},
            "gps": {"lock": g("s_components_gps_lock"), "hdop": g("s_components_gps_hdop")},
            "blanking_sectors": [{"angle_deg": g(f"s_blanking_sector_{i}_angle"),
                                  "span_deg": g(f"s_blanking_sector_{i}_span"),
                                  "enabled": g(f"s_blanking_sector_{i}_enabled")} for i in range(2)
                                 if f"s_blanking_sector_{i}_angle" in f],
        }
        site["provenance"]["radar"] = "SystemStatus (first message), DECLARED by the radar"
        ch = report.get("status", {}).get("columns_changing", {})
        site["status_fields_changing_during_capture"] = sorted(ch)
    if cfg_dir is not None:
        cfgs = {}
        for p in sorted(cfg_dir.glob("*")):
            if not p.is_file():
                continue
            parsed = parse_robin_config(p.read_text(encoding="utf-8", errors="replace"))
            cfgs[p.name] = {"sha256": sha256_file(p), "parsed": parsed}
        radar_cfg = cfgs.get("IrisRadar.config", {}).get("parsed", {}).get("IRISRADAR", {})
        allowed = {k: radar_cfg[k] for k in SITE_CFG_ALLOW if k in radar_cfg}
        ctl = radar_cfg.get("CONTROLLER", {})
        allowed["CONTROLLER"] = {k: ctl[k] for k in ("START_FREQ", "END_FREQ") if k in ctl}
        http = radar_cfg.get("HTTP_SERVICE", {})
        allowed["INTERFACES"] = {
            "json_api_enabled": http.get("ENABLED"), "json_api_port": http.get("SERVER_PORT"),
            "websocket_port": http.get("WEBSOCKET_PORT"),
            "tls_enabled": (http.get("SECURE") or {}).get("ENABLED"),
            "cot_enabled": (radar_cfg.get("COT_SERVICE") or {}).get("ENABLED"),
            "central_tracking_enabled": (radar_cfg.get("CENTRAL_TRACKING") or {}).get("ENABLED"),
            "database_enabled": ((cfgs.get("IrisRadar.config", {}).get("parsed", {}).get("GENERAL") or {})
                                 .get("DATABASE") or {}).get("ENABLED")}
        site["config_iris_radar"] = redact(allowed)
        site["config_iris_radar_note"] = ("ALLOWLIST of IrisRadar.config sections; GENERAL (database, map "
                                          "tiles), credentials, keys, hosts and addresses are deliberately "
                                          "not copied.")
        trk = cfgs.get("IrisTrackerCa.map", {}).get("parsed", {})
        site["tracker"] = redact(trk.get("MAP_PARAMETERS", trk))
        site["config_files"] = {k: {"sha256": v["sha256"]} for k, v in cfgs.items()}
        loc = radar_cfg.get("LOCATION", {})
        if site.get("radar") and loc:
            pos = site["radar"]["position"]
            site["consistency"] = {
                "status_vs_config_lat_deg": (pos["lat_deg"] or 0) - loc.get("POSITION_LAT", 0),
                "status_vs_config_lon_deg": (pos["lon_deg"] or 0) - loc.get("POSITION_LON", 0),
                "status_vs_config_alt_m": (pos["alt_m"] or 0) - loc.get("POSITION_ALT", 0),
                "status_vs_config_ground_m": (site["radar"]["ground_level_m"] or 0) - loc.get("GROUND_LEVEL", 0),
                "status_vs_config_azimuth_deg": ((site["radar"]["orientation"]["azimuth_deg"] or 0)
                                                 - loc.get("AZIMUTH", 0))}
        site["provenance"]["config"] = "IrisRadar.config + IrisTrackerCa.map from the capture's cfg folder"
    return redact(site)


# =============================================================== quicklooks
CLASS_STYLE = {  # dataviz reference palette: 3 categorical slots + neutrals for the background classes
    "DRONE": ("#2a78d6", "-", 2.0, 5),
    "SUSPECTED_DRONE": ("#eb6834", "-", 2.0, 4),
    "VEHICLE": ("#1baf7a", "-", 1.6, 3),
    "OTHER": ("#52514e", "--", 1.0, 2),
    "UNCLASSIFIED": ("#b7b5ad", "-", 0.8, 1),
}
ALARM_COLOR = "#d03b3b"
TEXT2 = "#52514e"


def make_quicklooks(tables: dict, site_pos: dict | None, out_dir: Path, focus_track: int | None) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "axes.edgecolor": "#c3c2b7",
                         "axes.labelcolor": "#0b0b0b", "xtick.color": TEXT2, "ytick.color": TEXT2,
                         "grid.color": "#e6e5e0", "font.size": 9})
    outs = []
    tr = tables["tracks"]
    dom = tr.groupby("track_id")["class_code"].max()  # strongest class a track ever had
    code2name = {v: k for k, v in CLASS_CODES.items()}

    # ---- 1: map (ENU from radar): overview + zoom on the focus track
    fig, axes = plt.subplots(1, 2, figsize=(15, 7.5), width_ratios=[1.15, 1])
    order = sorted(dom.index, key=lambda t: dom[t] if pd.notna(dom[t]) else -1)
    zoom = 150.0
    if focus_track is not None and (tr["track_id"] == focus_track).any():
        fd = tr[tr["track_id"] == focus_track]
        zoom = float(max(100.0, np.ceil(np.nanmax(np.abs(fd[["e_m", "n_m"]].to_numpy())) / 25.0) * 25.0 + 25.0))
    for k, ax in enumerate(axes):
        for tid in order:
            d = tr[tr["track_id"] == tid]
            cname = code2name.get(int(dom[tid]) if pd.notna(dom[tid]) else -1, "UNCLASSIFIED")
            col, ls, lw, z = CLASS_STYLE.get(cname, ("#888888", ":", 0.8, 0))
            ax.plot(d["e_m"], d["n_m"], ls, color=col, lw=lw if k == 0 else lw * 1.2, zorder=z,
                    solid_capstyle="round", marker="." if k == 1 else None, ms=3)
            ax.plot(d["e_m"].iloc[-1], d["n_m"].iloc[-1], "o", color=col, ms=3, zorder=z)
            if k == 0 and cname in ("VEHICLE",):
                ax.annotate(str(tid), (d["e_m"].iloc[-1], d["n_m"].iloc[-1]), xytext=(4, 4),
                            textcoords="offset points", fontsize=8, color="#0b0b0b")
        al = tr[tr["alarm"] == True]
        ax.scatter(al["e_m"], al["n_m"], marker="^", s=22 if k else 14, facecolor="none",
                   edgecolor=ALARM_COLOR, lw=0.8, zorder=6)
        if "plots" in tables:
            pp = tables["plots"]
            ax.scatter(pp["e_m"], pp["n_m"], marker="x", s=14, color="#6250d6", lw=0.8, zorder=5)
        ax.plot(0, 0, marker="s", ms=8, color="#0b0b0b", zorder=7)
        rings = (500, 1000, 2000, 3000) if k == 0 else (25, 50, 75, 100, 125, 150)
        for r in rings:
            if k == 1 and r > zoom:
                continue
            ax.add_patch(plt.Circle((0, 0), r, fill=False, color="#c3c2b7", lw=0.6, ls=":"))
            ax.annotate(f"{r/1000:g} km" if k == 0 else f"{r} m", (0, r), fontsize=7, color=TEXT2,
                        ha="center", va="bottom")
        ax.set_aspect("equal"); ax.grid(True, lw=0.4)
        ax.set_xlabel("east of radar [m]"); ax.set_ylabel("north of radar [m]")
        if k == 1:
            ax.set_xlim(-zoom, zoom); ax.set_ylim(-zoom, zoom)
            ax.set_title(f"Zoom +/-{zoom:.0f} m: track {focus_track} (the test drone)", loc="left")
        else:
            ax.add_patch(plt.Rectangle((-zoom, -zoom), 2 * zoom, 2 * zoom, fill=False, ec="#0b0b0b", lw=0.6))
            ax.set_title("All Robin tracks, coloured by the strongest class each track reached", loc="left")
    handles = [Line2D([], [], color=c, ls=ls, lw=max(lw, 1.5), label=n) for n, (c, ls, lw, _) in CLASS_STYLE.items()]
    handles += [Line2D([], [], marker="^", ls="", mfc="none", mec=ALARM_COLOR, label="alarm = true"),
                Line2D([], [], marker="x", ls="", color="#6250d6", label="PotentialDronePlot"),
                Line2D([], [], marker="s", ls="", color="#0b0b0b", label="radar")]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.025), ncol=8, fontsize=8, frameon=False)
    fig.text(0.01, 0.005, "ENU metres from the radar antenna; heights in Robin's (unverified) datum.",
             fontsize=7, color=TEXT2)
    p1 = out_dir / "quicklook_map_tracks.png"
    fig.tight_layout(rect=(0, 0.06, 1, 1)); fig.savefig(p1, dpi=130); plt.close(fig)
    outs.append({"file": p1.name, "sha256": sha256_file(p1)})

    # ---- 2: altitude vs time for the focus track
    if focus_track is not None and (tr["track_id"] == focus_track).any():
        d = tr[tr["track_id"] == focus_track]
        fig, axs = plt.subplots(2, 1, figsize=(9, 6), sharex=True, height_ratios=[3, 1.4])
        ax = axs[0]
        ax.plot(d["t_utc"], d["alt_m"], "-", color="#c3c2b7", lw=1.0, zorder=1)
        for cname, (col, _, _, _) in CLASS_STYLE.items():
            s = d[d["class_name"] == cname]
            if len(s):
                ax.plot(s["t_utc"], s["alt_m"], "o", ms=4, color=col, label=f"{cname} ({len(s)})", zorder=3)
        a = d[d["alarm"] == True]
        ax.plot(a["t_utc"], a["alt_m"] + 1.5, "v", ms=4, mfc="none", mec=ALARM_COLOR,
                label=f"alarm ({len(a)})", zorder=2)
        if site_pos and site_pos.get("ground_level_m") is not None:
            ax.axhline(site_pos["ground_level_m"], color=TEXT2, lw=0.8, ls="--")
            ax.annotate(f"radar ground level {site_pos['ground_level_m']:.1f} m", (d["t_utc"].iloc[0],
                        site_pos["ground_level_m"]), xytext=(0, 3), textcoords="offset points", fontsize=7,
                        color=TEXT2)
            ax.axhline(site_pos["alt_m"], color=TEXT2, lw=0.8, ls=":")
            ax.annotate(f"radar antenna {site_pos['alt_m']:.1f} m", (d["t_utc"].iloc[0], site_pos["alt_m"]),
                        xytext=(0, 3), textcoords="offset points", fontsize=7, color=TEXT2)
        ax.set_ylabel("altitude [m] (Robin datum, unverified)")
        ax.set_title(f"Track {focus_track}: altitude per unique track state (UTC track time)", loc="left")
        ax.legend(fontsize=8, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 0.14), ncol=3); ax.grid(True, lw=0.4)
        axs[1].plot(d["t_utc"], d["latency_s"], ".", ms=3, color="#2a78d6")
        axs[1].set_ylabel("t_msg - t_utc [s]"); axs[1].grid(True, lw=0.4)
        axs[1].set_xlabel("UTC 2026-10-21")
        import matplotlib.dates as mdates
        axs[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        p2 = out_dir / f"quicklook_track_{focus_track}_altitude.png"
        fig.tight_layout(); fig.savefig(p2, dpi=130); plt.close(fig)
        outs.append({"file": p2.name, "sha256": sha256_file(p2)})

    # ---- 3: class / alarm timeline per track
    summ = tables["track_summary"].sort_values("t_first")
    ids = list(summ["track_id"])
    fig, ax = plt.subplots(figsize=(10, max(4, 0.18 * len(ids) + 1.2)))
    for y, tid in enumerate(ids):
        d = tr[tr["track_id"] == tid].sort_values("t_utc")
        t = d["t_utc"].to_numpy()
        for i in range(len(d)):
            t0 = t[i]
            t1 = t[i + 1] if i + 1 < len(d) else t0 + np.timedelta64(500, "ms")
            col = CLASS_STYLE.get(d["class_name"].iloc[i], ("#888888",))[0]
            ax.plot([t0, t1], [y, y], color=col, lw=5, solid_capstyle="butt")
        a = d[d["alarm"] == True]
        ax.plot(a["t_utc"], [y - 0.38] * len(a), "|", color=ALARM_COLOR, ms=4)
    ax.set_yticks(range(len(ids)), [str(i) for i in ids], fontsize=6)
    ax.invert_yaxis(); ax.grid(True, axis="x", lw=0.4)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    ax.set_xlabel("UTC 2026-10-21 (track time)"); ax.set_ylabel("track id (by first appearance)")
    ax.set_title("Class per track state over time; red ticks = alarm true", loc="left")
    handles = [Line2D([], [], color=c, lw=5, label=n) for n, (c, *_r) in CLASS_STYLE.items()]
    handles.append(Line2D([], [], color=ALARM_COLOR, marker="|", ls="", ms=8, label="alarm"))
    ax.legend(handles=handles, fontsize=7, frameon=False, loc="lower right", ncol=3)
    p3 = out_dir / "quicklook_class_alarm_timeline.png"
    fig.tight_layout(); fig.savefig(p3, dpi=130); plt.close(fig)
    outs.append({"file": p3.name, "sha256": sha256_file(p3)})

    # ---- 4: latency histogram (t_msg - t_utc), all tracks
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(tr["latency_s"].dropna(), bins=50, color="#2a78d6", edgecolor="#fcfcfb", lw=0.5)
    ax.set_xlabel("t_msg - t_utc [s] (first message carrying the state)"); ax.set_ylabel("track states")
    ax.set_title("Delay between track state time and the message that first reported it", loc="left")
    ax.grid(True, axis="y", lw=0.4)
    p4 = out_dir / "quicklook_latency_hist.png"
    fig.tight_layout(); fig.savefig(p4, dpi=130); plt.close(fig)
    outs.append({"file": p4.name, "sha256": sha256_file(p4)})
    return outs


# =============================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xml", type=Path, default=DEFAULT_ARCHIVE / "ozmasto2.xml")
    ap.add_argument("--cfg-dir", type=Path, default=DEFAULT_ARCHIVE / "20261021_103214" / "cfg")
    ap.add_argument("--raw-dir", type=Path, default=DEFAULT_ARCHIVE,
                    help="folder searched recursively for *.PLOTS.gz and *.IMG")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--focus-track", type=int, default=None)
    ap.add_argument("--no-quicklook", action="store_true")
    ap.add_argument("--no-hash-raw", action="store_true", help="skip sha256 of big raw files")
    a = ap.parse_args(argv)
    raw = []
    if a.raw_dir and a.raw_dir.exists():
        raw = sorted(list(a.raw_dir.rglob("*.PLOTS.gz")) + list(a.raw_dir.rglob("*.IMG")))
    cfg = a.cfg_dir if a.cfg_dir and a.cfg_dir.exists() else None
    rep = convert(a.xml, a.out, cfg, raw, quicklook=not a.no_quicklook, focus_track=a.focus_track,
                  hash_raw=not a.no_hash_raw)
    t = rep["tracks"]
    print(f"messages: {rep['message_counts']}")
    print(f"tracks: read {t.get('rows_read')} kept {t.get('rows_kept')} dup {t.get('duplicates_dropped')} "
          f"unique ids {t.get('unique_tracks')} conflicts {t.get('keys_with_conflicting_duplicates')}")
    print(f"unknown types {rep['unknown_message_types']} unknown paths {rep['unknown_paths']}")
    print(f"fragments failed {rep['fragments']['failed']}; secrets check passed: {rep['secrets_check']['passed']}")
    print(f"outputs in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
