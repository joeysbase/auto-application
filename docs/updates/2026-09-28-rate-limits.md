# Rate pauses + daily ceiling; retry cap — 2026-09-28
**Step:** 1   **Status:** done

## What
- Removed `--max-details` and the per-run caps (`max_details_per_run`, `max_pages_per_run`, `max_run_minutes`).
- Depth is controlled by `max_pages` per search; every new job within it is fetched.
- Per source: `max_details_per_hour` → **rate pause** until the rolling hour frees up; `max_details_per_day` → safety ceiling (rest `budget_skipped`). Defaults: LinkedIn 40/h, 300/day; others 100/h, 600/day.
- Limits are seeded from sighting history, so they hold across runs.
- Retry pass capped at `retry.max_share_of_hourly` (0.5) of the hourly limit, so searches always run.
- `stats` shows rate pauses and minutes paused.

## Why
A per-run cap cut searches mid-page and built a backlog; one run spent its whole budget on retries and never searched.

## Changes
`collect/pacer.py` (`RateLimiter` replaces `Budget`), `collect/runner.py`, `collect/store.py` (`fetch_times`), `core/config.py`, `collect/report.py`, `cli.py`, both config files, README.

## Tests / verification
Unit test with history and virtual clock; fake-site tests: hourly pause fetches everything, daily ceiling skips, retry cap leaves room for the search.
