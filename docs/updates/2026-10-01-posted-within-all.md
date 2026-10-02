# `posted_within_days: all` — 2026-10-01
**Step:** 1   **Status:** done

## What
`posted_within_days` accepts `all` = no date limit, in `defaults`, a job-site search, or a company search (a number in one search still overrides an `all` default, and vice versa). Leaving the field out still inherits the default. `0` is now rejected (it used to mean nothing on companies and a meaningless "past 0 seconds" filter on LinkedIn).

- LinkedIn: no `f_TPR` ("date posted") parameter.
- Company sources: every listed job passes the date check.
- `auto-app plan` shows `posted_within_days  -  all (no date limit)`.

## Why
To collect every matching job on a company board (the NVIDIA search now uses it), not only recent ones.

## Changes
`core/config.py` (`PostedWithin` type), `collect/plan.py` (resolves `all` to no limit; plan display), `core/search.py`, `sources/linkedin/adapter.py`; README configuration section, `config/searches.example.yaml`, Step 1 doc §2.4.

## Tests
113 tests (1 new): `all` resolves to no limit for job sites and companies, LinkedIn URL has no date filter, an old company job still matches, a per-search number overrides an `all` default, `0` is rejected. Lint clean.
