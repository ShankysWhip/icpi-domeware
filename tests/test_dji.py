"""SIMULATED test of dji_to_domeware.convert (no real DJI data involved).

Builds a synthetic 10 Hz flight near the IRIS site: 20 s on ground, climb to 100 m, an
out-and-back leg, landing. The app clock (CUSTOM.dateTime) updates on ~2/3 of frames
with +-40 ms jitter, like the real log's 1050 Custom vs 1602 OSD records.
Writes synthetic/dji_osd_SIMULATED.parquet and checks the invariants.

Run: uv run python -I test_dji_to_domeware.py   (from Work/cuas-toolkit, give full path)
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from domeware.adapters.dji import QA_T_INTERP, ConvertOptions, Site, convert, n2000_to_ellipsoidal  # noqa: E402

RADAR_LAT, RADAR_LON = 65.176667, 26.043982
rng = np.random.default_rng(20261021)


def synth() -> pd.DataFrame:
    n = 1600
    t0 = pd.Timestamp("2026-10-21T10:29:35Z")
    on_ground = 200
    fly = np.where(np.arange(n) < on_ground, 0.0, (np.arange(n) - on_ground) / 10.0)
    fly[-50:] = fly[-51]  # landed: flyTime stops increasing
    h = np.clip((fly - 0) * 5.0, 0, 100.0)
    h[-120:] = np.linspace(h[-121], 0, 120)
    dist = np.clip((fly - 25) * 10.0, 0, 600)
    dist = np.where(fly > 85, np.clip(600 - (fly - 85) * 10.0, 0, 600), dist)
    home_lat, home_lon = 65.1700, 26.0500
    lat = home_lat + dist / 111_320.0
    lon = np.full(n, home_lon)
    true_t = t0 + pd.to_timedelta(np.arange(n) * 100, unit="ms")
    fresh = rng.random(n) < 0.66
    app = true_t + pd.to_timedelta(rng.normal(0, 40, n).round(), unit="ms")
    app_ff = pd.Series(np.where(fresh, app.as_unit("ns").asi8.astype(float), np.nan)).ffill().bfill()
    df = pd.DataFrame({
        "CUSTOM.dateTime": pd.to_datetime(app_ff, unit="ns", utc=True).map(lambda x: x.isoformat()),
        "OSD.flyTime": fly, "OSD.latitude": lat, "OSD.longitude": lon, "OSD.height": h,
        "OSD.vpsHeight": np.where(h < 10, h, 0), "OSD.altitude": h + 37.2,
        "OSD.xSpeed": np.gradient(dist) * 10, "OSD.ySpeed": 0.0, "OSD.zSpeed": -np.gradient(h) * 10,
        "OSD.pitch": 0.0, "OSD.roll": 0.0, "OSD.yaw": 0.0,
        "OSD.isGPSUsed": "true", "OSD.gpsNum": 18, "OSD.gpsLevel": 5,
        "OSD.isOnGround": np.where(h > 0.2, "false", "true"), "OSD.isMotorOn": np.where(fly > 0, "true", "false"),
        "OSD.flycState": np.where(h > 0.2, "GPSAtti", "AutoTakeoff"),
        "HOME.latitude": home_lat, "HOME.longitude": home_lon, "HOME.altitude": 37.2,
        "APP.tip": [""] * n, "APP.warn": [""] * n,
    })
    df.loc[900, "APP.warn"] = "SIMULATED warning"
    return df, true_t


def main() -> None:
    df, true_t = synth()
    radar_h_ell, desc = n2000_to_ellipsoidal(np.array([RADAR_LAT]), np.array([RADAR_LON]), np.array([97.5]))
    site = Site(RADAR_LAT, RADAR_LON, float(radar_h_ell[0]), name="IRIS (antenna 97.5 m, datum ASSUMED N2000 for this test)")
    opts = ConvertOptions(takeoff_ground_n2000_m=80.0, site=site, source_sha256="SIMULATED" * 8,
                          session_id="SIM", run_id="SIM-1", extra_meta={"provenance": "SIMULATED"})
    t, ev, meta = convert(df, opts, details={"aircraft_name": "SIMULATED"})
    out = t.to_pandas()

    err_ms = (out["t_utc"] - pd.Series(true_t)).dt.total_seconds().abs() * 1000
    flying = out["t_utc_method"] == "flytime_anchor"
    print("geoid/vertical op:", desc[:120])
    print("radar h_ellip:", round(site.h_ellip_m, 2), "(N ~", round(site.h_ellip_m - 97.5, 2), "m)")
    print("time model:", meta["time_model"]["segments"])
    print("t_utc error ms  flying p95=%.1f max=%.1f | ground p95=%.1f" % (
        err_ms[flying].quantile(0.95), err_ms[flying].max(), err_ms[~flying].quantile(0.95)))
    print("rows by method:", out["t_utc_method"].value_counts().to_dict())
    print("max slant range m:", round(float(out["slant_range_m"].max()), 1),
          "| max h_n2000:", round(float(out["h_n2000_m"].max()), 1))
    print("events:", ev.to_pandas()[["fly_time_s", "kind", "text"]].to_dict("records"))

    assert err_ms[flying].max() < 60, "flyTime-anchored time should be within ~1 jitter sample"
    assert out["t_utc"].is_monotonic_increasing
    assert ((out.loc[~flying, "qa_flags"] & QA_T_INTERP) > 0).all()
    assert abs(out["h_n2000_m"].max() - 180.0) < 0.01
    assert 16.0 < site.h_ellip_m - 97.5 < 19.5, "FIN2023N2000 undulation near Oulu should be ~17-18 m"
    syn = HERE / "synthetic"
    syn.mkdir(exist_ok=True)
    pq.write_table(t, syn / "dji_osd_SIMULATED.parquet", compression="zstd")
    pq.write_table(ev, syn / "dji_events_SIMULATED.parquet", compression="zstd")
    print("OK ->", syn)


if __name__ == "__main__":
    main()
