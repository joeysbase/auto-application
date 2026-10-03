# Step 1 — Collect

**Goal:** produce one clean, deduplicated list of **jobs** from job sites and company career sites, stored in a local database, ready for Step 2.

```
1a COLLECT (raw)                                        1b PROCESS
search / list jobs ─► cards ─► L1a known? ─► fetch ─►   parse ─► normalize ─► L1b ─► L2 ─► jobs
     (per source plugin)          (skip)     save raw   (per plugin)          same-site  cross-site
```

## Contents
1. [Status](#1-status)
2. [Sources and plugins](#2-sources-and-plugins)
3. [Collection engine](#3-collection-engine)
4. [Raw data](#4-raw-data)
5. [Parse](#5-parse)
6. [Normalize](#6-normalize)
7. [Dedup](#7-dedup)
8. [Storage](#8-storage)
9. [Retention (TTL)](#9-retention-ttl)
10. [CLI](#10-cli)
11. [Milestones](#11-milestones)
12. [Risks](#12-risks)
13. [Open questions](#13-open-questions)

---

## 1. Status

Detailed history: [updates/](updates/README.md).

| Part | State |
|---|---|
| Collection engine (search runs, cards, L1a, fetch, pacing, rate pauses, retry, blocks, manual checks, interruption) | ✅ implemented, tested on fake site |
| LinkedIn (built-in browser plugin) | ✅ verified on the live site |
| Handshake (built-in browser plugin) | ⚠️ implemented, not yet verified live |
| Jobright | ⛔ disabled — serves a Cloudflare check to automated browsers that cannot load; not worked around |
| Login flow (plain Chrome, mock keychain, Cmd+Q) | ✅ verified |
| Database (`data/auto_app.db`, SQLite + Alembic) as the store for every stage; file store as a file-only debug mode (`--debug-files`); `import-files`, `export` | ✅ implemented; existing data imported (2 batches, 12 postings) |
| Plugin system: **each plugin is one source** — discovery and load-time checks, filter capabilities, `plan`, `plugins`; config `sources.<plugin>.searches` + `limits`, `from:` copying (older layouts auto-migrated) | ✅ implemented (C12) |
| Platform bases Ashby, Greenhouse, Lever, Workday; one plugin file per company (`plugins/companies/`), written by `add-company` | ✅ implemented; live: UiPath (Ashby), NVIDIA + Adobe (Workday), Palantir (Lever), Anthropic (Greenhouse, via `add-company`) |
| Company browser plugins (Microsoft…) | interface ready (§2.2); implementations left to whoever needs them |
| Parse: LinkedIn + Ashby, Greenhouse, Lever, Workday; default JSON-LD parser for other browser plugins; `auto-app process parse` with coverage | ✅ implemented; all 46 saved postings parse (41 LinkedIn, 5 NVIDIA) |
| Normalize: employer job key, company aliases, title/location/description keys, MinHash; `auto-app process normalize` | ✅ implemented; all 46 saved postings normalized |
| Dedup: Level 1b + Level 2 with merge vetoes, near misses; `dedup --preview` (dry run) | ✅ implemented; calibration waits for overlapping data |
| Dedup writing jobs, incrementally: `auto-app process dedup`, `auto-app job list`, `dedup --rebuild` | ✅ implemented; 12 postings → 10 jobs stored |
| Purge (retention) + storage-light defaults (C9): `auto-app data purge` | ✅ implemented |
| Job state model (`step2` + `decision`), job TTL with delete / archive, archived records, repost warnings, `mark` (C11) | ✅ implemented (§7.5, §8.2, §9); migration applied to the existing database (68 jobs) |
| One-command pipeline + schedule (C10): `auto-app run`, `auto-app schedule` | ✅ implemented (schedule not installed yet) |

---

## 2. Sources and plugins

### 2.1 Sources are plugins

**Each plugin is one source** — the name used in the config (`sources.<name>`), in storage and on the command line (`-s <name>`). A source has one of two collection kinds:

| Kind | How it collects | Examples | Blocking risk |
|---|---|---|---|
| **BrowserSource** (`SourceAdapter`) | Drives a real Chrome: open search → read cards → open each new job page → save raw HTML (+ any JSON the page loads) | LinkedIn, Handshake, Microsoft careers | Medium–high: pacing, rate limits, block handling apply |
| **ApiSource** | Calls a job-board API; one call returns a company's job list (often with full descriptions) | NVIDIA (Workday), UiPath (Ashby) | Very low: public endpoints meant for this |

and is either a **job site** (many employers) or a **company source** (one employer — the plugin sets `company`):

| What | Where the code is | Code needed per source |
|---|---|---|
| **Job site** | built in (`src/auto_app/sources/linkedin/` …) | — |
| **Company on a known platform** (Ashby, Greenhouse, Lever, Workday) | a **platform base** in `src/auto_app/platforms/` does the work; the company is a plugin file in `plugins/companies/` that subclasses it | ~5 lines — `name`, `company`, `board` — written by `add-company` (§2.6) |
| **Company on its own career site** (Microsoft, Google …) | a plugin file in `plugins/companies/` | one plugin |

A platform base is not a source: it has no `name`, so it is never registered. "Single employer" comes from the plugin (`company` set), not from the config.

Verified so far: **UiPath → Ashby** (public API returned 103 jobs with title, location, workplace/employment type, compensation, publish date, full description, apply URL). **Microsoft → custom site on Eightfold** (`apply.careers.microsoft.com`; its JSON API rejects direct calls → BrowserSource, no login).

### 2.2 Plugin interface

```python
# src/auto_app/sources/api.py
class RawJob(BaseModel):
    id: str            # job id (unique within the board)
    url: str           # public job page
    record: dict       # the platform's record, stored as-is

class ApiSource:
    name: str                       # the source name (a base class leaves it unset)
    company: str | None = None      # set → a company source; the employer's display name
    platform: str | None = None     # set by platform bases ("workday" …)
    board: str | None = None        # the company's board on the platform (Workday: its careers URL)
    requests_per_second: float = 1.0
    needs_detail: bool = False      # list lacks descriptions → fetch_detail per new job
    filters: dict[str, Filter]      # which search fields it supports, and how (§2.5)
    SiteFilters: type[BaseModel]    # schema of its `site_filters` (e.g. departments, facets)
    def validate_board(self, board) -> str: ...                       # checked/normalized when loaded
    async def search(self, ctx, board, search: ResolvedSearch) -> list[RawJob]: ...
    def local_filter(self, job, search) -> str | None: ...           # why it doesn't match (None = match)
    async def fetch_detail(self, ctx, board, job) -> dict | None: ...
    def parse(self, raw: RawPosting) -> CommonPosting: ...           # step 1b; `parser_version`

# src/auto_app/platforms/workday.py — a platform base
class WorkdaySource(ApiSource):
    platform = "workday"
    ...                             # search, facets, detail, parse

# plugins/companies/nvidia.py — a company source (written by `auto-app source add`)
class Nvidia(WorkdaySource):
    name = "nvidia"
    company = "NVIDIA"
    board = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"

# src/auto_app/sources/base.py
class SourceAdapter:                # BrowserSource
    spec: SourceSpec                # name, base URL, login need, selectors, link patterns, pagination …
    company: str | None = None      # set for a company career site
    filters: dict[str, Filter]      # §2.5
    SiteFilters: type[BaseModel]
    sorted_by_date: bool = True     # False → early stop disabled for this source
    def build_queries(self, search: ResolvedSearch) -> list[SiteQuery]: ...
    def parse(self, raw: RawPosting) -> CommonPosting: ...          # step 1b
    # generic, overridable: read_cards, collect_cards, wait_detail_ready, snapshot, classify_apply, check_session
```

**What a plugin declares:** site knowledge only — URLs, selectors, job-ID patterns, API calls, which search fields the site supports and how (§2.5), its own `site_filters` schema, how to map its data to common fields. A company plugin can override any base method for that company's quirks (an odd facet name, description boilerplate) without touching the shared platform code.

**What the engine does for every plugin (plugins never do these):** config validation, search planning, card filters, local filters, Level 1a known check, storage, search runs and sightings, delays, hourly/daily limits, retries, block/CAPTCHA detection, cooldowns, manual-check hand-off, stats, `inspect`. A buggy plugin can't corrupt data or hammer a site.

### 2.3 Discovery and load-time checks

At startup the app registers the built-in job sites and imports every `*.py` under `plugins/` (files starting with `_` are skipped). A class defined in that file is a source when it defines its own `name` (`ApiSource`) or is a `SourceAdapter`; bases and helpers are skipped. Each source is checked as it loads:
- the name is lowercase letters, digits, `-`, `_`, and unique;
- a platform-based plugin has a `company` and a valid `board` (normalized once — e.g. a Workday URL with `/en-US/` loses the locale).

A file that fails (import error, bad board, duplicate name) is **reported, not fatal**: it's listed by `auto-app source show` and named in the error when the config refers to it; every other source still runs. `auto-app source list` lists sources (name, job site or company + platform, file) and the available platforms; `auto-app source show <source or platform>` shows supported search fields and `site_filters`.

Company plugin files are **committed** with the code: they hold no personal data (a company name and a public board URL). What you search for, your limits and logins stay in git-ignored files.

### 2.4 Configuration

The config says **what to search on each source**, keyed by plugin name. Each source has its own named searches, because sites offer different search features (LinkedIn's experience-level filter, Workday's facets, Ashby's departments).

```yaml
defaults:                                   # shared by every search unless it sets its own
  posted_within_days: 1
  card_filters:
    exclude_title_words: [senior, staff, principal, manager, director]
    exclude_companies: []

sources:
  linkedin:
    searches:
      - name: ml-engineer-us                # unique within the source
        keywords: ["machine learning engineer", "applied scientist"]
        locations: ["United States"]
        workplace_types: [remote, hybrid, onsite]
        employment_types: [full_time]
        experience_levels: [entry, associate]
        max_pages: 5
        # site_url: "https://…"             # browser sources: a search URL copied from the browser
    limits: { max_details_per_hour: 40, max_details_per_day: 300, min_delay_s: 4, max_delay_s: 11 }

  uipath:                                   # plugins/companies/uipath.py (Ashby)
    searches:
      - from: linkedin/ml-engineer-us       # copy that search's generic fields; name defaults to "ml-engineer-us"
        site_filters: { departments: [Engineering] }

  nvidia:                                   # plugins/companies/nvidia.py (Workday)
    searches:
      - name: new-grad
        locations: ["United States"]
        posted_within_days: all
        site_filters: { facets: {"Job Type": ["New College Graduate"], "Job Category": ["Engineering", "Research"]} }
      - name: experienced
        from: linkedin/ml-engineer-us
        experience_levels: []               # Workday can't filter by level: clear the copied field
    limits: { max_details_per_hour: 60 }

  handshake: {}                             # a source with no searches doesn't run
```

Resolution and validation:
- A search's effective settings = `defaults` ← the search named in `from` (recursively) ← the search's own fields. A field left out is inherited; an empty list clears an inherited one. `posted_within_days: all` means **no date limit** (LinkedIn: no "date posted" filter; company boards: every listed job passes the date check). `0` is rejected.
- `from: <source>/<search>` copies only the **generic** fields (keywords, locations, dates, workplace/employment types, experience levels, `max_pages`, early stop, card filters). `site_url` and `site_filters` belong to one source and are never copied. A missing source or search, a malformed reference, or a loop is an error.
- Every search is validated by its source's plugin: `site_filters` against the plugin's `SiteFilters` schema; `site_url` only on browser sources; each generic field against the plugin's `filters`. A field the plugin can't apply is an **error** when written in the search itself and a **notice** (deferred to Step 2) when copied with `from` — nothing is silently ignored.
- Browser searches run one search run per keyword (× location); API searches one per search.
- `limits` sit with the source (`sources.<name>.limits`), on top of built-in defaults (LinkedIn's are stricter).
- A config entry whose plugin doesn't exist is an error; a source asked for on the command line (`-s`) that has no searches is an error; `--search` names that match nothing are an error.
- **Older layouts are migrated automatically:** `job_sites.searches` (with `sites`, per-site `site_url`) → one search per site under `sources`; `job_sites.limits` → `sources.<site>.limits`; `companies.<key>`'s `search` / `searches` / `search_from` / `limits` → `sources.<key>` (`search_from` → `from:` entries). The company's `platform` / `board` / `display_name` now belong in a plugin file; if it doesn't exist, the error prints the `add-company` command that creates it.

### 2.5 Search support differs by site

Each plugin declares, per generic search field, **where** it can be applied:

| Level | Meaning | Example |
|---|---|---|
| **Native** | the site applies it (URL or API parameter) | LinkedIn `f_E=2` for entry level |
| **Local** | the engine applies it to what the site returns — card text (browser) or the full record (API) | Ashby: `employmentType` field of each record |
| **Deferred** | only knowable from the job page → judged by Step 2 (the LLM reads the job text against your profile) | experience level on a site without that filter |

```python
class LinkedIn(BrowserSource):
    filters = {
        "keywords":           Native("keywords"),
        "locations":          Native("geoId"),
        "posted_within_days": Native("f_TPR"),             # rounded up to 24 h / week / month
        "workplace_types":    Native("f_SAL Remote"),      # only [remote]
        "employment_types":   Native("f_SAL employment type"),
        "experience_levels":  Native("f_SAL experience level"),
    }                                                      # newer search page: see §2.7

    SiteFilters = LinkedInFilters                          # employment_type, experience_level, remote, easy_apply, …

class Ashby(ApiSource):
    filters = {
        "keywords":           Local(fields=["title"]),
        "location":           Local(fields=["location", "secondaryLocations", "isRemote"]),
        "posted_within_days": Local(field="publishedAt"),
        "workplace_types":    Local(field="workplaceType"),
        "employment_types":   Local(field="employmentType"),
    }                                        # experience_levels not declared → deferred
    class SiteFilters(BaseModel):
        departments: list[str] = []
        teams: list[str] = []
```

Rules:
- Card filters (title words, companies) are local for every source.
- Fields a plugin doesn't declare are an **error** when the search sets them itself, and **deferred** (with a notice) when they come from a copied search (`from:`) — so a source's config only contains what actually works there, while one search can still be reused on sites with different support.
- **Unmappable values** (a site has no "associate" level) → warning, that value deferred.
- **Coarse date filters** (site offers only 24h / week / month) → round up natively, then apply exactly locally; never miss jobs.
- **Keywords:** browser sites run one query per keyword; API sources match any keyword locally (title by default).
- **Not sortable by date** (`sorted_by_date = False`) → early stop off; `max_pages` alone limits depth.
- Deferred fields are recorded with each search run (`search_runs.query.deferred`) and shown by `plan`. Step 2 doesn't re-apply search fields: preferences that matter beyond the search go in your Step 2 command, which the agent runs for every job ([step2 §2.1](step2-select.md#21-commandmd--run-for-every-job)).

`auto-app source plan` (and the start of every `collect`) prints where each field is applied:

```
linkedin  (job site)
  search ml-engineer-us
    keywords             native    ['machine learning engineer', 'applied scientist']
    experience_levels    native    ['entry']
    early stop           no (not sorted by date) after 15 known; max 5 pages

uipath  (company UiPath · ashby, board uipath)
  search ml-engineer-us
    keywords             local     ['machine learning engineer', 'applied scientist']
    experience_levels    deferred  ['entry']
  ! ml-engineer-us: experience_levels (copied from linkedin/ml-engineer-us) not supported by uipath → not filtered (state it in your Step 2 command if it matters)

nvidia  (company NVIDIA · workday, board https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite)
  search new-grad
    locations            native    ['United States']
    posted_within_days   -         all (no date limit)
    site_filters         plugin    {'facets': {'Job Type': ['New College Graduate'], …}}
```

### 2.6 Adding a company

- `auto-app source add <careers URL> [--name NAME] [--company "Display Name"]` — detects a known platform from the URL or the page's links/embeds (`jobs.ashbyhq.com/<board>`, `boards.greenhouse.io/<board>`, `jobs.lever.co/<board>`, `*.myworkdayjobs.com`, …), test-calls it (prints the number of open jobs), writes **`plugins/companies/<name>.py`** (a subclass of the platform base with `name`, `company`, `board`) and adds a commented search stub under `sources: <name>:` in the config, pointing at your first search for `from:`. Nothing runs for that source until you add a search. If no platform is recognized, it says a company plugin is needed. **Limitation:** many marketing careers pages (e.g. anthropic.com/careers, careers.adobe.com) load their job lists with JavaScript, so the platform link isn't in the fetched HTML — pass the job-board URL instead (open any job's *Apply* page to find it, e.g. `adobe.wd5.myworkdayjobs.com/external_experienced`).
- A company on its own career site: write `plugins/companies/<name>.py` by hand — an `ApiSource` or `SourceAdapter` subclass with `company` set (planned helper: `auto-app source new-plugin <name> --kind browser|api`, scaffolding the file, a test and `tests/fixtures/<name>/`).
- Each plugin is tested offline against saved API responses / pages.

### 2.7 Source notes

| Source | Notes |
|---|---|
| **LinkedIn** | **Generated searches use the newer search page** `/jobs/search-results/` (what the search bar on linkedin.com/jobs opens). Its URL filters, found 2026-10-01 by applying each filter button and reading the URL: `keywords`; `geoId` (location; United States = 103644278 — the `location=` text is ignored); `f_TPR=r86400 / r604800 / r2592000` (Date posted: 24 h / week / month); **`f_SAL=f_SA_id_<filter>:<value>,<value>$…`** carries every multi-choice filter, despite the name — `225001` workplace (only Remote `272001`), `226001` employment type (Full-time `272015`, Part-time `273001`, Contract `274001`, Internship `275001`, Volunteer `272002`), `227001` experience level (Entry-level `276001`, Senior `277001`, Manager `278001`, Director `272003`, Executive `279001`); `f_AL=true` Easy Apply, `f_EA=true` Under 10 applicants, `f_JIYN=true` In my network (also seen: `f_C` company, `f_VJ`). The classic page's `f_WT`, `f_JT`, `f_E`, `sortBy` are ignored. Generic fields map onto these only where LinkedIn has an exact equivalent; a field with any value LinkedIn can't filter is left unfiltered as a whole (with a `plan` notice), so no wanted value is filtered out; `site_filters` (`LinkedInFilters`) set the buttons directly. After the exact matches the page appends "related" results below a note ("…may not be exact matches, but could still be a great fit"), which ignore the filters: cards after the note are dropped and no further pages are read (`end_of_results` hook). Relevance order only → `sorted_by_date = False`; `start=` paging works (25 per page). Cards are `div[role=button]` without links; the job id is in `componentkey="job-card-component-ref-<id>"` (the `attr~regex` card-id form); a hidden " (Verified job)" title label is removed. The **classic page** `/jobs/search/` (`f_TPR, f_WT, f_JT, f_E, sortBy=DD`; cards `li[data-occludable-job-id]`) still works through `site_url`. Job pages are the same for both (`/jobs/view/<id>/`). Detail ready: `h2:has-text("About the job")` (2026-09 layout, hashed class names). Job data is in HTML, not JSON. Easy Apply jobs have no external URL. Strictest limits (40/h, 300/day). |
| **Handshake** | School SSO login (`sources.handshake.limits.base_url` for school subdomains). Search URL format unverified → use `--interactive` or `site_url`. |
| **Jobright** | Disabled: automated browsers get a Cloudflare check that won't load. Use by hand. |
| **Ashby** | Official public API: `GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` → full list with descriptions; country from `address.postalAddress`. Verified: UiPath (103 jobs). |
| **Greenhouse** | Official public API: `GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` → full list with HTML content; US detected from office locations / state codes. Tested against a saved Anthropic response. |
| **Lever** | Official public API: `GET api.lever.co/v0/postings/{board}?mode=json` → full list with descriptions; `country` code, `categories.commitment`. Verified: Palantir (318 jobs). |
| **Workday** | `POST https://<tenant>.wdN.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` (needs `Accept: application/json`) — **not an official API**: the undocumented endpoint the career site itself uses (public data, no login), called directly — an exception to §3's "read only what the page loads" rule, kept at ≈1 req/s with per-company limits; if a company starts refusing it, switch that company to a browser plugin. Server-side `searchText` + facets: locations resolved to facet ids (e.g. "United States" → `locationHierarchy1` / `locationCountry`), `employment_types` → `timeType`, any other facet via `site_filters.facets` (e.g. NVIDIA `{"Job Type": ["New College Graduate"]}`). Can't sort by date → all pages read (20/page); posted-within applied locally from "Posted Today / Yesterday / N Days Ago". List lacks descriptions → detail GET per new job (counts toward rate limits). Search is broad (matches descriptions), so titles vary; Step 2 filters. Verified: NVIDIA (`NVIDIAExternalCareerSite`), Adobe (`external_experienced`; careers.adobe.com is a Phenom front end over Workday). |
| **Microsoft** | Browser plugin, no login; Eightfold-based UI; capture the JSON its pages load. |

---

## 3. Collection engine

### 3.1 Terms

| Term | Meaning | Stored in |
|---|---|---|
| **Batch** | One `auto-app collect run` run | `batches` |
| **Search run** | One search on one source within a batch: *job site* = site × keyword × location (`search_name` = the job-site search); *company* = one company search (`search_name` = `<company>` or `<company>/<search>`) | `search_runs` |
| **Search result / sighting** | One card (browser) or one matching job (API) seen by a search run, with its outcome | `search_results` |
| **Posting** | One job on one source, saved once (first time its page/record is fetched) | `postings` + `posting_raw` |

### 3.2 Browser flow (per search run)

```
open search (page 1)  [--interactive: user adjusts filters, collection starts from that URL]
loop pages (≤ max_pages):
    scroll the list, reading cards as they render
    for each new card on the page:
        1. card filter (title words, companies)          → filtered
        2. (source, job_id) in postings?  = Level 1a      → known   (touch last_seen_at)
        3. daily ceiling reached?                         → budget_skipped
        4. hourly limit reached?                          → rate pause, then continue
        5. open job page, wait for ready, snapshot, save → fetched  (or fetch_failed)
        record a sighting for every card
    stop: early_stop (15 consecutive known, promoted excluded) | max_pages | daily ceiling | no more results | block
```

Two tabs per source: results tab (keeps scroll position) and detail tab. Company **browser** plugins (Microsoft) use this same flow, with their native filters and `site_filters` built into the search URL.

### 3.3 Company flow (API plugins)

```
list_jobs(board)  — once per batch, shared by all of this company's searches
for each of the company's searches:
    local filters, as planned in §2.5 (keywords on title, location, posted within, site_filters …)
    for each matching job:
        card filter → filtered
        Level 1a (source=<platform>, source_job_id=<board>/<id>) → known
        (Workday: fetch_detail, rate-limited)
        save raw record → fetched
        record a sighting
```
Non-matching jobs are counted in the search run, not stored as sightings (big boards have thousands).

### 3.4 Pacing and limits

| Control | Where | Behavior |
|---|---|---|
| `max_pages` | per search (`--max-pages`) | how deep each browser search goes; everything new within it is fetched |
| `min_delay_s` / `max_delay_s` | per source | random delay before every navigation |
| `long_pause_every` / `long_pause_s` | per source | longer pause every N job pages |
| `max_details_per_hour` | per source | **rate pause** when reached, resume when the rolling hour frees up |
| `max_details_per_day` | per source | safety ceiling; the rest become `budget_skipped` |
| `retry.max_share_of_hourly` (0.5) | global | retry pass may use at most this share of the hourly limit |
| `requests_per_second` | API plugin | per-host request rate |

Limits count job-page opens **across runs** (from sighting history), so re-running doesn't reset them. Pauses are per source; other sources continue. Runs have no time limit. `--scheduled` additionally enforces a run window (08:00–23:00) and minimum hours between runs (for cron).

### 3.5 Retry pass

Before searches, each source re-fetches cards whose latest outcome was `fetch_failed` / `budget_skipped`, seen within 7 days, with < 3 failed attempts and still no posting — capped at `max_share_of_hourly × max_details_per_hour` per run, so new searches always run.

### 3.6 Sessions and login

- One persistent Chrome profile per source: `browser-profiles/<source>/`.
- `auto-app source login <source>` opens **plain Chrome** (no automation flags, so Google/SSO sign-in works) with `--use-mock-keychain` (the same cookie-encryption key Playwright uses — otherwise the collector can't read the saved login). The user logs in and quits that Chrome with **Cmd+Q** (flushes cookies); the app then verifies the session headlessly.
- Before each run, `check_session` loads a logged-in page; if redirected to login → `needs_login`, source skipped, notification.
- Sources that need no login (API plugins, Microsoft) skip this.

### 3.7 Health: blocks, cooldowns, manual checks

| Signal | Action |
|---|---|
| Login redirect | stop source → `needs_login`, notify |
| Security check (CAPTCHA, Cloudflare interstitial, `/challenge` URL) | stop source → cooldown (24–48 h), screenshots to `failures/`, notify — **unless** `manual_checks` is on: bring window forward, notify, wait (≤ 5 min, ≤ 5 per run) for the user to complete it, then continue |
| HTTP 403 / 429 / 999 | stop source → cooldown |
| 404 / 410 | `fetch_failed`, no retry |
| Timeout / 5xx | retry ×2 with backoff; 3 consecutive failed cards → stop source |
| No cards on page 1 | `stop_reason: empty`, screenshot saved (stale selectors or bad search URL) |

Never evaded: no fingerprint spoofing, automation hiding, or simulated human input to pass checks.

### 3.8 Apply link

Found without clicking Apply. Direct employer URLs kept as-is; site-hosted redirect wrappers decoded from the query string or followed with plain HTTP (≤ 5 hops). `apply_kind`: `easy_apply | external | onsite | unknown`.

### 3.9 Interruption

Ctrl+C at any time (including during a pause): every saved posting is already committed; the batch is marked `interrupted` with per-source progress, the running search run gets `stop_reason: interrupted`. The next run continues (saved jobs are `known`, leftovers retried).

---

## 4. Raw data

Captured per posting (exactly what the site delivered; nothing interpreted except the card's job ID and text for filtering):

| Item | Browser sources | API sources |
|---|---|---|
| Page HTML | ✅ `detail.html` | — |
| Visible text | ✅ `page_text.txt` (inspection only) | — |
| Captured JSON (responses the page loaded) | ✅ `payloads.json` | — |
| Embedded JSON (JSON-LD, `__NEXT_DATA__`, JSON scripts) | ✅ `embedded.json` | — |
| API record | — | ✅ `record.json` |
| Card / list entry | ✅ `card.json` | ✅ (the record) |
| Fetch metadata (final URL, status, ready, apply link/kind) | ✅ `capture.json` | ✅ |
| Screenshot | optional (`--screenshots`) | — |

Current file layout (`data/raw/`, until the DB lands):
```
postings/<source>/<job_id>/   posting.json (commit marker; presence = known), card.json, capture.json,
                              detail.html, page_text.txt, payloads.json, embedded.json, screenshot.png
batches/<batch_id>/           batch.json, search_runs.jsonl, search_results.jsonl, cards/, failures/, collect.log
source_health.json
```
Measured on LinkedIn: ~400 KB per job with everything on (HTML 229 KB, screenshot 134 KB, text 12 KB).

---

## 5. Parse

Each plugin's `parse(raw: RawPosting) -> CommonPosting` turns saved raw data into common fields; `auto-app process parse` runs it over postings: `fetched` → `parsed` (`parsed.json` written next to the raw files) or `parse_failed` (reason recorded; raw kept for a fix + re-parse). Parsers never touch the network and are tested against saved real pages/records.

**Interface.** `RawPosting` gives a parser everything saved for one posting: `capture`, `card`, and — browser sources — `page_text`, `html`, `payloads`, `embedded`; API sources — `record`, `detail`; plus `company_hint` (a company source's `company` name from its plugin). Each plugin declares `parser_version`; a posting is re-parsed automatically when its parser's version changes (or with `--reparse`), and failed postings are retried every run. Browser plugins without their own parser get the default: the page's schema.org `JobPosting` (JSON-LD), which many career sites embed.

| Field | Notes |
|---|---|
| `title`, `company`, `locations[]` | as shown; company sources use their plugin's `company` name (Workday's legal entity, e.g. "2100 NVIDIA USA", goes to `extras.hiring_entity`) |
| `workplace_type` | onsite / hybrid / remote / unknown (Workday: `remoteType`, else the `#LI-Hybrid`-style tag in the description) |
| `employment_type` | full_time / part_time / contract / temporary / internship / unknown |
| `seniority` | if the source states it |
| `salary` | `{min, max, currency, period, text, source, plausible}` — `source`: header (LinkedIn), structured (Ashby/Lever/JSON-LD), or description (a pay line such as "base salary range is 116,000 USD - 184,000 USD"); values kept as shown, `plausible: false` flags e.g. "$90/yr" |
| `posted_at`, `posted_at_precision`, `posted_text` | exact (API timestamps) / day (Workday start date) / approx ("12 minutes ago") |
| `reposted`, `reposted_at` | LinkedIn: header line `· Reposted <time> ago ·` (not the "Repost" share button, not description text). Recorded only — Step 2 decides |
| `applicants`, `applicants_text` | LinkedIn ("Over 100 applicants" → 100) |
| `apply_url`, `apply_kind`, `url` | LinkedIn: resolved employer link when external; API: the platform's apply/job URL |
| `req_id` | Workday `jobReqId`, Greenhouse `requisition_id`, else a "Job Req ID: …" pattern |
| `description_text`, `description_html` | the job description only (LinkedIn: the "About the job" section, which holds the full text — "… more" is only the expand button) |
| `extras` | source-specific: LinkedIn `promoted`, `already_applied`, `responses_managed_off_linkedin`; Ashby department/team; Greenhouse departments/offices/metadata; Workday time type, requisition location |

**LinkedIn** is parsed from the page's visible text, anchored on stable text rather than LinkedIn's generated class names: company line, title line, `<location> · <posted> ago · <applicants>`, then salary / workplace / employment lines until the action buttons, and the description between "About the job" and the first end marker ("… more", "Set alert for similar jobs", "About the company").

`auto-app process parse` prints field coverage per source — the quickest check of what a parser gets right:
```
source       ok fail   title company location description posted workplace employment salary apply_url
linkedin     41    0    100%    100%     100%        100%   100%      100%       100%    46%       85%
nvidia        5    0    100%    100%     100%        100%   100%       20%       100%   100%      100%
```

---

## 6. Normalize

Computed from parsed fields → state `normalized`, written to `normalized.json` per posting (`src/auto_app/process/normalize.py`). Versioned (`NORMALIZER_VERSION`): postings are normalized again when the rules change or the posting was re-parsed. Cross-source dedup is only possible on normalized postings: each source presents the same job differently. `auto-app process normalize` prints, per source, the share of postings with an employer key and how each company was resolved. It works on each posting independently and never compares postings — finding duplicates (including shared employer keys) is dedup's job (`auto-app process dedup --preview`).

| Key | Rule | Sources |
|---|---|---|
| `company_norm` | lowercase, strip suffixes (inc, llc, corp…) and punctuation, then apply `company_aliases` (below) | LinkedIn: header/card; company sources: the config key |
| `title_norm` | lowercase; expand abbreviations (sr→senior, swe→software engineer, ml→machine learning); strip noise ("(Remote)", years, req numbers) | all |
| **employer job key** `(platform, board, job_id)` | one function, `employer_job_key(url)`, parses every supported platform's job URL (Workday `…/job/…_<id>`, Ashby `jobs.ashbyhq.com/<board>/<uuid>`, Greenhouse `…/<board>/jobs/<id>` or `gh_jid`, Lever `jobs.lever.co/<board>/<uuid>`); tracking params and `/apply`, `/application` suffixes ignored | LinkedIn: resolved external apply link (35 of 41 saved LinkedIn jobs have one); company sources: the posting's own URL |
| `req_id` | Workday `bulletFields`, Greenhouse `requisition_id`, else regex on description ("Job Req ID: 30258", "R-12345") | all |
| `location_norm` | `santa clara, ca` (also from Workday's `US, CA, Santa Clara`), `ca, us` (state + country), `us`, `remote` / `remote-us`; full state names become codes only when used as a state ("New York, NY" stays a city) — an **attribute**, never identity | all |
| cleaned description | the parsed description with boilerplate lines (EEO, accommodations, E-Verify, pay-transparency, `#LI-` tags, privacy) and URLs removed, lowercased, whitespace collapsed — for comparison only | all |
| `desc_hash`, `minhash`, `shingle_count` | SHA-1 of the cleaned description; 128-permutation MinHash over 5-word shingles (fixed seed, so signatures compare across runs) and the shingle count (for containment estimates when one text is purged) | all |

**Measured on the saved data:** employer keys for 100% of company postings and 24% of LinkedIn postings (the rest link to employer systems without a plugin — Oracle, SAP, iCIMS, Rippling…, or are Easy Apply) — and they already surfaced a real duplicate: two LinkedIn postings (`4473065722`, `4473077041`) → the same Workday job `R24265`. Description similarity separates cleanly: that pair scores 1.0 (identical after cleaning); among the other 1,034 pairs the highest is 0.40 and the median 0.0. The highest unrelated pairs are different NVIDIA jobs sharing NVIDIA's standard company intro — a calibration point for §7.6 (company-intro text raises same-company similarity).

**Company aliases** (`company_aliases`; learned ones in `data/raw/company_aliases.json` until the DB), from three places:
1. **Company plugins** — each company source's name and `company` (`nvidia`, "NVIDIA").
2. **Platform boards** — a posting whose employer job key points at a configured company's board (e.g. the `nvidia` Workday tenant) belongs to that company, whatever name LinkedIn shows ("NVIDIA Corporation").
3. **Learned** — every exact (employer-job-key) match records the other source's company string as an alias, which later helps match that company's Easy Apply postings.

---

## 7. Dedup

### 7.1 Principles
- **Posting ≠ job.** A posting is one listing on one source; a job is the real opening. Dedup links postings to jobs; Steps 2–3 work on jobs.
- **Job identity = company + title + description. Location is never part of identity** — the same role in several locations is one job, its locations listed in `job_locations`.
- **A different title is a different job.** If the poster titled two postings differently, they treat them as different openings (NVIDIA "ASIC Verification Engineer - New College Grad 2026" vs "… 2027": same description, separate requisitions per graduating class). Only cosmetic differences are normalized away (case, abbreviations, "(Remote)"-style noise, requisition numbers in titles); the same employer job key is the only override.
- **Nothing is deleted.** Duplicates remain postings linked to a job (`match_rule`, `match_confidence` recorded), so a wrong merge can be undone.
- **Company postings are the source of truth.** When a job has a company-source posting, it is the job's primary posting and gives the canonical apply URL; LinkedIn postings stay attached (and stay `known` for Level 1a).

### 7.2 Levels

| Level | When | Scope | Rule | Result | Status |
|---|---|---|---|---|---|
| **1a** | during collection, before fetching | same source, same ID | `(source, source_job_id)` saved | skip fetch (`known`) | ✅ |
| **1b** | after normalize | same source, **different ID** | same `company_norm` + `title_norm` + `desc_hash` (location ignored, description ≥ 300 chars), or same employer job key; veto: different employment type; the earliest posting is canonical | `duplicate_of` → canonical posting | ✅ |
| **2** | after 1b, canonical postings only | **across sources** (and same-source pairs 1b left apart) | rules in §7.3 | `job_id` (link or create) | ✅ |

"Same source, different ID" (Level 1b) covers: reposts under a new ID; the same role posted by the company and a recruiter/agency; per-location copies with the same description; Workday multi-location reqs (`JR2026584-1`, `-2`). It is deliberately strict (exact description hash): changed content goes to Level 2's similarity rules instead.

### 7.3 Level 2 rules
A posting is compared with each candidate job using the union of the job's keys (`job_keys`). Candidates come from indexed lookups on the employer job key, `company_norm` + `req_id`, `company_norm`, and MinHash LSH buckets.

| # | Rule | Confidence | Typical case |
|---|---|---|---|
| 1 | same employer job key | 1.0 | LinkedIn external-apply job ↔ the company's own posting |
| 2 | same `company_norm` + same `req_id` | 0.95 | multi-location Workday reqs; a LinkedIn link to another location of the same req |
| 3 | same `company_norm` + description containment ≥ 0.95 (titles equal — see veto) | 0.9 | LinkedIn Easy Apply copy; unresolved apply link |
| 4 | same `desc_hash` | 0.9 | identical text under different company names (agencies) |
| 5 | same company + title, one side has no description, posted within 14 days | 0.6 → **probable**, flagged for review | thin postings |
| **Veto** | different normalized title; different employment type (both known); both descriptions present and containment < 0.5 | blocks the match | "… Grad 2026" vs "… 2027"; same title, different team |

A shared employer job key overrides the vetoes (it is the same job id on the employer's own system — e.g. a LinkedIn copy that retitled it). Pairs with matching descriptions but different titles are listed as near misses ("… titles differ — different jobs"). Different employer job IDs or req IDs are **not** a veto (one req per location is common). Containment = |A∩B| / min(|A|,|B|) over 5-word shingles (MinHash estimate when one side's text is purged).

### 7.4 Grouping
1. **In-batch:** canonical postings of the batch are grouped (union-find), strongest links first, with vetoes checked whenever two groups would merge — against every member **and its Level 1b duplicates** — so A~B and B~C can't chain A and C together across a veto. A refused merge is reported as a near miss ("blocked: … — reason").
2. **Against the DB:** each group is matched to existing jobs, including purged ones (their `job_keys` are kept as tombstones).
3. **Link or create:** link to the best match, or create a job; add the group's keys to `job_keys`, locations to `job_locations`; set the primary posting (company source > external apply > Easy Apply; then longest description).
4. **Learn aliases** from exact matches (§6).

### 7.4.1 Incremental linking (implemented: `src/auto_app/process/link.py`)
`auto-app process dedup` links only **postings not yet linked to a job**; earlier links are sticky:
1. **Level 1b** — a new posting that is an exact same-source copy (same employer key, or same company + title + description hash) of an earlier posting, already linked or new, becomes its `duplicate_of` and joins that posting's job.
2. **Level 2** — the other new postings are grouped among themselves (rules §7.3, merge vetoes incl. Level 1b copies), then each group is compared with existing jobs through their postings (blocked by employer key, company, description hash). The group **joins the best-matching job** unless any of its postings is vetoed by any posting of that job (reported as a near miss: "match to job #N blocked: …"); otherwise it **becomes a new job**.
3. **Active jobs keep their ids and state** (a repost of an active recommended job attaches to it and keeps its Step 2 result and your decision). **Archived jobs are never candidates** (§7.5, §9). For every job that gained postings, the primary posting (company source > external apply > Easy Apply), canonical apply URL, company/title, first/last-seen dates, `job_keys` (employer key, company|req id, description hash, company|title fingerprint) and `job_locations` are updated.
4. Each posting records how it was linked: `match_rule` (`new_job`, `employer_key`, `req_id`, `description`, `desc_hash`, `probable`, `1b:employer_key`, `1b:desc_hash`) and `match_confidence`; state becomes `linked`.

`dedup --preview` runs the same plan without writing. `dedup --rebuild` unlinks everything and regroups from scratch — refused once any job has been evaluated by Step 2 (C11; today: once any status is beyond `collected`), so Step 2/3 decisions are never lost. `auto-app job list` lists jobs; `auto-app job list <id>` shows a job's postings (and how each was linked), keys and locations; `inspect <source> <id>` shows a posting's job.

### 7.5 Reposts, history and archived jobs
- Same ID → `known`, not re-fetched (optional: refresh when marked "Reposted" and last fetched > 14 days ago). This also holds for postings of **archived** jobs: the listing is not fetched again and the archived job **stays archived** (its posting's last-seen date is updated; the job is not revived).
- New ID → fetched once, then Level 1b or 2 attaches it to the matching **active** job, which keeps its state.
- **Archived jobs are excluded from linking** (Level 1b and Level 2). A new posting that would have matched one becomes a **new job** (and goes through Step 2 again) — but dedup still **checks** new jobs against archived jobs' kept matching keys (employer key, company + req id, description hash, company + title) and attaches a warning to the new job: *"looks like job #12, which you applied to on 2026-10-03"* / *"… which you declined on …"*. Steps 2–3 show the warning.
- Deleted jobs (TTL, §9) leave nothing behind: a listing seen again later is collected and evaluated as new.
- Whether reposts are wanted is a Step 2 matter: write it in your Step 2 filter's command ("skip reposted jobs") and add `reposted` to its `filter.yaml` `job_details`.

### 7.6 Rollout and calibration
1. **Parse** (C6) and **normalize** (C8a), with `employer_job_key()` tested on the real saved LinkedIn apply URLs and company postings.
2. **Preview** (C8b, ✅ `src/auto_app/process/dedup.py`): `auto-app process dedup --preview [--json FILE]` runs 1b + 2 on the current files and lists the jobs it would form — members, primary posting (★), each link's rule and confidence, locations — plus **near misses**: same-company pairs whose descriptions overlap between the veto and match thresholds, matching descriptions with differing titles, same titles blocked by a veto, and merges refused at grouping. Nothing is written. Candidates are compared only within blocks (same employer key, company, or description hash). On the current data: 46 postings → 45 jobs (the Intuitive Machines LinkedIn pair, via Level 1b), 0 near misses.
3. **Calibration data:** overlapping runs — a LinkedIn search that returns the tracked companies (NVIDIA, Adobe, UiPath) plus `collect --companies` with a wider posted-within window (e.g. 7 days). Tune the 0.95 / 0.5 containment thresholds on labeled groups.
4. **Database** (C7), then dedup writes `jobs`, `job_locations`, `job_keys` (C8c).

---

## 8. Storage

### 8.1 Stores

Every stage talks to one storage interface (`src/auto_app/collect/store.py`), addressing postings as `PostingRef(source, job_id)`. Two implementations:

| Store | Where | Role |
|---|---|---|
| **`DbStore`** (`core/db/store.py`) | `data/auto_app.db` (SQLite, WAL) | **the source of truth**, used by every command |
| **`FileStore`** | `data/raw/` (folders, JSON, HTML) | **file-only debug mode**: with `--debug-files` (or `AUTO_APP_DEBUG_FILES=true` in `.env`) every command uses the file store **instead of** the database — nothing is written to the database. Jobs live only in the database, so dedup can only preview (`run` previews its dedup step) and `jobs`, `purge`, `export`, `import-files` refuse. `export <source> <id>` writes one database posting as files; `import-files` loads a file store into the database |

Both implementations run the same tests (collection on the fake site, company collection, parse and normalize stages run once per store).

### 8.2 Database (`data/auto_app.db`)

Schema in `src/auto_app/core/db/tables.py`; migrations in `migrations/` (Alembic), applied automatically when the database is opened — all or nothing (one transaction, DDL included; foreign-key checks off while SQLite rebuilds tables, then `PRAGMA foreign_key_check`). New migration: `uv run alembic revision --autogenerate -m "…"`.

```sql
batches        (batch_id PK, started_at, finished_at, interrupted, config_hash, data JSON)   -- data = options + per-source summaries
search_runs    (search_run_id PK autoinc, batch_id FK, search_name, source, query JSON, search_url,
                started_at, finished_at, status, stop_reason, pages_loaded, outcomes JSON)
search_results (id PK, search_run_id FK, batch_id, search_name, source, source_job_id, page, rank, is_promoted,
                title, url, card JSON, card_html BLOB(zstd), outcome, outcome_detail, seen_at)
                -- indexes (source, source_job_id), (source, outcome, seen_at): retry list, rate history
source_health  (source PK, status, data JSON)   -- cooldown_until, last_block_*, last_ok_at, last_run_at

postings (
  posting_id PK, source, source_job_id, source_url, kind (browser|api), platform, board, batch_id, first_search_run_id,
  state (fetched → parsed | parse_failed → normalized), parser, parsed_at, parse_error, first_seen_at, last_seen_at,
  parsed JSON (CommonPosting) + title, company, posted_at, apply_url, apply_kind,
  normalized JSON + company_norm, title_norm, employer_key, req_id, desc_hash      -- indexed keys
  duplicate_of FK, job_id FK, match_rule, match_confidence, purged                 -- C8c
  UNIQUE (source, source_job_id)                                                    -- Level 1a: one indexed lookup
)
posting_raw (posting_id PK FK, fetched_at, capture JSON, card JSON, page_text, detail_html, payloads, embedded,
             record, detail  -- BLOBs, zstd-compressed; screenshot_path)             -- purged ~7 days after parse (C9)

jobs          (job_id PK, primary_posting_id FK, company_norm, title_norm, canonical_url, first/last_seen_at,
               step2 (NULL | recommended | un_recommended), step2_at,               -- C11: replaces `status`
               decision (NULL | applied | not_applied), decided_at,
               expires_at,                       -- last_seen_at + the TTL of the job's state
               archived_at, archive JSON,        -- C11: compact record once archived (§9.2)
               archive_description BLOB(zstd),   -- applied jobs only
               warnings JSON)                    -- C11: e.g. "looks like job #12 (…), which you applied to on …"
job_locations (job_id FK, location_norm, workplace_type, apply_url, posting_id FK)  -- C8c
job_keys      (key_type, key_value, job_id FK)   -- C8c; never purged (tombstones)
company_aliases (alias_norm PK, company_norm, origin)   -- learned aliases
```

### 8.3 What stays on disk
- Screenshots: `data/screenshots/<source>/<id>.png` (the database stores the path).
- Run logs: `data/logs/<batch_id>.log`; block/CAPTCHA artifacts: `data/failures/<batch_id>/<source>/`.
- Browser profiles, config.
- The file store under `data/raw/` from `--debug-files` runs.

### 8.4 Moving data
- `auto-app data import [--from DIR]`: batches (runs, sightings, card HTML, logs, failure artifacts), postings (raw, parsed, normalized, screenshot paths), source health and learned aliases. Idempotent — what's already in the database is skipped. Search-run ids are renumbered (the database's are global) and postings' first-run references are remapped.
- `auto-app data export <source> <job_id> [--to DIR]`: one posting, in the file store's layout.

---

## 9. Retention (TTL)

Two kinds of expiry, both applied by `auto-app data purge` (and `run --purge`, the schedule): a **job TTL** that deletes or archives whole jobs, and **posting-data retention** that drops heavy data from live postings.

### 9.1 Job TTL (C11, implemented: `src/auto_app/core/db/job_ttl.py`)

One global setting; the clock is the time since **any** of the job's postings was last seen (each purge first recomputes every job's `last_seen_at` from its postings — postings' dates are refreshed whenever collect sees them, including as `known`):

```bash
# .env (global settings; template .env.example)
AUTO_APP_JOBS__TTL_DAYS__UNFILTERED=14        # not evaluated by Step 2 yet → deleted
AUTO_APP_JOBS__TTL_DAYS__UN_RECOMMENDED=7     # Step 2 left it out → deleted
AUTO_APP_JOBS__TTL_DAYS__RECOMMENDED=30       # undecided, applied or not applied → archived
```

| State (README §3) | After its TTL |
|---|---|
| not evaluated, un_recommended | **deleted**: the job, its `job_keys`, `job_locations`, and **every linked posting** (incl. Level 1b duplicates) with its raw data and screenshot — in one transaction |
| recommended (undecided, applied, not applied) | **archived** (9.2) |

- `expires_at` (= `last_seen_at` + the state's TTL) is kept on every active job; `auto-app job list` shows "in N d" (`due` = the next purge acts on it) and flags recommended-undecided jobs (`!`) 3 days before they're archived.
- Until Steps 2–3 exist, `auto-app job mark JOB… --as STATE` sets the state by hand (`applied` / `not_applied` also mark the job recommended).
- `dedup` only refreshes `last_seen_at` / `expires_at`; deleting and archiving happen in `purge`, and `purge --dry-run` lists the jobs it would delete or archive.
- Caveat: "not seen" isn't always "closed". Company sources with `posted_within_days: all` re-list every open job each run; LinkedIn searches only show recent postings and stop early, so a still-open LinkedIn job can stop being seen and expire.

### 9.2 Archived jobs

An archived job is a **record**: kept, hidden from `auto-app job list` by default (`--archived` lists them), and **excluded from dedup** (§7.5). Only what's needed is kept (≈ 1 KB per job; a few KB with a description):

| Kept | Dropped |
|---|---|
| Job summary: title, company, locations, canonical apply URL, first/last seen, archived date | raw pages / records / captured JSON, screenshots, card data |
| Step 2 result: recommended, score, one-line reason | full parsed and normalized records, MinHash signature |
| Your decision and its date; for applied jobs, the link to the application record (CV version, answers, submission) | `job_locations` rows (folded into the summary) |
| Posting identity: source, source job id, URL, title, first/last seen (so collect still skips the same listing) | description text of undecided / not-applied jobs |
| Matching keys: employer key, company + req id, description hash, company + title — for repost warnings (§7.5) | |
| **Description text for applied jobs** (compressed) — listings disappear online; needed for interviews | |

### 9.3 Posting-data retention (C9, implemented)

`src/auto_app/core/db/purge.py`, settings `AUTO_APP_RETENTION__*` in `.env` (global settings moved there 2026-10-03). (The earlier `description_days` rule was retired by C11 — the job TTL deletes or archives jobs instead; old configs that still set it load and ignore it.)

| Data | Rule (default) | After |
|---|---|---|
| Raw pages / records / captured JSON, screenshots | `raw_days: 7` after fetch, **only once parsed** (failed parses keep raw data for a fix) | parse skips them and keeps the earlier result; `inspect` shows the parsed and normalized data |
| Card sightings (`search_results`) | `sightings_days: 30` | retry list and rate history only look back days, not weeks |
| Run logs, block artifacts | `logs_days: 30` | — |

Files from `--debug-files` runs (`data/raw/`) are not purged (purge needs the database). Storage-light defaults: screenshots (`--screenshots`) and card HTML (`--card-html`) are off unless asked for; page text stays (the LinkedIn parser reads it), compressed.

---

## 10. CLI

Grouped commands (2026-10-03): `auto-app <group> <command>`; a group alone shows its help.

```
auto-app source list | show <SOURCE|PLATFORM>                 # sources (one plugin each), platforms, search options
auto-app source add <url> [--name NAME] [--company TEXT]      # writes plugins/companies/NAME.py + a config stub
auto-app source login <source>                                # plain Chrome; quit with Cmd+Q to save
auto-app source health [--reset SOURCE]                       # ok / needs_login / cooldown
auto-app source plan [-s SOURCE]… [--job-sites|--companies] [--search NAME]…   # what collect would run, per field
auto-app collect run [-s SOURCE]… [--job-sites | --companies] [--search NAME]…
                     [--max-pages N] [--cards-only] [--ignore-known]
                     [--interactive] [--manual-checks] [--capture-all-json] [--headless] [--force] [--scheduled]
auto-app collect probe <source> <job id | URL>                # fetch one job, show what was captured
auto-app collect stats [BATCH]                                # search runs, outcomes, pauses
auto-app process parse [-s SOURCE]… [--reparse]               # field coverage per source
auto-app process normalize [-s SOURCE]… [--again]             # each posting's dedup keys
auto-app process dedup [--preview] [--rebuild] [--no-near-misses]   # link new postings to jobs; --preview = dry run
auto-app run [-s SOURCE]… [--job-sites|--companies] [--no-collect] [--purge] [--scheduled] [--headless]
                                                              # collect → parse → normalize → dedup (→ purge); one at a time
auto-app schedule install --at HH:MM… [--headless] [--dry-run] | show | remove   # macOS launchd: `run --scheduled --purge`
auto-app job list [-s SOURCE] [-c TEXT] [--state S] [--archived] [--limit N]    # jobs, newest first
auto-app job show <JOB_ID>                                    # one job in detail
auto-app job mark <JOB_ID>... --as recommended|un_recommended|applied|not_applied|not_evaluated
auto-app data inspect [SOURCE [JOB_ID]]                       # saved postings
auto-app data export <source> <job_id> [--to DIR]             # one posting from the database as files
auto-app data import [--from DIR]                             # load a file store (data/raw/) into the database
auto-app data purge [--dry-run] [--vacuum]                    # job TTL (delete / archive), then posting-data retention (§9)
auto-app --debug-files <group> <command> …                    # files under data/raw/ instead of the database
# planned
auto-app source new-plugin <name> --kind api|browser
```

---

## 11. Milestones

| # | Deliverable | State |
|---|---|---|
| C1 | Collection engine + file store + LinkedIn | ✅ |
| C2 | Rate pauses, retry cap, manual checks, clean interruption | ✅ |
| C3 | Plugin interface + discovery; filter capabilities + `plan`; new config layout (`job_sites` / `companies`) with automatic migration; move LinkedIn/Handshake/Jobright to it | ✅ |
| C4 | API platform plugins: Ashby (UiPath), Greenhouse, Lever, Workday (NVIDIA, Adobe); `add-company` | ✅ |
| C5 | Further plugins (e.g. Microsoft careers, browser) | left to whoever needs them — the interface (§2.2) is the deliverable |
| C6 | Parse: LinkedIn + API platforms (incl. `reposted`) | ✅ |
| C8a | Normalize, incl. `employer_job_key()` and company aliases | ✅ |
| C8b | `dedup --preview` on files; calibration | ✅ preview · calibration pending overlapping data |
| C7 | Database (`DbStore`, Alembic, `import-files`) | ✅ |
| C8c | Dedup writes jobs (L1b, L2) | ✅ |
| C9 | Purge; storage-light defaults (no screenshots/card HTML by default) | ✅ |
| C10 | `run` + scheduling (launchd) | ✅ |
| C12 | One plugin = one source: platform bases in `src/auto_app/platforms/`, one plugin file per company (`add-company` writes it), config `sources.<plugin>` with named searches + `from:` + limits, load-time plugin checks, `-s` for every source; migration of the `job_sites` / `companies` layout | ✅ |
| C11 | Job state model (`step2` + `decision`, migration from `status`), job TTL (delete / archive), archived-job records, repost warnings, `mark`; atomic migrations | ✅ |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| LinkedIn account restriction | real session, strict hourly/daily limits, stop + cooldown on first signal |
| Site layout changes | selectors per plugin, `ready`/empty-page signals, fixtures, raw kept for re-parse |
| Bot checks (Jobright) | stop or hand to user; never evade; prefer API platforms |
| Over-merging generic titles at big companies (location isn't a separator) | boilerplate removal, containment veto, calibration on real data |
| Plugin bugs | engine owns storage/pacing; plugins are site knowledge only; offline tests |

---

## 13. Open questions

1. ~~Company list~~ — UiPath (Ashby), NVIDIA, Adobe (Workday) as plugins in `plugins/companies/`; Microsoft needs C5. More companies: `add-company`.
2. API local filter: keyword match on title only, or title + description?
3. Reopened roles: a job you applied to reappearing after > 90 days — stay attached or surface as new?
4. Refresh "Reposted" LinkedIn jobs after 14 days?
6. Archived job whose same listing (same source id) is seen again: stays archived (decided default, §7.5) — revisit if it hides reopened roles.
5. Handshake: verify live, or drop for now (LinkedIn-only focus)?
