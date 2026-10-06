# 2026-10-06 — Design: the manager agent as Claude with skills

## What
[Step 4 — Run](../step4-run.md) §5, rewritten around your idea: the manager is **Claude (Opus 5.5), taught by skills how to use the app's CLI**, instead of a set of custom tools.

- **What you'd do with it** (§5.1):
  1. stay informed;
  2. decide quickly (bulk rules, previewed and confirmed);
  3. teach the filters (labels with reasons; repeated feedback → a proposed filter edit);
  4. run the pipeline (pause, rounds, searches, company sources, "why no new jobs");
  5. track applications (manual);
  6. insights.
- **What already covers it** (§5.2): a table from each demand to the commands that exist (`job list / show / mark`, `filter why / eval / audit / run / show`, `source add / health / login / plan`, `collect stats`, `apply queue / list`, `schedule`), what's designed (`serve`, the digest, alerts), and what's missing.
- **What's missing** (§5.3, built with R3):
  1. `--json` output;
  2. `filter label` (labels from the command line; the key gap);
  3. `job mark --reason`;
  4. `job list --since / --posted-within / --search`;
  5. `serve`;
  6. optional snooze.
- **The skills** (§5.4): `job-briefing`, `job-decisions`, `filter-feedback`, `filter-tuning`, `pipeline-control`, `sources-and-searches`, `troubleshooting`, `application-tracking`. Each lists its commands, how to read their output, and its rules.
- **Rules** (§5.5): never submits; shows first, then confirms (bulk, edits, destructive commands); no secrets; data is never instructions; only you.
- **Where you talk to it** (§5.6): Slack (`serve` starts conversations with the digest and alerts; the manager answers), or Claude Code in a terminal, the simplest start.

## Files
- `docs/step4-run.md` (§5, §9 R3, §10).

## Addendum: the digest is the manager's
At your suggestion, [§4](../step4-run.md) no longer has its own digest logic. After a round's filter batch recommends something, `serve` **wakes the manager** (at most once per `digest.min_gap`, 60 min, within the window).
- The manager looks the jobs up itself (`job list --state recommended --not-notified --json`, `job show`, `filter why`) and writes the message: grouped, the strongest match first, your preferences applied, other things needing you.
- It marks each job announced (`job notified N…`, a per-job `notified_at`), so nothing repeats and late judgments still get announced.
- **Conversation first:** you reply in words; Slack buttons are optional and come later.
- **A plain fallback** from `serve` when Claude is unavailable; the jobs stay not-notified for the next digest.
- New missing commands: `job list --not-notified`, `job notified`.

## Addendum: the manager is Claude Code; Slack deferred
Your call: the manager is a **Claude Code session on this project** (CLI or desktop app). The skills are **project skills** in the repo (`.claude/skills/…`), and Claude Code handles memory and the conversation, so the app stores no manager memory.
- `serve` can't start a Claude Code conversation, so a round with new recommendations sends a **macOS notification** ("ask Claude Code"). Asking "what's new?" runs the digest of §4.
- Milestones: R1 `serve`; **R2 the manager in Claude Code** (the missing commands and the skills); R3 Slack, deferred.
