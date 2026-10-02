# LinkedIn searches use the newer search page by default — 2026-10-01
**Step:** 1   **Status:** done (verified live)

## What
- Generated LinkedIn searches now open **`/jobs/search-results/`**, the page the search bar on linkedin.com/jobs opens, instead of the classic `/jobs/search/`. No separate config entry is needed. The extra `applied-scientist-new` search was removed from `config/searches.yaml`.
- **The query is written the way you'd type it in the search bar**, because the newer page reads only `keywords`, `geoId` and `f_TPR` from the URL:
  - format: `[experience] keywords [job type] [workplace] in <location>`;
  - e.g. `experience_levels: [entry]`, `employment_types: [full_time]`, `locations: ["United States"]` → *"entry-level applied scientist full-time in United States"*, plus `f_TPR` for posted-within;
  - several values are joined with "or"; selecting all three workplaces, or every job type, leaves that part out.
- **`auto-app plugins linkedin` shows how each field is applied:**
  - keywords: matched by meaning;
  - location: query text, which sets LinkedIn's location filter;
  - posted within: `f_TPR`;
  - workplace / job type / experience: query text, treated as a **preference, not a strict filter**. Step 2 applies them exactly.
- **`sorted_by_date = False`:** the newer page orders by relevance only, so early stop is off and `max_pages` sets the depth.
- The classic page is still available through `site_url` (strict `f_E`, `f_JT`, `f_WT`, `sortBy=DD`).

## Why
The user searches from the linkedin.com/jobs search bar and wants the tool to search the same way by default.

## How it was verified (live, the tool's LinkedIn profile)
- **URL parameters:** `f_WT`, `f_JT`, `f_E`, `sortBy` and `location=` are removed from the URL by the newer page. With or without them, the results were identical.
- **Location in the query:** "applied scientist **in Seattle, WA**" set the location to *Seattle, WA (50 mi)*, and the results were in Seattle.
- **Filter words in the query:** "entry-level … full-time remote or hybrid" made the results remote, but didn't switch the Experience level / Employment type buttons on. They act as a preference.
- **Paging:** `start=0/25/50` gave 3 × 25 cards, no overlap.
- **The real search:** `collect -s linkedin --search test --cards-only` (debug files) opened `…/search-results/?keywords=entry-level%20applied%20scientist%20full-time%20in%20United%20States&f_TPR=r86400` and read 25 cards (23 new, 1 known, 1 filtered by title words).

## Changes (files)
- `src/auto_app/sources/linkedin/adapter.py`: `query_text()`, `search_url()` on the newer page, filter notes, `sorted_by_date = False`.
- `tests/sources/test_urls.py`: the URL and query-text tests.
- `config/searches.yaml`; docs: `README.md` (configuration), `docs/step1-collect.md` §2.5 and §2.7.

## Follow-ups
- Results are broader than on the classic page (meaning-based matching). Card title filters and Step 2 narrow them down.
- The salary range from the user's browser URL (`f_SAL`) isn't generated. It could become a LinkedIn `site_filters` option once its encoding is understood.
