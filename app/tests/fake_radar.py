#!/usr/bin/env python3
"""Fake radar for testing Orbit's live feed: replays a Robin XML file (concatenated
<Robin> messages) at a chosen speed, the way a radar would stream it.

  python3 fake_radar.py serve   --port 5055 --file radar.xml [--speed 10]   # radar as TCP server (app connects)
  python3 fake_radar.py connect --port 5056 --file radar.xml [--host H]     # radar connects to the app's listener
  python3 fake_radar.py udp     --port 5057 --file radar.xml [--host H]     # one UDP datagram per message
  python3 fake_radar.py append  --out live.xml --file radar.xml             # a file that keeps growing
"""
import argparse, datetime as dt, re, socket, sys, time


def messages(path):
    text = open(path, encoding='utf-8').read()
    out = []
    for m in re.finditer(r'<Robin>.*?</Robin>', text, flags=re.S):
        chunk = m.group(0)
        ts = re.search(r'[tT]imestamp="([^"]+)"|<Timestamp>([^<]+)<', chunk)
        t = None
        if ts:
            s = (ts.group(1) or ts.group(2))[:23]
            try:
                t = dt.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%f').timestamp()
                if t < 1e9:
                    t = None
            except ValueError:
                t = None
        out.append((t, chunk))
    return out


def paced(msgs, speed, limit):
    t0 = next((t for t, _ in msgs if t), None)
    wall0 = time.time()
    for k, (t, chunk) in enumerate(msgs):
        if limit and k >= limit:
            return
        if t and t0:
            wait = (t - t0) / speed - (time.time() - wall0)
            if wait > 0:
                time.sleep(min(wait, 2))
        yield chunk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['serve', 'connect', 'udp', 'append'])
    ap.add_argument('--file', required=True)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5055)
    ap.add_argument('--out')
    ap.add_argument('--speed', type=float, default=10)
    ap.add_argument('--limit', type=int, default=0, help='stop after N messages')
    a = ap.parse_args()
    msgs = messages(a.file)
    print('messages', len(msgs), flush=True)
    sent = 0
    if a.mode == 'serve':
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', a.port))
        srv.listen(1)
        print('listening', a.port, flush=True)
        conn, _ = srv.accept()
        for chunk in paced(msgs, a.speed, a.limit):
            # split each message across two sends to exercise reassembly
            b = (chunk + '\n').encode()
            conn.sendall(b[:len(b) // 2]); conn.sendall(b[len(b) // 2:])
            sent += 1
        conn.close()
    elif a.mode == 'connect':
        s = socket.create_connection((a.host, a.port), timeout=5)
        for chunk in paced(msgs, a.speed, a.limit):
            s.sendall((chunk + '\n').encode())
            sent += 1
        s.close()
    elif a.mode == 'udp':
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for chunk in paced(msgs, a.speed, a.limit):
            s.sendto(chunk.encode(), (a.host, a.port))
            sent += 1
    else:
        with open(a.out, 'a', encoding='utf-8') as fh:
            for chunk in paced(msgs, a.speed, a.limit):
                fh.write(chunk + '\n')
                fh.flush()
                sent += 1
    print('sent', sent, flush=True)


if __name__ == '__main__':
    main()
