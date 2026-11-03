"""The radar's own drone tracks, end to end: every track the radar itself classed DRONE, in time order, how long the
radar held them together and how fast it picked the drone up again after each break.

Needs no drone log, so it also answers while a DJI log is still encrypted (29 Oct: the M400's three laps came back as
three DRONE tracks). With a drone log, dw compare's association by distance is the stronger answer: a bird the radar
calls DRONE counts here too.
"""

from __future__ import annotations

import json

import pandas as pd


def chain(tracks: pd.DataFrame, window: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> dict | None:
    """Tracks with at least one DRONE state, by first update. held_s is the union of their spans (overlaps count
    once); gap_s is the wait from the latest end so far to the next start. With a window (the logged flight), share is
    the held time inside it over its length. None when the radar never said DRONE."""
    if not len(tracks) or "class_name" not in tracks:
        return None
    t = tracks.assign(t_utc=pd.to_datetime(tracks["t_utc"], utc=True))
    ids = t.loc[t["class_name"].eq("DRONE"), "track_id"].unique()
    if not len(ids):
        return None
    spans = t[t["track_id"].isin(ids)].groupby("track_id")["t_utc"].agg(["min", "max"]).sort_values("min")
    rows, end = [], None
    for tid, (a, b) in spans.iterrows():
        rows.append({"track_id": int(tid), "start": a, "end": b, "held_s": round((b - a).total_seconds(), 1),
                     "gap_s": None if end is None else round(max((a - end).total_seconds(), 0.0), 1)})
        end = b if end is None else max(end, b)
    out = {"tracks": rows, "held_s": _union_s(spans), "window_s": None, "share": None, "share_pct": None}
    if window is not None and window[1] > window[0]:
        w = (window[1] - window[0]).total_seconds()
        ratio = _union_s(spans, window) / w
        # the percent is rounded once, here, from the exact ratio: dw check, the report and the HUD print the same number
        out.update(window_s=round(w, 1), share=round(ratio, 3), share_pct=int(ratio * 100 + 0.5))
    return out


def _union_s(spans: pd.DataFrame, window=None) -> float:
    total, end = 0.0, None
    for a, b in zip(spans["min"], spans["max"], strict=True):
        if window is not None:
            a, b = max(a, window[0]), min(b, window[1])
        if end is not None:
            a = max(a, end)
        if b > a:
            total += (b - a).total_seconds()
            end = b
    return round(total, 1)


def flight_window(s) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The logged flight from an encrypted DJI log's header (start and total time): the yardstick for share."""
    for f in sorted(s.streams.glob("dji_details/*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("start_time") and d.get("total_time"):
            t0 = pd.Timestamp(d["start_time"]).tz_convert("UTC")
            return t0, t0 + pd.Timedelta(seconds=float(d["total_time"]))
    return None


def for_session(s) -> dict | None:
    files = sorted(s.streams.glob("robin/tracks.parquet")) + sorted(s.streams.glob("radar/tracks.parquet"))
    if not files:
        return None
    tracks = pd.concat([pd.read_parquet(f, columns=["t_utc", "track_id", "class_name"]) for f in files])
    return chain(tracks, flight_window(s))


def describe(c: dict) -> str:
    """One line for dw check and the report."""
    held = f"{int(c['held_s'] // 60)} min {int(c['held_s'] % 60):02d} s"
    text = (f"{len(c['tracks'])} track(s) the radar classed DRONE ({', '.join(str(r['track_id']) for r in c['tracks'])}),"
            f" held {held}")
    if c["share_pct"] is not None:
        text += f", {c['share_pct']} % of the logged flight"
    gaps = [f"{r['gap_s']:.0f} s" for r in c["tracks"] if r["gap_s"]]
    return text + (f"; picked up again after {', '.join(gaps)}" if gaps else "")
