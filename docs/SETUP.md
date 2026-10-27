> Copied from Work/domeware-vendor/SETUP.md (23 Oct 2026). The vendor data files (PROJ grids, leap table) are not in git: download them as listed below into `Work/domeware-vendor`.

# Domeware: laptop setup (no admin needed)

Set up on Niklas's work laptop on 23 Oct 2026. Repeat on each co-worker's laptop. Every step works as a standard user.

## 1. Python packages (Work\cuas-toolkit, uv)

Already in the lock file: pymavlink 2.4.50, stonesoup 1.9.1, pyarrow 26.0.0, pandas 3.0.6, pyproj 3.8.0 (PROJ 9.8.1), scipy, numpy, plotly, matplotlib, simplekml, fmiopendata, jupyterlab, ruff.

Added 23 Oct:

```
uv add "rerun-sdk==0.40.0" pyubx2 ntplib jsonschema pyyaml
uv add --dev pytest pytest-socket vulture
```

These resolved to:
- rerun-sdk 0.40.0 (pinned on purpose)
- pyubx2 1.3.9, plus pynmeagps 1.1.8 and pyrtcm 1.2.1 as its dependencies
- ntplib 0.4.0
- jsonschema 4.26.0
- pytest 9.1.1, pytest-socket 0.8.1, vulture 2.16

On another laptop, just run `uv sync --frozen` in Work\cuas-toolkit.

**Not added yet, on purpose:**
- libasterix, protobuf, grpcio-tools: wait until Robin confirms the IRIS interface.
- skyfield, georinex: after the pilot.

**Do not launch the Rerun viewer yet.** It may open a network port, and on a no-admin laptop the firewall prompt creates permanent block rules. Check this once with an admin present, or confirm that it binds to 127.0.0.1 only.

## 2. Offline data files: Work\domeware-vendor

| File | Source | sha256 |
|---|---|---|
| proj/fi_nls_fin2023n2000.tif (N2000 geoid, CC BY 4.0 NLS) | https://cdn.proj.org/ | 82ce62ed36910a61fc79010702b3e48fdb45cdfc90a248114ac90a7cb5e30cf7 |
| proj/eur_nkg_nkgrf17vel.tif (NKG velocity model) | https://cdn.proj.org/ | 515f68aeb59f659a3f3c340626cd060f58de6f93b4546df67732b906949e0d66 |
| time/leap-seconds.list (IANA; expires 2027-06-28) | https://data.iana.org/time-zones/tzdb/leap-seconds.list | db5a895f16853b03bfc865e8d68f9fc8710ef1740e3400c701cd46a5bbbc3433 |

Checked with `pyproj.datadir.append_data_dir(<proj folder>)` and network access off, at Helsinki (60.17N, 24.94E):
- **Without the grids:** a 50 m ellipsoidal height came out as 50.0 m "N2000" via a ballpark transform, about 17.6 m wrong. ITRF2014 → EUREF-FIN was a zero-shift ballpark step.
- **With the grids:** 32.389 m N2000 via "EUREF-FIN to N2000 height (2)". ITRF2014 → EUREF-FIN ran the full chain ITRF2014 → ETRF2014 → NKG_ETRF14 → EUREF-FIN.

These files move into the domeware repo (geo/data, time/data) once D2 decides where the code lives.

## 3. External tools

| Tool | Where | How |
|---|---|---|
| PlotJuggler 4.0.0 | %LOCALAPPDATA%\Programs\PlotJuggler4 | Official GitHub release, sha256 701b3deb…a407. The Qt installer runs as invoker. Install with `--root "%LOCALAPPDATA%\Programs\PlotJuggler4" --accept-licenses --default-answer --confirm-command install`. The Parquet, MCAP, CSV and ULog loaders are **bundled**. The installer also adds a Start menu entry and a Desktop shortcut. |
| PJ telemetry off | User environment variable | `PJ_DISABLE_TELEMETRY=1` |
| RTKLIB-EX 2.5.1 | Work\tools\RTKLIB_EX_2.5.1\RTKLIB_EX_2.5.1 | Official GitHub release zip, sha256 0b20c940…96f5. Portable: convbin, rnx2rtkp, rtkconv, rtkpost. |
| age 1.3.2 | winget user scope | `winget install --id FiloSottile.age --scope user`. Adds the aliases age and age-keygen; open a new shell to pick up PATH. |
| VS Code extensions | — | Already had Python, Pylance, Jupyter, Ruff, YAML, Rainbow CSV. Added `tamasfe.even-better-toml` and `ms-toolsai.datawrangler` (views Parquet and CSV). |

Installer files are kept in Work\installers for the co-workers' laptops.

## 4. Needs an admin session (not done)

- Field laptop with Windows Pro and BitLocker (or device encryption turned on) before holding any Robin or client data.
- USB-serial driver for a weather station, only if the capture box can't take the line.
- Wireshark with Npcap: only if live packet capture on a laptop is ever needed. Reading .pcap files needs no Npcap.
- From CLAUDE.md: WSL2, Docker Desktop, QGroundControl / Mission Planner.
- Never a firewall rule for a laptop listener. Use the Linux capture box instead.

## 5. Hardware: buy, not install (decision D18)

- Linux time-and-capture box with GNSS+PPS, running chrony+gpsd
- Truth receivers (D9)
- Base and survey receiver
- Weather station
- SSDs
- Lux meter
