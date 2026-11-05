#!/usr/bin/env python3
"""Orbit helper (Windows app; also runs on Mac/Linux for development).

It serves the app UI on 127.0.0.1 and does the jobs a web page cannot do on its own:
  * caches map tiles on disk so maps keep working offline, and saves areas for offline use;
  * forwards DJI keychain requests (DJI's API refuses calls made from a web page);
  * receives a live radar feed (TCP client, TCP listener, UDP, or a file that keeps growing);
  * opens and saves files with the system dialogs, and opens files passed by Windows.

Only the Python standard library is used. Usage:
  pythonw orbit_server.py [files...]        # Windows app: starts and opens an Edge app window
  python3 orbit_server.py --dev             # development: also serves /tests, opens Chrome
  python3 orbit_server.py --no-browser --port 47615 --print-url
"""
import argparse
import base64
import datetime as dt
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

APP = 'Orbit'
VERSION = '2.5.1'
HERE = os.path.dirname(os.path.abspath(__file__))
DJI_ENDPOINT = 'https://dev.dji.com/openapi/v1/flight-records/keychains'
USER_AGENT = APP + '/' + VERSION + ' (flight data replay desktop app)'

# name: (url template, file extension, max zoom)
TILE_SOURCES = {
    'esri-imagery': ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', 'jpg', 19),
    'esri-labels': ('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', 'png', 19),
    'esri-topo': ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', 'jpg', 19),
    'osm': ('https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'png', 19),
    'opentopo': ('https://tile.opentopomap.org/{z}/{x}/{y}.png', 'png', 17),
    'terrain': ('https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png', 'png', 15),
}
PREFETCH_LIMIT = 30000
TILE_MAX_AGE = 90 * 86400


def data_dir():
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    # ORBIT_DATA keeps tests away from the real data folder
    d = os.environ.get('ORBIT_DATA') or os.path.join(base, APP)
    os.makedirs(d, exist_ok=True)
    return d


def web_dir():
    for cand in (os.path.join(HERE, 'web'), os.path.join(HERE, '..', 'web')):
        if os.path.isfile(os.path.join(cand, 'index.html')):
            return os.path.abspath(cand)
    raise SystemExit('web folder with index.html not found next to the helper')


DATA = data_dir()
LOG_PATH = os.path.join(DATA, 'helper.log')


def log(*parts):
    line = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(p) for p in parts)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + '.1')
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass
    if sys.stdout and not sys.executable.lower().endswith('pythonw.exe'):
        try:
            print(line, flush=True)
        except Exception:
            pass


def ssl_context():
    ctx = ssl.create_default_context()
    return ctx


# --------------------------------------------------------------------------- settings
class Settings:
    def __init__(self):
        self.path = os.path.join(DATA, 'settings.json')
        self.lock = threading.Lock()

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def merge(self, patch):
        with self.lock:
            cur = self.load()
            for k, v in patch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(cur, fh, indent=1)
            os.replace(tmp, self.path)
            return cur


SETTINGS = Settings()


# --------------------------------------------------------------------------- network state
class Net:
    down_until = 0.0
    last_error = ''
    last_ok = 0.0

    @classmethod
    def online(cls):
        return time.time() >= cls.down_until

    @classmethod
    def failed(cls, err):
        cls.down_until = time.time() + 20
        cls.last_error = str(err)

    @classmethod
    def ok(cls):
        cls.down_until = 0
        cls.last_ok = time.time()


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return r.status, r.read(), r.headers.get('Content-Type', '')


# --------------------------------------------------------------------------- tiles
TILE_DIR = os.path.join(DATA, 'tiles')


def tile_path(src, z, x, y):
    return os.path.join(TILE_DIR, src, str(z), str(x), '%d.%s' % (y, TILE_SOURCES[src][1]))


def sniff_type(b):
    if b[:4] == b'\x89PNG':
        return 'image/png'
    if b[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if b[:4] == b'RIFF':
        return 'image/webp'
    return 'application/octet-stream'


def fetch_tile(src, z, x, y, force_net=False):
    """Returns (bytes, content_type, status) from the disk cache or the network."""
    path = tile_path(src, z, x, y)
    cached = None
    if os.path.exists(path):
        try:
            with open(path, 'rb') as fh:
                cached = fh.read()
        except OSError:
            cached = None
        if cached and (time.time() - os.path.getmtime(path) < TILE_MAX_AGE or not Net.online()):
            return cached, sniff_type(cached), 200
    if not Net.online() and not force_net:
        return (cached, sniff_type(cached), 200) if cached else (None, None, 504)
    url = TILE_SOURCES[src][0].format(z=z, x=x, y=y)
    try:
        status, body, _ = http_get(url, timeout=8)
        Net.ok()
        if status == 200 and body:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.%d.tmp' % threading.get_ident()
            with open(tmp, 'wb') as fh:
                fh.write(body)
            os.replace(tmp, path)
            return body, sniff_type(body), 200
        return (cached, sniff_type(cached), 200) if cached else (None, None, 404)
    except urllib.error.HTTPError as e:
        Net.ok()
        return (cached, sniff_type(cached), 200) if cached else (None, None, e.code if e.code in (403, 404) else 502)
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        Net.failed(e)
        return (cached, sniff_type(cached), 200) if cached else (None, None, 504)


def tile_ranges(bbox, zmin, zmax):
    """(z, x0, x1, y0, y1) for each zoom; cheap to count before listing any tiles."""
    import math
    w, s, e, n = bbox
    out = []
    for z in range(zmin, zmax + 1):
        def tx(lon):
            return int((lon + 180) / 360 * 2 ** z)

        def ty(lat):
            lat = max(-85.0511, min(85.0511, lat))
            r = math.radians(lat)
            return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)
        x0, x1 = max(0, tx(w)), min(2 ** z - 1, tx(e))
        y0, y1 = max(0, ty(n)), min(2 ** z - 1, ty(s))
        if x1 >= x0 and y1 >= y0:
            out.append((z, x0, x1, y0, y1))
    return out


