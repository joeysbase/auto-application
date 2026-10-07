---
name: app-config
description: Configure auto-app, the user's job pipeline — what to search (config/searches.yaml), which job sites and companies to collect from (sources, `source add`, logins), the filters that judge jobs (filters/<name>/), global settings (.env, without secrets), and the background schedule (config/serve.yaml). Use for "also search for applied scientist in Seattle", "add Stripe", "only remote jobs", "make a filter for research roles", "change the job TTL", "set up the app".
---

# Configuring auto-app

**Every change works the same way:**
1. Read the current file.
2. **Show the change** (the YAML / text lines) and why.
3. Edit **only after the user's OK.**
4. **Verify** with the command listed in each section.
5. Say when it takes effect.

All of these files are git-ignored and personal. Never commit them.

## 1. What to search: `config/searches.yaml`

Layout (template `config/searches.example.yaml`; details in `docs/step1-collect.md` §2.4 and README "Configuration"):

```yaml
defaults:                      # shared by every search
  posted_within_days: 1        # days back; "all" = no limit
  card_filters:
    exclude_title_words: [senior, staff, principal, manager, director]   # skipped before any page opens
sources:
  linkedin:
    searches:
      - name: ml-engineer-us   # unique within the source
        keywords: ["machine learning engineer", "applied scientist"]
        locations: ["United States"]
        workplace_types: [remote, hybrid, onsite]
        employment_types: [full_time]         # full_time | part_time | contract | temporary | internship
        experience_levels: [entry]            # internship | entry | associate | mid_senior | director | executive
        max_pages: 3
        site_filters: { geo_id: "103644278" } # the site's own filter buttons: `auto-app source show linkedin`
    limits: { max_details_per_hour: 40, max_details_per_day: 300 }
  nvidia:                      # a company source (plugin)
    searches:
      - from: linkedin/ml-engineer-us        # copy that search's generic fields
```

**Rules to remember:**
- **`from: <source>/<search>`** copies the generic fields only (keywords, locations, dates, types, levels, pages,
  card filters), never `site_url` / `site_filters`. The copied search **must exist**: renaming a search breaks every
  `from` that points to it. Check with `grep -n "from:"` when renaming.
- **No `keywords`** means no keyword filter. `posted_within_days` left out means the default; `all` means no limit.
- **LinkedIn** applies a generic field only where it has an exact filter; otherwise the field isn't applied (a notice
  in `source plan`). `site_filters` sets LinkedIn's own buttons (`employment_type`, `experience_level`, `remote`,
  `easy_apply`, `under_10_applicants`, `in_my_network`, `geo_id`). Places other than the US need a `geo_id` (copied
  from LinkedIn's URL).
- A field the source can't apply, written in the search itself, is an error. Unknown `site_filters` are errors.

**Verify:** `uv run auto-app source plan` (what will run, and where each field is applied). A "config problem" there
means the edit is wrong: fix it before anything else. It takes effect at the next collect / `serve` round.

**Filters vs searches:** a search decides what's *collected*; a filter decides what's *recommended*. "Only remote jobs"
is best as a search field (`workplace_types: [remote]`, or LinkedIn's `site_filters: {remote: true}`) if the site
filters it exactly. Otherwise, as a filter dealbreaker (section 3).

## 2. Where to collect from: sources

- **List:** `uv run auto-app source list` (job sites: linkedin, handshake, jobright; company plugins in
  `plugins/companies/`). A source with no searches in `searches.yaml` doesn't run.
- **What a source supports:** `uv run auto-app source show <source>` (search fields, `site_filters`, its board).
- **Add a company:** `uv run auto-app source add <careers or job-board URL> [--company "Stripe"] [--name stripe]`. It detects the
  platform (Ashby, Greenhouse, Lever, Workday), writes `plugins/companies/<name>.py`, and adds a search stub to
  `searches.yaml`. Then adjust its search (often `from: linkedin/<search>`) and run `source plan`. An unsupported
  platform → tell the user; don't hand-write plugins (they're left to the user).
- **Logins** (LinkedIn and other job sites): only the **user** can log in, with `uv run auto-app source login <source>`
  (it opens Chrome). Never ask for or type their password.
- **Health:** `uv run auto-app source health --json`. `--reset <source>` clears a cooldown (suggest waiting instead).
- **Per-source limits** (`limits:` under the source, e.g. pages per hour / day): lower them if a site blocks.

## 3. What gets recommended: filters, `filters/<name>/`

Creating, rewriting or tuning a filter: use the **`filter-authoring`** skill. It covers the files (`command.md`,
`filter.yaml`), drafting from what the user wants, trying it on jobs they've already decided before it judges
everything, versions and `--rescore`, and measuring it. Quick checks: `uv run auto-app filter list` and
`uv run auto-app filter show <name>`.

## 4. Global settings: `.env` (template `.env.example`)

- **Settings** are `AUTO_APP_…` lines (nested with `__`), e.g.:
  - job TTLs: `AUTO_APP_JOBS__TTL_DAYS__RECOMMENDED=30`, `…__UN_RECOMMENDED=7`, `…__UNFILTERED=14`;
  - `AUTO_APP_SELECT__PARALLEL=3` (judgments at once);
  - `AUTO_APP_SELECT__RUNNER` (`auto` / `api` / `claude-cli`);
  - `AUTO_APP_RUN_WINDOW__START` / `END`;
  - `AUTO_APP_UI__THEME`;
  - retention: `AUTO_APP_RETENTION__RAW_DAYS`.
  
  `.env.example` lists every setting with its default; read **that** file to explain settings.
- **`.env` holds secrets** (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`). **Never read, print or edit `.env` yourself.**
  Tell the user the exact line to add or change, and they edit it.

**Job expiry** (`AUTO_APP_JOBS__TTL_DAYS__UNFILTERED` / `__UN_RECOMMENDED` / `__RECOMMENDED`, plus the
`AUTO_APP_RETENTION__…` days for raw pages and logs):
- a job not seen for that many days expires: never judged → deleted; judged by any filter or decided → archived
  (a compact record, training data); `want_to_apply` never expires;
- expiry happens by itself, whenever job dates are refreshed (collecting, judging, decisions). Shortening a TTL takes
  effect at the next round, so show what would go first: `uv run auto-app data purge --dry-run`;
- retention runs in `serve` once per `retention` (`config/serve.yaml`, default `24h`, `null` = never);
- a real `data purge` is destructive: only with the user's OK.

## 5. The background schedule: `config/serve.yaml`

The window, round cap, pause, per-source `time_limit` / `every` / `enabled: false`, filter tries. This is covered by
the **`pipeline-control`** skill. Template `config/serve.example.yaml`; verify with `uv run auto-app serve status`. It
takes effect when `serve` restarts.

## 6. Not here

- **`profile/`** (the profile, answers, account passwords): for applying, which is off. Never read it.
- **`config/apply.yaml`**: automated applying, off. Leave it.

## Rules

- Show first, edit after the OK, verify after every edit, and report what the verify command said.
- Never edit `.env` or `profile/`, and never commit config files.
- Keep the user's existing comments and layout in YAML files. Change only the lines needed.
