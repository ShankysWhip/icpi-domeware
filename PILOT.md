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

## If something goes wrong

- **More files arrive later:** run the same `dw run` command again. Files already in the session are skipped.
- **"not found":** check the folder name in the command.
- **Something else:** copy the red text to Claude.
