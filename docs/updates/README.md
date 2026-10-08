# Implementation progress

One entry per feature or significant fix, newest first. Design lives in the step docs ([overview](../README.md)); this folder records what has actually been built.

**With every feature or behavior change, update all three in the same change:**
1. this folder — a dated entry and a row in the table below;
2. the matching step doc — status table, milestones, and the sections describing the behavior;
3. the [project README](../../README.md) — commands, options, defaults, config fields, workflows and data locations (check it against `auto-app <command> --help`).

| Date | Feature | Step | Status | Entry |
|---|---|---|---|---|
| 2026-10-07 | Web frontend **W2**: decide and label in the browser — want to apply / not applying, should / shouldn't per filter or all, set state, reasons, undo; the TUI's keys; `JobBrowser.add_reason` after `B` fixed | web | ✅ W2 done | [2026-10-07-web-w2](2026-10-07-web-w2.md) |
| 2026-10-07 | Web frontend designed (full app next to the TUI; FastAPI + React/Vite; localhost only) and **W1** built: `auto-app web`, the Jobs page (tabs, narrowing, table, detail with every verdict), keys, URL state | web | ✅ W1 done | [2026-10-07-web-w1](2026-10-07-web-w1.md) |
| 2026-10-07 | Step 4 fixes: the launchd service's `PATH` includes where `uv` / `claude` are (and `~/.local/bin`), so filters run under `serve install`; `serve resume` after a stop now reaches a running loop (the backlog is judged at the next tick, not only at start-up) | 4 | ✅ done | [2026-10-07-serve-fixes](2026-10-07-serve-fixes.md) |
| 2026-10-06 | Step 4: jobs expire by themselves (any refresh of job dates archives / deletes, not only `data purge`); `serve` runs the retention once a day (`retention: 24h`); expiry texts corrected (un-recommended = archived) | 1, 4 | ✅ done | [2026-10-06-step4-cleanup](2026-10-06-step4-cleanup.md) |
| 2026-10-06 | Step 4 **R2: the manager in Claude Code** — project skills (`job-briefing`, `job-decisions`, `filter-feedback`, `pipeline-control`, `troubleshooting`, `application-tracking`); `--json` everywhere it's needed, `job list --since/--search/--not-notified`, `job mark --reason`, `filter label`, `job notified`; settings allow read-only commands and deny `.env` / `profile/` | 4 | ✅ done | [2026-10-06-step4-r2-manager](2026-10-06-step4-r2-manager.md) |
| 2026-10-06 | Step 4 **R1: `auto-app serve`** — rounds that collect until **caught up** (or a time cap spread across searches; also `run --time-limit`), a **filter batch** per round, a pause from the end of collecting (the next round waits for the batch); no usage → wait, unjudgeable jobs, a broken filter → notify and stop; `serve run / status / pause / resume / install` | 4 | ✅ done | [2026-10-06-step4-r1-serve](2026-10-06-step4-r1-serve.md) |
| 2026-10-06 | Design: the **manager agent = Claude with skills** over the app's CLI — what you'd do with it, which commands already cover it, the gaps (`--json`, `filter label`, `job mark --reason`, `job list --since/--search`, `serve`, snooze), 8 skills, rules (never submits; preview → confirm; no secrets) | 4 | 📐 designed | [2026-10-06-manager-skills-design](2026-10-06-manager-skills-design.md) |
| 2026-10-05 | Design: Step 4's **schedule in rounds** — a round browses until **caught up** (or a time cap), then a **fixed pause from the end of collecting**, the next round also waiting for its jobs to be **judged** (the gap adapts); no usage → wait, unjudgeable jobs marked, a broken filter → notify at once and stop | 4 | 📐 designed | [2026-10-05-step4-schedule-design](2026-10-05-step4-schedule-design.md) |
| 2026-10-05 | **Automated applying off** (`AUTO_APP_APPLY__AUTOMATED=false`, the default): the app collects, filters and recommends; the Apply section is just Queued — `enter` opens the link and your CV, `a` applied, `d` not applying; the agent / submit code is kept for later | 3 | ✅ done | [2026-10-05-automated-applying-off](2026-10-05-automated-applying-off.md) |
| 2026-10-05 | Design: **Step 4 — Run** — `auto-app serve` (launchd KeepAlive): collect cycles (e.g. 30 min on / 30 off) in your window, new jobs filtered at once (per search, a filter queue), a Slack **digest** every N min with buttons; a **manager agent** (Opus 5.5, our tools only, Slack DM) for feedback, schedule and applying — Apply starts the trial run, Submit only on your explicit go | 4 | 📐 designed | [2026-10-05-step4-design](2026-10-05-step4-design.md) |
| 2026-10-05 | The agent **fills (`f`) and submits (`s`)**; **starting a job is a trial run** through every step (sign-in, Next) that stops before the final submit; a submit counts only with a confirmation (none → Action needed, never a second applier); `f` / `s` on Action needed | 3 | ✅ done | [2026-10-05-agent-fill-submit](2026-10-05-agent-fill-submit.md) |
| 2026-10-05 | The agent **signs in / creates site accounts** with your email and Keychain password (`apply account set`, `fill_secret` — never seen by Claude; `apply accounts`); **live progress** on the Apply status line; clicks through overlays | 3 | ✅ done | [2026-10-05-agent-accounts-progress](2026-10-05-agent-accounts-progress.md) |
| 2026-10-05 | Step 3 **A7.1**: the browser-agent applier `agent` starts jobs — Opus 5.5 on your Claude Code login (Agent SDK), only our tools (snapshot / click / type / select / check / upload, answer_for, ask_user, done), guards in code (no submit outside `s`, no Next in a dry run, stay on the site, sensitive answers only yours), a last check, time limit, a log per run; **the default applier** (after Greenhouse's API applier on Greenhouse) | 3 | ✅ done | [2026-10-05-step3-a7-1-agent](2026-10-05-step3-a7-1-agent.md) |
| 2026-10-05 | Design: Step 3 **browser-agent applier** (A7) — Claude Opus 5.5 in a loop with generic browser tools, our guards (no submit outside `s`, no Next in a dry run, sensitive answers only from answers.yaml, secrets never shown to the model) and your answers; accounts created with your info; time limit, no token budget | 3 | 📐 designed | [2026-10-05-agent-design](2026-10-05-agent-design.md) |
| 2026-10-05 | `auto-app ui` layout: Jobs tabs All · Not evaluated · **New recommended** · **All recommended** · Un-recommended · Applied · Not applied (no Want to apply — it's Apply → Queued); a decision moves the job out at once; **Inbox renamed Apply** (F3); a short key bar (the tab's main keys, `?` for all) | 2–3 | ✅ done | [2026-10-05-ui-layout](2026-10-05-ui-layout.md) |
| 2026-10-04 | Step 3: **starting a job is a dry run** — the form filled headless and read back (required + empty → Needs input, all filled → Ready to submit); **automated Simplify** (`simplify_auto`) built: Start Application, "Essentials only" consent, Autofill, gaps filled from profile / answers | 3 | ✅ done | [2026-10-04-step3-dry-run-simplify-auto](2026-10-04-step3-dry-run-simplify-auto.md) |
| 2026-10-04 | Step 3 A2 + A3: questions answered from profile / answers.yaml / Claude CLI (sensitive only from answers.yaml), **Needs input** tab; **Greenhouse applier** (API questions, browser fill + submit, verified on a real form without submitting), Ready to submit, Action needed | 3 | ✅ done | [2026-10-04-step3-a2-a3](2026-10-04-step3-a2-a3.md) |
| 2026-10-04 | `auto-app ui`: Jobs (F1), Audit (F2), Inbox (F3) in one app — section bar with what's waiting, state kept per section; `job browse` / `filter audit` / `apply inbox` open it on their section | 2–3 | ✅ done | [2026-10-04-auto-app-ui](2026-10-04-auto-app-ui.md) |
| 2026-10-04 | Step 3 A1: profile / answers / apply policy, applier chains with fallback, **Simplify hand-off** + manual, `apply queue / run / inbox / list` | 3 | ✅ done | [2026-10-04-step3-a1](2026-10-04-step3-a1.md) |
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
| Step 3 A4: Lever and Ashby appliers · A5: auto-submit with pace limits, notifications | 3 | [step3 §10](../step3-apply.md) |

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
