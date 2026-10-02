# Dedup: a different title is a different job — 2026-10-01
**Step:** 1   **Status:** done

## What
- **New merge veto:** postings whose normalized titles differ are never the same job. Only an identical employer job key overrides it, since that means the same posting.
- **Rule 3** ("description") no longer accepts merely *similar* titles (word similarity ≥ 70); the titles must be equal after normalization. `TITLE_SIM_MATCH` is removed.
- **New near miss:** a pair with matching descriptions but different titles is listed as "descriptions match (overlap …) but titles differ (similarity …) — different jobs".

## Why
`dedup --preview` merged two NVIDIA postings: "ASIC Verification Engineer - New College Grad 2026" (JR2012573) and "… 2027" (JR2026343). Their descriptions overlap 0.95, and the title similarity of 86 passed the old threshold. They are separate requisitions for different graduating classes.

The user's rule: *if the title is different, whoever posted it treats them as different jobs.* Normalization still removes cosmetic differences (case, abbreviations, "(Remote)" and similar noise, requisition numbers in titles), so only real title differences count.

## Changes (files)
- `src/auto_app/process/dedup.py`: the title veto in `veto()`; rule 3 now checks containment only; a new near-miss text.
- `tests/process/test_dedup.py`:
  - the NVIDIA 2026/2027 case;
  - an employer-key override test;
  - the Easy Apply copy test now uses the same title.
- Docs: `docs/step1-collect.md` §7.1 and §7.3; the `README.md` dedup description.

## Tests / verification
- 122 tests pass.
- On the 38 saved NVIDIA postings, `--debug-files dedup --preview` now gives 38 jobs (previously 37). The 2026/2027 pair appears as a near miss.

## Follow-ups
- Jobs already in the database aren't re-split automatically. Run `auto-app dedup --rebuild` while all jobs are still `collected` (the database is empty right now, so nothing is affected).
- Watch for LinkedIn copies that retitle a company posting and have no employer key, e.g. Easy Apply. These now stay separate jobs, by design.
