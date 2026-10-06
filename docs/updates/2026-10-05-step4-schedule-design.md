# 2026-10-05 — Design: Step 4's schedule, collecting in rounds

## What
The schedule part of [Step 4 — Run](../step4-run.md) §2, replacing the "30 min on / 30 min off" cycles of the first draft:

- **A round** is one browsing session over every source's searches (in parallel), like a person checking the job sites. It ends at **the first goal reached**:
  - **`caught_up`** (default): every search has reached postings already collected (15 known in a row), or older than `posted_within_days`, or the end of its results;
  - **`time_limit`** (a cap, e.g. 30 min, spread across searches; caught up early → ends early);
  - optional `max_new_postings` / `max_pages`.
  
  Per-site safety limits always win.
- **The pause (`interval`, fixed, ± jitter) starts when collecting ends** (your call). Judging runs during it, and **the next round starts once the pause is over and the backlog is 0**. So the gap adapts to the round: a light round waits the pause, a heavy one waits for judging. Collecting never outpaces filtering. Rounds happen inside your daily window.
- **The edge cases** (your call):
  - **No usage left** → judging pauses until the reset, and collecting waits; it resumes by itself.
  - **A job that can't be judged** (3 tries, while the filter works on other jobs) → **unjudgeable**, and it no longer blocks.
  - **A broken filter** (a config or login error, or 3 different jobs failing in a row) → **you're notified at once and collecting stops** until you resume.
- **A round report** each time: what each source found, what ended it, how long judging took, and when the next round is.
- **Simplified (2026-10-06, your call):** `serve` is just a loop: **collect** (`auto-app run`, until the round's goal), then **one `filter run` batch** on every job not judged yet, then the pause. The next round waits for both. Filtering is in batches, not one job at a time, so there's no per-job event pipeline, queue tables, control socket or concurrency rules. Pause / resume / status go through `data/serve.json`.

## Files
- `docs/step4-run.md` (§2, §3, §9, §10).
