# Step 2 — Select

**Goal:** from the pool of collected jobs, recommend the ones worth applying to, as **a list for you to decide on** — judged the way **you** describe it, and learning from your decisions.

```
filters/<name>/  (one folder = one filter; you can have several)
  command.md  filter.yaml  [parser.py]  [other files]          preference model (one per filter)
          │                                                              │
jobs ─────┴─► agent runs the command on the job ─► answer ─► parser ─► recommend? ──┐
                                                                                     OR ─► the filter recommends?
                                         preference model (S5; §7) ──────────────► recommend? ──┘
                                                                                          │
                                       job recommended  =  any filter that judged it recommends ─► you: apply / no
                                                                                                        │
                                                                     your decisions train each filter's preference model
```

Status: S1 ✅ (filters: loading, validation, versions, job details, `filters`); S2 ✅ (agent runner, parsing, `select`, `why`, `--reparse`); S3 ✅ (review in `auto-app job browse`: decisions, per-filter feedback, `want_to_apply` queue); S4 ✅ (judged jobs archived as training data, good / bad labels, `filter audit`, `filter eval`). Design simplified 2026-10-01/02; replaces the earlier hard-filter + scorer design. The agent answers fit + dealbreaker (§3.1). The preference model (S5) is deferred — evaluation decides whether it's needed (§7.5). Depends on Step 1 (parsed jobs with descriptions; job state from C11).

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

Every command without a `parser.py` makes the agent end its answer with **two judgments** (since 2026-10-04):

```
{"fit": true | false, "dealbreaker": true | false, "reason": "…"}
```
- **`fit`** — does the job meet your requirements (what you want, your background, preferences)? If unsure, true.
- **`dealbreaker`** — does one of the command's deal-breakers apply? True only when the posting or a lookup **clearly** shows it; if unsure, false (high recall).
- The filter **recommends** the job when it fits and has no dealbreaker (a dealbreaker alone is enough to say no — `fit` may then be left out).

A JSON object on its own, with nothing after it. The built-in parser takes the **last** JSON object in the answer; the values must be `true` or `false`; `reason` is optional (missing → empty). Anything else can come before it. The older `{"recommend": true | false, "reason": "…"}` is still read (fit = recommend, dealbreaker not given — treated as none); `filter list` warns about commands that still ask for it.

Why two parts: a job can fit perfectly and still be ruled out (sponsorship, clearance, years), and the two mistakes differ — a wrong dealbreaker call hides a job however well it fits. Keeping them apart lets evaluation tell them apart (§7.6, "by cause"), and leaves room for a preference model that judges **fit only**, with the agent's dealbreaker as a veto (§7.5, deferred).

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
the filter recommends  =  its agent says the job fits AND finds no dealbreaker (§3.1)
                       OR its answer couldn't be read (recommend = None)   ← high recall: you see it
