#!/usr/bin/env python3
"""Writes made-up ArduPilot logs in the real file formats, for tests/t_ardupilot.js.

  python3 make_ardupilot.py

- fixtures/SYNTHETIC_ArduCopter_sysid2.BIN: a DataFlash log of an ArduCopter 4.6.2 quad (SYSID 2). It boots
  at 11:59:30 UTC on 2026-10-29, gets a 3D fix at +10 s and RTK at +20 s, arms at +40 s and climbs to 30 m.
  In Auto it flies a 200 m square at 10 m/s (east, north, west, south from +50 s), then RTL at +130 s,
  Land at +135 s, lands at +140 s and disarms at +145 s. POS and ATT are at 10 Hz and GPS at 5 Hz, with
  a few junk bytes between records. The same flight without POS records goes to ..._noPOS.BIN.
- fixtures/SYNTHETIC_fleet_sysid3_4.tlog: a ground station's telemetry log with two quads. Sysid 3 sends
  MAVLink 2 and flies east at 25 m; sysid 4 sends MAVLink 1 and flies 60 m north of it at 40 m; both
  fly at 10 m/s from 12:00:10 to 12:01:10 UTC. There are GCS heartbeats, junk bytes and a packet with
  a bad CRC.
Home is 65.0 N 25.0 E at 80 m MSL. None of this is real flight data."""
import math, os, struct
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fixtures')
LAT0, LON0, ALT0 = 65.0, 25.0, 80.0
KY = 111132.92 - 559.82 * math.cos(2 * math.radians(LAT0)) + 1.175 * math.cos(4 * math.radians(LAT0))
KX = 111412.84 * math.cos(math.radians(LAT0)) - 93.5 * math.cos(3 * math.radians(LAT0))
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc).timestamp()
BOOT = datetime(2026, 10, 29, 11, 59, 30, tzinfo=timezone.utc).timestamp()


def ll(east, north):
    return LAT0 + north / KY, LON0 + east / KX


# ---------------------------------------------------------------- the .BIN flight
def square(s):
    """(east, north, height, course) s seconds after boot."""
    if s < 40: return 0.0, 0.0, 0.0, 0.0
    if s < 50: return 0.0, 0.0, 3.0 * (s - 40), 0.0
    if s < 130:
        k = s - 50
        if k < 20: return 10 * k, 0.0, 30.0, 90.0
        if k < 40: return 200.0, 10 * (k - 20), 30.0, 0.0
        if k < 60: return 200 - 10 * (k - 40), 200.0, 30.0, 270.0
        return 0.0, 200 - 10 * (k - 60), 30.0, 180.0
    if s < 140: return 0.0, 0.0, max(0.0, 30 - 3 * (s - 130)), 180.0
    return 0.0, 0.0, 0.0, 180.0


FMTS = {  # type: (name, format, columns)
    128: ('FMT', 'BBnNZ', 'Type,Length,Name,Format,Columns'),
    64: ('PARM', 'QNff', 'TimeUS,Name,Value,Default'),
    91: ('MSG', 'QZ', 'TimeUS,Message'),
    92: ('MODE', 'QMBB', 'TimeUS,Mode,ModeNum,Rsn'),
    93: ('ARM', 'QBIBB', 'TimeUS,ArmState,ArmChecks,Forced,Method'),
    94: ('EV', 'QB', 'TimeUS,Id'),
    95: ('GPS', 'QBBIHBcLLeffffB', 'TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U'),
    96: ('POS', 'QLLfff', 'TimeUS,Lat,Lng,Alt,RelHomeAlt,RelOriginAlt'),
    97: ('ATT', 'QccccCCCCB', 'TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw,ErrRP,ErrYaw,AEKF'),
    98: ('BAT2', 'Qff', 'TimeUS,Volt,Curr'),  # defined but not read by Orbit
}
PY = {'a': '64s', 'b': 'b', 'B': 'B', 'M': 'B', 'h': 'h', 'H': 'H', 'c': 'h', 'C': 'H', 'i': 'i', 'I': 'I', 'e': 'i', 'E': 'I', 'L': 'i',
      'f': 'f', 'd': 'd', 'n': '4s', 'N': '16s', 'Z': '64s', 'q': 'q', 'Q': 'Q'}
SCALE = {'c': 100, 'C': 100, 'e': 100, 'E': 100, 'L': 1e7}


def record(t, values):
    name, fmt, _ = FMTS[t]
    vals = []
    for ch, v in zip(fmt, values):
        if ch in SCALE: v = int(round(v * SCALE[ch]))
        if ch in 'nNZa': v = v.encode()
        vals.append(v)
    return bytes([0xA3, 0x95, t]) + struct.pack('<' + ''.join(PY[c] for c in fmt), *vals)


def length(t):
    return 3 + struct.calcsize('<' + ''.join(PY[c] for c in FMTS[t][1]))


