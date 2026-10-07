---
name: pipeline-control
description: Check and control auto-app's background loop (`auto-app serve`) — status, pause / resume, the schedule (rounds, pause length, window), and collecting or filtering now. Use for "is it running?", "pause until Monday", "make rounds shorter", "collect now".
---

# Pipeline control

`auto-app serve` runs the app by itself:
1. **collect** a round, until every search is caught up or a time cap;
2. **judge** the round's new jobs as a batch;
3. **pause** (counted from the end of collecting), then repeat, inside a daily window.

Settings are in `config/serve.yaml` (template `config/serve.example.yaml`). State is in `data/serve.json`.

## Status

`uv run auto-app serve status --json` gives the status (collecting / filtering / pausing until … / paused / stopped /
waiting for usage / not running), `next_round_at`, `last_round` (each source's new postings, caught up or what stopped
it, judged, recommended), and the effective config. Summarise it in plain words.

## Control

| the user says | run |
|---|---|
| pause (until …) | `uv run auto-app serve pause --until 2h` · `--until 09:00` · `--until "2026-10-12 09:00"` · no `--until`: until resumed |
| resume / start again | `uv run auto-app serve resume` (after a broken filter was fixed, it judges the backlog first) |
| collect now, once | `uv run auto-app serve run --once` (a full round, if `serve` isn't running in the background), or `uv run auto-app run` (collect + process only). `run` options: `-s <source>` (repeatable), `--job-sites` / `--companies`, `--search <name>`, `--time-limit 30m` / `--max-new N` / `--round-pages N` (a round's goal), `--headless`; `--force` ignores a cooldown (avoid: a site blocked it) |
| judge what's waiting | `uv run auto-app filter run` (costs Claude usage) |
| is it installed? | `uv run auto-app serve status` (a pid means it's running); `serve install` / `serve remove` for the background service |

**The old daily schedule** (`auto-app schedule install / show / remove`, fixed times via launchd) is superseded by
`serve`. If `schedule show` lists one while `serve` runs too, suggest `schedule remove`.

## Changing the schedule

`config/serve.yaml` holds:
- `window` (days, start, end);
- `round.time_limit` (cap per source) and optional `max_new_postings` / `max_pages`;
- `interval` (the pause) and `jitter`;
- `sources.<name>`: `time_limit`, `every` (e.g. 6h), `enabled: false`;
- `filter.tries_per_job` / `broken_after`;
- `retention` (default `24h`; `null` = never): how often a round also drops old raw pages, sightings and logs.
  `serve status` shows it on the `cleanup` line. Expired jobs need no setting: they go by themselves.

1. Show the change you'd make (the YAML lines).
2. Edit only after the user's OK.
3. Then say it applies **when `serve` restarts.** If it runs in the background: `uv run auto-app serve remove` and
   `uv run auto-app serve install`.

## Rules

- Confirm before changing settings, removing / installing the service, or anything that collects or judges at length
  (Claude usage, site load).
- Never touch `.env` or `profile/`. Searches, sources and filters: the `app-config` skill.
