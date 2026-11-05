# domeware: rules for anyone (or any Claude) changing this code

domeware turns one C-UAS test day (radar, drone logs, video, weather) into one UTC-aligned session that can be
replayed and scored. Internal tool for the three of us at ICPI. Design and reasons: `docs/ARCHITECTURE.md`; the
full decision record lives outside the repo (`domeware-decision.md`). The field app Orbit lives in `app/`
(JavaScript, no build step: `app/README.md`; how the two become one: `docs/decisions/0002`).

## Data rules (never break these)

- **Raw is sacred.** Files in `raw/` are copied once, hashed, read-only. Nothing edits, renames or deletes them.
- **Never half-parse.** An unknown layout fails loudly and names what it found. Every row read is kept, dropped with
  a counted reason, or reported as unprocessed.
- **Every cap announces itself.** A limit, gate, tolerance or skipped file is printed and written to the manifest.
- **UTC everywhere.** Every stream has `t_utc` (timestamp, UTC) and its time method in the manifest. Parquet can hold
  us or ns: compare times only through `compare._ns`, never `astype("int64")` on a raw column.
- **Every height has a datum.** N2000 needs the vendored grid (`dw setup`); a ballpark transform is an error.
- **One frame, one conversion function.** ENU around the radar's declared position, through ECEF.
- **Never guess a time.** A clip, log or file without a known clock is left out with the reason, not placed.
- **SIMULATED stays SIMULATED.** Test and synthetic data carry the word in file names and sources.
- **Measured vs modelled.** LOS and similar layers are labelled MODELLED; scorecard values are proposals, reviewers
  set Pass / Fail.
- **The team's files are the team's.** `runs.csv`, `notes/`, `video_offsets.csv` are written once by domeware and
  never overwritten.

## Working rules

- A bug fix lands with a test that was seen failing first. `uv run pytest -q` and `uv run ruff check src tests scripts`
  stay green; the ruff rules are pinned in `pyproject.toml`.
- Render it and look: a change to a converter or a picture regenerates the quicklooks and someone looks at them.
- Thresholds are calibrated on real data, never on hope. Numbers in docs come from the tools.
- Keep labels apart in docs and notes: verified, [inferred], [estimate], [decision].
- The Rerun viewer binds to 127.0.0.1 only (`dw view`): a listener on all interfaces makes Windows create firewall
  block rules that need an admin to remove.
- The app's tests: `node app/tests/run_tests_node.js` (readers, analysis, CWA scoring; the tests that need a real
  recording skip without `app/tests/real-data/`). Third-party files: `python3 app/tools/fetch_vendor.py`.
- Orbit's helper (`app/server/orbit_server.py`) opens its live-feed listeners on all interfaces unless told
  otherwise: never start a live TCP/UDP listener on the Windows laptop until that is fixed.
- Commit messages say why. Plan before big changes.

## Team rules

Two developers and their AI assistants share this repo. Follow `AGENTS.md`: one issue, one branch, one pull request;
never push to `main`; tests green before a merge; stay in your area (`.github/CODEOWNERS`). The session data model in
`docs/DATA.md` is a contract between all tools. Workflow: `CONTRIBUTING.md`.
