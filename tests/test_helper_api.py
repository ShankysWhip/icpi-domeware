#!/usr/bin/env python3
"""End-to-end checks of a running Orbit helper (the Python one or the Mac app's Swift one).

  python3 test_helper_api.py --base http://127.0.0.1:47616 --token devtoken

Covers: security checks, settings, the file and open queue endpoints, the tile cache, the DJI
keychain proxy's error path (no data is sent to DJI) and all four live feed modes, driven by
fake_radar.py replaying a Robin XML: the real 29 Oct recording if tests/real-data/ (or ORBIT_REAL_DATA)
holds it, else the synthetic one in tests/fixtures. Real recordings never go into git."""
import argparse, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.environ.get('ORBIT_REAL_DATA') or os.path.join(HERE, 'real-data')
ROBIN = os.path.join(REAL, 'iCPI kopio 2.xml')
if not os.path.exists(ROBIN):
    ROBIN = os.path.join(HERE, 'fixtures', 'SYNTHETIC_fleet_radar.xml')
results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(('PASS ' if ok else 'FAIL ') + name + (' — ' + detail if detail else ''), flush=True)


def req(base, token, method, path, body=None, headers=None, raw=False):
    h = {'X-SF-Token': token} if token else {}
    h.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault('Content-Type', 'application/json')
    r = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            b = resp.read()
            return resp.status, (b if raw else json.loads(b or b'{}'))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, (b if raw else json.loads(b or b'{}'))
        except ValueError:
            return e.code, b


