"""dw: the domeware command line.

  dw run SESSION FILES...     everything in one go: ingest + convert + env + replay
  dw ingest SESSION FILES...  copy files/folders into the session (hashed, read-only)
  dw convert SESSION          convert every known file type to Parquet
  dw env SESSION              fetch real terrain + orthophoto around the site (Finland, no key needed)
  dw replay SESSION           build views/replay.rrd + quicklook images
  dw compare SESSION          radar tracks vs drone truth: delays, errors, coverage
  dw events SESSION           drone events (take-off, landing, modes, failsafes) on the UTC clock
  dw runs SESSION             runs.csv: runs from take-offs/landings; --card FILE adds phase times from a scorecard
  dw scorecard SESSION        Blue/Red scorecard values per run and phase (import them on the Run Scorecards page)
  dw performance SESSION      radar detection share by range and height (views/performance.png)
  dw kml SESSION              views/session.kml for Google Earth (time slider)
  dw report SESSION           views/report.md
  dw view SESSION             open the replay (loopback only)
  dw check SESSION            in the field: is the data complete? (offline, seconds)
  dw verify SESSION           check raw files against their hashes
  dw info SESSION             show what is in the session
  dw list [ROOT]              all sessions in one table (+ ROOT/index.html)
  dw doctor                   check this laptop has everything domeware needs
  dw setup                    new laptop: fetch the height grids and leap-second list, then run doctor
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from domeware.session import Session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dw", description="domeware: C-UAS test-run converter and replay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "ingest"):
        p = sub.add_parser(name)
        p.add_argument("session", type=Path)
        p.add_argument("files", type=Path, nargs="+")
        if name == "run":
            p.add_argument("--radius", type=float, default=3000.0, help="environment radius around the radar (m)")
            p.add_argument("--no-env", action="store_true", help="skip terrain/imagery download")
    for name in ("convert", "replay", "verify", "info", "report", "view", "events", "check", "performance", "kml"):
        sub.add_parser(name).add_argument("session", type=Path)
    p = sub.add_parser("runs", help="runs.csv: runs and phase windows")
    p.add_argument("session", type=Path)
    p.add_argument("--card", type=Path, help="phase times from a Run Scorecards export (JSON)")
    p = sub.add_parser("scorecard", help="scorecard values per run and phase")
    p.add_argument("session", type=Path)
    p.add_argument("--run", help="only this run id")
    p.add_argument("--gate", type=float, default=50.0)
    sub.add_parser("list", help="all sessions in one table").add_argument("root", type=Path, nargs="?")
    sub.add_parser("doctor", help="check this laptop")
    sub.add_parser("setup", help="fetch height grids and leap-second list, then doctor")
    p = sub.add_parser("env")
    p.add_argument("session", type=Path)
    p.add_argument("--radius", type=float, default=3000.0)
    p = sub.add_parser("compare", help="radar tracks vs drone truth: delays, errors, coverage")
    p.add_argument("session", type=Path)
    p.add_argument("--gate", type=float, default=50.0, help="association gate, metres (median 3D distance)")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        from domeware.overview import setup

        return setup()
    if a.cmd == "doctor":
        from domeware.overview import doctor

        return doctor()
    if a.cmd == "list":
        from domeware.overview import DEFAULT_ROOT, list_sessions

        df = list_sessions(a.root or DEFAULT_ROOT)
        print(df.to_string(index=False) if len(df) else "no sessions")
        print(f"index: {(a.root or DEFAULT_ROOT) / 'index.html'}")
        return 0
    s = Session(a.session)
    if a.cmd == "check":
        from domeware import check

        return check.run(s)

    if a.cmd in ("run", "ingest"):
        missing = [str(f) for f in a.files if not Path(f).exists()]
        if missing:
            raise SystemExit(f"not found: {missing}")
        print(f"session {s.root}")
        s.ingest(a.files)
    if a.cmd in ("run", "convert"):
        from domeware import convert

        convert.run(s)
    if a.cmd == "env" or (a.cmd == "run" and not a.no_env):
        from domeware import env

        env.fetch(s, radius_m=a.radius)
        if a.cmd == "run":  # truth heights need the DEM, which needs the site from the radar data
            from domeware import convert

            convert.convert_truth(s)
            convert.convert_planned(s)
            s.save()
        from domeware import features, weather

        try:
            features.fetch(s)
        except Exception as e:  # realism layers are optional; the run continues without them
            s.note(f"features: not fetched ({e})")
        try:
            from domeware import los

            los.compute(s, max_range_m=getattr(a, "radius", 3000.0))
        except Exception as e:
            s.note(f"los: not computed ({e})")
        try:
            weather.fetch(s)
        except Exception as e:  # weather is context; a failed fetch must not stop the run
            s.note(f"weather: not fetched ({e})")
    has_truth = a.cmd in ("run", "events", "runs", "scorecard", "performance") and (any(s.streams.glob("truth/*.parquet")) or any(s.streams.glob("dji/*_osd.parquet")))
    if a.cmd == "events" or (a.cmd == "run" and has_truth):
        from domeware import events

        events.build(s)
    if a.cmd == "runs" or (a.cmd == "run" and has_truth):
        from domeware import runs

        df = runs.import_card(s, a.card) if getattr(a, "card", None) else runs.ensure(s)
        if a.cmd == "runs":
            print(df.to_string(index=False) if len(df) else "no runs: no drone was airborne in the truth logs")
    if a.cmd in ("run", "replay"):
        from domeware import replay

        stats = replay.build(s)
        for e in stats.get("events", []):
            print("  ", e)
    if a.cmd == "compare" or (a.cmd == "run" and any(s.streams.glob("truth/*.parquet"))):
        from domeware import compare

        for r in compare.run(s, gate_m=getattr(a, "gate", 50.0)):
            print(json.dumps({k: v for k, v in r.items() if not k.startswith("error_")}, indent=1, default=str))
    if a.cmd == "scorecard" or (a.cmd == "run" and has_truth):
        from domeware import scorecard

        for c in scorecard.run(s, gate_m=getattr(a, "gate", 50.0), only=getattr(a, "run", None)):
            print(f"  {c['run_id']}: {len(c['items'])} values -> views/scorecard_{c['run_id']}.json")
    if a.cmd == "performance" or (a.cmd == "run" and has_truth):
        from domeware import performance

        performance.build(s)
    if a.cmd == "kml" or a.cmd == "run":
        from domeware import kml

        if kml.build(s) is None and a.cmd == "kml":
            print("nothing to draw: no drone positions or radar tracks with positions")
    if a.cmd in ("run", "report"):
        from domeware import report

        report.build(s)
    if a.cmd == "view":
        import subprocess

        rrd = s.views / "replay.rrd"
        if not rrd.exists():
            raise SystemExit(f"No replay yet: run dw replay {a.session}")
        exe = Path(sys.executable).with_name("rerun.exe" if sys.platform == "win32" else "rerun")
        subprocess.run([str(exe), "analytics", "disable"], capture_output=True)
        # loopback only: a viewer listening on all interfaces makes Windows create firewall block rules
        subprocess.Popen([str(exe), "--bind", "127.0.0.1", "--port", "auto", str(rrd)])
        print(f"opening {rrd}  (if Windows asks about the firewall, press Cancel)")
    if a.cmd == "verify":
        problems = s.verify()
        print("\n".join(problems) if problems else f"OK: {len(s.manifest['raw'])} raw files match their hashes")
        return 1 if problems else 0
    if a.cmd == "info":
        m = s.manifest
        print(json.dumps({"session_id": m["session_id"], "site": m.get("site"), "streams": m["streams"],
                          "raw_by_role": {r: sum(1 for x in m["raw"] if x["role"] == r) for r in {x["role"] for x in m["raw"]}}},
                         indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
