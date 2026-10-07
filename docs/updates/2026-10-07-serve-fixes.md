# Step 4 fixes: the service finds `claude`; `serve resume` reaches a running loop — 2026-10-07

Found on the first real `serve install` run: round 1 collected 65 jobs, then judged none.

**1. The launchd service couldn't find `claude`.** Both plists (`serve install`, `schedule install`) set a fixed
`PATH` (`/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`). Claude Code installs to
`~/.local/bin`, so the filter runner failed with "no ANTHROPIC_API_KEY and no `claude` CLI found", the batch counted
as a broken filter, and `serve` stopped.
- `schedule.service_path()` (`src/auto_app/schedule.py`), used by both `build_plist`s: the folders where `uv` and
  `claude` are found at install time, then `~/.local/bin`, then the system folders (no duplicates).
- Reinstall to pick it up: `serve remove` + `serve install`. `AUTO_APP_SELECT__CLAUDE_CLI=<full path>` in `.env`
  still works too.

**2. `serve resume` after a stop left a running loop stuck.** `resume` clears `stopped` and sets
`filter_pending`; the loop judged a pending batch only at start-up, so a loop already running waited forever on
"the last round's jobs are still being judged". Now `Serve.backlog_due()` (pending, not stopped, not paused) is
checked at the top of every tick of `run_forever` (`src/auto_app/serve.py`), so the backlog is judged at start-up
or as soon as `resume` lands, then the next round starts. A paused loop leaves the backlog until resumed.

**Tests** (365): `test_resume_while_the_loop_waits_judges_the_backlog` (hangs on the old code),
`test_a_paused_backlog_waits`, `test_the_service_finds_uv_and_claude`.
