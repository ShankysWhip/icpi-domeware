# Inbox: things to add to the build

`main` is the build: the combined app, with domeware's core at the top and Orbit in `app/`. The two old apps go on
separately on their own branches, `domeware` and `orbit`. What either side wants in the build lands here as one item,
and a strong AI model (Claude Fable) brings it into the build, one item per pull request. Decision: `docs/decisions/0002`.

| Here | What |
|---|---|
| `from-domeware/` | items from the `domeware` branch |
| `from-orbit/` | items from the `orbit` branch |
| `done/` | items the build has taken in, each with its pull request |
| `synced.json` | the last commit of each side branch that the build already contains |

## Adding a thing (people)

1. Make and test it on your branch (`domeware` or `orbit`), through a pull request as usual.
2. Copy `TEMPLATE.md` to `from-domeware/` or `from-orbit/` as `YYYY-MM-DD-short-name.md`, fill it in and open a pull
   request to `main` with only that file. An idea without code is fine too.

## Bringing it in (the AI)

Open Claude Code in this repo, pick the Fable model (`claude-fable-5-1`) and say "process the inbox". Then take each
item in `from-domeware/` and `from-orbit/` (every `.md` file except `README.md`), oldest first:

1. Read the item and the commits it names. `git log <synced>..<branch>` shows everything the side has done since the
   build last took something in.
2. Port it into the build: domeware changes into `src/`, `tests/` and the docs; Orbit changes into `app/`
   (`git diff A B | git apply --directory=app` is often enough). Then make it fit the other part: one reader per file
   type, one frame, one clock, the data contract in `docs/DATA.md`.
3. If both parts already do the same thing differently (the method table in decision 0002: gate, tolerance, clock
   sync, heights, CWA), do not choose. Write the question into the item ("Status: question for people") and move on.
4. Check: `uv run ruff check src tests scripts`, `uv run pytest -q` and `node app/tests/run_tests_node.js`, plus the
   item's own check. Say in the pull request which outputs changed and by how much.
5. One branch and one pull request per item (`ai/inbox-<short-name>`, title `inbox: <title>`). In the same pull
   request, move the item to `done/` with the pull request's link and update `synced.json`. Never push to `main`:
   people review and merge.
