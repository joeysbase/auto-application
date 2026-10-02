# Readable `auto-app jobs` — 2026-10-01
**Step:** 1   **Status:** done

## What
- **The list:**
  - newest posting date first, with columns sized to the terminal (long titles end in "…");
  - the company as the posting shows it ("Schrödinger", not the dedup key "schr dinger");
  - readable places ("Austin, TX +2", "Remote (US)") and the sources that found each job;
  - the status column appears only once jobs have different statuses;
  - the summary line counts the jobs found on more than one source.
- **New options:** `-s/--source`, `-c/--company` and `--limit`.
- **`jobs <#>`:**
  - labelled details: company, where, posted, seen, apply and status;
  - each posting with its match in words, e.g. "same job id on the employer's system (1.00)" or "repeat of … on the same source", plus the posting's own title when it differs from the job's;
  - the matching keys.
- **`DbStore.jobs()`** now also returns the primary posting's `company` and `posted_at`, and the job's `sources`.

## Why
The user asked for a more readable `jobs` output. The old list cut companies at 18 characters and titles at 50, showed dedup keys instead of company names, and repeated "collected" on every row.

## Changes (files)
- `src/auto_app/process/jobs_view.py` (new): `format_job_list`, `format_job`, `place`.
- `src/auto_app/core/db/store.py`: `jobs()`.
- `src/auto_app/cli.py`: the `jobs` command.
- `tests/process/test_jobs_view.py` (new).
- `README.md`: the `jobs` reference.

## Tests / verification
3 new tests. Checked on the database: 68 jobs, the list and job #28 (NVIDIA plus two LinkedIn postings).
