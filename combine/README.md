# Combining domeware and Orbit: the brief for the AI that builds it

For the AI model that builds the combined app (Claude Fable, `claude-fable-5-1`, in Claude Code) and the person who
runs it. Read this first. It says:

- what to build
- where everything is today
- what to keep from each side
- the order of work
- how to check every step
- when to stop and ask people.

## The goal

One app from two, on `main`. Today it holds two parts:

- domeware at the top level: Python. Sessions, readers, analysis, reports, replay.
- Orbit in `app/`: JavaScript field app. Map in 2D and 3D, live radar feed, Windows and Mac installers.

The work is done when:

- there is one reader per file type, one analysis, one set of CWA 18150 numbers, one local server, one user
  interface and one installer
- every number the app shows comes from one place and matches the report
- `uv run pytest -q`, `node app/tests/run_tests_node.js` and every parity check (`uv run python -m domeware.parity`
  and the ones you add) pass
- the docs say how to install, run and test it.

The two old apps keep going on their own branches (`domeware`, `orbit`). What their owners want in the build arrives
in `inbox/` (`inbox/README.md`). Integration work, the steps below, goes to `main` through pull requests.

## Read first, in this order

1. `AGENTS.md`: the rules for every AI assistant.
2. `CLAUDE.md`: the data rules.
3. `docs/decisions/0001-working-together.md` and `docs/decisions/0002-orbit-joins-as-app.md`.
4. `docs/ARCHITECTURE.md` and `docs/DATA.md`: the session contract between all tools.
5. `app/README.md` and `app/INTEGRATION.md`: Orbit's data model and its four ways to connect.
6. `inbox/README.md`.

## The rules that matter most here

- **Pull requests only.** One issue, one branch, one pull request per step. Never push to `main`, and never merge
  your own pull request.
- **No real data in git:** no recordings, and no screenshots or documents of real tests or their results. Work with
  the synthetic fixtures. Real files stay local (`app/tests/real-data/`, `domeware-data/`) with whoever holds them.
- **The data rules** (`CLAUDE.md`):
  - UTC everywhere
  - every height has a datum
  - never half-parse
  - raw files are never changed
  - every cap announces itself.
- **Method differences:** where both sides do the same thing differently, build both behind one named option. Use
  the "Proposed default" column in decision 0002 until Niklas confirms it, and list every option in the pull
  request. Never choose silently.
- **Offline and local:**
  - no new runtime calls to web services
  - downloads are fetched at setup, hash-pinned
  - servers listen on 127.0.0.1 only (a listener on all interfaces makes Windows create firewall rules).

## Who does what today

| Part | domeware (top level) | Orbit (`app/`) | Keep |
|---|---|---|---|
| Robin IRIS XML | `src/domeware/adapters/robin_xml.py`: provenance, quality flags, secrets redacted, raw headers | `app/web/js/radar.js`: fast, generic radar XML/CSV mapping, live messages | domeware's reader as the one in the core; port Orbit's generic mapping and live parsing. Parity: identical on the synthetic file and on the 29 Oct recording. |
| DJI flight records | `adapters/dji.py`, `adapters/dji_decode.py` (DJI key API) | `web/js/dji.js` + `aes.js` (v13+ decryption, keychain cache) | one reader; Orbit's keychain cache. Fix Orbit's take-off altitude first. |
| ArduPilot, MAVLink, other truth | `adapters/truth.py`: DJI .SRT, ArduPilot .bin, MAVLink .tlog, RTKLIB .pos | `web/js/ardupilot.js` (.BIN, multi-vehicle .tlog), `web/js/tracks.js` (CSV, GPX, KML) | the union; parity on Orbit's SYNTHETIC .BIN and .tlog fixtures |
| Weather | `adapters/vaisala.py`, `weather.py` (FMI) | `web/js/weather.js`, `xlsx.js` (Vaisala CSV/xlsx, WXT lines) | the union |
| Association, coverage, errors, clock | `compare.py` (3D, gate 50 m, `best_time_shift`), `dronetracks.py`, `performance.py`, `siap.py`, `stats.py` | `web/js/analysis.js` (gate 75 m, ±3 s, `estimateOffset` ±60 s, fleet) | domeware's methods plus Orbit's fleet logic; differences as options (0002) |
| CWA 18150 | `courageous.py` (Annex H export), `scorecard.py`, `report.py`, confidence intervals in `stats.py` | `web/js/cwa.js` (15 metrics of 13.4.7 as 0-1 scores, fleet pooling) | Orbit's metric set computed once in the core with domeware's intervals; the app shows the core's numbers |
| Heights and frames | `datum.py` (N2000/ellipsoid via the FIN2023N2000 grid), `env.py` (NLS terrain), ENU through ECEF | flat ground (above take-off and radar ground), haversine | domeware's; flat ground only as a labelled fallback without terrain |
| Local server | `hud.py`, `viewer.py` (Rerun), `app.py` (launcher) | `server/orbit_server.py` (token, Host check, tile cache, DJI proxy, live feed), `mac/main.swift` | one Python server: Orbit's token and Host check, file access limited to the session folders |
| User interface | HUD (`src/domeware/web/`: Rerun 3D + glass cards), `dw app` | `web/index.html` + `web/js/app.js` (MapLibre map 2D/3D, timeline, panels), `report.js` | Orbit's map shell, domeware's terrain and instruments. The look is chosen from pictures, by people. |
| Live radar feed | none | `orbit_server.py` (TCP client and listener, UDP, file follow) + `app.js` | Orbit's, listening on 127.0.0.1 unless set otherwise |
| Packaging | uv, `dw setup` | `build.py`, `windows/install.ps1`, Mac app, licence (`web/js/license.js`) | one installer from version tags |
| Tests | pytest, ruff, GitHub (Windows) | `app/tests/run_tests_node.js` (Node), GitHub (Linux) | both, plus the parity checks |

