"""views/session.kml: radar, drone paths and radar tracks for Google Earth (desktop or phone), with the time slider.

Drones are drawn in the replay's colours; a radar track that was a drone is drawn in that drone's colour, other
tracks in grey. Heights: drones above their take-off ground (clamped to the terrain Google Earth shows), radar tracks
at the altitude the radar reports. Thinned to one point per second to keep the file small.
Each drone's whole path is also drawn with a see-through curtain down to the ground, so its height reads at a glance;
all equipment (dw equipment) is a named pin, and the radar has range rings.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

import pandas as pd

from domeware.replay import drone_colours, read_truth, truth_files
from domeware.session import Session


def _abgr(rgb, a=255) -> str:
    return f"{a:02x}{rgb[2]:02x}{rgb[1]:02x}{rgb[0]:02x}"


def _track(name: str, d: pd.DataFrame, alt_col: str | None, rgb, mode: str) -> str:
    d = d.dropna(subset=["lat_deg", "lon_deg", *([alt_col] if alt_col else [])]).copy()
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


def _curtain(name: str, d: pd.DataFrame, rgb) -> str:
    d = d.dropna(subset=["lat_deg", "lon_deg", "h_rel_takeoff_m"]).copy()
    d["t_utc"] = pd.to_datetime(d["t_utc"], utc=True)
    d = d.sort_values("t_utc").groupby(d["t_utc"].dt.floor("1s")).first()
    if d.empty:
        return ""
    coords = " ".join(f"{lo:.7f},{la:.7f},{h:.1f}" for lo, la, h in zip(d["lon_deg"], d["lat_deg"], d["h_rel_takeoff_m"], strict=True))
    return (f"<Placemark><name>{escape(name)} path</name><Style><LineStyle><color>{_abgr(rgb)}</color><width>2</width>"
            f"</LineStyle><PolyStyle><color>{_abgr(rgb, 70)}</color></PolyStyle></Style><LineString><extrude>1</extrude>"
            f"<tessellate>1</tessellate><altitudeMode>relativeToGround</altitudeMode><coordinates>{coords}</coordinates>"
            f"</LineString></Placemark>")


def _equipment(s: Session) -> str:
    from domeware.equipment import load

    df, _ = load(s)
    pins = []
    for r in df.itertuples():
        desc = f"{r.kind}; {r.height_m:.1f} m above ground{' (assumed)' if r.height_assumed else ''}; source: {r.source}"
        if isinstance(r.note, str) and r.note:
            desc += f". {r.note}"
        pins.append(f"<Placemark><name>{escape(r.name)}</name><description>{escape(desc)}</description>"
                    f"<Point><coordinates>{r.lon_deg:.7f},{r.lat_deg:.7f},0</coordinates></Point></Placemark>")
    return f"<Folder><name>Equipment</name>{''.join(pins)}</Folder>" if pins else ""


def _rings(site: dict) -> str:
    out = []
    for r_m in (500, 1000, 2000, 3000):
        pts = []
        for i in range(121):
            a = 2 * math.pi * i / 120
            pts.append(f"{site['lon_deg'] + r_m * math.sin(a) / (111320 * math.cos(math.radians(site['lat_deg']))):.7f},"
                       f"{site['lat_deg'] + r_m * math.cos(a) / 110574:.7f},0")
        out.append(f"<Placemark><name>{r_m} m</name><Style><LineStyle><color>b4ffffff</color><width>1</width></LineStyle>"
                   f"</Style><LineString><tessellate>1</tessellate><coordinates>{' '.join(pts)}</coordinates>"
                   f"</LineString></Placemark>")
    return f"<Folder><name>Radar range rings</name>{''.join(out)}</Folder>"


def build(s: Session) -> str | None:
    site = s.manifest.get("site")
    parts, owner = [], {}
    rp = s.views / "replay_stats.json"
    if rp.exists():
        import json

        owner = {int(k): v for k, v in (json.loads(rp.read_text(encoding="utf-8")).get("track_owner") or {}).items()}
    drones = [f for f in truth_files(s)]
    colour = drone_colours(s)
    for f in drones:
        d = read_truth(f)
        if {"lat_deg", "lon_deg", "h_rel_takeoff_m"} <= set(d.columns):
            parts.append(_track(f"drone {f.stem}", d, "h_rel_takeoff_m", colour[f.stem], "relativeToGround"))
            parts.append(_curtain(f"drone {f.stem}", d, colour[f.stem]))
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
    if not site and not any(parts):
        return None
    around = (_equipment(s) + _rings(site)) if site else ""
    kml = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2" '
           'xmlns:gx="http://www.google.com/kml/ext/2.2"><Document>'
           f"<name>{escape(s.root.name)}</name>{around}{''.join(parts)}</Document></kml>")
    s.views.mkdir(parents=True, exist_ok=True)
    (s.views / "session.kml").write_text(kml, encoding="utf-8")
    s.note(f"kml: views/session.kml ({sum(1 for p in parts if p)} paths) - open in Google Earth, use the time slider")
    return kml
