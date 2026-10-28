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
        elif rel.startswith("raw/") and sha256_file(f) != sha:
            problems.append(f"changed: {rel}")  # raw must match exactly; team files may have grown at the destination
    return problems


def _merge_lines(src: Path, dst: Path) -> None:
    have = set(dst.read_text(encoding="utf-8").splitlines()) if dst.exists() else set()
    new = list(dict.fromkeys(ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip() and ln not in have))
    if new:
        with open(dst, "a", encoding="utf-8") as f:
            f.write(("\n" if have and not dst.read_text(encoding="utf-8").endswith("\n") else "") + "\n".join(new) + "\n")


def _merge_runs(src: Path, dst: Path) -> bool:
    """runs.csv: every row the destination has stays; rows only the source has are added. True if rows conflicted."""
    import pandas as pd

    a, b = pd.read_csv(dst, dtype=str, keep_default_na=False), pd.read_csv(src, dtype=str, keep_default_na=False)
    key = ["run_id", "part"]
    both = b.merge(a[key + ["start_utc", "end_utc"]], on=key, how="inner", suffixes=("", "_dst"))
    conflict = bool(((both["start_utc"] != both["start_utc_dst"]) | (both["end_utc"] != both["end_utc_dst"])).any())
    add = b.merge(a[key], on=key, how="left", indicator=True)
    add = add[add["_merge"] == "left_only"].drop(columns="_merge")
    if len(add):
        pd.concat([a, add], ignore_index=True).to_csv(dst, index=False)
    return conflict


def sync(s: Session, dest: Path) -> list[str]:
    """Returns problems (empty = good). Team files that differ at the destination are never overwritten: runs.csv
    rows are merged, other files are kept and the incoming version is written next to them as *.incoming-<utc>."""
    seal(s)  # always fresh: what is copied is what is checked
    target = Path(dest) / s.root.name
    if target.exists() and (target / "manifest.json").exists():
        other = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if other.get("session_id") != s.manifest["session_id"]:
            raise SystemExit(f"{target} holds a different session (id {other.get('session_id')}): nothing copied")
    copied = skipped = 0
    kept: list[str] = []
    stamp = now_utc().replace(":", "").replace("-", "")
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
        if out.exists() and rel.as_posix() == "runs.csv":
            if _merge_runs(f, out):
                kept.append("runs.csv: some run times differ at the destination; its rows were kept")
            continue
        if out.exists() and rel.as_posix() == "manifest.json":
            mine, theirs = json.loads(f.read_text(encoding="utf-8")), json.loads(out.read_text(encoding="utf-8"))
            have = {r["sha256"] for r in theirs["raw"]}
            theirs["raw"] += [r for r in mine["raw"] if r["sha256"] not in have]  # their streams and log stay theirs
            out.chmod(0o666)
            out.write_text(json.dumps(theirs, indent=2, default=str), encoding="utf-8")
            continue
        if out.exists() and rel.as_posix() != "seal.json":  # cards, video_offsets, image_clocks: theirs stays
            side = out.with_name(f"{out.stem}.incoming-{stamp}{out.suffix}")
            shutil.copy2(f, side)
            kept.append(f"{rel.as_posix()}: differs at the destination; kept theirs, yours is {side.name}")
            continue
        if out.exists():
            out.chmod(0o666)
        shutil.copy2(f, out)
        copied += 1
    problems = check_seal(target) + kept
    s.note(f"sync: {copied} copied, {skipped} already there -> {target}; check: {'OK' if not problems else problems}")
    return problems
