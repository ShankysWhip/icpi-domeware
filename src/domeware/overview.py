"""Team-level helpers: `dw list` (all sessions in one table + index.html) and `dw doctor` (is this laptop ready?)."""

from __future__ import annotations

import html
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path.home() / "Work" / "domeware-data" / "sessions"


def session_row(root: Path) -> dict:
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    site = m.get("site") or {}
    runs = pd.read_csv(root / "runs.csv", dtype=str) if (root / "runs.csv").exists() else pd.DataFrame(columns=["run_id", "part"])
    cmp_p = root / "views" / "compare.json"
    cmp_ = json.loads(cmp_p.read_text(encoding="utf-8")) if cmp_p.exists() else []
    delays = [r["delay_airborne_to_first_track_s"] for r in cmp_ if r.get("delay_airborne_to_first_track_s") is not None]
    errs = [r["error_horizontal_m"]["median"] for r in cmp_ if r.get("error_horizontal_m")]
    first = runs[runs["part"] == "RUN"]["start_utc"].min() if "start_utc" in runs and len(runs) else None
    return {"session": root.name, "date": (first or m.get("created_utc") or "")[:10], "site": site.get("name") or "",
            "runs": int((runs["part"] == "RUN").sum()) if len(runs) else 0,
            "drones": sum(1 for k in m["streams"] if k.startswith("truth/")) + sum(1 for k in m["streams"] if k.endswith("_osd")),
            "raw_files": len(m["raw"]),
            "first_track_delay_s": min(delays) if delays else None,
            "horiz_error_median_m": max(errs) if errs else None,
            "scorecards": len((m.get("scorecard") or {}).get("runs", [])),
            "report": (root / "views" / "report.md").exists(), "replay": (root / "views" / "replay.rrd").exists()}


def list_sessions(root: Path = DEFAULT_ROOT) -> pd.DataFrame:
    rows = []
    for d in sorted(p for p in Path(root).iterdir() if (p / "manifest.json").exists()):
        try:
            rows.append(session_row(d))
        except Exception as e:  # one broken session must not hide the others
            rows.append({"session": d.name, "site": f"unreadable: {e}"})
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["date", "session"], ascending=False, na_position="last")
        _index_html(Path(root), df)
    return df


def _index_html(root: Path, df: pd.DataFrame) -> None:
    def link(r, name, path):
        return f'<a href="{html.escape(r["session"])}/{path}">{name}</a>' if r.get(name.split()[0].lower()) else ""
    head = ["Session", "Date", "Site", "Runs", "Drones", "Raw files", "First track (s)", "Horiz. error (m)", "Open"]
    rows = "".join(
        f"<tr><td>{html.escape(str(r['session']))}</td><td>{r.get('date', '')}</td><td>{html.escape(str(r.get('site', '')))}</td>"
        f"<td>{r.get('runs', '')}</td><td>{r.get('drones', '')}</td><td>{r.get('raw_files', '')}</td>"
        f"<td>{'' if pd.isna(r.get('first_track_delay_s')) else r.get('first_track_delay_s')}</td>"
        f"<td>{'' if pd.isna(r.get('horiz_error_median_m')) else r.get('horiz_error_median_m')}</td>"
        f"<td>{link(r, 'report', 'views/report.md')} {link(r, 'replay file', 'views/replay.rrd')}</td></tr>"
        for r in df.to_dict("records"))
    (root / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>domeware sessions</title><style>body{font:14px system-ui;margin:24px}"
        "table{border-collapse:collapse}td,th{padding:6px 10px;border-bottom:1px solid #ccc;text-align:left}"
        "th{background:#eee}</style><h1>domeware sessions</h1><p>Open a replay with <code>uv run dw view &lt;session&gt;</code>."
        f"</p><table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{rows}</table>", encoding="utf-8")


