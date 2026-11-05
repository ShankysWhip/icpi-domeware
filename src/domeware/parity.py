"""Do both apps read a Robin XML file the same way?

    uv run python -m domeware.parity [file.xml] [--out report.md]

Runs domeware's reader (adapters/robin_xml.py) and Orbit's (app/web/js/radar.js under Node, through
app/tests/dump_robin.js) on the same file and compares every track update and potential-drone plot. Without a file it
uses Orbit's synthetic radar fixture with its track names turned into numbers first: domeware's reader takes only
numeric track ids, as real Robin files have them (a known difference, combine/README.md). Exit code 1 when they differ.

The same pattern (one dump per app, one comparison here) checks the other shared parts as they are combined: the DJI
and ArduPilot readers, association and coverage, clock sync, the CWA scores.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from domeware.adapters import robin_xml
from domeware.compare import _ns

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"
SYNTHETIC = APP / "tests" / "fixtures" / "SYNTHETIC_fleet_radar.xml"
POS_TOL_M = 0.01  # same update read to within 1 cm, horizontally and in height


def numeric_ids(text: str) -> tuple[str, dict[str, int]]:
    """Give every non-numeric <Track id> a number (900001, 900002, ... by first appearance), as real files have."""
    names: dict[str, int] = {}

    def sub(m: re.Match) -> str:
        tid = m.group(1)
        if tid.isdigit():
            return m.group(0)
        names.setdefault(tid, 900001 + len(names))
        return f'<Track id="{names[tid]}"'

    return re.sub(r'<Track id="([^"]+)"', sub, text), names


def domeware_side(xml: Path) -> dict:
    """domeware's reader, as `dw convert` runs it, without quicklooks or raw hashing."""
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        out = Path(d)
        robin_xml.convert(xml, out, quicklook=False, hash_raw=False)
        tr = pd.read_parquet(out / "tracks.parquet")
        pl = pd.read_parquet(out / "plots.parquet") if (out / "plots.parquet").exists() else pd.DataFrame()
    ms = _ns(tr["t_utc"]) // 1_000_000
    updates, no_pos = {}, 0
    for (tid, lat, lon, alt, cls, alarm), t in zip(
            tr[["track_id", "lat_deg", "lon_deg", "alt_m", "class_name", "alarm"]].itertuples(index=False), ms, strict=True):
        if pd.isna(lat) or pd.isna(lon):
            no_pos += 1
            continue
        updates[(str(tid), int(t))] = (float(lat), float(lon), None if pd.isna(alt) else float(alt),
                                       (cls or "UNCLASSIFIED").strip().upper(), bool(alarm) if not pd.isna(alarm) else False)
    plots = []
    if len(pl):
        pms = _ns(pl["t_utc"]) // 1_000_000
        plots = sorted((int(t), float(a), float(b), None if pd.isna(c) else float(c))
                       for t, a, b, c in zip(pms, pl["lat_deg"], pl["lon_deg"], pl["alt_m"], strict=True))
    return {"updates": updates, "plots": plots, "no_position": no_pos, "tracks": tr["track_id"].nunique()}


