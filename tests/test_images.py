"""Screenshots on the UTC clock. The pictures are SIMULATED, made in the test."""

import pandas as pd
from PIL import Image

from domeware.images import pictures
from domeware.session import Session


def test_analyser_screenshots_need_a_declared_clock(tmp_path):
    src = tmp_path / "card" / "PR200"
    src.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(src / "Screenshot_2026-10-28_11-14-30_SIMULATED.png")
    Image.new("RGB", (8, 8), "white").save(src / "trace_SIMULATED.png")  # no time in the name; differs, so not a duplicate
    s = Session(tmp_path / "S")
    s.ingest([tmp_path / "card"])
    assert all(p["t_utc"] is None and "no clock declared" in p["method"] for p in pictures(s))
    (s.root / "image_clocks.csv").write_text("match,clock\nPR200,Europe/Helsinki\ntrace_SIMULATED.png,2026-10-28T09:20:00Z\n")
    got = {p["file"].name: p["t_utc"] for p in pictures(s)}
    assert got["Screenshot_2026-10-28_11-14-30_SIMULATED.png"] == pd.Timestamp("2026-10-28 09:14:30", tz="UTC")  # winter time
    assert got["trace_SIMULATED.png"] == pd.Timestamp("2026-10-28 09:20:00", tz="UTC")