class Prefetch:
    lock = threading.Lock()
    state = {'running': False, 'done': 0, 'total': 0, 'failed': 0, 'cached': 0, 'bytes': 0, 'message': ''}
    cancel = threading.Event()

    @classmethod
    def start(cls, sources, bbox, zmin, zmax):
        plan = []
        for src in sources:
            if src not in TILE_SOURCES:
                continue
            zt = min(zmax, TILE_SOURCES[src][2])
            if src == 'osm':
                zt = min(zt, 16)  # OSM's tile policy forbids bulk downloads at zoom 17 and above
            plan += [(src,) + r for r in tile_ranges(bbox, zmin, zt)]
        total = sum((x1 - x0 + 1) * (y1 - y0 + 1) for (_, _, x0, x1, y0, y1) in plan)
        if not total:
            return {'ok': False, 'error': 'Nothing to download for that area.'}
        if total > PREFETCH_LIMIT:
            return {'ok': False, 'error': 'That is %d tiles; the limit is %d. Zoom in or lower the detail.' % (total, PREFETCH_LIMIT), 'total': total}
        jobs = [(src, z, x, y) for (src, z, x0, x1, y0, y1) in plan for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        with cls.lock:
            if cls.state['running']:
                return {'ok': False, 'error': 'A download is already running.'}
            cls.state = {'running': True, 'done': 0, 'total': len(jobs), 'failed': 0, 'cached': 0, 'bytes': 0, 'message': ''}
            cls.cancel.clear()
        threading.Thread(target=cls._run, args=(jobs,), daemon=True).start()
        return {'ok': True, 'total': len(jobs)}

    @classmethod
    def _run(cls, jobs):
        idx = {'i': 0}
        lk = threading.Lock()

        def worker():
            while not cls.cancel.is_set():
                with lk:
                    if idx['i'] >= len(jobs):
                        return
                    job = jobs[idx['i']]
                    idx['i'] += 1
                src, z, x, y = job
                path = tile_path(src, z, x, y)
                if os.path.exists(path):
                    with cls.lock:
                        cls.state['done'] += 1
                        cls.state['cached'] += 1
                    continue
                body, _, status = fetch_tile(src, z, x, y, force_net=True)
                with cls.lock:
                    cls.state['done'] += 1
                    if status == 200 and body:
                        cls.state['bytes'] += len(body)
                    else:
                        cls.state['failed'] += 1
                time.sleep(0.02)
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with cls.lock:
            cls.state['running'] = False
            cls.state['message'] = 'cancelled' if cls.cancel.is_set() else 'finished'
        log('prefetch', cls.state)


def cache_stats():
    total, count, by = 0, 0, {}
    if os.path.isdir(TILE_DIR):
        for src in os.listdir(TILE_DIR):
            b = n = 0
            for root, _, files in os.walk(os.path.join(TILE_DIR, src)):
                for f in files:
                    try:
                        b += os.path.getsize(os.path.join(root, f))
                        n += 1
                    except OSError:
                        pass
            by[src] = {'bytes': b, 'count': n}
            total += b
            count += n
    return {'bytes': total, 'count': count, 'bySource': by}


# --------------------------------------------------------------------------- DJI keychains
KEY_DIR = os.path.join(DATA, 'dji-keychains')


def safe_id(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(s))[:120]


def dji_keychains(body):
    req = body.get('request')
    api_key = (body.get('apiKey') or SETTINGS.load().get('djiApiKey') or '').strip()
    if not api_key:
        return 400, {'ok': False, 'error': 'No DJI API key set. Add it in Settings.'}
    if not isinstance(req, dict):
        return 400, {'ok': False, 'error': 'Missing keychain request.'}
    data = json.dumps(req).encode()
    r = urllib.request.Request(DJI_ENDPOINT, data=data, method='POST', headers={
        'Content-Type': 'application/json', 'Api-Key': api_key, 'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(r, timeout=30, context=ssl_context()) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
        Net.ok()
    except urllib.error.HTTPError as e:
        Net.ok()
        detail = e.read().decode('utf-8', 'replace')[:400]
        msg = 'DJI refused the API key (HTTP 403).' if e.code == 403 else 'DJI answered HTTP %d.' % e.code
        return 502, {'ok': False, 'error': msg, 'status': e.code, 'detail': detail}
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        Net.failed(e)
        return 504, {'ok': False, 'error': 'Could not reach DJI (%s). Check the internet connection.' % e}
    except ValueError:
        return 502, {'ok': False, 'error': 'DJI sent an answer that is not JSON.'}
    result = payload.get('result') or {}
    if result.get('code', 0) != 0:
        return 502, {'ok': False, 'error': 'DJI: %s' % (result.get('msg') or 'error %s' % result.get('code')), 'status': result.get('code')}
    keychains = payload.get('data')
    if not keychains:
        return 502, {'ok': False, 'error': 'DJI sent no keys for this log.'}
    if body.get('id'):
        os.makedirs(KEY_DIR, exist_ok=True)
        with open(os.path.join(KEY_DIR, safe_id(body['id']) + '.json'), 'w', encoding='utf-8') as fh:
            json.dump({'id': body['id'], 'saved': time.time(), 'data': keychains}, fh)
    return 200, {'ok': True, 'data': keychains}


# --------------------------------------------------------------------------- live radar feed
class Live:
    lock = threading.Lock()
    items = []
    seq = 0
    status = {'state': 'stopped', 'message': '', 'bytes': 0, 'messages': 0, 'peer': '', 'mode': '', 'record': ''}
    stop_ev = threading.Event()
    thread = None
    record_fh = None
    MAX_ITEMS = 20000

    @classmethod
    def set(cls, **kw):
        with cls.lock:
            cls.status.update(kw)

    @classmethod
    def emit(cls, text):
        with cls.lock:
            cls.seq += 1
            cls.items.append((cls.seq, int(time.time() * 1000), text))
            if len(cls.items) > cls.MAX_ITEMS:
                del cls.items[:len(cls.items) - cls.MAX_ITEMS]
            cls.status['messages'] += 1
        if cls.record_fh:
            try:
                cls.record_fh.write(text + '\n')
                cls.record_fh.flush()
            except OSError:
                pass

    @classmethod
    def poll(cls, since, limit=3000):
        with cls.lock:
            out = [{'seq': s, 't': t, 'xml': x} for (s, t, x) in cls.items if s > since][:limit]
            return {'status': dict(cls.status), 'seq': cls.seq, 'items': out}

    @classmethod
    def start(cls, cfg):
        cls.stop()
        mode = cfg.get('mode')
        if mode not in ('tcp-client', 'tcp-server', 'udp', 'file'):
            return {'ok': False, 'error': 'Unknown live mode.'}
        cls.stop_ev = threading.Event()
        rec_dir = os.path.join(DATA, 'live')
        os.makedirs(rec_dir, exist_ok=True)
        rec = os.path.join(rec_dir, 'live_%s.xml' % dt.datetime.now().strftime('%Y%m%d_%H%M%S'))
        try:
            cls.record_fh = open(rec, 'w', encoding='utf-8')
        except OSError:
            cls.record_fh, rec = None, ''
        with cls.lock:
            cls.items = []
            cls.status = {'state': 'starting', 'message': '', 'bytes': 0, 'messages': 0, 'peer': '', 'mode': mode, 'record': rec}
        cls.thread = threading.Thread(target=cls._run, args=(cfg, cls.stop_ev), daemon=True)
        cls.thread.start()
        return {'ok': True, 'record': rec}

    @classmethod
    def stop(cls):
        cls.stop_ev.set()
        if cls.thread and cls.thread.is_alive():
            cls.thread.join(timeout=3)
        cls.thread = None
        if cls.record_fh:
            try:
                cls.record_fh.close()
            except OSError:
                pass
            cls.record_fh = None
        cls.set(state='stopped')
        return {'ok': True}

    @classmethod
    def _run(cls, cfg, stop):
        mode = cfg['mode']
        try:
            if mode == 'tcp-client':
                cls._tcp_client(cfg, stop)
            elif mode == 'tcp-server':
                cls._tcp_server(cfg, stop)
            elif mode == 'udp':
                cls._udp(cfg, stop)
            else:
                cls._file(cfg, stop)
        except Exception as e:  # keep the helper alive whatever the feed does
            log('live error', repr(e))
            cls.set(state='error', message=str(e))

    @classmethod
    def _consume(cls, splitter, data):
        with cls.lock:
            cls.status['bytes'] += len(data)
        for msg in splitter.feed(data):
            cls.emit(msg)

    @classmethod
    def _tcp_client(cls, cfg, stop):
        host, port = cfg.get('host') or '127.0.0.1', int(cfg.get('port') or 0)
        while not stop.is_set():
            cls.set(state='connecting', message='Connecting to %s:%d' % (host, port), peer='')
            try:
                s = socket.create_connection((host, port), timeout=5)
            except OSError as e:
                cls.set(state='retrying', message='Cannot connect to %s:%d (%s). Retrying.' % (host, port, e))
                stop.wait(3)
                continue
            s.settimeout(1.0)
            cls.set(state='connected', message='Connected to %s:%d' % (host, port), peer='%s:%d' % (host, port))
            sp = XmlSplitter()
            try:
                while not stop.is_set():
                    try:
                        data = s.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    cls._consume(sp, data)
            except OSError as e:
                cls.set(message='Connection lost: %s' % e)
            finally:
                s.close()
            if not stop.is_set():
                cls.set(state='retrying', message='Disconnected from %s:%d. Reconnecting.' % (host, port))
                stop.wait(2)

    @classmethod
    def _tcp_server(cls, cfg, stop):
        port = int(cfg.get('port') or 0)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((cfg.get('bind') or '0.0.0.0', port))
        srv.listen(4)
        srv.settimeout(1.0)
        cls.set(state='listening', message='Waiting for the radar to connect to port %d' % port)
        try:
            while not stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                conn.settimeout(1.0)
                cls.set(state='connected', message='Radar connected from %s' % addr[0], peer='%s:%d' % addr)
                sp = XmlSplitter()
                try:
                    while not stop.is_set():
                        try:
                            data = conn.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        cls._consume(sp, data)
                except OSError:
                    pass
                finally:
                    conn.close()
                cls.set(state='listening', message='Radar disconnected; waiting on port %d' % port, peer='')
        finally:
            srv.close()

    @classmethod
    def _udp(cls, cfg, stop):
        port = int(cfg.get('port') or 0)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((cfg.get('bind') or '0.0.0.0', port))
        s.settimeout(1.0)
        cls.set(state='listening', message='Listening for UDP on port %d' % port)
        sp = XmlSplitter()
        try:
            while not stop.is_set():
                try:
                    data, addr = s.recvfrom(65535)
                except socket.timeout:
                    continue
                cls.set(state='connected', peer='%s:%d' % addr, message='Receiving from %s' % addr[0])
                cls._consume(sp, data)
        finally:
            s.close()

    @classmethod
    def _file(cls, cfg, stop):
        path = cfg.get('path') or ''
        from_start = bool(cfg.get('fromStart'))
        sp = XmlSplitter()
        pos = None
        while not stop.is_set():
            try:
                size = os.path.getsize(path)
            except OSError:
                cls.set(state='retrying', message='Waiting for the file %s' % path)
                stop.wait(1)
                continue
            if pos is None:
                pos = 0 if from_start else size
                cls.set(state='connected', message='Following %s' % os.path.basename(path), peer=path)
            if size < pos:  # truncated or replaced
                pos = 0
                sp = XmlSplitter()
            if size > pos:
                with open(path, 'rb') as fh:
                    fh.seek(pos)
                    data = fh.read(min(size - pos, 8 << 20))
                pos += len(data)
                cls._consume(sp, data)
                continue
            stop.wait(0.5)


class XmlSplitter:
    """Cuts a byte stream into complete top-level XML elements (e.g. <Robin>...</Robin>)."""

    def __init__(self):
        self.buf = ''
        self.dec = None

    def feed(self, data):
        import codecs
        if self.dec is None:
            self.dec = codecs.getincrementaldecoder('utf-8')('replace')
        self.buf += self.dec.decode(data)
        out = []
        while True:
            i = self.buf.find('<')
            if i < 0:
                self.buf = ''
                break
            if i:
                self.buf = self.buf[i:]
            if self.buf.startswith('<?') or self.buf.startswith('<!'):
                j = self.buf.find('>')
                if j < 0:
                    break
                self.buf = self.buf[j + 1:]
                continue
            m = re.match(r'<([A-Za-z_][\w.:-]*)', self.buf)
            if not m:
                self.buf = self.buf[1:]
                continue
            name = m.group(1)
            head_end = self.buf.find('>')
            if head_end < 0:
                break
            if self.buf[head_end - 1] == '/':
                out.append(self.buf[:head_end + 1])
                self.buf = self.buf[head_end + 1:]
                continue
            close = '</' + name + '>'
            j = self.buf.find(close)
            if j < 0:
                if len(self.buf) > 16 << 20:
                    self.buf = ''
                break
            out.append(self.buf[:j + len(close)])
            self.buf = self.buf[j + len(close):]
        return out


# --------------------------------------------------------------------------- native dialogs
def _win_dialog(save, title, filters, multiple=False, default_name=''):
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                    ('lpstrFilter', wintypes.LPCWSTR), ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
                    ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR), ('nMaxFile', wintypes.DWORD),
                    ('lpstrFileTitle', wintypes.LPWSTR), ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
                    ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD), ('nFileOffset', wintypes.WORD),
                    ('nFileExtension', wintypes.WORD), ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
                    ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR), ('pvReserved', ctypes.c_void_p),
                    ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD)]
    ctypes.windll.ole32.CoInitializeEx(None, 2)
    buf = ctypes.create_unicode_buffer(default_name, 65536)
    flt = ''.join('%s\0%s\0' % (n, p) for n, p in filters) + '\0'
    # the filter is a list of NUL-separated strings, so pass it as an explicit buffer
    flt_buf = ctypes.create_unicode_buffer(len(flt) + 1)
    flt_buf[:len(flt)] = flt
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    ofn.lpstrFilter = ctypes.cast(flt_buf, wintypes.LPCWSTR)
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = 65536
    ofn.lpstrTitle = title
    ext = os.path.splitext(default_name)[1].lstrip('.')
    ofn.lpstrDefExt = ext or None
    if save:
        ofn.Flags = 0x00080000 | 0x00000002 | 0x00000008 | 0x00000800  # explorer, overwrite prompt, no chdir, path must exist
        ok = ctypes.windll.comdlg32.GetSaveFileNameW(ctypes.byref(ofn))
    else:
        ofn.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008 | (0x00000200 if multiple else 0)
        ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return []
    raw = ctypes.wstring_at(ctypes.addressof(buf), 65536)
    parts = raw.split('\0')
    parts = parts[:parts.index('')] if '' in parts else parts
    if len(parts) > 1:
        return [os.path.join(parts[0], p) for p in parts[1:]]
    return parts


