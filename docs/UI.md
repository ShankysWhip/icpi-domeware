# domeware UI guide

How domeware's screens look and behave, agreed with Niklas (31 Oct 2026). New screens follow this page, so a new
view needs no rounds of "make it look like the rest".

## Principles

1. **The 3D map is the star.** It fills the window; everything else floats over it or docks at an edge. No empty
   space for its own sake.
2. **White, crisp, professional.** "Futuristic" lives only in the details: monospace tabular numbers, small spaced
   uppercase labels, status lights, thin-line dials.
3. **Instant.** A choice reacts on press, not on release. Transitions stay at or under 160 ms. Playback must not
   stutter on real data: measure it (below) before calling anything done.
4. **Plain words.** Weather, Events, Equipment. Every command shows its purpose (a label plus one line), not only
   on hover. Details stay folded away until needed.
5. **Not:** dark sci-fi screens, glows, neon, grid textures, scanlines, corner frames, soft rounded "chat app"
   boxes, the generic AI look.

## Tokens (`src/domeware/web/hud.css`, `:root`)

| Token | Value | Use |
|---|---|---|
| `--bg` / `--panel` / `--panel-2` | #e6ebf0 / #fff / #f5f7fa | page, cards, quiet fills |
| `--line` / `--line-2` / `--hair` | #e1e6ec / #c9d2dc / 8 % ink | hairlines, borders, card-header rule |
| `--text` / `--muted` / `--dim` | #121b26 / #586678 / #95a0ae | text levels |
| `--accent` / `--accent-2` | #0a8db6 / #0b6f91 | the one teal accent (selection, playhead, numbers) |
| `--green` / `--red` / `--amber` / `--violet` | #138a52 / #cf3a57 / #c27b0c / #6252cc | ok, lost/bad, warning, pending |
| `--r` | 3px | every corner (cards, buttons, chips) |
| `--lift` | hairline + two soft shadows | anything floating over the map |
| `--mono` / `--sans` | Cascadia Mono / Segoe UI Variable Text | numbers and codes / words |
| `--top` / `--side` / `--rail` | 46 / 372 (344 under 1500 px) / 66 px | top bar, instrument column, rail |

ICPI orange (logo only): #f39200.

## Components

- **Logo**: `web/icpi.svg`, dark letters and the orange spark, on light backgrounds only (never in a black box). The
  HUD top bar, the menu page, the loading screen, the report header, dw app (`brand.py` draws it on Tk).
- **Card**: 3 px corners, hairline edge; over the map as glass (`.glass`: white 84 %, blur 16 px). Header 34 px:
  `01 | TITLE ───── meta ×` (the number is the instrument's key).
- **Instrument rail**: icon, label and key number on every button; on = white with an accent bar; one press opens
  or closes; any number may be open; the choice is remembered on that computer.
- **Tiles**: hairline grid; a 2 px state bar on top (green, red, violet); square status light; value in mono 17 px
  with a small unit; a 15-minute trend line or squares; one quiet line under it.
- **Choices**: segmented control for one of a few, chips for several, a choice list (dropdown) for many, a switch
  for on/off in the menu. All react on press.
- **Timeline dock**: white, lanes as tall as their content, accent playhead, screenshot thumbnails. Calm colours:
  long stripes use a softened opaque colour (`soften()`, mixed half with white), amounts are soft areas with a thin
  top line, bands are quiet tints, event marks muted. Bright colour only for the playhead.
- **3D names**: placed for the opening view so none hides another (`equipment.label_lifts`); a thin leader joins a
  lifted name to its point. Range-ring names sit on the ring's south side, the wind arrow has no text.
- **Menu page**: slides in from the left; numbered sections (session, data, view, open, keys).
- **Data colours**: drones mid-tone (light ones get more ink on white panels, `ink()`), equipment by kind
  (`KIND_COLOR`), weather series fixed (wind teal, gust amber, temperature magenta).

## Making a change

1. A new look or a bigger layout change: show 2-3 options as pictures first and let Niklas pick.
2. After any visible change: `uv run dw shots <session>` takes full-resolution pictures at the window size the HUD
   last reported (`views/hud_client.json`); they go to the chat before the word "ready".
3. Speed: `uv run python scripts/hud_bench.py <session>` measures playback (fps, slow frames) in a hidden Edge;
   compare before and after.
4. Code: `web/index.html`, `web/hud.css`, `web/hud.mjs` and the modules in `web/js/` (core, app, panels, weather,
   equipment, timeline, menu). `tests/test_app.py` checks that every module parses and every import exists.