def fake(*args):
    return subprocess.Popen([sys.executable, os.path.join(HERE, 'fake_radar.py')] + list(args) + ['--file', ROBIN],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def live_round(base, token, cfg, starter, label, want=40):
    st, r = req(base, token, 'POST', '/api/live/start', cfg)
    check(label + ': start', st == 200 and r.get('ok'), str(r))
    proc = starter()
    seq, msgs, bad, t0 = 0, 0, 0, time.time()
    status = {}
    while time.time() - t0 < 25 and msgs < want:
        time.sleep(0.5)
        st, r = req(base, token, 'GET', '/api/live/poll?since=%d' % seq)
        status = r.get('status', {})
        for it in r.get('items', []):
            seq = it['seq']
            msgs += 1
            if not (it['xml'].startswith('<Robin') and it['xml'].rstrip().endswith('</Robin>')):
                bad += 1
    check(label + ': messages arrive whole', msgs >= want and bad == 0, '%d messages, %d broken, state %s' % (msgs, bad, status.get('state')))
    rec = status.get('record')
    req(base, token, 'POST', '/api/live/stop', {})
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    if rec:
        txt = open(rec, encoding='utf-8').read() if os.path.exists(rec) else ''
        check(label + ': session recorded to disk', txt.count('</Robin>') >= msgs * 0.9, rec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:47616')
    ap.add_argument('--token', default='devtoken')
    ap.add_argument('--skip-live', action='store_true')
    a = ap.parse_args()
    B, T = a.base.rstrip('/'), a.token

    st, info = req(B, T, 'GET', '/api/info')
    check('info', st == 200 and info.get('app') == 'Orbit', '%s helper %s' % (info.get('platform'), info.get('helper')))
    st, _ = req(B, '', 'GET', '/api/info')
    check('API refuses requests without the token', st == 403)
    st, _ = req(B, T, 'GET', '/api/info', headers={'Host': 'evil.example.com'})
    check('API refuses a foreign Host header', st == 403)
    st, body = req(B, '', 'GET', '/', raw=True)
    check('serves the app page', st == 200 and b'Orbit' in body)
    st, _ = req(B, '', 'GET', '/../server/orbit_server.py', raw=True)
    check('no path traversal', st in (400, 403, 404))

    st, s1 = req(B, T, 'POST', '/api/settings', {'testKey': 'x1'})
    st2, s2 = req(B, T, 'GET', '/api/settings')
    check('settings round trip', s2.get('testKey') == 'x1')
    req(B, T, 'POST', '/api/settings', {'testKey': None})

    st, body = req(B, T, 'GET', '/api/file?path=' + urllib.request.quote(ROBIN), raw=True)
    check('reads a local file for the app', st == 200 and body[:7] == b'<Robin>', '%d bytes' % len(body))
    st, _ = req(B, T, 'GET', '/api/file?path=/nonexistent/file.xml')
    check('missing file gives 404', st == 404)

    req(B, T, 'POST', '/api/open-queue', {'paths': ['/tmp/a.xml']})
    st, q = req(B, T, 'GET', '/api/open-queue')
    check('open queue', q.get('paths') == ['/tmp/a.xml'])

    st, img = req(B, '', 'GET', '/tiles/esri-imagery/16/37503/16981', raw=True)
    check('map tile through the cache', st == 200 and img[:2] == b'\xff\xd8', '%d bytes' % len(img))
    st, stats = req(B, T, 'GET', '/api/tiles/stats')
    check('tile cache stats', st == 200 and stats.get('count', 0) >= 1, '%d tiles, %.1f MB' % (stats.get('count', 0), stats.get('bytes', 0) / 1048576))
    st, _ = req(B, '', 'GET', '/tiles/nosuchsource/1/0/0', raw=True)
    check('unknown tile source refused', st == 404)
    st, r = req(B, T, 'POST', '/api/tiles/prefetch', {'sources': ['esri-imagery'], 'bbox': [26.040, 65.175, 26.046, 65.178], 'minZoom': 14, 'maxZoom': 15})
    ok = st == 200 and r.get('ok')
    t0 = time.time()
    while ok and time.time() - t0 < 30:
        time.sleep(0.5)
        _, p = req(B, T, 'GET', '/api/tiles/prefetch')
        if not p.get('running'):
            break
    check('save an area for offline', ok and not p.get('running') and p.get('done') == p.get('total'), json.dumps(p) if ok else str(r))
    st, r = req(B, T, 'POST', '/api/tiles/prefetch', {'sources': ['esri-imagery'], 'bbox': [20, 60, 30, 70], 'minZoom': 10, 'maxZoom': 18})
    check('refuses a too-large offline area', st == 200 and not r.get('ok'), r.get('error', ''))

    # DJI proxy: only the no-key path, so nothing from the user's log goes to DJI here
    saved = req(B, T, 'GET', '/api/settings')[1].get('djiApiKey')
    if not saved:
        st, r = req(B, T, 'POST', '/api/dji/keychains', {'request': {'version': 4, 'department': 7, 'keychainsArray': []}, 'id': 'test'})
        check('DJI unlock without an API key explains itself', st == 400 and 'API key' in r.get('error', ''), r.get('error', ''))
    st, r = req(B, T, 'GET', '/api/dji/keychains?id=does-not-exist')
    check('no cached DJI keys for an unknown log', st == 404)

    # report PDF: two fixed A4 pages in, a two-page A4 PDF out (WebKit on Mac, Edge/Chrome otherwise)
    page = '<div class="page" style="width:794px;height:1123px;page-break-after:always;padding:60px;font:20px sans-serif">Orbit PDF test page %d</div>'
    html = ('<!doctype html><html><head><meta charset="utf-8"><style>@page{size:210mm 297mm;margin:0}body{margin:0}</style></head><body>' + page % 1 + page % 2 + '</body></html>').encode()
    out = os.path.join(tempfile.mkdtemp(), 'test report.pdf')
    st, r = req(B, T, 'POST', '/api/pdf?name=test.pdf&pages=2&path=' + urllib.request.quote(out), html, headers={'Content-Type': 'text/html; charset=utf-8'})
    pdf_ok = st == 200 and r.get('ok') and os.path.exists(out)
    pages = 0
    if pdf_ok:
        data = open(out, 'rb').read()
        pages = len(re.findall(rb'/Type\s*/Page[^s]', data))
    check('report PDF is made', pdf_ok and pages == 2 and data[:4] == b'%PDF', '%s pages, %s' % (pages, r.get('error') or out))

    if not a.skip_live:
        live_round(B, T, {'mode': 'tcp-client', 'host': '127.0.0.1', 'port': 5155},
                   lambda: fake('serve', '--port', '5155', '--speed', '40'), 'live TCP client')
        live_round(B, T, {'mode': 'tcp-server', 'port': 5156},
                   lambda: (time.sleep(0.6), fake('connect', '--port', '5156', '--speed', '40'))[1], 'live TCP listener')
        live_round(B, T, {'mode': 'udp', 'port': 5157},
                   lambda: (time.sleep(0.6), fake('udp', '--port', '5157', '--speed', '40'))[1], 'live UDP')
        tmp = os.path.join(tempfile.mkdtemp(), 'growing.xml')
        open(tmp, 'w').close()
        live_round(B, T, {'mode': 'file', 'path': tmp, 'fromStart': True},
                   lambda: fake('append', '--out', tmp, '--speed', '40'), 'live file follow')
    failed = [n for n, ok in results if not ok]
    print('\n%d checks, %d failed%s' % (len(results), len(failed), (': ' + ', '.join(failed)) if failed else ''))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
