# auto-application — Design Overview

A personal pipeline that finds job postings, picks the ones worth applying to, and prepares (and eventually submits) tailored applications.

| Step | Doc | Question it answers | Status |
|---|---|---|---|
| 1 | [step1-collect.md](step1-collect.md) | *What jobs are out there?* Collect postings from job sites and company career sites, store them, and turn them into one clean, deduplicated list of jobs | Raw collection working for LinkedIn; plugins, parse, DB, dedup designed |
| 2 | [step2-select.md](step2-select.md) | *Which of them should I apply to?* Your filters (a command + settings, one folder each): an agent runs the command on each job (and may look things up), OR-ed with each filter's preference model; you decide | S1–S3 implemented (filters, agent judgment, review in `job browse`); preference model open |
| 3 | [step3-apply.md](step3-apply.md) | *How do I apply?* Tailor the CV per job and submit, with a human approving | Designed |

Implementation progress, one entry per feature: [updates/](updates/README.md).

---

## 1. Pipeline at a glance

```
                    STEP 1 — COLLECT                                   STEP 2 — SELECT        STEP 3 — APPLY
┌────────────────────────────────────────────────────────────┐   ┌──────────────────┐   ┌─────────────────────┐
│ sources (plugins)                                           │   │ agent + command  │   │ tailor CV (+ letter)│
│  LinkedIn · company career sites · ATS platforms (API)      │   │ preference model │   │ verify no fabricat. │
│        │                                                    │   │ you decide       │   │ review queue (you)  │
│        ▼                                                    │   │                  │   │ submit per platform │
│ collect raw ─► parse ─► normalize ─► dedup (L1a/L1b/L2) ─► jobs ─► shortlist ────────► applied            │
└────────────────────────────────────────────────────────────┘   └──────────────────┘   └─────────────────────┘
```

## 2. Principles

1. **Stages share only the database.** Each stage reads rows in one state and moves them to the next. Any stage can be re-run alone; a crash just leaves rows in their current state.
2. **Posting ≠ job.** A *posting* is one listing on one site; a *job* is the real opening. Dedup links postings to jobs. Steps 2 and 3 work on jobs.
3. **Store raw first, interpret later.** Collection saves what the site delivered; parsing is a separate, re-runnable stage. A parser bug never loses data.
4. **Be the user's browser, never a disguised bot.** Real logged-in sessions, human-like pacing, hard limits. On a login wall, CAPTCHA, or block, stop that source (optionally hand the check to the user) — never evade detection.
5. **A human approves every application** until the system has earned trust; nothing is fabricated on a CV.
6. **Personal data stays local and inside the project folder**, git-ignored (config, browser logins, database, CVs).

## 3. Job state

Every collected job is in the **pool**. Step 2 evaluates pool jobs and **recommends** a subset — your list; you decide on each recommended job in review. A job's state is two fields:

| # | State | `step2` | `decision` | Set by |
|---|---|---|---|---|
| 1 | not evaluated | — | — | Step 1 (dedup) |
| 2 | **un_recommended** — evaluated, left out (reason kept) | `un_recommended` | — | Step 2 |
| 3 | **recommended, undecided** | `recommended` | — | Step 2 |
| 4 | **want to apply** — queued for Step 3 | `recommended` | `want_to_apply` | you, in review |
| 5 | recommended, **applied** | `recommended` | `applied` | Step 3 (on submission) |
| 6 | recommended, **not applied** | `recommended` | `not_applied` | you, in review (or withdrawing from the queue) |

```
pool ──(step 2)──► un_recommended ──(you, in review: "should have been recommended")──┐
  │                                                                                   ▼
  └──(step 2)──► recommended ──(you: apply)──► want_to_apply ──(step 3 applies)──► applied
                     │                               │
                     └──(you: no)──► not_applied ◄───┘ (you withdraw)
```

