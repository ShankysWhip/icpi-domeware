"""dw: the domeware command line.

  dw run SESSION FILES...     everything in one go: ingest + convert + env + replay
  dw ingest SESSION FILES...  copy files/folders into the session (hashed, read-only)
  dw convert SESSION          convert every known file type to Parquet
  dw env SESSION              fetch real terrain + orthophoto around the site (Finland, no key needed)
  dw replay SESSION           build views/replay.rrd + quicklook images
  dw verify SESSION           check raw files against their hashes
  dw info SESSION             show what is in the session
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
    for name in ("convert", "replay", "verify", "info", "report"):
        sub.add_parser(name).add_argument("session", type=Path)
    p = sub.add_parser("env")
    p.add_argument("session", type=Path)
    p.add_argument("--radius", type=float, default=3000.0)
    p = sub.add_parser("compare", help="radar tracks vs drone truth: delays, errors, coverage")
    p.add_argument("session", type=Path)
    p.add_argument("--gate", type=float, default=50.0, help="association gate, metres (median 3D distance)")
    a = ap.parse_args(argv)
    s = Session(a.session)

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
    if a.cmd in ("run", "replay"):
        from domeware import replay

        stats = replay.build(s)
        for e in stats.get("events", []):
            print("  ", e)
    if a.cmd == "compare" or (a.cmd == "run" and any(s.streams.glob("truth/*.parquet"))):
        from domeware import compare

        for r in compare.run(s, gate_m=getattr(a, "gate", 50.0)):
            print(json.dumps({k: v for k, v in r.items() if not k.startswith("error_")}, indent=1, default=str))
    if a.cmd in ("run", "report"):
        from domeware import report

        report.build(s)
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
