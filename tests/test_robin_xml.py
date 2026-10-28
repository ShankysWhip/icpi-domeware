"""Tests for the prototype Robin XML converter.

Run from the toolkit env (pytest is installed there):
  cd C:/Users/nikla_68b56qz/Work/cuas-toolkit
  uv run python -I -m pytest -p no:cacheprovider C:/Users/nikla_68b56qz/Work/domeware-data/proto/robin/tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import os  # noqa: E402

from domeware.adapters import robin_xml as R  # noqa: E402

ARCHIVE = Path(os.environ.get("DOMEWARE_SAMPLE_ARCHIVE", str(Path.home() / "Work" / "domeware-data" / "archive-2026-10-21")))  # real sample run; tests skip if missing
REAL_XML = ARCHIVE / "ozmasto2.xml"
REAL_CFG = ARCHIVE / "20261021_103214" / "cfg"

# ------------------------------------------------------------------ synthetic stream
SYN = b"""<Robin>
    <RadarMotion RadarId="0">
        <Timestamp>1970-01-01T00:00:00.000Z</Timestamp>
        <Position><Latitude>0</Latitude><Longitude>0</Longitude><Altitude>0</Altitude></Position>
        <Speed>0</Speed><Heading>0</Heading><Pitch>0.1</Pitch><Roll>-0.2</Roll>
    </RadarMotion>
</Robin>
<Robin>
    <SystemStatus timestamp="2026-10-21T10:00:00.000Z">
        <Name>S</Name><Version>1</Version><OperationalState>operational</OperationalState><Messages/>
        <Sensors><SensorStatus id="0"><Name>S</Name>
            <Position><Latitude>65.0</Latitude><Longitude>26.0</Longitude><Altitude>100.0</Altitude></Position>
            <GroundLevel>90.0</GroundLevel>
            <Orientation><Azimuth>-5</Azimuth><Elevation>0</Elevation></Orientation>
            <Components><GPS Ready="true" Running="true" Lock="true" HDOP="0.5"/></Components>
        </SensorStatus></Sensors>
    </SystemStatus>
</Robin>
<Robin>
    <Tracks timestamp="2026-10-21T10:00:01.000Z">
        <Track id="1">
            <Timestamp>2026-10-21T10:00:00.500Z</Timestamp>
            <Position><Latitude>65.001</Latitude><Longitude>26.0</Longitude><Altitude>120</Altitude></Position>
            <Velocity><Azimuth>90</Azimuth><Elevation>0</Elevation><Speed>10</Speed></Velocity>
            <Classification id="6">DRONE</Classification>
            <MedianRCS>-17</MedianRCS><CurrentRCS>-20</CurrentRCS><Score>0.5</Score><Alarm>false</Alarm>
        </Track>
    </Tracks>
</Robin>
<Robin>
    <RadarMotion RadarId="0">
        <Timestamp>1970-01-01T00:00:00.000Z</Timestamp>
        <Position><Latitude>0</Latitude><Longitude>0</Longitude><Altitude>0</Altitude></Position>
        <Speed>0</Speed><Heading>0</Heading><Pitch>0.11</Pitch><Roll>-0.2</Roll>
    </RadarMotion>
</Robin>
<Robin>
    <Tracks timestamp="2026-10-21T10:00:02.000Z">
        <Track id="1">
            <Timestamp>2026-10-21T10:00:00.500Z</Timestamp>
            <Position><Latitude>65.001</Latitude><Longitude>26.0</Longitude><Altitude>120</Altitude></Position>
            <Velocity><Azimuth>90</Azimuth><Elevation>0</Elevation><Speed>10</Speed></Velocity>
            <Classification id="6">DRONE</Classification>
            <MedianRCS>-17</MedianRCS><CurrentRCS>-20</CurrentRCS><Score>0.5</Score><Alarm>true</Alarm>
            <NewThing>1</NewThing>
        </Track>
        <Track id="2">
            <Timestamp>2026-10-21T10:00:01.900Z</Timestamp>
            <Position><Latitude>65.0</Latitude><Longitude>26.001</Longitude><Altitude>95</Altitude></Position>
            <Velocity><Azimuth>0</Azimuth><Elevation>0</Elevation><Speed>1</Speed></Velocity>
            <Classification>UNCLASSIFIED</Classification>
            <MedianRCS>-30</MedianRCS><CurrentRCS>-30</CurrentRCS><Score>0.3</Score><Alarm>false</Alarm>
        </Track>
    </Tracks>
