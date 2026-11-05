#!/usr/bin/env python3
"""Checks how the Windows helper (server/orbit_server.py) handles several starts and its window.

  python3 test_instances.py

Covers: three helpers started at the same moment (Explorer opens each selected file in its own
process) end as one helper holding all three files; the window process is watched, so a minimised
window that pings only once a minute keeps the helper alive, a closed window lets it quit, and files
opened after the window closed get a new window. Uses a temporary data folder (ORBIT_DATA) and
stand-in browser scripts, so no real window opens."""
import json, os, shutil, socket, stat, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'server', 'orbit_server.py')
TMP = tempfile.mkdtemp(prefix='orbit-inst-')
os.environ['ORBIT_DATA'] = os.path.join(TMP, 'data')
results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(('PASS ' if ok else 'FAIL ') + name + (' — ' + detail if detail else ''), flush=True)


def call(port, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data, method=method,
                               headers={'X-SF-Token': token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read() or b'{}')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def script(name, body):
    p = os.path.join(TMP, name)
    with open(p, 'w') as fh:
        fh.write('#!/bin/sh\n' + body + '\n')
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


# ---------------------------------------------------------------- three starts at once
def race():
    files = []
    for n in ('flight.txt', 'radar.xml', 'weather.csv'):
        p = os.path.join(TMP, n)
        open(p, 'w').write('x')
        files.append(p)
    port = free_port()
    procs = [subprocess.Popen([sys.executable, SERVER, '--no-browser', '--exit-when-idle', '30', '--port', str(port), f])
             for f in files]
    t0 = time.time()
    while time.time() - t0 < 25 and sum(p.poll() is None for p in procs) > 1:
        time.sleep(0.2)
    running = [p for p in procs if p.poll() is None]
    check('race: one helper keeps running', len(running) == 1, '%d running after %.1f s' % (len(running), time.time() - t0))
    info = json.load(open(os.path.join(os.environ['ORBIT_DATA'], 'helper.json')))
    got = []
    t1 = time.time()
    while time.time() - t1 < 5 and len(got) < 3:
        got += call(info['port'], info['token'], 'GET', '/api/open-queue')['paths']
        time.sleep(0.3)
    check('race: the helper holds all three files', sorted(got) == sorted(files), str(got))
    check('race: start claim removed', not os.path.exists(os.path.join(os.environ['ORBIT_DATA'], 'helper.starting')))
    call(info['port'], info['token'], 'POST', '/api/quit', {})
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    check('race: helper quits on request', all(p.poll() is not None for p in procs))


# ---------------------------------------------------------------- the window process
def window():
    sys.path.insert(0, os.path.dirname(SERVER))
    import orbit_server as S
    long_browser = script('browser_long.sh', 'exec sleep 60')
    quick_browser = script('browser_quick.sh', 'exit 0')  # hands its window to a browser already running
    S.find_browser = lambda: long_browser
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    S.State.port, S.State.token = srv.server_address[1], 'tok'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port, tok = S.State.port, 'tok'
    started = time.time() - 300

    check('window: unknown before any window', S.window_state() == 'unknown')
    call(port, tok, 'POST', '/api/window', {})
    check('window: /api/window opens one', len(S.State.browsers) == 1 and S.window_state() == 'open')
    S.find_browser = lambda: quick_browser
    call(port, tok, 'POST', '/api/window', {})
    time.sleep(1.0)
    check('window: a hand-off process does not count as closed', S.window_state() == 'open', S.window_state())
    S.State.pinged, S.State.last_ping = True, time.time() - 600
    check('window: open window, no ping for 10 min (minimised) keeps the helper', not S.idle_too_long(45, started))

    first = S.State.browsers[0]
    first.started -= 60  # it has been open for a minute
    first.kill()
    t0 = time.time()
    while first.ended is None and time.time() - t0 < 5:
        time.sleep(0.05)
    check('window: closed once the browser quits', S.window_state() == 'closed', S.window_state())
    S.State.last_ping = time.time() - 30
    check('window: closed, 30 s quiet: still waiting', not S.idle_too_long(45, started))
    S.State.last_ping = time.time() - 50
    check('window: closed, 50 s quiet: quits', S.idle_too_long(45, started))

    S.State.last_ping = time.time() - 5  # the page pinged just before the window closed
    S.find_browser = lambda: long_browser
    call(port, tok, 'POST', '/api/open-queue', {'paths': ['/tmp/a.txt']})
    check('window: file after close opens a new window', len(S.State.browsers) == 3 and S.window_state() == 'open')
    check('window: and the file waits for it', call(port, tok, 'GET', '/api/open-queue')['paths'] == ['/tmp/a.txt'])
    call(port, tok, 'POST', '/api/open-queue', {'paths': ['/tmp/b.txt']})
    check('window: file while a window is open opens no other', len(S.State.browsers) == 3)

    for b in S.State.browsers:
        if b.poll() is None:
            b.kill()
    with S.State.lock:
        S.State.browsers = []
    S.State.last_ping = time.time() - 100
    check('window: no window process, 100 s quiet: waits (Edge pings once a minute)', not S.idle_too_long(45, started))
    S.State.last_ping = time.time() - 160
    check('window: no window process, 160 s quiet: quits', S.idle_too_long(45, started))

    S.State.no_browser = True
    call(port, tok, 'POST', '/api/window', {})
    check('window: --no-browser never opens one', not S.State.browsers)
    srv.shutdown()


if __name__ == '__main__':
    try:
        race()
        window()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print('\n%d/%d passed' % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)
