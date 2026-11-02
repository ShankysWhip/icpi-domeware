# The session data model (a contract)

Every tool that reads or writes a domeware session follows this page: domeware itself, the offline Robin app, and
whatever comes next. Adding a column is fine; readers ignore columns they do not know. Renaming or removing a
column, or changing what it means or its unit, is a breaking change: its own pull request with a note in
`docs/decisions/`. The column lists below are taken from real sessions (2 Nov 2026).

Check a session against this page: `uv run dw validate <session>` (OK / WARN / FAIL per table; exit code 1 on a
FAIL). The same contract lives in `src/domeware/validate.py`, and a test keeps that file and this page in step.

## A session folder

```
<session>/
  manifest.json      session id, site (radar position and its height datum), every stream's source and time method,
                     the hashes of the raw files, a log of what was done
  raw/               the files exactly as brought from the field: copied once, hashed, read-only
  streams/           the converted data, one Parquet table per stream (below)
  env/               terrain, orthophoto and other environment layers for the site
  views/             everything generated: replay.rrd, hud/data.json, report.html, session.kml, shots/ ...
  runs.csv, equipment.csv, image_clocks.csv, video_offsets.csv, notes/, cards/
                     the team's own files: written once by domeware, never overwritten
```

Data never goes into git. Sessions live in `domeware-data/sessions/<name>/`.

## Rules every table keeps

- **Time:** `t_utc`, a timestamp with time zone UTC (microsecond or nanosecond precision; compare through
  `compare._ns`). How each stream got its time is in the manifest.
- **Place:** `lat_deg`, `lon_deg` (WGS84) and, where a position is used in 3D, `e_m`, `n_m`, `u_m`: metres east,
  north and up from the radar's declared position (`manifest.json` site), converted through ECEF
  (`replay.enu`). `range_m` is the slant range from the radar.
- **Height:** every height column states its datum. `alt_m` is the source's own altitude; for Robin its datum is
  still unverified. `h_above_site_ground_m` is above the radar site's ground level; `h_rel_takeoff_m` above the
  drone's take-off point.
- **Traceability:** `source` names the file or device; Robin rows also carry `raw_ref` (the raw file) and
  `msg_index` (the message in it), and `qflags` (quality flags, 0 = clean).

## The tables

| Table | One row is | Columns every reader may rely on |
|---|---|---|
| `streams/robin/tracks.parquet` | one Robin track state | `t_utc`, `track_id`, `lat_deg`, `lon_deg`, `alt_m`, `e_m`, `n_m`, `u_m`, `range_m`, `az_deg`, `el_deg`, `h_above_site_ground_m`, `speed_mps`, `v_e_mps`, `v_n_mps`, `v_u_mps`, `class_name`, `class_code`, `score`, `alarm`, `median_rcs_dbsm`, `current_rcs_dbsm`, `latency_s`, `raw_ref`, `msg_index`, `qflags` |
| `streams/robin/track_summary.parquet` | one Robin track | `track_id`, `t_first`, `t_last`, `n_states`, `duration_s`, `alarm_any`, `t_first_alarm_state`, `t_first_drone_state`, `max_score`, `min_range_m`, `alt_min_m`, `alt_max_m`, `median_speed_mps`, `median_rcs_dbsm`, `classes`, `last_class` |
| `streams/robin/plots.parquet` | one Robin plot (detection before tracking) | `plot_id`, `t_utc`, `lat_deg`, `lon_deg`, `alt_m`, `e_m`, `n_m`, `u_m`, `range_m`, `az_deg`, `el_deg`, `rcs_dbsm`, `latency_s`, `raw_ref`, `msg_index`, `qflags` |
| `streams/robin/status.parquet` | one Robin status message | `t_utc`, `sys_version`, `s_operational_state`, `s_position_latitude`, `s_position_longitude`, `s_position_altitude`, `s_ground_level`, `s_orientation_azimuth`, `s_processing_operating_mode_id`, `s_processing_sensitivity`, blanking sectors, GPS and component states |
| `streams/robin/motion.parquet`, `image_meta.parquet` | the radar's own motion; radar image metadata | `t_utc` plus their fields as read |
| `streams/radar/tracks.parquet` | one track state from any radar (also simulated) | `t_utc`, `track_id`, `e_m`, `n_m`, `u_m`, `range_m`, `lat_deg`, `lon_deg`, `alt_m`, `h_above_site_ground_m`, `class_name`, `class_code`, `alarm`, `score`, `speed_mps`, `median_rcs_dbsm`, `source` |
| `streams/radar/status.parquet` | a moment the radar was sending | `t_utc` |
| `streams/truth/<drone>.parquet` | one drone position (ground truth) | `t_utc`, `lat_deg`, `lon_deg`, `e_m`, `n_m`, `u_m`, `h_rel_takeoff_m`, `source` |
| `streams/planned/<name>.parquet` | one planned point (a show or mission) | `drone`, `t_show_s`, `lat_deg`, `lon_deg`, `h_rel_takeoff_m`, `h_radar_frame_m`, `e_m`, `n_m`, `u_m` |
| `streams/weather/onsite_vaisala.parquet` | one weather minute | `t_utc`, `wind_ms`, `wind_dir_deg`, `gust_ms`, `temp_c`, `rh_pct`, `pressure_hpa`, `source` (and the other Vaisala fields) |
| `streams/events/*.parquet` | one drone event (take-off, landing, mode, failsafe) | `t_utc`, `drone`, `kind`, `detail`, `source`, `time_method` |

## Changing the model

1. Open an issue: what changes, why, which tools read it.
2. One pull request that changes the writer, every reader in this repo, this page and a note in
   `docs/decisions/`.
3. Old sessions keep working: either a converter step upgrades them, or readers accept both forms for a while.
