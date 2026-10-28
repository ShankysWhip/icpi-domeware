# Domeware: 2-day work plan (28–29 Oct 2026)

Goal: domeware handles a **real, full test day** (several runs, several drones, video, events), runs fast on a big
session, sets up on a co-worker's laptop in one step, and its numbers can be trusted.

**How to read the estimates:** "time" is Claude's working time. "usage" is the share of one session's usage limit,
judged from earlier work (the last four features took about 3 %). Each work package ends with a commit and a ✅ here.

Status: ⬜ not started · 🔄 in progress · ✅ done · ⛔ blocked (needs Niklas)

## Day 1 (Tue 28 Oct): a full test day, end to end

| # | Work package | What you get | Time | Usage | Status |
|---|---|---|---|---|---|
| 1 | **Runs inside a session** | A `runs.yaml` (run id, UTC start/end, scenario, drones, notes) written automatically from take-offs and landings, editable by hand. Per-run replay, compare and report sections. | 2 h | 5 % | ✅ `runs.csv` (CSV: no new dependency), `dw runs --card` |
| 2 | **Drone events** | Take-off, landing, flight-mode changes, failsafes, GPS loss and low battery from ArduPilot/tlog/DJI. Shown in the event log, on the timeline and in the report. | 1.5 h | 4 % | ✅ truth, ArduPilot .bin, MAVLink .tlog (DJI flight-record events need the API key) |
| 3 | **Video in the replay** | DJI/GoPro MP4 synced to UTC (SRT time, or a time-slate offset) as a video panel next to the 3D view. | 2.5 h | 6 % | ✅ SRT or video_offsets.csv; never a guessed time (needs a real DJI clip to confirm the codec plays) |
| 4 | **Big-session speed** | Multi-hour radar XML and 7 drones: streaming parse, `dw run` skips steps whose inputs haven't changed, timing shown per step. Target: a full day in under 2 min. | 2 h | 5 % | ✅ 80 MB radar file < 10 s; unchanged files skipped; one parse instead of two |
| 5 | **Swarm view** | All drones with their own colour, legend and ids; per-drone compare table; "which radar track was which drone" timeline. | 1.5 h | 4 % | ✅ colours, names, legend, per-drone height and error lanes, track = drone labels |

**Day 1 total: about 9.5 h, about 24 %.**

## Day 2 (Wed 29 Oct): trustworthy numbers and the team

| # | Work package | What you get | Time | Usage | Status |
|---|---|---|---|---|---|
| 6 | **Exact coordinates** | The ~0.9 m ETRS89↔WGS84 plate-motion shift done properly with the NKG grid. A checked datum for every height column. Report says "sub-metre" only when true. | 1.5 h | 4 % | ✅ 0.89 m shift applied in the replay (ITRF2020 to ETRF2000); heights already on the N2000 grid |
| 7 | **Scoring hooks (CWA-style)** | Explicit 1 s time grid; association table (which track was which drone, per second); detection under the 4 definitions (first plot, confirmed track, classified, alarm); gaps > 1 s. No final CWA scores yet: hooks only, as decided. | 3 h | 8 % | ✅ 1 s grid, association, gaps; first plot / first track / DRONE class / alarm; radar time shift |
| 8 | **One-step setup for co-workers** | `setup.ps1`: uv sync, vendor grids + leap table download, PlotJuggler hint, self-test. A co-worker goes from `git clone` to a working replay in about 10 min. | 1.5 h | 4 % | ✅ `dw setup` downloads and hash-checks grids, then `dw doctor` |
| 9 | **Session overview** | `dw list`: all sessions in one table (date, site, runs, drones, first-detection delay, errors). An HTML index page opens with a double-click. | 1.5 h | 4 % | ✅ |
| 10 | **Docs + clean-up** | User guide (FI/EN short), architecture page, updated README; code review pass; all tests green. | 1.5 h | 4 % | ✅ docs/USER-GUIDE.md (FI/EN), docs/ARCHITECTURE.md, ruff rules pinned and clean |

**Day 2 total: about 9 h, about 24 %.**

**Two days: about 18.5 h, about 50 % of one session's usage.** The rest stays as buffer for fixes and your requests.

**Added 28 Oct:** `dw scorecard` fills the Run Scorecards v2 page (Import domeware values), measured per run and phase.

## Blocked on Niklas (do these when you can, no rush)

| What | Why | Unblocks |
|---|---|---|
| Push origin in GitHub Desktop (after each work package) | Co-workers get the code | Team testing |
| A real DJI .MP4 + .SRT from any flight | Real caption format; video sync | #3 fully verified |
| A real ArduPilot .bin / .tlog | Real message timing, events | #2 fully verified |
| DJI developer API key (free) | Decoding encrypted FlightRecord logs | Encrypted DJI path |
| Org admin: enable Remote Control | Steering Claude from the Mac | Remote work |

## Rules while working unattended

- One work package at a time. Commit after each. Tests stay green.
- Nothing is sent outside the laptop (no push, no uploads) without Niklas.
- Real data is never changed: raw files stay read-only. New test data is labelled SIMULATED.
- If a package is blocked, mark ⛔ with the reason and move to the next.
