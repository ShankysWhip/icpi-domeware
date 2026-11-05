# 0002 Orbit joins this repo as app/

Date: 2026-11-05. Status: accepted by Niklas on 5 Nov. The repo is `ShankysWhip/icpi-domeware`, with three branches.
The Orbit author's agreement is still open (below).

## Context

ICPI has two apps for the same test data. domeware (this repo) is Python: sessions, analysis, reports, replay. Orbit
(`salmiseba-tech/ORBIT-ICPI`) is a JavaScript field app: map in 2D and 3D, live radar feed, Windows and Mac installers.
They overlap: both read Robin IRIS XML and DJI logs, both match drone truth to radar tracks, both score against
CWA 18150. Two copies drift apart and every fix is made twice. Decision 0001 already said that the offline app gets its
own folder and area, uses the shared data model, and that one Robin reader stays.

## Decision

- **One repo, three branches (Niklas, 5 Nov):**
  - `main` is the build: the combined app, domeware's core at the top and Orbit in `app/`.
  - `domeware` carries the old domeware app, developed on its own. Its base is domeware's history.
  - `orbit` carries the old Orbit app, developed on its own, with the app at the top of the branch as in its old repo.
  Orbit came in from `c48639e` (the only commit in its repo, so no history is lost).
- **Things reach the build through `inbox/`.**
  - Each side writes what it wants in the build as an item in `inbox/from-domeware/` or `inbox/from-orbit/`.
  - A strong AI model (Claude Fable) brings in one item per pull request, runs both parts' tests and reports what
    changed in the outputs.
  - `inbox/synced.json` records how far each side branch has been taken in.
  - Method differences (the table below) go to people first.
  - Integration work on the build itself (the steps below) goes straight to `main`, through pull requests.
- **The old GitHub repos are archived** once both sides work on their branches here.
- **The import is byte-exact.** The 59 imported files are Orbit's committed bytes, checked blob by blob.
- **Left out of the import, and why:**
  - `tests/fixtures/SIMULATED_truth_from_radar_airdata.csv` and `tests/make_fixture.py`: made from the real 29 Oct
    radar tracks with the real take-off point.
  - `docs/checklist-analysis-tab.jpg`, `docs/redesign-2.5-before-after.jpg`, `docs/windows-test-checklist.html`,
    `docs/make_checklist.py`: they show or list the real 29 Oct flight and its results.
  - `vendor/maplibre/`: an unused copy of `web/vendor/`.
  - MapLibre GL JS 5.24.0 and the Windows embeddable Python 3.14.10 (14 MB): fetched instead (next point).
- **Third-party files are fetched, not committed:** `app/tools/fetch_vendor.py`, hash-pinned (AGENTS.md rule 11).
  The pins are the files Orbit shipped; the publishers' downloads (npm, python.org) matched them byte for byte on
  5 Nov 2026.
- **Fixtures:** small synthetic files made by a script in the repo (`SYNTHETIC_*`) may live in git; real recordings
  never do (rule 8). The app's tests that need a recording read `app/tests/real-data/` or `ORBIT_REAL_DATA` and print
  SKIP without it.
- **Tests:** GitHub runs the app's reader, analysis and CWA tests under Node on every pull request, next to
  domeware's (`node app/tests/run_tests_node.js`). The same files still run with jsc on a Mac, from `app/`.
- **Areas:** `app/` becomes the Orbit author's area once agreed (`.github/CODEOWNERS`). The data contract, the rules
  and the tests on GitHub stay shared.

## How the two become one

In this order, one pull request each:

1. **Bugs found in the intake (4 Nov):**
   - the DJI take-off altitude is read 10x too big (`app/web/js/dji.js`)
   - the live listener opens on all interfaces (`app/server/orbit_server.py`)
   - times follow the computer's time zone instead of UTC (`app/web/js/tracks.js`).
2. **A bridge:** `dw export orbit` writes a session as `.orbit.json`, which Orbit opens as it is (`app/INTEGRATION.md`).
   One view then shows domeware's heights (N2000, terrain) and decoded drone paths. One synthetic reference session
   is used by both test sets.
3. **Duplicates, one at a time:** a side-by-side run on the same data, a decision line here, then the loser is removed.
   - Robin reader
   - DJI reader
   - clock sync
   - association
   - heights
   - CWA metrics.
4. **One local server.** Both are stdlib Python on 127.0.0.1 behind an Edge app window.
5. **One user interface.** Options are shown as pictures first.
6. **One installer,** built from version tags.
7. **Side branches stop.** When a part is fully combined, its side branch stops and that part is developed on `main`
   only.

## Method differences: people decide, then the AI ports

Until Niklas confirms a row, the AI building the combined app uses the proposed default, keeps the other as a named
option, and lists both in its pull request (`combine/README.md`). The proposals are Claude's, from the 4 Nov intake.

| | domeware | Orbit | Proposed default (Niklas confirms) |
|---|---|---|---|
| Drone-to-track gate | 50 m | 75 m | 50 m; 75 m reported alongside |
| Coverage tolerance | 2 s | 3 s | 2 s; 3 s reported alongside |
| Clock offset | bias-free fit within ±5 s | search within ±60 s | the ±60 s search, then the bias-free fit |
| Heights | N2000 or ellipsoid through the FIN2023N2000 grid, NLS terrain | flat ground: above take-off and radar ground | domeware's; flat ground only as a labelled fallback without terrain |
| Distances | ENU around the radar, through ECEF | spherical (haversine) | ENU through ECEF |
| CWA 18150 | COURAGEOUS export, scorecards, confidence intervals | 15 metrics as 0-1 scores, fleet pooling | Orbit's metric set computed in the core, with domeware's intervals |
| Robin track ids | numbers only | any text | both (a data contract change) |

## Open

- The repo is `ShankysWhip/icpi-domeware` for now. A transfer to an ICPI organisation later keeps everything.
- Running the AI step automatically (a GitHub Action running Fable on new inbox items) needs an Anthropic API key and
  costs money per run. Decide later; until then it runs in a Claude Code session.
- The Orbit author agrees in writing:
  - ICPI owns the code
  - the import is cleaned as above
  - his repo is archived after the switch
  - the real-derived files are removed from its history.
- Licence gate: off for internal use?
- Who installs the app (ICPI laptops only, or customers too). This decides packaging and Mac support.
- Print-only tests (`t_dji_synth.js`, `t_robin.js`, `t_dji.js`) should get PASS/FAIL checks.
- The helper's own tests (`test_helper_api.py`, `test_instances.py`) need a network, a browser and Unix shell
  scripts. They run on the developer's Mac, not yet on GitHub.

## Consequences

- Each side keeps working as before on its own branch. The build grows one item at a time, each with a review and a
  test run, under one set of rules for every developer and AI assistant.
- A fix to an old app is made on its branch and reaches the build through the inbox, so both stay right. Until a side
  branch stops, that is a second step for every change.
- The first weeks carry duplicates on purpose. Each one is removed only after a side-by-side run shows what changes.
- Real-data tests and screenshots move to local folders, so each developer keeps their own copy of the recordings.
