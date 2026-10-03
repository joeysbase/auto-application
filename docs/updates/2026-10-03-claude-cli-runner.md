# Step 2: run filters with your Claude Code login (no API key) — 2026-10-03
**Step:** 2   **Status:** done. Verified live: one real job judged through `claude -p`.

## What
- **The `claude-cli` runner** (`src/auto_app/select/runner_cli.py`): one headless Claude Code process (`claude -p --output-format json`) per (job, filter), running in the filter's folder. It's locked down to the same abilities as the API runner:
  - `--tools`: WebSearch / WebFetch / Read, per the filter's `tools` and files;
  - `--permission-mode dontAsk`, so anything not allowed is denied;
  - allow `Read(./**)`, deny `Read` on `command.md` / `filter.yaml` / `parser.py`, deny `WebFetch(domain:*linkedin.com)`;
  - `--setting-sources ""`, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`;
  - `ANTHROPIC_API_KEY` removed from its environment, so it uses the Claude Code login.

  It reads `result`, `usage`, `num_turns`, `total_cost_usd` and `permission_denials`.
  - Running out of turns (`error_max_turns`) gives no answer, so the job is "couldn't read" and goes to review.
  - Other failures (not logged in, missing CLI, timeout after 600 s, malformed output) are errors, retried on the next `select`.
- **Choosing the runner:** `AUTO_APP_SELECT__RUNNER=auto | api | claude-cli` (default `auto`: the API when `ANTHROPIC_API_KEY` is set, otherwise the CLI). `AUTO_APP_SELECT__CLAUDE_CLI` sets the executable. `select` prints which runner it uses.
- **Cost:** CLI runs record Claude Code's list-price-equivalent cost. `--max-cost` works with it without any price config. The output says it's billed to your subscription.
- **Internal:** `run_select` now takes a runner `(filter, prompt) -> RunResult`; `api_runner(client)` wraps the API loop.

## Every call is a new session (added on request)
- Each call gets a fresh `--session-id` (a UUID), runs with `--no-session-persistence`, and never uses `--continue` / `--resume`.
- It also runs in a **new temporary folder outside the project** (`auto-app-judge-*`), holding only copies of the filter's other files and deleted afterwards. No `CLAUDE.md`, memory or project file can carry context in, and `Read` can't reach the project at all.
- **Checked live:** with our flags, an ancestor `CLAUDE.md` ("always say BANANA") was *not* used even before this change. There are no `CLAUDE.md` files on your machine's path today. No session or memory folders were created in `~/.claude/projects/` by the test runs.
- After the change, a second real job was judged from a temporary folder (`sponsorship-notes.md` read there; recommend, ~$0.04 list price), and no temporary folders were left behind.
- Tests: a new session ID and a new folder for every call; the folder holds only the filter's files and is gone afterwards.

## Verified live (Claude Code 2.1.285, Haiku 4.5)
- **Sandbox:** Read inside the filter folder works; `../outside.txt` is denied and reported in `permission_denials`.
- **LinkedIn:** WebFetch on `www.linkedin.com/jobs/` was denied; `example.com` was fetched.
- **End to end:** `select --limit 1` on a database copy, with an example filter switched to Haiku, judged ByteDance job #24 as **recommend**. The agent looked up ByteDance's H-1B history, as the command asks, and the answer was parsed by the convention parser. It used about 17k / 2k tokens, about $0.05 list price.

## Tests
- `tests/select/test_runner_cli.py`, 4 tests with a fake process:
  - the lockdown flags;
  - the folder as working directory and no API key in the environment;
  - reading the JSON;
  - errors, timeout, missing CLI and running out of turns;
  - `select` using the CLI's cost.
- The CLI tests no longer start a real `claude`.
- 36 Step 2 tests in total.

## Limitations
- `why` shows the CLI's final answer, turns and denied lookups, but not each lookup (the JSON output doesn't list them). `--output-format stream-json` could add them later.
- `max_tool_calls` isn't enforced separately by the CLI; only `--max-turns` is.
- Whether scripted use of a personal subscription fits Claude's consumer terms is yours to check (noted when we discussed it).
