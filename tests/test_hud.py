"""The HUD: its data has no NaN, and its server hands out only the page, the viewer and this session's replay."""

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from domeware import hud
from domeware.overview import WEB_VIEWER
from domeware.session import Session


def test_segments_and_json_safe_values():
    t = np.array([0, 1000, 2000, 6000, 7000], dtype=np.int64)
    assert hud._segments(t, np.array([True, True, False, True, True])) == [[0, 1000], [6000, 7000]]
    assert hud._vals([1.234, np.nan, np.inf, 2]) == [1.2, None, None, 2.0]


@pytest.fixture
def server(tmp_path):
    if not (WEB_VIEWER / "re_viewer_bg.wasm").exists():
        pytest.skip("web viewer not on this laptop (dw setup)")
    s = Session(tmp_path / "S")
    s.save()
    (s.views / "hud").mkdir(parents=True)
    (s.views / "replay.rrd").write_bytes(b"RRD")
    (s.views / "hud" / "data.json").write_text(json.dumps({"ok": 1}))
    (s.views / "secret.txt").write_text("not for the page")
    srv, url, _ = hud.make_server(s)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield url
    srv.shutdown()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read(64)
    except urllib.error.HTTPError as e:
        return e.code, None, b""


def test_server_serves_the_page_the_viewer_and_this_replay_only(server):
    assert server.startswith("http://127.0.0.1:")
    assert _get(server)[0] == 200 and _get(server + "data/hud/data.json")[2] == b'{"ok": 1}'
    assert _get(server + "data/replay.rrd")[:2] == (200, "application/octet-stream")
    assert _get(server + "viewer/re_viewer")[1] == "text/javascript"  # imported without its extension
    assert _get(server + "viewer/re_viewer_bg.wasm")[1] == "application/wasm"
    for bad in ("data/secret.txt", "data/../manifest.json", "data/%2e%2e/manifest.json", "app/../hud.py",
                "viewer/../package.json", "data/hud/../../manifest.json",
                "data/hud/..%2fsecret.txt", "data/hud/%2e%2e%2fsecret.txt",  # review: hud/..%2f reached views/
                "viewer///host/share/x", "viewer/%5C%5Chost%5Cshare%5Cx", "app/C:%5CWindows%5Cwin.ini"):
        assert _get(server + bad)[0] == 404, bad


def test_closing_window_says_bye(server):
    req = urllib.request.Request(server + "api/bye", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 204


def test_airborne_is_counted_as_dw_compare_counts_it():
    """A log running on the ground before take-off and after landing is not flight (review finding)."""
    has_fix = np.ones(9, dtype=bool)
    height = np.array([0, 0.5, 1, 3, 10, 12, 3, 0.4, 0])
    assert hud._airborne(has_fix, height).tolist() == [False] * 3 + [True] * 4 + [False] * 2
    assert hud._airborne(has_fix, None).all()
