"""Decode a DJI flight record (v13/v14) with pydjirecord and write domeware Parquet.

NOT RUN against the real log yet: needs Niklas's DJI developer key (see HOW-TO in the report).

Steps (each is a sub-command; only `fetch` goes online, once per log):
  details  offline: header details (aircraft, serials, start time, home) -> <out>/details.json
  request  offline: build the keychain request (version, department, encrypted KeyStorage blobs)
           -> <log>.keychain-request.json   (contains no positions, no API key)
  fetch    ONLINE once: POST the request to https://dev.dji.com/openapi/v1/flight-records/keychains
           with header Api-Key = $DJI_API_KEY; save the answer next to the log as
           <log>.keychains.json (AES key+IV per feature point; same JSON shape as dji-log-parser)
  frames   offline: decrypt with the sidecar keychains -> dji_osd.parquet, dji_events.parquet, meta.json

Run from Work/cuas-toolkit (no change to pyproject/uv.lock):
  uv run --with pydjirecord==1.3.0 python -I <this> details "<log>" <outdir>
  uv run --with pydjirecord==1.3.0 python -I <this> request "<log>"
  set DJI_API_KEY in the user environment, then:
  uv run --with pydjirecord==1.3.0 python -I <this> fetch "<log>"
  uv run --with pydjirecord==1.3.0 python -I <this> frames "<log>" <outdir> --ground-n2000 83.4 \
      --site 65.176667,26.043982,<radar ellipsoidal h> --clock-offset 0
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from enum import Enum
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir; we add only our own folder

import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from domeware.adapters.dji import ConvertOptions, Site, convert, sha256_file  # noqa: E402

ENDPOINT = "https://dev.dji.com/openapi/v1/flight-records/keychains"


def _jsonable(v):
    if dataclasses.is_dataclass(v):
        return {k: _jsonable(x) for k, x in dataclasses.asdict(v).items()}
    if isinstance(v, Enum):
        return v.name
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def load_log(path: Path):
    from pydjirecord import DJILog
    return DJILog.from_bytes(path.read_bytes())


def details_dict(log) -> dict:
    d = _jsonable(log.details)
    # street/city strings are location PII; keep them out of shared outputs
    for k in ("sub_street", "street", "city", "area"):
        if k in d:
            d[k] = "<redacted>"
    d["log_version"] = log.version
    return d


def cmd_details(args) -> None:
    log = load_log(Path(args.log))
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    (out / "details.json").write_text(json.dumps(details_dict(log), indent=2, default=str), encoding="utf-8")
    print(json.dumps(details_dict(log), indent=2, default=str))


def cmd_request(args) -> None:
    log = load_log(Path(args.log))
    req = log.keychains_request()
    body = req.to_dict()
    p = Path(str(args.log) + ".keychain-request.json")
    p.write_text(json.dumps(body, indent=1), encoding="utf-8")
    n = sum(len(g) for g in body.get("keychainsArray", []))
    print(f"wrote {p} (version={body.get('version')}, department={body.get('department')}, "
          f"{len(body.get('keychainsArray', []))} keychain group(s), {n} feature points)")


def cmd_fetch(args) -> None:
    api_key = os.environ.get("DJI_API_KEY", "")
    if not api_key:
        sys.exit("DJI_API_KEY is not set (user environment variable). Nothing sent.")
    log = load_log(Path(args.log))
    req = log.keychains_request()
    kc = req.fetch(api_key, cache=False)  # we keep our own sidecar; pydjirecord's cache expires after 30 days
    from pydjirecord.keychain.feature_point import FeaturePoint
    sidecar = [[{"featurePoint": FeaturePoint(fp.feature_point).api_name, "featurePointId": fp.feature_point,
                 "aesKey": fp.aes_key, "aesIv": fp.aes_iv} for fp in group] for group in kc]
    p = Path(str(args.log) + ".keychains.json")
    p.write_text(json.dumps({"endpoint": ENDPOINT, "log_sha256": sha256_file(args.log), "keychains": sidecar}, indent=1),
                 encoding="utf-8")
    print(f"wrote {p}: {len(kc)} group(s). Treat it like the raw log (it unlocks that log only).")


def load_sidecar(log_path: Path):
    from pydjirecord.keychain.api import KeychainFeaturePoint
    p = Path(str(log_path) + ".keychains.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("log_sha256") and data["log_sha256"] != sha256_file(str(log_path)):
        sys.exit("keychain sidecar belongs to a different file (sha256 mismatch)")
    return [[KeychainFeaturePoint(feature_point=int(e["featurePointId"]), aes_key=e["aesKey"], aes_iv=e["aesIv"])
             for e in g] for g in data["keychains"]]


FRAME_MAP = {  # pydjirecord frame attribute -> dji-log-parser CSV column name
    ("custom", "date_time"): "CUSTOM.dateTime",
    ("osd", "fly_time"): "OSD.flyTime", ("osd", "latitude"): "OSD.latitude", ("osd", "longitude"): "OSD.longitude",
    ("osd", "height"): "OSD.height", ("osd", "vps_height"): "OSD.vpsHeight", ("osd", "altitude"): "OSD.altitude",
    ("osd", "x_speed"): "OSD.xSpeed", ("osd", "y_speed"): "OSD.ySpeed", ("osd", "z_speed"): "OSD.zSpeed",
    ("osd", "pitch"): "OSD.pitch", ("osd", "roll"): "OSD.roll", ("osd", "yaw"): "OSD.yaw",
    ("osd", "flyc_state"): "OSD.flycState", ("osd", "flight_action"): "OSD.flightAction",
    ("osd", "is_gpd_used"): "OSD.isGPSUsed", ("osd", "gps_num"): "OSD.gpsNum", ("osd", "gps_level"): "OSD.gpsLevel",
    ("osd", "go_home_status"): "OSD.goHomeStatus", ("osd", "is_on_ground"): "OSD.isOnGround",
    ("osd", "is_motor_on"): "OSD.isMotorOn", ("osd", "is_vision_used"): "OSD.isVisionUsed",
    ("gimbal", "mode"): "GIMBAL.mode", ("gimbal", "pitch"): "GIMBAL.pitch", ("gimbal", "roll"): "GIMBAL.roll",
    ("gimbal", "yaw"): "GIMBAL.yaw", ("camera", "is_video"): "CAMERA.isVideo",
    ("rc", "uplink_signal"): "RC.uplinkSignal", ("rc", "downlink_signal"): "RC.downlinkSignal",
    ("rc", "aileron"): "RC.aileron", ("rc", "elevator"): "RC.elevator", ("rc", "throttle"): "RC.throttle",
    ("rc", "rudder"): "RC.rudder",
    ("battery", "charge_level"): "BATTERY.chargeLevel", ("battery", "voltage"): "BATTERY.voltage",
    ("battery", "current"): "BATTERY.current", ("battery", "temperature"): "BATTERY.temperature",
    ("home", "latitude"): "HOME.latitude", ("home", "longitude"): "HOME.longitude", ("home", "altitude"): "HOME.altitude",
    ("app", "tip"): "APP.tip", ("app", "warn"): "APP.warn",
}


def frames_to_df(frames) -> pd.DataFrame:
    rows = []
    for f in frames:
        r = {}
        for (grp, attr), col in FRAME_MAP.items():
            v = getattr(getattr(f, grp, None), attr, None)
            if isinstance(v, Enum):
                v = v.name
            elif hasattr(v, "isoformat"):
                v = v.isoformat()
            r[col] = v
        rows.append(r)
    return pd.DataFrame(rows)


def cmd_frames(args) -> None:
    log_path = Path(args.log)
    log = load_log(log_path)
    kc = load_sidecar(log_path) if log.version >= 13 else None
    frames = log.frames(kc)
    df = frames_to_df(frames)
    site = None
    if args.site:
        la, lo, h = (float(x) for x in args.site.split(","))
        site = Site(la, lo, h, name="radar", note="h must be ellipsoidal (ETRS89)")
    opts = ConvertOptions(clock_offset_s=args.clock_offset, takeoff_ground_n2000_m=args.ground_n2000,
                          takeoff_height_offset_m=args.takeoff_offset, site=site,
                          source_sha256=sha256_file(str(log_path)), session_id=args.session, run_id=args.run,
                          extra_meta={"decoder": "pydjirecord==1.3.0", "log_version": log.version})
    t, ev, meta = convert(df, opts, details=details_dict(log))
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out / "dji_osd.parquet", compression="zstd")
    pq.write_table(ev, out / "dji_events.parquet", compression="zstd")
    (out / "dji_meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"{t.num_rows} frames, {ev.num_rows} events -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("details", "request", "fetch", "frames"):
        p = sub.add_parser(name)
        p.add_argument("log")
        if name in ("details", "frames"):
            p.add_argument("outdir")
        if name == "frames":
            p.add_argument("--ground-n2000", type=float, default=None, help="NLS DEM N2000 height at take-off point")
            p.add_argument("--takeoff-offset", type=float, default=0.0)
            p.add_argument("--site", default=None, help="lat,lon,h_ellipsoidal of the ENU origin (radar)")
            p.add_argument("--clock-offset", type=float, default=0.0)
            p.add_argument("--session", default="")
            p.add_argument("--run", default="")
    a = ap.parse_args()
    {"details": cmd_details, "request": cmd_request, "fetch": cmd_fetch, "frames": cmd_frames}[a.cmd](a)


if __name__ == "__main__":
    main()
