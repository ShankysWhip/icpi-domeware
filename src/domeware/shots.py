"""dw shots: full-resolution pictures of the HUD as its user sees it, for a review in chat.

  dw shots SESSION                          at the window size and screen scale the HUD last reported
  dw shots SESSION --size 1536x816@1.25     any size and screen scale; --size can repeat
  dw shots SESSION --at 13:45:10 --open over,radar --menu

Serves the HUD on 127.0.0.1, opens it in a hidden Microsoft Edge with its own empty profile (removed afterwards),
waits until the 3D has drawn and saves PNGs in views/shots/. Nothing leaves this computer. Edge is driven through its
DevTools protocol on 127.0.0.1; the tiny WebSocket client below is all that needs (no extra packages).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from domeware import hud
from domeware.session import Session

DEFAULT_SIZE = (1720, 988, 1.0)  # Edge's --app window from dw app, inside its frame


def parse_size(text: str) -> tuple[int, int, float]:
    m = re.fullmatch(r"\s*(\d{3,5})\s*[x×]\s*(\d{3,5})\s*(?:@\s*(\d+(?:\.\d+)?))?\s*", text)
    if not m:
        raise ValueError(f"size {text!r}: write it as 1720x988, or 1536x816@1.25 with the screen scale")
    w, h, scale = int(m[1]), int(m[2]), float(m[3] or 1)
    if not 0.5 <= scale <= 4:
        raise ValueError(f"screen scale {scale}: use 0.5 to 4")
    return w, h, scale


def last_size(s: Session) -> tuple[int, int, float]:
    """The window size and screen scale the HUD last reported on this computer, else a large laptop window."""
    try:
        c = json.loads((s.views / hud.CLIENT_FILE).read_text(encoding="utf-8"))
        w, h = c["window"]
        return int(w), int(h), float(c.get("scale") or 1)
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_SIZE


def encode_frame(payload: bytes, opcode: int = 1) -> bytes:
    """One final WebSocket frame from a client (clients must mask)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class WebSocket:
    """Just enough RFC 6455 for DevTools on 127.0.0.1: masked text frames out, whole messages in."""

    def __init__(self, url: str | None = None, timeout: float = 30.0, sock=None):
        self.buf = bytearray()
        if sock is not None:  # tests hand in a fake socket
            self.sock = sock
            return
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            self._more()
        head, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(f"DevTools refused the connection: {head.splitlines()[0]!r}")
        self.buf = bytearray(rest)

    def _more(self) -> None:
        chunk = self.sock.recv(1 << 16)
        if not chunk:
            raise ConnectionError("DevTools closed the connection")
        self.buf += chunk

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._more()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode("utf-8")))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            op = b0 & 0x0F
            if op == 8:
                raise ConnectionError("DevTools closed the connection")
            if op == 9:  # ping
                self.sock.sendall(encode_frame(data, opcode=10))
                continue
            if op == 10:
                continue
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode("utf-8")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _Tab:
    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:  # events in between are not needed
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def js(self, expression: str):
        return self.call("Runtime.evaluate", expression=expression, returnByValue=True).get("result", {}).get("value")


def _devtools(profile: Path, proc: subprocess.Popen, timeout: float = 20.0) -> str:
    """The page's DevTools address, once Edge has written its port (port 0 lets it pick a free one)."""
    port_file = profile / "DevToolsActivePort"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SystemExit("Edge stopped before it was ready")
        try:
            port = int(port_file.read_text().split()[0])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                pages = [t for t in json.load(r) if t.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.2)
    raise SystemExit("Edge did not open its DevTools port in time")


def _name(w: int, h: int, scale: float, at: str | None, panels: str | None, hide: bool, menu: bool) -> str:
    bits = [f"hud_{w}x{h}" + (f"@{scale:g}" if scale != 1 else "")]
    if at:
        bits.append(at.replace(":", ""))
    if panels:
        bits.append(panels.replace(",", "-"))
    bits += ["map"] * hide + ["menu"] * menu
    return re.sub(r"[^A-Za-z0-9@._-]", "", "_".join(bits)) + ".png"


def take(s: Session, *, sizes: list[tuple[int, int, float]] | None = None, at: str | None = None,
         panels: str | None = None, hide: bool = False, menu: bool = False, wait_s: float = 60.0,
         out: Path | None = None) -> list[Path]:
    edge = hud.edge_path()
    if not edge:
        raise SystemExit("Microsoft Edge was not found: dw shots needs it")
    hud.ensure_built(s)
    sizes = sizes or [last_size(s)]
    out = out or s.views / "shots"
    out.mkdir(parents=True, exist_ok=True)
    server, url, _ = hud.make_server(s)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = Path(tempfile.mkdtemp(prefix="dw-shots-"))
    w0, h0, _ = sizes[0]
    proc = subprocess.Popen([edge, "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-port=0",
                             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                             "--hide-scrollbars", f"--window-size={w0},{h0}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    saved = []
    try:
        tab = _Tab(_devtools(profile, proc))
        page = url + hud.view_query(at=at, panels=panels, hide=hide, menu=menu, shot=True)
        for w, h, scale in sizes:
            tab.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=scale, mobile=False)
            tab.call("Page.navigate", url=page)
            end = time.monotonic() + wait_s
            drawn = False
            while time.monotonic() < end:
                state = tab.js("window.__hud && window.__hud.R ? (window.__hud.V.ready ? 2 : 1) : 0")
                if state == 2:
                    drawn = True
                    break
                time.sleep(0.4)
            time.sleep(2.5 if drawn else 0.5)  # the viewer draws a few frames; transitions are off in shot mode
            png = base64.b64decode(tab.call("Page.captureScreenshot", format="png")["data"])
            f = out / _name(w, h, scale, at, panels, hide, menu)
            f.write_bytes(png)
            saved.append(f)
            if not drawn:
                print(f"{f.name}: the 3D had not drawn after {wait_s:.0f} s; the picture shows the panels only")
        tab.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)  # our own temporary profile, nothing else
    return saved
