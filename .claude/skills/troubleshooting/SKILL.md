---
name: troubleshooting
description: Explain why auto-app isn't bringing new jobs or is stuck — blocked or logged-out sources, cooldowns, a broken filter, no Claude usage left, config errors, failed collects. Use for "why no new jobs?", "what's wrong?", or a notification like "collecting stopped" / "usage used up" / "login needed".
---

# Troubleshooting

Check, in this order:

1. **The loop:** `uv run auto-app serve status --json`.
   - `stopped`: a **broken filter** (the reason is in `stopped`). Find the filter (`filters/<name>/`), explain the
     error, help fix it (with the user's OK), then `uv run auto-app serve resume`.
   - `waiting for usage`: Claude (or DeepSeek) usage is used up. It resumes by itself at `usage_until`; nothing to do.
   - `paused …` / `outside the window` / `pausing until …`: normal; say when the next round is.
   - `not running`: `serve` isn't running. Offer `uv run auto-app serve run --once` or `serve install`.
   - `last_round.problems`: read them out.
2. **Sources:** `uv run auto-app source health --json`.
   - `needs_login`: the user must log in themselves with `uv run auto-app source login <source>` (opens Chrome). You
     can't log in for them.
   - `cooldown`: blocked by the site, so it waits until `cooldown_until`. `--reset` clears it, but suggest waiting.
     If `last_block_reason` is a network error (`ERR_INTERNET_DISCONNECTED` …) from before 2026-10-16, it wasn't a
     block: `--reset` is fine.
   - a round's source status `offline`: the Mac had no network; no cooldown, the next round tries again.
3. **The last collect:** `uv run auto-app collect stats --json` gives each search's stop reason:
   - `early_stop` / `done`: caught up, which is normal;
   - `time_limit` / `goal`: capped;
   - `blocked` / `error` / `empty`: a problem.
   
   Logs are in `data/logs/` (one per batch; `serve.log`; `serve-rounds.jsonl`).
4. **Judging:** jobs waiting: `uv run auto-app job list --state not_evaluated --json`; jobs a filter gave up on:
   `uv run auto-app filter unjudgeable --json`. `filter run --dry-run` shows what would be judged.
5. **Config:** a "config problem" on any command means `config/searches.yaml` has an error; quote it, propose the fix,
   and edit only with the user's OK.

## Deeper tools (when the above doesn't explain it)

- **A source returns nothing / looks wrong:** `uv run auto-app source plan` (what each search really asks the site, and
  which fields aren't applied). `uv run auto-app collect probe <source> <job id>` fetches one job page and shows what
  was read. `uv run auto-app source login <source> --verify` checks a login.
- **Collected but no jobs appeared:** `uv run auto-app run --no-collect` (parse → normalize → dedup what's saved, and
  report failures); `uv run auto-app data inspect [<source> [<job id>]]` (saved postings, their parsed fields).
- **Jobs not judged:**
  - `uv run auto-app job list --state not_evaluated --json`;
  - `uv run auto-app filter unjudgeable --json` (jobs a filter gave up on; retry with `filter run --job N`);
  - `uv run auto-app filter run --dry-run` (what would be judged).
- **A filter's answers "couldn't be read":** its command drifted from the answer convention (`filter-authoring`). After
  a `parser.py` change: `uv run auto-app filter reparse --dry-run`, then without `--dry-run`.
- **A job vanished:** it expired (none of its postings was seen for its TTL; it goes by itself at the next refresh).
  Look in `uv run auto-app job list --archived --json`. Never-judged jobs are deleted, so they aren't there. If a
  source was blocked or logged out for days, its jobs can expire though still posted: they come back as new jobs
  once it collects again.
- **"retention failed"** in a round's problems: the daily cleanup of old raw data hit an error. It retries next
  round; `uv run auto-app data purge --dry-run` shows the error too.
- **Collecting twice:** an old daily `schedule` alongside `serve`: `uv run auto-app schedule show`.
- **The full reasoning of a judgment:** `uv run auto-app filter why <id> --trace`.

## Rules

- Explain in plain words: what happened, whether it fixes itself, and what (if anything) the user must do.
- Don't run long or costly commands (collecting, judging) to "test" without asking.
- Never read or print `.env`, passwords or the profile's `accounts:` section.
