# Step 2 redesign: LLM judgment + preference model (design) — 2026-10-01
**Step:** 2   **Status:** designed. The result is in [step2-select.md](../step2-select.md).

## Decisions
- **No hard-filter stage.** Step 1's searches already apply what sites can filter. Everything else (skill fit, years, sponsorship wording) is read from the job text by the LLM. The earlier rules stage and `preferences.yaml` are dropped.
- **A command + job details are the agent's input.** A *command* (`commands/<name>.md`, in the style of Claude Code's slash commands: markdown, optional front matter, `$ARGUMENTS` where the job details go) says how you want jobs filtered and recommended, and **runs for every job**.
  - A *filter profile* (`profiles/<name>.yaml`) picks one command and the job details, plus the agent's settings (`model`, `tools`, budgets), the parser and `applies_to`.
  - There can be several profiles; a job is recommended if any profile recommends it.
  - A profile's version is a hash of the command text, job details, length cap, output convention, model, tools and budgets.
  - History: an earlier draft had one free-text profile, then a prompt per profile, then Agent Skills (`skills/<name>/SKILL.md`). Skills were replaced by commands on 2026-10-02: a skill is loaded only when the agent thinks it relevant, whereas this prompt must run every time.
- **The job details are configurable per profile** (`job_details`), validated.
  - Default: `[title, company, locations, description]`.
  - Available: title, company, locations, workplace_type, employment_type, salary, posted_at, reposted, description.
  - Judgments are stored per (job, profile version); `--rescore` redoes out-of-date ones.
- **The app doesn't define how to judge** (2026-10-02). Scores, thresholds and deal-breaker rules are up to your command. Step 2 only needs to know whether to recommend, plus a reason to show; how that's read from the answer is set per profile (below).
- **Recommending:** a job is recommended if any profile's agent says recommend, **OR** (later) the preference model does.
- **The parsed output is `{recommend: bool | None, reason: str}`** (2026-10-02), and nothing else. `recommend` is the decision; `None` means the answer couldn't be read. Scores, links and the like go in `reason`; the full answer and the trace are kept.
- **High recall:**
  - the default convention ends with "if unsure, recommend", which your command can override;
  - jobs whose answer couldn't be read go to review, marked as such.

  Reviewed jobs become training labels, and the preference model needs many and varied ones.
- **Reading the answer is configurable too** (2026-10-02), per profile (`output.parser`), because the command decides what the agent writes:
  - **`default`, our convention:** the app tells the agent to end with `{"recommend": true|false, "reason": …}`, and reads the last JSON object.
  - **`match`:** declarative. A regex or JSON field plus `yes: [...]` / `at_least: N`.
  - **`python`:** a `parse(answer) -> {recommend, reason}` function.
  - With a custom parser the app adds no output instructions.
  - The raw answer is stored, so changing a parser re-reads stored answers (`select --reparse`, also `--dry-run`) without new agent runs. The parser is therefore not part of the profile version; the default convention is.
  - An answer that can't be read is "unparsed", never "no".
- **The preference model is set aside.** Its design is open (Bradley–Terry was discussed and dropped for now). Fixed so far: it learns only from your decisions, it scores jobs independently of the LLM, and its yes/no is OR-ed with the LLM's. Review decisions are stored from the start, so it will have data.
- Dropped from earlier drafts: the per-profile `threshold`, fit-score / verdict / deal-breaker output fields, the weighted combination, and the low-ranked review sample.
- **An agent, not a single LLM call** (2026-10-02; reverses the earlier "plain call" choice). The command can use information beyond the posting: a company's sponsorship history, engineering blog, news.
  - Bounded by the profile's `tools` (read-only web search / fetch) and a per-job budget (`max_turns`, `max_tool_calls`; `select --max-cost`).
  - It has no LinkedIn access (blocked by domain), no credentials and no file writes. Job text and fetched pages are treated as data, never instructions.
  - Its raw answer, trace and cost are stored per judgment.
  - The profile version also covers model, tools and budgets.
  - Option for later: a cheap single-call first pass.

- **A filter is a folder** (2026-10-02), `filters/<name>/`. It holds `command.md` (run for every job, `$ARGUMENTS` = the job details), `filter.yaml` (job details, model, tools, budgets, `applies_to`), an optional `parser.py`, and any other files the command refers to; the agent may read files in its own folder only. This replaces the separate `commands/` and `profiles/` directories.
  - **The output convention is written in the command,** not appended by the app: the agent ends with `{"recommend": true|false, "reason": "…"}`. A filter with its own output format adds `parser.py` (`parse(answer) -> {recommend, reason}`). The earlier declarative `match` parser was dropped.
  - **The filter's version** covers the command, the other files and the agent settings. It excludes `parser.py` (re-reading stored answers is free) and `applies_to`.
  - **One preference model per filter:** its labels are your decisions on jobs that filter judged; it's tied to the filter's name, not its version; its state lives in `data/`. Per filter, agent OR preference model; a job is recommended if any filter recommends it.
  - Commands: `filters`, `select --filter`.

- **Runner: our own loop on the Claude API** (2026-10-02), chosen over the Claude Agent SDK. It uses the Messages API with tool use, with exactly three tools:
  - the API's server-side `web_search` and `web_fetch`, with linkedin.com blocked by domain;
  - our `read_file`, limited to the filter's folder.

  The app counts turns, tool calls and cost, records the trace, and is tested with a fake model. The Agent SDK would bring a full coding-agent environment that would have to be switched off, plus the Claude Code CLI as a dependency. The runner can be swapped later without changing filters, parsing or storage.

## Docs updated
- `docs/step2-select.md`: rewritten (commands, filter profiles, agent judgment, OR recommendation, agent vs single call, preference model set aside, review, storage, CLI, milestones S1–S3, risks, open questions).
- `docs/README.md`: the Step 2 summary row, pipeline diagram and `select/` package note.
- `docs/step1-collect.md` §2.5 / §2.7 / §7.5 and `README.md`: deferred search fields are now "not filtered — state it in your profile". Step 2 no longer re-applies search fields.
- Code wording only: the `plan` notices and the unsupported-field error now say "not filtered (state it in your Step 2 command if it matters)" instead of "applied in Step 2".

## Next
S1 (commands + filter profiles, job-details builder) → S2 (agent judgment, `select`, `why`) → S3 (`review`). The preference model comes later; its design is open.
