"""The two apps' Robin readers agree (domeware.parity): the first of the parity checks the combined build grows."""
import shutil

import pytest

from domeware import parity

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node to run Orbit's reader")


def test_both_apps_read_the_synthetic_radar_file_the_same_way():
    r = parity.robin()
    assert r["verdict"] == "IDENTICAL", r["report"]
    assert r["numbers"]["updates_same"] == 193 and r["numbers"]["only_domeware"] == r["numbers"]["only_orbit"] == 0


def test_a_reader_that_fails_is_reported_not_hidden():
    # Known difference (combine/README.md): Orbit's synthetic file names its tracks (B1, R3, ...), real Robin files
    # number them, and domeware's reader takes numbers only. When the combined reader takes both, change this test.
    r = parity.robin(parity.SYNTHETIC, map_ids=False)
    assert r["verdict"] == "DIFFERENT"
    assert "domeware's reader failed" in r["report"] and "'B1'" in r["report"]


def test_track_names_become_numbers_in_order():
    text, names = parity.numeric_ids('<Track id="B1"/><Track id="7"/><Track id="R3"/><Track id="B1"/>')
    assert names == {"B1": 900001, "R3": 900002}
    assert text == '<Track id="900001"/><Track id="7"/><Track id="900002"/><Track id="900001"/>'