def write_bin(path, with_pos):
    recs = []  # (time s, bytes)
    for t in FMTS:
        name, fmt, cols = FMTS[t]
        recs.append((-1, record(128, (t, length(t), name, fmt, cols))))
    us = lambda s: int(round(s * 1e6))
    recs.append((0.5, record(64, (us(0.5), 'SYSID_THISMAV', 2.0, 1.0))))
    recs.append((1.0, record(91, (us(1.0), 'ArduCopter V4.6.2 (a1b2c3d4)'))))
    recs.append((1.1, record(91, (us(1.1), 'Frame: QUAD/X'))))
    for s, mode in ((35, 5), (41, 3), (130, 6), (135, 9)):
        recs.append((s, record(92, (us(s), mode, mode, 2))))
    recs.append((40, record(93, (us(40), 1, 0, 0, 0))))
    recs.append((40, record(94, (us(40), 10))))
    recs.append((140, record(94, (us(140), 18))))
    recs.append((145, record(93, (us(145), 0, 0, 0, 0))))
    recs.append((145, record(94, (us(145), 11))))
    for k in range(0, 751):  # GPS at 5 Hz, 0-150 s
        s = k / 5
        e, n, h, crs = square(s)
        status = 1 if s < 10 else 3 if s < 20 else 6
        lat, lon = ll(e, n) if status >= 3 else (0.0, 0.0)
        gms_total = int(round((BOOT + s - GPS_EPOCH + 18) * 1000))
        gwk, gms = divmod(gms_total, 604800000) if status >= 3 else (0, 0)
        e2, n2, h2, _ = square(s + 0.1)
        spd = math.hypot(e2 - e, n2 - n) / 0.1
        vz = -(h2 - h) / 0.1
        recs.append((s, record(95, (us(s), 0, status, gms, gwk, 12 if status >= 3 else 4, 0.7, lat, lon, ALT0 + h, spd, crs, vz, 0.0, 1))))
    for k in range(200, 1501):  # POS and ATT at 10 Hz, 20-150 s
        s = k / 10
        e, n, h, crs = square(s)
        lat, lon = ll(e, n)
        if with_pos: recs.append((s, record(96, (us(s), lat, lon, ALT0 + h, h, h))))
        recs.append((s, record(97, (us(s), 0.0, 0.5, 0.0, -0.5, crs, crs, 0.1, 0.2, 0))))
        if k % 50 == 0: recs.append((s, record(98, (us(s), 24.6, 12.0))))
    recs.sort(key=lambda r: r[0])
    out = bytearray()
    for k, (_, b) in enumerate(recs):
        out += b
        if k in (50, 400, 1200): out += b'\x00\x13\xa3\x01'  # junk the reader must skip
    with open(path, 'wb') as fh:
        fh.write(out)
    return len(out)


# ---------------------------------------------------------------- the .tlog
EXTRA = {0: 50, 24: 24, 33: 104, 253: 83, 30: 39}


