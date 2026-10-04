# Step 2 — Select

**Goal:** from the pool of collected jobs, recommend the ones worth applying to, as **a list for you to decide on** — judged the way **you** describe it, and learning from your decisions.

```
filters/<name>/  (one folder = one filter; you can have several)
  command.md  filter.yaml  [parser.py]  [other files]          preference model (one per filter)
          │                                                              │
jobs ─────┴─► agent runs the command on the job ─► answer ─► parser ─► recommend? ──┐
                                                                                     OR ─► the filter recommends?
                                         preference model (later; design open) ─► recommend? ──┘
                                                                                          │
                                       job recommended  =  any filter that judged it recommends ─► you: apply / no
                                                                                                        │
                                                                     your decisions train each filter's preference model
```

Status: S1 ✅ (filters: loading, validation, versions, job details, `filters`); S2 ✅ (agent runner, parsing, `select`, `why`, `--reparse`); S3 ✅ (review in `auto-app job browse`: decisions, per-filter feedback, `want_to_apply` queue). Design simplified 2026-10-01/02; replaces the earlier hard-filter + scorer design. Focus: the agent part; the preference model's design is open. Depends on Step 1 (parsed jobs with descriptions; job state from C11).

---

## 1. Principles

1. **No rule stage.** Step 1's searches already apply what the sites can filter (location, job type, experience, date …). Everything else is judged by the agent from the job text — and, where your command asks for it, from what it looks up.
2. **A filter is a folder you write.** Its **command** (`command.md`) says, in your own words, how to judge and when to recommend, and is run for **every** job. Its `filter.yaml` says which job details to send and how the agent runs. Any other files in the folder come with it (reference notes, an optional parser). Everything in the folder identifies the filter's version.
3. **The app doesn't define how to judge.** No built-in scores, thresholds or deal-breaker rules. It only needs to know, from each answer, **recommend or not** and **why** — read through our output convention, or your own parser.
4. **Two judges per filter, OR-ed:** the agent running the command, and the filter's own **preference model**, trained on your decisions. The agent never sees the preference model's output.
5. **High recall.** Step 2 should rarely drop a job you might want: every job you review becomes a training label, and each preference model needs many and varied ones — rejects included. When unsure, recommend; when an answer can't be read, show it to you.
6. **You decide.** Step 2 only sets `step2` (`recommended` / `un_recommended`); `decision` is yours — set in review (§8): `want_to_apply` (queued for Step 3) or `not_applied`.
7. **An agent, so a command can use information beyond the posting** (§6) — bounded by read-only tools, a per-job budget, and no access to LinkedIn.

---

## 2. A filter — `filters/<name>/` (git-ignored)

```
filters/
  ml-engineer/
    command.md              # required: the prompt run for every job
    filter.yaml             # required: job details, agent settings, applies_to
    parser.py               # optional: your own way to read the answer (§3.2)
    sponsorship-notes.md    # optional: any other files the command refers to
  research-scientist/
    command.md
    filter.yaml
```

The folder name is the filter's name. `auto-app filter list` lists them; an example filter is committed under `filters.example/`.

### 2.1 `command.md` — run for every job

In the style of Claude Code's custom slash commands: a markdown file, optional front matter (`description`), and the prompt as the body. **`$ARGUMENTS`** marks where the job details go (if it's missing, they're appended at the end). Unless the filter has a `parser.py`, the command must make the agent end with **our output convention** (§3.1).

```markdown
---
description: Screen ML engineer / applied scientist jobs for me.
---
Decide whether I should consider applying to this job. If unsure, recommend.

## My background
Python, PyTorch, CUDA basics, distributed training (DDP, FSDP), LLM fine-tuning (LoRA), MLOps (Docker, k8s).
M.S. CS, 2 ML internships (inference latency, recommendation models).

## What I want
Entry-level ML engineer / applied scientist; applied ML or ML systems over pure research; production work.
US, any city; remote or hybrid preferred.

## Deal-breakers
- No visa sponsorship (I need H-1B sponsorship). If the posting doesn't say, check whether the company has
  sponsored before (see sponsorship-notes.md for how).
- Security clearance or US citizenship required
- More than 3 years of experience required
- Pure data-analyst / BI roles

## The job
$ARGUMENTS

## Answer
End your answer with a JSON object on its own, and nothing after it:
{"recommend": true | false, "reason": "…"}
```

