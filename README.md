# auto-application

A personal pipeline that finds job postings, picks the ones worth applying to, and prepares applications. Design: [`docs/README.md`](docs/README.md) (overview) and one doc per step. Progress log: [`docs/updates/`](docs/updates/README.md).

**Implemented: Step 1 — Collect.** Postings from **job sites** (LinkedIn, verified) and **company career sites** (one plugin per company on the Ashby, Greenhouse, Lever and Workday platforms — verified on UiPath, NVIDIA, Adobe, Palantir) are collected, parsed into common fields, normalized, and deduplicated into **jobs**, stored in a local SQLite database. **Step 2 — Select** is being built: **filters** (one folder each — a command, its settings, optional parser) are written by you, and `filter run` runs each filter's agent on the jobs (Claude API, your Claude Code login, or DeepSeek — with web lookups) and marks them recommended / un_recommended (S1–S2); review comes next (S3). Step 3 (apply) is designed, not built.

---

## Contents
- [Setup](#setup)
- [Key terms](#key-terms)
- [Configuration](#configuration)
- [Common workflows](#common-workflows)
- [Command reference](#command-reference)
  - [Global options](#global-options)
  - [`source`](#source--step-1-setup-where-jobs-come-from) — `list`, `show`, `add`, `login`, `health`, `plan`
  - [`collect`](#collect--step-1a-run-the-searches) — `run`, `probe`, `stats`
  - [`process`](#process--step-1b-postings--jobs) — `parse`, `normalize`, `dedup`
  - [`run` and `schedule`](#run-and-schedule--all-of-step-1-automatically)
  - [`job`](#job--the-job-pool) — `list`, `show`, `mark`
  - [`filter`](#filter--step-2-judge-jobs-with-your-filters) — `list`, `show`, `run`, `why`, `reparse`
  - [`data`](#data--storage) — `inspect`, `export`, `import`, `purge`
- [Where the data goes](#where-the-data-goes)
- [Safety](#safety)
- [Adding a source plugin](#adding-a-source-plugin)
- [Tests](#tests)

---

## Setup

```bash
uv sync
cp config/searches.example.yaml config/searches.yaml   # what to search — then edit (git-ignored)
cp .env.example .env                                   # global settings + your API key — then edit (git-ignored)
uv run auto-app --help
```

Job sites use your installed **Google Chrome** (fallback: `uv run playwright install chromium`). Company sources on API platforms need no browser. Everything — config, browser logins, the database — stays inside the project folder and is git-ignored.

## Key terms

| Term | Meaning |
|---|---|
| **Source** | Where postings come from. **Each source is one plugin**, named by its plugin name (`linkedin`, `nvidia`, …) — the name used in the config, in storage and with `-s` |
| **Job site** | A source with many employers: LinkedIn (verified), Handshake (unverified), Jobright (disabled — bot check). Built in |
| **Company source** | A source with one employer: a small plugin file in `plugins/companies/` (e.g. `nvidia.py`) that names the company and its board on a **platform** (Ashby, Greenhouse, Lever, Workday — the platform code is shared). `auto-app source add` writes it |
| **Batch** | One `collect run` (or `run`) invocation |
| **Search** | A named search under a source in the config (`sources.<source>.searches`); can copy another source's search with `from: <source>/<search>` |
| **Search run** | One search on one source within a batch |
| **Sighting** | One card / list entry a search run saw, with its outcome: `fetched`, `known`, `filtered`, `budget_skipped`, `fetch_failed`, `cards_only` |
| **Posting** | One listing on one source, saved once. States: `fetched` → `parsed` (or `parse_failed`) → `normalized` → `linked` |
| **Job** | The real opening; one or more postings (from any sources, any locations) linked by dedup |
| **Job state** | Step 2's verdict (`recommended` / `un_recommended`) and your decision (`applied` / `not_applied`); a new job is *not evaluated*. Set by Steps 2–3 later, by hand now with `auto-app job mark` |
| **Job TTL** | A job expires `AUTO_APP_JOBS__TTL_DAYS__*` days (in `.env`) after its postings were last seen: not-evaluated / un-recommended jobs are **deleted** (with their postings), recommended ones **archived** (kept as a record, left out of dedup) |

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
        site_filters:           # this source's own options: `auto-app source show nvidia`
          facets: {"Job Type": ["New College Graduate"], "Job Category": ["Engineering", "Research"]}
```

**Global settings and the API key: `.env`** at the project root (git-ignored; template `.env.example`, which lists every setting with its default). One `KEY=value` per line; nested settings use `__`, maps are JSON on one line. Variables set in your shell win over `.env`; global settings still written in `searches.yaml` work too, with a notice to move them (`.env` wins).

```bash
ANTHROPIC_API_KEY=sk-ant-…                       # Step 2 (`filter run`): console.anthropic.com → API Keys
AUTO_APP_JOBS__TTL_DAYS__UNFILTERED=14           # job TTL in days unseen: delete …
AUTO_APP_JOBS__TTL_DAYS__UN_RECOMMENDED=7        # … delete …
AUTO_APP_JOBS__TTL_DAYS__RECOMMENDED=30          # … archive
AUTO_APP_RETENTION__RAW_DAYS=7                   # also SIGHTINGS_DAYS, LOGS_DAYS
AUTO_APP_SELECT__PARALLEL=3
AUTO_APP_SELECT__PRICES={"claude-haiku-4-5-20251001": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}}
AUTO_APP_RUN_WINDOW__START=08:00                 # also RUN_WINDOW__END, MIN_HOURS_BETWEEN_RUNS, RETRY__…
AUTO_APP_DEBUG_FILES=false                       # true = files under data/raw/ instead of the database
```

- **Inheritance:** a field left out comes from the search named in `from` (if any), else from `defaults`. `from` copies only the generic fields (keywords, locations, dates, types, levels, pages, card filters) — never `site_url` or `site_filters`. Setting a field overrides the copy; an empty list (`experience_levels: []`) clears it.
- **Empty vs. all:** no `keywords` means no keyword filter (every job matching the other filters), while no `posted_within_days` means the default, not "no limit" — write `posted_within_days: all` for that. On LinkedIn, `all` drops the "date posted" filter; on company boards, every listed job passes the date check.
- **Checked against the plugin:** a field the source can't apply is an error when written in the search itself (remove it — and state it in your Step 2 command if it matters) and a notice when copied with `from`; unknown `site_filters` are errors.
- **LinkedIn searches** run on LinkedIn's newer search page (the one the search bar on linkedin.com/jobs opens), using its **own filters**, never extra keywords:
  - generic fields map onto them when LinkedIn has an exact equivalent: `employment_types` (full_time, part_time, contract, internship), `experience_levels` (entry, director, executive), `workplace_types: [remote]`, `posted_within_days` (rounded up to LinkedIn's 24 hours / week / month), `locations: ["United States"]`;
  - anything else (associate, mid_senior, temporary, hybrid, on-site …) is **not filtered, with a notice in `source plan`** — the whole field, so LinkedIn never filters out values you asked for; state it in your Step 2 command if it matters;
  - `site_filters` sets LinkedIn's filter buttons directly and replaces the generic mapping: `employment_type` (full_time | part_time | contract | internship | volunteer), `experience_level` (LinkedIn's own levels: entry_level | senior | manager | director | executive), `remote`, `easy_apply`, `under_10_applicants`, `in_my_network`, `geo_id` (a location id copied from LinkedIn's URL — needed for places other than the United States);
  - after the exact matches LinkedIn lists "related" jobs that ignore the filters ("…may not be exact matches"); the tool stops reading at that note. Results are ordered by relevance, so there's no early stop: `max_pages` sets the depth.
- **`site_url`** (browser sources) replaces the generated search with a URL copied from your browser; its own filters then apply, not the search's generic fields. For LinkedIn, both the newer `/jobs/search-results/` page and the classic `/jobs/search/` page work (the classic one honours strict filter parameters like `f_E=2` and `sortBy=DD`).
- **Older layouts** (`job_sites:` + `companies:`) still load; a company without a plugin file gets an error with the `source add` command that creates it.

`auto-app source plan` shows what this config will run and where each field is applied (by the site, locally, or not at all — deferred); `auto-app source show <source>` lists what a source supports.

## Common workflows

**First time**
```bash
uv run auto-app source login linkedin                         # log in once in a normal Chrome window; quit it with Cmd+Q
uv run auto-app source add https://jobs.ashbyhq.com/uipath    # writes plugins/companies/uipath.py + a config stub
uv run auto-app source plan                                   # check what will run
uv run auto-app run --max-pages 1                             # a small first run: collect → parse → normalize → dedup
uv run auto-app job list                                      # the result
```

**Every day**
```bash
uv run auto-app run --purge                                   # or let the schedule do it:
uv run auto-app schedule install --at 09:00 --at 17:00
```

**Judging jobs with your filters (Step 2)**
```bash
mkdir -p filters && cp -r filters.example/ml-engineer filters/   # then edit filters/ml-engineer/command.md
uv run auto-app filter list                                   # problems / warnings
uv run auto-app filter show ml-engineer --job 28              # exactly what the agent gets for job 28
uv run auto-app filter run --limit 10 --max-cost 1            # a first trial
uv run auto-app filter why 28                                 # how job 28 was judged
uv run auto-app job list --state recommended
```

**Checking a new site or company**
```bash
uv run auto-app collect run -s linkedin --cards-only --max-pages 1      # are cards read correctly? (no job pages opened)
uv run auto-app collect probe linkedin "https://www.linkedin.com/jobs/view/<id>/"   # one job page, what was captured
uv run auto-app collect run -s nvidia && uv run auto-app process parse -s nvidia    # coverage per field
uv run auto-app data inspect nvidia <id>                                # raw → parsed → normalized
```

**Debugging a source with plain files (no database)**
```bash
uv run auto-app --debug-files collect run -s nvidia           # writes data/raw/postings/nvidia/<id>/… instead of the database
uv run auto-app --debug-files process parse && uv run auto-app --debug-files process normalize
uv run auto-app --debug-files process dedup --preview         # jobs need the database, so only a preview
uv run auto-app data import                                   # later: load the files into the database
```

**Step by step instead of `run`**
```bash
uv run auto-app collect run
uv run auto-app process parse
uv run auto-app process normalize
uv run auto-app process dedup --preview && uv run auto-app process dedup
```

---

## Command reference

Commands are grouped: `uv run auto-app <group> <command> [options]` — `source`, `collect`, `process`, `job`, `filter`, `data`, `schedule` — plus `run` on its own. A group by itself (`uv run auto-app job`) shows its commands; every command has `--help`.

```
auto-app run                     all of Step 1          auto-app job      list · show · mark
auto-app schedule                install · show · remove auto-app filter   list · show · run · why · reparse
auto-app source                  list · show · add · login · health · plan
auto-app collect                 run · probe · stats    auto-app data     inspect · export · import · purge
auto-app process                 parse · normalize · dedup
```

### Global options

Given before the command, e.g. `uv run auto-app --debug-files collect run`.

| Option | Effect |
|---|---|
| `-v`, `--verbose` | debug logging |
| `--debug-files` | **file-only mode**: postings, batches, parse and normalize results are written as browsable files under `data/raw/` **instead of** the database (same as `AUTO_APP_DEBUG_FILES=true` in `.env`). Nothing touches the database. Jobs need the database, so in this mode `process dedup` can only `--preview` (`run` previews its dedup step), and `job`, `data purge`, `data export` and `data import` stop with a message. Load the files into the database later with `auto-app data import` |

Most commands also accept `--config PATH` (default `config/searches.yaml`) and `--db PATH` (default `data/auto_app.db`).

---

### `source` — Step 1 setup: where jobs come from

#### `source list` · `source show NAME`
Lists the sources — one plugin each: built-in job sites and the files in `plugins/` (company sources in `plugins/companies/`) — with their kind, company and platform, plus the platforms available for new companies. Plugin files that failed to load (import error, bad board, duplicate name) are listed with the reason. With a name (a source, or a platform such as `workday`): its company and board, which search fields it supports (native / local / deferred) and its `site_filters`.

```bash
uv run auto-app source list
uv run auto-app source show nvidia
```

#### `source add URL`
Detects a company's job platform from a careers page or job-board URL and test-calls it (prints the number of open jobs). Then it writes the company's plugin, `plugins/companies/<name>.py` (a few lines: name, company, board on the platform base; commit it with your code and edit it for anything company-specific), and adds a commented search stub under `sources: <name>:` in your config. The source runs once you add a search there.

| Option | |
|---|---|
| `--name NAME` (alias `--key`) | source name (default: from the board) |
| `--company TEXT` | the employer's display name (default: from the name) |

```bash
uv run auto-app source add https://jobs.lever.co/palantir --company Palantir
uv run auto-app source add https://adobe.wd5.myworkdayjobs.com/external_experienced --name adobe --company Adobe
```
Marketing careers pages that load jobs with JavaScript (e.g. careers.adobe.com) can't be detected — open any job's *Apply* page and pass that URL.

#### `source login NAME`
Opens a **normal, non-automated Chrome** on the source's saved profile (`browser-profiles/<source>/`) so you can sign in — Google / school SSO work here. Log in, then **quit that Chrome with Cmd+Q** (that's what saves the session); the session is then checked and the page it landed on is printed.

| Option | |
|---|---|
| `--no-verify` | skip the session check afterwards |

```bash
uv run auto-app source login linkedin
```
Output: `linkedin session: ok (checked https://www.linkedin.com/feed/)`. Re-run when a site logs you out (collect stops that site with `needs_login` and notifies you).

#### `source health`
Health of each source: `ok`, `needs_login`, or `cooldown` (after a block, until a time), with the last reason and last successful run.

| Option | |
|---|---|
| `--reset SOURCE` | clear a cooldown / login status by hand |

```bash
uv run auto-app source health
uv run auto-app source health --reset linkedin
```

#### `source plan`
Shows what `collect run` would run: every source, its searches, and for each search field whether it is applied **natively** (by the site), **locally** (on what the site returns) or **deferred** (not filtered — state it in your Step 2 command if it matters); warns about fields a source can't apply. Config errors (unknown fields or sources, bad `site_filters`, bad `from`, a selected source with no searches) are reported here.

| Option | |
|---|---|
| `-s/--source NAME`, `--job-sites`, `--companies`, `--search NAME` | same selection as `collect run` |

```bash
uv run auto-app source plan
uv run auto-app source plan -s nvidia
```

---

### `collect` — Step 1a: run the searches

#### `collect run`
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
| `--scheduled` | enforce the run window and minimum hours between runs (`AUTO_APP_RUN_WINDOW__*`, `AUTO_APP_MIN_HOURS_BETWEEN_RUNS` in `.env`; for automation) |

```bash
uv run auto-app collect run                                # everything in the config
uv run auto-app collect run -s linkedin --max-pages 2
uv run auto-app collect run --companies
uv run auto-app collect run -s linkedin --search nvidia-check
```
How much is fetched: depth is `max_pages`; pacing is `max_details_per_hour` (pause, then continue) and `max_details_per_day` (stop; the rest become `budget_skipped`, fetched first next run). Stop any time with Ctrl+C — everything saved is kept.

#### `collect probe SOURCE JOB`
Fetches **one** job page (browser sources) by id or URL, overwriting any saved copy, and prints what was captured. Not rate-limited; takes a screenshot by default.

| Option | |
|---|---|
| `--no-screenshots`, `--capture-all-json` | |

```bash
uv run auto-app collect probe linkedin 4471589084
uv run auto-app collect probe linkedin "https://www.linkedin.com/jobs/view/4471589084/"
```

#### `collect stats [BATCH]`
Summary of a batch (default: the latest): per source — status, pages loaded, job pages fetched, outcomes, stop reasons, rate pauses, errors; per search run — stop reason, pages, outcomes (`listed` / `not_matching` for companies).

```bash
uv run auto-app collect stats
uv run auto-app collect stats 20261001-105917
```

---

### `process` — Step 1b: postings → jobs

#### `process parse`
Turns raw postings into common fields (title, company, locations, workplace / employment type, salary, posted date, reposted, applicants, apply URL, req id, description …) with each source's plugin parser. Only new postings and those whose parser version changed are parsed; failures are recorded (`parse_failed`) and retried. Prints field coverage per source — the quickest check of what a parser gets right.

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only these sources |
| `--reparse` | parse everything again |

```bash
uv run auto-app process parse
uv run auto-app process parse -s linkedin --reparse
```

#### `process normalize`
Computes each posting's dedup keys: normalized company (via the company source, employer board, or aliases) and title, **employer job key** (the same job on Workday / Ashby / Greenhouse / Lever, from LinkedIn's apply link or the company posting's URL), req id, normalized locations, a description hash and MinHash signature. Works on each posting independently. Prints, per source, the share with an employer key and how companies were resolved.

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only these sources |
| `--again` | normalize everything again |

```bash
uv run auto-app process normalize
```

#### `process dedup`
Links new normalized postings to **jobs**, incrementally:
1. **Level 1b** — exact same-source copies (reposts, recruiter copies, per-location copies) become duplicates of the earlier posting.
2. **Level 2** — the rest are grouped across sources (same employer key → same company + req id → same company + description overlap ≥ 0.95 → identical description → probable), with vetoes (**different title** — the poster treats differently titled postings as different jobs; different employment type; descriptions overlapping < 0.5; a shared employer key overrides them); each group joins the best-matching existing job, unless a veto with any of its postings blocks it, or becomes a new job.

Existing jobs keep their ids and state; every active job's last-seen and expiry dates are refreshed. Archived jobs take no part; a new job that shares a matching key with an archived job you acted on gets a **repost warning** (⚠ in `job list`). Prints new jobs with several postings, postings joining existing jobs, and **near misses** (pairs that looked related but weren't merged — for calibration).

| Option | Effect |
|---|---|
| `--preview` | dry run: show what dedup would do, write nothing |
| `--rebuild` | unlink everything and regroup from scratch — refused once any job is evaluated, decided or archived |
| `--no-near-misses`, `--limit N` | output control |

```bash
uv run auto-app process dedup --preview
uv run auto-app process dedup
```

---

### `run` and `schedule` — all of Step 1, automatically

#### `run`
The whole of Step 1 in one command: **collect → parse → normalize → dedup** (→ purge). Prints a section per stage and any problems (blocked sources, parse failures). Only one run at a time (`data/run.lock`).

| Option | Effect |
|---|---|
| `-s`, `--job-sites`, `--companies`, `--search`, `--max-pages`, `--headless`, `--force`, `--manual-checks` | as for `collect run` |
| `--no-collect` | skip collecting; only process what's saved |
| `--purge` | apply retention at the end |
| `--scheduled` | respect `run_window` / `min_hours_between_runs` and send a notification when done (used by the schedule) |

```bash
uv run auto-app run
uv run auto-app run --companies --purge
uv run auto-app run --no-collect
```

#### `schedule install` · `schedule show` · `schedule remove`
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

---

### `job` — the job pool

#### `job list` · `job show JOB_ID`
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
| `-d/--description` (`job show`) | also print the job's **full description** (its primary posting's text, as saved; for an archived job you applied to, the kept copy) |

```bash
uv run auto-app job list
uv run auto-app job list -s nvidia --limit 20
uv run auto-app job list --state applied
uv run auto-app job list --archived
uv run auto-app job show 28
uv run auto-app job show 28 --description
```

#### `job mark JOB_ID... --as STATE`
Sets jobs' state by hand — what Steps 2 and 3 will do once they exist: `recommended`, `un_recommended`, `applied`, `not_applied` (both also mark the job recommended) or `not_evaluated` (clears it). The state decides the job's TTL (`AUTO_APP_JOBS__TTL_DAYS__*` in `.env`), so e.g. marking a job `applied` keeps it 30 days after it was last seen and then archives it instead of deleting it. Archived jobs can't be changed.

```bash
uv run auto-app job mark 28 --as applied
uv run auto-app job mark 12 13 14 --as un_recommended
```
Output: one line per job with its new state and expiry date.

---

### `filter` — Step 2: judge jobs with your filters

A **filter** is a folder in `filters/` (git-ignored) that says how you want jobs judged — design: [`docs/step2-select.md`](docs/step2-select.md). You can have several. Start from an example:

```bash
mkdir -p filters && cp -r filters.example/ml-engineer filters/
```

| File | |
|---|---|
| `command.md` (required) | the prompt run for **every** job, in your own words: background, what you want, deal-breakers, when to recommend. `$ARGUMENTS` marks where the job details go (appended at the end if missing). Optional front matter: `description` only. Unless the filter has a `parser.py`, it must make the agent end with **our output convention**: `{"recommend": true \| false, "reason": "…"}` |
| `filter.yaml` (required) | `job_details` to send (`title, company, locations, workplace_type, employment_type, salary, posted_at, reposted, description`; default the first three + description), `max_description_chars`, the agent's `model`, `tools` (`web_search`, `web_fetch`), `max_turns`, `max_tool_calls`, and `applies_to` (sources or `source/search`; omit = every job) |
| `parser.py` (optional) | `parse(answer) -> {"recommend": bool, "reason": str}` for a command with its own output format (see `filters.example/verdict-format/`) |
| any other files | notes the command refers to (e.g. `sponsorship-notes.md`); the agent may read files in its own folder. Under 200 KB each |

A filter's **version** is a hash of its command, its other files and the agent settings in `filter.yaml` (not `parser.py`, not `applies_to`); judgments will be stored per version, so editing a command marks old judgments out of date.

#### `filter list` · `filter show NAME`
Lists the filters: version, model, tools, how the answer is read (output convention or `parser.py`), which jobs each judges, its description, and any problem (✗ — e.g. an `applies_to` naming no configured source or search) or warning (! — e.g. a command that doesn't mention the output convention or has no `$ARGUMENTS`). Folders that don't load are listed with the reason (file and field). With a name: that filter's settings and files; with `--job`: **exactly the text the agent would get for that job** — the command with the job details in place of `$ARGUMENTS`, inside a block marked as data from the posting (legal boilerplate removed, original casing kept, long descriptions cut and marked).

| Option | |
|---|---|
| `--job N` | with a name: print what the agent gets for job N |
| `--dir PATH` | another filters folder (e.g. `filters.example`) |

```bash
uv run auto-app filter list
uv run auto-app filter show ml-engineer --job 28
uv run auto-app filter list --dir filters.example
```

#### `filter run`
Runs each filter's agent on **active, undecided** jobs it hasn't judged yet and sets each job's Step 2 state. Per job and filter:

1. the command is run with the job's details (what `filter show NAME --job N` shows), by an agent that may use the filter's `tools` — web search / web fetch (**linkedin.com blocked**) — and read files in its own folder, within `max_turns` / `max_tool_calls` (then it must answer with what it has);
2. its answer is read with the output convention (or `parser.py`): **recommend**, **no**, or **couldn't read**;
3. the job becomes **recommended** if any filter that judged it recommends it or its answer couldn't be read (high recall — you see it), **un_recommended** if all said no. A failed run (API error) leaves the job undecided and is retried on the next `filter run`. Jobs you decided on (`applied` / `not_applied`) are never changed.

Every answer is stored with the trace (each lookup), tokens, tool calls and cost. A filter's later edit (new version) doesn't re-judge anything unless you pass `--rescore`.

| Option | |
|---|---|
| `-f/--filter NAME` (repeatable) | only these filters |
| `-j/--job N` (repeatable) | only these jobs — judged again even if already judged; a job you already decided on is judged too (see `filter why`), but its state doesn't change; each filter's `applies_to` still applies |
| `--limit N` | at most N jobs, newest first |
| `--rescore` | also re-judge jobs judged by an older version of a filter |
| `--max-cost USD` | stop starting new runs once this much was spent (runs already going finish). With the `api` runner it needs prices for the filters' models in `AUTO_APP_SELECT__PRICES`; with `claude-cli` it uses Claude Code's reported cost |
| `--parallel N` | agent runs at once (default `AUTO_APP_SELECT__PARALLEL`, 3) |
| `--dry-run` | list what would be judged; run nothing |
| `--stream` | watch each agent work live: a header per job, its text as it's written, each lookup (`→ WebSearch: …`) and its result (`✓ …`), then the verdict line. Runs go one job at a time (so the output stays readable) |
| `--dir PATH` | another filters folder |

**Who runs the agent** (`AUTO_APP_SELECT__RUNNER` in `.env`, default `auto`):
- **`api`** — the Claude API with `ANTHROPIC_API_KEY` (from `.env` or your shell); billed per token; cost from `AUTO_APP_SELECT__PRICES`. Full trace of every lookup.
- **`claude-cli`** — your **Claude Code login** (`claude -p`, headless), no API key: usage counts against your Claude subscription. Each run is **a brand-new Claude Code session with no context from earlier runs** — a fresh session id, nothing saved, none of your Claude Code settings or memory, in a new temporary folder (deleted afterwards) holding only copies of the filter's own files, so no `CLAUDE.md` or other project file can be picked up — locked down to the same abilities: only WebSearch / WebFetch / Read exist, anything not allowed is denied (`dontAsk`), Read is limited to the folder (not `command.md`, `filter.yaml`, `parser.py`), WebFetch to linkedin.com is denied, none of your Claude Code settings, hooks, MCP servers or commands are loaded, and nothing is saved as a session. The cost shown is Claude Code's list-price equivalent. `filter why` shows each lookup, as for the API runner (read from Claude Code's event stream).
- **`auto`** — `api` when `ANTHROPIC_API_KEY` is set, otherwise `claude-cli`.
- **DeepSeek** — chosen per filter by its model: a filter with `model: deepseek-chat` (or any `deepseek-…`) runs on DeepSeek's API with `DEEPSEEK_API_KEY` (in `.env`), whatever the setting above. Same loop, budget and output; `read_file` as usual; `web_fetch` is done by the app (linkedin.com and local / private network addresses refused, every redirect checked, 2 MB / 20 s limits, pages as plain text); **`web_search` isn't available** with DeepSeek (`filter run` says so and runs without it). For cost tracking and `--max-cost`, add the model's price to `AUTO_APP_SELECT__PRICES`. Filters on different providers can run in the same `filter run`.

`filter run` prints which runner it uses. No runner (or key) is needed for `--dry-run` or `filter reparse`.

```bash
uv run auto-app filter run --dry-run --limit 30
uv run auto-app filter run --limit 30 --max-cost 2      # a first trial
uv run auto-app filter run -f ml-engineer --rescore      # after editing that filter's command
uv run auto-app filter run --job 28                      # just job 28 (again, if already judged)
uv run auto-app filter run --job 28 --stream             # … and watch the agent work
```
Output: one line per judgment (`#28 ml-engineer recommend — reason`), then totals: recommend / no / couldn't read / errors, how many jobs are now recommended or un_recommended, tokens and cost.

#### `filter why JOB_ID`
Why a job was or wasn't recommended: for each filter that judged it — its version (current, out of date, or filter removed), model, verdict and reason, how the answer was read (and why not, if it couldn't be), tokens / tool calls / cost, every lookup it made (`web_search`, `web_fetch`, `read_file` with their inputs), and the full answer. `--trace` adds every model turn and tool result.

```bash
uv run auto-app filter why 28
uv run auto-app filter why 28 --trace
```

#### `filter reparse`
Re-reads the stored answers with each filter's **current** parser — e.g. after adding or fixing a `parser.py` — and updates the verdicts and the jobs' Step 2 state. No agent runs, no cost (the raw answers are kept).

| Option | |
|---|---|
| `-f/--filter NAME` (repeatable) | only these filters |
| `--dry-run` | only list the verdicts that would change |
| `--dir PATH` | another filters folder |

```bash
uv run auto-app filter reparse --dry-run
uv run auto-app filter reparse
```

---

### `data` — storage

#### `data inspect [SOURCE [JOB_ID]]`
Without arguments: a table of saved postings (state, readiness, size, structured data found, apply kind, title). With a source: only that source. With a source and job id: one posting in full — capture details, its job, the **parsed** fields, the **normalized** keys, and the description.

| Option | |
|---|---|
| `--chars N` | how much description / page text to print (default 1200) |

```bash
uv run auto-app data inspect
uv run auto-app data inspect linkedin
uv run auto-app data inspect nvidia JR2026576
```

#### `data export SOURCE JOB_ID`
Writes one posting from the database as files (raw page or record, `parsed.json`, `normalized.json`, screenshot) for a closer look.

| Option | |
|---|---|
| `--to DIR` | destination (default `data/export/`) |

```bash
uv run auto-app data export linkedin 4445417609
```

#### `data import`
Loads a file store (`data/raw/`, e.g. from `--debug-files` runs) into the database: batches with their search runs, sightings, logs and failure artifacts; postings with raw data, parse and normalize results; source health; learned company aliases. Safe to re-run — what's already in the database is skipped.

| Option | |
|---|---|
| `--from DIR` | file store to import (default `data/raw/`) |

```bash
uv run auto-app data import
```

#### `data purge`
Two passes:

1. **Job TTL** (`AUTO_APP_JOBS__TTL_DAYS__*` in `.env`; the clock is the time since any of the job's postings was last seen — refreshed whenever collect sees them, including as `known`):
   - not evaluated (14 days) and un-recommended (7 days) jobs are **deleted** together with every linked posting, its raw data and screenshot — a listing seen again later is collected as new;
   - recommended jobs — undecided, applied or not applied (30 days) — are **archived**: kept as a compact record (summary, Step 2 result, your decision, posting identities, matching keys; the description for jobs you applied to), hidden from `job list` (see `--archived`) and left out of dedup. Their postings stay as identities, so collect still skips those listings.
2. **Posting-data retention** (`AUTO_APP_RETENTION__*` in `.env`): raw pages / records / screenshots 7 days after a successful parse; sightings, logs and block artifacts after 30 days. Live postings keep their identity, parsed fields and dedup keys.

`--dry-run` lists the jobs it would delete or archive (number, state, last seen, company, title).

| Option | |
|---|---|
| `--dry-run` | show what would be removed |
| `--vacuum` | compact the database file afterwards |

```bash
uv run auto-app data purge --dry-run
uv run auto-app data purge --vacuum
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
data/raw/                             file-only mode — with --debug-files (or AUTO_APP_DEBUG_FILES=true), instead of the database
data/export/                          `auto-app data export` output
browser-profiles/<source>/            saved logins
.env                                  global settings and the API key (template: .env.example)
filters/<name>/                       your Step 2 filters (command.md, filter.yaml, …); examples in filters.example/
```

## Safety

- Browsers run as **your own logged-in Chrome**, with human-like pacing and hard hourly / daily limits per source.
- On a login wall, CAPTCHA / security check, or HTTP 403 / 429 / 999, that source **stops** (artifacts saved, notification sent) and goes into **cooldown**; other sources continue. Detection is never evaded — `--manual-checks` only hands a check to you.
- Workday is collected through its career site's own (undocumented) JSON endpoint at about one request per second; Ashby, Greenhouse and Lever through their official public job-board APIs.

## Adding a source plugin

**Each plugin is one source.** Any `*.py` under `plugins/` is discovered at startup; a class there that defines its own `name` is a source.

- **A company on a known platform** (Ashby, Greenhouse, Lever, Workday): `auto-app source add URL` writes the plugin — a subclass of the platform base (`src/auto_app/platforms/`) with `name`, `company` and `board`. Override methods in that file for anything specific to the company.
- **A company on its own career site, or a new job site:** write the plugin yourself — an `ApiSource` (JSON API) or `SourceAdapter` (browser) subclass in `plugins/` (companies: `plugins/companies/`, with `company` set). A new platform base fits in `src/auto_app/platforms/`.

Plugins are checked when loaded (unique name, a `company` and valid `board` for platform-based ones); a broken file is reported by `auto-app source list` and doesn't stop the other sources. A plugin supplies site knowledge only — how to list or search jobs, which search fields it supports (`filters`) and its `SiteFilters`, how to match a job, and how to parse its raw data (`parse`, with a `parser_version`). The engine handles known checks, pacing, limits, blocks and storage. See [`docs/step1-collect.md` §2](docs/step1-collect.md#2-sources-and-plugins).

## Tests

```bash
uv run pytest
```

Browser collection runs end to end against a local fake job site (`tests/fakesite/`); platform plugins and parsers against saved real responses and pages (`tests/fixtures/`, personal data removed); company collection over a fake HTTP transport; collection, parse and normalize run against both the database and the file store. No test touches the network.
