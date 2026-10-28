"""views/session.kml: radar, drone paths and radar tracks for Google Earth (desktop or phone), with the time slider.

Drones are drawn in the replay's colours; a radar track that was a drone is drawn in that drone's colour, other
tracks in grey. Heights: drones above their take-off ground (clamped to the terrain Google Earth shows), radar tracks
at the altitude the radar reports. Thinned to one point per second to keep the file small.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import pandas as pd

from domeware.replay import DRONE_RGB, read_truth, truth_files
from domeware.session import Session


def _abgr(rgb, a=255) -> str:
    return f"{a:02x}{rgb[2]:02x}{rgb[1]:02x}{rgb[0]:02x}"


def _track(name: str, d: pd.DataFrame, alt_col: str | None, rgb, mode: str) -> str:
    d = d.dropna(subset=["lat_deg", "lon_deg"]).copy()
    d["t_utc"] = pd.to_datetime(d["t_utc"], utc=True)
    d = d.sort_values("t_utc").groupby(d["t_utc"].dt.floor("1s")).first()
    if d.empty:
        return ""
    whens = "".join(f"<when>{t:%Y-%m-%dT%H:%M:%SZ}</when>" for t in d["t_utc"])
    coords = "".join(f"<gx:coord>{lo:.7f} {la:.7f} {0.0 if alt_col is None else float(h):.1f}</gx:coord>"
                     for lo, la, h in zip(d["lon_deg"], d["lat_deg"], d[alt_col] if alt_col else [0] * len(d), strict=True))
    return (f"<Placemark><name>{escape(name)}</name><Style><LineStyle><color>{_abgr(rgb)}</color><width>3</width></LineStyle>"
            f"<IconStyle><color>{_abgr(rgb)}</color><scale>0.6</scale></IconStyle></Style>"
            f"<gx:Track><altitudeMode>{mode}</altitudeMode>{whens}{coords}</gx:Track></Placemark>")


def build(s: Session) -> str | None:
    site = s.manifest.get("site")
    parts, owner = [], {}
    rp = s.views / "replay_stats.json"
    if rp.exists():
        import json

        owner = {int(k): v for k, v in (json.loads(rp.read_text(encoding="utf-8")).get("track_owner") or {}).items()}
    drones = [f for f in truth_files(s)]
    colour = {f.stem: DRONE_RGB[i % len(DRONE_RGB)] for i, f in enumerate(drones)}
    for f in drones:
        d = read_truth(f)
        if {"lat_deg", "lon_deg", "h_rel_takeoff_m"} <= set(d.columns):
            parts.append(_track(f"drone {f.stem}", d, "h_rel_takeoff_m", colour[f.stem], "relativeToGround"))
    for name in s.manifest["streams"]:
        tp = s.streams / name / "tracks.parquet"
        if not tp.exists():
            continue
        tr = pd.read_parquet(tp, columns=["t_utc", "track_id", "lat_deg", "lon_deg", "alt_m"])
        for tid, g in tr.groupby("track_id"):
            mine = owner.get(int(tid))
            if mine is None and len(g) < 5:  # one-off clutter would bury the picture
                continue
            parts.append(_track(f"radar track {tid}" + (f" = {mine}" if mine else ""), g, "alt_m",
                                colour.get(mine, [150, 150, 150]), "absolute"))
    if not any(parts):
        return None
    radar = (f"<Placemark><name>{escape(site.get('name') or 'radar')}</name><Point><coordinates>{site['lon_deg']:.7f},"
             f"{site['lat_deg']:.7f},0</coordinates></Point></Placemark>") if site else ""
    kml = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2" '
           'xmlns:gx="http://www.google.com/kml/ext/2.2"><Document>'
           f"<name>{escape(s.root.name)}</name>{radar}{''.join(parts)}</Document></kml>")
    s.views.mkdir(parents=True, exist_ok=True)
    (s.views / "session.kml").write_text(kml, encoding="utf-8")
    s.note(f"kml: views/session.kml ({sum(1 for p in parts if p)} paths) - open in Google Earth, use the time slider")
    return kml
