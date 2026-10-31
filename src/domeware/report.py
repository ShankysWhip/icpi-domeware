"""One readable report per session: views/report.md (Markdown with the quicklook images)."""

from __future__ import annotations

import json

import pandas as pd

from domeware.session import Session, now_utc


def _tbl(rows: list[list], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    cell = lambda v: "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731 - notes may hold | or newlines
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def build(s: Session) -> str:
    m, v = s.manifest, s.views
    site = m.get("site") or {}
    stats = json.loads((v / "replay_stats.json").read_text(encoding="utf-8")) if (v / "replay_stats.json").exists() else {}
    cmp_ = json.loads((v / "compare.json").read_text(encoding="utf-8")) if (v / "compare.json").exists() else []
    md = [f"# Run report: {site.get('name') or s.root.name}", "",
          f"Session `{s.root.name}` · id `{m['session_id']}` · generated {now_utc()} by domeware", ""]

    md += ["## Data received", ""]
    roles: dict[str, list] = {}
    for r in m["raw"]:
        roles.setdefault(r["role"], []).append(r)
    md += [_tbl([[k, len(x), f"{sum(i['size'] for i in x) / 1e6:.1f}"] for k, x in sorted(roles.items())],
                ["type", "files", "MB"]), "",
           f"All {len(m['raw'])} raw files are kept unchanged with SHA-256 hashes in `manifest.json` (`dw verify` checks them).", ""]

    md += ["## Site", "", _tbl([[site.get("name"), site.get("lat_deg"), site.get("lon_deg"), site.get("alt_m"),
                                 site.get("ground_level_m"), site.get("azimuth_deg")]],
                               ["radar", "lat", "lon", "antenna alt (m)", "ground (m)", "azimuth (deg)"]),
           "", f"Position as declared by the radar. Altitude datum: {site.get('alt_datum')}.", ""]

    md += ["## Radar", ""]
    for name, st in m["streams"].items():
        if "unique_tracks" in st:
            md.append(f"- **{name}**: {st['unique_tracks']} tracks, {st['tracks_kept']} unique track states "
                      f"({st['duplicates_dropped']} repeated states removed), focus track **{st.get('focus_track')}**.")
    if stats.get("events"):
        md += ["", "Focus-track events (UTC):", ""] + [f"- {e}" for e in stats["events"]]
    md.append("")

    md += ["## Radar vs drone truth", ""]
    if cmp_:
        keys = ["truth", "flight_s", "drone_track_ids", "delay_airborne_to_first_plot_s", "delay_airborne_to_first_track_s",
                "delay_airborne_to_first_DRONE_class_s", "delay_airborne_to_first_alarm_s", "tracked_share_of_flight",
                "track_breaks", "range_at_first_plot_m", "range_at_first_track_m", "max_range_tracked_m",
                "other_tracks_during_flight", "radar_time_shift_s"]
        md += [_tbl([[k.replace("_", " ")] + [r.get(k) for r in cmp_] for k in keys], ["measure"] + [f"#{i + 1}" for i in range(len(cmp_))]), ""]
        for r in cmp_:
            e = r.get("error_horizontal_m")
            if e:
                md.append(f"- {r['truth']}: horizontal error median **{e['median']} m**, p90 {e['p90']} m, RMS {e['rms']} m "
                          f"(n = {e['n']}); vertical median {(r.get('error_vertical_m') or {}).get('median')} m.")
        md += ["", "Rule: a radar track is the drone when its median 3D distance to truth is under the gate "
               f"({(m.get('compare') or {}).get('gate_m')} m). Details: `compare.md`.", ""]
    else:
        md += ["No drone truth in this session yet (DJI .SRT captions, ArduPilot .bin or RTKLIB .pos).", ""]

    if (s.root / "runs.csv").exists():
        from domeware import runs as R

        rd = R.load(s)
        md += ["## Runs and phases", "", _tbl([[r.run_id, r.part, f"{r.start_utc:%H:%M:%S}", f"{r.end_utc:%H:%M:%S}",
                                               int((r.end_utc - r.start_utc).total_seconds()), r.notes] for r in rd.itertuples()],
                                             ["run", "part", "start UTC", "end UTC", "s", "from"]),
               "", "Edit `runs.csv` by hand, or add phase times from a scorecard export: `dw runs <session> --card <file>.json`.", ""]
    from domeware import notes as N

    nt = N.load(s)
    if len(nt):
        md += ["## Team notes", "", _tbl([[f"{r.t_utc:%H:%M:%S}" if pd.notna(r.t_utc) else "?", r.author, r.text]
                                          for r in nt.itertuples()], ["UTC", "who", "note"]), ""]
    de = s.streams / "events" / "drone_events.parquet"
    if de.exists():
        d = pd.read_parquet(de)
        md += ["## Drone events", "", _tbl([[f"{r.t_utc:%H:%M:%S}", r.drone, r.kind, r.detail] for r in d.itertuples()][:60],
                                           ["UTC", "drone", "event", "detail"]), ""]
        if len(d) > 60:
            md += [f"First 60 of {len(d)} events; all are in `streams/events/drone_events.parquet` and the replay's Events panel.", ""]
    for cp in sorted((s.root / "cards").glob("*.json")):
        card = json.loads(cp.read_text(encoding="utf-8"))
        rid = (card.get("run") or {}).get("run_id") or card.get("run_id") or cp.stem
        items = card.get("items") or {}
        res = {k: (x or {}).get("result") for k, x in items.items()}
        bp = sum(1 for k, r in res.items() if r == "Pass" and k.startswith("B"))
        bf = sum(1 for k, r in res.items() if r == "Fail" and k.startswith("B"))
        rp, rf = (sum(1 for k, r in res.items() if r == "Pass" and not k.startswith("B")),
                  sum(1 for k, r in res.items() if r == "Fail" and not k.startswith("B")))
        md += [f"## Team scorecard: {rid}", "",
               f"Red (our drones) {rp} pass / {rf} fail; Blue (radar) {bp} pass / {bf} fail; "
               f"Red team wins from Blue fails: **{bf}**. Filled by hand during the run (`cards/{cp.name}`).", ""]
        fails = [[k, (items[k] or {}).get("value", ""), (items[k] or {}).get("note", "")] for k, r in sorted(res.items()) if r == "Fail"]
        if fails:
            md += ["Fails and why:", "", _tbl(fails, ["check", "value", "why"]), ""]
        meas_p = v / f"scorecard_{rid}.json"
        if meas_p.exists():
            meas = json.loads(meas_p.read_text(encoding="utf-8")).get("items") or {}
            both = [[k, res.get(k) or "", (items.get(k) or {}).get("value", ""), meas[k]["value"]]
                    for k in sorted(meas) if (items.get(k) or {}).get("value") or res.get(k)]
            if both:
                md += ["Team entry next to what domeware measured (a big difference is worth a second look):", "",
                       _tbl(both, ["check", "team result", "team value", "measured"]), ""]
    for p in sorted(v.glob("scorecard_*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        val = c.get("validity") or {}
        md += [f"## Scorecard values: {c['run_id']}", "",
               (f"**Run {'VALID' if val.get('valid') else 'NOT VALID'}**" + (": " + "; ".join(val["reasons"]) if val.get("reasons") else "")
                + ("" if not val.get("warnings") else " (" + "; ".join(val["warnings"]) + ")")) if val else "", "",
               _tbl([[k, x["value"], x["note"]] for k, x in c["items"].items()], ["check", "value", "how measured"]), "",
               f"Import `{p.name}` on the Run Scorecards page (Import domeware values). Results stay with the reviewers.", ""]
        sp = c.get("siap") or {}
        if sp and "error" not in sp:
            md += ["Independent check, Stone Soup SIAP metrics (Dstl; time-averaged over the run):", "",
                   _tbl([[sp.get("completeness"), sp.get("ambiguity"), sp.get("spuriousness"), sp.get("position_accuracy_m"),
                          sp.get("velocity_accuracy_mps"), sp.get("track_number_changes"), sp.get("longest_segment")]],
                        ["completeness (1)", "ambiguity (1)", "spuriousness (0)", "position acc. m (0)", "velocity acc. m/s (0)",
                         "track number changes (0)", "longest segment (1)"]),
                   "", f"Target in brackets. {sp.get('method')}. Position accuracy is a 3D mean, so it reads higher than the "
                   "horizontal medians above.", ""]

    perf = m.get("performance")
    if perf and (v / "performance.png").exists():
        md += ["## Detection by range and height", "",
               f"The radar had its own track on our drones for **{perf['share_overall']:.0%}** of {perf['drone_seconds']} "
               + (f"(95 % CI {perf['ci95_overall'][0]:.0%}-{perf['ci95_overall'][1]:.0%}, bootstrap over "
                  f"{perf.get('flights')} flights) " if perf.get("ci95_overall") else
                  f"(only {perf.get('flights')} flight(s): too few for an honest interval) ") +
               "airborne drone-seconds (" + ", ".join(f"{k} {x:.0%}" for k, x in perf["share_by_drone"].items()) + "). "
               "Measured share for these flights, not a general probability of detection; each bar shows its seconds.",
               (f" Flights with at least one radar track: {perf['flights_with_track']}/{perf['flights']} "
                f"(95 % CI {perf['ci95_flights_with_track'][0]:.0%}-{perf['ci95_flights_with_track'][1]:.0%}, Clopper-Pearson)."
                if perf.get("flights") else ""), "",
               "![performance](performance.png)", ""]
    if (v / "session.kml").exists():
        md += ["Google Earth: open `views/session.kml` (desktop or phone) and use the time slider.", ""]
    pvf_p = v / "planned_vs_flown.json"
    if pvf_p.exists():
        pvf = json.loads(pvf_p.read_text(encoding="utf-8"))
        md += ["## Planned vs flown (Skybrush)", "",
               _tbl([[r["planned"], r.get("flown"), (r.get("deviation_horizontal_m") or {}).get("median"),
                      (r.get("deviation_horizontal_m") or {}).get("p90"), (r.get("deviation_horizontal_m") or {}).get("max"),
                      (r.get("deviation_vertical_m") or {}).get("median"), r.get("note", "")] for r in pvf],
                    ["planned", "flown log", "horiz. median m", "p90 m", "max m", "vert. median m", "note"]),
               "", "Show time is tied to UTC at take-off (first moment above 2 m). Axis convention of the show file is "
               "ASSUMED until verified on a real show.", ""]

    wx = s.streams / "weather" / "fmi_nearest.parquet"
    if wx.exists() and cmp_:
        w = pd.read_parquet(wx)
        w["t_utc"] = pd.to_datetime(w["t_utc"], utc=True)
        a, b = pd.Timestamp(cmp_[0]["airborne_utc"]), pd.Timestamp(cmp_[0]["landed_utc"])
        win = w[(w["t_utc"] >= a.floor("10min")) & (w["t_utc"] <= b.ceil("10min"))]
        params = (m["streams"].get("weather") or {}).get("parameters", {})
        rows = [[c, f"{win[c].min():.1f}", f"{win[c].max():.1f}", params.get(c, {}).get("unit"),
                 params.get(c, {}).get("station"), params.get(c, {}).get("dist_km")]
                for c in ("wind_ms", "gust_ms", "wind_dir_deg", "temp_c", "visibility_m", "cloud_okta", "precip_mmh")
                if c in win and win[c].notna().any()]
        md += ["## Conditions during the flight (FMI, nearest stations)", "", _tbl(rows, ["", "min", "max", "unit", "station", "km"]),
               "", "These are regional observations, not on-site measurements.", ""]

    md += ["## Environment", ""]
    env, feat, los = m.get("env") or {}, m.get("features") or {}, m.get("los") or {}
    if env:
        md.append(f"- Terrain: NLS 2 m elevation model; aerial photo {', '.join(env['ortho']['labels'])}; radius {env['radius_m']:.0f} m.")
    if feat:
        md.append(f"- {feat.get('buildings', 0)} buildings (heights ESTIMATED from floor class), {feat.get('power_lines', 0)} "
                  f"power-line segments (heights ASSUMED), canopy {feat.get('canopy', {}).get('years')}.")
    if los:
        md.append(f"- **MODELLED line of sight** (geometry only): a target at 30 m above ground is in sight over "
                  f"{los['share_visible_below_30m']:.0%} of the area within {los['max_range_m']:.0f} m; at ground level "
                  f"{los['share_visible_at_ground']:.0%}.")
    md.append(f"- Terrain aligned to the radar's declared ground level: {stats.get('vertical_alignment_m')} m.")
    md.append("")

    md += ["## Pictures", ""] + [f"![{p}]({p})" for p in stats.get("quicklooks", [])] + \
          [f"![{p.name}]({p.name})" for p in sorted(v.glob("compare_*.png"))] + [""]
    md += ["## Replay", "", "`uv run dw view <session>` (viewer telemetry off, listens on 127.0.0.1 only)", ""]
    md += ["## Known limits", "",
           "- The radar's altitude datum is unconfirmed; vertical errors are indicative until the radar owner confirms it.",
           f"- NLS layers (ETRS89) are moved onto the GPS frame by {(stats.get('nls_layers_shifted_m') or [0, 0])[0]} m east and "
           f"{(stats.get('nls_layers_shifted_m') or [0, 0])[1]} m north: "
           "plate motion since 1989, ITRF2020 to ETRF2000 at today's epoch, good to a few centimetres.",
           "- Building and power-line heights are estimates; line of sight is MODELLED, not detection probability.",
           "", "## Sources", "", f"- {(env or {}).get('attribution', '')}", f"- {feat.get('attribution', '')}",
           f"- {(m['streams'].get('weather') or {}).get('attribution', '')}", ""]
    text = "\n".join(md)
    (v / "report.md").write_text(text, encoding="utf-8")
    (v / "report.html").write_text(to_html(text, v), encoding="utf-8")
    s.note("report: views/report.md and views/report.html (one file, pictures inside: opens on a phone)")
    return text


def to_html(md: str, views) -> str:
    """The report as one HTML file (pictures inside), readable on a phone. Handles only what build() writes."""
    import base64
    import html
    import re

    def inline(t: str) -> str:
        t = html.escape(t, quote=False)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)

    out, rows, items = [], [], []

    def close():
        if rows:
            head, body = rows[0], [r for r in rows[2:]]
            out.append('<div class="t"><table><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</table></div>")
            rows.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in md.split("\n"):
        if line.startswith("|"):
            if items:
                close()
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if line.startswith("- "):
            if rows:
                close()
            items.append(line[2:])
            continue
        close()
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", line.strip())
        if m:
            p = views / m.group(1)
            if p.exists() and p.suffix.lower() == ".png":
                out.append(f'<img alt="{html.escape(p.stem)}" src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}">')
        elif line.startswith("#"):
            n = len(line) - len(line.lstrip("#"))
            out.append(f"<h{n}>{inline(line[n:].strip())}</h{n}>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    close()
    css = ("body{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:12px 16px;color:#1d232a;background:#fafaf8}"
           "h1{font-size:22px}h2{font-size:18px;margin-top:28px;border-bottom:1px solid #ddd}.t{overflow-x:auto}"
           "table{border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #ddd;padding:5px 8px;text-align:left;"
           "vertical-align:top}th{background:#eee}img{max-width:100%;margin:8px 0}code{background:#eee;padding:0 3px}"
           "header{display:flex;align-items:center;gap:12px;padding:4px 0 10px;border-bottom:1px solid #ddd}"
           "header svg{height:28px;width:auto}header span{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#6b7480}"
           "@media(prefers-color-scheme:dark){body{background:#15191d;color:#e8ebe6}th{background:#252b31}code{background:#252b31}"
           "td,th,h2,header{border-color:#333}header .letters{stroke:#e8ebe6}}")
    title = re.sub(r"<[^>]+>", "", out[0]) if out else "domeware report"
    from domeware import brand

    head = f"<header>{brand.svg()}<span>domeware · test report</span></header>"
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{css}</style>{head}" + "\n".join(out) + "</html>")
