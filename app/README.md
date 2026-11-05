# Orbit

Replays a drone flight log against radar tracks and weather on a satellite map (2D and 3D),
measures how well the radar saw the drone, takes a live radar feed, and keeps working offline.

## Downloads (built)

| File | For | Notes |
|---|---|---|
| `dist/Orbit-2.5.1-mac.dmg` | macOS 11+, Apple Silicon and Intel | Drag the app to Applications. Ad-hoc signed, not notarised. |
| `dist/Orbit-2.5.1-windows.zip` | Windows 10/11 (x64, runs on ARM too) | Unzip, run `Install Orbit.cmd`. Python 3.14.10 embeddable is inside. |

## What it reads

- **Drone log**: DJI flight records (`DJIFlightRecord_*.txt`, every format version). From format 13 on (DJI Fly, DJI Pilot 2) the records are AES-encrypted; the app fetches each log's keys from DJI once (needs a free DJI developer API key in Settings and internet) and caches them, so the log opens offline afterwards. Also CSV exports (Airdata, PhantomHelp, Litchi, dji-log, any CSV with time + lat/lon; feet/mph units are converted), GPX, KML/KMZ `gx:Track`.
- **Radar**: Robin Radar IRIS XML (concatenated `<Robin>` messages: tracks, drone plots, sensor status, image-line events). Any other XML or CSV with track records: fields are detected by name and can be remapped in a dialog; positions can be lat/lon, range/azimuth/elevation or x/y metres from a radar origin.
- **Weather**: Vaisala observation exports (CSV or xlsx with metadata rows), plain logger CSVs, raw WXT ASCII lines (`0R0,Dm=…,Sm=…`). Repairs cells a spreadsheet turned into dates (46207 → 4.07).
- **Session** files (`.orbit.json`) saved from the app.

## How it is built

```
web/          the app (HTML/CSS/JS, MapLibre GL JS 5.24.0 from tools/fetch_vendor.py) — the same files run on both platforms
  js/util.js xml.js aes.js dji.js radar.js weather.js xlsx.js tracks.js   readers (no DOM needed; tested with jsc)
  js/ardupilot.js  ArduPilot DataFlash .BIN (GPS week/ms → UTC) and MAVLink .tlog (one drone per system id)
  js/cwa.js        CWA 18150:2024 metrics and 0-1 scores, one drone or several flying together
  js/analysis.js   drone-truth vs radar: matching, coverage, breaks, errors, range bins, auto clock sync
  js/mapview.js    MapLibre base map + canvas overlay projected with MapLibre's camera matrix (true 3D heights)
  js/timeline.js   lanes: height, range, radar bands, track count, error, wind, events
  js/app.js        UI, playback, live feed, settings, exports;  js/exporters.js  report/CSV/KML/session
server/orbit_server.py   Windows helper (stdlib Python): serves web/, tile cache, DJI keychain proxy,
                             live feed (TCP client/listener, UDP, file follow), native dialogs, open queue
mac/main.swift               Mac app: WKWebView window + the same helper API in Swift (Network.framework)
windows/install.ps1, uninstall.ps1   per-user install, shortcuts, "Open with", Apps entry
build.py                     builds dist/ (python3 build.py [mac|windows|check])
tests/                       jsc reader tests (DJI, Robin, ArduPilot, CWA, fleet), helper API test, start/window test, fake radar,
                             make_ardupilot.py (synthetic .BIN/.tlog/radar), fixtures (SIMULATED / SYNTHETIC only)
tools/license/                orbit-license (Swift, signs license codes) + Orbit License Maker.app (build.sh); vendor's Mac only
tools/fetch_vendor.py         fetches MapLibre and the Windows embeddable Python (not in git), hash-pinned
docs/                        Windows test checklist (HTML source; python3 docs/make_checklist.py prints the PDF)
```

On Windows the helper is a separate process from its Edge window. One process starts it even when Explorer
opens several files at once (`helper.starting` claim, the others hand over their files). It quits about 45 s after the
Edge process it started has exited and never while that process runs, because Edge pings only once a minute from a
window hidden for 5 minutes; when it cannot see the window's process, it waits for 150 s without pings.

The page talks to its helper over `http://127.0.0.1:<port>` with a per-launch token; the helper rejects
requests without it or with a foreign Host header. Map tiles go through `/tiles/<source>/z/x/y` and are cached in
the data folder (Mac: `~/Library/Application Support/Orbit`, Windows: `%LOCALAPPDATA%\Orbit`), together
with settings, DJI keychains and live recordings.

## Develop and test

```
python3 tools/fetch_vendor.py                     # once: MapLibre and the Windows embeddable Python, hash-checked
python3 server/orbit_server.py --dev --no-browser --print-url --token devtoken   # then open the printed URL
python3 tests/test_helper_api.py --base http://127.0.0.1:47615 --token devtoken      # 28 checks incl. live modes
python3 tests/test_instances.py                     # 17 checks: three starts at once, window watching (temp data folder)
build/Orbit.app/Contents/MacOS/Orbit --serve-only --port 47700 --token devtoken  # Swift helper, same test
/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc tests/jsc_prelude.js tests/t_robin.js
build/Orbit.app/Contents/MacOS/Orbit --open FILE --snapshot out.png --wait 10 --eval 'JSON.stringify(SF.state.analysis.coverage)'
python3 tests/fake_radar.py serve --port 5055 --file radar.xml --speed 10            # fake live radar
```

`tests/fixtures/SIMULATED_truth_from_radar_airdata.csv` is a made-up drone track derived from radar data (its clock
is deliberately 2.0 s off, for the auto-sync test). `SYNTHETIC_DJIFlightRecord_v14.txt` is a generated encrypted
DJI log with its keys in `SYNTHETIC_DJI_v14_keys.json`. Neither is real flight data.

## Licences

MapLibre GL JS: BSD-3-Clause (`web/vendor/LICENSE-maplibre.txt`). Python: PSF licence (`python/LICENSE.txt` in the
Windows build). DJI log format knowledge follows the open-source dji-log-parser (MIT). Map imagery © Esri, Maxar,
Earthstar Geographics; map data © OpenStreetMap contributors; OpenTopoMap (CC-BY-SA); terrain: Mapzen/AWS Terrain Tiles.