def _mac_dialog(save, title, default_name=''):
    if save:
        script = 'POSIX path of (choose file name with prompt %s default name %s)' % (json.dumps(title), json.dumps(default_name))
    else:
        script = 'POSIX path of (choose file with prompt %s)' % json.dumps(title)
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    p = r.stdout.strip()
    return [p] if r.returncode == 0 and p else []


def native_dialog(save, title, filters=None, multiple=False, default_name=''):
    filters = filters or [('All files', '*.*')]
    try:
        if sys.platform == 'win32':
            return _win_dialog(save, title, filters, multiple, default_name)
        if sys.platform == 'darwin':
            return _mac_dialog(save, title, default_name)
        cmd = ['zenity', '--file-selection', '--title=' + title] + (['--save', '--confirm-overwrite', '--filename=' + default_name] if save else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        return [r.stdout.strip()] if r.returncode == 0 and r.stdout.strip() else []
    except Exception as e:
        log('dialog failed', repr(e))
        raise


def html_to_pdf(html_bytes, out):
    """Prints the report HTML to PDF with headless Edge (or Chrome). Returns (ok, error)."""
    import pathlib
    import tempfile
    exe = find_browser()
    if not exe:
        return False, 'Neither Microsoft Edge nor Google Chrome was found to make the PDF.'
    tmp = tempfile.mkdtemp(prefix='orbit-pdf-')
    try:
        src = os.path.join(tmp, 'report.html')
        with open(src, 'wb') as fh:
            fh.write(html_bytes)
        if os.path.exists(out):
            os.remove(out)
        args = [exe, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                '--user-data-dir=' + os.path.join(tmp, 'profile'), '--no-pdf-header-footer', '--print-to-pdf-no-header',
                '--print-to-pdf=' + out, pathlib.Path(src).as_uri()]
        kw = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}
        r = subprocess.run(args, capture_output=True, timeout=120, **kw)
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            return True, None
        log('pdf failed', r.returncode, (r.stderr or b'')[-400:])
        return False, 'The browser could not print the report (code %s).' % r.returncode
    except subprocess.TimeoutExpired:
        return False, 'Printing the report took too long.'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def open_file(path):
    try:
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    except OSError:
        return False


