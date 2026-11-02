"""dw validate keeps every tool on the data contract. All data here is SIMULATED."""
from pathlib import Path

import pandas as pd

from domeware import validate
from domeware.session import Session


def _valid(need: list[str], n: int = 3) -> pd.DataFrame:
    cols = {}
    for c in need:
        if c in validate.TIMES or c.startswith("t_first_"):
            cols[c] = pd.date_range("2026-10-29 12:00", periods=n, freq="1s", tz="UTC")
        elif c in ("lat_deg", "s_position_latitude"):
            cols[c] = [65.0] * n
        elif c in ("lon_deg", "s_position_longitude"):
            cols[c] = [26.0] * n
        elif c in ("track_id", "plot_id", "msg_index", "qflags", "class_code", "n_states", "s_processing_sensitivity"):
            cols[c] = list(range(n))
        elif c in ("alarm", "alarm_any"):
            cols[c] = [False] * n
        elif c.endswith(("_m", "_deg", "_mps", "_s", "_dbsm", "_ms", "_c", "_pct", "_hpa")) or c in ("score", "max_score",
                                                                                                    "s_ground_level", "s_position_altitude", "s_orientation_azimuth"):
            cols[c] = [1.0] * n
        else:
            cols[c] = ["SIMULATED"] * n
    return pd.DataFrame(cols)


def _session(tmp_path) -> Session:
    s = Session(tmp_path / "S")
    s.manifest["site"] = {"name": "SIM radar", "lat_deg": 65.0, "lon_deg": 26.0, "alt_m": 100.0, "alt_datum": "N2000"}
    for pattern, need in validate.CONTRACT.items():
        f = s.streams / pattern.replace("*", "SIMULATED")
        f.parent.mkdir(parents=True, exist_ok=True)
        _valid(need).to_parquet(f)
    return s


def test_a_session_on_the_contract_passes(tmp_path):
    lines = validate.run(_session(tmp_path))
    assert not [m for lv, m in lines if lv == "FAIL"], lines
    assert len([m for lv, m in lines if lv.strip() == "OK"]) == len(validate.CONTRACT)


def test_broken_tables_fail_with_the_reason(tmp_path):
    s = _session(tmp_path)
    f = s.streams / "robin" / "tracks.parquet"
    df = pd.read_parquet(f).drop(columns=["range_m"])
    df.loc[0, "lat_deg"] = 95.0
    df["t_utc"] = df["t_utc"].dt.tz_localize(None)
    df.to_parquet(f)
    fails = [m for lv, m in validate.run(s) if lv == "FAIL"]
    assert any("missing range_m" in m for m in fails)
    assert any("lat_deg outside" in m for m in fails)
    assert any("t_utc" in m and "not a UTC time" in m for m in fails)
    assert validate.main(s) == 1


def test_tables_need_a_site_to_be_placed(tmp_path):
    s = _session(tmp_path)
    del s.manifest["site"]
    assert any(lv == "FAIL" and "no site position" in m for lv, m in validate.run(s))


def test_the_contract_in_code_matches_docs_data_md():
    doc = (Path(__file__).parents[1] / "docs" / "DATA.md").read_text(encoding="utf-8")
    for pattern, need in validate.CONTRACT.items():
        assert pattern.split("*")[0] in doc, pattern
        missing = [c for c in need if f"`{c}`" not in doc]
        assert not missing, (pattern, missing)
