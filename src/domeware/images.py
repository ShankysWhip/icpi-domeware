"""Screenshots on the UTC clock: spectrum analyser (e.g. R&S PR200 SD card), radar screen, phone photos.

Each picture's time comes from its file name (any 2026-10-28 11-14-30 style stamp) read in the clock that
<session>/image_clocks.csv declares for its folder or file:

  match,clock
  PR200,Europe/Helsinki          a folder: every picture in it, file-name time read as Finnish local time
  radar_screen,UTC
  IMG_0042.png,2026-10-28T09:14:30Z   one file with no time in its name: its time, written down

A picture with no declaration is left out of the timeline with the reason: an analyser clock is never assumed to be
UTC. Trace exports (CSV or the instrument's own format) get a proper adapter once a real sample file exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from domeware.session import Session

STAMP = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ T_-]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})")


def _rules(s: Session) -> dict[str, str]:
    p = s.root / "image_clocks.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    return {m.strip().lower(): c.strip() for m, c in zip(df["match"], df["clock"], strict=True) if m.strip()}


def _to_utc(local: pd.Timestamp, clock: str) -> pd.Timestamp:
    if clock.upper() == "UTC":
        return local.tz_localize("UTC")
    if re.fullmatch(r"[+-]\d{2}:\d{2}", clock):
        return pd.Timestamp(local.isoformat() + clock).tz_convert("UTC")
    return local.tz_localize(clock, ambiguous="raise", nonexistent="raise").tz_convert("UTC")  # e.g. Europe/Helsinki


def pictures(s: Session) -> list[dict]:
    rules = _rules(s)
    out = []
    for f in s.raw_files("image"):
        rule = rules.get(f.name.lower())
        folders = [p.name.lower() for p in f.relative_to(s.raw / "image").parents if p.name]
        if rule is None:
            rule = next((rules[d] for d in folders if d in rules), None)
        group = folders[0] if folders else "pictures"
        if rule is None:
            out.append({"file": f, "group": group, "t_utc": None, "method": "left out: no clock declared in image_clocks.csv"})
            continue
        try:
            if re.match(r"\d{4}-\d{2}-\d{2}T", rule):  # a time written down for this one file
                t = pd.Timestamp(rule)
                out.append({"file": f, "group": group, "t_utc": t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC"),
                            "method": "time written in image_clocks.csv"})
                continue
            m = STAMP.search(f.stem)
            if not m:
                out.append({"file": f, "group": group, "t_utc": None, "method": "left out: no time in the file name"})
                continue
            local = pd.Timestamp(f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}")
            out.append({"file": f, "group": group, "t_utc": _to_utc(local, rule), "method": f"file name, clock {rule}"})
        except (ValueError, TypeError) as e:  # a bad declaration is reported, never guessed around
            out.append({"file": f, "group": group, "t_utc": None, "method": f"left out: {e}"})
    return out


def log_pictures(rec, s: Session) -> list[str]:
    """Each group (folder) becomes one picture lane in the replay; returns the entity paths for the blueprint."""
    import rerun as rr

    groups = []
    for p in pictures(s):
        if p["t_utc"] is None:
            s.note(f"picture: {p['file'].name}: {p['method']}")
            continue
        path = f"screens/{p['group']}"
        rec.set_time("utc", timestamp=p["t_utc"].to_datetime64())
        rec.log(path, rr.EncodedImage(path=Path(p["file"])))
        if path not in groups:
            groups.append(path)
    return groups
