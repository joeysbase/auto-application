# `filter review` merged into `job browse` — 2026-10-04
**Step:** 2   **Status:** done.

## What
- **`auto-app filter review` is gone.** You review in **`auto-app job browse`**:
  - `--state recommended` opens on the Recommended tab;
  - the Recommended, Un-recommended and Want to apply tabs are the old review lists.
- **`job browse` gained `-f/--filter NAME`** (repeatable): only jobs those filters judged, and only their verdicts (as `filter review -f` did). An unknown name is refused, with the list of filters that have judgments.
- **One key set:** `a` `n` `g` `b` `m` `r` `u` `o` `/` `s` `c` `q`, plus `tab`, `←` `→` and `1`–`9`.
- **The detail pane** shows `job show`'s details, then a "Filters" section (verdicts, lookups, your feedback), then the description.

## Why
`filter review`'s three lists were a subset of `job browse`'s tabs, on the same screen with the same keys. Two commands meant two things to learn and document, and room for small differences to creep in.

## Files
- `src/auto_app/process/job_browser.py` — now the only app: `JobBrowser` (lists + `mark`, on top of `Review`), `StateMenu`, `JobBrowserApp`, `check_themes`.
- `src/auto_app/select/review_app.py` — removed.
- `src/auto_app/select/review.py` — keeps the answers (detail, decide, rate, reason, undo); its own lists were removed.
- `src/auto_app/cli.py` — `filter review` removed; `job browse -f`.
- Docs: README (one `job browse` section with the review details), step 2 §8 / §10 / status, the overview, step 3 §0, `.env.example`.

## Tests
`tests/select/test_review.py` now runs on `JobBrowser` / `JobBrowserApp`:
- the review tabs, decisions, implied feedback, ratings, undo, the pilot run and themes;
- `-f` narrowing;
- the CLI checks for `job browse -f`.

`tests/process/test_job_browser.py` is unchanged in what it covers.
