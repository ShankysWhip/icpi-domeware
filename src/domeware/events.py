"""Drone events on the UTC clock -> streams/events/drone_events.parquet.

Columns: t_utc, drone (same name as its truth stream), kind, detail, source, time_method.
  - from every truth stream: take-off, landing, log gap while airborne, weak GPS (when the log has satellites / HDOP)
  - ArduPilot .bin: flight-mode changes, arm / disarm and landing events (EV), failsafes and errors (ERR),
    autopilot messages (MSG); UTC from the log's own GPS time
  - MAVLink .tlog: flight-mode and arm changes (HEARTBEAT), warnings (STATUSTEXT); UTC = ground-station receive time
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from domeware.session import Session

COLUMNS = ["t_utc", "drone", "kind", "detail", "source", "time_method"]
EV_IDS = {10: "armed", 11: "disarmed", 15: "auto-armed", 17: "land complete", 18: "land complete (maybe)", 28: "not landed"}
ERR_SUBSYS = {5: "radio failsafe", 6: "battery failsafe", 7: "GPS failsafe", 8: "GCS failsafe", 9: "fence breach",
              11: "GPS glitch", 12: "crash check", 16: "EKF check", 17: "EKF failsafe", 18: "barometer",
              24: "terrain data"}


def from_truth(df: pd.DataFrame, drone: str, airborne_m: float = 2.0, gap_s: float = 3.0) -> list[dict]:
    d = df.sort_values("t_utc")
    t = pd.to_datetime(d["t_utc"], utc=True)
    out = []
    ev = lambda i, kind, detail: out.append({"t_utc": t.iloc[i], "drone": drone, "kind": kind, "detail": detail})  # noqa: E731
    air = (d["h_rel_takeoff_m"] > airborne_m).to_numpy()
    if air.any():
        rises, falls = np.flatnonzero(~air[:-1] & air[1:]) + 1, np.flatnonzero(air[:-1] & ~air[1:])
        if air[0]:
            rises = np.r_[0, rises]
        if air[-1]:
            falls = np.r_[falls, len(air) - 1]
        for i in rises:
            ev(int(i), "take-off", f"above {airborne_m:g} m over take-off point")
        for i in falls:
            ev(int(i), "landing", f"last fix above {airborne_m:g} m")
        dt = t.diff().dt.total_seconds().to_numpy()
        for i in np.flatnonzero((dt > gap_s) & air):
            ev(int(i) - 1, "log gap", f"no fix for {dt[i]:.1f} s while airborne")
    if "nsats" in d and d["nsats"].notna().any():
        weak = (d["nsats"] < 6).to_numpy()
        for i in np.flatnonzero(weak & ~np.r_[False, weak[:-1]]):
            ev(int(i), "weak GPS", f"{int(d['nsats'].iloc[i])} satellites")
    if "hdop" in d and d["hdop"].notna().any():
        bad = (d["hdop"] > 2.0).to_numpy()
        for i in np.flatnonzero(bad & ~np.r_[False, bad[:-1]]):
            ev(int(i), "weak GPS", f"HDOP {d['hdop'].iloc[i]:.1f}")
    return out


def _mode_names(vehicle: str) -> dict[int, str]:
    from pymavlink import mavutil

    table = {"plane": mavutil.mode_mapping_apm, "rover": mavutil.mode_mapping_rover}.get(vehicle, mavutil.mode_mapping_acm)
    return {v: k for k, v in table.items()}


def from_ardupilot_bin(path: Path) -> tuple[list[dict], str]:
    from pymavlink import DFReader

    from domeware.adapters.truth import GPS_UTC_LEAP_S

    log = DFReader.DFReader_binary(str(path), zero_time_base=True)
    offsets, raw, vehicle = [], [], "copter"
    while (m := log.recv_match(type=["GPS", "MODE", "EV", "ERR", "MSG"])) is not None:
        d, typ = m.to_dict(), m.get_type()
        if typ == "GPS":
            if d.get("I", 0) == 0 and d.get("Status", 0) >= 3 and d.get("GWk"):
                utc = pd.Timestamp("1980-01-06", tz="UTC") + pd.Timedelta(weeks=d["GWk"], milliseconds=d["GMS"]) \
                    - pd.Timedelta(seconds=GPS_UTC_LEAP_S)
                offsets.append(utc.value - d["TimeUS"] * 1000)
            continue
        if typ == "MSG":
            text = str(d.get("Message", "")).strip()
            low = text.lower()
            vehicle = "plane" if "arduplane" in low else "rover" if "rover" in low else vehicle
            raw.append((d["TimeUS"], "autopilot message", text))
        elif typ == "MODE":
            raw.append((d["TimeUS"], "flight mode", d.get("ModeNum", d.get("Mode"))))
        elif typ == "EV":
            raw.append((d["TimeUS"], "event", EV_IDS.get(d.get("Id"), f"event id {d.get('Id')}")))
        elif typ == "ERR":
            name = ERR_SUBSYS.get(d.get("Subsys"), f"subsystem {d.get('Subsys')}")
            raw.append((d["TimeUS"], "failsafe / error", f"{name} {'cleared' if d.get('ECode') == 0 else 'code ' + str(d.get('ECode'))}"))
    if not offsets:
        return [], "no GPS time in the log: events not placed on UTC"
    off = float(np.median(offsets))
    modes = _mode_names(vehicle)
    out = []
    for us, kind, detail in raw:
        if kind == "flight mode":
            detail = modes.get(int(detail), f"mode {detail}") if str(detail).lstrip("-").isdigit() else str(detail)
        out.append({"t_utc": pd.Timestamp(int(us * 1000 + off), tz="UTC"), "drone": path.stem, "kind": kind, "detail": detail})
    return out, f"log time + GPS offset (median of {len(offsets)} fixes)"


def from_mavlink_tlog(path: Path) -> tuple[list[dict], str]:
    from pymavlink import mavutil

    mlog = mavutil.mavlink_connection(str(path), dialect="ardupilotmega")
    last: dict[int, tuple] = {}
    out = []
    while (m := mlog.recv_match(type=["HEARTBEAT", "STATUSTEXT"])) is not None:
        sysid, t = m.get_srcSystem(), pd.Timestamp(getattr(m, "_timestamp", 0), unit="s", tz="UTC")
        drone = f"{path.stem}_sys{sysid}"
        if m.get_type() == "HEARTBEAT":
            if m.type in (6, 18, 26, 27):  # ground station, onboard computer, gimbal, ADS-B: not a vehicle
                continue
            state = (mavutil.mode_string_v10(m), bool(m.base_mode & 128))
            prev = last.get(sysid)
            if prev is None or state[0] != prev[0]:
                out.append({"t_utc": t, "drone": drone, "kind": "flight mode", "detail": state[0]})
            if prev is not None and state[1] != prev[1]:
                out.append({"t_utc": t, "drone": drone, "kind": "event", "detail": "armed" if state[1] else "disarmed"})
            last[sysid] = state
        elif m.severity <= 4:  # warning or worse
            out.append({"t_utc": t, "drone": drone, "kind": "autopilot message", "detail": str(m.text).strip()})
    return out, "ground-station receive time (includes link delay)"


def build(s: Session) -> pd.DataFrame:
    from domeware.replay import truth_files

    rows = []
    for f in truth_files(s):
        for r in from_truth(pd.read_parquet(f), f.stem):
            rows.append(dict(r, source=f"truth/{f.name}", time_method="truth stream time"))
    for role, reader in (("ardupilot_bin", from_ardupilot_bin), ("mavlink_tlog", from_mavlink_tlog)):
        for f in s.raw_files(role):
            try:
                evs, method = reader(f)
            except Exception as e:  # one unreadable log must not stop the others
                s.note(f"events: {f.name}: not read ({e})")
                continue
            rows += [dict(r, source=f.name, time_method=method) for r in evs]
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["t_utc"] = pd.to_datetime(df["t_utc"], utc=True).dt.as_unit("ns")
    df = df.sort_values(["t_utc", "drone"]).reset_index(drop=True)
    out = s.streams / "events"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "drone_events.parquet", index=False)
    counts = df["kind"].value_counts().to_dict()
    s.manifest["streams"]["events/drone_events"] = {"rows": len(df), "kinds": counts}
    s.note(f"events: {len(df)} drone events ({', '.join(f'{k} {v}' for k, v in counts.items())})")
    s.save()
    return df
