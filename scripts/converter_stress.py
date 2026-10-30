"""Stress and robustness test for every converter. Nothing here is real data except, if present, the 21 Oct Robin
sample, which is only repeated (time-shifted) to make a large file.

  uv run python scripts/converter_stress.py OUT_DIR [--hours 1]

For each converter: a large SIMULATED input (about `hours` of data at the device's real rate), timed, with rows per
second; then broken inputs (empty, random bytes, cut in half): each must either convert or fail with a clear error,
never hang or crash with something unexpected. Results: OUT_DIR/converter_stress.json and a table on screen.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

T0 = pd.Timestamp("2026-10-28 09:00:00", tz="UTC")
CLEAN_ERRORS = (ValueError, KeyError, OSError, EOFError, struct.error)  # "this is not a valid file", said clearly


def make_srt(path: Path, n: int) -> None:
    """DJI video captions at 30 fps, local time (Helsinki), as the Mavic 3 / Matrice 4 write them."""
    t = (T0.tz_convert("Europe/Helsinki") + pd.to_timedelta(np.arange(n) * 1000 / 30, unit="ms")).strftime("%Y-%m-%d %H:%M:%S.%f")
    lat = 65.17 + np.arange(n) * 1e-7
    out = []
    for i in range(n):
        out.append(f"{i + 1}\n00:00:00,000 --> 00:00:00,033\n<font size=\"28\">FrameCnt: {i + 1}, DiffTime: 33ms\n{t[i][:-3]}\n"
                   f"[iso: 100] [shutter: 1/1000.0] [fnum: 2.8] [ev: 0] [color_md: default] [focal_len: 24.00] "
                   f"[latitude: {lat[i]:.6f}] [longitude: 26.040000] [rel_alt: 40.000 abs_alt: 117.650] </font>\n")
    path.write_text("\n".join(out), encoding="utf-8")


def make_pos(path: Path, n: int) -> None:
    """RTKLIB .pos at 5 Hz, GPST."""
    t = (T0 + pd.to_timedelta(np.arange(n) * 200, unit="ms")).strftime("%Y/%m/%d %H:%M:%S.%f")
    lines = ["% program   : RTKLIB ver.2.4.3 SIMULATED", "%  GPST                  latitude(deg) longitude(deg)  height(m)   Q  ns"]
    lines += [f"{t[i][:-3]}   65.170000000   26.040000000   120.0000   1  18   0.01 0.01 0.02" for i in range(n)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_tlog(path: Path, n: int, vehicles: int = 2) -> None:
    """A ground-station telemetry log: SYSTEM_TIME at 1 Hz, GLOBAL_POSITION_INT at 10 Hz per vehicle."""
    from pymavlink.dialects.v20 import ardupilotmega as mav

    t0_ms = int(T0.timestamp() * 1000)
    buf = bytearray()
    links = [mav.MAVLink(None, srcSystem=v + 1, srcComponent=1) for v in range(vehicles)]
    for k in range(n):
        boot = 60_000 + k * 100
        for v, m in enumerate(links):
            msgs = [mav.MAVLink_global_position_int_message(boot, int(65.17e7), int(26.04e7) + v, 90_000, 40_000, 0, 0, 0, 0)]
            if k % 10 == 0:
                msgs.insert(0, mav.MAVLink_system_time_message((t0_ms + k * 100) * 1000, boot))
            for msg in msgs:
                buf += struct.pack(">Q", (t0_ms + k * 100 + 250) * 1000) + msg.pack(m)
    path.write_bytes(bytes(buf))


def make_dji_csv(n: int) -> pd.DataFrame:
    """A DJI flight-log CSV export (Airdata / FlightReader columns) at 10 Hz."""
    t = T0 + pd.to_timedelta(np.arange(n) * 100, unit="ms")
    fly = np.maximum(0, (np.arange(n) - 200) / 10.0)
    h = np.clip(fly * 5, 0, 60)
    return pd.DataFrame({
        "CUSTOM.dateTime": t.map(lambda x: x.isoformat()), "OSD.flyTime": fly, "OSD.latitude": 65.17 + fly * 1e-5,
        "OSD.longitude": 26.04, "OSD.height": h, "OSD.vpsHeight": 0.0, "OSD.altitude": h + 37.2, "OSD.xSpeed": 5.0,
        "OSD.ySpeed": 0.0, "OSD.zSpeed": 0.0, "OSD.pitch": 0.0, "OSD.roll": 0.0, "OSD.yaw": 0.0, "OSD.isGPSUsed": "true",
        "OSD.gpsNum": 18, "OSD.gpsLevel": 5, "OSD.isOnGround": np.where(h > 0.2, "false", "true"),
        "OSD.isMotorOn": np.where(fly > 0, "true", "false"), "OSD.flycState": "GPSAtti", "HOME.latitude": 65.17,
        "HOME.longitude": 26.04, "HOME.altitude": 37.2, "APP.tip": "", "APP.warn": ""})


def make_wxt(path: Path, minutes: int) -> None:
    """A Vaisala WXT536 Excel export, one row per minute with the empty row between rows."""
    import openpyxl

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append(["Generated (dd-MM-yyyy HH:mm:ss)", "28-10-2026 09:00:00", "Time Zone:", "UTC"])
    ws.append(["Device:", "SIM WXT536"])
    ws.append(["Timestamp", "Wind speed mean PT1M 0.0 m (m/s) - X", "Wind gust PT1M 0.0 m (m/s) - X",
               "Air temperature mean PT1M 0.0 m (C) - X", "Wind direction mean PT1M 0.0 m (deg) - X"])
    for i in range(minutes):
        ts = (T0 + pd.Timedelta(minutes=i)).strftime("%d-%m-%Y %H:%M")
        ws.append([ts, f"{4 + (i % 5) * 0.1:.2f}", f"{6 + (i % 7) * 0.1:.2f}", "7.50", "190.00"])
        ws.append([None, None, None, None, None])
    wb.save(path)


def robin_big(out: Path, copies: int) -> Path | None:
    """The 21 Oct Robin sample repeated `copies` times, each copy one hour later (only if the sample is here)."""
    src = Path(os.environ.get("DOMEWARE_SAMPLE_ARCHIVE", str(Path.home() / "Work" / "domeware-data" / "archive-2026-10-21"))) / "ozmasto2.xml"
    if not src.exists():
        return None
    text = src.read_text(encoding="utf-8", errors="replace")
    parts = [text]
    for k in range(1, copies):
        parts.append(text.replace("2026-10-21T", f"2026-10-21T{k:02d}SHIFT").replace(f"2026-10-21T{k:02d}SHIFT1", f"2026-10-{21 + k // 10}T1"))
    p = out / "robin_big.xml"
    p.write_text("\n".join(parts), encoding="utf-8")
    return p


def timed(fn):
    t = time.perf_counter()
    res = fn()
    return res, round(time.perf_counter() - t, 2)


def broken(path: Path, good: bytes, reader) -> dict:
    """Empty, random bytes, and the good file cut in half: each must convert or fail with a clear error."""
    out = {}
    rng = np.random.default_rng(0)
    for name, data in (("empty", b""), ("random", rng.integers(0, 256, 4096, dtype=np.uint8).tobytes()),
                       ("half", good[: len(good) // 2])):
        path.write_bytes(data)
        try:
            res = reader(path)
            n = len(res[0]) if isinstance(res, tuple) else (len(res) if hasattr(res, "__len__") else "?")
            out[name] = f"converted ({n} rows)"
        except CLEAN_ERRORS as e:
            out[name] = f"clean error: {type(e).__name__}: {str(e)[:70]}"
        except Exception as e:  # noqa: BLE001 - this is what the test is looking for
            out[name] = f"UNEXPECTED {type(e).__name__}: {str(e)[:70]}"
    return out


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    hours = float(argv[argv.index("--hours") + 1]) if "--hours" in argv else 1.0
    out.mkdir(parents=True, exist_ok=True)
    from domeware.adapters import dji as D
    from domeware.adapters import robin_xml as R
    from domeware.adapters.truth import read_dji_srt, read_mavlink_tlog, read_rtklib_pos
    from domeware.adapters.vaisala import read_wxt_excel

    results = []

    def case(name, path, make, reader, rate_note):
        t = time.perf_counter()
        make()
        made = round(time.perf_counter() - t, 1)
        res, sec = timed(lambda: reader(path))
        rows = len(res[0]) if isinstance(res, tuple) else len(res)
        good = path.read_bytes()
        rec = {"converter": name, "input": rate_note, "mb": round(len(good) / 1e6, 1), "rows": rows, "seconds": sec,
               "rows_per_s": int(rows / max(sec, 1e-3)), "made_in_s": made}
        rec["broken"] = broken(path.with_name("broken_" + path.name), good, reader)
        results.append(rec)
        print(f"{name:14s} {rec['mb']:7.1f} MB {rows:9d} rows {sec:7.2f} s  ({rec['rows_per_s']:,} rows/s)")

    n_srt, n_pos, n_tlog, n_dji = int(30 * 3600 * hours), int(5 * 3600 * hours), int(10 * 3600 * hours), int(10 * 3600 * hours)
    case("dji_srt", out / "big.SRT", lambda: make_srt(out / "big.SRT", n_srt), read_dji_srt, f"{hours:g} h at 30 fps")
    case("rtklib_pos", out / "big.pos", lambda: make_pos(out / "big.pos", n_pos), read_rtklib_pos, f"{hours:g} h at 5 Hz")
    case("mavlink_tlog", out / "big.tlog", lambda: make_tlog(out / "big.tlog", n_tlog), read_mavlink_tlog, f"{hours:g} h, 2 vehicles, 10 Hz")
    case("vaisala_wxt", out / "big_wxt.xlsx", lambda: make_wxt(out / "big_wxt.xlsx", int(60 * 24 * 7 * max(hours, 1))),
         read_wxt_excel, "7 days of 1-min rows")
    df = make_dji_csv(n_dji)
    res, sec = timed(lambda: D.convert(df, D.ConvertOptions()))
    results.append({"converter": "dji_csv", "input": f"{hours:g} h at 10 Hz", "rows": len(df), "seconds": sec,
                    "rows_per_s": int(len(df) / max(sec, 1e-3))})
    print(f"{'dji_csv':14s} {'':7s}    {len(df):9d} rows {sec:7.2f} s  ({int(len(df) / max(sec, 1e-3)):,} rows/s)")
    big = robin_big(out, 6)
    if big is not None:
        rep, sec = timed(lambda: R.convert(big, out / "robin_out", None, [], quicklook=False, hash_raw=False))
        rows = (rep.get("tracks") or {}).get("rows_kept")
        mb = round(big.stat().st_size / 1e6, 1)
        results.append({"converter": "robin_xml", "input": "21 Oct sample x6", "mb": mb, "rows": rows, "seconds": sec,
                        "mb_per_s": round(mb / max(sec, 1e-3), 1)})
        print(f"{'robin_xml':14s} {mb:7.1f} MB {rows or 0:9d} rows {sec:7.2f} s  ({mb / max(sec, 1e-3):.1f} MB/s)")
    (out / "converter_stress.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("\nbroken inputs:")
    for r in results:
        for k, v in (r.get("broken") or {}).items():
            print(f"  {r['converter']:14s} {k:7s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