def orbit_side(xml: Path) -> dict:
    """Orbit's reader under Node (app/tests/dump_robin.js), the same code the app runs."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node is needed to run Orbit's reader (https://nodejs.org)")
    r = subprocess.run([node, str(APP / "tests" / "dump_robin.js"), str(xml)], capture_output=True, text=True,
                       encoding="utf-8", check=False)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-2000:])
    d = json.loads(r.stdout)
    updates = {}
    for tr in d["tracks"]:
        for t, lat, lon, alt, cls, alarm in tr["updates"]:
            updates[(str(tr["id"]), int(round(t)))] = (lat, lon, alt, cls, bool(alarm))
    plots = sorted((int(round(t)), a, b, c) for t, a, b, c in d["plots"])
    return {"updates": updates, "plots": plots, "tracks": len(d["tracks"]), "messages": d["messages"]}


def _metres(a: tuple, b: tuple) -> tuple[float, float]:
    """Horizontal and vertical distance between two (lat, lon, alt, ...) readings."""
    dn = (a[0] - b[0]) * 111_320.0
    de = (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    if a[2] is None or b[2] is None:  # a missing height matches only another missing height
        dv = 0.0 if a[2] is None and b[2] is None else math.inf
    else:
        dv = abs(a[2] - b[2])
    return math.hypot(dn, de), dv


def _utc(ms: int | None) -> str:
    return "-" if ms is None else pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def robin(xml: Path | None = None, map_ids: bool | None = None) -> dict:
    """Run both readers on one file. Returns the verdict, a Markdown report and the numbers behind it."""
    src = Path(xml) if xml else SYNTHETIC
    text = src.read_bytes().decode("utf-8")
    mapping: dict[str, int] = {}
    if map_ids if map_ids is not None else xml is None:
        text, mapping = numeric_ids(text)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / src.name
        f.write_bytes(text.encode("utf-8"))
        sides, failed = {}, {}
        for name, fn in (("domeware", domeware_side), ("Orbit", orbit_side)):
            try:
                sides[name] = fn(f)
            except Exception as e:  # noqa: BLE001 - a reader that fails is a finding, reported in full
                failed[name] = f"{type(e).__name__}: {e}"
    lines = [f"# Robin reader parity: {src.name}", "",
             f"sha256 {hashlib.sha256(src.read_bytes()).hexdigest()[:16]}"
             + (f"; track names numbered first: {mapping}" if mapping else ""), ""]
    if failed:
        lines += [f"- **{k}'s reader failed:** {v}" for k, v in failed.items()]
        return {"verdict": "DIFFERENT", "report": "\n".join(lines + ["", "Verdict: **DIFFERENT**"]), "numbers": {}}
    dw, ob = sides["domeware"], sides["Orbit"]
    common = dw["updates"].keys() & ob["updates"].keys()
    worst_h = worst_v = 0.0
    cls_diff = alarm_diff = 0
    for k in common:
        h, v = _metres(dw["updates"][k], ob["updates"][k])
        worst_h, worst_v = max(worst_h, h), max(worst_v, v)
        cls_diff += dw["updates"][k][3] != ob["updates"][k][3]
        alarm_diff += dw["updates"][k][4] != ob["updates"][k][4]
    plots_same = len(dw["plots"]) == len(ob["plots"]) and all(
        a[0] == b[0] and max(_metres(a[1:], b[1:])) <= POS_TOL_M for a, b in zip(dw["plots"], ob["plots"], strict=False))
    n = {"tracks": (dw["tracks"], ob["tracks"]), "updates": (len(dw["updates"]), len(ob["updates"])),
         "updates_same": len(common), "only_domeware": len(dw["updates"].keys() - common),
         "only_orbit": len(ob["updates"].keys() - common), "plots": (len(dw["plots"]), len(ob["plots"])),
         "worst_horizontal_m": round(worst_h, 4), "worst_vertical_m": round(worst_v, 4),
         "class_differs": cls_diff, "alarm_differs": alarm_diff, "no_position_domeware": dw["no_position"]}
    same = (n["only_domeware"] == 0 and n["only_orbit"] == 0 and worst_h <= POS_TOL_M and worst_v <= POS_TOL_M
            and cls_diff == 0 and alarm_diff == 0 and plots_same)
    t_dw, t_ob = [k[1] for k in dw["updates"]], [k[1] for k in ob["updates"]]
    lines += ["| | domeware | Orbit |", "|---|---|---|",
              f"| tracks | {n['tracks'][0]} | {n['tracks'][1]} |",
              f"| track updates with a position | {n['updates'][0]} | {n['updates'][1]} |",
              f"| potential-drone plots | {n['plots'][0]} | {n['plots'][1]} |",
              f"| first update (UTC) | {_utc(min(t_dw, default=None))} | {_utc(min(t_ob, default=None))} |",
              f"| last update (UTC) | {_utc(max(t_dw, default=None))} | {_utc(max(t_ob, default=None))} |", "",
              f"- Same updates (track id and time): {n['updates_same']}; only in domeware: {n['only_domeware']}; "
              f"only in Orbit: {n['only_orbit']}",
              f"- Largest difference on the same update: {n['worst_horizontal_m']} m horizontally, "
              f"{n['worst_vertical_m']} m in height (allowed {POS_TOL_M} m)",
              f"- Class differs on {cls_diff} updates, alarm on {alarm_diff}; plots {'match' if plots_same else 'DIFFER'}",
              f"- Updates without a position: {dw['no_position']} in domeware's table (Orbit drops them on reading)", "",
              f"Verdict: **{'IDENTICAL' if same else 'DIFFERENT'}**"]
    return {"verdict": "IDENTICAL" if same else "DIFFERENT", "report": "\n".join(lines), "numbers": n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xml", nargs="?", type=Path, help="a Robin XML file (default: Orbit's synthetic fixture)")
    ap.add_argument("--out", type=Path, help="also write the report here (Markdown)")
    a = ap.parse_args(argv)
    r = robin(a.xml)
    print(r["report"])
    if a.out:
        a.out.write_text(r["report"] + "\n", encoding="utf-8")
    return 0 if r["verdict"] == "IDENTICAL" else 1


if __name__ == "__main__":
    sys.exit(main())
