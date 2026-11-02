"""dw survey reads a data drop in place; dw check tells the radar's height system. All data here is SIMULATED."""
import numpy as np
import pandas as pd
import pytest

from domeware import datum, survey
from domeware.session import Session


def test_a_drop_is_mapped_without_touching_it(tmp_path):
    drop = tmp_path / "drop"
    (drop / "export").mkdir(parents=True)
    (drop / "raw").mkdir()
    xml = drop / "export" / "tracks_SIMULATED.xml"
    xml.write_text('<?xml version="1.0"?><Robin><Calib at="1970-01-01T00:00:00Z"/>'  # an unset field, as in real files
                   '<Tracks timestamp="2026-10-29T12:50:01.100Z"/>'
                   + "<Track/>" * 9000 + '<Tracks timestamp="2026-10-29T14:10:22.900+00:00"/></Robin>', encoding="utf-8")
    (drop / "raw" / "radar_0001.img").write_bytes(b"ROBIN RADAR SYSTEMS image" + bytes(5000))
    (drop / "notes.txt").write_text("hello")
    (drop / "bundle.zip").write_bytes(b"PK\x03\x04")
    before = {p: p.stat().st_mtime_ns for p in drop.rglob("*") if p.is_file()}

    r = survey.survey(drop)
    g = r["groups"]
    assert set(g) == {"robin_xml", "robin_raw", "unknown", "archive"}
    assert (g["robin_xml"]["first"], g["robin_xml"]["last"]) == (pd.Timestamp("2026-10-29 12:50:01.1", tz="UTC"),
                                                                  pd.Timestamp("2026-10-29 14:10:22.9", tz="UTC"))
    text = survey.report(drop)
    assert "12:50:01 -> 14:10:22 UTC" in text and "To ingest: 1 files" in text
    assert "Leave on the drive: 1 raw radar recordings" in text and "Archives: unpack" in text
    assert {p: p.stat().st_mtime_ns for p in drop.rglob("*") if p.is_file()} == before  # nothing written or moved


def _session_with_radar_status(tmp_path, ground_m: float, map_m: float | None) -> Session:
    s = Session(tmp_path / "S")
    (s.streams / "robin").mkdir(parents=True)
    pd.DataFrame({"t_utc": pd.date_range("2026-10-29 12:00", periods=3, freq="1min", tz="UTC"),
                  "s_position_latitude": 65.0, "s_position_longitude": 26.0, "s_position_altitude": ground_m + 14.0,
                  "s_ground_level": ground_m}).to_parquet(s.streams / "robin" / "status.parquet")
    if map_m is not None:
        import rasterio
        from rasterio.transform import from_origin

        from domeware.env import TO_TM35

        e, n = TO_TM35.transform(26.0, 65.0)
        s.env.mkdir(parents=True, exist_ok=True)
        with rasterio.open(s.env / "dem.tif", "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
                           crs="EPSG:3067", transform=from_origin(e - 20, n + 20, 10, 10)) as dst:
            dst.write(np.full((1, 4, 4), map_m, dtype="float32"))
    return s


def test_radar_heights_on_the_map_are_sea_level(tmp_path):
    hd = datum.robin_height_datum(_session_with_radar_status(tmp_path, ground_m=85.0, map_m=84.4))
    assert hd["verdict"] == "orthometric" and hd["difference_m"] == pytest.approx(0.6) and hd["mast_m"] == pytest.approx(14.0)


def test_radar_heights_above_the_map_by_the_geoid_are_ellipsoidal(tmp_path):
    from domeware.adapters import truth as T

    if not (T.PROJ_DIR / "fi_nls_fin2023n2000.tif").exists():
        pytest.skip("N2000 geoid grid not on this machine (dw setup)")
    geoid = 100.0 - float(np.ravel(T.ellipsoidal_to_n2000([65.0], [26.0], [100.0]))[0])
    hd = datum.robin_height_datum(_session_with_radar_status(tmp_path, ground_m=85.0 + geoid, map_m=85.0))
    assert hd["verdict"] == "ellipsoidal" and 15 < hd["geoid_m"] < 21


def test_without_map_heights_the_datum_stays_open(tmp_path):
    hd = datum.robin_height_datum(_session_with_radar_status(tmp_path, ground_m=85.0, map_m=None))
    assert hd["verdict"] == "unclear" and "dw env" in hd["why"]
    assert datum.robin_height_datum(Session(tmp_path / "empty")) is None  # no radar status: nothing to say
