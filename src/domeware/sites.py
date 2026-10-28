"""Test sites as named profiles, and SIMULATED runs placed at a real site.

A profile is the radar's declared position and orientation at a test site, saved once from a real session:
  dw site save <session> ouluzone       -> domeware-data/sites/ouluzone/site.json (outside git: a site is data)
  dw site list
Then a SIMULATED run can be put at that exact place, in its real terrain, photo, buildings and forest:
  dw simulate ouluzone <new session> --drones 2 --latency 0.5 --noise 4
It flies the pilot's phase profile (P1 low passes, P2 out to range and back, P3 approach to a stand-off point,
P4 hover), writes runs.csv with the phase windows, and makes radar tracks from it with the stated errors. Every file
is labelled SIMULATED and the manifest says so; the environment comes from the same cached open data as a real run.
Generic sites and other test environments come later; a profile only needs a position, so any place in Finland
works the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from domeware.session import Session, now_utc

SITES = Path.home() / "Work" / "domeware-data" / "sites"
KEYS = ("name", "lat_deg", "lon_deg", "alt_m", "alt_datum", "ground_level_m", "azimuth_deg")


def save(s: Session, name: str) -> Path:
    site = s.manifest.get("site")
    if not site:
        raise SystemExit("This session has no site yet: convert its radar data first (dw convert).")
    p = SITES / name / "site.json"
    if p.exists():
        raise SystemExit(f"{p} exists: profiles are never overwritten; pick another name or delete it by hand")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {k: site.get(k) for k in KEYS} | {"profile": name, "saved_utc": now_utc(), "from_session": s.root.name,
                                            "source": site.get("source")}
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return p


def load(name: str) -> dict:
    p = SITES / name / "site.json"
    if not p.exists():
        raise SystemExit(f"No site profile '{name}'. Known: {', '.join(list_sites()) or 'none'} (dw site save <session> <name>)")
    return json.loads(p.read_text(encoding="utf-8"))


def list_sites() -> list[str]:
    return sorted(p.parent.name for p in SITES.glob("*/site.json"))


SIM_START = pd.Timestamp("2026-10-28 09:00:00", tz="UTC")


def phase_profile(hz: int = 10, start: pd.Timestamp = SIM_START, heading_deg: float = 200.0, offset_m: float = 0.0):
    """One drone flying the pilot's phases on a bearing from the radar, at small-drone speeds (about 15-20 m/s).
    Returns (truth, phase windows)."""
    def leg(seconds):
        n = seconds * hz
        return n, np.linspace(0, 1, n, endpoint=False)
    n1, u1 = leg(120)  # P1: two low crossings of 1.2 km at 5 m, 150 m out: 20 m/s
    n2, u2 = leg(240)  # P2: out from 300 m to 2 km and back at 40 m: about 14 m/s average
    n3, u3 = leg(120)  # P3: approach from 2 km to a 200 m stand-off at 30 m: 15 m/s
    n4, _ = leg(60)    # P4: hover at 300 m, 20 m
    legs = {
        "P1": (np.where(u1 < 0.5, 600 - 2400 * u1, -1800 + 2400 * u1), np.full(n1, 150.0), np.full(n1, 5.0)),
        "P2": (np.zeros(n2), 300 + 1700 * np.sin(np.pi * u2), np.full(n2, 40.0)),
        "P3": (np.zeros(n3), 2000 - 1800 * u3, np.full(n3, 30.0)),
        "P4": (np.zeros(n4), np.full(n4, 300.0), np.full(n4, 20.0)),
    }
    a = np.radians(heading_deg)
    rows, windows, t0 = [], {}, start
    for pid, (x, y, h) in legs.items():  # x across the bearing, y along it
        t = pd.date_range(t0, periods=len(x), freq=pd.Timedelta(seconds=1 / hz))
        e = y * np.sin(a) + (x + offset_m) * np.cos(a)
        nn = y * np.cos(a) - (x + offset_m) * np.sin(a)
        rows.append(pd.DataFrame({"t_utc": t, "e_m": e, "n_m": nn, "u_m": h, "h_rel_takeoff_m": h}))
        windows[pid] = (t[0], t[-1])
        t0 = t[-1] + pd.Timedelta(seconds=1 / hz)
    return pd.concat(rows, ignore_index=True), windows


def simulate(site_name: str, root: Path, drones: int = 1, latency_s: float = 0.4, noise_m: float = 4.0,
             miss_share: float = 0.05, seed: int = 0, fetch_env: bool = True, spacing_m: float = 60.0) -> Session:
    from domeware import runs as R
    from domeware.synthetic import radar_from_truth

    site = load(site_name)
    s = Session(root)
    if s.manifest_path.exists():
        raise SystemExit(f"{root} is already a session: simulate into a new folder")
    s.manifest["site"] = {k: site[k] for k in KEYS} | {"source": f"site profile {site_name} (SIMULATED run)"}
    s.manifest["simulated"] = {"site": site_name, "drones": drones, "spacing_m": spacing_m, "latency_s": latency_s, "noise_m": noise_m,
                               "miss_share": miss_share, "seed": seed, "made_utc": now_utc()}
    (s.streams / "truth").mkdir(parents=True, exist_ok=True)
    (s.streams / "radar").mkdir(parents=True, exist_ok=True)
    lat0, lon0 = site["lat_deg"], site["lon_deg"]
    tracks, windows = [], {}
    for i in range(drones):
        truth, windows = phase_profile(offset_m=spacing_m * i)
        truth["lat_deg"] = lat0 + truth["n_m"] / 111_320.0
        truth["lon_deg"] = lon0 + truth["e_m"] / (111_320.0 * np.cos(np.radians(lat0)))
        truth["source"] = f"SIMULATED phase profile at {site_name}"
        truth.to_parquet(s.streams / "truth" / f"D{i + 1}_SIMULATED.parquet", index=False)
        s.manifest["streams"][f"truth/D{i + 1}_SIMULATED"] = {"time_method": "SIMULATED", "rows": len(truth)}
        r = radar_from_truth(truth[truth["h_rel_takeoff_m"] > 2], latency_s=latency_s, noise_m=noise_m,
                             miss_share=miss_share, track_id=100 + i, seed=seed + i)
        tracks.append(r)
    tr = pd.concat(tracks, ignore_index=True)
    tr["lat_deg"] = lat0 + tr["n_m"] / 111_320.0
    tr["lon_deg"] = lon0 + tr["e_m"] / (111_320.0 * np.cos(np.radians(lat0)))
    tr["alt_m"] = site["alt_m"] + tr["u_m"]
    tr["h_above_site_ground_m"] = tr["u_m"] + site["alt_m"] - (site.get("ground_level_m") or site["alt_m"])
    tr["class_code"], tr["score"], tr["median_rcs_dbsm"], tr["speed_mps"] = 4, 0.9, -20.0, np.nan
    tr.to_parquet(s.streams / "radar" / "tracks.parquet", index=False)
    a, b = tr["t_utc"].min(), tr["t_utc"].max()
    pd.DataFrame({"t_utc": pd.date_range(a.floor("s"), b.ceil("s"), freq="3s")}).to_parquet(s.streams / "radar" / "status.parquet")
    s.manifest["streams"]["radar"] = {"focus_track": 100, "note": "SIMULATED radar tracks"}
    s.save()
    rows = [{"run_id": "SIM-1", "part": "RUN", "start_utc": min(w[0] for w in windows.values()),
             "end_utc": max(w[1] for w in windows.values()), "notes": "SIMULATED phase profile"}]
    rows += [{"run_id": "SIM-1", "part": p, "start_utc": w[0], "end_utc": w[1], "notes": "SIMULATED phase profile"}
             for p, w in windows.items()]
    R._write(s, pd.DataFrame(rows, columns=R.COLUMNS))
    s.note(f"simulate: {drones} SIMULATED drone(s) at {site_name}, latency {latency_s} s, noise {noise_m} m")
    if fetch_env:
        from domeware import env

        try:
            env.fetch(s)
        except SystemExit as e:  # environment is optional for a simulation
            s.note(f"simulate: environment not fetched ({e})")
    return s
