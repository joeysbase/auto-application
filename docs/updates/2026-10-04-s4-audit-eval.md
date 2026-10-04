# Step 2 S4: archived jobs as training data, good / bad labels, audit and evaluation — 2026-10-04
**Step:** 1–2   **Status:** done. Tested offline; the migration checked on a copy of the real database (89 judged jobs; `filter eval` and a purge dry run work on it).

## What
- **Judged jobs are archived, never deleted** — they're training data.
  - When a job expires, it's archived if a filter judged it, it's recommended, or you decided on it. Jobs never judged are deleted, as before.
  - An archived job keeps the primary posting's parsed fields (what a filter reads, description included), its judgments and your labels; raw and normalized data are dropped.
  - Its postings are renamed `<id>#archived<job>`, so a listing seen again becomes a **new job**. The new job gets a repost warning such as "looks like job #12, … which Step 2 left out".
  - **Dedup within the archive:** an archived job with the same title and a shared matching key as an earlier archived job is linked to it (`jobs.duplicate_of`). `data purge` reports how many were linked.
  - **Fixes the purge bug:** deleting a judged un_recommended job used to fail on the `judgments` foreign key.
- **Labels:** `g` / `b` mean **good / bad job for this filter**, whatever it said. This replaces "the verdict was right / wrong".
  - `feedback.label` is the label; `correct` is now derived from it.
  - The migration converts existing ratings exactly.
  - `a` on a job a filter said no to labels it good for that filter (implied).
  - Archived jobs can be labelled, but not decided on or changed.
  - The detail pane shows the label and what it makes the verdict: right, a miss, or a bad pick.
- **`auto-app filter audit [-f NAME]… [-n 30]`:**
  - For each filter whose last sample is fully labelled, it draws a stored, uniformly random sample of the filter's "no" pool (active and archived jobs, not labelled, not sampled yet).
  - It then opens the app in **audit mode**: only the **Audit** tab (the randomly drawn jobs — no pool tabs, which would invite non-random labels), with the **evaluation panel**.
  - Keys: `g` / `b`, `B` (bad for every filter, one undo), `d` (draw `AUTO_APP_SELECT__AUDIT_MORE` = 10 more — audit mode only), `e` (panel on / off — also in `job browse`, which has no Audit tab).
  - Marks: `?` still to label, `✓` bad for every sampled filter, `✗` a miss.
  - After a label the cursor moves to the job's next unlabelled filter, else to the next job still to label (it doesn't follow the labelled job down the list).
- **`auto-app filter eval [-f NAME]… [--all-versions]`:** per filter — precision, noise, good jobs lost, recall, review load, and the training-data check. The panel shows the same numbers, recomputed after every label.
  - Ranges are 95% Wilson intervals.
  - The training-data check passes when the noise's one-sided 95% exact (Clopper–Pearson) upper bound is under `AUTO_APP_SELECT__NOISE_LIMIT` (5%). When it fails, it says how many more clean audits are needed: 59 with no miss, 93 after one, …
- **Settings:** `AUTO_APP_SELECT__NOISE_LIMIT`, `__AUDIT_SIZE` (30), `__AUDIT_MORE` (10).
- **Design doc:** the noise test is now one-sided exact (was: the upper end of the two-sided Wilson range). [docs/study.md](../study.md) has the worked example and a reading list.

## Simpler than the design (noted in step 2 §11)
- **Noise** is pooled over a filter's draws, times its current "no" count; it isn't weighted per draw.
- **Archive dedup** uses the matching keys plus the same title, not the full Level 2 rules (normalized data is dropped at archiving).
- **Parsed fields** are kept as saved (~7.5 KB per job), not recompressed.
- **`filter eval --rerun`** comes later.

## Files
- `src/auto_app/core/db/job_ttl.py` — archive judged jobs, free identities, `_link_archived`, `original_id`.
- `src/auto_app/core/db/tables.py`, `migrations/versions/f1741e85a397_…py` — `jobs.duplicate_of`, `feedback.label` (`correct` nullable), `audit_draws`, `audit_items`; the data steps convert ratings and rename postings of jobs archived earlier.
- `src/auto_app/core/db/store.py` — archived inputs (locations from the record), original ids in `job_postings`, labels in `feedback_marks`, repost wording.
- `src/auto_app/select/audit.py`, `evaluate.py` (new); `review.py` (labels).
- `src/auto_app/process/job_browser.py` — Audit tab, panel, `g` / `b` / `B` / `d` / `e`.
- `src/auto_app/cli.py` — `filter audit`, `filter eval`.
- `src/auto_app/core/config.py`, `.env.example` — the settings.

## How to use
```bash
uv run auto-app filter audit                 # label random "no" jobs; the panel updates as you go
uv run auto-app filter eval                  # the numbers, printed
uv run auto-app data purge --dry-run         # judged jobs → archived, never-judged → deleted
```

## Tests
- `tests/core/test_archive_training.py`: judged jobs archived (the old purge failure), never-judged deleted, a re-seen listing becomes a new job with a warning, archive linking, a different title not linked.
- `tests/select/test_audit_eval.py`:
  - the bounds against the worked example;
  - draws are random, stored, and skip labelled or sampled jobs;
  - the evaluation numbers;
  - audit mode via the pilot (only the Audit tab, `B` + undo, `g` → `✗`, the live panel, `e`, `d`); `job browse` has no Audit tab and can't draw; after a label the cursor goes to the next unlabelled filter / job;
  - `filter eval` / `filter audit` from the CLI;
  - the migration's conversions.
- Updated: review tests (labels), job browser tests (Audit tab, labelling archived jobs), TTL tests (freed identities, kept parsed fields).

248 tests pass.

## Changed the same day: the keys are worded as what the filter should do
"Good / bad" read like a grade of the filter's verdict ("the classification was good"). It actually means "should this filter recommend this job?". The app now says so:
- **Footer:** `g` should recommend · `b` shouldn't · `B` no filter should.
- **Detail pane:** `you: 👍 should recommend → it missed it` (or `→ it was right` / `→ a bad pick`).
- **Status line:** "ml-engineer should recommend it (it said no)".
- **Evaluation text:** "18 should be recommended", "1 missed → ≈3 missed in all".

The stored data is unchanged (`feedback.label`: True / False). The README and step 2 §7.1 / §7.4 use the same wording.

## Changed the same day: each app's footer shows only its own keys
- **`filter audit`:** labelling keys (`g` / `b` / `B`, `←→`), `u`, `r`, `o`, `/`, `d`, `e`, `q`. Decisions (`a` / `n`), states (`m`), narrowing (`s` / `c`) and tabs are hidden **and disabled** — they're not part of an audit.
- **`job browse`:** everything except `d` (drawing samples is audit-only).
- Test: `test_each_app_shows_only_its_own_keys`.

## Changed the same day: `job browse` opens on Recommended
`auto-app job browse` with no `--state` now opens on the **Recommended** tab — the jobs to review and label (it used to open on All). `--state all` opens on All; `filter run`'s closing hint says just `auto-app job browse`.
