# `job browse`: the job pool in a full-screen app — 2026-10-04
**Step:** 1–2   **Status:** done. Tested offline (data side + Textual pilot).

## What
- **`auto-app job browse [-s SOURCE]… [-c TEXT] [--state TAB]`** — every job in a Textual app, on the same screen as `filter review`:
  - **tabs**: All, Not evaluated, Recommended, Un-recommended, Want to apply, Applied, Not applied, Archived;
  - **columns**: state and expiry are added to review's;
  - **detail pane**: what `job show` prints (postings with how they joined, matching keys, warnings), each filter's verdict and your feedback, the description.
- **Keys**:
  - `m` opens a state menu, with the same rules as `job mark`;
  - `s` / `c` narrow the list to sources / a company;
  - review's keys work as before (`a` `n` `g` `b` `r` `u` `o` `/`), and undo covers `m` too.
- **Archived records** are read-only. Their description is the copy kept with the record.
- `job list` / `job show` are unchanged (plain text).

## Internal
- The review app became a base: overridable lists, columns, detail header and read-only rule, plus its own prompts. `JobBrowserApp` subclasses it.
- `job mark`'s rules moved to `DbStore.mark_job`, shared by the CLI and the app.
- `Review.detail` takes the list row (no full reload per cursor move) and reads archived descriptions.

## Files
- `src/auto_app/process/job_browser.py` (new) — `JobBrowser`, `StateMenu`, `JobBrowserApp`.
- `src/auto_app/select/review_app.py`, `review.py` — hooks.
- `src/auto_app/core/db/store.py` — `mark_job`.
- `src/auto_app/cli.py` — `job browse`; `job mark` uses `mark_job`.

## How to use
```bash
uv run auto-app job browse
uv run auto-app job browse --state want_to_apply
```

## Tests
`tests/process/test_job_browser.py`:
- tabs, counts, source / company narrowing, search over sources;
- mark with keep-in-tab and undo; archived marking refused; archived description;
- the app via the pilot: detail content, the `m` menu (its keys don't reach the app), undo, `s` narrowing, archived read-only;
- CLI checks.

233 tests pass.
