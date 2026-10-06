# auto-application

A personal pipeline that finds job postings, picks the ones worth applying to, and prepares applications. Design: [`docs/README.md`](docs/README.md) (overview) and one doc per step. Progress log: [`docs/updates/`](docs/updates/README.md).

**Implemented: Step 1 — Collect.** Postings from **job sites** (LinkedIn, verified) and **company career sites** (one plugin per company on the Ashby, Greenhouse, Lever and Workday platforms — verified on UiPath, NVIDIA, Adobe, Palantir) are collected, parsed into common fields, normalized, and deduplicated into **jobs**, stored in a local SQLite database. **Implemented: Step 2 — Select.** **Filters** (one folder each — a command, its settings, optional parser) are written by you; `filter run` runs each filter's agent on the jobs (Claude API, your Claude Code login, or DeepSeek — with web lookups), which answers **fit** + **dealbreaker**; you review and label in `job browse`, audit rejections in `filter audit`, and measure each filter with `filter eval`. **Step 3 — Apply** is being built: the jobs you queued go through per-platform **applier chains** — **Greenhouse** is automated (questions answered from your profile, answer bank and Claude; what only you can answer is asked in the Apply section; the form filled and submitted when you say so), everything else uses the **Simplify hand-off** (your Chrome autofills, you submit) or **manual**; **Automated applying is off for now**: you apply by hand from the Apply section's queue. The browser agent, trial runs and submit come back with `AUTO_APP_APPLY__AUTOMATED=true`. **Step 4 — Run** is designed ([`docs/step4-run.md`](docs/step4-run.md)): `auto-app serve` collects on a schedule (e.g. 30 min on / 30 off), filters new jobs at once, and a **manager agent in Slack** sends you a digest of recommendations, takes your feedback, starts applying and submits on your go.

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
  - [`ui`](#ui--every-interactive-part-in-one-app) — Jobs (F1), Audit (F2), Apply (F3)
  - [`run`, `serve` and `schedule`](#run-and-schedule--all-of-step-1-automatically)
  - [`job`](#job--the-job-pool) — `list`, `show`, `browse`, `mark`
  - [`filter`](#filter--step-2-judge-jobs-with-your-filters) — `list`, `show`, `run`, `why`, `audit`, `eval`, `reparse`
  - [`apply`](#apply--step-3-apply-to-the-jobs-you-queued) — `profile`, `policy`, `queue`, `run`, `inbox`, `list`
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
cp -r profile.example profile                          # Step 3: your profile, answers, CV (git-ignored) — then edit
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
| **Job state** | Step 2's verdict (`recommended` / `un_recommended`, set by `filter run`) and your decision — `want_to_apply` (queued for Step 3) or `not_applied`, set when you review in `job browse`; `applied` will be set by Step 3. A new job is *not evaluated*. Any state can also be set by hand with `auto-app job mark` |
| **Label** | in `job browse` / `filter audit`, your answer for one job and one filter: should that filter **recommend** this job or **not** (`g` / `b`) — about the job, not a grade of what the filter said. Compared with its verdict, it says whether the filter was right; kept per filter as the training data for its preference model (later) |
| **Audit** | random samples of a filter's "no" jobs that you label, to measure its **noise** — how often it wrongly rejects (`filter audit`, `filter eval`) |
| **Applier** | Step 3: something that applies to one kind of application form. Each platform has an ordered **chain** of appliers (`config/apply.yaml`); when one can't handle a job, the next takes over. A **hand-off** applier (Simplify, manual) gives the job to you to submit |
| **Job TTL** | A job expires `AUTO_APP_JOBS__TTL_DAYS__*` days (in `.env`) after its postings were last seen: jobs a filter judged (recommended or un-recommended) or you decided on are **archived** — kept as training data, never merged with active jobs; jobs never judged are **deleted** (with their postings) |

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
AUTO_APP_JOBS__TTL_DAYS__UNFILTERED=14           # job TTL in days unseen: never judged → deleted
AUTO_APP_JOBS__TTL_DAYS__UN_RECOMMENDED=7        # … judged → archived (training data)
AUTO_APP_JOBS__TTL_DAYS__RECOMMENDED=30          # … archived
AUTO_APP_SELECT__NOISE_LIMIT=0.05                # evaluation: unlabelled "no" jobs may train a model below this noise
AUTO_APP_RETENTION__RAW_DAYS=7                   # also SIGHTINGS_DAYS, LOGS_DAYS
AUTO_APP_SELECT__PARALLEL=3
AUTO_APP_SELECT__PRICES={"claude-haiku-4-5-20251001": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}}
AUTO_APP_RUN_WINDOW__START=08:00                 # also RUN_WINDOW__END, MIN_HOURS_BETWEEN_RUNS, RETRY__…
AUTO_APP_DEBUG_FILES=false                       # true = files under data/raw/ instead of the database
AUTO_APP_UI__THEME=auto                          # full-screen apps: follow the system's light / dark mode; or a theme name
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
uv run auto-app job browse --state all                        # … or browse it in a full-screen app
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
uv run auto-app job browse                                    # review (opens on Recommended): decide; per filter: should it recommend the job?
uv run auto-app filter audit                                  # label random "no" jobs: how often each filter misses
uv run auto-app filter eval                                   # precision, noise, recall per filter
uv run auto-app job list --state want_to_apply               # your queue for Step 3
```

**Applying (Step 3)**
```bash
cp -r profile.example profile && cp ~/Documents/cv.pdf profile/cv.pdf   # then fill in profile/*.yaml
uv run auto-app apply profile                                 # what's missing
uv run auto-app ui                                            # Jobs (F1): a on jobs to apply to → Apply (F3): R starts them,
                                                              # answer what's asked, then s submit / f fill only (or hand-offs: o, a)
uv run auto-app apply queue                                   # (or from the command line: the queue …
uv run auto-app apply run                                     #  … and start it)
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

Existing jobs keep their ids and state; every active job's last-seen and expiry dates are refreshed. Archived jobs take no part (a listing seen again after its job was archived becomes a new job); a new job that shares a matching key with an archived job gets a **repost warning** (⚠ in `job list`: "looks like job #12, which you applied to / declined / Step 2 left out"). Prints new jobs with several postings, postings joining existing jobs, and **near misses** (pairs that looked related but weren't merged — for calibration).

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

### `ui` — every interactive part in one app

```bash
uv run auto-app ui
```
One full-screen app (in your terminal) with a section for each interactive part — switch by **clicking** a section in the top bar, with the **function keys**, or with **`[` / `]`** (previous / next — use these where F-keys are taken, e.g. F3 in VS Code's terminal, or press fn+F1… on a Mac):

| key | section | what it is | also opened by |
|---|---|---|---|
| **F1** (or click, `[` `]`) | **Jobs** | the job pool and your review — [`job browse`](#job-browse) | `auto-app job browse` (with its options) |
| **F2** | **Audit** | random samples of each filter's "no" jobs, with the evaluation panel — [`filter audit`](#filter-audit) | `auto-app filter audit` (which draws samples first) |
| **F3** | **Apply** | Step 3: applying to the jobs you queued — Queued, Needs input, Ready to submit, Hand-offs, … — [`apply inbox`](#apply-inbox) | `auto-app apply inbox` |

- The **bar at the top** shows the sections and what's waiting in each — `Audit (8 left)`, `Apply (5 queued, 2 ready)` — with the current one highlighted.
- Each section **keeps its state** (tab, cursor, search, narrowing) while you're in another, and **reloads when you come back** — a job you queued in Jobs shows up in Apply → Queued right away.
- **The mouse works:** click a section in the top bar, a tab in a section's tab bar, or a row in the list to select it.
- **`?` lists every key** of the current section (in its current tab), with what each does — including the ones kept off the key bar (`tab`, `←` `→` / `1`–`9` pick a filter, `m` set state, `u` undo, `r` reason, `o` open link, `s` / `c` narrow). `Esc` closes it.
- The **key bar** at the bottom shows only the **main keys of the current tab**, then `? all keys · q quit` (each is clickable); `ctrl+p` (the command palette: themes, "Go to Jobs / Audit / Apply") still works. Switching is ignored while a pop-up (e.g. the state menu) is open; in a search or reason box, `[` and `]` are typed as usual.
- Audit stays isolated: only the randomly drawn jobs and audit keys (Jobs' narrowing doesn't affect it).
- If a section can't open (e.g. `config/apply.yaml` doesn't load), it says why instead.

| Option | |
|---|---|
| `--dir PATH` | another filters folder |

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
| `--time-limit 30m` | the round's **time cap per source**, spread across its searches (each gets the time left ÷ the searches left). Ends early once every search is **caught up**, i.e. it reached postings already collected or the end of its results |
| `--max-new N` · `--round-pages N` | the round's caps per source: new postings, result pages |

With a goal, each source's summary says **caught up**, or **not caught up — stopped by time_limit / goal**. The next round finds the rest.

```bash
uv run auto-app run
uv run auto-app run --companies --purge
uv run auto-app run --no-collect
```

#### `serve` — the app running by itself
One long-running loop (design: [`docs/step4-run.md`](docs/step4-run.md)): **collect** (`auto-app run`, until every search is caught up, or the time cap), then **judge the round's new jobs as one batch** (`filter run`), then a **pause counted from the end of collecting**. The next round also waits until the batch is judged, so collecting never outpaces filtering. Rounds only run inside your daily window.

**When filtering can't keep up:**
- **No usage left** (Claude Code, API or DeepSeek limits): judging waits until the reset the error names (otherwise it retries every 15 min), and so does collecting. You get one notice, and it resumes by itself.
- **A job failing 3 times** for a filter that otherwise works becomes **unjudgeable** and stops blocking. `filter run --job N` gives it a fresh start.
- **A broken filter** (a config that won't load, a login or API key rejected, an unknown model, or 3 different jobs failing with none working): **you're notified at once and collecting stops** until you fix it and run `serve resume`, which judges the backlog first.

A round with new recommendations sends a notification ("ask Claude Code"). Each round's report goes into `serve status` and `data/logs/serve-rounds.jsonl`, for example `round 09:00–09:10 · linkedin: caught up, 37 new / 6 pages · 41 judged (5 recommended) · next round ≈ 09:40`. State lives in `data/serve.json`; the settings in `config/serve.yaml` (template `config/serve.example.yaml`: window, round cap, pause, jitter, per-source `time_limit` / `every` / `enabled: false`, filter tries).

| command | |
|---|---|
| `serve run` | the loop in the foreground (`--once`: one round now, or why not). `Ctrl-C` / SIGTERM stops it within 15 s (what's collected is saved). Holds `data/serve.lock`; a manual `auto-app run` during collecting waits for the next round |
| `serve install` · `serve remove` | keep it running in the background (launchd KeepAlive: starts at log-in, restarts after a crash; `--dry-run` shows the plist); log `data/logs/serve.log` |
| `serve status` | running / paused / stopped, the next round, the last round's report |
| `serve pause [--until 2h \| 09:00 \| 2026-10-07 09:00]` · `serve resume` | no new rounds (judging goes on); resume after a pause, or after fixing a broken filter |

```bash
uv run auto-app serve run --once
uv run auto-app serve install
uv run auto-app serve status
uv run auto-app serve pause --until 2h
uv run auto-app serve resume
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

- one row per job: number, posted date, company (as the posting shows it), title, where (`Austin, TX +2`), the sources it was found on (`linkedin, nvidia`), and when it **expires** (`in 12d`; `due` = the next purge deletes or archives it; `!` = recommended and undecided, archived within 3 days; `queued` = want to apply, never expires while waiting for Step 3);
- a state column appears once jobs are in different states; ⚠ marks a repost warning;
- the summary line counts the jobs found on more than one source.

With a job number, it shows that job:

- company, every location, posted date, first and last seen, apply link, state (with Step 2's and your dates), when it expires and what happens then, and any repost warning;
- each posting (the same opening on each source) with how it joined the job, in words: "first posting of this job", "same job id on the employer's system (1.00)", "repeat of linkedin/… on the same source" …; a posting titled differently from the job shows its title;
- its matching keys (how later postings find this job);
- for an archived job: the description kept with it (judged jobs keep what a filter reads).

| Option | |
|---|---|
| `-s/--source NAME` (repeatable) | only jobs found on this source |
| `-c/--company TEXT` | only jobs whose company contains this text |
| `--state S` | `not_evaluated`, `un_recommended`, `recommended` (undecided), `want_to_apply`, `applied` or `not_applied` |
| `--archived` | archived job records instead of active jobs |
| `--limit N` | only the newest N jobs |
| `-d/--description` (`job show`) | also print the job's **full description** (its primary posting's text, as saved — archived judged jobs keep it) |

```bash
uv run auto-app job list
uv run auto-app job list -s nvidia --limit 20
uv run auto-app job list --state applied
uv run auto-app job list --state want_to_apply
uv run auto-app job list --archived
uv run auto-app job show 28
uv run auto-app job show 28 --description
```

#### `job browse`
The job pool in a **full-screen app** (in your terminal) — and where you **review** what your filters found: decide on each job, and say for each filter whether it should recommend it. Every key saves at once; nothing is submitted anywhere. It's the **Jobs** section (F1) of [`auto-app ui`](#ui--every-interactive-part-in-one-app) — `job browse` opens the app there; F2 / F3 switch to Audit and Apply.

```
 All (64)  Not evaluated (12)  [New recommended (13)]  All recommended (23)  Un-recommended (41)  Applied (2)  Not applied (7)
┌───────────────────────────────────────────────────────────┬─────────────────────────────────────────────────┐
│ ★ 17  RemoteHunter  Data Scientist, …  want to apply  …   │ job #28  Research Scientist, AI Accelerator     │
│   28  NVIDIA        Research Scientist recommended in 14d │   company  NVIDIA · where Austin, TX · …        │
│ ✗ 15  Amazon        Applied Scientist  not applied    👎  │   postings (2) … matching keys …                │
│                                                           │ Filters ──────────                              │
│                                                           │ ▸[1] ml-engineer   recommend · you: 👍 should …  │
│                                                           │     strong ML systems fit; sponsors H-1B        │
│                                                           │     looked up: web_search: NVIDIA H-1B …        │
│                                                           │  [2] research      no · you: —                  │
│                                                           │ Description ──────────                          │
└───────────────────────────────────────────────────────────┴─────────────────────────────────────────────────┘
```

- **Tabs** (`tab` / `shift+tab`): All · Not evaluated · **New recommended** (recommended and not decided yet — the ones to decide on; opens here) · **All recommended** (every job a filter recommended, whatever you decided) · **Un-recommended** (undecided, every filter said no — to catch wrong rejections) · **Applied** · **Not applied**. A job you mark `a` (want to apply) **leaves New recommended at once** and goes to [Apply → Queued](#apply-inbox) — there's no Want to apply tab; `n` moves it to Not applied. `u` undoes; `r` right after `a` / `n` adds a reason to that decision. Archived jobs and audit samples are in [`filter audit`](#filter-audit) / `job list`. Newest first; columns: mark (★ want to apply, ✗ not applied, ✓ applied), number, company, title, posted, state, expires, 👍 / 👎 your labels.
- **Detail pane** (right; scrolls): everything `job show` prints — company, places, dates, apply link, state, expiry, repost warnings, each posting and how it joined the job, matching keys — then **every filter that judged the job**: its verdict, reason, what it looked up, your label (`you: 👍 should recommend → it was right` / `it missed it` / `a bad pick`) (`older version` = judged before you last edited that filter); then the description.
- **Archived jobs** can be labelled (they're training data) but not decided on or changed.
- `e` shows the **evaluation panel** (as in `filter audit`).

| Key | |
|---|---|
| `↑` `↓` | move through the jobs |
| `a` | **want to apply** — queues the job for Step 3 (it never expires while queued). On a job some filter said no to, also labels it **should recommend** for those filters (implied — unless you've labelled it for them yourself) |
| `n` | **not apply** |
| `←` `→` or `1`–`9` | pick a filter (marked ▸) when several judged the job |
| `g` / `b` | that filter **should** / **shouldn't recommend** this job — your answer about the job, whatever the filter said (not "its verdict was good / bad"); a label for that filter's preference model only; labelling again replaces it |
| `B` | **no filter should recommend** it — `b` for every filter on the job, in one key (one undo) |
| `m` | **set any state** — a menu: want to apply, applied, not applied, recommended, un-recommended, not evaluated (same rules as `job mark`) |
| `r` | add a short reason to your last answer on this job |
| `u` | undo your last change (any job, `m` included) |
| `o` | open the job's link in your browser |
| `/` | search title / company / source / job number (`Esc` clears) |
| `s` / `c` | show only jobs from these sources (comma-separated) / whose company contains this text; clear the box for all |
| `e` | show / hide the evaluation panel (`d`, drawing more audit samples, works only in `filter audit`) |
| `q` | quit |

What's saved: your decision on the job (its state, as `job list --state` shows), a `reviews` row per `a` / `n`, and a `feedback` row per (job, filter) with the filter's version and verdict — both keep the job's title, company and description, so they outlive the job's TTL.

| Option | |
|---|---|
| `--state TAB` | open on this tab: `recommended` (New recommended, default — the jobs to review), `all`, `not_evaluated`, `all_recommended`, `un_recommended`, `applied`, `not_applied` |
| `-f/--filter NAME` (repeatable) | only jobs these filters judged, and only their verdicts |
| `-s/--source NAME` (repeatable) | start narrowed to these sources |
| `-c/--company TEXT` | start narrowed to this company |
| `--dir PATH` | another filters folder (for the "older version" marks) |

```bash
uv run auto-app job browse                              # review what filter run recommended (opens on Recommended)
uv run auto-app job browse -f ml-engineer
uv run auto-app job browse --state all
uv run auto-app job browse --state all_recommended
uv run auto-app job browse -s nvidia
```
`job list` / `job show` stay plain text (for scripts and pipes).

**Colors:** by default the app **follows your system's light / dark mode**, and switches while open when you change it (macOS, Linux/GNOME, Windows). Set in `.env`:

| Setting | |
|---|---|
| `AUTO_APP_UI__THEME` | `auto` (default) or a fixed Textual theme: `textual-light`, `textual-dark`, `ansi-light` / `ansi-dark` (your terminal's own colors), `solarized-light`, `catppuccin-latte`, `nord`, `dracula`, `tokyo-night`, … (an unknown name lists them all) |
| `AUTO_APP_UI__LIGHT_THEME` / `AUTO_APP_UI__DARK_THEME` | what `auto` uses in light / dark mode (default `textual-light` / `textual-dark`) |

`ctrl+p` → "theme" in the app tries a theme for that session.

#### `job mark JOB_ID... --as STATE`
Sets jobs' state by hand (in the app: `m` in `job browse`) — normally `filter run`, your review in `job browse` and (later) Step 3 do it: `recommended`, `un_recommended`, `want_to_apply`, `applied`, `not_applied` (the last three keep Step 2's verdict, or mark the job recommended if it has none) or `not_evaluated` (clears it). The state decides the job's TTL (`AUTO_APP_JOBS__TTL_DAYS__*` in `.env`), so e.g. marking a job `applied` keeps it 30 days after it was last seen and then archives it; a `want_to_apply` job never expires while it waits for Step 3. Archived jobs can't be changed.

```bash
uv run auto-app job mark 28 --as applied
uv run auto-app job mark 31 --as want_to_apply
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
| `command.md` (required) | the prompt run for **every** job, in your own words: background, what you want, deal-breakers, when to recommend. `$ARGUMENTS` marks where the job details go (appended at the end if missing). Optional front matter: `description` only. Unless the filter has a `parser.py`, it must make the agent end with **our output convention**: `{"fit": true \| false, "dealbreaker": true \| false, "reason": "…"}` — **fit**: does the job meet your requirements (if unsure, true); **dealbreaker**: does one of your deal-breakers clearly apply (if unsure, false). The filter recommends a job that fits with no dealbreaker. (The older `{"recommend": …, "reason": …}` still works; `filter list` suggests updating.) |
| `filter.yaml` (required) | `job_details` to send (`title, company, locations, workplace_type, employment_type, salary, posted_at, reposted, description`; default the first three + description), `max_description_chars`, the agent's `model`, `tools` (`web_search`, `web_fetch`), `max_turns`, `max_tool_calls`, and `applies_to` (sources or `source/search`; omit = every job) |
| `parser.py` (optional) | `parse(answer) -> {"fit": bool, "dealbreaker": bool, "reason": str}` (or the older `{"recommend": bool, "reason": str}`) for a command with its own output format (see `filters.example/verdict-format/`) |
| any other files | notes the command refers to (e.g. `sponsorship-notes.md`); the agent may read files in its own folder. Under 200 KB each |

A filter's **version** is a hash of its command, its other files and the agent settings in `filter.yaml` (not `parser.py`, not `applies_to`); judgments will be stored per version, so editing a command marks old judgments out of date.

#### `filter list` · `filter show NAME`
Lists the filters: version, model, tools, how the answer is read (output convention or `parser.py`), which jobs each judges, its description, and any problem (✗ — e.g. an `applies_to` naming no configured source or search) or warning (! — e.g. a command that doesn't mention the output convention, still asks for the older `{"recommend": …}` answer, or has no `$ARGUMENTS`). Folders that don't load are listed with the reason (file and field). With a name: that filter's settings and files; with `--job`: **exactly the text the agent would get for that job** — the command with the job details in place of `$ARGUMENTS`, inside a block marked as data from the posting (legal boilerplate removed, original casing kept, long descriptions cut and marked).

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
2. its answer is read with the output convention (or `parser.py`): **recommend** (fits, no dealbreaker), **no — dealbreaker**, **no — doesn't fit**, or **couldn't read**;
3. the job becomes **recommended** if any filter that judged it recommends it or its answer couldn't be read (high recall — you see it), **un_recommended** if all said no. A failed run (API error) leaves the job undecided and is retried on the next `filter run`. Jobs you decided on (`want_to_apply` / `applied` / `not_applied`) are never changed.

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
| `--stream` | watch each agent work live: a header per job (`── [3/30] #28 title · filter`), its text as it's written, each lookup (`→ WebSearch: …`) and its result (`✓ …`), then the verdict line. Runs go one job at a time (so the output stays readable) |
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
Output:
- **Progress while it runs.** First, what will run (`judging 30 runs on 30 jobs, 3 at a time`). Then one line per finished judgment, numbered: `[ 7/30] #28 ml-engineer recommend — reason` (or `no — dealbreaker — …` / `no — doesn't fit — …`). In a terminal, a **live bar** stays at the bottom: done / total, cost so far, elapsed and estimated time left, and which jobs are running now with how long each has taken (`running #28(41s) #31(12s)`). With `--stream`, or when the output is piped or logged, there's no bar; the `[n/N]` lines remain, and `--stream` puts them in each job's header too.
- **At the end:** how long it took, then totals — recommend / no (how many for a dealbreaker) / couldn't read / errors, how many jobs are now recommended or un_recommended, tokens and cost — and the command to review them (`job browse`).

#### `filter why JOB_ID`
Why a job was or wasn't recommended: for each filter that judged it — its version (current, out of date, or filter removed), model, verdict and reason (with its two parts: `fit: yes · dealbreaker: no`; "not given" for older answers), how the answer was read (and why not, if it couldn't be), tokens / tool calls / cost, every lookup it made (`web_search`, `web_fetch`, `read_file` with their inputs), and the full answer. `--trace` adds every model turn and tool result.

```bash
uv run auto-app filter why 28
uv run auto-app filter why 28 --trace
```

#### `filter audit`
Measures how often each filter **wrongly rejects** — its **noise** — by having you label **random samples** of its "no" jobs. Opens [`auto-app ui`](#ui--every-interactive-part-in-one-app) on its **Audit** section (F2): **only the Audit tab** — the randomly drawn jobs, nothing else (labelling other jobs here would bias the estimate) — with the **evaluation panel** at the top, recomputed after every label.

```
 [Audit (8 left of 30)]
 ml-engineer  (as used)  judged 89 · picks 20 (review load 22%) · no 69
   precision      78% (61–89%)     23 of its picks labelled, 18 should be recommended
   noise          5% (1–22%)       22 audited, 1 missed → ≈3 missed in all (1–15) of 69 said no
   recall         ≈84% (49–94%)
   training data  NO — noise could be 20% > 5% limit (~71 more clean audits); your labels only
┌──────────────────────────────────────────────┬──────────────────────────────────────────┐
│ ? 141  Acme      Account Executive, East     │ job #141  Account Executive, East        │
│ ✓ 122  Stripe    Data Engineer               │ … ▸[1] ml-engineer  no · you: —          │
│ ✗ 118  Datadog   ML Platform Engineer        │                                          │
```

- **Drawing:** for each filter (or `-f` ones) whose last sample is fully labelled, it draws `-n` jobs (default `AUTO_APP_SELECT__AUDIT_SIZE`, 30) uniformly at random from the filter's **pool**: jobs whose latest verdict from it is "no" — active and archived — that you haven't labelled for it and that aren't in one of its samples already. Each draw is stored with the pool's size, so a sample can't be cherry-picked. `d` in the app draws `AUTO_APP_SELECT__AUDIT_MORE` (10) more per filter.
- **Labelling:** for the picked filter (`←` `→` / `1`–`9`): `g` it **should** have recommended the job (a miss) / `b` it **shouldn't** (it rightly said no); `B` = no filter should — most audited jobs are obvious rejections. After a label the cursor moves on by itself: to the job's next unlabelled filter (when several filters drew it), else to the **next job still to label**. Marks: `?` still to label, `✓` no sampled filter should recommend it (they rightly said no), `✗` one should have (a **miss**). `u` undoes.
- **The panel** (`e` hides it): see [`filter eval`](#filter-eval).
- **Only audit keys:** the footer lists `g` · `b` · `B` · `←→` filter · `u` undo · `r` reason · `o` open link · `/` search · `d` draw more · `e` evaluation · `q` quit. Deciding on jobs (`a` / `n`), setting states (`m`) and narrowing (`s` / `c`) belong to `job browse` and do nothing here.
- Only labels on **sampled** jobs measure the noise: a "no" job you labelled elsewhere (e.g. in Un-recommended) wasn't picked at random.

| Option | |
|---|---|
| `-f/--filter NAME` (repeatable) | only these filters |
| `-n N` | jobs to draw per filter (default 30) |
| `--dir PATH` | another filters folder |

```bash
uv run auto-app filter audit
uv run auto-app filter audit -f ml-engineer -n 60
```
With no miss found, **59** audits show the noise is below 5% (one-sided 95%); each miss found raises that — 1 → 93, 2 → 124, 3 → 153 ([docs/study.md](docs/study.md) has the worked example).

#### `filter eval`
How well each filter judges, **against your labels** — the same numbers as the audit panel, printed. Per filter:

| | from | meaning |
|---|---|---|
| precision | your labels on its picks (recommend, or couldn't read) | the share of its picks it should have recommended |
| noise | your labels on its audited "no" jobs | the share of its rejections it should have recommended (misses) |
| missed in all | noise × the jobs it said no to | an estimate of the jobs it wrongly hid |
| recall | right picks ÷ (right picks + missed in all) | the share of good jobs it finds — the number that matters most |
| review load | picks ÷ judged | how much it asks you to read |
| by cause | its "no"s split into dealbreaker and "doesn't fit" rejections, with the audited misses of each | a wrong dealbreaker call hides a job however well it fits (shown once a filter reports dealbreakers) |
| training data | the noise's **one-sided 95% exact** upper bound vs `AUTO_APP_SELECT__NOISE_LIMIT` (5%) | YES: its unlabelled "no" jobs may train its preference model; NO: how many more clean audits would get there |

Ranges are 95% Wilson intervals. Nothing is cached — every label changes the numbers at once.

| Option | |
|---|---|
| `-f/--filter NAME` (repeatable) | only these filters |
| `--all-versions` | also each filter version on its own (its own verdicts and the audits drawn while it was current) |

```bash
uv run auto-app filter eval
uv run auto-app filter eval -f ml-engineer --all-versions
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

### `apply` — Step 3: apply to the jobs you queued

> **Automated applying is off for now (2026-10-05).** The pipeline collects, filters and recommends. Jobs you mark `a` (want to apply) go to the **Apply section's Queued tab** (F3), and **you apply by hand**:
> - `enter` opens the apply link in your browser and your CV in Finder;
> - `a` marks it **applied**;
> - `d` marks it **not applying**.
>
> Both then show in Jobs (F1) → Applied / Not applied. `apply run` only says so. Everything below (applier chains, the browser agent, trial runs, fill only / submit, site accounts) is kept, and comes back with `AUTO_APP_APPLY__AUTOMATED=true`.

Works through your **want to apply** queue (jobs you marked `a` in `job browse`) — design: [`docs/step3-apply.md`](docs/step3-apply.md). Each job's platform (from its apply link: `greenhouse`, `lever`, `ashby`, `workday`, `linkedin_easy_apply`, `other`) picks an **applier chain** from `config/apply.yaml`; appliers are tried in order, and when one can't handle the job (or isn't built yet), the next takes over:

| applier | status | how |
|---|---|---|
| `simplify_auto` | ✅ — not in the default chains since 2026-10-05 (the agent took its place); name it in `config/apply.yaml` to use it | **automated Simplify**: Playwright's Chromium with your Simplify Copilot extension (copied from your Chrome) and a dedicated profile you log in to once (`apply login simplify`). Starting a job opens the form **headless**, clicks "Start Application" on a description page (or opens a form embedded from an ATS directly), answers Simplify's one-time cookie notice ("Essentials only" — `AUTO_APP_APPLY__SIMPLIFY_CONSENT`), clicks **Autofill This Page**, fills what Simplify left empty from your profile / answers, and reads the form back (below). Submitting does the same in a browser, then submits (or fill only). When Simplify's panel doesn't appear (not logged in, no form, panel changed), the next applier takes over. Fills from **your Simplify profile** — Simplify's terms may not allow automated use: your choice, your account |
| `simplify` | ✅ | **hand-off**: opens the apply link in **your Chrome**, where the [Simplify](https://simplify.jobs) extension autofills the form; you check, submit, then press `a` in `apply inbox` |
| `manual` | ✅ | **hand-off**, always last: opens the apply link and shows your CV in Finder; you apply, then press `a` |
| `greenhouse` | ✅ | **automated**: reads the job's questions from Greenhouse's public API, answers them (below), asks you what's left (**Needs input**); once complete it's **Ready to submit** — you check every answer, then the app fills the form in a browser (CV first, then text and dropdowns) and submits it, or fills it for you to submit. A closed posting stops (no fallback); a form it can't fill falls back to Simplify |
| `agent` | ✅ — **first in every default chain** (after the Greenhouse API applier on Greenhouse; never on LinkedIn Easy Apply). Starting a job: a **trial run** through every step, stopping before the final submit. `f`: the same in a visible window, left on the last step for you. `s`: the same, then the final submit and the confirmation check (no confirmation → Action needed, never a second applier) — [`docs/step3-agent.md`](docs/step3-agent.md) | a **Claude browser agent** (Opus 5.5, your Claude Code login) that works out any form: generic browser tools as its hands, our code for the rules — never submits outside `s`, never clicks Next in a dry run, stays on the application, fills sensitive questions only from `answers.yaml`; creates / signs in to site accounts with your email and Keychain password (never shown to the model); a time limit per run |
| `lever`, `ashby`, `workday` | planned | automated, like greenhouse (A4–A6); until built, chains skip them (recorded as "not built yet") |

LinkedIn Easy Apply is never automated. The app submits only when you press `s` on a ready application (auto-submit comes with A5).

**Starting a job is a trial run** (`enter` / `R` / `r` in Apply, `apply run`): the automated applier fills the real form in a headless browser and **reads it back** — the browser agent goes through **every step** (signing in / creating the account if the site asks; each step is saved on the site as a draft) and **stops before the final submit**, so Ready to submit means it can be submitted; only `s` submits. On a **multi-step form** (Workday, Adobe's careers site, …) only the first step: its Next button sends the step to the site, so the later steps are filled when you submit (`s`, or `f` in a window) — a new question there brings the job back to Needs input with your answers kept — every field, its label, whether it's required, what's in it. Nothing is submitted. A required field still empty (one the API didn't list, or an answer that didn't take — e.g. not one of the dropdown's options) becomes a question for you → **Needs input**; when every required field is filled → **Ready to submit**. Each answer is recorded with where it came from (`simplify`, `form`, profile, `bank:<key>`, claude, you). Forms without `<label>`s (Rippling) are read by the question text around each field; a dropdown's own search box isn't a question.

**How questions are answered** (Greenhouse, and later the other automated appliers):

| kind | answered from |
|---|---|
| standard — name, email, phone, CV, country, LinkedIn / GitHub / website, city, address, state, postal code | `profile.yaml` (`location:` holds address1 / address2 / city / state — full name — / postal_code / country) |
| known — start date, relocation, "interviewed here before?" … | `answers.yaml`: the same question (ignoring case / punctuation, `{company}` / `{title}` filled in), else **Claude** picks the entry that means the same, with a confidence (below `AUTO_APP_APPLY__MATCH_THRESHOLD`, 0.9, it goes to you) |
| **sensitive** — work authorization, sponsorship, citizenship, demographics, legal agreements / consent | **only** an `answers.yaml` entry (exact, or picked by Claude above the threshold) — never drafted, never guessed |
| free text — text areas ("Why us?") | a saved free-text answer (`kind: free_text`), else **drafted by Claude** from your profile and the job — marked ✎ and shown to you in review |
| dropdowns | the answer mapped onto the listed options (exact, or yes / no) |

Optional questions with no answer are left blank, never asked. Claude runs through your **Claude Code CLI** login (`AUTO_APP_APPLY__MODEL`, default `claude-sonnet-5`): one call per application, no tools, a fresh session, nothing saved, `ANTHROPIC_API_KEY` not used. Without a working profile (`apply profile` reports problems), the automated appliers are skipped and jobs go to Simplify.

**Your files** (git-ignored; templates committed):

| File | |
|---|---|
| `profile/profile.yaml` | name, email, phone, location, links, education, experience, `cv:` path, `accounts:` (the email the agent signs in / up with on sites like Workday — default your email — and `accept_terms`, whether it may tick a site's terms box when creating an account) — template `profile.example/profile.yaml` |
| `profile/answers.yaml` | your **answer bank**: work authorization, sponsorship, start date, relocation, salary, demographics, saved free-text answers (`kind: free_text`, `{company}` / `{title}` filled in) — template `profile.example/answers.yaml`. Used by the automated appliers; answers you give in the inbox can be added to it (`S`) |
| `profile/cv.pdf` | your CV, uploaded as is |
| `config/apply.yaml` | the **apply policy**: `workers`, `pace` (per hour / day / company, hours window — for automated submission), `order` (`queued` / `newest_posting`), `appliers` (the chain per platform; `"*"` for any other), `submit` (`review` / `auto` per automated applier), `auto_drafts`, `notify` — template `config/apply.example.yaml`; without it, the template's defaults apply |

#### `apply profile` · `apply policy`
Check your files. `apply profile`: your name and email, the CV, how many answers are saved, problems (✗ — a missing file, invalid YAML, an answer without `question` / `answer`, the CV not found; exit code 1), and the common answers still missing (you'll be asked when an application needs them). `apply policy`: the effective policy and each platform's chain with ✓ (built) / … (not built yet), and problems: unknown appliers or platforms, a chain not ending in `manual`, LinkedIn Easy Apply with an automated applier.

#### `apply account set` · `apply accounts`
Site accounts for the browser agent (Workday and other sites that need one): it **signs in**, or **creates an account**, with the email and password **you** provide.
- **In `profile/profile.yaml`** (git-ignored):
  ```yaml
  accounts:
    email: you@mail.com          # default: your email
    password: "…"                # for signing up everywhere
    passwords: { nvidia.wd5.myworkdayjobs.com: "…" }   # a site where you already have an account: sign in there
    accept_terms: true           # may tick a site's terms box when creating an account
  ```
  The `accounts:` section is **never sent to Claude** (the question matcher leaves it out).
- **Or the macOS Keychain:** `apply account set` asks for the password twice and saves it (service `auto-app`). `--site HOST` sets one site's own. `profile.yaml` wins when both are set.
- The agent types it with its `fill_secret` tool, so Claude never sees it, and it isn't logged.
- **Sign in or sign up:** where a site needs an account, the agent **signs in only with account info for that site**: an account on record (`apply accounts`), or a password you set for it with `--site`. **Otherwise it signs up** with your email and password. If the site says the email is already registered, it signs in with the same email and password instead; a wrong password → Action needed. It does this whenever a site asks, including the trial run. The job's note says "created an account on …" or "signed in to …".
- **What it needs from you:** at least a valid **email** and a valid **password**. `apply account set` refuses a password a sign-up would reject (it needs 8+ characters, upper and lower case, a number and a symbol). Without either, the agent doesn't try, and the job waits in Action needed ("set …"). `apply profile` shows whether both are set.
- Email verification goes to **Action needed**: verify, then `r`.

`apply accounts` lists every account the agent created or signed in to (site, email, created / signed in, last used, e.g. "verification pending") and whether a password is set.

```bash
uv run auto-app apply account set
uv run auto-app apply account set --site nvidia.wd5.myworkdayjobs.com
uv run auto-app apply accounts
```

#### `apply login simplify`
Opens the browser `simplify_auto` uses — Playwright's Chromium with the Simplify Copilot extension copied from your Chrome (the newest installed version; recopied when Chrome updates it) and a dedicated profile in `browser-profiles/simplify/` (git-ignored). Log in to Simplify there once (the window hides the automation markers Google checks, so "Continue with Google" usually works — otherwise use your email), click the extension's icon to check you're logged in, then close the window. If a window on that profile is still open, the command says so (quit it first). Needs `uv run playwright install chromium` (branded Chrome no longer loads extensions under automation).

#### `apply queue`
The jobs you queued, in the policy's order: number, company, title, platform, chain, and the application's status (`not started`, `handoff (simplify)`, `failed`, …).

#### `apply run`
Starts an application for each queued job without one, and moves it through its chain to the first applier that can take it: the form is filled headless and read back (Simplify autofill / Greenhouse API answers; questions answered with at most one Claude call each), landing in **Needs input** or **Ready to submit**; jobs no automated applier can take become **hand-offs**. Refuses while `apply profile` reports problems. A failed application isn't restarted by `run`: retry it in the inbox.

| Option | |
|---|---|
| `-j/--job N` (repeatable) | only these jobs |

#### `apply inbox`
Step 3 — the **Apply** section (F3) of [`auto-app ui`](#ui--every-interactive-part-in-one-app); `apply inbox` opens the app there. **With automated applying off (the default) it's just the Queued tab: `enter` opens the link and your CV, `a` applied, `d` not applying.** With it on (`AUTO_APP_APPLY__AUTOMATED=true`), all of the following: **`R`** (any tab) **starts the jobs you queued** that haven't started — the same as `apply run`, in the background; the tab bar says how many are waiting ("2 queued, not started — R starts"). Starts run one at a time (about a minute each — one Simplify browser), and `s` / `f` wait for the job being started (and starts wait while a fill-only window is open): `enter` / `r` / `R` pressed while one is running **waits its turn** and starts automatically after it. **Progress**: the status line at the bottom says which job is being filled and how many are done ("starting job 22 — … 1 done, 4 more after it", then "job 22 → Needs input · 2 done, 3 to go"); in Queued, the job being filled says *starting…*, the rest *next* / *waiting*; each finished job appears in its tab right away (the tab counts go up). Quitting never waits for it (what didn't start stays queued); while a form is being submitted, `q` warns once — press it again to quit anyway. A tab each (`tab` switches; the key bar shows that tab's main keys, `?` all of them):

| tab | keys |
|---|---|
| **Queued** — jobs you marked `a` (want to apply), not started yet; they appear here right away | `enter` **start this one** · `R` start all · `d` take it out of the queue (not applied) · `o` open the link |
| **Needs input** — questions only you can answer | `enter` **answer** the question marked ▸ — a dropdown / radio / checkbox opens a **list to pick from** (↑↓ `enter`; a checkbox: *Yes — check it* / *No — leave it unchecked*; a multi-select: `space` ticks, `enter` saves), anything else a text box; after each answer the **next open question opens by itself** — this job's, then the next job's (Esc stops) · `A` answer it for **every** job waiting with the same question · `S` the same, and **save** it to `answers.yaml` · `n` next applier · `o` open the link · `d` drop |
| **Action needed** — a CAPTCHA, a sign-in or an email verification | `r` **start over** (e.g. after you verified your email, or set an account password) · `f` fill only / `s` submit in a visible browser (solve it there) · `n` next applier · `o` · `d` |
| **Ready to submit** — every question answered | the detail lists every answer with its source (profile / `bank:<key>` / claude / ✎ draft / you) — `s` **submit** (fills the form in a browser and submits; the job becomes `applied` on the confirmation page) · `f` **fill only** (a visible browser, filled; you check, submit, close it; then `a`) · `e` edit an answer (`3: new answer`; a choice question: just `3`, then pick) · `a` applied · `n` next applier · `o` · `d` |
| **Hand-offs** — oldest first | `o` open (Simplify: in your Chrome; manual: the link + your CV) · `a` **applied** (the job becomes `applied`) · `c` **couldn't** (the next applier in the chain takes over) · `l` later (to the back) · `r` **start over** (back to the queue and started again in the background — the automated appliers get another go, e.g. after a browser problem) · `d` drop (the job becomes `not applied`) |
| **Failed** — every applier tried | `r` start over (the chain from the top, in the background) · `m` apply by hand (manual) · `d` drop |
| **Done** — submitted or dropped, newest first (history: a job you set back to want to apply — `m` in Jobs — is queued again, and its old row here says "re-queued") | `o` open the apply link |

The detail pane shows the job, its apply link, the chain (▸ the current applier), its questions (✓ answered, ? open, ✎ draft) and every attempt with its outcome and reason. Submitting runs in the background ("submitting…"); a browser window opens unless `AUTO_APP_APPLY__HEADLESS=true`. If a job goes straight to Hand-offs with "the browser crashed at start", the attempt's reason and `data/logs/browser-launch.log` say why. (`DYLD_*` variables such as `DYLD_LIBRARY_PATH=/opt/homebrew/lib`, which crashed Playwright's Chromium in ImageIO, are already removed from every browser's environment.)

#### `apply list`
Application history: every application with its job, platform, status (and date, once submitted), applier and number of attempts.

| Option | |
|---|---|
| `--status S` | `queued`, `needs_input`, `action_needed`, `ready`, `handoff`, `failed`, `submitted` or `dropped` |

```bash
uv run auto-app apply queue
uv run auto-app apply run
uv run auto-app apply run --job 28
uv run auto-app apply inbox
uv run auto-app apply list --status submitted
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
   - jobs a filter **judged** — recommended (30 days) or un-recommended (7 days) — and jobs you decided on are **archived**, never deleted: they're **training data** for evaluation and the preference model. Kept: a record (summary, Step 2 result, your decision, the original posting identities, matching keys) plus what a filter reads (the primary posting's parsed fields, description included); dropped: raw data, normalized data, the other postings' parsed data. Archived jobs are hidden from `job list` (see `--archived`), never judged again and **never merged with active jobs** — a listing seen again becomes a new job. Among themselves they're **deduplicated**: one with the same title and a shared matching key as an earlier archived job is linked to it (one training example);
   - jobs **never judged** (not evaluated, 14 days) are **deleted** together with every linked posting, its raw data and screenshot — a listing seen again later is collected as new.
2. **Posting-data retention** (`AUTO_APP_RETENTION__*` in `.env`): raw pages / records / screenshots 7 days after a successful parse; sightings, logs and block artifacts after 30 days. Live postings keep their identity, parsed fields and dedup keys.

`--dry-run` lists the jobs it would delete or archive (number, state, last seen, company, title); the summary counts archived jobs linked to an earlier archived copy.

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
                                      archived jobs (training data), Step 2 judgments, your decisions and labels, audit samples, Step 3 applications and their attempts,
                                      batches, search runs, sightings, source health, aliases
data/logs/<batch_id>.log              run logs (scheduled runs: data/logs/scheduled.log)
data/screenshots/<source>/<id>.png    only with --screenshots / probe
data/failures/<batch_id>/<source>/    screenshots + HTML when a site blocks or shows a login wall / CAPTCHA
data/raw/                             file-only mode — with --debug-files (or AUTO_APP_DEBUG_FILES=true), instead of the database
data/export/                          `auto-app data export` output
browser-profiles/<source>/            saved logins
.env                                  global settings and the API key (template: .env.example)
filters/<name>/                       your Step 2 filters (command.md, filter.yaml, …); examples in filters.example/
profile/                              Step 3: profile.yaml, answers.yaml, cv.pdf; templates in profile.example/
config/apply.yaml                     Step 3: the apply policy (template config/apply.example.yaml)
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

Browser collection runs end to end against a local fake job site (`tests/fakesite/`); platform plugins and parsers against saved real responses and pages (`tests/fixtures/`, personal data removed); company collection over a fake HTTP transport; collection, parse and normalize run against both the database and the file store; Step 2 agents against a fake model; `job browse` through Textual's test driver. No test touches the network.
