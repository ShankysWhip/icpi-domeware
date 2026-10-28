"""COURAGEOUS export, validated against the official schema 0.4.1 (fetched by dw setup). SIMULATED tracks."""

import pandas as pd
import pytest

from domeware import courageous as C

SITE = {"lat_deg": 65.17, "lon_deg": 26.04, "alt_m": 97.5, "name": "SIM"}


def _tracks():
    t = pd.date_range("2026-10-28 09:00:00", periods=4, freq="1s", tz="UTC")
    return pd.DataFrame({"t_utc": t, "track_id": [7, 7, 8, 8], "lat_deg": 65.18, "lon_deg": 26.05, "alt_m": 140.0,
                         "class_name": ["UNCLASSIFIED", "DRONE", "VEHICLE", "SUSPECTED_DRONE"],
                         "alarm": [False, True, None, False], "v_e_mps": 1.0, "v_n_mps": 2.0, "v_u_mps": 0.0})


def test_mapping_and_times():
    doc = C.document(_tracks(), None, SITE, "IRIS SIM")
    recs = [r for tr in doc["tracks"] for r in tr["records"]]
    assert [r["classification"] for r in recs] == ["Unknown", "UAV", "Other", "Unknown"]
    assert recs[0]["time"] == 1793178000000  # Unix ms, UTC
    assert recs[1]["alarm"] == {"active": True, "certainty": 1.0} and recs[2]["alarm"]["active"] is False
    assert len({r["record_number"] for r in recs}) == 4 and doc["tracks"][0]["uas_id"] == 7


@pytest.mark.skipif(not C.SCHEMA_PATH.exists(), reason="run dw setup to fetch the COURAGEOUS schema")
def test_valid_against_official_schema_and_catches_errors():
    plots = _tracks().drop(columns=["track_id"])
    doc = C.document(_tracks(), plots, SITE, "IRIS SIM")
    assert C.validate(doc) == []
    doc["tracks"][0]["records"][0]["classification"] = "Bird"  # not in the schema's enum
    assert any("Bird" in p for p in C.validate(doc))