def doctor() -> int:
    """Check what a co-worker's laptop needs; print a fix for everything missing. Changes nothing."""
    from domeware.adapters.truth import PROJ_DIR

    fixes = 0

    def check(ok: bool, what: str, fix: str = ""):
        nonlocal fixes
        print(f"  {'OK ' if ok else 'FIX'}  {what}" + ("" if ok else f"\n        -> {fix}"))
        fixes += not ok

    print("domeware doctor")
    check(sys.version_info >= (3, 13), f"Python {sys.version.split()[0]}", "install Python 3.13 with: uv python install 3.13")
    for mod in ("pandas", "pyarrow", "pyproj", "rasterio", "rerun", "pymavlink", "trimesh"):
        try:
            __import__(mod)
            check(True, f"package {mod}")
        except ImportError:
            check(False, f"package {mod}", "run: uv sync")
    grids = ["fi_nls_fin2023n2000.tif"]
    for g in grids:
        check((PROJ_DIR / g).exists(), f"height grid {g}",
              "run: uv run dw setup (or copy domeware-vendor from a co-worker); "
              "without it heights are ~18 m off")
    check(date.today() < date(2027, 6, 28), "GPS-UTC leap second table (valid to 2027-06-28)",
          "update GPS_UTC_LEAP_S in adapters/truth.py from IERS Bulletin C")
    exe = Path(sys.executable).with_name("rerun.exe" if sys.platform == "win32" else "rerun")
    check(exe.exists(), "Rerun viewer", "run: uv sync (rerun-sdk ships the viewer)")
    import shutil

    print(f"  {'OK ' if shutil.which('ffmpeg') else '-- '}  ffmpeg on PATH (optional: only for H.264/H.265 video in the replay viewer)")
    data = Path.home() / "Work" / "domeware-data"
    check(data.exists(), f"data folder {data}", f"create it: mkdir {data}")
    print("All good." if not fixes else f"{fixes} thing(s) to fix.")
    return 1 if fixes else 0


VENDOR = Path.home() / "Work" / "domeware-vendor"
# Files domeware needs offline. Hashes pinned on 23 Oct 2026; a different file is refused, never used.
VENDOR_FILES = [
    ("proj/fi_nls_fin2023n2000.tif", "https://cdn.proj.org/fi_nls_fin2023n2000.tif",
     "82ce62ed36910a61fc79010702b3e48fdb45cdfc90a248114ac90a7cb5e30cf7"),
    ("proj/eur_nkg_nkgrf17vel.tif", "https://cdn.proj.org/eur_nkg_nkgrf17vel.tif",
     "515f68aeb59f659a3f3c340626cd060f58de6f93b4546df67732b906949e0d66"),
    ("time/leap-seconds.list", "https://data.iana.org/time-zones/tzdb/leap-seconds.list",
     "db5a895f16853b03bfc865e8d68f9fc8710ef1740e3400c701cd46a5bbbc3433"),
]


def setup(vendor: Path = VENDOR) -> int:
    """Fetch the height grids and the leap-second list into domeware-vendor, check their hashes, then run doctor."""
    import hashlib

    import requests

    bad = 0
    for rel, url, sha in VENDOR_FILES:
        dst = vendor / rel
        if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest() == sha:
            print(f"  OK   {rel} (already there)")
            continue
        if rel.startswith("time/") and dst.exists():
            print(f"  OK   {rel} (kept: a newer IANA list replaces the pinned one)")
            continue
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  FIX  {rel}: download failed ({e}); copy domeware-vendor from a co-worker instead")
            bad += 1
            continue
        got = hashlib.sha256(r.content).hexdigest()
        if got != sha and not rel.startswith("time/"):
            print(f"  FIX  {rel}: the server sent a different file (sha256 {got[:12]}...); not saved")
            bad += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(r.content)
        print(f"  OK   {rel} downloaded ({len(r.content) / 1e6:.1f} MB)")
    if vendor == VENDOR:
        return doctor() or (1 if bad else 0)
    return 1 if bad else 0
