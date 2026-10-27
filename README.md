# domeware

ICPI internal tool. Turns a C-UAS test run's files (radar output, drone logs, truth, weather) into one UTC-aligned
session and replays it in its real environment (NLS terrain + aerial photo).

## Use

```
uv run dw run <session folder> <files or folders from the test>
```

That one command:
1. **ingest:** copies everything into `<session>/raw/`, hashed and read-only, sorted by detected type;
2. **convert:** writes Parquet to `<session>/streams/` for every known type;
3. **env:** downloads the NLS 2 m elevation model and the newest orthophoto around the radar (Finland, no key);
4. **replay:** writes `<session>/views/replay.rrd` and `quicklook_map.png`.

The steps can also be run one by one: `dw ingest`, `dw convert`, `dw env`, `dw replay`, plus `dw verify` (hash check)
and `dw info`. Adding files later: `dw ingest` again, then `dw convert` and `dw replay`.

## Open the replay

Open it yourself, in your own PowerShell. Bind to 127.0.0.1 so Windows doesn't create firewall rules:

```
rerun analytics disable
rerun --bind 127.0.0.1 --port auto <session>\views\replay.rrd
```

If a Windows Firewall prompt appears anyway, press **Cancel**.

## What it reads today

| Detected type | Status |
|---|---|
| Robin IRIS XML stream (`<Robin>` fragments) | converted: tracks, plots, status, motion, image meta; site position read from the data; secrets removed |
| Robin `IrisRadar.config` / `IrisTrackerCa.map` | settings kept, secrets removed |
| Robin `.PLOTS.gz` / `.IMG` | stored raw; text headers read |
| DJI CSV (dji-log-parser style `OSD.*` columns) | converted, on the same ENU frame as the radar |
| DJI FlightRecord `.txt` v13+ (encrypted) | stored raw; decode with a DJI API key (`python -m domeware.adapters.dji_decode`) or export CSV |
| DJI video captions `.SRT` (turn on "Video Captions" in DJI Fly) | converted as drone truth; local time → UTC, DST-aware |
| ArduPilot `.bin` | converted as drone truth (GPS week/ms − 18 s leap) |
| RTKLIB `.pos` (RTK truth) | converted as drone truth; GPST → UTC, ellipsoid → N2000 (fails loudly without the geoid grid) |
| MAVLink `.tlog` | stored raw; converter next |

## Environment, weather and line of sight (automatic in `dw run`, or `dw env`)

- **Terrain:** NLS 2 m elevation model + newest aerial photo.
- **Features:** buildings and power lines (NLS topographic database), forest heights (Metsäkeskus canopy model).
- **Weather:** FMI observations for the run's time window, from the nearest station with data per parameter.
- **MODELLED line of sight from the radar:** the lowest height above ground at which a target is visible past terrain,
  forest and buildings (k = 4/3). Geometry only, not detection probability.

## Report

`dw report <session>` (also part of `dw run`) writes `views/report.md`. It covers: data received, site, radar events,
radar-vs-truth numbers, flight-time weather, environment, pictures, sources and known limits.

## Radar vs drone

When drone truth exists, `dw run` also runs `dw compare` and writes `views/compare.md` + `compare_<log>.png`:
- **Delays:** from airborne to the first radar track, first DRONE class and first alarm.
- **Tracking:** share of the flight tracked, track breaks.
- **Position error:** horizontal, vertical and 3D (median / p90 / RMS).
- **Range:** at first track, and the maximum range tracked.
- **Other tracks:** how many other tracks appeared during the flight.

Rule: a radar track is the drone if its median 3D distance to truth is under the gate (`--gate`, default 50 m).

## Rules

- Raw files are never changed.
- Every height has a datum.
- Times are UTC.
- Nothing is half-parsed.
- Secrets never leave the raw folder.

The vertical alignment of the terrain to the radar's declared ground level is reported in `views/replay_stats.json`.
