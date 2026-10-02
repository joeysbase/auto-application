# Job state model, job TTL and archived jobs (design) — 2026-10-01
**Step:** 1–3 (cross-cutting)   **Status:** designed — implemented in [2026-10-01-c11-job-state-ttl](2026-10-01-c11-job-state-ttl.md)

A design discussion, recorded here; the result is in [overview §3](../README.md#3-job-state), [step1 §7.5, §8.2, §9](../step1-collect.md#9-retention-ttl), [step2](../step2-select.md) and [step3](../step3-apply.md). Nothing below is implemented yet; today every job has `status = collected` and only posting-data retention (C9) exists.

## Starting point
- `auto-app purge` (C9) already drops raw data, stale descriptions, old sightings and logs, but jobs never expire: `jobs.expires_at` exists and is never set, and a job that vanished from every source looks the same as an active one.
- The planned job statuses were seven (`collected`, `filtered_out`, `shortlisted`, `skipped`, `tailored`, `approved`, `applied`).

## Decisions

### 1. Job state: two fields instead of one status
- **Step 2 recommends, you decide.** Step 2 returns a list of jobs for you to decide on; it never decides for you.
- **Filtered jobs are a subset of the pool**, not a stage after it: every collected job stays in the pool; Step 2 marks the subset it recommends.
- **"Step 2 didn't recommend it" is kept as its own state** (`un_recommended`), distinct from "not evaluated yet", so Step 2's choices can be audited, overridden and calibrated.
- Result — two fields, five states:

| State | `step2` | `decision` |
|---|---|---|
| not evaluated | — | — |
| un_recommended | `un_recommended` | — |
| recommended, undecided (un_applied) | `recommended` | — |
| recommended, applied | `recommended` | `applied` |
| recommended, not applied | `recommended` | `not_applied` |

- Step 3's progress (tailored, approved, submission attempts) lives on the **application record**; the job's `decision` becomes `applied` only when submitted.
- Replaces the seven-status plan (`filtered_out` + `skipped` → `un_recommended` / `not_applied`; `shortlisted` / `tailored` / `approved` → recommended-undecided + application status).

### 2. Job TTL: one global setting, a TTL per state
```yaml
jobs:
  ttl_days:
    unfiltered: 14        # not evaluated → deleted
    un_recommended: 7     # short → deleted
    recommended: 30       # long → archived
```
- **Global**, not per source. The clock is time since any of the job's postings was last seen.
- **un_recommended: short TTL; recommended: long TTL** (as requested).
- **Unfiltered and un_recommended jobs are deleted** after their TTL — the job and **every posting linked to it** (with raw data, keys, locations, screenshots). A listing seen again later is collected as new.
- **All recommended jobs have a TTL too, but are not deleted** — they're **archived**: kept as records, **excluded from dedup**. (Earlier idea "applied jobs never expire" was replaced by this.)

### 3. Archived jobs keep only what's necessary
| Kept | Dropped |
|---|---|
| summary (title, company, locations, apply URL, first/last seen, archived date) | raw pages / records / captured JSON, screenshots, card data |
| Step 2 result (score, one-line reason) | full parsed / normalized records, MinHash |
| your decision + date; for applied, link to the application record | descriptions of undecided / not-applied jobs |
| posting identity (source, id, URL, title, first/last seen) | |
| **matching keys** (employer key, company + req id, description hash, company + title) — kept, as decided | |
| **description text for applied jobs** — kept, as decided (listings disappear; needed for interviews) | |

≈ 1 KB per archived job (a few KB with a description).

### 4. Archived jobs and dedup
- **Excluded from linking** (Level 1b and Level 2): a new posting that would have matched becomes a **new job** and goes through Step 2 again.
- **Repost warning:** new jobs are still checked against archived jobs' matching keys; a match adds a warning to the new job — *"looks like job #12, which you applied to on …"* / *"… which you declined"* — shown in Steps 2–3 (guards against applying twice).
- **Same listing seen again** (same source id): collect still skips it as known; the archived job **stays archived** (only the posting's last-seen date updates). Recorded as the default; open to revisit (step1 open question #6).

### 5. Related
- `retention.description_days` / `keep_description_statuses` will be removed — the job TTL deletes or archives jobs before that rule would apply.
- Fix needed first: a job's `last_seen_at` currently only updates when it gains a posting; the TTL pass must recompute it from its postings (which collect refreshes whenever it sees them, including as `known`).
- Caveat kept in the docs: "not seen" isn't always "closed" — reliable for company sources with `posted_within_days: all`, not for LinkedIn (recent-only searches, early stop).

## Docs updated
- `docs/README.md` §3 Job state (states, lifecycle, TTL table, archive summary).
- `docs/step1-collect.md`: status table, §7.4.1, §7.5 (archived jobs, repost warning, same-listing rule), §8.2 schema (`step2`, `step2_at`, `decision`, `decided_at`, `expires_at`, `archived_at`, `archive`, `warnings`; replaces `status`), §9 (9.1 job TTL, 9.2 archived jobs, 9.3 posting-data retention), milestone C11, CLI, open question #6.
- `docs/step2-select.md`: outputs `recommended` / `un_recommended`; review = your decision; promote un_recommended; re-scoring only undecided jobs.
- `docs/step3-apply.md`: input = jobs you chose to apply to; application status carries progress; `decision = applied` on submission; submitters refuse archived / applied jobs.

## Next
C11: migration from `status` to `step2` + `decision`; `jobs.ttl_days`; TTL pass in `purge` (delete / archive, `--dry-run`) and refresh in `dedup`; compact archive; repost warnings; `jobs` filters and "expires in"; tests; README.
