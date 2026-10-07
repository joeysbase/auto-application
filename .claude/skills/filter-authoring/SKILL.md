---
name: filter-authoring
description: Create, rewrite or tune an auto-app filter (filters/<name>/) — the Claude agent that judges every collected job — by asking the user what they want, drafting command.md and filter.yaml, trying it on jobs they've already decided before letting it judge everything, and measuring it. Use for "make a filter for research roles", "my filter is too strict / recommends junk", "rewrite my filter", "add a deal-breaker", "should I use a cheaper model?".
---

# Writing and testing a filter

A **filter** is a folder `filters/<name>/` (git-ignored; template `filters.example/ml-engineer/`; design
`docs/step2-select.md` §1–§5). Its agent judges **every** new job and answers *recommend or not*, with a reason. A job
is recommended if **any** filter recommends it. Principle: **high recall**. The user would rather see a few extra
jobs than miss a good one, so "if unsure, recommend".

## The files

**`command.md`**: the prompt, run for every job:
```markdown
---
description: Screen ML engineer / applied scientist jobs for me.
---
Decide whether I should consider applying to this job. If unsure, recommend.

## My background
(skills, degree, experience — the user's words)
## What I want
(roles, level, kind of work, places, remote / hybrid)
## Deal-breakers
- (only things that rule a job out for sure: sponsorship, clearance / citizenship, years of experience, role type …)
## The job
$ARGUMENTS
## Answer
Explain briefly, then end your answer with a JSON object on its own, and nothing after it:
{"fit": true | false, "dealbreaker": true | false, "reason": "one or two sentences"}
- fit: does the job match my background and what I want? If unsure, true.
- dealbreaker: true only if one of my deal-breakers clearly applies (the posting says so, or you found it); if unsure, false.
```
- **The answer convention:** keep the JSON line exactly. The app reads the **last** JSON object (recommended = fit and
  no dealbreaker). Without it the answer can't be read. That still means "recommend", but it's noisy.
- **`$ARGUMENTS`** is where the job goes, as a `<job_posting>` block marked as data, not instructions.
- **Deal-breakers vs. fit:** a deal-breaker hides a job however well it fits, so list only clear, checkable ones.
  Preferences ("remote preferred") belong under *What I want*.
- **Lookups:** with `web_search` / `web_fetch`, the command can ask the agent to check things the posting doesn't say
  (e.g. "has the company sponsored H-1B before?"). Put how-to notes in an extra file in the folder (like
  `sponsorship-notes.md`) and refer to it.

**`filter.yaml`**:
```yaml
job_details: [title, company, locations, description]   # also: workplace_type, employment_type, salary, posted_at, reposted
max_description_chars: 12000
model: claude-sonnet-5        # a smaller model costs less; a deepseek-… model uses DEEPSEEK_API_KEY (no web_search)
tools: [web_search, web_fetch]
max_turns: 8                  # the per-job budget
max_tool_calls: 6
# applies_to: [linkedin, nvidia/new-grad]   # only jobs from these sources / searches; omit = every job
```
- **Cost** is filters × jobs × lookups. `applies_to` and a smaller `model` / fewer `max_tool_calls` keep it down.
- **Versions:** editing `command.md`, any file in the folder, or those settings makes a **new version**. New jobs get
  it; old judgments stay as they were until re-judged (`--rescore`). `applies_to` and `parser.py` aren't part of the
  version.

## The workflow

1. **Ask** what the filter is for, unless the user already said:
   - the roles (titles, level);
   - their background (skills, degree, experience);
   - what they want (kind of work, places, remote);
   - the deal-breakers.
   
   Starting from an existing filter: read its `command.md` / `filter.yaml` first. **Don't read `profile/`** (it holds
   passwords); ask the user instead.
2. **Draft** both files in the shape above, in the user's words. Show them **in full** (or as a diff when editing),
   with a line on cost (model, tools, `applies_to`).
3. **Write** them after the OK: `mkdir -p filters/<name>` (or `cp -r filters.example/ml-engineer filters/<name>` and
   edit). Folder names are the filter's name: lowercase, no spaces.
4. **Check it loads:**
   - `uv run auto-app filter list` (its version, warnings: e.g. a command missing the answer convention);
   - `uv run auto-app filter show <name>` (settings, problems);
   - `uv run auto-app filter show <name> --job <id>` (the exact text the agent gets).
5. **Try it on a few jobs before letting it judge everything** (3–6 jobs; it costs Claude usage, so say how many and
   ask):
   1. Pick jobs the user already decided: some from `job list --state want_to_apply --json` and some from
      `--state not_applied`, or labelled ones.
   2. Run `uv run auto-app filter run -f <name> --job <id> --job <id> …` (it re-judges even jobs judged before). For
      one job with its reasoning live: `--stream`.
   3. Read the verdicts: `uv run auto-app filter why <id> --json`.
   4. Show a small table: job · the user's decision · the filter's verdict · its reason.
   5. Where they disagree, say why (a missing deal-breaker, too strict a "fit", a lookup that failed), propose a
      small edit, and repeat from 2.
6. **Let it judge the pool:**
   1. `uv run auto-app filter run -f <name> --dry-run` shows how many jobs it would judge.
   2. Then run it, with `--limit N` (and `--max-cost` if prices are set) if that's a lot. Ask first: this is the costly
      step.
   
   From then on, `serve` (or `filter run`) judges new jobs with it automatically.
7. **Measure it over time:**
   - the user's labels (`filter-feedback` skill);
   - `uv run auto-app filter eval -f <name>`: precision, misses, recall, review load, with ranges (few labels → wide
     ranges);
   - `filter audit` (in the app) samples its "no"s for misses.

**Optional `parser.py`:** a filter can read answers in its own format with `parser.py` (a `parse(answer) -> {recommend,
reason}`; see `filters.example/verdict-format/`). It isn't part of the version. After changing it, re-read the
stored answers for free: `uv run auto-app filter reparse --dry-run`, then without `--dry-run`.
**Full reasoning of one judgment:** `uv run auto-app filter why <id> --trace`.

## Tuning an existing filter

- **Too many bad picks** (low precision): sharpen *What I want*, or add a clear deal-breaker the bad picks share. Read
  their reasons (`filter why`) first.
- **Missing good jobs** (misses in eval / audit): loosen. Check whether a deal-breaker fires on unclear evidence, and
  remind the agent "if unsure, recommend / dealbreaker false".
- **Answers can't be read:** the command has drifted from the answer convention. Restore the JSON line.
- **After an edit:** try it on the same sample jobs (step 5), then offer `filter run --rescore -f <name> --limit N` for
  recent jobs.

## Rules

- Show the full files or diff first; write only after the user's OK.
- Ask before anything that runs the agent on jobs (steps 5–6, `--rescore`): say how many jobs.
- **Never delete or rename a filter folder** without the user's explicit OK: its judgments and the user's labels are
  tied to its name.
- Never read `profile/` or `.env`.
