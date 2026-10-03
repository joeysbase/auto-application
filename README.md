# auto-application

A personal pipeline that finds job postings, picks the ones worth applying to, and prepares applications. Design: [`docs/README.md`](docs/README.md) (overview) and one doc per step. Progress log: [`docs/updates/`](docs/updates/README.md).

**Implemented: Step 1 — Collect.** Postings from **job sites** (LinkedIn, verified) and **company career sites** (one plugin per company on the Ashby, Greenhouse, Lever and Workday platforms — verified on UiPath, NVIDIA, Adobe, Palantir) are collected, parsed into common fields, normalized, and deduplicated into **jobs**, stored in a local SQLite database. Steps 2 (select) and 3 (apply) are designed, not built.

---

## Contents
- [Setup](#setup)
- [Key terms](#key-terms)
- [Configuration](#configuration)
- [Common workflows](#common-workflows)
- [Command reference](#command-reference)
  - [Global options](#global-options)
  - [Sources and setup](#sources-and-setup) — `login`, `sources`, `plugins`, `add-company`, `plan`
  - [Collecting](#collecting) — `collect`, `probe`, `stats`
  - [Processing](#processing) — `parse`, `normalize`, `dedup`, `jobs`, `mark`
  - [Pipeline, schedule, retention](#pipeline-schedule-retention) — `run`, `schedule`, `purge`
  - [Looking at data](#looking-at-data) — `inspect`, `export`, `import-files`
- [Where the data goes](#where-the-data-goes)
- [Safety](#safety)
- [Adding a source plugin](#adding-a-source-plugin)
- [Tests](#tests)

---

## Setup

```bash
uv sync
cp config/searches.example.yaml config/searches.yaml   # then edit (git-ignored)
uv run auto-app --help
```

Job sites use your installed **Google Chrome** (fallback: `uv run playwright install chromium`). Company sources on API platforms need no browser. Everything — config, browser logins, the database — stays inside the project folder and is git-ignored.

## Key terms

| Term | Meaning |
|---|---|
| **Source** | Where postings come from. **Each source is one plugin**, named by its plugin name (`linkedin`, `nvidia`, …) — the name used in the config, in storage and with `-s` |
| **Job site** | A source with many employers: LinkedIn (verified), Handshake (unverified), Jobright (disabled — bot check). Built in |
| **Company source** | A source with one employer: a small plugin file in `plugins/companies/` (e.g. `nvidia.py`) that names the company and its board on a **platform** (Ashby, Greenhouse, Lever, Workday — the platform code is shared). `add-company` writes it |
| **Batch** | One `collect` (or `run`) invocation |
| **Search** | A named search under a source in the config (`sources.<source>.searches`); can copy another source's search with `from: <source>/<search>` |
| **Search run** | One search on one source within a batch |
| **Sighting** | One card / list entry a search run saw, with its outcome: `fetched`, `known`, `filtered`, `budget_skipped`, `fetch_failed`, `cards_only` |
| **Posting** | One listing on one source, saved once. States: `fetched` → `parsed` (or `parse_failed`) → `normalized` → `linked` |
| **Job** | The real opening; one or more postings (from any sources, any locations) linked by dedup |
| **Job state** | Step 2's verdict (`recommended` / `un_recommended`) and your decision (`applied` / `not_applied`); a new job is *not evaluated*. Set by Steps 2–3 later, by hand now with `auto-app mark` |
| **Job TTL** | A job expires `jobs.ttl_days` days after its postings were last seen: not-evaluated / un-recommended jobs are **deleted** (with their postings), recommended ones **archived** (kept as a record, left out of dedup) |

## Configuration

`config/searches.yaml` (layout: [`docs/step1-collect.md` §2.4](docs/step1-collect.md#24-configuration); template: `config/searches.example.yaml`) says **what to search on each source**. Which board a company source reads and its display name are in its plugin file, not here.

```yaml
defaults:                       # shared by every search
  posted_within_days: 1         # days back; "all" = no date limit (a search can override either way)
  card_filters:
    exclude_title_words: [senior, staff, principal, manager, director]   # skipped before any page is opened

sources:                        # one entry per source (plugin name); a source with no searches doesn't run
  linkedin:
    searches:
      - name: ml-engineer-us    # unique within the source
        keywords: ["machine learning engineer", "applied scientist"]
        locations: ["United States"]
        workplace_types: [remote, hybrid, onsite]
        employment_types: [full_time]
        experience_levels: [entry]          # internship | entry | associate | mid_senior | director | executive
        # site_url: "https://www.linkedin.com/jobs/search-results/?…"   # optional: a search URL copied from the browser
    limits: { max_details_per_hour: 40, max_details_per_day: 300 }

  uipath:                       # plugins/companies/uipath.py
    searches:
      - from: linkedin/ml-engineer-us     # copy that search's fields; the name defaults to the copied one's

  nvidia:                       # plugins/companies/nvidia.py
    searches:
      - name: new-grad
        locations: ["United States"]
        employment_types: [full_time]
        posted_within_days: all
        site_filters:           # this source's own options: `auto-app plugins nvidia`
          facets: {"Job Type": ["New College Graduate"], "Job Category": ["Engineering", "Research"]}

jobs:
  ttl_days: { unfiltered: 14, un_recommended: 7, recommended: 30 }   # job TTL (days unseen): delete / delete / archive
retention: { raw_days: 7, sightings_days: 30, logs_days: 30 }
debug_files: false              # true = write files under data/raw/ instead of the database (like --debug-files)
```

- **Inheritance:** a field left out comes from the search named in `from` (if any), else from `defaults`. `from` copies only the generic fields (keywords, locations, dates, types, levels, pages, card filters) — never `site_url` or `site_filters`. Setting a field overrides the copy; an empty list (`experience_levels: []`) clears it.
- **Empty vs. all:** no `keywords` means no keyword filter (every job matching the other filters), while no `posted_within_days` means the default, not "no limit" — write `posted_within_days: all` for that. On LinkedIn, `all` drops the "date posted" filter; on company boards, every listed job passes the date check.
- **Checked against the plugin:** a field the source can't apply is an error when written in the search itself (remove it — and state it in your Step 2 command if it matters) and a notice when copied with `from`; unknown `site_filters` are errors.
- **LinkedIn searches** run on LinkedIn's newer search page (the one the search bar on linkedin.com/jobs opens), using its **own filters**, never extra keywords:
  - generic fields map onto them when LinkedIn has an exact equivalent: `employment_types` (full_time, part_time, contract, internship), `experience_levels` (entry, director, executive), `workplace_types: [remote]`, `posted_within_days` (rounded up to LinkedIn's 24 hours / week / month), `locations: ["United States"]`;
  - anything else (associate, mid_senior, temporary, hybrid, on-site …) is **not filtered, with a notice in `plan`** — the whole field, so LinkedIn never filters out values you asked for; state it in your Step 2 command if it matters;
  - `site_filters` sets LinkedIn's filter buttons directly and replaces the generic mapping: `employment_type` (full_time | part_time | contract | internship | volunteer), `experience_level` (LinkedIn's own levels: entry_level | senior | manager | director | executive), `remote`, `easy_apply`, `under_10_applicants`, `in_my_network`, `geo_id` (a location id copied from LinkedIn's URL — needed for places other than the United States);
  - after the exact matches LinkedIn lists "related" jobs that ignore the filters ("…may not be exact matches"); the tool stops reading at that note. Results are ordered by relevance, so there's no early stop: `max_pages` sets the depth.
- **`site_url`** (browser sources) replaces the generated search with a URL copied from your browser; its own filters then apply, not the search's generic fields. For LinkedIn, both the newer `/jobs/search-results/` page and the classic `/jobs/search/` page work (the classic one honours strict filter parameters like `f_E=2` and `sortBy=DD`).
- **Older layouts** (`job_sites:` + `companies:`) still load; a company without a plugin file gets an error with the `add-company` command that creates it.

`auto-app plan` shows what this config will run and where each field is applied (by the site, locally, or not at all — deferred); `auto-app plugins <source>` lists what a source supports.

## Common workflows

**First time**
```bash
uv run auto-app login linkedin             # log in once in a normal Chrome window; quit it with Cmd+Q
uv run auto-app add-company https://jobs.ashbyhq.com/uipath   # writes plugins/companies/uipath.py + a config stub
uv run auto-app plan                        # check what will run
uv run auto-app run --max-pages 1           # a small first run: collect → parse → normalize → dedup
uv run auto-app jobs                        # the result
```

**Every day**
```bash
uv run auto-app run --purge                 # or let the schedule do it:
uv run auto-app schedule install --at 09:00 --at 17:00
```

**Checking a new site or company**
```bash
uv run auto-app collect -s linkedin --cards-only --max-pages 1   # are cards read correctly? (no job pages opened)
uv run auto-app probe linkedin "https://www.linkedin.com/jobs/view/<id>/"   # one job page, what was captured
uv run auto-app collect -s nvidia && uv run auto-app parse -s nvidia        # coverage per field
uv run auto-app inspect nvidia <id>                                         # raw → parsed → normalized
```

**Debugging a source with plain files (no database)**
```bash
uv run auto-app --debug-files collect -s nvidia     # writes data/raw/postings/nvidia/<id>/… instead of the database
uv run auto-app --debug-files parse && uv run auto-app --debug-files normalize
uv run auto-app --debug-files dedup --preview       # jobs need the database, so only a preview
uv run auto-app import-files                        # later: load the files into the database
```

**Step by step instead of `run`**
```bash
uv run auto-app collect
uv run auto-app parse
uv run auto-app normalize
uv run auto-app dedup --preview && uv run auto-app dedup
```

---

## Command reference

All commands: `uv run auto-app <command> [options]`. Every command has `--help`.

### Global options

Given before the command, e.g. `uv run auto-app --debug-files collect`.

| Option | Effect |
|---|---|
| `-v`, `--verbose` | debug logging |
| `--debug-files` | **file-only mode**: postings, batches, parse and normalize results are written as browsable files under `data/raw/` **instead of** the database (same as `debug_files: true` in the config). Nothing touches the database. Jobs need the database, so in this mode `dedup` can only `--preview` (`run` previews its dedup step), and `jobs`, `purge`, `export` and `import-files` stop with a message. Load the files into the database later with `auto-app import-files` |

Most commands also accept `--config PATH` (default `config/searches.yaml`) and `--db PATH` (default `data/auto_app.db`).

---

### Sources and setup

#### `login SOURCE`
Opens a **normal, non-automated Chrome** on the source's saved profile (`browser-profiles/<source>/`) so you can sign in — Google / school SSO work here. Log in, then **quit that Chrome with Cmd+Q** (that's what saves the session); the session is then checked and the page it landed on is printed.

| Option | |
|---|---|
| `--no-verify` | skip the session check afterwards |

```bash
uv run auto-app login linkedin
```
Output: `linkedin session: ok (checked https://www.linkedin.com/feed/)`. Re-run when a site logs you out (collect stops that site with `needs_login` and notifies you).

#### `sources`
Health of each source: `ok`, `needs_login`, or `cooldown` (after a block, until a time), with the last reason and last successful run.

| Option | |
|---|---|
| `--reset SOURCE` | clear a cooldown / login status by hand |

```bash
uv run auto-app sources
uv run auto-app sources --reset linkedin
```

#### `plugins [NAME]`
Lists the sources — one plugin each: built-in job sites and the files in `plugins/` (company sources in `plugins/companies/`) — with their kind, company and platform, plus the platforms available for new companies. Plugin files that failed to load (import error, bad board, duplicate name) are listed with the reason. With a name (a source, or a platform such as `workday`): its company and board, which search fields it supports (native / local / deferred) and its `site_filters`.

```bash
uv run auto-app plugins
uv run auto-app plugins nvidia
```

#### `add-company URL`
Detects a company's job platform from a careers page or job-board URL and test-calls it (prints the number of open jobs). Then it writes the company's plugin, `plugins/companies/<name>.py` (a few lines: name, company, board on the platform base; commit it with your code and edit it for anything company-specific), and adds a commented search stub under `sources: <name>:` in your config. The source runs once you add a search there.

| Option | |
|---|---|
| `--name NAME` (alias `--key`) | source name (default: from the board) |
| `--company TEXT` | the employer's display name (default: from the name) |

```bash
uv run auto-app add-company https://jobs.lever.co/palantir --company Palantir
uv run auto-app add-company https://adobe.wd5.myworkdayjobs.com/external_experienced --name adobe --company Adobe
```
Marketing careers pages that load jobs with JavaScript (e.g. careers.adobe.com) can't be detected — open any job's *Apply* page and pass that URL.

#### `plan`
Shows what `collect` would run: every source, its searches, and for each search field whether it is applied **natively** (by the site), **locally** (on what the site returns) or **deferred** (not filtered — state it in your Step 2 command if it matters); warns about fields a source can't apply. Config errors (unknown fields or sources, bad `site_filters`, bad `from`, a selected source with no searches) are reported here.

| Option | |
|---|---|
| `-s/--source NAME`, `--job-sites`, `--companies`, `--search NAME` | same selection as `collect` |

```bash
uv run auto-app plan
uv run auto-app plan -s nvidia
```

---

### Collecting

#### `collect`
Runs the searches and saves new postings **raw**. For each card: card filters → already saved? (`known`, skipped without opening) → daily ceiling → hourly limit (rate pause) → open the job page / fetch the record → save. Searches stop early after a run of known cards. Starts with a retry pass for previously failed / skipped jobs. Prints the plan first and a batch summary at the end.

| Option | Effect |
|---|---|
| `-s/--source NAME` (repeatable; alias `--site`) | only these sources (any plugin name: `linkedin`, `nvidia` …) |
| `-c/--company NAME` | same as `-s` (kept for old scripts) |
| `--job-sites` / `--companies` | only job sites / only company sources |
| `--search NAME` (repeatable) | only searches with this name |
| `--max-pages N` | cap result pages per job-site search |
| `--cards-only` | read cards and record sightings, open no job pages |
| `--ignore-known` | re-fetch jobs already saved |
| `--interactive` | pause after opening each search so you can set filters in the browser, then collect from that page |
| `--manual-checks` | if a site shows a security check, bring the window forward and wait for you to complete it (headed only) |
| `--capture-all-json` | keep every JSON response a site loads (for investigating a site) |
| `--screenshots`, `--card-html` | keep a screenshot per job page / each card's HTML (off by default) |
| `--no-retry` | skip the retry pass |
| `--headless` | no browser windows |
| `--force` | ignore cooldowns |
| `--scheduled` | enforce `run_window` and `min_hours_between_runs` (for automation) |

```bash
uv run auto-app collect                                # everything in the config
uv run auto-app collect -s linkedin --max-pages 2
uv run auto-app collect --companies
uv run auto-app collect -s linkedin --search nvidia-check
```
How much is fetched: depth is `max_pages`; pacing is `max_details_per_hour` (pause, then continue) and `max_details_per_day` (stop; the rest become `budget_skipped`, fetched first next run). Stop any time with Ctrl+C — everything saved is kept.

#### `probe SOURCE JOB`
Fetches **one** job page (browser sources) by id or URL, overwriting any saved copy, and prints what was captured. Not rate-limited; takes a screenshot by default.

| Option | |
|---|---|
| `--no-screenshots`, `--capture-all-json` | |

```bash
uv run auto-app probe linkedin 4471589084
uv run auto-app probe linkedin "https://www.linkedin.com/jobs/view/4471589084/"
```

#### `stats [BATCH]`
Summary of a batch (default: the latest): per source — status, pages loaded, job pages fetched, outcomes, stop reasons, rate pauses, errors; per search run — stop reason, pages, outcomes (`listed` / `not_matching` for companies).

```bash
uv run auto-app stats
uv run auto-app stats 20261001-105917
```

---

### Processing

#### `parse`
Turns raw postings into common fields (title, company, locations, workplace / employment type, salary, posted date, reposted, applicants, apply URL, req id, description …) with each source's plugin parser. Only new postings and those whose parser version changed are parsed; failures are recorded (`parse_failed`) and retried. Prints field coverage per source — the quickest check of what a parser gets right.

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only these sources |
| `--reparse` | parse everything again |

```bash
uv run auto-app parse
uv run auto-app parse -s linkedin --reparse
```

#### `normalize`
Computes each posting's dedup keys: normalized company (via the company source, employer board, or aliases) and title, **employer job key** (the same job on Workday / Ashby / Greenhouse / Lever, from LinkedIn's apply link or the company posting's URL), req id, normalized locations, a description hash and MinHash signature. Works on each posting independently. Prints, per source, the share with an employer key and how companies were resolved.

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only these sources |
| `--again` | normalize everything again |

```bash
uv run auto-app normalize
```

#### `dedup`
Links new normalized postings to **jobs**, incrementally:
1. **Level 1b** — exact same-source copies (reposts, recruiter copies, per-location copies) become duplicates of the earlier posting.
2. **Level 2** — the rest are grouped across sources (same employer key → same company + req id → same company + description overlap ≥ 0.95 → identical description → probable), with vetoes (**different title** — the poster treats differently titled postings as different jobs; different employment type; descriptions overlapping < 0.5; a shared employer key overrides them); each group joins the best-matching existing job, unless a veto with any of its postings blocks it, or becomes a new job.

Existing jobs keep their ids and state; every active job's last-seen and expiry dates are refreshed. Archived jobs take no part; a new job that shares a matching key with an archived job you acted on gets a **repost warning** (⚠ in `jobs`). Prints new jobs with several postings, postings joining existing jobs, and **near misses** (pairs that looked related but weren't merged — for calibration).

| Option | Effect |
|---|---|
| `--preview` | dry run: show what dedup would do, write nothing |
| `--rebuild` | unlink everything and regroup from scratch — refused once any job is evaluated, decided or archived |
| `--no-near-misses`, `--limit N` | output control |

```bash
uv run auto-app dedup --preview
uv run auto-app dedup
```

#### `jobs [JOB_ID]`
Lists active jobs, newest posting date first, sized to your terminal:

- one row per job: number, posted date, company (as the posting shows it), title, where (`Austin, TX +2`), the sources it was found on (`linkedin, nvidia`), and when it **expires** (`in 12d`; `due` = the next purge deletes or archives it; `!` = recommended and undecided, archived within 3 days);
- a state column appears once jobs are in different states; ⚠ marks a repost warning;
- the summary line counts the jobs found on more than one source.

With a job number, it shows that job:

- company, every location, posted date, first and last seen, apply link, state (with Step 2's and your dates), when it expires and what happens then, and any repost warning;
- each posting (the same opening on each source) with how it joined the job, in words: "first posting of this job", "same job id on the employer's system (1.00)", "repeat of linkedin/… on the same source" …; a posting titled differently from the job shows its title;
- its matching keys (how later postings find this job);
- for an archived job you applied to: the job description kept with the record.

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only jobs found on this source |
| `-c/--company TEXT` | only jobs whose company contains this text |
| `--state S` | `not_evaluated`, `un_recommended`, `recommended` (undecided), `applied` or `not_applied` |
| `--archived` | archived job records instead of active jobs |
| `--limit N` | only the newest N jobs |

```bash
uv run auto-app jobs
uv run auto-app jobs -s nvidia --limit 20
uv run auto-app jobs --state applied
uv run auto-app jobs --archived
uv run auto-app jobs 28
```

#### `mark JOB_ID... --as STATE`
Sets jobs' state by hand — what Steps 2 and 3 will do once they exist: `recommended`, `un_recommended`, `applied`, `not_applied` (both also mark the job recommended) or `not_evaluated` (clears it). The state decides the job's TTL (`jobs.ttl_days`), so e.g. marking a job `applied` keeps it 30 days after it was last seen and then archives it instead of deleting it. Archived jobs can't be changed.

```bash
uv run auto-app mark 28 --as applied
uv run auto-app mark 12 13 14 --as un_recommended
```
Output: one line per job with its new state and expiry date.

---

### Pipeline, schedule, retention

#### `run`
The whole of Step 1 in one command: **collect → parse → normalize → dedup** (→ purge). Prints a section per stage and any problems (blocked sources, parse failures). Only one run at a time (`data/run.lock`).

| Option | Effect |
|---|---|
| `-s`, `--job-sites`, `--companies`, `--search`, `--max-pages`, `--headless`, `--force`, `--manual-checks` | as for `collect` |
| `--no-collect` | skip collecting; only process what's saved |
| `--purge` | apply retention at the end |
| `--scheduled` | respect `run_window` / `min_hours_between_runs` and send a notification when done (used by the schedule) |

```bash
uv run auto-app run
uv run auto-app run --companies --purge
uv run auto-app run --no-collect
```

#### `schedule install | show | remove`
A macOS launchd agent that runs `auto-app run --scheduled --purge` daily at the given times, logging to `data/logs/scheduled.log` (`~/Library/LaunchAgents/com.auto-app.run.plist`).

| `install` option | |
|---|---|
| `--at HH:MM` (repeatable, default 09:00) | run times |
| `--headless` | no browser windows (default: visible — safest for LinkedIn) |
| `--dry-run` | print the plist, install nothing |

```bash
uv run auto-app schedule install --at 09:00 --at 17:00 --dry-run
uv run auto-app schedule install --at 09:00 --at 17:00
uv run auto-app schedule show
uv run auto-app schedule remove
```

#### `purge`
Two passes:

1. **Job TTL** (`jobs.ttl_days`; the clock is the time since any of the job's postings was last seen — refreshed whenever collect sees them, including as `known`):
   - not evaluated (14 days) and un-recommended (7 days) jobs are **deleted** together with every linked posting, its raw data and screenshot — a listing seen again later is collected as new;
   - recommended jobs — undecided, applied or not applied (30 days) — are **archived**: kept as a compact record (summary, Step 2 result, your decision, posting identities, matching keys; the description for jobs you applied to), hidden from `jobs` (see `--archived`) and left out of dedup. Their postings stay as identities, so collect still skips those listings.
2. **Posting-data retention** (`retention:`): raw pages / records / screenshots 7 days after a successful parse; sightings, logs and block artifacts after 30 days. Live postings keep their identity, parsed fields and dedup keys.

`--dry-run` lists the jobs it would delete or archive (number, state, last seen, company, title).

| Option | |
|---|---|
| `--dry-run` | show what would be removed |
| `--vacuum` | compact the database file afterwards |

```bash
uv run auto-app purge --dry-run
uv run auto-app purge --vacuum
```

---

### Looking at data

#### `inspect [SOURCE [JOB_ID]]`
Without arguments: a table of saved postings (state, readiness, size, structured data found, apply kind, title). With a source: only that source. With a source and job id: one posting in full — capture details, its job, the **parsed** fields, the **normalized** keys, and the description.

| Option | |
|---|---|
| `--chars N` | how much description / page text to print (default 1200) |

```bash
uv run auto-app inspect
uv run auto-app inspect linkedin
uv run auto-app inspect nvidia JR2026576
```

#### `export SOURCE JOB_ID`
Writes one posting from the database as files (raw page or record, `parsed.json`, `normalized.json`, screenshot) for a closer look.

| Option | |
|---|---|
| `--to DIR` | destination (default `data/export/`) |

```bash
uv run auto-app export linkedin 4445417609
```

#### `import-files`
Loads a file store (`data/raw/`, e.g. from `--debug-files` runs) into the database: batches with their search runs, sightings, logs and failure artifacts; postings with raw data, parse and normalize results; source health; learned company aliases. Safe to re-run — what's already in the database is skipped.

| Option | |
|---|---|
| `--from DIR` | file store to import (default `data/raw/`) |

```bash
uv run auto-app import-files
```

---

## Where the data goes

Everything lives in `data/` in the project directory (git-ignored):

```
data/auto_app.db                      the database: postings (raw, parsed, normalized), jobs and their state,
                                      archived job records, batches, search runs, sightings, source health, aliases
data/logs/<batch_id>.log              run logs (scheduled runs: data/logs/scheduled.log)
data/screenshots/<source>/<id>.png    only with --screenshots / probe
data/failures/<batch_id>/<source>/    screenshots + HTML when a site blocks or shows a login wall / CAPTCHA
data/raw/                             file-only mode — with --debug-files (or debug_files: true), instead of the database
data/export/                          `auto-app export` output
browser-profiles/<source>/            saved logins
```

## Safety

- Browsers run as **your own logged-in Chrome**, with human-like pacing and hard hourly / daily limits per source.
- On a login wall, CAPTCHA / security check, or HTTP 403 / 429 / 999, that source **stops** (artifacts saved, notification sent) and goes into **cooldown**; other sources continue. Detection is never evaded — `--manual-checks` only hands a check to you.
- Workday is collected through its career site's own (undocumented) JSON endpoint at about one request per second; Ashby, Greenhouse and Lever through their official public job-board APIs.

## Adding a source plugin

**Each plugin is one source.** Any `*.py` under `plugins/` is discovered at startup; a class there that defines its own `name` is a source.

- **A company on a known platform** (Ashby, Greenhouse, Lever, Workday): `auto-app add-company URL` writes the plugin — a subclass of the platform base (`src/auto_app/platforms/`) with `name`, `company` and `board`. Override methods in that file for anything specific to the company.
- **A company on its own career site, or a new job site:** write the plugin yourself — an `ApiSource` (JSON API) or `SourceAdapter` (browser) subclass in `plugins/` (companies: `plugins/companies/`, with `company` set). A new platform base fits in `src/auto_app/platforms/`.

Plugins are checked when loaded (unique name, a `company` and valid `board` for platform-based ones); a broken file is reported by `auto-app plugins` and doesn't stop the other sources. A plugin supplies site knowledge only — how to list or search jobs, which search fields it supports (`filters`) and its `SiteFilters`, how to match a job, and how to parse its raw data (`parse`, with a `parser_version`). The engine handles known checks, pacing, limits, blocks and storage. See [`docs/step1-collect.md` §2](docs/step1-collect.md#2-sources-and-plugins).

## Tests

```bash
uv run pytest
```

Browser collection runs end to end against a local fake job site (`tests/fakesite/`); platform plugins and parsers against saved real responses and pages (`tests/fixtures/`, personal data removed); company collection over a fake HTTP transport; collection, parse and normalize run against both the database and the file store. No test touches the network.
