# Config & data inside the project folder — 2026-09-28
**Step:** —   **Status:** done

## What
Default home moved from `~/.auto-application` to the project root: `config/searches.yaml`, `browser-profiles/`, `data/`. All git-ignored. `AUTO_APP_HOME` still overrides.

## Why
Everything browsable from the open project; no second window.

## Changes
`src/auto_app/settings.py` (`PROJECT_ROOT`), `.gitignore`, CLI help, README, config example header.

## Tests / verification
Suite passes; `git check-ignore` confirms the three paths are ignored.
