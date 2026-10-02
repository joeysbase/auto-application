# Normalize stage (C8a) — 2026-09-30
**Step:** 1   **Status:** done

## What
- **Employer job key** (`sources/common/urls.py`): `employer_job_key(url)` → `(platform, board, job_id)` for Workday, Ashby, Greenhouse (global ids, incl. `?gh_jid=` on company domains) and Lever URLs, ignoring locales and tracking params. Used on LinkedIn's resolved apply links and on company postings' own URLs. The Workday plugin now takes its job ids from the same shared rule (`workday_job_id`), so both sides always agree; saved NVIDIA ids are unchanged.
- **Company resolution** (`process/normalize.py`): company sources → the config key; a posting whose employer link points at a configured company's board → that company (and its displayed name is learned as an alias); otherwise config names/display names, learned aliases, or the normalized name (suffixes like Inc/Corp and parentheticals removed). Learned aliases: `data/raw/company_aliases.json`.
- **Title / location / description keys**: abbreviations expanded (Sr, ML, SWE…), title noise removed; locations normalized (Workday `US, CA, Santa Clara` = `Santa Clara, CA`; state names only when used as states); description cleaned of boilerplate lines, hashed, 5-word shingles + 128-permutation MinHash (`process/similarity.py`, no new dependencies).
- **Stage + CLI**: `auto-app normalize [-s] [-c] [--again]` writes `normalized.json`, state `normalized`; versioned — re-normalizes when the rules change or the posting was re-parsed. Output: per-source employer-key share, how companies were resolved, and employer keys shared by several postings. `inspect` shows the normalized keys.

## Live verification
- All 46 postings normalized. Employer keys: NVIDIA 100%, LinkedIn 24% (other LinkedIn links go to employer systems without a plugin, or are Easy Apply).
- The LinkedIn NVIDIA job resolved to company `nvidia` through its Workday link.
- **A real duplicate found:** LinkedIn `4473065722` and `4473077041` ("Simulations Software Developer", Intuitive Machines) share employer key `workday:intuitivemachines/lanterisspace:R24265` and an identical cleaned description.
- Similarity: that pair 1.0; the other 1,034 pairs max 0.40, median 0.0 — highest among different NVIDIA jobs sharing NVIDIA's company intro (calibration point for dedup).
- Two location bugs found in the spot-check and fixed (normalizer v3): "New York, NY" → "ny, ny"; "Washington, D.C." → "wa, d.c.".

## Tests
69 tests (15 new): employer keys on real resolved LinkedIn apply links (Workday with locales / odd ids like `REQ_0000082877-1`, Ashby `/application`, Greenhouse incl. company-domain `gh_jid`, Lever `/apply`); key parity between company-posting URLs and LinkedIn-style links; company names and alias learning; titles; locations; boilerplate-insensitive hashing; MinHash/containment; the stage (board resolution, shared keys, versioned skip / `--again`). Lint clean.

## Follow-ups
C8b `dedup --preview` + calibration (needs overlapping LinkedIn/company data; consider removing company-intro paragraphs from the comparison).

## Later change
2026-09-30: the "employer keys shared by several postings" listing was removed from `normalize`'s output — comparing postings is dedup's job; `auto-app dedup --preview` shows those pairs (rule `employer_key`) along with the other rules, vetoes and near misses.
