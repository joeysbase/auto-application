# Step 2 S2: the agent runner, reading answers, `select`, `why` — 2026-10-03
**Step:** 2   **Status:** done, tested offline against a fake model. Not yet run against the real API (no `ANTHROPIC_API_KEY` in the dev shell). Design: [step2-select.md §3–5](../step2-select.md#3-reading-the-answer)

## What
- **The agent runner** (`src/auto_app/select/runner.py`): our own tool-use loop on the Claude Messages API (`anthropic` SDK 1.11).
  - **Tools:**
    - `web_search_20250305` and `web_fetch_20250910` (server-side), with `blocked_domains: [linkedin.com]` and `max_uses` set to the remaining budget;
    - `read_file`, ours, which returns only the filter's own other files. Paths outside the folder and the reserved files are refused.
  - **Budget:**
    - `max_turns` and `max_tool_calls` are counted across client and server tools;
    - when the budget is used up, the next call has `tool_choice: none` plus a wrap-up message;
    - `pause_turn` is handed back unchanged, and there's a hard stop at `max_turns + 3` calls.
  - **Recorded:** a trace (text, tool calls and inputs, search result URLs and titles, fetch URLs and errors), tokens, tool calls, searches and fetches.
  - **Errors:** API errors (after the SDK's 4 retries) give `status = error`, never an answer.
  - **System message:** short. It frames the posting and web pages as data, forbids LinkedIn, and says "answer in the format asked for". It doesn't say how to judge.
- **Reading answers** (`select/parse.py`), producing `ParsedOutput {recommend: bool | None, reason}`:
  - **The convention parser** takes the last JSON object with `recommend`; it must be true or false.
  - **`parser.py`** must return that shape.
  - Anything unreadable gives `None` with the reason, never "no".
- **Storage:** migration `ff73a7af38bb` adds:
  - `filter_versions`: the exact command, files and settings run;
  - `judgments`: per (job, filter version): raw answer, parsed output, parser id, errors, trace (zstd), tokens, tool calls, cost;
  - `reviews`: for S3.
- **The engine** (`select/engine.py`):
  - **What gets judged:** a (job, filter) pair when the job is active and undecided, `applies_to` covers it, and the filter hasn't judged it. `--rescore` adds pairs judged by an older version; failed runs are retried automatically. `applies_to` matches a job's sources, and `source/search` through each posting's first search run.
  - **Runs:** in parallel threads (`select.parallel`, default 3). Database writes happen on the main thread.
  - **Budget:** `--max-cost` is checked before each run starts.
  - **Deciding the job:**
    - a filter recommends when its answer says recommend or couldn't be read;
    - a job is `recommended` if any filter recommends it, and `un_recommended` if all said no;
    - it's left undecided while any applicable filter's run failed or is missing;
    - decided jobs are never touched.
  - Job expiry dates are refreshed afterwards, since the TTL depends on the state.
  - **`reparse`:** re-reads stored answers with the current parsers and re-decides those jobs, with no agent runs.
- **Config:** `select:` (`parallel`, `prices` per model, `web_search_usd_per_1000`). Only Haiku 4.5 is priced by default ($1 / $5 per million tokens); add other models from anthropic.com/pricing. `--max-cost` refuses to run with unpriced models.
- **CLI:**
  - `select [-f NAME] [--limit N] [--rescore] [--max-cost USD] [--parallel N] [--dry-run] [--reparse] [--dir]`; it needs `ANTHROPIC_API_KEY` except for `--dry-run` / `--reparse`.
  - `why JOB [--trace]`: each filter's judgment (version current / out of date / removed), the lookups made, cost and the full answer.

## Tests / verification
- 14 new tests, run offline against a fake model:
  - **`tests/select/test_runner.py`:** request shape and tools, no tools for a tool-less filter, the `read_file` sandbox, the budget leading to `tool_choice: none`, server tools and `pause_turn`, API errors, the convention parser and `parser.py`.
  - **`tests/select/test_select.py`:**
    - judging and state (recommend / no / couldn't read → recommended);
    - no repeat runs; versions and `--rescore`; decided jobs untouched; failed runs retried;
    - two filters OR-ed, with `applies_to`; `--limit`; `--max-cost`;
    - `--reparse` (dry run and real);
    - the CLI (`--dry-run`, a missing API key, a missing price, `why`).
- 173 tests pass in total. `select --dry-run --dir filters.example --limit 3` on the real database lists 6 judgments on 3 jobs.

## Not done yet / follow-ups
- **The first real run.** Set `ANTHROPIC_API_KEY`, add the price of your filter's model under `select: prices:`, then `auto-app select --limit 30 --max-cost 2`.
- **Prompt caching** isn't used yet. The job sits in the middle of the command, so only the system message could be cached as it stands.
- **The budget:** `--max-cost` doesn't stop runs already in progress; at most `parallel` runs can go over.
- **S3:** `review` (decisions → `reviews`, labels per filter).
