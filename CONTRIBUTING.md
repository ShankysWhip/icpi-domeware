# Contributing to domeware

One project, one repo, several hands: people and their AI assistants. This page is the whole workflow. Rules for AI
assistants: `AGENTS.md`. The data model every tool shares: `docs/DATA.md`. The look: `docs/UI.md`. Decisions:
`docs/decisions/`.

## Set up

A new laptop: `docs/SETUP.md` (uv, `uv sync`, `uv run dw setup`, `uv run dw doctor`). For the app in `app/`:
`python3 app/tools/fetch_vendor.py` (MapLibre and the Windows embeddable Python, hash-checked; `app/README.md`).

## A change, step by step

1. **Issue first.** Open or pick an issue and assign it to yourself: one person per task.
2. **Branch from the latest main.** `git switch main`, `git pull`, then `git switch -c <who>/<topic>`.
3. **Small commits that say why.** Docs (README, docs/) change in the same branch as the code.
4. **Check before you push.** `uv run ruff check src tests scripts` and `uv run pytest -q`; in `app/` also
   `node app/tests/run_tests_node.js`. A change you can see:
   `uv run dw shots <session>` and put the pictures in the pull request.
5. **Pull request.** `git push -u origin <who>/<topic>`, open a pull request (the template asks the right
   questions) and link the issue (`Closes #12`).
6. **Checks and review.** GitHub runs the tests on every push (`.github/workflows/ci.yml`). Merge when they are
   green and the owner of the touched area has approved (`.github/CODEOWNERS`): each developer approves their own
   areas; Niklas approves the shared ones (the data model, customer-facing outputs such as the report, the
   COURAGEOUS export and the scorecards, the tests on GitHub and these rules).
7. **Squash merge and delete the branch.** One commit per pull request on main. `main` always works.

## Keeping out of each other's way

- Stay in your area. Another area needs an issue and its owner's review.
- Keep branches short and take in main often (`git pull origin main` on your branch) when it moves.
- No mass reformatting or renaming inside other work: give it its own pull request.
- The data model (`docs/DATA.md`) is a contract: a change to it gets its own pull request and a decision note.
- Talk anywhere, decide in writing: a decision that changes code, the data model or the rules goes into the pull
  request or `docs/decisions/`, so nobody undoes one by accident.
- Offline is a goal: after `dw setup` everything works without a network. No new runtime calls to web services;
  anything downloaded is fetched in `dw setup`, hash-pinned.

## Data and secrets

Data never goes into git. Sessions live in `domeware-data/`, vendored files in `domeware-vendor/` (`dw setup`);
the app's third-party files come from `app/tools/fetch_vendor.py` and its real recordings stay in
`app/tests/real-data/`, which git ignores.
Tests that need real data skip when it is absent and refer to their fixtures by sha256. No tokens, keys, passwords
or third-party data in commits.
