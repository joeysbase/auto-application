---
name: application-tracking
description: Track the user's job applications in auto-app — the queue of jobs they want to apply to, marking jobs applied or dropped, application history, and nudges about queued jobs. Use for "what's in my queue?", "I applied to #41", "did I apply to Adobe?", "what haven't I applied to yet?".
---

# Application tracking

Applying is **manual** (automated applying is off): jobs marked *want to apply* wait in the Apply section's queue, and
the user applies on the company's site.

- **The queue:** `uv run auto-app job list --state want_to_apply --json`. For each job: title, company, the apply
  link (`apply_url`), and how long it's been waiting (`decided_at`). Ignore application / applier statuses
  (`action_needed`, `ready`, `handoff`): automated applying is off, and they're leftovers from testing it.
- **"I applied to #41":** `uv run auto-app job mark 41 --as applied`.
- **"Not applying to #41 after all":** `uv run auto-app job mark 41 --as not_applied --reason "<why>"`.
- **History:** `uv run auto-app job list --state applied --json`, with `--company Adobe` / `--search …`. Also
  `uv run auto-app apply list --json`.
- **Nudge:** queued jobs waiting more than a few days. Mention them briefly when the user asks what's new.
- **Older history:** applied jobs not seen for a while are **archived** (not lost):
  `uv run auto-app job list --archived --state applied --json`.
- **In the user's TUI:** `auto-app ui` → Apply (F3) → Queued (`enter` opens the link and the CV, `a` applied, `d` not
  applying). Suggest it for working through the queue.
- **The rest of the `apply` group** (`apply run / inbox / profile / policy / login / accounts / account set`) is
  automated applying, which is off. Don't use it.

## Rules

- **Never submit an application** or start automated applying (`apply run`, `serve`'s applying). The user applies.
- Mark *applied* only when the user says they applied.
