# Connecting another app to Orbit

For developers who want Orbit to exchange data with another drone, radar or test-range tool, or to reuse its parts.
Read `README.md` first for the architecture, the build and the tests.

## Data model

Everything is in memory as columnar arrays: one array per field, all the same length.

- **Time** is UTC epoch milliseconds.
- **Positions** are WGS-84 degrees.
- **Heights** are metres.
- **Speeds** are m/s.
- **Missing values** are `NaN`.

**Drone.** Produced by `SF.tracks.fromRows / fromGPX / fromKML`, `SF.dji.decode` and `SF.ardupilot.binToDrone / tlogToDrones`.

```js
{ t, lat, lon, h /* above take-off */, alt /* above sea level */, hs, vs, yaw, bat, volt, gps, mode, motor, ground,
  source, file, name }
```

**Radar.** Build a radar store like this:

```js
const store = SF.radar.newStore('generic', 'My radar');
SF.radar.addUpdate(store, trackId, { t, lat, lon, alt, spd, hdg, el, cls: 'DRONE', score, rcs, alarm });
SF.radar.finalize(store);   // picks each track's main class, sorts tracks by first sighting
// store.tracks[i] = { id, t, lat, lon, alt, spd, hdg, el, cls, score, rcs, mrcs, alarm, mainClass, first, last }
```

**Weather.** `wx.data` holds `{ t, wind, gust, dir, temp, rh, pres, rain, ... }`, one array per quantity.
`SF.weather.at(wx, 'wind', t)` reads the value nearest to a time.

## Four ways to connect

### 1. Files (loosest coupling, recommended first)

- **Opening files in Orbit.** Orbit opens what your app already writes:
  - drone tracks as CSV (time + lat/lon columns, found by name), GPX or KML `gx:Track`;
  - radar tracks as XML or CSV (fields detected by name, remappable in a dialog);
  - Vaisala-style weather CSV/xlsx.
- **Session files.** An Orbit session (`.orbit.json`, written by `SF.exporters.session`) is the whole state in one JSON:
  ```js
  { app: 'Orbit', version: 1, saved, files, offsets, drone, radar, weather, aopts, meta, cwa, fleet?, sel? }
  ```
  `radar.classes` is an array of `[class, count]` pairs. `null` stands for a missing number. Another app can write one
  and Orbit opens it as-is (`SF.exporters.loadSession` is the reader).
- **Exports from Orbit.** Orbit writes:
  - samples and match results as CSV (`SF.exporters.csv`);
  - KML;
  - an HTML/PDF test report.

### 2. Reuse the readers and the analysis

The files in `web/js` have no DOM dependency and run in any JavaScript engine. The tests run them under `jsc`; see
`tests/jsc_prelude.js`.

Readers:
- `dji.js` for DJI flight records, including v13+ decryption with keys from DJI;
- `ardupilot.js` for `.BIN` and `.tlog`;
- `radar.js` for Robin IRIS XML and generic XML/CSV;
- `weather.js`, `tracks.js`, `xml.js`, `xlsx.js`.

Analysis:
- `SF.analysis.run(drone, radar, wx, opts)` matches drone truth to radar tracks: coverage, breaks, errors, range bins.
- `SF.analysis.estimateOffset` estimates the clock offset between the two.
- `SF.cwa.run(drones, radar, analyses, opts, cfg)` gives the CWA 18150:2024 metrics and 0-1 scores, for one drone or a fleet.

### 3. Live data

Orbit's live feed can take radar or track data from another program over:
- a TCP client,
- a TCP listener,
- UDP,
- or a file that keeps growing.

It accepts Robin `<Robin>` XML messages, or any repeating XML record that it maps once, like a file import.
`tests/fake_radar.py` shows the sending side. The helper endpoints are `/api/live/start`, `/api/live/poll` and `/api/live/stop`.

### 4. Starting Orbit from your app

- **Mac:** `Orbit.app/Contents/MacOS/Orbit --open FILE` (repeat `--open` for several files).
- **Windows:** the installer registers "Open with Orbit".
- **Already running:** a second start hands its files to the running copy through `/api/open-queue`.

## The local helper API

The helper is the Swift helper on Mac, `server/orbit_server.py` on Windows:
- It listens only on `127.0.0.1`.
- Every `/api/*` call needs the per-launch token, in the `X-SF-Token` header or as `?token=`.
- It refuses foreign `Host` headers.

Endpoints:

| Area | Endpoints |
|---|---|
| App | `/api/info`, `/api/ping` |
| Settings | `/api/settings` |
| Network | `/api/net` |
| DJI | `/api/dji/keychains` (proxy to DJI, keys cached) |
| Map tiles | `/api/tiles/prefetch`, `/api/tiles/stats`, plus `/tiles/<source>/z/x/y` |
| Files | `/api/file`, `/api/open-queue` |
| Live feed | `/api/live/start`, `/api/live/poll`, `/api/live/stop` |

For a fixed token in development:

```
python3 server/orbit_server.py --dev --token devtoken
```

## Not in this repository

- **Build outputs and the marketing video project.** Run `python3 build.py` to build the app.
- **Each user's DJI developer API key.** It's entered in Settings and stored in the user's data folder, never in the code.
- **The licence signing key.** It stays on the vendor's Mac. `tools/license` contains only the tool that uses it.
  Orbit checks licences offline with a public key.
- **Real flight, radar or weather recordings.** `tests/fixtures` holds only synthetic or simulated files.
