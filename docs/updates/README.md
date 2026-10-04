# Implementation progress

One entry per feature or significant fix, newest first. Design lives in the step docs ([overview](../README.md)); this folder records what has actually been built.

**With every feature or behavior change, update all three in the same change:**
1. this folder — a dated entry and a row in the table below;
2. the matching step doc — status table, milestones, and the sections describing the behavior;
3. the [project README](../../README.md) — commands, options, defaults, config fields, workflows and data locations (check it against `auto-app <command> --help`).

| Date | Feature | Step | Status | Entry |
|---|---|---|---|---|
| 2026-10-04 | Design: Step 3 — parallel appliers with fallback chains (Simplify hand-off first), needs-input queue + `apply inbox`, YAML profile / answers up front, review or auto-submit; no LinkedIn Easy Apply automation; tailoring deferred | 3 | 📐 designed | [2026-10-04-step3-design](2026-10-04-step3-design.md) |
| 2026-10-04 | Agent answers **fit + dealbreaker** (recommend = fit and no dealbreaker; older answers still read); evaluation by cause; preference model deferred | 2 | ✅ done | [2026-10-04-fit-dealbreaker](2026-10-04-fit-dealbreaker.md) |
| 2026-10-04 | Step 2 S4: judged jobs archived as training data (purge bug fixed; dedup within the archive; re-seen listing = new job), good / bad labels, `filter audit` (Audit tab + live evaluation panel), `filter eval` | 1–2 | ✅ done | [2026-10-04-s4-audit-eval](2026-10-04-s4-audit-eval.md) |
| 2026-10-04 | Design: evaluation + audit (interactive, live eval panel), the label and its bias, preference model data plan; judged jobs = training data (archived, never deleted; dedup within the archive); model as a second judge with shadow mode; un-recommended as training data only below the noise limit; purge bug found | 2 | 📐 designed | [2026-10-04-eval-audit-design](2026-10-04-eval-audit-design.md) |
| 2026-10-04 | `filter run` progress: `[n/N]` per judgment; live bar (done, cost, time left, running jobs) in a terminal | 2 | ✅ done | [2026-10-04-filter-run-progress](2026-10-04-filter-run-progress.md) |
| 2026-10-04 | `filter review` merged into `job browse` (`--state recommended`, new `-f`); one app, one key set | 2 | ✅ done | [2026-10-04-merge-review-into-browse](2026-10-04-merge-review-into-browse.md) |
| 2026-10-04 | Full-screen apps follow the system's light / dark mode (`AUTO_APP_UI__THEME=auto`, live), or a fixed theme | 2 | ✅ done | [2026-10-04-ui-theme](2026-10-04-ui-theme.md) |
| 2026-10-04 | `job browse`: the job pool in a full-screen app (tabs per state, `job show` details, `m` set state, source / company narrowing, archived read-only) | 1–2 | ✅ done | [2026-10-04-job-browse](2026-10-04-job-browse.md) |
| 2026-10-04 | Step 2 S3: review app (`filter review`, Textual; since merged into `job browse`) — decisions (`want_to_apply` / `not_applied`), per-filter feedback, undo; `want_to_apply` never expires | 2 | ✅ done | [2026-10-04-s3-review-app](2026-10-04-s3-review-app.md) |
| 2026-10-04 | Design: review app (Textual) with per-filter feedback; `want_to_apply` queue for Step 3 (no expiry; user-configurable handling) | 2–3 | 📐 designed | [2026-10-04-review-design](2026-10-04-review-design.md) |
| 2026-10-04 | `filter run --stream` (live text + lookups, one job at a time); CLI runner reads Claude Code's event stream, so `filter why` shows its lookups | 2 | ✅ done (verified live) | [2026-10-04-filter-run-stream](2026-10-04-filter-run-stream.md) |
| 2026-10-03 | Commands grouped: `auto-app source · collect · process · job · filter · data <command>` (+ `run`, `schedule`); old names removed | 1–2 | ✅ done | [2026-10-03-command-groups](2026-10-03-command-groups.md) |
| 2026-10-03 | Step 2: DeepSeek runner — `deepseek-…` models with `DEEPSEEK_API_KEY` (our web_fetch; no web_search) | 2 | ✅ done (offline-tested) | [2026-10-03-deepseek-runner](2026-10-03-deepseek-runner.md) |
| 2026-10-03 | Step 2: `claude-cli` runner (your Claude Code login, no API key; locked down; `runner: auto` default) | 2 | ✅ done (verified live) | [2026-10-03-claude-cli-runner](2026-10-03-claude-cli-runner.md) |
| 2026-10-03 | Global settings + `ANTHROPIC_API_KEY` in `.env` (`AUTO_APP_…`, `__` nesting); `searches.yaml` = what to search only | all | ✅ done | [2026-10-03-env-settings](2026-10-03-env-settings.md) |
| 2026-10-03 | Step 2 S2: agent runner (Claude API tool loop, LinkedIn blocked, budgets), answer parsing, `judgments`, `select`, `why`, `--reparse` | 2 | ✅ done (offline-tested) | [2026-10-03-s2-agent-select](2026-10-03-s2-agent-select.md) |
| 2026-10-02 | Step 2 S1: filters — folder loader + validation, versions, job details, `filters` command (`--job` preview), examples | 2 | ✅ done | [2026-10-02-s1-filters](2026-10-02-s1-filters.md) |
| 2026-10-01 | Design: Step 2 simplified — filters (folder: command + settings + optional parser) — an agent runs the command on each job; OR-ed with a preference model later | 2 | 📐 designed | [2026-10-01-step2-redesign](2026-10-01-step2-redesign.md) |
| 2026-10-01 | C11: job state (`step2` + `decision`), job TTL (delete / archive), archived records, repost warnings, `mark`; atomic migrations | 1 | ✅ done | [2026-10-01-c11-job-state-ttl](2026-10-01-c11-job-state-ttl.md) |
| 2026-10-01 | Readable `auto-app jobs` (newest first, display names, places, sources; `--source`, `--company`, `--limit`) | 1 | ✅ done | [2026-10-01-readable-jobs](2026-10-01-readable-jobs.md) |
| 2026-10-01 | LinkedIn: real filters on the newer search page (`f_SAL` segments, `f_AL`/`f_EA`/`f_JIYN`), LinkedIn `site_filters`, stop at "related" results | 1 | ✅ done | [2026-10-01-linkedin-real-filters](2026-10-01-linkedin-real-filters.md) |
| 2026-10-01 | LinkedIn searches use the newer search page by default (filters written into the query text) | 1 | ↩️ replaced by real filters | [2026-10-01-linkedin-newer-page-default](2026-10-01-linkedin-newer-page-default.md) |
| 2026-10-01 | LinkedIn: newer search page (`/jobs/search-results/`) via `site_url`; `attr~regex` card ids | 1 | ✅ done | [2026-10-01-linkedin-newer-search-page](2026-10-01-linkedin-newer-search-page.md) |
| 2026-10-01 | Dedup: description-overlap rule raised from 0.85 to 0.95 | 1 | ✅ done | [2026-10-01-description-overlap-095](2026-10-01-description-overlap-095.md) |
| 2026-10-01 | Dedup: a different title is a different job (merge veto) | 1 | ✅ done | [2026-10-01-title-veto](2026-10-01-title-veto.md) |
| 2026-10-01 | One plugin = one source: platform bases, one plugin file per company (`add-company` writes it), per-source named searches with `from:` (C12) | 1 | ✅ done | [2026-10-01-one-plugin-one-source](2026-10-01-one-plugin-one-source.md) |
| 2026-10-01 | `--debug-files` is a file-only mode (no database writes) | 1 | ✅ done | [2026-10-01-debug-files-only](2026-10-01-debug-files-only.md) |
| 2026-10-01 | Design: job state model (`step2` + `decision`), job TTL (delete / archive), archived-job records, repost warnings | 1–3 | 📐 designed → ✅ C11 | [2026-10-01-job-state-ttl-design](2026-10-01-job-state-ttl-design.md) |
| 2026-10-01 | `posted_within_days: all` (no date limit) | 1 | ✅ done | [2026-10-01-posted-within-all](2026-10-01-posted-within-all.md) |
| 2026-10-01 | Purge + storage-light defaults (C9); `auto-app run` pipeline + launchd schedule (C10) | 1 | ✅ done | [2026-10-01-purge-run-schedule](2026-10-01-purge-run-schedule.md) |
| 2026-09-30 | Dedup writes jobs (C8c): incremental linking, stable job ids/status, `dedup`, `jobs`, `--rebuild` | 1 | ✅ done | [2026-09-30-jobs](2026-09-30-jobs.md) |
| 2026-09-30 | Database (C7): SQLite + Alembic store for every stage; file store as `--debug-files` copy; `import-files`, `export` | 1 | ✅ done | [2026-09-30-database](2026-09-30-database.md) |
| 2026-09-30 | Dedup preview (C8b): Level 1b + Level 2, merge vetoes, near misses; `dedup --preview` | 1 | ✅ done (calibration pending data) | [2026-09-30-dedup-preview](2026-09-30-dedup-preview.md) |
| 2026-09-30 | Normalize stage (C8a): employer job keys, company aliases, title/location/description keys, MinHash | 1 | ✅ done | [2026-09-30-normalize](2026-09-30-normalize.md) |
| 2026-09-30 | Parse stage (C6): LinkedIn, Ashby, Greenhouse, Lever, Workday parsers; `auto-app parse` with coverage | 1 | ✅ done | [2026-09-30-parse](2026-09-30-parse.md) |
| 2026-09-29 | Source plugins + company collection (Ashby, Greenhouse, Lever, Workday); config split; `plan`, `plugins`, `add-company` | 1 | ✅ done | [2026-09-29-plugins-and-companies](2026-09-29-plugins-and-companies.md) |
| 2026-09-29 | Design docs reorganized by step; plugin design; this progress log | — | ✅ done | [2026-09-29-design-docs-by-step](2026-09-29-design-docs-by-step.md) |
| 2026-09-28 | Clean interruption (Ctrl+C) | 1 | ✅ done | [2026-09-28-clean-interruption](2026-09-28-clean-interruption.md) |
| 2026-09-28 | Rate pauses + daily ceiling replace per-run caps; retry cap | 1 | ✅ done | [2026-09-28-rate-limits](2026-09-28-rate-limits.md) |
| 2026-09-28 | LinkedIn verified live; detail-ready selector updated | 1 | ✅ done | [2026-09-28-linkedin-verified](2026-09-28-linkedin-verified.md) |
| 2026-09-28 | Login via plain Chrome; mock-keychain cookie fix | 1 | ✅ done | [2026-09-28-login-flow](2026-09-28-login-flow.md) |
| 2026-09-28 | Security-check detection; manual-check hand-off; Jobright disabled | 1 | ✅ done | [2026-09-28-security-checks](2026-09-28-security-checks.md) |
| 2026-09-28 | Config & data inside the project folder | — | ✅ done | [2026-09-28-project-local-paths](2026-09-28-project-local-paths.md) |
| 2026-09-28 | Raw collection engine (LinkedIn, Handshake, Jobright adapters; file store; CLI) | 1 | ✅ done | [2026-09-28-raw-collection](2026-09-28-raw-collection.md) |

## Up next

| Feature | Step | Design |
|---|---|---|
| Dedup calibration on overlapping LinkedIn + company data | 1 | [step1 §7.6](../step1-collect.md#76-rollout-and-calibration) |
| Step 2 S5 (deferred — evaluation decides): preference model, fit only, dealbreaker veto | 2 | [step2 §7.5–7.6](../step2-select.md) |
| Step 3 A1: profile / answers / policy, applier chain runner, Simplify hand-off + manual, `apply inbox` (hand-offs) | 3 | [step3 §10](../step3-apply.md) |

Further source plugins (e.g. Microsoft careers) are left to whoever needs them; the plugin interface is documented in [step1 §2.2](../step1-collect.md#22-plugin-interface).

## Entry template

```markdown
# <Feature> — YYYY-MM-DD
**Step:** 1 | 2 | 3   **Status:** done | partial | reverted
## What
## Why
## Changes (files)
## How to use
## Tests / verification
## Follow-ups
```