- **Review** (`auto-app job browse`, [step2 §8](step2-select.md#8-review)) records your decision — `want_to_apply` or `not_applied` — and, separately, per-filter feedback on whether each verdict was right (the preference models' labels).
- **`want_to_apply` is a queue.** Step 3 picks queued jobs up, applies, and sets `applied`. *How* the queue is worked is your choice — one at a time, one every N minutes, at most N a day, in an order you pick ([step3 §1](step3-apply.md)).
- Step 3's progress on one application (tailoring, your approval, submission attempts) lives on its application record, not on the job.

Implemented in C11 (Step 1): the two fields, the TTL and archiving below; `want_to_apply` and review in Step 2 S3. `auto-app job mark JOB… --as recommended | un_recommended | want_to_apply | applied | not_applied | not_evaluated` sets them by hand (until Step 3 exists, also `applied`).

**Job TTL** (global, `AUTO_APP_JOBS__TTL_DAYS__*` in `.env`; clock = time since any of the job's postings was last seen; details in [step1 §9](step1-collect.md#9-retention-ttl)):

| State | Default TTL | After the TTL |
|---|---|---|
| not evaluated | 14 days | **deleted** with every linked posting (never judged — not training data) |
| un_recommended | 7 days | **archived** |
| recommended (undecided, applied, not applied) | 30 days | **archived** |
| **want to apply** | **none** — never expires while queued | stays until Step 3 applies (→ applied) or you withdraw it (→ not applied); Step 3 tells you if its posting has gone |

**Archived jobs are training data** (planned, Step 2 S4 — today un_recommended jobs are deleted): every **judged** job (recommended or un_recommended) is archived after its TTL, never deleted; jobs never judged are deleted, as they aren't training data. An archived job keeps what a filter reads (title, company, locations, description, …), its Step 2 verdicts, your labels and decision, and its matching keys; only raw captures are dropped. Archived jobs leave the active pool: hidden from `job list` / `job browse`'s active tabs, never judged again, and **never merged with active jobs**. Among themselves they **are deduplicated** (when a job is archived), so the training data holds each job once. A listing seen again after its job was archived is collected as a **new job** (judged again); one that looks like an archived job you applied to / declined gets a warning ("looks like job #12, which you applied to on …").

## 4. Tech stack

| Concern | Choice |
|---|---|
| Language / env | Python 3.12, **uv** |
| Browser automation | **Playwright** (async) driving installed **Google Chrome**, persistent profile per source |
| HTTP (APIs, redirects) | **httpx** |
| Database | **SQLite** (WAL) via **SQLAlchemy 2.0 Core**, **Alembic** migrations |
| Models / config | **Pydantic v2**, YAML |
| Near-duplicate text | **datasketch** (MinHash/LSH), **rapidfuzz** (titles) |
| LLM (steps 2–3) | Claude API (model chosen per task; see step 2) |
| CV rendering (step 3) | template → PDF (Typst or HTML → PDF) |
| CLI | **Typer** (`auto-app …`) |
| Tests | **pytest**, **pytest-asyncio**, local fake job site, saved fixtures |

## 5. Project layout

```
auto-application/
├── pyproject.toml, uv.lock, README.md
├── config/
│   ├── searches.example.yaml        # template (committed)
│   └── searches.yaml                # your config (git-ignored)
├── plugins/                         # source plugins discovered at startup (step 1)
│   ├── platforms/                   #   ashby.py, greenhouse.py, lever.py, workday.py …
│   └── companies/                   #   microsoft.py, google.py …
├── src/auto_app/
│   ├── cli.py, settings.py
│   ├── core/                        # config, models, db, logging, notify — no site knowledge
│   ├── sources/                     # plugin interface + built-in plugins (linkedin, handshake, jobright)
│   ├── collect/                     # step 1a: collection engine
│   ├── process/                     # step 1b: parse, normalize, dedup, purge; `job browse` (the full-screen app)
│   ├── select/                      # step 2: filters, job details, agent runners, judgment, review answers
│   └── apply/                       # step 3: tailor, verify, render, review, submitters
├── tests/                           # mirrors src/; fakesite/, fixtures/<source>/
├── docs/                            # these design docs
├── filters/<name>/                 # your Step 2 filters (git-ignored); examples in filters.example/
├── browser-profiles/<source>/       # saved logins (git-ignored)
└── data/                            # git-ignored
    ├── auto_app.db                  # the database (planned; today: data/raw/ files)
    ├── raw/                         # current file-based raw store
    ├── screenshots/, failures/, logs/
    └── output/tailored/<job_id>/    # step 3 outputs
```
