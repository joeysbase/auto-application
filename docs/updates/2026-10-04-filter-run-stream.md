# `filter run --stream`: watch the agents work — 2026-10-04
**Step:** 2   **Status:** done. Verified live through the Claude Code runner.

## What
- **`auto-app filter run --stream`** shows each run live: a header per job (`── #22 title · filter (model)`), the agent's text as it's written, each lookup (`→ WebSearch: query=…`, `→ read_file: path=…`) and its result on one line (`✓ …` / `✗ …`), then the usual verdict line.
- **One job at a time:** with `--stream`, runs aren't parallel, so the output stays readable. `filter run` says so when `--parallel` / `AUTO_APP_SELECT__PARALLEL` is above 1.
- **Per runner:**
  - **Claude API:** the SDK's streaming calls, so text arrives token by token. Tool calls and results are shown as they complete.
  - **Claude Code CLI:** now always reads Claude Code's event stream (`--output-format stream-json --verbose --include-partial-messages`): text deltas, `tool_use` / `tool_result` messages, then the `result` line. **Side benefit:** CLI judgments now store every lookup, so `filter why` lists them, which it couldn't before.
  - **DeepSeek:** one event per turn (its text, then each tool call and result), not token by token.
- **Internal:**
  - runners take an optional `on_event(kind, data)` with kinds `text`, `tool`, `tool_result`;
  - `run_select(..., write=)` and `StreamPrinter` format them;
  - a display error never breaks a run.

## Verified live
`filter run --job 22 --stream` on a database copy (Haiku, Claude Code login):
- the web search for ByteDance's H-1B history was shown as it happened, then the analysis streamed in, then the verdict (recommend);
- `filter why 22` then listed both lookups (`Read sponsorship-notes.md`, `WebSearch …`).

## Tests
- **API runner:** streaming emits text in pieces and the tool events in order; without `on_event`, plain calls.
- **CLI runner:** a stream-json fixture gives live events and a full trace. The existing CLI tests were moved to the event-stream format with a fake `Popen`.
- **DeepSeek:** events per turn.
- **Engine:** `--stream` with `parallel=3` still runs one job at a time, with a header per job and each job's own text under it.

## Added: `job show --description`
`auto-app job show N -d` also prints the job's full description: its primary posting's text as saved, with nothing removed except that Workday's split "-" bullets are rejoined for reading. For an archived job you applied to, it prints the copy kept with the record. Test: `test_job_show_prints_the_description`.
