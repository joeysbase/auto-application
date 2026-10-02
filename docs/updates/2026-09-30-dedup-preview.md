# Dedup preview (C8b) — 2026-09-30
**Step:** 1   **Status:** done — calibration pending overlapping data

## What
- `src/auto_app/process/dedup.py` implements §7 over normalized postings:
  - **Level 1b** (same source, different id): same employer key, or same company + title + description hash (descriptions ≥ 300 chars); employment-type veto; the earliest posting is canonical.
  - **Level 2** (across sources, plus same-source pairs 1b left apart): same employer key (1.0, overrides vetoes) → same company + req id (0.95) → same company + description containment ≥ 0.85 + title similarity ≥ 70 (0.9) → identical description hash (0.9) → probable: same company + title, a description missing, posted within 14 days (0.6). Vetoes: different employment type; both descriptions present with containment < 0.5.
  - **Grouping:** union-find, strongest links first; every merge is checked for vetoes across all members *and their Level 1b duplicates*; refused merges become near misses.
  - **Primary posting:** company source > external apply > Easy Apply, then the longest description. Job locations = all members' locations.
  - Candidate blocking: pairs are compared only if they share an employer key, company, or description hash.
- `auto-app dedup --preview [--json FILE] [--limit N] [--no-near-misses]`: prints the jobs dedup would form (members, ★ primary, each link's rule/confidence, locations) and near misses (overlap between thresholds, matching descriptions with different titles, same titles blocked by a veto, refused merges). Writes nothing; writing jobs needs the database (C7).

## Live verification
Current data: 46 postings → 45 after Level 1b → 45 jobs. The one merge is the Intuitive Machines LinkedIn pair (`4473065722` / `4473077041`, same Workday job `R24265`). No near misses: different NVIDIA jobs stay apart (description overlap < 0.5).

## Bug found by the tests
A Level 1b duplicate joined its canonical posting's group without being checked against the other group in a Level 2 merge — so a full-time company posting could be merged with an internship that was a same-source duplicate. Fixed: merge vetoes include members' Level 1b duplicates.

## Tests
77 tests (8 new), on constructed postings (real overlap is still thin): employer-key merge with the company posting as primary; Easy Apply copy merged on description + title; same title / different team kept apart and reported; veto blocks chaining across three sources; Level 1b duplicates in merge vetoes; employment-type veto; repost (Level 1b, earliest canonical) and probable match; title similarity and report text. Lint clean.

## Follow-ups
- **Calibration:** collect overlapping data (a LinkedIn search returning tracked companies + a company run over the same days), review the preview's merges and near misses, tune 0.85 / 70 / 0.5; consider removing company-intro paragraphs from comparisons.
- C7 database, then C8c (dedup writes `jobs`, `job_keys`, `job_locations`).
