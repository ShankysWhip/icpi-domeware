"""Team notes on the UTC clock: <session>/notes/<author>.jsonl, one line per note, append-only.

`dw note SESSION "bird flock east of the radar" --at 09:14:30` adds one. Each author has their own file, so notes
from three laptops merge without conflicts when sessions are synced. Notes show in the replay's Events panel and in
the report. A note is never edited or deleted in place: write a new one that corrects it.
"""

from __future__ import annotations

import getpass
import json
import re
import subprocess

import pandas as pd

from domeware.session import Session, now_utc


def author() -> str:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or getpass.getuser()


def _when(at: str | None, s: Session) -> pd.Timestamp:
    if not at:
        return pd.Timestamp.now(tz="UTC").floor("s")
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?", at.strip()):  # a clock time: the day comes from the session's runs
        from domeware import runs as R

        df = R.load(s)
        day = df["start_utc"].min().strftime("%Y-%m-%d") if len(df) else pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        return pd.Timestamp(f"{day}T{at.strip()}", tz="UTC")
    t = pd.Timestamp(at)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def add(s: Session, text: str, at: str | None = None, who: str | None = None) -> dict:
    who = who or author()
    rec = {"t_utc": _when(at, s).isoformat(), "author": who, "text": text.strip(), "written_utc": now_utc()}
    d = s.root / "notes"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", who)[:60] or "someone"
    with open(d / f"{safe}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(s: Session) -> pd.DataFrame:
    rows = []
    for p in sorted((s.root / "notes").glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"t_utc": None, "author": p.stem, "text": f"(unreadable line in {p.name})"})
    df = pd.DataFrame(rows, columns=["t_utc", "author", "text", "written_utc"])
    df["t_utc"] = pd.to_datetime(df["t_utc"], utc=True, errors="coerce", format="ISO8601")
    return df.sort_values("t_utc").reset_index(drop=True)
