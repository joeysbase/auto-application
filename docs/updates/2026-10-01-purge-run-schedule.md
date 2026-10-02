# Purge, storage-light defaults (C9); run + schedule (C10) — 2026-10-01
**Step:** 1   **Status:** done

## What
**C9 — retention** (`src/auto_app/core/db/purge.py`, `auto-app purge [--dry-run] [--vacuum]`, settings `retention:`):
- raw pages/records/captured JSON + screenshots: 7 days after fetch, only once parsed (failed parses keep raw data);
- description text of jobs not seen for 30 days unless their status is shortlisted/tailored/approved/applied — MinHash, shingle count and hash are kept so dedup keeps matching;
- card sightings and run logs / block artifacts after 30 days;
- never: posting identity, parse/normalize keys, jobs, status, `job_keys`.
- Later stages after a purge: `parse` skips purged postings and keeps their earlier result (reported, not marked failed); re-normalizing keeps the description signature; dedup falls back to MinHash; `inspect` shows what's kept.
- Storage-light defaults: screenshots and card HTML off (`--screenshots`, `--card-html` to keep them); page text stays (the LinkedIn parser needs it).

**C10 — one command + schedule**:
- `auto-app run` (`src/auto_app/pipeline.py`): collect → parse → normalize → dedup (→ `--purge`), with the usual selection flags and `--no-collect`; a lock file (`data/run.lock`) prevents overlapping runs; `--scheduled` applies the run window / minimum interval and sends a notification ("N new jobs · problems").
- `auto-app schedule install --at HH:MM… [--headless] [--dry-run] | show | remove` (`src/auto_app/schedule.py`): a launchd agent (`~/Library/LaunchAgents/com.auto-app.run.plist`) running `uv run --project <repo> auto-app run --scheduled --purge`, logging to `data/logs/scheduled.log`. Visible browser by default (safest for LinkedIn).

## Live verification
- `purge --dry-run` on the current database: nothing old enough yet.
- `run -c uipath`: collect → parse → normalize → dedup in one go, per-stage summary; jobs unchanged (10).
- `schedule install --at 09:00 --at 17:00 --dry-run`: plist with the right command and times. **Not installed** — that's your call.

## Tests
112 tests (6 new): purge dry run changes nothing; purge removes raw data (parsed only), screenshots, stale descriptions (not of applied jobs), sightings, old logs — and keeps identity, keys, jobs and status; parse / normalize / dedup still work after a purge; the full `run` pipeline on the fake job site (5 jobs, second run 0 new); the run lock; the launchd plist. Lint clean.