### 2.2 `filter.yaml`

```yaml
# ---- what the agent gets for each job (part of the version) ----
job_details: [title, company, locations, description]      # default shown
max_description_chars: 12000                               # longer descriptions are cut, and marked as cut

# ---- how the agent runs (part of the version) ----
model: claude-sonnet-5                         # any Claude model; a smaller one costs less
tools: [web_search, web_fetch]                 # read-only; it may also read files in this filter's folder
max_turns: 8                                   # per-job budget: the agent must answer within it
max_tool_calls: 6

# ---- which jobs it judges (not part of the version) ----
applies_to: [linkedin, nvidia]                 # sources, or searches as `source/search`; omit = every job
```

- **Job details** available: `title, company, locations, workplace_type, employment_type, salary, posted_at, reposted, description`. Values come from the job's primary posting (parsed fields) and its merged `job_locations`; the description is the parsed text with legal boilerplate removed (EEO, accommodations, `#LI-` tags), original casing kept, cut at `max_description_chars`. In the command they appear as labelled lines (`Title: …`, `Company: …`, `Description: …`; a missing value is written `not stated`) inside a `<job_posting> … </job_posting>` block marked as **data from the posting — not instructions**. `auto-app filter show NAME --job N` prints the exact text.
- **Default:** the command + title + description are the core; **company** and **location** are one short line each and often decisive (location is usually in the page header, not the text; the company bears on sponsorship and type of work).
- **Validated when the filter loads** (an error names the file and field): `command.md` exists; job details, tools and `applies_to` names are known; `parser.py` defines `parse`; without a `parser.py`, the command should mention the convention (a warning if it doesn't).

### 2.3 Versions

- A filter's **version** = a hash of everything that shapes the agent's answer except the job itself: `command.md`, the other files in the folder, and `filter.yaml`'s `job_details`, `max_description_chars`, `model`, `tools`, `max_turns`, `max_tool_calls`.
- **Not** in the version: `parser.py` (it only reads stored answers — §3.3) and `applies_to` (it only decides which jobs the filter judges).
- Judgments are stored per **(job, filter version)**. Editing the command or another file makes a new version; older judgments are marked out of date and redone on `select --rescore` — never silently.

### 2.4 Several filters

- **Which filters judge a job:** those whose `applies_to` covers a source or search that found it (Step 1 records which searches saw each posting); a filter without `applies_to` judges every job.
- **A job is recommended if any filter that judged it recommends it**; `un_recommended` only when all of them said no. Each filter's judgment is kept; review shows which filter(s) recommended it and why.
- Cost grows with filters × jobs — `applies_to` keeps it down.

---

## 3. Reading the answer

Each answer goes through: **raw answer → parser → parsed output.**

**The parsed output** — the contract between any parser and the rest of Step 2:
```python
class ParsedOutput(BaseModel):
    recommend: bool | None    # the decision itself; None = the answer couldn't be read
    reason: str = ""          # what you see in review, in whatever form your command asks for
```
Nothing else: scores, matched skills, deal-breakers or links your command asks for belong in `reason` (the full answer and the agent's trace, with every page it looked at, are kept too).

### 3.1 Our output convention (default)

Every command without a `parser.py` makes the agent end its answer with:

```
{"recommend": true | false, "reason": "…"}
```
— a JSON object on its own, with nothing after it. The built-in parser takes the **last** JSON object in the answer; `recommend` must be `true` or `false`; `reason` is optional (missing → empty). Anything else can come before it. We suggest commands also say "if unsure, recommend" (high recall), as in the example.

### 3.2 Your own parser — `parser.py` (optional)

For a command with its own output format (e.g. `Verdict: apply | maybe | skip` and `Reason: …`):

```python
# filters/research-scientist/parser.py
def parse(answer: str) -> dict:
    """Return {"recommend": bool, "reason": str}; raise ValueError if the answer can't be read."""
```
- It runs in the app, on the stored answer; anything it can't read → raise, and the result is "couldn't read".
- Try it on stored answers before switching: `select --reparse --filter NAME --dry-run`.

### 3.3 Answers are kept raw

- **Stored per (job, filter version):** the agent's **raw final answer**, the parsed output with the parser that produced it (built-in or `parser.py`, by hash), the model, the full **trace** (every tool call and a summary of its result), token and tool counts, cost.
- **Changing or adding a `parser.py` re-reads the stored answers** (`select --reparse`) — no new agent runs, no cost.
- A run that fails is recorded and retried; an answer that can't be read gets `recommend = None` (fix the command or parser, then `--reparse` or `--rescore`). Neither is ever counted as "no".

---

## 4. Agent run

One agent run per job per applicable filter, started with the filter's `command.md`, the job details in place of `$ARGUMENTS`.

**Runner: our own small loop on the Claude API** (Messages API with tool use) — chosen 2026-10-02 over the Claude Agent SDK. The agent here is deliberately narrow, so a minimal loop with exactly the tools it needs is safer and more predictable than a general-purpose coding agent with most of it switched off:

| Tool | What it is |
|---|---|
| `web_search`, `web_fetch` | the API's server-side tools, with **linkedin.com blocked by domain** |
| `read_file` | ours: reads a file in the filter's own folder only (path checked; no other files) |

The loop: send the command + job → the model calls tools → results go back → repeat until it answers, or `max_turns` / `max_tool_calls` is reached (then it's told to answer with what it has). Implemented in `src/auto_app/select/runner.py` with the SDK's `web_search_20250305` / `web_fetch_20250910` tool definitions (`blocked_domains: [linkedin.com]`, `max_uses` = the remaining tool budget); when the budget is used up, the next call sets `tool_choice: none` and adds a wrap-up message; `pause_turn` hands the turn back unchanged. A short system message frames the posting and web pages as data and forbids LinkedIn — it doesn't say how to judge. Cost = tokens × `AUTO_APP_SELECT__PRICES` + searches × `AUTO_APP_SELECT__WEB_SEARCH_USD_PER_1000` (both in `.env`); `--max-cost` is checked before each run starts, so runs already in flight can go slightly over. The app counts tokens and cost per run, records the trace, retries transient API errors, and stops a `select` run at `--max-cost`. Tested offline with a fake model (as Step 1 tests collection against a fake site). If filters ever need much more (running scripts, many tools), the runner can be swapped for the Agent SDK without changing filters, parsing or storage.

**Second runner: your Claude Code login** (added 2026-10-03). `AUTO_APP_SELECT__RUNNER=auto` (default) uses the API runner when `ANTHROPIC_API_KEY` is set and otherwise runs `claude -p` (headless Claude Code, `src/auto_app/select/runner_cli.py`) — usage then counts against your Claude subscription. **Every call is a new session with no context from earlier calls:** a fresh `--session-id` (UUID), `--no-session-persistence`, `--setting-sources ""` and our own system prompt (no settings or memory), and a new temporary folder outside the project per call, holding only copies of the filter's other files and deleted afterwards — so no `CLAUDE.md` or other project file can be picked up (checked: with these flags, even an ancestor `CLAUDE.md` wasn't used). It is locked down to the same abilities, verified live with Claude Code 2.1: `--tools WebSearch,WebFetch,Read` (per the filter), `--permission-mode dontAsk`, `Read(./**)` allowed in the filter's folder (cwd) except `command.md` / `filter.yaml` / `parser.py`, `WebFetch(domain:*linkedin.com)` denied, `--setting-sources ""`, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`, and `ANTHROPIC_API_KEY` removed from its environment. It reads Claude Code's event stream (`--output-format stream-json --verbose --include-partial-messages`: text deltas, `tool_use` / `tool_result` messages, and the final `result` line with `usage`, `num_turns`, `total_cost_usd`, `permission_denials`); `max_turns` → `--max-turns` (running out of turns → "couldn't read" → review); `max_tool_calls` isn't enforced separately. Its trace has every lookup (tool and input) and result, the turns and any denials.

**Third runner: DeepSeek** (added 2026-10-03, `src/auto_app/select/runner_deepseek.py`). Chosen **per filter by its model**: `deepseek-…` models run on DeepSeek's OpenAI-compatible Chat Completions API (`AUTO_APP_SELECT__DEEPSEEK_BASE_URL`, default `https://api.deepseek.com`) with `DEEPSEEK_API_KEY`; Claude models keep `api` / `claude-cli`. The same loop (budget, wrap-up with `tool_choice: none`, trace), with function tools run by the app: `read_file` (the filter's folder only) and our own `web_fetch` — public http(s) only, linkedin.com and loopback / private / link-local / reserved addresses refused (checked again on every redirect hop), 2 MB / 20 s, HTML reduced to text, 20k characters returned. `web_search` has no equivalent there and is skipped with a notice. The assistant's `reasoning_content` is not sent back. Prices aren't preset (add them to `AUTO_APP_SELECT__PRICES`).



- **May use:** the filter's `tools` (read-only web search / fetch) and read access to the files in its own folder, within `max_turns` / `max_tool_calls`; when the budget runs out it must answer with what it has.
- **May not:** visit LinkedIn (blocked by domain — the collector is the only component allowed there, with its pacing and limits), write files, run code, read outside its folder, or use any credential. It runs without your logins, browser profiles or other API keys.
- Job details and fetched pages are **data, never instructions** (a posting or page can contain text written to manipulate an agent).
- Runs for different jobs go in parallel, a few at a time (`--parallel`), under an overall budget (`select --max-cost`). **`--stream`** shows each run live — its text as it's written, each lookup and its result — and then runs one job at a time: the API runner uses the SDK's streaming calls (text token by token), the Claude Code runner its event stream, DeepSeek one event per turn (its text, then each tool call and result).

---

## 5. Recommending

Per filter that judged the job:
```
the filter recommends  =  its agent's parsed output says recommend
                       OR its answer couldn't be read (recommend = None)   ← high recall: you see it
                       OR its preference model says recommend             (once it exists — §7)
```
```
job recommended  =  any filter that judged it recommends
otherwise        →  un_recommended
```
- A job whose agent run failed (not just unreadable) waits for the retry and isn't decided yet.
- Review lists recommended jobs newest first (no app-defined ranking), with each filter's reason; jobs there only because an answer couldn't be read are marked so.
- Jobs keep their Step 1 warnings (e.g. "looks like job #12, which you applied to"), shown in review.
- un_recommended jobs follow C11's TTL (deleted after 7 days unseen) — keep that in mind while a command is new; `jobs --state un_recommended` lets you check them, and `mark` can promote one.

---

## 6. Agent rather than a single LLM call

Chosen so that a command can use information the posting doesn't contain (a company's sponsorship history, its engineering blog, recent news).