def reveal(path):
    try:
        if sys.platform == 'win32':
            subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path)])
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- HTTP
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
        '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg',
        '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8', '.map': 'application/json', '.xml': 'application/xml',
        '.csv': 'text/csv; charset=utf-8'}


class State:
    token = secrets.token_urlsafe(24)
    port = 0
    web = ''
    tests = ''
    open_queue = []
    last_ping = time.time()
    pinged = False
    server = None
    no_browser = False
    browsers = []  # the window processes this helper started
    lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = APP + '/' + VERSION
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    # ---- helpers
    def _host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in ('127.0.0.1:%d' % State.port, 'localhost:%d' % State.port)

    def _auth(self):
        tok = self.headers.get('X-SF-Token') or urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token', [''])[0]
        return secrets.compare_digest(tok, State.token)

    def _send(self, status, body=b'', ctype='application/json', headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _read_body(self):
        # Always drain the body: on a kept-alive connection unread bytes would corrupt the next request.
        if getattr(self, '_raw', None) is None:
            n = int(self.headers.get('Content-Length') or 0)
            self._raw = self.rfile.read(n) if n > 0 else b''
        return self._raw

    def _json(self):
        raw = self._read_body()
        if not raw:
            return {}
        return json.loads(raw.decode('utf-8'))

    def _body(self):
        return self._read_body()

    # ---- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {'ok': False, 'error': 'bad host'})
        u = urllib.parse.urlparse(self.path)
        p = u.path
        q = urllib.parse.parse_qs(u.query)
        if p.startswith('/tiles/'):
            return self._tile(p)
        if p.startswith('/api/'):
            if not self._auth():
                return self._send(403, {'ok': False, 'error': 'missing token'})
            return self._api_get(p, q)
        return self._static(p)

    def do_POST(self):
        self._raw = None
        self._read_body()
        if not self._host_ok():
            return self._send(403, {'ok': False, 'error': 'bad host'})
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith('/api/') or not self._auth():
            return self._send(403, {'ok': False, 'error': 'missing token'})
        try:
            return self._api_post(u.path, urllib.parse.parse_qs(u.query))
        except ValueError as e:
            return self._send(400, {'ok': False, 'error': 'bad request: %s' % e})

    def _static(self, p):
        root = State.web
        if p.startswith('/tests/') and State.tests:
            root, p = State.tests, p[len('/tests'):]
        if p in ('', '/'):
            p = '/index.html'
        full = os.path.normpath(os.path.join(root, urllib.parse.unquote(p).lstrip('/')))
        if not full.startswith(os.path.normpath(root) + os.sep) or not os.path.isfile(full):
            return self._send(404, 'not found', 'text/plain')
        with open(full, 'rb') as fh:
            body = fh.read()
        ext = os.path.splitext(full)[1].lower()
        # no-cache: after an update the window must not keep showing old files (icons included)
        self._send(200, body, MIME.get(ext, 'application/octet-stream'), {'Cache-Control': 'no-cache'})

    def _tile(self, p):
        m = re.match(r'^/tiles/([a-z0-9-]+)/(\d+)/(\d+)/(\d+)(?:\.\w+)?$', p)
        if not m or m.group(1) not in TILE_SOURCES:
            return self._send(404, b'', 'text/plain')
        src, z, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if z > TILE_SOURCES[src][2] or x >= 2 ** z or y >= 2 ** z:
            return self._send(404, b'', 'text/plain')
        body, ctype, status = fetch_tile(src, z, x, y)
        if status != 200 or not body:
            return self._send(status, b'', 'text/plain', {'Cache-Control': 'no-store'})
        self._send(200, body, ctype, {'Cache-Control': 'max-age=3600'})

    def _api_get(self, p, q):
        if p == '/api/info':
            return self._send(200, {'ok': True, 'app': APP, 'version': VERSION, 'platform': sys.platform, 'helper': 'python', 'machine': machine_id(),
                                    'dataDir': DATA, 'online': Net.online(), 'python': sys.version.split()[0],
                                    'tileSources': {k: v[2] for k, v in TILE_SOURCES.items()}})
        if p == '/api/settings':
            return self._send(200, SETTINGS.load())
        if p == '/api/net':
            return self._send(200, {'online': Net.online(), 'lastError': Net.last_error})
        if p == '/api/dji/keychains':
            path = os.path.join(KEY_DIR, safe_id(q.get('id', [''])[0]) + '.json')
            if os.path.isfile(path):
                with open(path, encoding='utf-8') as fh:
                    return self._send(200, {'ok': True, 'data': json.load(fh)['data'], 'cached': True})
            return self._send(404, {'ok': False})
        if p == '/api/tiles/prefetch':
            with Prefetch.lock:
                return self._send(200, dict(Prefetch.state))
        if p == '/api/tiles/stats':
            return self._send(200, cache_stats())
        if p == '/api/file':
            path = q.get('path', [''])[0]
            if not os.path.isfile(path):
                return self._send(404, {'ok': False, 'error': 'File not found: %s' % path})
            with open(path, 'rb') as fh:
                body = fh.read()
            return self._send(200, body, 'application/octet-stream', {'X-File-Name': urllib.parse.quote(os.path.basename(path)), 'X-File-Size': str(len(body))})
        if p == '/api/open-queue':
            with State.lock:
                paths, State.open_queue = State.open_queue, []
            return self._send(200, {'paths': paths})
        if p == '/api/live/poll':
            since = int(q.get('since', ['0'])[0] or 0)
            return self._send(200, Live.poll(since))
        return self._send(404, {'ok': False, 'error': 'unknown endpoint'})

    def _api_post(self, p, q):
        if p == '/api/ping':
            State.last_ping = time.time()
            State.pinged = True
            return self._send(200, {'ok': True, 'online': Net.online()})
        if p == '/api/settings':
            return self._send(200, SETTINGS.merge(self._json()))
        if p == '/api/dji/keychains':
            status, out = dji_keychains(self._json())
            return self._send(status, out)
        if p == '/api/tiles/prefetch':
            b = self._json()
            return self._send(200, Prefetch.start(b.get('sources') or ['esri-imagery'], b['bbox'], int(b.get('minZoom', 10)), int(b.get('maxZoom', 17))))
        if p == '/api/tiles/prefetch/cancel':
            Prefetch.cancel.set()
            return self._send(200, {'ok': True})
        if p == '/api/tiles/clear':
            shutil.rmtree(TILE_DIR, ignore_errors=True)
            return self._send(200, {'ok': True})
        if p == '/api/open-queue':
            b = self._json()
            with State.lock:
                State.open_queue += [x for x in b.get('paths', []) if isinstance(x, str)]
            # files opened from Explorer just after the window was closed still need a window
            if not State.no_browser and not window_showing():
                open_window(page_url())
            return self._send(200, {'ok': True})
        if p == '/api/window':
            if not State.no_browser:
                open_window(page_url())
            return self._send(200, {'ok': True})
        if p == '/api/open-dialog':
            b = self._json()
            try:
                paths = native_dialog(False, b.get('title') or 'Open', [tuple(f) for f in b.get('filters') or []] or None, bool(b.get('multiple')))
            except Exception as e:
                return self._send(500, {'ok': False, 'error': str(e)})
            return self._send(200, {'ok': True, 'paths': paths})
        if p == '/api/save':
            name = q.get('name', ['export.dat'])[0]
            body = self._body()
            try:
                paths = native_dialog(True, 'Save ' + name, [('All files', '*.*')], False, name)
            except Exception as e:
                return self._send(500, {'ok': False, 'error': str(e)})
            if not paths:
                return self._send(200, {'ok': False, 'cancelled': True})
            with open(paths[0], 'wb') as fh:
                fh.write(body)
            return self._send(200, {'ok': True, 'path': paths[0]})
        if p == '/api/reveal':
            return self._send(200, {'ok': reveal(self._json().get('path', ''))})
        if p == '/api/open-file':
            return self._send(200, {'ok': open_file(self._json().get('path', ''))})
        if p == '/api/pdf':
            name = q.get('name', ['Orbit report.pdf'])[0]
            target = q.get('path', [''])[0]
            if not target:
                try:
                    paths = native_dialog(True, 'Save ' + name, [('PDF document', '*.pdf')], False, name)
                except Exception as e:
                    return self._send(500, {'ok': False, 'error': str(e)})
                if not paths:
                    return self._send(200, {'ok': False, 'cancelled': True})
                target = paths[0]
            ok, err = html_to_pdf(self._body(), target)
            return self._send(200 if ok else 500, {'ok': ok, 'path': target if ok else None, 'error': err})
        if p == '/api/live/start':
            return self._send(200, Live.start(self._json()))
        if p == '/api/live/stop':
            return self._send(200, Live.stop())
        if p == '/api/quit':
            threading.Thread(target=lambda: (time.sleep(0.3), State.server.shutdown()), daemon=True).start()
            return self._send(200, {'ok': True})
        return self._send(404, {'ok': False, 'error': 'unknown endpoint'})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # lets a restarted helper take its old port back at once; on Windows the flag would let
    # two programs share a port, so it stays off there
    allow_reuse_address = sys.platform != 'win32'


# --------------------------------------------------------------------------- window
def find_browser():
    if sys.platform == 'win32':
        cands = []
        for env in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
            base = os.environ.get(env)
            if base:
                cands.append(os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') as k:
                        cands.append(winreg.QueryValue(k, None))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            base = os.environ.get(env)
            if base:
                cands.append(os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'))
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None
    if sys.platform == 'darwin':
        for c in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'):
            if os.path.isfile(c):
                return c
        return None
    return shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('microsoft-edge')


def open_window(url):
    exe = find_browser()
    if exe:
        profile = os.path.join(DATA, 'window-profile')
        args = [exe, '--app=' + url, '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check',
                '--disable-features=Translate,msEdgeSidebarV2,msUndersideButton', '--window-size=1440,900']
        try:
            kw = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}
            proc = subprocess.Popen(args, **kw)
            proc.started, proc.ended = time.time(), None
            with State.lock:
                State.browsers.append(proc)
            threading.Thread(target=_note_exit, args=(proc,), daemon=True).start()
            return True
        except OSError as e:
            log('browser launch failed', repr(e))
    import webbrowser
    return webbrowser.open(url)


def _note_exit(proc):
    proc.wait()
    proc.ended = time.time()


def page_url():
    return 'http://127.0.0.1:%d/?token=%s' % (State.port, State.token)


def window_state():
    """'open' while a window process this helper started runs, 'closed' once one ran and quit, else 'unknown'."""
    with State.lock:
        procs = list(State.browsers)
    if any(p.ended is None for p in procs):
        return 'open'
    # a process that quits within seconds only handed its window to a browser that was already running
    if any(p.ended - p.started > 10 for p in procs):
        return 'closed'
    return 'unknown'


def window_showing():
    state = window_state()
    return state == 'open' or (state == 'unknown' and time.time() - State.last_ping < 15)


def idle_too_long(idle, started):
    """Whether no window has been left for `idle` seconds, so the helper can quit."""
    state = window_state()
    if state == 'open':
        return False
    # without a window process to watch, pings are the only sign of a window, and Edge sends them
    # only once a minute from a window that has been minimised or covered for 5 minutes
    limit = idle if state == 'closed' else max(idle, 150)
    if State.pinged:
        return time.time() - State.last_ping > limit
    return time.time() - started > max(limit, 120)


def machine_id():
    """A stable id of this computer, for licenses locked to one computer (the page shows only a hash of it).
    Windows: MachineGuid; Mac: the hardware UUID, the same value the Mac app reads."""
    try:
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return str(winreg.QueryValueEx(k, 'MachineGuid')[0])
        if sys.platform == 'darwin':
            out = subprocess.run(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], capture_output=True, text=True).stdout
            m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
            if m:
                return m.group(1)
        with open('/etc/machine-id', encoding='utf-8') as fh:
            return fh.read().strip()
    except Exception:
        pass
    return 'node-%012x' % uuid.getnode()


def lock_path():
    return os.path.join(DATA, 'helper.json')


def claim_start():
    """Lets one process start the helper when Windows starts several at once (one per file opened from Explorer).
    Returns the claim file to remove once the helper is up, or None when another process holds it."""
    path = os.path.join(DATA, 'helper.starting')
    try:
        if time.time() - os.path.getmtime(path) > 20:  # left by a start that failed
            os.remove(path)
    except OSError:
        pass
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return path
    except OSError:
        return None


def post_to(info, path, payload):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (info['port'], path), data=json.dumps(payload).encode(), method='POST',
                                 headers={'X-SF-Token': info['token'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
            return r.status == 200
    except Exception as e:
        log('forward failed', path, repr(e))
        return False


def existing_instance():
    try:
        with open(lock_path(), encoding='utf-8') as fh:
            info = json.load(fh)
        req = urllib.request.Request('http://127.0.0.1:%d/api/info' % info['port'], headers={'X-SF-Token': info['token'], 'Host': '127.0.0.1:%d' % info['port']})
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                return info
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser(description='Orbit helper')
    ap.add_argument('files', nargs='*')
    ap.add_argument('--port', type=int, default=47615)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--dev', action='store_true', help='serve the tests folder and stay up without a window')
    ap.add_argument('--print-url', action='store_true')
    ap.add_argument('--exit-when-idle', type=int, default=None, help='quit N seconds after the window stops pinging')
    ap.add_argument('--token', default=None, help='fixed access token (development and tests only)')
    a = ap.parse_args()
    if a.token:
        State.token = a.token
    files = [os.path.abspath(f) for f in a.files if os.path.isfile(f)]

    State.no_browser = a.no_browser
    other = None if a.dev else existing_instance()
    claim = None
    if not other and not a.dev:
        claim = claim_start()
        if not claim:
            # another Orbit is starting this very moment (several files opened at once): give it the files
            for _ in range(80):
                time.sleep(0.25)
                other = existing_instance()
                if other:
                    break
    if other:
        if files:
            post_to(other, '/api/open-queue', {'paths': files})
        elif not a.no_browser and not post_to(other, '/api/window', {}):
            # a helper older than 2.1 has no /api/window
            open_window('http://127.0.0.1:%d/?token=%s' % (other['port'], other['token']))
        return

    State.web = web_dir()
    tests = os.path.join(HERE, '..', 'tests')
    State.tests = os.path.abspath(tests) if a.dev and os.path.isdir(tests) else ''
    State.open_queue = files
    srv = None
    for port in [a.port] + list(range(a.port + 1, a.port + 20)) + [0]:
        try:
            srv = Server(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit('no free port')
    State.server = srv
    State.port = srv.server_address[1]
    url = 'http://127.0.0.1:%d/?token=%s' % (State.port, State.token)
    with open(lock_path(), 'w', encoding='utf-8') as fh:
        json.dump({'port': State.port, 'token': State.token, 'pid': os.getpid()}, fh)
    if claim:
        try:
            os.remove(claim)
        except OSError:
            pass
    log('helper started', VERSION, 'port', State.port, 'web', State.web, 'python', sys.version.split()[0])
    if a.print_url:
        print(url, flush=True)
    if not a.no_browser and not a.dev:
        open_window(url)
    elif a.dev and not a.no_browser:
        open_window(url)

    idle = a.exit_when_idle
    if idle:
        def watchdog():
            start = time.time()
            while True:
                time.sleep(5)
                if idle_too_long(idle, start):
                    log('no window for %ds; quitting' % int(time.time() - State.last_ping))
                    Live.stop()
                    srv.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        try:
            with open(lock_path(), encoding='utf-8') as fh:
                if json.load(fh).get('pid') == os.getpid():
                    os.remove(lock_path())
        except (OSError, ValueError):
            pass
        log('helper stopped')


if __name__ == '__main__':
    main()