(deferred, §7.5: … OR its preference model says it fits — never past the agent's dealbreaker)
```
```
job recommended  =  any filter that judged it recommends
otherwise        →  un_recommended
```
- A job whose agent run failed (not just unreadable) waits for the retry and isn't decided yet.
- Review lists recommended jobs newest first (no app-defined ranking), with each filter's reason; jobs there only because an answer couldn't be read are marked so.
- Jobs keep their Step 1 warnings (e.g. "looks like job #12, which you applied to"), shown in review.
- un_recommended jobs follow the TTL (archived after 7 days unseen, as training data — S4); `job browse --state un_recommended` lets you check them, `filter audit` samples them at random, and `m` / `job mark` can promote one.

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

## 7. Labels, evaluation and the preference model

Each filter has **two judges**: its **agent** (§4) and, later, its **preference model** (§7.5). Both are evaluated the same way, against your labels. Evaluation comes first: it measures the agent, and says whether the cheap labels (un-recommended jobs nobody reviewed) are clean enough to train the model on. Design agreed 2026-10-04. **S4 implemented** (labels, audit, evaluation, archived jobs as training data — `select/audit.py`, `select/evaluate.py`, `core/db/job_ttl.py`); the preference model (S5) not yet, so for now each filter has one judge, and the "both-no" region is the agent's "no" pool.

### 7.1 The label

Per **job and filter**: *should this filter recommend this job?* — you answer it directly, whatever the judges said. The app words the keys that way ("should recommend" / "shouldn't"), never as "good / bad", which reads like a grade of the filter's verdict:

| key (per filter, in `job browse` / audit) | label |
|---|---|
| `g` | this filter **should recommend** this job (stored as *good*) |
| `b` | this filter **shouldn't recommend** it (stored as *bad*) |

Each judge's verdict is then **right or wrong by comparison** (recommend + good = right; no + good = a miss; …). The label is about the **job**, not about a judge or a version, so every version of the agent and of the model is scored against the same labels, and labels stay valid when you edit a command or the model retrains. This replaces S3's "this verdict was right / wrong" ratings (which can't work with two judges that disagree); existing ratings convert exactly (recommend + right → good, recommend + wrong → bad, no + right → bad, no + wrong → good). `a` (want to apply) on a job is a **good** label for every filter whose judges all said no (implied). Your apply / not-apply decision stays a weaker, per-job signal.

### 7.2 Verdicts: both judges, kept per job

- **Agent verdicts:** `judgments` (§9), per filter version — as today.
- **Model verdicts:** per filter and **model version** (each retrain is a new version, like each command edit): score, threshold, recommend / no. A verdict is **frozen** once made; a new version adds its own verdicts for the active, undecided jobs it scores and never rewrites old ones — so each version is evaluated on the calls it actually made.
- **A filter recommends a job if its agent OR its model (once switched on, §7.5) recommends it;** a job is recommended if any filter does (§5). `job browse` and `filter why` show both, e.g. `ml-engineer — agent: no (sales role) · model: recommend 0.82`.

### 7.3 Where labels come from — and the bias

- **Recommended jobs are labelled reliably:** you review all of them, labelling each filter. Negatives among them are only *hard* ones (plausible enough to fool a judge).
- **Un-recommended jobs are many, and mostly never read.** Treating them all as "no" adds noise — the judges' **miss rate**, unknown until measured.

Per filter, every job falls in one of four regions:

| agent | model | the filter recommends? | labelled by |
|---|---|---|---|
| yes | yes | yes | you, in review |
| yes | no | yes | you, in review |
| no | yes | yes (model switched on) / Second look (shadow) | you, in review / Second look |
| no | no | **no** | **audit sample** (§7.4) |

Wherever the judges disagree, the filter recommends the job, so you label it anyway. **Only the both-no region needs random audit samples** — one audit stream per filter measures both judges and their combination. Before a model exists, the "both-no" region is simply the agent's "no" pool. Pools are per filter: a job another filter recommended still counts in this filter's region if you haven't labelled it for this filter.

### 7.4 Audit — `auto-app filter audit` (interactive)

The **Audit** section (F2) of `auto-app ui` — the same app as `job browse` — opened in audit mode: **only the Audit tab** — a **random sample** of each filter's both-no region, nothing else (pool tabs would invite labelling jobs that weren't drawn at random, and `job browse` has no Audit tab) — the usual detail pane, and an **evaluation panel** that updates the moment you label a job. Its footer lists only what's involved in an audit: no decisions (`a` / `n`), states (`m`) or narrowing (`s` / `c`) — those belong to `job browse`, and they're disabled here too.

```
 Audit (8 left of 30)                                                                                    filter: all
┌─ Evaluation ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ ml-engineer   agent v a1b2c3   precision 78% (61–89%) · recall ≈ 49% (31–70%)                               │
│               model v7 SHADOW  precision 70% (52–84%) · second look 14 labelled → 6 good (43%, 21–67%)      │
│               both-no region: audited 22/30, 1 missed → noise 4.5% (0.8–22%) · ≈19 missed in all of 412      │
│               if switched on: recall ≈ 63% (+14 pts) · review load +37 jobs (+16%)                          │
│               un-recommended as training data: NO — noise could be 20% > 5% limit (~71 more clean audits)    │
├──────────────────────────────────────────────┬─────────────────────────────────────────────────────────────┤
│ ? 141  Acme      Account Executive, East     │ job #141  Account Executive, East                           │
│ ? 137  NVIDIA    Solutions Architect, …      │ … (job show details)                                         │
│ ✓ 122  Stripe    Data Engineer               │ Filters ──────────                                           │
│ ✗ 118  Datadog   ML Platform Engineer        │ ▸[1] ml-engineer  agent: no (sales role) · model: no 0.08    │
│                                              │      you: —                                                 │
└──────────────────────────────────────────────┴─────────────────────────────────────────────────────────────┘
 g should recommend  b shouldn't  B no filter should  ←→ filter  u undo  r reason  o link  / search  d draw more  e panel  q quit
```

- **Sampling:** `filter audit [-f NAME]… [-n 30]` (or `d` in the app, "draw 10 more") draws uniformly at random from each filter's both-no region, skipping jobs already sampled, and **stores the draw** (with the region's size at that moment). A stored sample can't be cherry-picked — not even by accident — and draws from different weeks combine correctly.
- **Labelling:** per filter `g` should recommend (a miss) / `b` shouldn't (it rightly said no); `B` = no filter should, in one key — most audited jobs are obvious rejections, so auditing stays fast. After a label the cursor moves on: to the job's next unlabelled filter, else to the next job still to label (labelled jobs sink below, so the cursor doesn't follow them). Marks: `?` not labelled, `✓` all bad (the judges were right), `✗` good for some filter (a miss). `u` undoes.
- **The evaluation panel** (`e` shows / hides it; also in `job browse`) recomputes after every label — counts and ranges come straight from the database, so it's instant.
- Sampled jobs stay valid when they expire: judged jobs are archived, not deleted (§7.8).
- Audit labels are ordinary labels (they also train the model); the sample membership is what makes them usable for unbiased estimates.

### 7.5 Preference model — one per filter, with shadow mode

> **Deferred (2026-10-04).** Not built for now: with few labels and low volume, a model may not beat a good command, and it can't judge dealbreakers (a deciding sentence in a long posting, or facts that need a lookup). Evaluation (§7.6) decides — if the agent's recall and precision are good enough, no model is needed; if it misses jobs in ways command edits can't fix, try your labelled jobs as examples in the prompt first. If built, it judges **fit only** (the agent's `fit`, §3.1) on dealbreaker-free jobs, the agent's `dealbreaker` vetoes it, and its encoder is fixed (local or API embeddings) with a classification head trained per filter. The design below stays as the plan.

Fixed:
- **One per filter**, learning that filter's taste (different filters stand for different preferences). Tied to the filter's **name**, not its version. Trained state in `data/`.
- **A binary classifier** predicting the §7.1 label from the job alone (title, company, description, a few structured fields). First plan (checked by §7.6): a small local text embedding + logistic regression with calibrated probabilities — trains in milliseconds, hard to overfit on tens to hundreds of labels. No model until a filter has ~30 labels with both classes. Explanations: the most similar jobs you labelled.
- **Retrains in batches as labels arrive** (below); each retrain is a new version, which must pass an **offline replay test** first (§7.6) — otherwise the previous version keeps judging.
- **Never trained on its own verdicts:** its weak negatives come only from the agent's unreviewed "no"s (§7.7).

**Shadow mode** — so a young model can't flood your review queue:
1. A new model starts in **shadow**: it judges every job (verdicts recorded, §7.2) but **doesn't change what's recommended** — the agent alone decides.
2. Jobs the model says yes to but the agent rejected go to a **Second look** tab in `job browse` (score and both verdicts shown). Nothing forces you to look; labelling them (`g` / `b`) measures the model and trains it exactly where the agent is weakest. Second look is listed in **random order**, not by score, and the estimate uses the jobs you labelled in that order — so labelling only the obvious ones can't flatter the model.
3. The evaluation panel shows **what switching on would gain and cost**: recall with the model OR-ed in vs. the agent alone, and the extra review load.
4. **Switched on automatically** when the switch-on rule holds — default: ≥ 20 Second look labels and model-only precision ≥ 50% (lower end of its range above a floor) — or by hand (`filter model on|off -f NAME`, or a key in the app). Once on, its yes-but-agent-no jobs become ordinary recommendations and Second look empties into review.
5. **Switched back to shadow automatically** if a later evaluation shows its extra picks falling below the rule (settings: `AUTO_APP_SELECT__MODEL_SWITCH_ON`, `…__MODEL_SHADOW=false` to skip shadow mode).

**Training — when and how** (the same in shadow and when on; shadow only decides whether the model's "yes" adds jobs to your queue):
- **Main trigger: new labels.** A filter's model retrains when it has at least `AUTO_APP_SELECT__RETRAIN_MIN_LABELS` (default 10) new labels since its last version — checked **when you leave the app** (`job browse` / `filter audit`) and **at the start of `filter run`** (so new jobs are scored by the latest version). On demand: `auto-app filter train [-f NAME] [--force]`. Never in the background while you label, so the list and the panel stay still under your cursor. Not after every label: each retrain is a version evaluated on its own calls, and hundreds of tiny versions would make every number noise.
- **One retrain:**
  1. gather the training data (§7.7) — your labels, plus unreviewed un-recommended jobs only if the noise is below the limit **now**;
  2. train — job embeddings are computed once, when a job is first judged, and stored, so training is fitting the classifier on stored vectors (milliseconds);
  3. **replay test** against the baselines and the current version (§7.6) — worse → discarded, the current version keeps judging, and the panel says why;
  4. the new version becomes current and **inherits the state** (shadow / on) — retraining never switches a model on, the switch-on rule does;
  5. it **scores the active, undecided jobs** (a new frozen verdict each) — in shadow this refreshes Second look; when on, newly picked jobs go to review;
  6. re-check the switch-on / back-to-shadow rule; refresh the evaluation panel.
- **Drift check — a significant drop triggers action.** After every scoring, the model's **recent precision** (its last `AUTO_APP_SELECT__DRIFT_WINDOW` = 20 labelled picks of its own — the jobs only it recommended; they move first, being labelled in Second look or review) is compared with its **earlier precision**. A drop counts only if it's beyond chance (a one-sided two-proportion test — Fisher's exact — at 95%), never a raw point difference — 20 labels swing ±20 points by chance alone. When it fires:
  1. **retrain right away** with whatever new labels exist (even fewer than 10 — they're what shows what changed);
  2. if the new version is still below the switch-on rule, **back to shadow**;
  3. the panel flags it: *"ml-engineer model: recent picks 30% good (15–52%) vs 68% before — retrained, back in shadow; label Second look to recover"*.
- Recall and the both-no region's noise move only with audits — too slow to trigger on; the noise is checked at every retrain instead (§7.7).

Open: embedding model (local vs API), structured features, the decision threshold (chosen for recall), a "corrector" variant that also sees the agent's verdict and reason (evaluated side by side).

### 7.6 Evaluation — `auto-app filter eval`

The same numbers as the panel, printed. Per filter — **for the agent, the model, and the two combined** — current versions by default (`--all-versions`: each version with enough labels):

| | from | meaning |
|---|---|---|
| **precision** | your labels on the jobs that judge recommended (Second look labels for a model in shadow) | share of its picks that were good |
| **misses** | its "no"s you labelled good — counted exactly in the disagreement regions, estimated in the both-no region from audits (noise × region size, per draw) | good jobs it would have hidden |
| **recall** | good picks ÷ (good picks + misses) | the share of good jobs it finds — the number that matters most |
| **review load** | its picks ÷ judged | how much it asks you to read |
| **what the model adds** | good jobs only the model picked vs. bad jobs only the model picked | gain vs. extra review |
| **noise of the both-no region** | audit samples: good ÷ audited | decides whether unreviewed un-recommended jobs may be training data (§7.7) |

- **Every rate comes with a range** (95% Wilson interval) — for describing a rate.
- **Yes / no questions use a one-sided exact bound** (Clopper–Pearson, 95%): "is the noise below the limit?" passes when the one-sided upper bound is under it. With no miss found, 59 audits show the noise is below 5% (30 bound it at ~9.5%); each miss found raises that — 1 → 93, 2 → 124, 3 → 153. Worked example in [study.md](study.md).
- **Live:** nothing is cached; every label changes the next `filter eval` and the panel immediately.
- **Two views of the model:** **as used** (default) — each job counts with the verdict of the version that was current when it was judged, all versions pooled: "has the model helped?", with enough data to answer; the switch-on rule and the drift check use this view. **Per version** (`--all-versions`) — only once a version has made enough calls; new versions are covered by the replay test.
- **Offline replay test** for each new model version: walk through labelled jobs in time order, train on what existed then, score the next batch; compare with baselines (recommend everything, the agent alone, a title-words-only model) on precision-recall AUC and on recall / precision at the chosen threshold (threshold chosen on training data only). Linked duplicates (§7.8) stay on one side of every split. Also checks calibration (are 70% scores right ~70% of the time?).
- `--rerun` (later): judge the labelled jobs with the current command, to compare an edited command with the previous version on the same labels (costs agent runs).
- Ground truth is **your labels only** — never the agent's unreviewed "no"s.

### 7.7 Training data per filter

| source | label | weight |
|---|---|---|
| recommended jobs you labelled | `g` / `b` | 1 |
| audited and Second look jobs you labelled | `g` / `b` | 1 |
| **unreviewed un-recommended jobs** (the agent said no; active and archived) | **no** | **only while the both-no region's noise is below the limit** — its one-sided 95% exact upper bound (§7.6) under `AUTO_APP_SELECT__NOISE_LIMIT` (default 5%); then low weight (from the measured noise), subsampled (e.g. 3 per positive) |
| unreviewed recommended jobs | — | never |
| jobs no filter judged | — | never |

The noise rule is re-checked at every retrain: if new audits push the noise above the limit, the next version trains on your labels only (and the panel says why). Until enough audits exist, the model trains on your labels only.

### 7.8 Archived jobs are training data

The model retrains whenever new labels arrive, so its data — and the evaluation's — must only grow. Hence (Step 1 §7.5, §9.2):
- **Training data = judged jobs only** — recommended or un_recommended. Every expired judged job is **archived, never deleted**; jobs no filter ever judged are deleted as before. An archived job keeps what a filter reads (so `filter eval --rerun` can rebuild the exact prompt), both judges' verdicts, your labels and decision; raw captures are dropped.
- Archived jobs leave the active pool: not in the active tabs, never judged again, **never merged with active jobs**. A listing seen again after its job was archived becomes a **new job** and is judged again.
- **Archived jobs are deduplicated among themselves** when archived (linked, not merged), so each job is one training example: per filter the most recent label counts, and a linked group stays on one side of a train / test split.
- Training and evaluation read judged jobs, active **and** archived. Audit samples stay valid when their jobs expire.
- This also fixes a bug found 2026-10-04 (TTL deletion of a judged job fails on the `judgments` foreign key, so `data purge` stops).

---

## 8. Review

You review in **`auto-app job browse`** — the **Jobs** section (F1) of `auto-app ui`, the one app for every interactive part (F2 Audit, F3 Step 3's Inbox) — a **full-screen terminal app** (built with [Textual](https://textual.textualize.io/)) over the whole job pool: decide on recommended jobs and tell each filter whether it judged right. It has a tab per state; the three review tabs are below; it **opens on Recommended** by default (`--state` picks another tab). It started as a separate `filter review` command and was merged into `job browse` (2026-10-04), since its lists were a subset of the pool's tabs.

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
2. **Feedback, per filter:** pick a filter (`←` `→` or its number; with one filter, no picking needed) and press `g` (its verdict was **right**) or `b` (**wrong**). *(Since S4: a direct per-filter label — `g` this filter should recommend the job / `b` it shouldn't, whatever it said — so it works with two judges, §7.1; `B`: no filter should.)* Each label is for **that filter's** preference model only (§7). A short reason can be added (`r`) to either answer.

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
audit_draws     (draw_id PK, filter, filter_version, pool_size, size, drawn_at)       -- S4 ✅: one random draw …
audit_items     (draw_id, job_id, filter, PRIMARY KEY (draw_id, job_id))            -- … and the jobs in it
jobs.duplicate_of (S4 ✅)  -- an archived job linked to the archived job it duplicates (§7.8); archived jobs keep their primary posting's parsed fields
feedback.label (S4 ✅)     -- your per-filter label: good / bad (§7.1); `correct` is now derived (label = verdict), NULL without a verdict
model_versions  (filter, model_version, trained_at, n_labels, uses_weak_negatives, replay_metrics JSON, state)   -- S5: shadow | on | retired
model_verdicts  (job_id, filter, model_version, score, threshold, recommend, created_at, PRIMARY KEY (job_id, filter, model_version))  -- S5: frozen per version
-- S5: preference model state per filter (data/), its scores per job
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
auto-app filter audit [-f NAME]… [-n 30]                          # draw random samples of each filter's both-no region; label them in the app, with the live evaluation panel (S4 ✅)
auto-app filter eval [-f NAME]… [--all-versions] [--rerun]        # precision, noise, recall, review load, training-data check — with ranges (S4 ✅; --rerun later; model columns S5)
auto-app filter model on|off -f NAME                              # switch a filter's preference model out of / back into shadow mode (S5)
auto-app filter train [-f NAME]… [--force]                        # retrain now (normally: on leaving the app / at filter run, ≥ 10 new labels; or on a drift drop) (S5)
```
`auto-app job mark` (C11) still sets any state by hand, `want_to_apply` included.

---

## 11. Milestones

| # | Deliverable |
|---|---|
| S1 | Filters: folder loader + validation (`command.md`, `filter.yaml`, optional `parser.py`, other files), versions, job-details builder (labelled lines, boilerplate stripped, length cap), `$ARGUMENTS` substitution; `filters` command (list, check, `--job` preview); `filters.example/` committed — ✅ `src/auto_app/select/` |
| S2 ✅ | Agent judgment: agent runners — our loop on the Claude API, headless Claude Code with your login (`runner: auto`), and DeepSeek for `deepseek-…` models (server-side web search / fetch with LinkedIn blocked, `read_file` limited to the filter's folder, per-job budget), raw answers + traces + cost in `judgments`; built-in convention parser and `parser.py`, `--reparse`; several filters (any recommends); `select` (parallel, `--max-cost`), `why` — first tried on ~30 saved jobs |
| S3 ✅ | Review app (Textual; merged into `job browse`): Recommended / Un-recommended / Want to apply lists, detail pane, decisions (`want_to_apply` / `not_applied`), per-filter feedback (`feedback` table), undo, open link; `want_to_apply` state + no-expiry rule; data layer tested without the UI, the app with Textual's test driver — `src/auto_app/select/review.py`, `process/job_browser.py` |
| S4 ✅ | Evaluation + audit: every expired judged job archived as training data, with its parsed fields (fixes the purge bug; never-judged jobs still deleted); dedup within the archive; a re-seen archived listing becomes a new job; stored random audit draws, `filter audit` (the app's Audit tab with a live evaluation panel; `B`, `d`, `e`), per-filter good / bad labels (§7.1, replacing right / wrong), `filter eval` (precision, misses, recall, review load, noise; Wilson ranges, one-sided exact noise test) — design §7.1–7.4, 7.6, 7.8. As built (2026-10-04): noise pooled over a filter's draws × its current "no" count (not weighted per draw); archive dedup by matching keys + same title; `--rerun` later |
| S5 (deferred) | Preference model per filter (§7.5) — fit only, dealbreaker veto, fixed encoder + per-filter head: embeddings + calibrated logistic regression, frozen verdicts per model version, batch retraining (≥ 10 new labels, on leaving the app / at `filter run`, `filter train`), drift check (significant drop in recent precision → retrain now, back to shadow if still below the rule), offline replay test, **shadow mode** (Second look tab, automatic switch on / back to shadow), OR with the agent; un-recommended jobs as training data only while the noise is below the limit (§7.7); two-judge scorecard in `filter eval` and the panel |

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

1. The preference model's open details (§7.5): embedding model, structured features, threshold, the corrector variant.
2. Should `applies_to` also accept company names (e.g. a filter only for NVIDIA jobs from LinkedIn)?
3. A longer un_recommended TTL by default while a command is new?
4. Shared text between filters (e.g. one sponsorship section used by several commands): an include syntax, or copy for now?
