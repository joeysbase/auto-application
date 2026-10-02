# C11: job state, job TTL, archived jobs, repost warnings — 2026-10-01
**Step:** 1 (used by 2–3)   **Status:** done. Design: [2026-10-01-job-state-ttl-design](2026-10-01-job-state-ttl-design.md)

## What
- **Job state.** `jobs.status` is replaced by:
  - `step2` (`recommended` / `un_recommended`) with `step2_at`;
  - `decision` (`applied` / `not_applied`) with `decided_at`.

  States: not evaluated · un_recommended · recommended (undecided) · applied · not applied, plus **archived**.
- **The migration** (`fe74495b274e`) converts old statuses:
  - `collected` → not evaluated;
  - `filtered_out` → un_recommended;
  - `shortlisted` / `tailored` / `approved` → recommended;
  - `skipped` → recommended + not_applied;
  - `applied` → recommended + applied.

  It also drops `status` and `jobs.purged`, and adds `archived_at`, `archive`, `archive_description` and `warnings`.
- **Job TTL** (`jobs.ttl_days`, global; defaults 14 / 7 / 30): the clock is the time since *any* of the job's postings was last seen.
  - `dedup` and `purge` refresh `last_seen_at` and `expires_at` for every active job.
  - **`purge` deletes expired not-evaluated and un-recommended jobs** in one transaction: the job, its keys and locations, and every linked posting (Level 1b duplicates included) with its raw data and screenshot.
  - **It archives expired recommended jobs**, whether undecided, applied or not applied.
  - `purge --dry-run` lists both and changes nothing.
- **Archived jobs** are compact records:
  - `jobs.archive` holds the summary (title, company, locations, apply URL, posted / first / last seen), Step 2's verdict, your decision, and each posting's identity;
  - jobs you applied to also keep their description (zstd).
  - Postings stay as identities, with `state = archived`. Their parsed / normalized data and raw data are dropped, so collect still skips those listings, and parse, normalize and dedup ignore them.
  - Matching keys (`job_keys`) are kept. `job_locations` rows fold into the record.
  - `jobs` hides archived jobs; `jobs --archived` lists them.
- **Repost warnings.** A new job that shares a matching key (employer job id, company + req id, description hash, company + title) with an archived job gets a warning, e.g. *"looks like job #12 (…), which you applied to on 2026-10-03"*. It shows as ⚠ in `jobs` and in full in `jobs <#>`.
- **`auto-app mark JOB… --as STATE`** sets the state by hand until Steps 2–3 do it. `applied` / `not_applied` also mark the job recommended; archived jobs can't change.
- **`jobs`:**
  - an "expires" column (`in 12d`, `due`, `!` = recommended and undecided, archived within 3 days);
  - a state column when states differ;
  - `--state`, `--archived`;
  - details show the state with its dates, expiry, warnings, and the kept description of applied archived jobs.
- **`dedup --rebuild`** is refused once any job is evaluated, decided or archived.
- **Retired:** `retention.description_days` / `keep_description_statuses` and purge's description step. The job TTL replaces them. Old configs that still set them load and ignore them.
- **Atomic migrations** (`core/db/engine.upgrade`): migrations now run on their own connection:
  - as one transaction, DDL included (the sqlite3 driver otherwise commits before each ALTER);
  - with foreign-key checks off while SQLite rebuilds tables, then `PRAGMA foreign_key_check`.

## Why
- Jobs never expired, and the old seven-status plan didn't match how Steps 2–3 work: Step 2 recommends, you decide.
- The design discussion settled the state model, the per-state TTLs, deleting unwanted jobs with all their postings, and archiving recommended jobs as compact records excluded from dedup, with repost warnings.

## Incident during rollout (fixed)
- The first attempt to migrate the real database failed with `FOREIGN KEY constraint failed`. Dropping `status` makes SQLite rebuild `jobs`, and postings reference it.
- Because the driver had already committed the `ADD COLUMN`s, the database was left half-migrated.
- **Recovery:** it was restored from the backup taken just before (`sqlite3 .backup`; same 68 jobs and 82 postings; `integrity_check` ok). No data was lost.
- **The fix:** the atomic-migration change above, and two new tests:
  - the migration runs with postings and locations referencing jobs;
  - a migration that fails part-way leaves the old schema untouched.
- The fixed migration was tried on a copy first, then applied: `integrity_check` and `foreign_key_check` are clean, all 68 jobs are kept, and all now have expiry dates.

## Changes (files)
- `src/auto_app/core/db/tables.py`; `migrations/versions/fe74495b274e_job_state_ttl_archive.py`; `src/auto_app/core/db/engine.py` (atomic migrations).
- `src/auto_app/core/db/job_ttl.py` (new): the TTL pass, delete and archive.
- `src/auto_app/core/db/purge.py`: the TTL first; the description step removed.
- `src/auto_app/core/db/store.py`: `jobs(archived=…)`, `job_state`, `set_job_state`, `refresh_job_dates`, `add_repost_warnings` and `archived_description`.
- `src/auto_app/core/config.py`: `JobsConfig` / `JobTTL`, and `RetentionConfig` ignoring the retired keys.
- `src/auto_app/process/link.py`: repost warnings, date refresh.
- `src/auto_app/process/parse.py`, `normalize.py`: skip archived postings.
- `src/auto_app/pipeline.py`, `src/auto_app/cli.py` (`jobs`, `mark`, `purge`, `dedup`), `src/auto_app/process/jobs_view.py`.
- `config/searches.yaml`, `config/searches.example.yaml`: the `jobs.ttl_days` block, `description_days` removed.
- Tests: `tests/core/test_job_ttl.py` (new, 8 tests); `test_purge.py`, `test_link.py` and `test_jobs_view.py` updated.
- Docs: `README.md` (key terms, configuration, `dedup`, `jobs`, `mark`, `purge`, data), `docs/README.md` §3, `docs/step1-collect.md` (§1, §8.2, §9, §10, milestone C11).

## How to use
```bash
uv run auto-app jobs                      # active jobs with their expiry
uv run auto-app mark 28 --as applied      # keep it (archived 30 days after last seen, not deleted)
uv run auto-app purge --dry-run           # what would be deleted / archived now
uv run auto-app jobs --archived
```

## Tests / verification
- 141 tests pass; lint clean.
- On the real database: `purge --dry-run` reports nothing expired, since every job was seen today. Every job expires 14 days after it was last seen.

## Caveats / follow-ups
- "Not seen" isn't always "closed". LinkedIn searches are by relevance and limited to `max_pages`, so a still-open LinkedIn job can stop being seen and expire. Company sources with `posted_within_days: all` re-list every open job each run.
- Step 2's score and reason aren't stored yet; the archive keeps Step 2's verdict and date. Add them when Step 2 exists.
- An archived job whose same listing (same source id) is seen again stays archived (open question #6).
