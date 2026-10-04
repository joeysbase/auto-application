# `filter run` shows progress — 2026-10-04
**Step:** 2   **Status:** done. Tested offline.

## What
Before, `filter run` printed a line only when a judgment finished. With slow agents (30–60 s each) there was no sign of how far along it was, what was running, or how long was left.

Now:
- **A start line:** `judging 30 runs on 30 jobs, 3 at a time`.
- **Numbered lines:** each finished judgment is printed as `[ 7/30] #28 ml-engineer recommend — reason`. Runs skipped because of `--max-cost` are counted too.
- **A live bar in a terminal**, kept at the bottom:
  - done / total;
  - cost so far;
  - elapsed and estimated time left;
  - the running jobs with their seconds so far (`running #28(41s) #31(12s)`).
  
  It disappears when the run ends, followed by `took m:ss`.
- **No bar** with `--stream` (its live text would fight the bar) or when output is piped or logged. The `[n/N]` lines remain, and `--stream` headers show `── [3/30] #28 …`.
- The closing hint now points to `job browse --state recommended`.

## Files
- `src/auto_app/select/engine.py` — a `Progress` interface (start / began / done / finish) called by `run_select`; the judgment bookkeeping moved to `_record`; `StreamPrinter.header` takes the count.
- `src/auto_app/select/progress_view.py` (new) — `LineProgress`, `LiveProgress` (rich), `make_progress`.
- `src/auto_app/cli.py` — `filter run` uses it.

## Tests
`tests/select/test_select.py`:
- the engine reports start, each run starting and finishing with n / total and cost so far, and finish;
- the line format;
- the bar, rendered to a fake terminal (counts, running jobs, markup-safe reasons, "took");
- no bar with `--stream` or without a terminal;
- the stream test checks the numbered headers.

236 tests pass.
