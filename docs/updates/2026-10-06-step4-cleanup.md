# Step 4: jobs expire by themselves; serve does the daily retention — 2026-10-06

**The gap:** jobs were deleted or archived only by `data purge` / `run --purge`. The old daily `schedule` ran the
purge on every run; `serve`, which replaced it, never did. With `serve` alone, expired jobs stayed active (closed
postings in the recommended list, reposts merged into old jobs instead of coming back with a warning), and raw
pages piled up.

**Expiry is part of the job model now** (`src/auto_app/core/db/store.py`, `core/db/job_ttl.py`):
- `refresh_job_dates(ttl, now, expire=True)` recomputes the dates, then calls `expire_jobs`, which archives judged
  or decided jobs and deletes never-judged ones past their date. Queued jobs never expire.
- Every write path already refreshed the dates (dedup, `run_select`, `job mark`, Review, the job browser, the queue),
  so expiry follows whoever runs: `serve`, `run`, `filter run`, the TUI, Claude Code.
- `apply_job_ttl` (used by `data purge`) = refresh without expiring, then `expire_jobs`, for its report.

**Retention in `serve`** (`src/auto_app/serve.py`, `cli.py`):
- New setting `retention: 24h` in `config/serve.yaml` (`null` = never).
- After a round's batch, if that long has passed since the last one, `serve` calls `purge(store, retention)`
  (posting data only).
- The round report adds `retention: …`; `serve status` has a `cleanup` line with the last run; state
  `retention_at`.

**Also corrected:**
- `.env.example`, `docs/step1-collect.md` §9 and the README `data purge` / `job list` texts (`due`, and who
  applies expiry);
- `.env.example` and the Step 1 doc had said un-recommended jobs are deleted: they're archived;
- the skills (`/auto-app`, `app-config`, `troubleshooting`, `pipeline-control`) and `CLAUDE.md`.

**Tests** (359):
- `test_jobs_expire_on_any_refresh_without_a_purge`: a refresh alone deletes never-judged jobs, archives judged
  ones, and keeps queued jobs;
- `test_retention_runs_once_a_day`: runs once, not again 40 min later, again after 24 h; `null` = never.

**Decided:** no safeguard for still-posted jobs that expire because they weren't seen (a source down for days, LinkedIn's early stop). Expiry stays time-since-last-seen.