def x25(data, extra):
    c = 0xFFFF
    for b in list(data) + [extra]:
        t = (b ^ (c & 0xFF)) & 0xFF
        t = (t ^ (t << 4)) & 0xFF
        c = ((c >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF
    return c


def packet(v2, seq, sysid, comp, msgid, payload, bad_crc=False):
    if v2:
        payload = payload.rstrip(b'\x00') or b'\x00'  # MAVLink 2 trims trailing zeros
        head = bytes([len(payload), 0, 0, seq & 255, sysid, comp, msgid & 0xFF, msgid >> 8 & 0xFF, msgid >> 16 & 0xFF])
        crc = x25(head + payload, EXTRA[msgid])
        return b'\xfd' + head + payload + struct.pack('<H', crc ^ (1 if bad_crc else 0))
    head = bytes([len(payload), seq & 255, sysid, comp, msgid])
    crc = x25(head + payload, EXTRA[msgid])
    return b'\xfe' + head + payload + struct.pack('<H', crc ^ (1 if bad_crc else 0))


def write_tlog(path):
    t0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    items = []  # (time s, bytes)
    seq = {3: 0, 4: 0, 255: 0}

    def add(ts, sysid, comp, msgid, payload, v2, bad=False):
        seq[sysid] += 1
        items.append((ts, struct.pack('>Q', int(round(ts * 1e6))) + packet(v2, seq[sysid], sysid, comp, msgid, payload, bad)))

    for sysid, v2, north, height in ((3, True, 0.0, 25.0), (4, False, 60.0, 40.0)):
        add(t0 + 1, sysid, 1, 253, struct.pack('<B50s', 6, b'ArduCopter V4.6.2 (a1b2c3d4)'), v2)
        for k in range(0, 81):  # heartbeat 1 Hz, 0-80 s: armed and in Auto from 8 s to 72 s
            s = t0 + k
            armed = 8 <= k < 72
            mode = 3 if armed else 5
            add(s + 0.05, sysid, 1, 0, struct.pack('<IBBBBB', mode, 2, 3, 0x59 | (128 if armed else 0), 4 if armed else 3, 3), v2)
            add(s + 0.06, sysid, 1, 24, struct.pack('<QiiiHHHHBB', int(k * 1e6), *[int(round(x * 1e7)) for x in ll(0, north)], 80000, 70, 90, 0, 0, 6, 28), v2)
        for k in range(0, 401):  # GLOBAL_POSITION_INT 5 Hz, 0-80 s
            s = k / 5
            east = 0.0 if s < 10 else min(600.0, 10 * (s - 10))
            h = 0.0 if s < 8 or s > 72 else height
            ve = 0 if s < 10 or s >= 70 else 1000  # cm/s east (vy); while still the heading is 0, so MAVLink 2 trims it
            lat, lon = ll(east, north)
            payload = struct.pack('<IiiiihhhH', int(s * 1000), int(round(lat * 1e7)), int(round(lon * 1e7)), int(round((ALT0 + h) * 1000)), int(round(h * 1000)),
                                  0, ve, 0, 9000 if ve else 0)
            add(t0 + s, sysid, 1, 33, payload, v2)
        add(t0 + 30.5, sysid, 1, 253, struct.pack('<B50s', 4, b'Radio failsafe test'), v2)
    for k in range(0, 81):  # the ground station's own heartbeat (sysid 255) must not become a drone
        add(t0 + k + 0.5, 255, 190, 0, struct.pack('<IBBBBB', 0, 6, 8, 0, 0, 3), True)
    add(t0 + 20.01, 3, 1, 33, struct.pack('<IiiiihhhH', 1, 0, 0, 0, 0, 0, 0, 0, 0), True, bad=True)  # bad CRC: dropped
    items.sort(key=lambda x: x[0])
    out = bytearray()
    for k, (_, b) in enumerate(items):
        out += b
        if k in (100, 900): out += b'\x55\xaa\x00'  # junk
    with open(path, 'wb') as fh:
        fh.write(out)
    return len(out)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (('SYNTHETIC_ArduCopter_sysid2.BIN', lambda p: write_bin(p, True)), ('SYNTHETIC_ArduCopter_sysid2_noPOS.BIN', lambda p: write_bin(p, False)),
                     ('SYNTHETIC_fleet_sysid3_4.tlog', write_tlog)):
        size = fn(os.path.join(OUT, name))
        print('wrote', name, size, 'bytes')


# ---------------------------------------------------------------- a radar for the .tlog scene
def write_radar(path):
    """Robin-style XML for the .tlog scene: the radar sits 100 m south-west of home. Track R3 follows
    sysid 3 8 m off from 12 s to 70 s; sysid 4 is tracked 10 m off as R4a (15-40 s) and, after a break,
    R4b (44-70 s); B1 is a bird 500 m north. Drone tracks carry DRONE and the alarm flag."""
    t0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    iso = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    rlat, rlon = ll(-100, -100)
    out = ['<Robin><SystemStatus timestamp="%s"><Name>Test radar</Name><Sensors><SensorStatus id="0"><Name>Test radar</Name><OperationalState>Operational</OperationalState>'
           '<Position><Latitude>%.8f</Latitude><Longitude>%.8f</Longitude><Altitude>85.0</Altitude></Position><GroundLevel>80.0</GroundLevel></SensorStatus></Sensors></SystemStatus></Robin>' % (iso(t0), rlat, rlon)]
    def east(s): return 0.0 if s < 10 else min(600.0, 10 * (s - 10))
    for k in range(0, 81):
        s = float(k)
        tracks = []
        if 12 <= s <= 70: tracks.append(('R3', east(s), 0 + 8, 25, 'DRONE', True))
        if 15 <= s <= 40: tracks.append(('R4a', east(s), 60 + 10, 40, 'DRONE', True))
        if 44 <= s <= 70: tracks.append(('R4b', east(s), 60 - 10, 40, 'DRONE', True))
        tracks.append(('B1', -100 + 2 * s, 400, 30, 'BIRD', False))
        body = ''.join('<Track id="%s"><Timestamp>%s</Timestamp><Position><Latitude>%.8f</Latitude><Longitude>%.8f</Longitude><Altitude>%.2f</Altitude></Position>'
                       '<Velocity><Azimuth>90.0</Azimuth><Elevation>0.0</Elevation><Speed>%.2f</Speed></Velocity><Classification id="1">%s</Classification><Score>0.9</Score><Alarm>%s</Alarm></Track>'
                       % (tid, iso(t0 + s), *ll(e, n), 80.0 + h, 10.0 if tid != 'B1' else 2.0, cls, 'true' if al else 'false') for tid, e, n, h, cls, al in tracks)
        out.append('<Robin><Tracks timestamp="%s">%s</Tracks></Robin>' % (iso(t0 + s), body))
    with open(path, 'w') as fh:
        fh.write('\n'.join(out))
    return os.path.getsize(path)


if __name__ == '__main__':
    print('wrote SYNTHETIC_fleet_radar.xml', write_radar(os.path.join(OUT, 'SYNTHETIC_fleet_radar.xml')), 'bytes')
