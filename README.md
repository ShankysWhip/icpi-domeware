# domeware

ICPI internal tool. Turns a C-UAS test run's files (radar output, drone logs, truth, weather) into one UTC-aligned
session and replays it in its real environment (NLS terrain + aerial photo).

## Use

Short guide in Finnish and English: [docs/USER-GUIDE.md](docs/USER-GUIDE.md). How it is built: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). New laptop: [docs/SETUP.md](docs/SETUP.md).

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
uv run dw app
```

One window: pick a session and press **Start replay**: the replay HUD opens in its own app window (Microsoft Edge
`--app`) and plays the test from its start, looping. **Classic 3D** opens the same replay in the Rerun viewer with
all its charts; **Google Earth** and **Report** open those files. Under the session name a line says what the
session has (radar, drone paths, screenshots, replay ready). **All commands** opens a side panel grouped by what you
want to do (check the data, build, results, share and archive), each command with one line on what it does; a
progress bar runs while it works, and the details stay folded away unless something fails.
Every button runs the same `dw` command as the command line: `uv run dw hud <session> --play`, `uv run dw view <session>`.

### The replay HUD (`dw hud`)

The 3D scene (the Rerun web viewer) with domeware's own panels around it, all on one clock, on a clean white
screen (numbers in monospace, small spaced labels, status lights):

- **Mission clock:** the time (UTC or local), T+ from the start of the activity, the phase, and a readout to choose:
  *Overview* (wind, gust, temperature, drones airborne, radar hold, events), *Drones* (range, height, speed, radar
  tracked or lost for each), *Radar* (tracks alive, share of airborne time tracked per drone), *Events* (latest events, click one to jump there).
- **Weather:** wind dial and numbers; wind, gust, direction, temperature, humidity and pressure in one chart,
  each series switched on or off; click the chart to jump there.
- **Equipment:** a choice list (all, sensors, weather, take-offs, crew), a radar-centred scope (site or whole area,
  with the drones moving on it) and the list itself (a swarm's take-off points fold into one row); pick an item, or
  click it in 3D, to see its position, height and where the number came from.
- **Timeline:** play, ±10 s, start/end, speed ½× to 30×, loop; lanes for the activity and its phases, each drone
  (bright where the radar tracked it, hatched red where it did not), radar tracks alive, events, screenshots as
  thumbnails (hover for a preview, click for full size) and wind. Wheel zooms, double-click fits the activity.
- Keys (the **?** button lists them): Space play/pause, ←/→ 5 s, Shift ←/→ 60 s, Home/End.

It is served from this computer only (127.0.0.1) and stops by itself about 45 s after its window is closed. The data
is written by `dw replay` (`views/hud/`); the web viewer comes from `dw setup` (hash-pinned in `domeware-vendor`,
never in git); nothing is fetched from the internet while it runs.

## Open the replay (manual)

Open it yourself, in your own PowerShell. Bind to 127.0.0.1 so Windows doesn't create firewall rules; the layout is a
file beside the replay:

```
uv run rerun analytics disable
uv run rerun --bind 127.0.0.1 --port auto <session>\views\replay.rrd <session>\views\replay.rbl
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
  `dw runs <session> --card <export>.json` (Run Scorecards v2 page, *Export JSON*). The card itself is kept in
  `cards/`, and the report shows the team's results, fails and values next to what domeware measured.
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

## Radar performance and sharing

- **`dw performance <session>`**: share of airborne seconds with a radar track, by ground range and by height
  (`views/performance.png` + CSV). Each bar shows how many drone-seconds it rests on.
- **`dw kml <session>`**: `views/session.kml` for Google Earth (desktop or phone), drones and their radar tracks in
  the replay colours, with the time slider.

- **`dw timeline <session>`**: `views/timeline_1s.csv`, one row per second: each drone's position and height, its
  radar track, error, class and alarm, weather and drone events. Opens in Excel or PlotJuggler.

All three run inside `dw run`.

## Trust in the numbers

- **Synthetic acceptance tests** (`tests/test_synthetic.py`): SIMULATED radar output with a known latency, clock
  offset, position bias, noise and missed updates; domeware must recover each within a tolerance set in advance.
  Latency and clock offset come out as one time shift (positions alone cannot split them); a fixed bias is found
  only when the drone turns; a hovering drone gives no time shift.
- **Run validity**: every run in `dw scorecard` is VALID or NOT VALID with each reason (truth lost > 3 s, radar silent
  > 10 s, no drone flying). Void runs are kept and counted.
- **Intervals**: the headline detection share carries a 95 % interval from a bootstrap over flights (drone x run),
  because seconds inside one flight are not independent; "flights with a radar track" uses Clopper-Pearson (D13).
  With fewer than 3 flights the report says there are too few for an interval.
- **Independent cross-check**: every run's scorecard also carries Stone Soup's SIAP metrics (Dstl, open source:
  completeness, ambiguity, spuriousness, position and velocity accuracy, track number changes, longest segment), so
  a disputed number can be shown with a published method nobody here wrote.