</Robin>
<Robin>
    <PotentialDronePlots timestamp="2026-10-21T10:00:02.500Z">
        <plot id="9" timestamp="2026-10-21T10:00:01.700Z" rcs="4.1">
            <position latitude="65.0005" longitude="26.0" altitude="110"/>
        </plot>
    </PotentialDronePlots>
</Robin>
<Robin>
    <RadarImageMeta RadarId="0" ImageId="100" Timestamp="2026-10-21T10:00:02.149Z" LineResolution="4.50" AzimuthCellSize="4.50">
        <LineClassifications><Lines Type="No Data">[10.25,14.75]</Lines></LineClassifications>
    </RadarImageMeta>
</Robin>
<Robin>
    <MysteryMessage a="1"/>
</Robin>
<Robin>
    <Tracks timestamp="2026-10-21T10:00:03.000Z">
        <Track id="3"><Timestamp>2026-10-21T10:0"""  # truncated last fragment

CFG = """GENERAL
{
    DATABASE
    {
        ENABLED TRUE
        USERNAME someuser
        PASSWORD TopSecretPw123
    }
    MAPTILEPROVIDER
    {
        NAME "A"
        API_KEY KeyKeyKey999
        URL_RASTER "https://x.example/{z}/{x}/{y}.png?key=KeyKeyKey999"
    }
    MAPTILEPROVIDER
    {
        NAME "B"
        API_KEY KeyKeyKey999
    }
}
IRISRADAR
{
    FILEPATHS ""
    CONTROLLER
    {
        ADDRESS 10.0.0.1
        START_FREQ 9239.99902
        END_FREQ 9260.00098
    }
    LOCATION
    {
        POSITION_LAT 65.0
        POSITION_LON 26.0
        POSITION_ALT 100.0
        NAME Syn
        GROUND_LEVEL 90.0
        AZIMUTH -5
    }
    HTTP_SERVICE
    {
        ENABLED TRUE
        SERVER_PORT 9080 ; comment
        WEBSOCKET_PORT 9081
        SECURE
        {
            ENABLED FALSE
            PRIVATE_KEY ""
        }
    }
}
"""


@pytest.fixture()
def syn_run(tmp_path):
    xml = tmp_path / "syn.xml"
    xml.write_bytes(SYN)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "IrisRadar.config").write_text(CFG, encoding="utf-8")
    out = tmp_path / "out"
    rep = R.convert(xml, out, cfg, [], quicklook=False, focus_track=1)
    return rep, out


def test_fragment_split_tolerates_truncation():
    frags = list(R.split_fragments(SYN))
    ok = [f for f in frags if f[2] is not None]
    bad = [f for f in frags if f[3] is not None]
    assert len(ok) == 8 and len(bad) == 1
    assert bad[0][1] == SYN.rfind(b"<Robin>")


def test_fragment_rejects_entities():
    evil = b'<Robin><!DOCTYPE x [<!ENTITY a "aaaa">]><Tracks timestamp="2026-01-01T00:00:00Z"/></Robin>'
    (_, _, root, err), = list(R.split_fragments(evil))
    assert root is None and "DOCTYPE" in err


def test_synthetic_tables(syn_run):
    rep, out = syn_run
    tr = pd.read_parquet(out / "tracks.parquet")
    # track 1 state repeated in two messages -> one row, n_msgs=2, alarm conflict flagged
    t1 = tr[tr.track_id == 1]
    assert len(t1) == 1 and int(t1.n_msgs.iloc[0]) == 2 and int(t1.n_dup.iloc[0]) == 1
    assert t1.qflags.iloc[0] & R.QF_DUP_CONFLICT
    assert bool(t1.alarm.iloc[0]) is False          # first report kept
    assert t1.latency_s.iloc[0] == pytest.approx(0.5)
    assert t1.t_msg_last.iloc[0] == pd.Timestamp("2026-10-21T10:00:02Z")
    assert rep["tracks"]["rows_read"] == 3 and rep["tracks"]["rows_kept"] == 2
    assert rep["tracks"]["conflicting_fields"]["alarm"] == 1
    # geometry: track 1 is ~111 m north of the radar, 20 m above it
    assert t1.n_m.iloc[0] == pytest.approx(111.4, abs=1.0)
    assert t1.u_m.iloc[0] == pytest.approx(20.0, abs=1.5)
    assert t1.h_above_site_ground_m.iloc[0] == pytest.approx(30.0)
    assert t1.class_code.iloc[0] == R.CLASS_CODES["DRONE"] and t1.class_id.iloc[0] == 6
    assert pd.isna(tr[tr.track_id == 2].class_id.iloc[0])
    # unknown element and unknown message type are reported, not dropped silently
    assert rep["unknown_paths"]["Tracks"] == {"Tracks/Track/NewThing": 1}
    assert rep["unknown_message_types"] == {"MysteryMessage": 1}
    assert rep["fragments"]["failed"] == 1


def test_synthetic_motion_time_inferred(syn_run):
    rep, out = syn_run
    mo = pd.read_parquet(out / "motion.parquet")
    assert len(mo) == 2 and rep["motion"]["rows_read"] == 2
    assert (mo.qflags & R.QF_TIME_INFERRED).all() and (mo.qflags & R.QF_RAW_TIME_EPOCH0).all()
    assert (mo.qflags & R.QF_POSITION_ZERO).all()
    # second motion msg lies between the 10:00:01 and 10:00:02 Tracks messages
    t = mo.t_utc.iloc[1]
    assert pd.Timestamp("2026-10-21T10:00:01Z") < t < pd.Timestamp("2026-10-21T10:00:02Z")


def test_exact_duplicate_messages_skipped(tmp_path):
    msg = b'<Robin><RadarMotion RadarId="0"><Timestamp>1970-01-01T00:00:00.000Z</Timestamp><Pitch>0.1</Pitch></RadarMotion></Robin>'
    ts = b'<Robin><Tracks timestamp="2026-10-21T10:00:01.000Z"/></Robin>'
    xml = tmp_path / "d.xml"
    xml.write_bytes(ts + msg + msg + ts)
    rep = R.convert(xml, tmp_path / "o", None, [], quicklook=False)
    assert rep["message_exact_duplicates"] == {"RadarMotion": 1, "Tracks": 1}
    assert rep["motion"]["rows_read"] == 1


def test_synthetic_other_tables(syn_run):
    _rep, out = syn_run
    pl = pd.read_parquet(out / "plots.parquet")
    assert len(pl) == 1 and pl.rcs_dbsm.iloc[0] == pytest.approx(4.1)
    im = pd.read_parquet(out / "image_meta.parquet")
    assert list(im.no_data_az_deg.iloc[0]) == [10.25, 14.75] and im.n_no_data_lines.iloc[0] == 2
    st = pd.read_parquet(out / "status.parquet")
    assert st.s_components_gps_hdop.iloc[0] == pytest.approx(0.5)
    assert bool(st.s_components_gps_lock.iloc[0]) is True
    assert st.s_ground_level.iloc[0] == pytest.approx(90.0)


def test_time_columns_are_ns_utc(syn_run):
    _, out = syn_run
    for name in ("tracks", "plots", "status", "motion", "image_meta", "track_summary"):
        sch = pq.read_schema(out / f"{name}.parquet")
        ts = [f for f in sch if pa.types.is_timestamp(f.type)]
        assert ts, name
        for f in ts:
            assert f.type == pa.timestamp("ns", tz="UTC"), (name, f)
        md = json.loads(sch.metadata[b"domeware"])
        assert md["table"] == name and "source_sha256" in md


def test_sorted_by_t_utc(syn_run):
    _, out = syn_run
    tr = pd.read_parquet(out / "tracks.parquet")
    assert tr.t_utc.is_monotonic_increasing


def test_config_parser_and_redaction(syn_run, tmp_path):
    cfg = R.parse_robin_config(CFG)
    assert isinstance(cfg["GENERAL"]["MAPTILEPROVIDER"], list) and len(cfg["GENERAL"]["MAPTILEPROVIDER"]) == 2
    assert cfg["IRISRADAR"]["HTTP_SERVICE"]["SERVER_PORT"] == 9080      # comment stripped
    assert cfg["IRISRADAR"]["FILEPATHS"] == ""
    red = json.dumps(R.redact(cfg))
    assert "TopSecretPw123" not in red and "KeyKeyKey999" not in red
    secrets = R.find_secret_values(CFG)
    assert "TopSecretPw123" in secrets and "KeyKeyKey999" in secrets
    _, out = syn_run
    for p in out.iterdir():
        txt = p.read_bytes()
        assert b"TopSecretPw123" not in txt and b"KeyKeyKey999" not in txt and b"10.0.0.1" not in txt, p
    site = json.loads((out / "site.json").read_text(encoding="utf-8"))
    assert site["radar"]["ground_level_m"] == 90.0
    assert site["config_iris_radar"]["INTERFACES"]["json_api_port"] == 9080
    assert "GENERAL" not in site.get("config_iris_radar", {})


def test_text_header_parser():
    raw = (b";------\n; FileVersion 3.0.4 : ROBIN RADAR SYSTEMS BV (C)\n;-----\n"
           b"; ORIGINAL       : 21-10-2026 10:30:01.455\n;----\nRESULT\n  BLOCK\n    A = 1;\n    B = x y;\n"
           b"    INNER\n      C = 2.5;\n    END_INNER\n    INNER\n      C = 3;\n    END_INNER\n  END_BLOCK\n"
           b"  START_BINARY;\n\x03\x00" + __import__("struct").pack("<d", 1792578545.11) + b"\x00" * 8)
    h = R.parse_text_header(raw)
    assert h["comment_meta"]["file_version"] == "3.0.4"
    assert h["comment_meta"]["original_utc_assumed"].startswith("2026-10-21T10:30:01.455")
    assert h["header"]["RESULT"]["BLOCK"]["A"] == 1 and h["header"]["RESULT"]["BLOCK"]["B"] == "x y"
    assert [x["C"] for x in h["header"]["RESULT"]["BLOCK"]["INNER"]] == [2.5, 3]
    assert h["binary_probe_unverified"]["float64_as_unix_utc"].startswith("2026-10-21T10:29:05")


# ------------------------------------------------------------------ real example run (skipped if absent)
real = pytest.mark.skipif(not REAL_XML.exists(), reason="example run not on this machine")


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("real")
    raw = sorted(ARCHIVE.rglob("*.PLOTS.gz"))
    rep = R.convert(REAL_XML, out, REAL_CFG if REAL_CFG.exists() else None, raw, quicklook=True,
                    focus_track=766186, hash_raw=False)
    return rep, out


@real
def test_real_counts(real_run):
    rep, _ = real_run
    assert rep["message_counts"] == {"RadarMotion": 399, "RadarImageMeta": 199, "Tracks": 390,
                                     "SystemStatus": 66, "PotentialDronePlots": 98}
    assert rep["fragments"]["failed"] == 0
    assert rep["unknown_message_types"] == {} and rep["unknown_paths"] == {}
    t = rep["tracks"]
    assert t["rows_read"] == 1644 and t["unique_tracks"] == 45
    assert t["rows_kept"] + t["duplicates_dropped"] == t["rows_read"]
    assert rep["plots"]["rows_kept"] == 124


@real
def test_real_drone_track(real_run):
    _, out = real_run
    tr = pd.read_parquet(out / "tracks.parquet")
    d = tr[tr.track_id == 766186]
    assert len(d) == 79
    assert d.t_utc.min() == pd.Timestamp("2026-10-21T10:29:46.571Z")
    assert d.t_utc.max() == pd.Timestamp("2026-10-21T10:31:06.478Z")
    assert 95 < d.alt_m.min() < 96 and 108 < d.alt_m.max() < 108.1
    assert set(d.class_name) == {"DRONE", "SUSPECTED_DRONE"}
    assert int(d.n_msgs.sum()) == 217
    s = pd.read_parquet(out / "track_summary.parquet").set_index("track_id")
    assert bool(s.loc[766186, "alarm_any"]) is True
    assert s.loc[766186, "t_first_alarm_state"] == pd.Timestamp("2026-10-21T10:29:46.571Z")  # alarm set on re-send
    assert (out / "quicklook_track_766186_altitude.png").stat().st_size > 10_000


@real
def test_real_no_secrets(real_run):
    if not REAL_CFG.exists():
        pytest.skip("cfg missing")
    _, out = real_run
    secrets = R.find_secret_values((REAL_CFG / "IrisRadar.config").read_text(encoding="utf-8", errors="replace"))
    assert secrets, "expected at least one secret value in the real config"
    for p in out.iterdir():
        b = p.read_bytes()
        for v in secrets:
            assert v.encode() not in b, f"secret leaked into {p.name}"


@real
def test_real_plots_headers(real_run):
    _, out = real_run
    rh = json.loads((out / "raw_headers.json").read_text(encoding="utf-8"))
    plots = [v for v in rh.values() if v.get("kind") == "PLOTS"]
    assert len(plots) == 4
    for v in plots:
        assert v["comment_meta"]["file_version"] == "3.0.4"
        assert v["plot_lists_unverified"]["count_matches_header"] is True
