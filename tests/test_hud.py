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


def test_menu_links_the_report_and_the_kml_only_when_they_exist(server, tmp_path):
    def head(path):
        try:
            with urllib.request.urlopen(urllib.request.Request(server + path, method="HEAD"), timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, b""

    views = tmp_path / "S" / "views"
    assert head("data/report.html")[0] == 404  # not made yet: the menu says how to make it
    (views / "report.html").write_text("<p>report</p>", encoding="utf-8")
    (views / "session.kml").write_text("<kml/>", encoding="utf-8")
    (views / "replay_stats.json").write_text("{}", encoding="utf-8")
    code, ctype, body = head("data/report.html")
    assert (code, body) == (200, b"") and ctype.startswith("text/html")  # HEAD: headers only
    assert head("data/session.kml")[:2] == (200, "application/vnd.google-earth.kml+xml")
    assert _get(server + "data/report.html")[2] == b"<p>report</p>"
    assert _get(server + "data/replay_stats.json")[0] == 404  # the rest of views/ stays private


def test_window_report_is_kept_on_this_computer_and_clamped(server, tmp_path):
    with urllib.request.urlopen(server + "api/ping?w=1536&h=816&dpr=1.25&glass=1&fps=58.4&evil=%3Cx%3E", timeout=10) as r:
        body = json.loads(r.read())
    assert set(body) == {"web", "restart"} and body["restart"] is False
    client = tmp_path / "S" / "views" / hud.CLIENT_FILE
    c = json.loads(client.read_text())
    assert c["window"] == [1536, 816] and c["scale"] == 1.25 and c["playback_fps"] == 60 and c["glass"] is True
    urllib.request.urlopen(server + "api/ping?w=99999&dpr=nan", timeout=10).read()
    c = json.loads(client.read_text())
    assert c["window"] == [10000, 816] and c["scale"] == 1.25  # clamped; nan ignored; nothing else is kept
    assert "evil" not in client.read_text()


def test_open_window_is_told_when_the_code_changed(server, monkeypatch):
    web, py = hud._code_stamp()
    monkeypatch.setattr(hud, "_code_stamp", lambda: (web + 5, py + 5))
    with urllib.request.urlopen(server + "api/ping", timeout=10) as r:
        assert json.loads(r.read()) == {"web": web + 5, "restart": True}


def test_view_address_matches_what_the_page_reads():
    assert hud.view_query() == ""
    assert hud.view_query(at="13:45:10", panels="over,radar", hide=True, shot=True) == \
        "?t=13:45:10&open=over,radar&panels=0&shot=1"


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
