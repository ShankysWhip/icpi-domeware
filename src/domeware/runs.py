"""Runs inside a session: <session>/runs.csv, one row per run and per phase.

  run_id,part,start_utc,end_utc,notes
  R1,RUN,2026-10-28T09:02:10Z,2026-10-28T09:38:30Z,from take-offs and landings
  R1,P1,2026-10-28T09:02:10Z,2026-10-28T09:14:40Z,from scorecard card

`dw runs` writes RUN rows from the drones' take-offs and landings (drones airborne with < 120 s between them are one
run). The file is never overwritten once it exists: it is the team's to edit. Phase rows (P1..P4) come from a hand
edit or from a scorecard export (`dw runs SESSION --card run.json`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from domeware.session import Session

COLUMNS = ["run_id", "part", "start_utc", "end_utc", "notes"]
PARTS = ["RUN", "P1", "P2", "P3", "P4"]


def runs_path(s: Session) -> Path:
    return s.root / "runs.csv"


def airborne_spans(s: Session, airborne_m: float = 2.0) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    from domeware.replay import truth_files

    spans = []
    for f in truth_files(s):
        from domeware.replay import read_truth

        d = read_truth(f)
        if "h_rel_takeoff_m" not in d:
            continue
        air = d[d["h_rel_takeoff_m"] > airborne_m].sort_values("t_utc")
        if len(air):
            spans.append((air["t_utc"].iloc[0], air["t_utc"].iloc[-1], f.stem))
    return sorted(spans)


def detect(s: Session, gap_s: float = 120.0, airborne_m: float = 2.0) -> pd.DataFrame:
    """One RUN row per stretch of time with at least one drone airborne."""
    rows, cur = [], None
    for a, b, stem in airborne_spans(s, airborne_m):
        if cur and (a - cur["end"]).total_seconds() <= gap_s:
            cur["end"], cur["drones"] = max(cur["end"], b), cur["drones"] + [stem]
        else:
            if cur:
                rows.append(cur)
            cur = {"start": a, "end": b, "drones": [stem]}
    if cur:
        rows.append(cur)
    return pd.DataFrame([{"run_id": f"R{i + 1}", "part": "RUN", "start_utc": r["start"], "end_utc": r["end"],
                          "notes": f"from take-offs and landings: {len(r['drones'])} drone log(s)"} for i, r in enumerate(rows)],
                        columns=COLUMNS)


def _write(s: Session, df: pd.DataFrame) -> None:
    out = df.copy()
    for c in ("start_utc", "end_utc"):
        out[c] = pd.to_datetime(out[c], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").str.replace(".000000Z", "Z")
    order = out["part"].map({p: i for i, p in enumerate(PARTS)}).fillna(len(PARTS))  # unknown parts kept as written
    out.assign(_o=order).sort_values(["start_utc", "run_id", "_o"]).drop(columns="_o").to_csv(runs_path(s), index=False)


def load(s: Session) -> pd.DataFrame:
    p = runs_path(s)
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    missing = set(COLUMNS[:4]) - set(df.columns)
    if missing:
        raise SystemExit(f"{p.name}: missing columns {sorted(missing)}; expected {COLUMNS}")
    for c in ("start_utc", "end_utc"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce", format="ISO8601")  # hand edits mix precisions
    bad = df[df["start_utc"].isna() | df["end_utc"].isna() | (df["end_utc"] <= df["start_utc"])]
    if len(bad):
        raise SystemExit(f"{p.name}: rows with a missing or reversed time: {bad[['run_id', 'part']].values.tolist()}")
    return df


def ensure(s: Session) -> pd.DataFrame:
    """runs.csv as it stands; written from the drone logs the first time."""
    if not runs_path(s).exists():
        df = detect(s)
        if df.empty:
            return df
        _write(s, df)
        s.note(f"runs: {len(df)} run(s) found from take-offs and landings -> runs.csv (edit it freely)")
    return load(s)


def windows(df: pd.DataFrame, run_id: str) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    return {r.part: (r.start_utc, r.end_utc) for r in df[df["run_id"] == run_id].itertuples()}


def _clock(value: str, day: str) -> pd.Timestamp | None:
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?", str(value or ""))
    if not m:
        return None
    return pd.Timestamp(f"{day}T{int(m[1]):02d}:{m[2]}:{float(m[3] or 0):06.3f}", tz="UTC")


def import_card(s: Session, card: Path) -> pd.DataFrame:
    """A scorecard export (ICPI Run Scorecards v2, Export JSON) into the session: the card itself is kept as
    cards/<run>.json (the team's results, values and notes, shown in the report next to the measured values), and its
    run and phase times go into runs.csv."""
    c = json.loads(Path(card).read_text(encoding="utf-8"))
    run = c.get("run") or {}
    run_id = (run.get("run_id") or c.get("run_id") or "").strip()
    if not run_id:
        raise SystemExit(f"{card}: no run id in the card")
    keep_dir = s.root / "cards"
    keep_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id)[:120]
    if safe != run_id:  # 'R 1' and 'R-1' must not share a file
        import hashlib

        safe += "-" + hashlib.sha256(run_id.encode()).hexdigest()[:6]
    dst = keep_dir / f"{safe}.json"
    text = json.dumps(c, indent=1, ensure_ascii=False)
    if dst.exists() and dst.read_text(encoding="utf-8") != text:  # the earlier export is kept, renamed
        dst.rename(keep_dir / f"{safe}.{dst.stat().st_mtime_ns}.json")
    dst.write_text(text, encoding="utf-8")
    s.note(f"cards: {run_id}: scorecard kept as cards/{safe}.json")
    df = ensure(s)
    day = (run.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        if df.empty:
            raise SystemExit(f"{card}: the card has no Date (UTC) and the session has no runs to take the day from")
        day = df["start_utc"].iloc[0].strftime("%Y-%m-%d")
    new = []
    a, b = _clock(run.get("start"), day), _clock(run.get("end"), day)
    if a is not None and b is not None and b > a:
        new.append({"run_id": run_id, "part": "RUN", "start_utc": a, "end_utc": b, "notes": "from scorecard card"})
    for pid, ph in (c.get("phases") or {}).items():
        if pid not in PARTS or (ph or {}).get("used") == "Not used":
            continue
        a, b = _clock(ph.get("start"), day), _clock(ph.get("end"), day)
        if a is not None and b is not None and b > a:
            new.append({"run_id": run_id, "part": pid, "start_utc": a, "end_utc": b, "notes": "from scorecard card"})
    if not new:
        s.note(f"runs: {run_id}: the card has no run or phase times yet; runs.csv unchanged")
        return df
    new = pd.DataFrame(new, columns=COLUMNS)
    if not (new["part"] == "RUN").any():  # a run row spanning its phases, so every phase sits inside a run
        new.loc[len(new)] = [run_id, "RUN", new["start_utc"].min(), new["end_utc"].max(), "span of the card's phases"]
    if "notes" not in df:  # optional column; a hand-made runs.csv may leave it out
        df["notes"] = ""
    keep = df[~((df["run_id"] == run_id) & df["part"].isin(new["part"]))]
    # an auto-detected run over the same time is this run: the card names it, so the auto row goes
    ra = new[new["part"] == "RUN"].iloc[0]
    auto = keep["notes"].str.startswith("from take-offs") & (keep["start_utc"] < ra["end_utc"]) & (keep["end_utc"] > ra["start_utc"])
    keep = keep[~keep["run_id"].isin(keep[auto]["run_id"])]
    out = pd.concat([keep, new], ignore_index=True) if len(keep) else new
    _write(s, out)
    s.note(f"runs: {run_id}: {', '.join(new['part'])} from {Path(card).name}")
    return load(s)
