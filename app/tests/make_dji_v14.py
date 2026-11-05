"""Builds a SYNTHETIC DJI flight record in format 14 (XOR + AES-256-CBC), with known content,
so Orbit's decoder can be tested end to end without DJI's server: header, summary block,
version block, KeyStorage records, encrypted OSD/home/custom/battery/tip records with chained
IVs, and the matching keychains JSON (what DJI's API would return). Uses the openssl CLI."""
import base64, json, math, os, struct, subprocess, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
os.makedirs(OUT, exist_ok=True)
TABLE = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ 0x95AC9329AC4BC9B5 if c & 1 else c >> 1
    TABLE.append(c)

def xor_key(rtype, seed):
    c = (seed + rtype) & 0xFF
    for b in struct.pack('<Q', (0x123456789ABCDEF0 * seed) & 0xFFFFFFFFFFFFFFFF):
        c = TABLE[(c ^ b) & 0xFF] ^ (c >> 8)
    return struct.pack('<Q', c)

def scramble(rtype, plain, seed):
    k = xor_key(rtype, seed)
    return bytes([seed]) + bytes(b ^ k[i % 8] for i, b in enumerate(plain))

def aes(key, iv, data):
    r = subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-K', key.hex(), '-iv', iv.hex()], input=data, capture_output=True, check=True)
    return r.stdout

KEYS = {fp: (os.urandom(32), os.urandom(16)) for fp in (1, 7, 13)}
iv_state = {fp: iv for fp, (k, iv) in KEYS.items()}
FP_NAME = {1: 'Base', 7: 'DJIFlyCustom', 13: 'Battery'}

records = bytearray()
seed = [7]
def rec(rtype, plain, fp=None):
    seed[0] = (seed[0] * 31 + 11) & 0xFF
    if fp is None:
        payload = plain + b'\x00'   # plaintext records: XOR only, plus the trailing byte
    else:
        key = KEYS[fp][0]
        ct = aes(key, iv_state[fp], plain)
        iv_state[fp] = ct[-16:]
        payload = ct + b'\x00'
    data = scramble(rtype, payload, seed[0])
    records.extend(bytes([rtype]) + struct.pack('<H', len(data)) + data + b'\xff')

# KeyStorage records (type 56): feature point + 32 bytes of "encrypted key" (opaque to us)
for fp in KEYS:
    blob = os.urandom(32)
    rec(56, struct.pack('<HH', fp, len(blob)) + blob)

lat0, lon0 = 60.1699, 24.9384   # Helsinki, synthetic
start_ms = 1793281595078
home_alt = 12.5
rec(2, struct.pack('<ddf', math.radians(lon0), math.radians(lat0), home_alt * 10) + bytes(40), fp=1)
truth = []
for i in range(300):
    t = start_ms + i * 100
    ang = i / 300 * 2 * math.pi
    lat = lat0 + 0.002 * math.sin(ang)
    lon = lon0 + 0.004 * (1 - math.cos(ang))
    h = 50 * math.sin(ang / 2)
    truth.append((t, lat, lon, h))
    rec(5, struct.pack('<BBffq', 0, 0, 5.0, 10.0 * i, t), fp=7)        # custom: UTC timestamp
    bp1 = 6                                                             # GPS (P)
    bp2 = (1 << 3) | (2 << 1)                                           # motors up, in the air
    osd = struct.pack('<dd', math.radians(lon), math.radians(lat)) + struct.pack('<hhhhhhh', int(h * 10), 31, 42, -5, 10, 20, 900)
    osd += bytes([bp1, 0, bp2, 0, 0x10 << 0, 0, 18, 0, 0, 0, 87, 0]) + struct.pack('<H', i) + bytes(5)
    osd += bytes(96 - 16 - len(osd)) if len(osd) < 80 else b''
    rec(1, osd, fp=1)
    if i % 10 == 0:
        bat = bytes([2, 1]) + struct.pack('<iiIIhBB', 23950, -12345, 5000, 4000, 251, 6, 80) + bytes(8)
        rec(22, bat, fp=13)
rec(9, b'Synthetic test tip: all good\x00\x00\x00\x00', fp=7)

# summary block (Details, v6+ layout) inside Auxiliary Info, XOR-scrambled with type 0
det = bytearray(436)
struct.pack_into('<i', det, 83, 300)
struct.pack_into('<q', det, 91, start_ms)
struct.pack_into('<ddf', det, 99, lon0, lat0, 1.234)
struct.pack_into('<ifff', det, 119, 30000, 50.0, 7.5, 2.5)
det[271] = 77
det[280:280 + 18] = b'Synthetic Mavic 3\x00'
info = bytes([1]) + struct.pack('<H', len(det)) + bytes(det) + struct.pack('<H', 0)
aux0 = scramble(0, info, 0x5A)
aux = bytes([0]) + struct.pack('<H', len(aux0)) + aux0
aux += bytes([1]) + struct.pack('<H', 3) + struct.pack('<HB', 4, 3)
prefix = bytearray(100)
rec_start = 100 + len(aux)
struct.pack_into('<QHB', prefix, 0, rec_start, len(det), 14)
log = bytes(prefix) + aux + bytes(records)
open(os.path.join(OUT, 'SYNTHETIC_DJIFlightRecord_v14.txt'), 'wb').write(log)
kc = [[{'featurePoint': 'FR_Standardization_Feature_%s_%d' % (FP_NAME[fp], fp), 'aesKey': base64.b64encode(k).decode(), 'aesIv': base64.b64encode(iv).decode()} for fp, (k, iv) in KEYS.items()]]
json.dump({'keychains': kc, 'truth': truth[::50]}, open(os.path.join(OUT, 'SYNTHETIC_DJI_v14_keys.json'), 'w'))
print('wrote', len(log), 'bytes,', len(truth), 'OSD records')
