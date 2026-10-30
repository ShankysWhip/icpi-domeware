"""What the converter stress test found (scripts/converter_stress.py), kept as tests. All inputs are SIMULATED."""

import datetime as dt
import hashlib

import openpyxl
import pandas as pd
import pytest

from domeware import convert
from domeware.adapters.truth import read_mavlink_tlog, read_rtklib_pos
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
