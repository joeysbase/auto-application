# Clean interruption (Ctrl+C) — 2026-09-28
**Step:** 1   **Status:** done

## What
Stopping a run (including mid rate-pause) marks the batch `interrupted` with finish time and per-source progress, marks the running search run `stop_reason: interrupted`, and `collect` prints the summary. Saved postings were already committed; the next run continues.

## Why
Before, an interrupted batch looked like a crash (no finish time, no per-source summary, search marked `error`).

## Changes
`core/models.py` (`StopReason.INTERRUPTED`), `collect/runner.py`, `collect/report.py`, `cli.py`.

## Tests / verification
Fake-site test: pause made to block, run cancelled → progress recorded, postings kept, next run sees them as known.
