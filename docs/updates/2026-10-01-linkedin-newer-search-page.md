# LinkedIn: the newer search page (`/jobs/search-results/`) — 2026-10-01
**Step:** 1   **Status:** done (verified live on cards, paging and one job fetch)

## What
- LinkedIn searches can now use the **newer search page**, the one the search bar on linkedin.com/jobs opens, through `site_url`. Generated searches still use the classic `/jobs/search/` page.
- **Card IDs from an attribute pattern:**
  - On the newer page, cards are `div[role=button]` without links. The job ID is only in `componentkey="job-card-component-ref-<id>"`.
  - `SourceSpec.card_id_attributes` now also accepts `"attr~regex"`, where the regex's first capture group is the job ID. This works for any browser plugin, not just LinkedIn.
- **LinkedIn selectors** (`SELECTOR_VERSION = linkedin-2026-10-01`):
  - the `componentkey` card-ID pattern;
  - the newer page's results list (`[data-testid="lazy-column"]`) for scrolling;
  - the hidden " (Verified job)" label is removed from card titles.
- **Config:** a new LinkedIn search `applied-scientist-new` with the user's browser URL as `site_url` (`currentJobId` and `origin` removed, since they only record what was clicked).

## Why
The user's own LinkedIn searches showed different results from the tool's. They search from the /jobs/ search bar, which opens the newer page. The tool only read the classic page and found 0 cards on the newer one.

## Changes (files)
- `src/auto_app/sources/common/js.py`: the `attr~regex` card IDs.
- `src/auto_app/sources/base.py`: a comment on the new form.
- `src/auto_app/sources/linkedin/selectors.py`, `linkedin/adapter.py` (`read_cards` title cleanup).
- `tests/sources/test_linkedin_cards.py` (new): card reading on minimal copies of both pages' markup, served locally (no network).
- `config/searches.yaml`; docs: `README.md` (`site_url`), `docs/step1-collect.md` §2.7.

## How to use
```yaml
sources:
  linkedin:
    searches:
      - name: applied-scientist-new
        site_url: "https://www.linkedin.com/jobs/search-results/?keywords=applied%20scientist&geoId=103644278&distance=0.0&f_TPR=r86400&f_SAL=…"
```
```bash
uv run auto-app collect -s linkedin --search applied-scientist-new --cards-only --max-pages 1   # check first
uv run auto-app collect -s linkedin --search applied-scientist-new
```

## Tests / verification
- 2 new tests. The full suite passes: 126 tests, after the leftovers of a rewound conversation branch were removed (see below).
- Live, with `--debug-files --cards-only --max-pages 2`: 25 + 25 cards with job IDs and titles. Page 2 was all new IDs, so `start=` paging works. One card was filtered by title words.
- `probe linkedin 4455900105` (a card from the newer page) fetched the job page. The apply link was resolved to amazon.jobs, and `parse` filled every field except salary, which that page doesn't show.

## Caveats (observed)
- **Loose keyword matching:** an "applied scientist" search also returns Data Scientist, ML Engineer and Client Success Manager. Card filters and Step 2 have to narrow this down.
- **Hidden filters:** the page showed **Entry-level** and **Full-time** switched on and rewrote the query as "entry-level applied scientist full-time posted in the past 24 hours", although neither filter is in the URL. It seems to carry filters over from earlier searches on the same profile. A salary chip wasn't visible.
- **Relevance order** (no sort-by-date in the URL): early stop on already-saved cards rarely triggers, so depth is limited by `max_pages`.
- The login check ("could not confirm login state") warns on every LinkedIn run right now, but collection works. The signed-in navigation selectors may need refreshing.

## Follow-ups
- Decide whether to clear the carried-over filters, e.g. by setting the filters explicitly in the URL.
- Refresh the `LOGGED_IN` selectors.
- LinkedIn `site_filters` (salary, Easy Apply, …) for generated searches.

## Note: leftovers from a rewound branch (cleaned up)
An earlier, rewound branch of this conversation had started a "title relevance" card filter (`auto_app.collect.relevance`, `card_filters.title_must_match_keywords`). Rewinding restored the source files, but some test edits made through shell commands stayed behind: `tests/collect/test_relevance.py` and a `title_must_match_keywords=False` argument in `tests/test_pipeline.py` and `tests/test_debug_files.py`. They broke 2 tests and one test import. They were removed, and nothing from that feature remains.
