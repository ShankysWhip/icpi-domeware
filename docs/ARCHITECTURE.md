# domeware architecture

One rule shapes everything: **a session folder is the whole truth about one test day.** Raw files go in once
(hashed, read-only); everything else is derived and can be rebuilt with `dw run`.

```
raw files ──ingest──> raw/<role>/ (sha256 in manifest.json, read-only)
                         │
                      convert ── adapters/ (robin_xml, dji, truth, skybrush) ──> streams/*/*.parquet  (UTC, ENU about the radar)
                         │
          env (NLS DEM + ortho) · features (buildings, power lines, canopy) · los (MODELLED) · weather (FMI)
                         │
     events (drone side) · runs (runs.csv) · compare (radar vs truth) · scorecard (per run / phase)
                         │
            replay (views/replay.rrd + blueprint) · report (views/report.md + .html) · check · list
```

| Module | Job | Key rule |
|---|---|---|
| `session.py` | folder layout, ingest, hashes, manifest | raw is never overwritten; `dw verify` re-checks hashes |
| `adapters/robin_xml.py` | Robin IRIS XML -> tracks, plots, status | repeated track states removed; config secrets redacted |
| `adapters/truth.py` | DJI SRT, ArduPilot .bin, MAVLink .tlog, RTKLIB .pos | every time to UTC with its method recorded; heights N2000 via vendored grid |
| `adapters/skybrush.py` | planned show paths | axis convention ASSUMED until a real show confirms it |
| `env.py`, `features.py`, `los.py`, `weather.py` | real environment for any Finnish site | open data only; LOS is geometry, not detection |
| `datum.py` | ETRS89 maps vs GPS | NLS layers shifted ~0.9 m onto the GPS frame |
| `compare.py` | radar vs truth | each track belongs to the nearest drone within the gate; time shift; first plot |
| `events.py`, `runs.py` | drone events, run and phase windows | `runs.csv` is the team's file: written once, never overwritten |
| `scorecard.py` | Blue / Red check values | phase values only from real phase times; reviewers set results |
| `video.py` | clips on the UTC clock | SRT or `video_offsets.csv`; never a guessed time |
| `replay.py` | Rerun recording + layout | one colour per drone; legend; events panel |
| `report.py`, `check.py`, `overview.py` | report, field check, list / doctor / setup | offline where possible |

**Time:** every stream has `t_utc` (timestamp, UTC) and its method in the manifest. GPS-UTC = 18 s (valid to
2027-06-28). Local DJI times use Europe/Helsinki with DST.

**Space:** ENU metres around the radar's declared position, built through ECEF. Terrain heights are aligned to the
radar's declared ground level; the alignment is reported.

**Scorecards:** the Run Scorecards page (web) and Doc are the record; `dw scorecard` only proposes values with a note
on how each was measured.
