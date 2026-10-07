---
name: job-decisions
description: Record the user's decisions on jobs in auto-app — want to apply (queue it) or not applying, with reasons, one job or many by a rule ("skip anything in NYC"). Use when the user says apply to / skip / not interested in / queue jobs.
---

# Job decisions

A decision is `want_to_apply` (the job goes to the Apply section's queue; the user applies by hand) or `not_applied`.
It's recorded like the app's `a` / `n`, with each filter's verdict, so the filters learn from it.

## One job, or a few the user named

```
uv run auto-app job mark <id> [<id> …] --as want_to_apply --reason "<why, in the user's words>"
uv run auto-app job mark <id> [<id> …] --as not_applied   --reason "<why>"
```

Always pass the user's reason when they give one ("too senior", "NYC", "sales, not engineering"). It's the most useful
signal for improving the filters.

## Bulk rules ("skip anything in NYC", "apply to all the ML ones")

1. Find the jobs: `uv run auto-app job list --state recommended --json`, plus `--search` / `--company` /
   `--posted-within`. Read `job show <id> --json` where the list isn't enough to decide.
2. **Show the list first:** "#41 Acme — ML Engineer (New York) · #57 …: mark these 3 not applied?"
3. Act only after the user confirms. With **more than 5 jobs**, always confirm, even if they sounded sure.
4. Then report what changed.

## Rules

- **Never submit an application,** and never run `apply run` or the automated appliers. Applying is manual for now:
  the user does it.
- Don't change a job the user already decided (applied) without asking.
- Repeated reasons ("NYC" again and again) may be worth a filter change: suggest the `filter-feedback` skill, but
  don't edit filters here.