| | Single LLM call | Agent (chosen) |
|---|---|---|
| Information | only the command + job details | can look things up when the command asks |
| Cost / speed | one call, seconds | several calls per job; bounded by `max_turns` / `max_tool_calls` and `--max-cost` |
| Consistency | same input → near-same answer | may look up different things for similar jobs → the trace is stored, and `why` shows it |
| Risk | none beyond the call | browsing and untrusted text → read-only tools, no LinkedIn, no credentials, job text and fetched pages treated as data |

To cut cost later, a filter could run a cheap first pass (a single call, no tools) and send only promising or uncertain jobs to the agent — the same command works for both.

---

## 7. Preference model — one per filter (design open)

Fixed so far:
- **One per filter**, learning that filter's taste — different filters stand for different preferences of the same user, so each needs its own model.
- **Its labels are your per-filter feedback in review** (§8): for each job the filter judged, whether its verdict was right — a good or bad recommendation, or rightly or wrongly left out. Never the agent's output. Your apply / not-apply decision is stored too, as a weaker, per-job signal (you may skip a good job for other reasons).
- Tied to the filter's **name**, not its version, so it keeps learning across command edits.
- It judges jobs independently of the agent; its yes/no is OR-ed with the agent's (§5).
- Its trained state lives in `data/` (with the rest of the app's data), not in the filter folder.
- Feedback is stored from the start (§8–9), so it will have data when it's built. Review shows un-recommended jobs too, so labels aren't only about jobs that were already recommended.

Open: its inputs, model type, and when it's trusted enough to recommend.

---

## 8. Review

You review in **`auto-app job browse`**, a **full-screen terminal app** (built with [Textual](https://textual.textualize.io/)) over the whole job pool: decide on recommended jobs and tell each filter whether it judged right. It has a tab per state; the three review tabs are below (`job browse --state recommended` opens on the first). It started as a separate `filter review` command and was merged into `job browse` (2026-10-04), since its lists were a subset of the pool's tabs.

```
┌ Recommended (23) · Un-recommended (41) · Want to apply (3) ─┬ #28 Research Scientist, AI Accelerator … ───────────────┐
│ ▸ #28 NVIDIA      Research Scientist, AI Accel…   14d       │ NVIDIA · Austin, TX; Santa Clara, CA · posted 2026-02-05 │
│   #24 ByteDance   Visual Generation & Multimod…   13d  👍   │ expires in 14 days · found on nvidia, linkedin           │
│   #22 ByteDance   Research Scientist Graduate…    13d       │                                                          │
│ ★ #17 RemoteHunter Data Scientist, Payments       13d  👍   │ [1] ml-engineer   recommend · 👍                         │
│ ✗ #15 Amazon      Applied Scientist, Worldwide…   12d  👎   │     strong ML systems fit; sponsors H-1B                 │
│   …                                                         │     looked up: WebSearch "NVIDIA H-1B sponsorship"       │
│                                                             │ [2] research      no · (not rated)                       │
│                                                             │     PhD-only research role                               │
│                                                             │ Description ─────────────────────────────────────────    │
│                                                             │ NVIDIA Research is searching for a world-class …         │
├─────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────┤
│ ↑↓ jobs  tab tab  ←→/1–9 filter  g right  b wrong  a want to apply  n not apply  m state  u undo  o link  / search  q quit │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Review tabs** (`tab` / `shift+tab`; among All, Not evaluated, Applied, Not applied, Archived): **Recommended** — undecided recommended jobs, the ones to decide on (default); **Un-recommended** — jobs every filter turned down, to catch wrong rejections; **Want to apply** — your queue, to review or withdraw. Each list is newest first, searchable (`/`), with marks for what you've answered: ★ want to apply, ✗ not applied, 👍 / 👎 your feedback. A job you answer stays in the list you answered it in (with its mark) until you leave the app, so you can change your mind; `-f NAME` limits the app to jobs those filters judged, and to their verdicts.

**Detail pane** (the selected job; scrolls): what `job show` prints — company, locations, dates, expiry, postings, matching keys, Step 1 warnings ("looks like job #12, which you applied to"), **every filter that judged it** — its verdict, reason, what it looked up, and your feedback so far — then the full description. `o` opens the apply link in your browser.

**Two separate answers**, both optional, saved the moment you press a key:
1. **Your decision, per job:** `a` → **want to apply** (queued for Step 3), `n` → **not applied**. Available in every list — choosing `a` on an un-recommended job also tells its filters they were wrong to leave it out unless you've rated them otherwise. `u` undoes the last answer.
2. **Feedback, per filter:** pick a filter (`←` `→` or its number; with one filter, no picking needed) and press `g` (its verdict was **right**) or `b` (**wrong**). On a filter that recommended: good / bad recommendation. On one that said no: rightly / wrongly left out. Each rating is a label for **that filter's** preference model only (§7). A short reason can be added (`r`) to either answer.

Nothing is submitted anywhere: `want_to_apply` only queues the job; Step 3 applies.

Also: `auto-app filter why <job>` (each filter's version, the agent's full answer, the parsed output, and the trace with every page it looked at).

---

## 9. Storage

```sql
filter_versions (filter_version PK, filter, files JSON, settings JSON, created_at)   -- exact command + files run
judgments       (job_id FK, filter, filter_version, model, answer TEXT,               -- the raw final answer
                 recommend BOOLEAN, reason TEXT, parser TEXT, parse_error TEXT,       -- parsed output; NULL = couldn't read
                 trace BLOB(zstd), tokens, tool_calls, cost_usd, status, created_at, parsed_at,
                 PRIMARY KEY (job_id, filter_version))
reviews         (review_id PK, job_id, decision, reason, job_text TEXT, created_at)  -- your decisions (per job)
feedback        (job_id, filter, filter_version, verdict BOOLEAN,                    -- the filter's verdict you rated …
                 correct BOOLEAN, reason TEXT, job_text TEXT, created_at,           -- … right / wrong = a label
                 PRIMARY KEY (job_id, filter))                                       -- latest rating per job and filter
-- later: preference model state per filter (data/), its scores per job
```
Review sets `jobs.decision` / `decided_at` (`want_to_apply` or `not_applied`; Step 3 sets `applied` on submission). `reviews` and `feedback` rows carry the job's text (title, company, description) and have no foreign key to `jobs`, so labels outlive the TTL. Archived jobs (C11) keep their Step 2 verdict in the archive record.

---

## 10. CLI

```
auto-app filter list [--dir PATH]                                 # your filters: version, model, tools, problems (S1 ✅)
auto-app filter show <NAME> [--job N] [--dir PATH]                # one filter; --job: exactly what the agent gets (S1 ✅)
auto-app filter run [-f NAME]… [-j JOB]… [--stream] [--limit N] [--rescore] [--max-cost USD] [--parallel N] [--dry-run]   # judge jobs (S2 ✅)
                                                                  #   progress: [n/N] per judgment; live bar (done, cost, time left, running jobs) in a terminal
auto-app filter why <JOB_ID> [--trace]                            # each filter's answer, lookups, cost, version (S2 ✅)
auto-app filter reparse [-f NAME]… [--dry-run]                    # re-read stored answers with the current parsers (S2 ✅)
auto-app job browse [--state TAB] [-f NAME]… [-s SOURCE]… [-c TEXT]   # review (and browse): decide, rate each filter's verdict (S3 ✅)
```
`auto-app job mark` (C11) still sets any state by hand, `want_to_apply` included.

---

## 11. Milestones

| # | Deliverable |
|---|---|
| S1 | Filters: folder loader + validation (`command.md`, `filter.yaml`, optional `parser.py`, other files), versions, job-details builder (labelled lines, boilerplate stripped, length cap), `$ARGUMENTS` substitution; `filters` command (list, check, `--job` preview); `filters.example/` committed — ✅ `src/auto_app/select/` |
| S2 ✅ | Agent judgment: agent runners — our loop on the Claude API, headless Claude Code with your login (`runner: auto`), and DeepSeek for `deepseek-…` models (server-side web search / fetch with LinkedIn blocked, `read_file` limited to the filter's folder, per-job budget), raw answers + traces + cost in `judgments`; built-in convention parser and `parser.py`, `--reparse`; several filters (any recommends); `select` (parallel, `--max-cost`), `why` — first tried on ~30 saved jobs |
| S3 ✅ | Review app (Textual; merged into `job browse`): Recommended / Un-recommended / Want to apply lists, detail pane, decisions (`want_to_apply` / `not_applied`), per-filter feedback (`feedback` table), undo, open link; `want_to_apply` state + no-expiry rule; data layer tested without the UI, the app with Textual's test driver — `src/auto_app/select/review.py`, `process/job_browser.py` |
| — | Preference model per filter, trained on its feedback: design open (§7) |

## 12. Risks

| Risk | Mitigation |
|---|---|
| The agent rejects good jobs, and C11 deletes them after 7 days | high recall: "if unsure, recommend" in commands; unreadable answers go to review; check `jobs --state un_recommended` early on; consider a longer `ttl_days.un_recommended` while a command is new |
| An edit silently changes results | every change to the folder (except `parser.py`, `applies_to`) is a new version; old judgments marked out of date, redone only on `--rescore` |
| A command doesn't follow the convention | `filters` warns when loading; unreadable answers → review, never "no"; fix and `--reparse` at no cost |
| Cost runs away | per-job `max_turns` / `max_tool_calls`, `select --max-cost`, cost stored per judgment; `applies_to`; optional cheap first pass (§6) |
| A posting or web page manipulates the agent (prompt injection) | job text and fetched pages are data, not instructions; read-only tools; no code execution, credentials, file writes or LinkedIn; the answer only feeds a yes/no |
| The agent visits LinkedIn and risks your account | LinkedIn blocked by domain; the agent only sees the job text the collector saved |
| `parser.py` is code from the filter folder | it's your own file, run by the app like a plugin; it only receives the answer text |
| Inconsistent judgments | trace stored; `why` shows what it looked up |

## 13. Open questions

1. The preference model (§7).
2. Should `applies_to` also accept company names (e.g. a filter only for NVIDIA jobs from LinkedIn)?
3. A longer un_recommended TTL by default while a command is new?
4. Shared text between filters (e.g. one sponsorship section used by several commands): an include syntax, or copy for now?
