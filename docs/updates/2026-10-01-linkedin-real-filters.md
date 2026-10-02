# LinkedIn: real filters on the newer search page, `site_filters` — 2026-10-01
**Step:** 1   **Status:** done (verified live). Replaces the "filters written into the query text" approach of [2026-10-01-linkedin-newer-page-default](2026-10-01-linkedin-newer-page-default.md).

## What
- **Nothing is added to the keywords any more.** The LinkedIn plugin sets the newer search page's own filters, which were found by applying each filter button in the browser and reading the resulting URL:

| Filter | URL |
|---|---|
| location | `geoId` (United States = `103644278`) |
| date posted | `f_TPR=r86400 / r604800 / r2592000` |
| workplace | `f_SAL=f_SA_id_225001:272001`. Only Remote exists |
| employment type | `f_SA_id_226001:` Full-time `272015`, Part-time `273001`, Contract `274001`, Internship `275001`, Volunteer `272002` |
| experience level | `f_SA_id_227001:` Entry-level `276001`, Senior `277001`, Manager `278001`, Director `272003`, Executive `279001` |
| Easy Apply / Under 10 applicants / In my network | `f_AL=true` / `f_EA=true` / `f_JIYN=true` |

  Several filters in `f_SAL` are joined with `$`, and several values with `,`.
- **Generic fields map onto these only where LinkedIn has an exact equivalent:**
  - `employment_types`: full_time, part_time, contract, internship;
  - `experience_levels`: entry, director, executive;
  - `workplace_types`: exactly `[remote]`;
  - `posted_within_days`: rounded up to 1 / 7 / 30 days;
  - `locations`: United States.
- **Anything else goes to Step 2, with a notice in `plan`**, e.g. associate or mid_senior, temporary, or a workplace set with hybrid or on-site.
  - The whole field is deferred, so LinkedIn never drops a value you asked for. Applying "Remote" for `[remote, hybrid]` would have lost the hybrid jobs.
  - A location other than the United States without a `geo_id` is a config error that explains how to get the id.
- **New `site_filters` for LinkedIn** (`LinkedInFilters`), LinkedIn's buttons set directly. Each replaces the generic mapping:
  - `employment_type` (adds `volunteer`);
  - `experience_level`, LinkedIn's own levels: `entry_level | senior | manager | director | executive`;
  - `remote`, `easy_apply`, `under_10_applicants`, `in_my_network`, `geo_id`.
- **"Related" results are dropped.** After the exact matches, the page lists jobs that ignore the filters, below "We found more results related to your search that may not be exact matches". The plugin drops the cards after that note, and a new engine hook, `SourceAdapter.end_of_results()`, stops reading further pages of that query. A search with no exact matches is reported as such, not as "selectors may be stale".
- **Plugin hook `check_search(search)`:** returns errors and notices about what a site can't filter exactly. The planner shows the notices in `plan` and turns the errors into config errors.

## Why
The user wants site-specific search options to be configured as `site_filters` and applied as the site's real filters: "now it just add it to keyword which is not the desired feature".

## Findings (live)
- The user's own browser URL, `f_SAL=f_SA_id_226001:272015$f_SA_id_227001:276001`, was **Full-time + Entry-level**, not a salary range. The generated URL for the `test` search is now identical to it.
- Applied filters show as active buttons and LinkedIn rewrites the query box to describe them:
  - generic fields → Past week, Entry-level, Employment type (2);
  - `site_filters` → also Remote, Easy Apply, Senior and Under 10 applicants (41 results, 2 exact before the "related" note).
- The account's saved job-seeker preferences (On-site, Full-time) appear in LinkedIn's "Preferences match" box but don't change the filters.

## Changes (files)
- `src/auto_app/sources/linkedin/adapter.py`: the ID tables, `LinkedInFilters`, `search_url`, `check_search`, `end_of_results` and `read_cards`. The query-text code was removed.
- `src/auto_app/sources/linkedin/selectors.py`: `NOT_EXACT_TEXTS`.
- `src/auto_app/sources/base.py`: `end_of_results` hook.
- `src/auto_app/collect/runner.py`: stops on `end_of_results`.
- `src/auto_app/collect/plan.py`: `check_search` hook.
- Tests: `tests/sources/test_urls.py`, `tests/sources/test_linkedin_cards.py` (the related-results note) and `tests/collect/test_runner_fakesite.py` (the engine stops paging).
- `config/searches.yaml` and `config/searches.example.yaml`: a LinkedIn search template with every option; the example also gains an `ml-easy-apply` search using `site_filters`. Docs: `README.md`, `docs/step1-collect.md` §2.5 and §2.7.

## How to use
```yaml
sources:
  linkedin:
    searches:
      - name: test
        keywords: ["applied scientist"]
        locations: ["United States"]
        posted_within_days: 1
        employment_types: [full_time]     # → LinkedIn's Employment type filter
        experience_levels: [entry]        # → Experience level: Entry-level
        site_filters:                     # LinkedIn-only options
          easy_apply: true
          under_10_applicants: true
```
`auto-app plugins linkedin` lists the options; `auto-app plan -s linkedin` shows what is applied and any Step 2 notices.

## Follow-ups
- Look up `geoId`s for other places: a small table, or read them from LinkedIn's location box.
- `f_C` (company) and `f_VJ` are not exposed yet.
