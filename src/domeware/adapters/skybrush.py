"""Skybrush show file (.skyc) -> PLANNED trajectory per drone, for planned-vs-flown in the replay.

.skyc = ZIP with JSON. Trajectories are in show coordinates (metres, z up) relative to the show origin; time is
seconds from show start. Georeference comes from the optional location block: origin [lat, lon, (alt)] and the
orientation of the X+ axis in degrees from north. Clean-room reader (Skybrush's own readers are GPL).
ASSUMED until checked on a real show: Y+ is 90 deg counter-clockwise from X+ (right-handed, z up), and origin
values above 1000 are degE7 (alt in mm).
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pandas as pd


def _find(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            if (r := _find(v, key)) is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            if (r := _find(v, key)) is not None:
                return r
    return None


def read_skyc(path: Path) -> tuple[pd.DataFrame, dict]:
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.endswith(".json") and "show" in n.lower()), None) \
            or next((n for n in z.namelist() if n.endswith(".json")), None)
        if name is None:
            raise ValueError(f"{path.name}: no JSON inside; not a Skybrush .skyc")
        show = json.loads(z.read(name))
    drones = _find(show, "drones")
    loc = _find(show, "location")
    if not drones:
        raise ValueError(f"{path.name}: no swarm.drones in {name}")
    if not loc or "origin" not in loc:
        raise ValueError(f"{path.name}: show has no geographic location; set the show origin in Skybrush Studio")
    o = loc["origin"]
    lat0, lon0 = (o[0] / 1e7, o[1] / 1e7) if abs(o[0]) > 1000 else (o[0], o[1])
    th = math.radians(float(loc.get("orientation", 0.0)))
    rows = []
    for i, d in enumerate(drones):
        st = d.get("settings", d)
        traj = st.get("trajectory") or {}
        label = st.get("name") or f"drone{i + 1}"
        for p in traj.get("points", []):
            t, (x, y, zz) = float(p[0]), p[1][:3]
            e = x * math.sin(th) - y * math.cos(th)
            n = x * math.cos(th) + y * math.sin(th)
            rows.append((label, t, lat0 + n / 111320.0, lon0 + e / (111320.0 * math.cos(math.radians(lat0))), float(zz)))
    if not rows:
        raise ValueError(f"{path.name}: drones have no trajectory points")
    df = pd.DataFrame(rows, columns=["drone", "t_show_s", "lat_deg", "lon_deg", "h_rel_takeoff_m"])
    meta = {"origin": [lat0, lon0], "orientation_deg": math.degrees(th), "drones": int(df["drone"].nunique()),
            "points": len(df), "duration_s": float(df["t_show_s"].max()),
            "note": "PLANNED. Time is show time (s from start), not UTC. Axis convention ASSUMED until verified."}
    return df, meta
