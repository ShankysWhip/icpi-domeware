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

## Open the replay (easy way)

```
uv run dw view <session folder>
```

## Open the replay (manual)

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
| MAVLink `.tlog` (ground-station log) | converted as drone truth, **one stream per vehicle (sysid)**; time from each vehicle's SYSTEM_TIME |
| Skybrush show `.skyc` | **planned** trajectories per drone, shown as white lines (show time; axis convention ASSUMED until verified) |

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
With several drones, each track belongs to one drone only: the nearest one.

## Runs, drone events and scorecards

- **`dw runs <session>`** writes `runs.csv` (run id, part, start and end UTC) from the drones' take-offs and landings.
  Edit it freely; it is never overwritten. Phase times (P1-P4) come from a hand edit or from a scorecard export:
  `dw runs <session> --card <export>.json` (Run Scorecards v2 page, *Export JSON*).
- **`dw events <session>`** puts drone events on the UTC clock: take-off, landing, log gaps, weak GPS, and from
  ArduPilot `.bin` / MAVLink `.tlog` the flight-mode changes, arm/disarm, failsafes and warnings. They show in the
  replay's Events panel and in the report.
- **`dw scorecard <session>`** measures the Blue (radar) and Red (drone) checks per run and phase and writes
  `views/scorecard_<run>.json`. On the Run Scorecards v2 page, *Import domeware values* fills the empty Value and
  Note fields; reviewers still set Pass / Fail. Phase checks are filled only from real phase times. A per-second table
  of which radar track was which drone is in `views/association_<run>.parquet`.

All three run inside `dw run` when drone truth exists.

## Swarm, video and map accuracy in the replay

- **Swarm:** every drone has its own colour and name. A radar track that was a drone wears that drone's colour and its
  label ends with `= <drone>`. Lanes show each drone's height and the radar's horizontal error per drone; the Legend
  panel lists drones, colours and their radar tracks.
- **Video:** `.MP4` / `.MOV` files are placed on the UTC clock from the DJI `.SRT` with the same name, or from
  `video_offsets.csv` (`file,start_utc`, e.g. from filming a GPS clock). A clip with no known time is left out, never
  guessed. H.264/H.265 playback in the viewer needs ffmpeg on the PATH (`dw doctor` shows it).
- **Map accuracy:** NLS maps are ETRS89 and GPS is ITRF; in Finland they now differ by ~0.9 m. The replay moves the
  NLS layers onto the GPS frame (ITRF2020 to ETRF2000 at today's epoch).
- **Speed:** a radar file that has not changed is not converted again; an 80 MB radar file converts in under 10 s.

## In the field and after

- **`dw check <session>`** (offline, seconds): did the radar record the whole flight, does every drone log have
  positions without gaps, do drone and radar clocks overlap, can every video be timed. OK / WARN / FAIL per line.
- **`dw compare`** also gives the first radar plot near the drone (before any track) and the **radar time shift**: how
  many seconds the radar's times must move to fit GPS best (latency plus clock). It fills scorecard check B2.
- **`dw report`** writes `views/report.html` too: one file with the pictures inside, opens on a phone.

## Team

- **`dw list`** shows every session in one table and writes `domeware-data/sessions/index.html`.
- **`dw doctor`** checks a laptop: Python, packages, height grids, leap-second table, viewer. It prints a fix for
  anything missing and changes nothing. New co-worker: `git clone`, `uv sync`, `uv run dw doctor`.

## Rules

- Raw files are never changed.
- Every height has a datum.
- Times are UTC.
- Nothing is half-parsed.
- Secrets never leave the raw folder.

The vertical alignment of the terrain to the radar's declared ground level is reported in `views/replay_stats.json`.