## Notes and moving data

- **`dw note <session> "text" --at 09:14:30`**: a team note on the UTC clock, one file per author, shown in the
  replay and the report.
- **`dw seal <session>`** then **`dw sync <session> <disk>`**: copies what the team brought (raw, runs, notes),
  never deletes at the destination, merges notes, and checks the copy against the seal. Encrypt the copy for
  transport (e.g. age).

## On-site weather (Vaisala)

A Vaisala WXT536 export (Excel, as from VTT OuluZone) is recognised by its name (`*WXT*.xlsx`) and becomes
`streams/weather/onsite_vaisala.parquet`: 1-minute wind, gust, direction, temperature, pressure, humidity and rain,
in the time zone stated in the file. Values a spreadsheet turned into dates (8.10 -> 8 October) are repaired and
counted, and checked by physics: humidity computed from temperature and dew point must match the measured humidity.
On-site weather shows in the replay, the timeline and the report next to FMI.

## Screenshots and the RF spectrum

Pictures (`.png` / `.jpg`) from a spectrum analyser SD card (e.g. R&S PR200), the radar screen or a phone go into
the replay as picture lanes on the UTC clock. Declare each device's clock once in `<session>/image_clocks.csv`:

```
match,clock
PR200,Europe/Helsinki
IMG_0042.png,2026-10-28T09:14:30Z
```

`match` is a folder or a file name; `clock` is `UTC`, a zone like `Europe/Helsinki`, an offset like `+02:00`, or the
picture's own UTC time. Undeclared pictures are left out, never assumed to be UTC (`dw check` lists them). RF trace
exports (CSV or the analyser's own format) get an adapter once a real sample file exists.

## Equipment

**`dw equipment <session>`** lists all equipment (and people) at the test and where they were: the radar (from its own
declaration), each drone's take-off (from its log, also the header of an encrypted DJI log), the weather station
(from its export header). The team adds what no file shows in `<session>/equipment.csv` (written empty on first run; an older `parties.csv` is read too):

```
name,kind,lat_deg,lon_deg,height_m,heading_deg,fov_deg,range_m,note
PR200 + drone pilot,rf,65.176488,26.043188,1.5,,,,beside the drone pilot; position not measured
EO camera,camera,65.1770,26.0441,3,200,30,800,declared by the team
```

`kind` is radar, operator, drone_home, pilot, weather, rf, camera, sensor, effector or other. `height_m` is above the
ground there; left empty, a default is used and the legend says *assumed*. `heading_deg` + `fov_deg` (+ `range_m`)
draw a coverage wedge, marked DECLARED. A team row with the same name replaces an automatic one; a bad row is left
out with its reason. In the replay every item is a marker with a ground ring and its height pole, the legend is a
table with its type, distance from the radar, height and source (a swarm's take-offs share one label), and the on-site wind is an arrow at the weather station (10 m per m/s,
pointing where the wind blows), one state per measured minute.

## Standard output for evaluators

- **`dw courageous <session>`** (also in `dw run`) writes the radar's output in the **COURAGEOUS data format**
  (CWA 18150 Annex H, JSON Schema 0.4.1): `views/courageous_<radar>_<run>.json`, checked against the official schema,
  with a `.notes.json` beside it that says how IRIS fields were mapped and whether the check passed. Evaluators
  can open it with their own tools (e.g. track2kml). Heights are the radar's own values: the schema asks for EGM96,
  and the radar's altitude datum is still unverified.
- The schema is fetched by `dw setup` (hash-pinned) and is not copied into this repo: its repository has no licence.

## Stress test

`uv run python scripts/stress_test.py <out folder> ouluzone 7 20` puts SIMULATED swarms of 7 and 20 drones at the
site, runs the whole chain with every step timed, then loads each replay in a viewer without a window, plays it and
takes five screenshots. On this desktop (30 Oct): 7 drones 15 s chain, 20 MB replay; 20 drones 26 s, 29 MB; the
viewer loads either in 1.7 s.

## Test sites and simulated runs

- **`dw site save <session> <name>`** keeps a test site (the radar's declared position and orientation) as a named
  profile in `domeware-data/sites/<name>/site.json`, outside git. `dw site list` shows them. OuluZone is saved as
  `ouluzone`.
- **`dw site use <session> <name>`** gives a session that has no radar data yet the site of a profile, so terrain,
  equipment, weather and pictures can be replayed at once. The radar data replaces it when it arrives, and the note
  says how far the radar's own position was from the profile.
- **`dw simulate <site> <new session> --drones 2 --latency 0.5 --noise 4`** puts a SIMULATED run at that exact place,
  in its real terrain, photo, buildings and forest: the pilot's phase profile at small-drone speeds (P1 two low
  crossings at 5 m, P2 out to 2 km and back, P3 approach to a 200 m stand-off, P4 hover), runs.csv with the phase
  windows, and radar tracks with the stated latency, noise and missed updates. Then `dw run` as for real data.
  Useful to rehearse a test day, train the team on the tools, and check that a scoring change does what it should.

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
