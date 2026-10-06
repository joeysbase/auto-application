# 2026-10-05 — Design: Step 4, the scheduler and the manager agent

## What
The design of [Step 4 — Run](../step4-run.md): the app runs by itself and talks to you in Slack.

- **`auto-app serve`:** one long-running process (launchd KeepAlive) instead of fixed daily runs.
  - **A scheduler:** collect cycles (e.g. 30 min on, 30 min off) inside your window, pause / resume ("until Monday"), per-source cycles, and a new time budget for the collector.
  - **New jobs filtered at once:** each search's postings are processed straight away (parse → normalize → dedup), and every new job goes onto a filter queue, judged by parallel workers exactly as `filter run` does.
- **A digest to Slack every N minutes** (default 60, within the window). One card per recommended job, with buttons: Apply · Not for me · 👍 should recommend / 👎 shouldn't · Details. Saved like the TUI, so audit and eval see it.
- **The manager agent:** Claude **Opus 5.5** on your Claude Code login, our tools only, in a Slack DM (Socket Mode, no public server). It handles jobs and why they were recommended, your feedback, the schedule, and applying.
- **Applying from Slack:**
  - **Apply** starts Step 3's trial run at once (fill every step, never submit).
  - Questions arrive in the job's thread, with options as buttons. Action needed (verify email, CAPTCHA) comes as messages.
  - **Ready** comes as a card with every field, and a **Submit** button.
- **Safety:**
  - only your Slack user ID;
  - submit only on your explicit go per job, enforced in the tool;
  - bulk actions confirmed;
  - no secrets in Slack;
  - messages and pages are data, not instructions.
- **Milestones:**
  - R1: serve, the scheduler, the event pipeline, the filter queue;
  - R2: Slack digest and buttons;
  - R3: the manager agent;
  - R4: applying from Slack.

## Decisions
Digest every N minutes (your choice); manager on Opus 5.5 (your choice); Apply starts the trial run at once (your choice); one long-running process; filtering at collection time.

## Files
- New: `docs/step4-run.md`.