## Order of work

One GitHub issue each, labelled `combine`.

1. **Small known bugs** (list below), one pull request each.
2. **Parity checks before any merging.** Extend `src/domeware/parity.py` (one dump per app, one comparison) and add
   each as a test:
   - DJI header (synthetic v14 log)
   - ArduPilot .BIN and .tlog (Orbit's SYNTHETIC fixtures)
   - association, coverage and clock offset (a synthetic flight like `app/tests/t_cwa.js`)
   - the CWA metrics.
   Record every difference in decision 0002's table.
3. **Track ids:** domeware's Robin reader takes numbers only. Real Robin files use numbers, but other radars and
   Orbit's fixtures use names. Make the core take both. This changes the data contract: `docs/DATA.md` plus a
   decision note.
4. **A bridge:** `dw export orbit <session>` writes the session as `.orbit.json`, which Orbit opens as it is
   (`app/INTEGRATION.md`). Test it: export the synthetic session, load it under Node with
   `SF.exporters.loadSession` and compare.
5. **Duplicates, one at a time:**
   - Robin reader
   - DJI reader
   - ArduPilot and MAVLink
   - weather
   - association, coverage and offset (as options)
   - heights
   - CWA.
   For each one: parity green first, then the loser goes, then parity again.
6. **One local server and one launcher.**
7. **One user interface.** Show two or three options as pictures and let people choose, then build.
8. **One installer,** built from version tags. Decide the licence gate first (0002, "Open").
9. **Docs:** install, run, test. After that the side branches stop (0002, step 7).

## Every pull request

```
uv run ruff check src tests scripts
uv run pytest -q
node app/tests/run_tests_node.js
uv run python -m domeware.parity                 # the synthetic radar file: both readers identical
uv run python -m domeware.parity <a real .xml>   # only on a machine that has the recording; never commit it or its report
```

In the pull request, say:

- what changed in the outputs, with numbers
- which method options were used
- pictures for anything visible (`uv run dw shots <session>` for the HUD, a screenshot for Orbit).

## Set up (Mac or Windows)

1. Install uv (docs.astral.sh/uv), Node 20 or newer, and git.
2. Run:
   ```
   uv sync
   uv run dw setup                       # height grids, leap seconds, 3D viewer: hash-pinned, outside git
   python3 app/tools/fetch_vendor.py     # MapLibre and the Windows embeddable Python: hash-pinned, outside git
   uv run dw doctor
   ```
3. Start domeware with `uv run dw app`.
4. Start Orbit with `python3 app/server/orbit_server.py --dev --no-browser --print-url --token devtoken` and open the
   printed address.

Orbit asks for a licence code before it opens files (`app/web/js/app.js`, `license.js`). Codes come from its author's
licence tool, until people decide whether the gate stays.

No real data is needed for this work. Checks on the real 29 Oct recording are run by whoever holds it.

## Stop and ask people when

- a method difference has no confirmed default
- a change touches the data contract (`docs/DATA.md`): write a decision note in the same pull request
- the licence gate comes up
- anything touches real data, secrets, network calls, or what the installers change on a computer
- the look of the app changes: pictures first.

## Known differences and bugs (found 4-5 Nov 2026)

- **Track ids:** domeware's Robin reader takes numeric ids only (`parse_track` in `adapters/robin_xml.py`). The parity
  check numbers names first, and a test records the difference.
- **DJI take-off altitude:** Orbit reads it 10 times too big for one header version (`app/web/js/dji.js`, `readDetails`:
  `r.f32(267)` without the /10 the other fields have).
- **Live feed listener:** opens on all interfaces by default (`app/server/orbit_server.py`, the two `bind` calls with
  `'0.0.0.0'`).
- **Time zone:** CSV times without a zone take the computer's time zone (`app/web/js/tracks.js`, `tzFor`). Files say
  UTC, or the user is asked.
- **Helper file access:** a token holder can read any file (`/api/file`), open any path (`/api/open-file`) and write a
  PDF anywhere (`/api/pdf`). Limit this to the session folders when the servers merge.
- **Machine id:** Orbit's machine id for the licence reads the Windows MachineGuid (`machine_id` in `orbit_server.py`).
- **Tests without verdicts:** `t_dji_synth.js`, `t_robin.js` and `t_dji.js` only print; give them PASS/FAIL checks.
- **Helper tests:** they need a network, a browser and Unix shell scripts, so they run on a Mac, not on GitHub.
