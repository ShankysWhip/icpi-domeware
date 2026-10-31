"""dw shots: sizes, the default from the window the HUD last reported, and the tiny WebSocket client."""
import json
import os
import struct
from pathlib import Path

import pytest

from domeware import cli, hud, overview, shots
from domeware.session import Session


def test_sizes_are_read_as_people_write_them():
    assert shots.parse_size("1720x988") == (1720, 988, 1.0)
    assert shots.parse_size(" 1536 x 816 @1.25 ") == (1536, 816, 1.25)
    for bad in ("1720", "big", "1536x816@9"):
        with pytest.raises(ValueError):
            shots.parse_size(bad)


def test_default_size_is_the_window_the_hud_last_reported(tmp_path):
    s = Session(tmp_path / "S")
    s.save()
    assert shots.last_size(s) == shots.DEFAULT_SIZE
    s.views.mkdir(parents=True, exist_ok=True)
    (s.views / hud.CLIENT_FILE).write_text(json.dumps({"window": [1536, 816], "scale": 1.25}))
    assert shots.last_size(s) == (1536, 816, 1.25)


class _FakeSock:
    def __init__(self, data: bytes):
        self.data, self.sent = bytearray(data), b""

    def recv(self, n):
        out = bytes(self.data[:n])
        del self.data[:n]
        return out

    def sendall(self, b):
        self.sent += b


def _server_frame(payload: bytes, op: int = 1, fin: bool = True) -> bytes:
    n = len(payload)
    size = bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 1 << 16 else bytes([127]) + struct.pack(">Q", n)
    return bytes([(0x80 if fin else 0) | op]) + size + payload


def test_websocket_reads_long_split_and_pinged_messages():
    big = "x" * 70000  # a screenshot answer is megabytes: the 64-bit length path
    data = (_server_frame(b"ping!", op=9) + _server_frame(b'{"a":', fin=False) + _server_frame(b" 1}", op=0)
            + _server_frame(big.encode()))
    ws = shots.WebSocket(sock=_FakeSock(data))
    assert ws.recv() == '{"a": 1}'
    assert ws.recv() == big
    assert ws.sock.sent[:1] == bytes([0x8A])  # the ping was answered with a pong


def test_client_frames_are_masked_and_decode_back():
    for n in (5, 300, 70000):
        f = shots.encode_frame(b"y" * n)
        assert f[0] == 0x81 and f[1] & 0x80  # a final text frame, masked as clients must
        k = 2 + (2 if 126 <= n < 1 << 16 else 8 if n >= 1 << 16 else 0)
        mask, body = f[k:k + 4], f[k + 4:]
        assert bytes(b ^ mask[i % 4] for i, b in enumerate(body)) == b"y" * n


def test_a_session_can_be_named_without_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(overview, "DEFAULT_ROOT", tmp_path)
    (tmp_path / "S1").mkdir()
    assert cli._session_path(Path("S1")) == tmp_path / "S1"
    assert cli._session_path(Path("not_there")) == Path("not_there")


@pytest.mark.skipif(not os.environ.get("DW_EDGE_TESTS"), reason="opens a hidden Edge: set DW_EDGE_TESTS=1")
def test_shots_take_a_real_picture(tmp_path):
    sim = overview.DEFAULT_ROOT / "SIM_ouluzone_phases"
    if not (sim / "views" / "hud" / "data.json").exists():
        pytest.skip("the simulated session is not on this laptop")
    files = shots.take(Session(sim), sizes=[(1280, 800, 1.0)], panels="over", out=tmp_path, wait_s=60)
    assert len(files) == 1 and files[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
