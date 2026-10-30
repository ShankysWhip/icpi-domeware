"""Real-data tests from our own pilot (OuluZone, 29 Oct 2026). The files stay outside git, in
domeware-data/sessions/2026-10-29_oulu; only their sha256 is written here. Tests skip on laptops without them.
The expected numbers were read from the files once and checked by physics (RH from T and Td), not typed by hand."""

import datetime as dt
import hashlib
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from domeware.adapters.vaisala import read_wxt_excel
from domeware.session import Session, detect_role

SESSION = Path.home() / "Work" / "domeware-data" / "sessions" / "2026-10-29_oulu"
REAL = {"vaisala_wxt": "5f2a9774835a4ea1a8379b50201be43d39143f0807270c0d2e602edd50974cbb",
        "dji_flightrecord_encrypted": "6b4954b10ee3c0c07b71ef7d655b2206fb02e8044c488ddd9de0aafe79f992db"}


def _real(role: str) -> Path:
    if not SESSION.exists():
        pytest.skip("pilot session not on this laptop")
    hit = [p for p in Session(SESSION).raw_files(role) if hashlib.sha256(p.read_bytes()).hexdigest() == REAL[role]]
    if not hit:
        pytest.skip(f"real {role} file not found by its hash")
    return hit[0]


def test_vaisala_wxt536_export_from_oulu():
    df, meta = read_wxt_excel(_real("vaisala_wxt"))
    assert meta["time_zone_in_file"] == "UTC" and meta["rows"] == 2285
    assert meta["cells_repaired_from_spreadsheet_dates"] == 1284  # 8.10 turned into 8 October by a spreadsheet
    assert meta["check_rh_max_diff_pct"] <= 0.1  # the repair is right: RH from T and Td matches measured RH
    assert meta["check_wind_order_violations"] == 0
    flight = df[(df["t_utc"] >= "2026-10-29 13:46") & (df["t_utc"] <= "2026-10-29 14:01")]
    assert 3.0 <= flight["wind_ms"].min() and flight["gust_ms"].max() <= 8.0
    assert flight["pressure_hpa"].max() - flight["pressure_hpa"].min() < 0.5  # baro drift of the drone < ~4 m


def test_dji_pilot_file_name_is_recognised():
    p = _real("dji_flightrecord_encrypted")
    assert p.name.startswith("DJIFlightRecord_") and detect_role(p) == "dji_flightrecord_encrypted"


def test_spreadsheet_date_repair_on_a_made_up_sheet(tmp_path):
    """SIMULATED: the same layout with one value saved as a date, as a day.month spreadsheet would."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Generated (dd-MM-yyyy HH:mm:ss)", "29-10-2026 14:05:57", "Time Zone:", "UTC"])
    ws.append(["Device:", "SIM"])
    ws.append(["Timestamp", "Air temperature mean PT1M 0.0 m (C) - X", "Wind speed mean PT1M 0.0 m (m/s) - X"])
    ws.append(["29-10-2026 13:46", dt.datetime(2026, 10, 8), "4.50"])
    ws.append([None, None, None])
    ws.append(["29-10-2026 13:47", "7.55", dt.datetime(2026, 5, 4)])
    p = tmp_path / "SIM_WXT536.xlsx"
    wb.save(p)
    df, meta = read_wxt_excel(p)
    assert df["temp_c"].tolist() == [8.10, 7.55] and df["wind_ms"].tolist() == [4.50, 4.05]
    assert meta["cells_repaired_from_spreadsheet_dates"] == 2
    assert df["t_utc"].iloc[0] == pd.Timestamp("2026-10-29 13:46", tz="UTC")
