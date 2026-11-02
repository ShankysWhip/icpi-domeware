"""dw survey: what is in a data drop, read in place. Nothing is copied, changed or written.

  dw survey D:\\robin_export     every file: what it is, how big, which UTC times it covers where that is cheap to read

Files are recognised as dw ingest recognises them, from the head of each file only, so even a drive of hundreds of GB
is surveyed in minutes. Raw radar recordings (.img, .plots) are listed but not needed: domeware reads the tracks,
plots and status from the radar's XML. The end says what to ingest and what to leave on the drive.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

from domeware.session import detect_role

TS = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)")  # ISO times with a zone only
EDGE = 1 << 16  # bytes read at each end of a file for its first and last time
WHAT = {
    "robin_xml": "radar tracks, plots and status (Robin XML)",
    "robin_cfg": "radar settings",
    "robin_raw": "raw radar recording: not needed, the tracks come from the XML",
    "dji_flightrecord_encrypted": "DJI flight log (encrypted: needs FlightReader CSV or the DJI key)",
    "dji_csv": "DJI flight log (CSV)",
    "dji_srt": "DJI video captions with positions",
    "ardupilot_bin": "ArduPilot flight log",
    "mavlink_tlog": "MAVLink telemetry log",
    "rtklib_pos": "RTK positions (RTKLIB)",
    "skybrush_skyc": "Skybrush show",
    "vaisala_wxt": "weather station (Vaisala)",
    "video": "video",
    "image": "pictures and screenshots",
    "archive": "archive: unpack it first, then survey again",
    "unknown": "not recognised: kept out",
}
NOT_NEEDED = {"robin_raw", "archive", "unknown"}


def _size(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _span(path: Path) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """First and last zoned ISO time in the file's first and last 64 kB (Robin XML stamps every message)."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(EDGE)
        fh.seek(max(0, size - EDGE))
        tail = fh.read()
    times, latest = [], pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    for raw in TS.findall(head) + TS.findall(tail):
        try:
            t = pd.Timestamp(raw.decode()).tz_convert("UTC")
        except ValueError:
            continue
        if pd.Timestamp("2000-01-01", tz="UTC") <= t <= latest:  # an unset field reads 1970-01-01: not a real time
            times.append(t)
    return (min(times), max(times)) if times else None


def survey(folder: Path) -> dict:
    """Every file under `folder`, grouped by what it is. Read only; nothing is written anywhere."""
    folder = Path(folder)
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    groups: dict[str, dict] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = Path(root) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            role = "archive" if name.lower().endswith((".zip", ".7z", ".rar", ".tar", ".tgz")) else detect_role(p)
            g = groups.setdefault(role, {"files": [], "bytes": 0, "first": None, "last": None})
            g["files"].append(p)
            g["bytes"] += size
            if role == "robin_xml":
                span = _span(p)
                if span:
                    g["first"] = span[0] if g["first"] is None else min(g["first"], span[0])
                    g["last"] = span[1] if g["last"] is None else max(g["last"], span[1])
    return {"folder": folder, "groups": groups}


def report(folder: Path) -> str:
    r = survey(folder)
    groups = r["groups"]
    n = sum(len(g["files"]) for g in groups.values())
    total = sum(g["bytes"] for g in groups.values())
    lines = [f"Survey of {r['folder']}: {n} files, {_size(total)}. Read in place: nothing copied or changed.", ""]
    for role, g in sorted(groups.items(), key=lambda kv: (kv[0] in NOT_NEEDED, -kv[1]["bytes"])):
        when = f"   {g['first']:%Y-%m-%d %H:%M:%S} -> {g['last']:%H:%M:%S} UTC" if g["first"] is not None else ""
        lines.append(f"  {len(g['files']):6d}  {_size(g['bytes']):>9}  {role:28s} {WHAT.get(role, role)}{when}")
        if role in ("unknown", "archive"):
            lines.append("            " + ", ".join(p.name for p in g["files"][:8]) + (" ..." if len(g["files"]) > 8 else ""))
    use = {k: g for k, g in groups.items() if k not in NOT_NEEDED}
    leave = {k: g for k, g in groups.items() if k == "robin_raw"}
    lines.append("")
    if use:
        lines.append(f"To ingest: {sum(len(g['files']) for g in use.values())} files, "
                     f"{_size(sum(g['bytes'] for g in use.values()))}  (uv run dw ingest <session> <the files or folders>)")
    if leave:
        lines.append(f"Leave on the drive: {len(leave['robin_raw']['files'])} raw radar recordings, "
                     f"{_size(leave['robin_raw']['bytes'])}")
    if "archive" in groups:
        lines.append("Archives: unpack them first, then survey again.")
    return "\n".join(lines)
