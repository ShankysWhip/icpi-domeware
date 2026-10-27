"""MAVLink .tlog (multi-vehicle) and Skybrush .skyc readers. All data here is SIMULATED, built in the test."""

import json
import struct
import zipfile

import pandas as pd

from domeware.adapters.skybrush import read_skyc
from domeware.adapters.truth import read_mavlink_tlog

T0_MS = int(pd.Timestamp("2026-10-28 09:00:00", tz="UTC").timestamp() * 1000)


def _tlog(path):
    from pymavlink.dialects.v20 import ardupilotmega as mav

    buf = bytearray()
    for sysid in (1, 2):
        m = mav.MAVLink(None, srcSystem=sysid, srcComponent=1)
        for k in range(5):
            boot = 60_000 + k * 1000  # 60 s after boot
            for msg in (mav.MAVLink_system_time_message((T0_MS + k * 1000) * 1000, boot),
                        mav.MAVLink_global_position_int_message(boot, int(65.17 * 1e7), int(26.04 * 1e7) + sysid,
                                                                90_000, 10_000 * k, 0, 0, 0, 0)):
                rx_us = (T0_MS + k * 1000 + 250) * 1000  # ground station receives 250 ms late
                buf += struct.pack(">Q", rx_us) + msg.pack(m)
    path.write_bytes(bytes(buf))


def test_tlog_two_vehicles_time_from_system_time(tmp_path):
    p = tmp_path / "gcs.tlog"
    _tlog(p)
    df, meta = read_mavlink_tlog(p)
    assert meta["vehicles"] == [1, 2]
    first = df[df["vehicle_id"] == 1].iloc[0]
    assert first["t_utc"] == pd.Timestamp(T0_MS, unit="ms", tz="UTC")  # vehicle clock, not the 250 ms late receive time
    assert df[df["vehicle_id"] == 2]["h_rel_takeoff_m"].tolist() == [0, 10, 20, 30, 40]


def test_skyc_orientation_and_degE7_origin(tmp_path):
    show = {"swarm": {"drones": [{"type": "generic", "settings": {"name": "d1", "trajectory": {
        "version": 1, "points": [[0, [0, 0, 0], []], [10, [100, 0, 50], []]]}}}]},
            "environment": {"location": {"origin": [651766670, 260439820], "orientation": 90}}}
    p = tmp_path / "show.skyc"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("show.json", json.dumps(show))
    df, meta = read_skyc(p)
    assert meta["origin"] == [65.176667, 26.043982]
    end = df.iloc[-1]
    assert abs(end["lat_deg"] - 65.176667) < 1e-6  # X+ points east when orientation = 90
    assert end["lon_deg"] > 26.043982 and end["h_rel_takeoff_m"] == 50
