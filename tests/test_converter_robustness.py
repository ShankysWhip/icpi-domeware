"""What the converter stress test found (scripts/converter_stress.py), kept as tests. All inputs are SIMULATED."""

import datetime as dt
import hashlib

import openpyxl
import pandas as pd
import pytest

from domeware import convert
from domeware.adapters.truth import read_dji_srt, read_mavlink_tlog, read_rtklib_pos
from domeware.adapters.vaisala import read_wxt_excel
from domeware.session import Session


def test_a_read_tlog_is_not_left_locked(tmp_path):
    from test_tlog_skyc import _tlog

    p = tmp_path / "gcs.tlog"
    _tlog(p)
    read_mavlink_tlog(p)
    p.write_bytes(b"")  # on Windows this failed while pymavlink still held the file open
    p.unlink()


def test_a_cut_rtklib_line_is_skipped_and_counted(tmp_path):
    p = tmp_path / "rover.pos"
    good = "2026/10/28 09:00:0{}.000   65.170000000   26.040000000   120.0000   1  18"
    p.write_text("% GPST latitude longitude height Q ns\n" + "\n".join(good.format(i) for i in range(3))
                 + "\n2026/10/28 09:00:03.000   65.17\n", encoding="utf-8")
    df, meta = read_rtklib_pos(p)
    assert len(df) == 3 and meta["skipped_lines"] == 1


def test_a_damaged_excel_export_says_so_clearly(tmp_path):
    p = tmp_path / "SIM_WXT536.xlsx"
    p.write_bytes(b"PK\x03\x04 cut short")
    with pytest.raises(ValueError, match="not a readable Excel export"):
        read_wxt_excel(p)


def _raw(s: Session, role: str, name: str, data: bytes) -> None:
    rel = f"raw/{role}/{name}"
    (s.root / rel).parent.mkdir(parents=True, exist_ok=True)
    (s.root / rel).write_bytes(data)
    s.manifest["raw"].append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "role": role})


def test_one_broken_channel_does_not_stop_the_others(tmp_path):
    s = Session(tmp_path / "S")
    _raw(s, "dji_csv", "broken.csv", b"\x00\x01 not a flight log")
    _raw(s, "robin_xml", "broken.xml", b"<Robin><Track>cut")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Generated (dd-MM-yyyy HH:mm:ss)", "28-10-2026 09:00:00", "Time Zone:", "UTC"])
    ws.append(["Timestamp", "Wind speed mean PT1M 0.0 m (m/s) - X"])
    ws.append(["28-10-2026 09:00", "4.50"])
    ws.append([None, None])
    ws.append(["28-10-2026 09:01", dt.datetime(2026, 5, 4)])
    wb.save(tmp_path / "w.xlsx")
    _raw(s, "vaisala_wxt", "SIM_wxt.xlsx", (tmp_path / "w.xlsx").read_bytes())
    s.save()
    convert.run(s)
    notes = " ".join(r["msg"] for r in s.manifest["log"])
    assert "broken.csv: NOT converted" in notes and "broken.xml: 0 track states" in notes
    assert not list((s.streams / "dji").glob("*.parquet"))  # no empty files that look like a flight
    w = pd.read_parquet(s.streams / "weather" / "onsite_vaisala.parquet")
    assert w["wind_ms"].tolist() == [4.5, 4.05]  # the weather channel still ran after the broken ones


def test_srt_without_gps_lock_is_a_clear_error_and_never_stops_convert(tmp_path):
    srt = ("1\n00:00:00,000 --> 00:00:00,033\n2026-10-28 11:00:00.000\n[latitude: 0.000000] [longitude: 0.000000] "
           "[rel_alt: 0.000 abs_alt: 0.000]\n")
    p = tmp_path / "DJI_0001.SRT"
    p.write_text(srt, encoding="utf-8")
    with pytest.raises(ValueError, match="latitude/longitude 0"):
        read_dji_srt(p)
    s = Session(tmp_path / "S")
    _raw(s, "dji_srt", "DJI_0001.SRT", srt.encode())
    s.save()
    convert.run(s)  # used to stop with IndexError before saving anything
    assert any("DJI_0001.SRT: NOT converted" in r["msg"] for r in s.manifest["log"])


def test_rtklib_geodetic_heights_are_refused(tmp_path):
    """RTKLIB writes 'height=WGS84/geodetic' for heights above the geoid: N2000 must not be subtracted again."""
    p = tmp_path / "rover.pos"
    p.write_text("% (lat/lon/height=WGS84/geodetic,Q=1:fix,2:float,3:sbas,4:dgps,5:single,6:ppp,ns=# of satellites)\n"
                 "% GPST latitude longitude height Q ns\n"
                 "2026/10/28 09:00:00.000   65.170000000   26.040000000   120.0000   1  18\n", encoding="utf-8")
    with pytest.raises(ValueError, match="geoid"):
        read_rtklib_pos(p)


def test_event_reader_does_not_leave_the_tlog_locked(tmp_path):
    from test_tlog_skyc import _tlog

    from domeware.events import from_mavlink_tlog

    p = tmp_path / "gcs.tlog"
    _tlog(p)
    from_mavlink_tlog(p)
    p.write_bytes(b"")  # locked while pymavlink held it open
    p.unlink()


def test_a_held_replay_is_kept_and_the_reason_is_clear(tmp_path):
    """Windows will not overwrite a replay a viewer still holds: the old one stays, nothing half-written is left."""
    import mmap
    import sys

    from domeware.replay import swap_in

    old, part = tmp_path / "replay.rrd", tmp_path / "replay.rrd.partial"
    old.write_bytes(b"old" * 100)
    part.write_bytes(b"new" * 100)
    swap_in(part, old)  # nothing holds it: swapped
    assert old.read_bytes() == b"new" * 100 and not part.exists()
    if sys.platform != "win32":
        return
    part.write_bytes(b"newer" * 100)
    fh = open(old, "rb")  # noqa: SIM115 - held on purpose, as a viewer would
    m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        with pytest.raises(SystemExit, match="held open by another program"):
            swap_in(part, old, wait_s=0.5)
    finally:
        m.close()
        fh.close()
    assert old.read_bytes() == b"new" * 100 and not part.exists()
