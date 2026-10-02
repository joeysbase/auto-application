# Source plugins + company collection — 2026-09-29
**Step:** 1   **Status:** done (milestones C3, C4)

## What
- **Plugin system.** Built-in job-site plugins (LinkedIn, Handshake, Jobright) plus any `*.py` under `plugins/` are discovered at startup (`src/auto_app/sources/registry.py`). Two kinds: browser (`SourceAdapter`) and API (`ApiSource`). Plugins declare which generic search fields they support — `Native` (site applies it), `Local` (applied to what the site returns), or undeclared = deferred to Step 2 — and a `SiteFilters` schema for site-specific options.
- **Config split** into `defaults` / `job_sites` / `companies` (old layout migrated automatically; your `config/searches.yaml` was rewritten, backup kept in the session scratchpad). A company without `search:` copies the job-site searches' settings; its own `search:`/`searches:` are validated against its plugin — unsupported explicit fields and unknown `site_filters` are startup errors; copied fields a platform can't apply are deferred with a notice.
- **Planner** (`collect/plan.py`) resolves every search and records, per field, native/local/deferred; `auto-app plan` prints it (also printed at the start of `collect`). Deferred fields are stored with each search run for Step 2.
- **Company collection flow** (`collect/api_collector.py`): list jobs once per company per run → plugin's local filters → card filters → Level 1a → (detail fetch if needed, rate-limited) → save raw. Throttled HTTP (≈1 req/s), honest User-Agent, 403/429 → cooldown, 5xx/timeouts retried.
- **Platform plugins** (`plugins/platforms/`): Ashby, Greenhouse, Lever (full lists, filtered locally), Workday (server-side search + facets; detail per new job).
- **CLI:** `collect -s SITE -c COMPANY --job-sites --companies`, `plan`, `plugins [NAME]`, `add-company <url>`; `probe`/`sources` understand companies.
- Shared collector bookkeeping moved to `collect/collector_base.py`; `inspect` understands API postings (`record.json`, `detail.json`).

## Why
Collect first-hand postings from company career sites alongside LinkedIn, with each site's different search support made explicit instead of silently dropped.

## Live verification
First `collect --companies` (posted within 1 day, keywords "machine learning engineer", "applied scientist", United States):

| Company | Platform | Listed | Matching | Saved |
|---|---|---|---|---|
| NVIDIA | Workday | 347 | 13 (8 dropped by title filter) | 5, each with full description |
| Adobe | Workday | 82 | 1 (dropped by title filter) | 0 |
| UiPath | Ashby | 103 | 0 in the last day | 0 |

`add-company https://jobs.lever.co/palantir` detected Lever and found 318 jobs.

## Findings
- Workday's `searchText` is broad (it matches descriptions): NVIDIA returned e.g. "HPC Middleware Developer" and a "PhD Research Intern" (NVIDIA marks interns full-time). Narrow with `site_filters.facets` (e.g. `{"Job Type": ["New College Graduate", "Regular Employee"]}`) or leave to Step 2.
- `add-company` can't detect platforms from JavaScript-rendered marketing careers pages (anthropic.com/careers, careers.adobe.com); pass the job-board URL instead.
- Workday requires `Accept: application/json` (HTTP 406 otherwise).

## Tests
43 tests: planner/validation/migration, each platform against saved real responses (`tests/fixtures/`), company collection end to end over a fake HTTP transport (saved, known next run, no detail refetch, 429 → cooldown), platform detection, `--scheduled` window/interval. Lint (ruff F) clean — it caught a deleted `--scheduled` helper, now restored and tested.

## Follow-ups
Microsoft (browser company plugin, C5); parse (C6); LinkedIn not re-run live after the refactor (covered by the fake-site tests).
