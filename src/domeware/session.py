"""A session = one test day/run folder. Raw files are copied in, hashed and never changed.

Layout:
  <session>/manifest.json   machine-written record of everything in the session
  <session>/raw/<role>/...  original files, read-only, sha256 in the manifest
  <session>/streams/<src>/  converted Parquet (regenerable from raw)
  <session>/env/            terrain / imagery for the site (regenerable)
  <session>/views/          replay files and quicklook images (regenerable)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "domeware-session-0.1"


def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def detect_role(path: Path) -> str:
    """Decide what a file is from its name and first bytes. Unknown files are still kept."""
    name = path.name.lower()
    try:
        head = path.read_bytes()[:4096] if not name.endswith(".gz") else gzip.open(path).read(4096)
    except OSError:
        head = b""
    if name.endswith(".xml") and b"<Robin" in head:
        return "robin_xml"
    if name in ("irisradar.config", "iristrackerca.map") or (name.endswith((".config", ".map")) and b"IRISRADAR" in head):
        return "robin_cfg"
    if b"ROBIN RADAR SYSTEMS" in head and (".plots" in name or name.endswith(".img")):
        return "robin_raw"
    if name.startswith("flightrecord") and name.endswith(".txt") and len(head) > 11 and head[10] >= 13:
        return "dji_flightrecord_encrypted"
    if name.endswith(".csv") and (b"OSD.latitude" in head or b"OSD.longitude" in head):
        return "dji_csv"
    if name.endswith(".srt") and (b"latitude" in head.lower() or b"longtitude" in head.lower()):
        return "dji_srt"
    if name.endswith(".bin") and head[:2] == b"\xa3\x95":
        return "ardupilot_bin"
    if name.endswith(".skyc"):
        return "skybrush_skyc"
    if name.endswith(".tlog"):
        return "mavlink_tlog"
    if name.endswith(".pos"):
        return "rtklib_pos"
    return "unknown"


class Session:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.raw = self.root / "raw"
        self.streams = self.root / "streams"
        self.env = self.root / "env"
        self.views = self.root / "views"
        self.manifest_path = self.root / "manifest.json"
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            self.manifest = {"schema": SCHEMA, "session_id": str(uuid.uuid4()), "created_utc": now_utc(),
                             "raw": [], "streams": {}, "site": None, "env": None, "log": []}

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.manifest_path)  # atomic: a crash never leaves half a manifest

    def note(self, msg: str) -> None:
        self.manifest["log"].append({"utc": now_utc(), "msg": msg})
        print(msg)

    def raw_files(self, role: str | None = None) -> list[Path]:
        return [self.root / r["path"] for r in self.manifest["raw"] if role is None or r["role"] == role]

    def ingest(self, sources: list[Path]) -> list[dict]:
        """Copy files (or whole folders) into raw/<role>/, keep sub-folder names, hash, set read-only."""
        added = []
        known = {r["sha256"]: r for r in self.manifest["raw"]}
        for src in sources:
            src = Path(src)
            files = [p for p in src.rglob("*") if p.is_file()] if src.is_dir() else [src]
            base = src if src.is_dir() else src.parent
            for f in sorted(files):
                digest = sha256_file(f)
                if digest in known:
                    print(f"  already in session: {f.name}")
                    continue
                role = detect_role(f)
                rel = Path("raw") / role / f.relative_to(base)
                dst = self.root / rel
                if dst.exists():
                    raise FileExistsError(f"{rel} exists with different content; refusing to overwrite raw data")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                os.chmod(dst, stat.S_IREAD)  # raw is immutable
                rec = {"path": rel.as_posix(), "sha256": digest, "size": f.stat().st_size, "role": role,
                       "source_name": f.name, "added_utc": now_utc()}
                self.manifest["raw"].append(rec)
                known[digest] = rec
                added.append(rec)
                print(f"  + {role:28s} {rel.as_posix()}")
        self.save()
        return added

    def verify(self) -> list[str]:
        problems = []
        for r in self.manifest["raw"]:
            p = self.root / r["path"]
            if not p.exists():
                problems.append(f"missing: {r['path']}")
            elif sha256_file(p) != r["sha256"]:
                problems.append(f"changed: {r['path']}")
        return problems
