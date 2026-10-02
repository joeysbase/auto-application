# Raw collection engine — 2026-09-28
**Step:** 1   **Status:** done

## What
First working collector: runs configured searches in a real, logged-in Chrome, records every card, skips known jobs (Level 1a), opens new job pages and saves them raw. No parsing, no database — files under `data/raw/` so the raw data can be inspected.

## Why
Verify that raw data is fetched correctly before building parse / DB / dedup on top.

## Changes
- `src/auto_app/collect/`: `runner.py` (batch → search runs → cards → fetch), `browser.py` (persistent profiles, passive JSON capture), `pacer.py`, `guard.py` (login/CAPTCHA/status detection, cooldowns), `filters.py` (card filters), `apply_link.py` (redirect decoding), `store.py` (file store), `report.py` (`inspect`/`stats` output)
- `src/auto_app/sources/`: `base.py` (adapter + generic card reading, scrolling, snapshot), `common/js.py`, `linkedin/`, `jobright/`, `handshake/`
- `src/auto_app/core/`: config (Pydantic), models, logging, notifications; `cli.py` (Typer)
- `tests/`: fake job site served through Playwright routes, runner scenarios, unit tests

## How to use
`uv run auto-app login linkedin` · `uv run auto-app collect -s linkedin --max-pages 1` · `uv run auto-app stats` · `uv run auto-app inspect`

## Tests / verification
16 tests at the time (fake site end to end: sightings, known skip, early stop, retry, budget, cards-only, CAPTCHA cooldown, login check).

## Follow-ups
Selectors for Jobright/Handshake unverified; parse and DB not started.
