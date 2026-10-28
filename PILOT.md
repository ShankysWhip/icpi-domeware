# Pilot card (28 Oct 2026)

## In the field: what to save

Put **everything into one folder**: `C:\Users\nikla_68b56qz\Downloads\ICPI\pilot-2026-10-28\`

| What | From where | Note |
|---|---|---|
| Robin XML stream (`*.xml`) | Robin/VTT | Start capture at least 5 min before the first take-off; keep it running |
| Robin `cfg` folder (`IrisRadar.config`, `IrisTrackerCa.map`) | Robin/VTT | One copy per day |
| Robin `.PLOTS.gz` / `.IMG` | Robin/VTT | If they give them |
| DJI video `.MP4` + **`.SRT`** | drone SD card | Turn **"Video Captions" ON** in DJI Fly, and record for the whole flight |
| DJI `FlightRecord_*.txt` | phone / controller | Encrypted, but keep it |
| ArduPilot `.bin` | our own drones' SD cards | If our drones fly |
| Notes: times of take-offs, what was flown | paper / phone | Write in local time (UTC+2 from 25 Oct) |

## In the field: check before you leave (1 minute, no internet needed)

Copy everything into one folder on the laptop, then:

```powershell
uv run dw ingest C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot <the folder>
```
```powershell
uv run dw check C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot
```

Every line says OK, WARN or FAIL. Fix every FAIL before leaving: copy the missing file again, export the log, or
write the right time down if a clock was wrong.

## After the pilot: two commands

Open PowerShell and paste these one at a time:

```powershell
cd C:\Users\nikla_68b56qz\Work\domeware
```
```powershell
uv run dw run C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot C:\Users\nikla_68b56qz\Downloads\ICPI\pilot-2026-10-28
```

This takes about 1-3 minutes. It copies and hashes the files, converts them, downloads the terrain, builds the replay and,
if a drone log or SRT is included, writes the radar-vs-drone numbers.

## Look at the result

```powershell
uv run rerun --bind 127.0.0.1 --port auto C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot\views\replay.rrd
```

The numbers are in `C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot\views\compare.md`.

## Scorecards

1. In the field, fill the run on the **Run Scorecards v2** page (the *Now* buttons write UTC phase times).
2. After the run, press **Export JSON** on the page and save the file into the pilot folder.
3. Give domeware the phase times, then the values:

```powershell
uv run dw runs C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot --card <the exported file>.json
```
```powershell
uv run dw scorecard C:\Users\nikla_68b56qz\Work\domeware-data\sessions\2026-10-28_pilot
```

4. On the page, press **Import domeware values** and pick `views\scorecard_<run>.json`. Check each value, then set
   Pass / Fail.

## If something goes wrong

- **More files arrive later:** run the same `dw run` command again. Files already in the session are skipped.
- **"not found":** check the folder name in the command.
- **Something else:** copy the red text to Claude.
