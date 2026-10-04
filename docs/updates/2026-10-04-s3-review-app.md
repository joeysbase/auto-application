# Step 2 S3: the review app — 2026-10-04
**Step:** 2 (feeds Step 3)   **Status:** done. Tested offline (data layer + Textual pilot); smoke-run on a copy of the real database.

## What
- **`auto-app filter review [-f NAME]…`** — a full-screen terminal app (Textual):
  - **lists** (`tab` / `shift+tab`): Recommended (undecided), Un-recommended (undecided), Want to apply (your queue);
  - **detail pane**: the job's summary, state, expiry, sources, link, repost warnings, every filter's verdict / reason / lookups / your feedback, then the description;
  - **keys**: `a` want to apply, `n` not apply, `←` `→` / `1`–`9` pick a filter, `g` / `b` its verdict was right / wrong, `r` reason for your last answer, `u` undo, `o` open link, `/` search, `q` quit;
  - marks ★ ✗ 👍 👎; a job you answer stays in its list (with its mark) until you leave, so you can change your mind.
- **`want_to_apply` state** (your decision, queued for Step 3):
  - never expires while queued: `refresh_job_dates` gives it no expiry, `data purge` skips it, `job list` shows `queued`;
  - `job mark --as want_to_apply`, `job list --state want_to_apply`;
  - `job mark` with a decision now keeps an `un_recommended` verdict too (it used to force `recommended`).
- **Feedback per filter** (new `feedback` table, migration `7d2ba831d6e3`): one row per (job, filter) — the filter's version and verdict, right / wrong, `explicit` or `implied`, an optional reason, the job's text. No foreign key, so labels outlive the job's TTL.
- **Implied feedback**: `a` on a job some filter said no to rates those filters wrong — unless you've rated them yourself; undoing the decision removes it.
- **Decisions** are also logged in `reviews` (decision, reason, every filter's verdict at the time, the job's text).

## Why
Your decision drives Step 3 (the queue) and the TTL; your per-filter ratings are the training labels for each filter's own preference model (filters encode different preferences, so each gets its own model).

## Files
- `src/auto_app/select/review.py` — the data layer: lists, job detail, `decide`, `rate`, `add_reason`, `undo`; usable without a UI.
- `src/auto_app/select/review_app.py` — the Textual app (screen and keys only).
- `src/auto_app/cli.py` — `filter review`; `want_to_apply` in `job mark` / `--state`.
- `src/auto_app/core/db/tables.py`, `migrations/versions/7d2ba831d6e3_review_feedback.py` — `feedback`.
- `src/auto_app/core/db/store.py` — review / feedback methods; no expiry for queued jobs. `job_ttl.py`, `process/jobs_view.py` — queued jobs.
- `pyproject.toml` — `textual`.

## How to use
```bash
uv run auto-app filter review
uv run auto-app filter review -f ml-engineer
uv run auto-app job list --state want_to_apply
```

## Tests
`tests/select/test_review.py`:
- lists, detail and filter scope;
- decisions: state, `reviews` row, no expiry, only `want_to_apply` / `not_applied` allowed;
- implied feedback on an un-recommended job and its undo;
- rating upsert with version, reason and undo;
- the app driven by Textual's pilot (rate, open, decide, undo, search, reason, switch lists);
- the CLI's errors, plus `job mark` / `job list` with `want_to_apply`.

229 tests pass.

## Follow-ups
- The preference model per filter (design open; labels are now collected).
- Step 3's queue policy (`apply queue`, one at a time / spaced out).
