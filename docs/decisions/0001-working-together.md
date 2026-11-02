# 0001 Working together on one repo

Date: 2026-11-02. Status: accepted (Niklas), with his answers to the open questions the same day.

## Context

Two developers, each with an AI assistant, will build on domeware and on an offline app for the same Robin data.
Pushing straight to `main` from two places overwrites work and splits the project into private versions; AI
assistants change a lot quickly, which makes clashes more likely.

## Who and what for (Niklas, 2 Nov)

- The second developer works at ICPI and builds other parts of domeware too, not only the offline Robin app.
- domeware is ICPI's internal tool **and** part of the service: its reports and exports reach customers. That sets
  the bar: customer-facing outputs are reviewed before they change.
- The code belongs to ICPI. Still to confirm in writing with each contributor (employment or contractor terms).
- Both use several AI tools, including more capable paid ones. `AGENTS.md` is the one rule file they all read
  (Claude Code reads it through `CLAUDE.md`).
- Contact is continuous and in many channels; decisions are still written down (below).
- Offline is a goal: domeware should work without a network and install without developer tools. The packaging comes
  later; the rules keep the way open now.
- Who may see third-party (Robin) data is decided later. Until then data stays outside git and is shared only by
  Niklas's decision.

## Decision

- One repo; `main` always works. Every change goes through a short branch and a pull request, merged as one commit
  (squash).
- GitHub runs the tests (`.github/workflows/ci.yml`) on every pull request; nothing red is merged.
- Each developer approves changes in their own area; Niklas approves the shared areas: the data model
  (`docs/DATA.md`), customer-facing outputs (report, COURAGEOUS export, scorecards), the tests on GitHub and the rules
  themselves. The map is `.github/CODEOWNERS`.
- Every AI assistant follows `AGENTS.md`.
- The session data model (`docs/DATA.md`) is a contract shared by every tool; changing it is its own pull request
  with a note here.
- No new runtime network dependency: anything downloaded is fetched in `dw setup`, hash-pinned.
- Talk anywhere, decide in writing: a decision that changes code, the data model or the rules goes into the pull
  request or `docs/decisions/`.
- Data, secrets and third-party data stay outside git. One line-ending rule for every machine (`.gitattributes`).
- The repo moves to an ICPI organisation on GitHub's free plan. A paid plan (GitHub Team, about 4 USD a person a
  month) only when the need is clear, e.g. the rules must be enforced or code-owner reviews requested automatically:
  on the free plan a private repo has neither, so the rules hold by agreement and the tests.

## Consequences

- A single change takes a few minutes longer; no work is lost and there is one project instead of two versions.
- Claude stops pushing to `main`: it opens pull requests and merges after the tests are green and the agreed review.
- When the offline Robin app joins, it gets its own folder and area and uses the shared data model; after the first
  side by side comparison of both on the same data, one Robin reader becomes the one both use.
