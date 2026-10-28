"""Moving a session between the three of us (decision D16): seal it, copy it, check the copy.

`dw seal SESSION` writes seal.json: the sha256 of every file the team made or brought (raw/, runs.csv, notes/,
video_offsets.csv, manifest.json). Derived folders (streams/, env/, views/) are left out: `dw run` rebuilds them.
`dw sync SESSION DEST` copies those files to DEST/<session>, never deletes anything there, merges notes line by
line, and then checks every copied file against the seal. Encrypt the copy for transport (for example with age);
that step is the carrier's, outside domeware.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from domeware.session import Session, now_utc, sha256_file

KEEP = ("raw", "notes", "cards", "runs.csv", "video_offsets.csv", "image_clocks.csv", "manifest.json")


def _files(root: Path) -> list[Path]:
    out = []
    for k in KEEP:
        p = root / k
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file())
    return out


def seal(s: Session) -> dict:
    files = {f.relative_to(s.root).as_posix(): sha256_file(f) for f in _files(s.root)}
    rec = {"session_id": s.manifest["session_id"], "sealed_utc": now_utc(), "files": files}
    (s.root / "seal.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    s.note(f"seal: {len(files)} files sealed -> seal.json")
    return rec


def check_seal(root: Path) -> list[str]:
    p = root / "seal.json"
    if not p.exists():
        return ["no seal.json: run dw seal on the source first"]
    seal_ = json.loads(p.read_text(encoding="utf-8"))
    problems = []
    for rel, sha in seal_["files"].items():
        f = root / rel
        if not f.exists():
            problems.append(f"missing: {rel}")
        elif not rel.startswith(("notes/", "manifest.json")) and sha256_file(f) != sha:
            problems.append(f"changed: {rel}")  # notes grow and the manifest logs; raw and runs must match exactly
    return problems


def _merge_lines(src: Path, dst: Path) -> None:
    have = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    new = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip() and ln not in set(have)]
    if new:
        with open(dst, "a", encoding="utf-8") as f:
            f.write(("\n" if have and not dst.read_text(encoding="utf-8").endswith("\n") else "") + "\n".join(new) + "\n")


def sync(s: Session, dest: Path) -> list[str]:
    if not (s.root / "seal.json").exists():
        seal(s)
    target = Path(dest) / s.root.name
    if target.exists() and (target / "manifest.json").exists():
        other = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if other.get("session_id") != s.manifest["session_id"]:
            raise SystemExit(f"{target} holds a different session (id {other.get('session_id')}): nothing copied")
    copied = skipped = 0
    for f in [*_files(s.root), s.root / "seal.json"]:
        rel = f.relative_to(s.root)
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if rel.parts[0] == "notes":
            _merge_lines(f, out)
            continue
        if out.exists() and out.stat().st_size == f.stat().st_size and sha256_file(out) == sha256_file(f):
            skipped += 1
            continue
        if out.exists() and rel.parts[0] == "raw":
            raise SystemExit(f"{out} exists with different content: raw files are never overwritten")
        if out.exists():
            out.chmod(0o666)  # manifest/runs/seal are replaced by the sealed source; raw never reaches here
        shutil.copy2(f, out)
        copied += 1
    problems = check_seal(target)
    s.note(f"sync: {copied} copied, {skipped} already there -> {target}; check: {'OK' if not problems else problems}")
    return problems
