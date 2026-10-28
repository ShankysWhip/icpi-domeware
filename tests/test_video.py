"""Video on the UTC clock. The clip is SIMULATED: 2 s of a test pattern made with ffmpeg in the test."""

import subprocess

import pandas as pd
import pytest

from domeware.session import Session
from domeware.video import clips, log_videos


def _clip(path):
    ffmpeg = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_clip_starts_at_its_first_caption_and_every_frame_is_placed(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _clip(src / "DJI_0007_SIMULATED.MP4")
    # 28 Oct is winter time in Finland: local 11:00:05.250 = 09:00:05.250 UTC
    (src / "DJI_0007_SIMULATED.SRT").write_text("1\n00:00:00,000 --> 00:00:00,100\n2026-10-28 11:00:05.250\n"
                                                "[latitude: 65.1] [longitude: 26.0] [rel_alt: 1.0 abs_alt: 90.0]\n")
    s = Session(tmp_path / "SIM")
    s.ingest([src])
    [c] = clips(s)
    assert c["start_utc"] == pd.Timestamp("2026-10-28 09:00:05.250", tz="UTC")
    import rerun as rr

    rec = rr.RecordingStream("test-video")
    rec.memory_recording()
    assert log_videos(rec, s) == ["video/DJI_0007_SIMULATED"]


def test_clip_without_time_is_left_out_not_guessed(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _clip(src / "GOPRO_SIMULATED.mp4")
    s = Session(tmp_path / "SIM")
    s.ingest([src])
    [c] = clips(s)
    assert c["start_utc"] is None and "left out" in c["method"]
    (s.root / "video_offsets.csv").write_text("file,start_utc\nGOPRO_SIMULATED.mp4,2026-10-28T09:01:00Z\n")
    [c] = clips(s)
    assert c["start_utc"] == pd.Timestamp("2026-10-28 09:01:00", tz="UTC") and c["method"] == "video_offsets.csv"
