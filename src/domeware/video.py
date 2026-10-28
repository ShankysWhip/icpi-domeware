"""Video in the replay: MP4 / MOV clips placed on the UTC clock, shown next to the 3D view.

Time of a clip's first frame, best source first:
  1. the DJI .SRT caption file with the same name (its first caption is the first frame; local time -> UTC)
  2. <session>/video_offsets.csv with columns file,start_utc (for example from filming a GPS clock or a time slate)
A clip with neither is left out and the reason is noted: a guessed video time would make every comparison wrong.

The Rerun viewer decodes AV1 itself; H.264 and H.265 (most drone cameras) need ffmpeg on the PATH (`dw doctor`).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from domeware.session import Session


def clips(s: Session) -> list[dict]:
    srts = {p.stem.lower(): p for p in s.raw_files("dji_srt")}
    offsets = {}
    p = s.root / "video_offsets.csv"
    if p.exists():
        o = pd.read_csv(p, dtype=str)
        offsets = {Path(f).name.lower(): pd.Timestamp(t) for f, t in zip(o["file"], o["start_utc"], strict=True)}
    out = []
    for v in s.raw_files("video"):
        if v.stem.lower() in srts:
            from domeware.adapters.truth import read_dji_srt

            try:
                df, _ = read_dji_srt(srts[v.stem.lower()])
                out.append({"file": v, "start_utc": df["t_utc"].iloc[0], "method": f"first caption of {srts[v.stem.lower()].name}"})
                continue
            except ValueError as e:
                reason = f"its caption file has no times ({e})"
        else:
            reason = "no caption file with the same name"
        if v.name.lower() in offsets:
            t = offsets[v.name.lower()]
            out.append({"file": v, "start_utc": t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC"),
                        "method": "video_offsets.csv"})
        else:
            out.append({"file": v, "start_utc": None, "method": f"left out: {reason} and not in video_offsets.csv"})
    return out


def log_videos(rec, s: Session) -> list[str]:
    """Log every clip with a known start; returns the entity paths for the blueprint."""
    import rerun as rr

    paths = []
    for c in clips(s):
        if c["start_utc"] is None:
            s.note(f"video: {c['file'].name}: {c['method']}")
            continue
        path = f"video/{c['file'].stem}"
        try:
            asset = rr.AssetVideo(path=str(c["file"]))
            rec.log(path, asset, static=True)
            ts = np.asarray(asset.read_frame_timestamps_nanos(), dtype=np.int64)
        except Exception as e:  # an unreadable clip must not stop the replay
            s.note(f"video: {c['file'].name}: not added ({e})")
            continue
        utc = pd.Timestamp(c["start_utc"]).tz_convert("UTC").tz_localize(None).to_datetime64().astype("datetime64[ns]")
        rec.send_columns(path, indexes=[rr.TimeColumn("utc", timestamp=utc + ts.astype("timedelta64[ns]"))],
                         columns=rr.VideoFrameReference.columns_nanos(ts))
        s.note(f"video: {c['file'].name}: {len(ts)} frames from {pd.Timestamp(utc)} UTC ({c['method']})")
        paths.append(path)
    return paths
