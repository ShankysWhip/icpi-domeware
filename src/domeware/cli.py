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
  dw timeline SESSION         views/timeline_1s.csv: every drone, its radar track, weather and events per second
  dw courageous SESSION       radar output in the COURAGEOUS format (CWA 18150 Annex H), schema-checked
  dw report SESSION           views/report.md
  dw view SESSION             open the replay (loopback only); --start plays the test from its start
  dw app                      the same in one window: pick a session, press a button
  dw check SESSION            in the field: is the data complete? (offline, seconds)
  dw note SESSION "text"      a team note on the UTC clock (--at 09:14:30); shows in the replay and report
  dw seal SESSION             record the hash of every file the team brought (seal.json)
  dw sync SESSION DEST        copy the sealed session to DEST (e.g. an SSD), never delete there, check the copy
  dw verify SESSION           check raw files against their hashes
  dw info SESSION             show what is in the session
  dw list [ROOT]              all sessions in one table (+ ROOT/index.html)
  dw doctor                   check this laptop has everything domeware needs
  dw setup                    new laptop: fetch the height grids and leap-second list, then run doctor
  dw site save SESSION NAME   keep a test site (radar position) as a named profile; dw site list
  dw site use SESSION NAME    use a saved site before the radar data arrives (the radar data replaces it)
  dw equipment SESSION        all equipment at the test and where (radar, take-offs, weather, RF...); adds equipment.csv
  dw simulate SITE SESSION    a SIMULATED phase-profile run at a saved site, in its real environment
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
    for name in ("equipment", "convert", "replay", "verify", "info", "report", "events", "check", "performance", "kml", "timeline", "seal", "courageous"):
        sub.add_parser(name).add_argument("session", type=Path)
    p = sub.add_parser("sync", help="copy a sealed session to another disk and check it")
    p.add_argument("session", type=Path)
    p.add_argument("dest", type=Path)
    p = sub.add_parser("note", help="a team note on the UTC clock")
    p.add_argument("session", type=Path)
    p.add_argument("text")
    p.add_argument("--at", help="UTC time: 09:14:30 (day from the session) or a full ISO time; default now")
    p.add_argument("--author")
    p = sub.add_parser("runs", help="runs.csv: runs and phase windows")
    p.add_argument("session", type=Path)
    p.add_argument("--card", type=Path, help="phase times from a Run Scorecards export (JSON)")
    p = sub.add_parser("scorecard", help="scorecard values per run and phase")
    p.add_argument("session", type=Path)
    p.add_argument("--run", help="only this run id")
    p.add_argument("--gate", type=float, default=50.0)
    sub.add_parser("list", help="all sessions in one table").add_argument("root", type=Path, nargs="?")
    sub.add_parser("doctor", help="check this laptop")
    sub.add_parser("app", help="one window with big buttons")
    p = sub.add_parser("view", help="open the replay")
    p.add_argument("session", type=Path)
    p.add_argument("--start", action="store_true", help="play the test from its start")
    sub.add_parser("setup", help="fetch height grids and leap-second list, then doctor")
    p = sub.add_parser("site", help="test-site profiles")
    p.add_argument("action", choices=["save", "list", "use"])
    p.add_argument("session", type=Path, nargs="?")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("simulate", help="SIMULATED run at a saved site")
    p.add_argument("site")
    p.add_argument("out", type=Path)
    p.add_argument("--drones", type=int, default=1)
    p.add_argument("--latency", type=float, default=0.4)
    p.add_argument("--noise", type=float, default=4.0)
    p.add_argument("--miss", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--spacing", type=float, default=60.0, help="metres between drones")
    p = sub.add_parser("env")
    p.add_argument("session", type=Path)
    p.add_argument("--radius", type=float, default=3000.0)
    p = sub.add_parser("compare", help="radar tracks vs drone truth: delays, errors, coverage")
    p.add_argument("session", type=Path)
    p.add_argument("--gate", type=float, default=50.0, help="association gate, metres (median 3D distance)")
    a = ap.parse_args(argv)
    if a.cmd == "site":
        from domeware import sites

        if a.action == "list":
            print(chr(10).join(sites.list_sites()) or "no site profiles yet: dw site save <session> <name>")
            return 0
        if not (a.session and a.name):
            raise SystemExit(f"usage: dw site {a.action} <session> <name>")
        if a.action == "use":
            site = sites.use(Session(a.session), a.name)
            print(f"{a.session}: site {site['name']} from profile {a.name}; next: dw env, then dw replay")
            return 0
        print(f"saved {sites.save(Session(a.session), a.name)}")
        return 0
    if a.cmd == "simulate":
        from domeware import sites

        s = sites.simulate(a.site, a.out, a.drones, a.latency, a.noise, a.miss, a.seed, spacing_m=a.spacing)
        print(f"SIMULATED session at {s.root}: next, dw run {s.root} (or dw replay / dw scorecard / dw report)")
        return 0
    if a.cmd == "setup":
        from domeware.overview import setup

        return setup()
    if a.cmd == "app":
        from domeware.app import main as app_main

        return app_main()
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
    if a.cmd in ("seal", "sync"):
        from domeware import custody

        if a.cmd == "seal":
            custody.seal(s)
            return 0
        problems = custody.sync(s, a.dest)
        print(chr(10).join(problems) if problems else f"OK: copy at {a.dest / s.root.name} matches the seal")
        return 1 if problems else 0
    if a.cmd == "equipment":
        from domeware import equipment

        df, problems = equipment.load(s)
        print(df.drop(columns=["height_assumed"], errors="ignore").to_string(index=False) if len(df) else "no equipment yet")
        print(chr(10).join(problems))
        if equipment.template(s):
            print(f"wrote {s.root / 'equipment.csv'}: add a row per device or person the data does not show")
        return 0
    if a.cmd == "note":
        from domeware import notes

        r = notes.add(s, a.text, a.at, a.author)
        print(f"note {r['t_utc']} ({r['author']}): {r['text']}")
        return 0
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
    has_truth = a.cmd in ("run", "events", "runs", "scorecard", "performance", "timeline") and (any(s.streams.glob("truth/*.parquet")) or any(s.streams.glob("dji/*_osd.parquet")))
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
    if a.cmd == "timeline" or (a.cmd == "run" and has_truth):
        from domeware import performance

        if performance.timeline(s) is None:
            print("no drone positions: nothing to put on a timeline")
    if a.cmd == "kml" or a.cmd == "run":
        from domeware import kml

        if kml.build(s) is None and a.cmd == "kml":
            print("nothing to draw: no drone positions or radar tracks with positions")
    if a.cmd == "courageous" or a.cmd == "run":
        from domeware import courageous

        for f in courageous.export(s) if s.manifest.get("site") else []:
            print(f"  {f.name}")
    if a.cmd in ("run", "report"):
        from domeware import report

        report.build(s)
    if a.cmd == "view":
        from domeware.viewer import open_replay

        print(open_replay(s, start=a.start) + "  (if Windows asks about the firewall, press Cancel)")
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
