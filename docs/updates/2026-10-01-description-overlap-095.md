# Dedup: description-overlap rule raised to 0.95 — 2026-10-01
**Step:** 1   **Status:** done

## What
Level 2 rule 3 (same company, same title, descriptions overlapping) now needs a containment of **≥ 0.95**, up from 0.85 (`CONTAINMENT_MATCH` in `src/auto_app/process/dedup.py`). The veto threshold (< 0.5) is unchanged. Pairs from 0.5 up to 0.95 are listed as near misses.

## Why
The user chose a stricter match: only near-identical descriptions should merge two postings without an employer key, req id or identical text.

## Changes (files)
- `src/auto_app/process/dedup.py`: `CONTAINMENT_MATCH = 0.95`.
- `tests/process/test_dedup.py`: a test that a pair overlapping 0.9 is no longer merged.
- Docs: `docs/step1-collect.md` §7.3 and §7.6; the `README.md` dedup description.

## Tests / verification
- 123 tests pass.
- On the 38 saved NVIDIA postings: still 38 jobs and 7 near misses. Nothing there was merged by this rule, since the title veto already split the 2026/2027 pair.
