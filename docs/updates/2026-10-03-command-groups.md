# Commands grouped as `auto-app <group> <command>` — 2026-10-03
**Step:** 1–2 (CLI)   **Status:** done

## What
- **The command tree:**

```
auto-app run                      all of Step 1 (unchanged)
auto-app schedule  install · show · remove        (unchanged)
auto-app source    list · show · add · login · health · plan
auto-app collect   run · probe · stats
auto-app process   parse · normalize · dedup
auto-app job       list · show · mark
auto-app filter    list · show · run · why · reparse
auto-app data      inspect · export · import · purge
```

| Was | Now |
|---|---|
| `plugins`, `plugins NAME` | `source list`, `source show NAME` |
| `add-company URL` | `source add URL` |
| `login NAME` | `source login NAME` |
| `sources` | `source health` |
| `plan` | `source plan` |
| `collect`, `probe`, `stats` | `collect run`, `collect probe`, `collect stats` |
| `parse`, `normalize`, `dedup` | `process parse`, `process normalize`, `process dedup` |
| `jobs`, `jobs ID`, `mark` | `job list`, `job show ID`, `job mark` |
| `filters`, `filters NAME` | `filter list`, `filter show NAME` |
| `select`, `select --reparse`, `why` | `filter run`, `filter reparse`, `filter why` |
| `inspect`, `export`, `import-files`, `purge` | `data inspect`, `data export`, `data import`, `data purge` |

- **Old names are removed**, at the user's request; they give "No such command". The option aliases kept for old scripts are gone too: `-c/--company` and `--site` as aliases of `-s/--source`.
- **A group by itself shows its help.** Subcommands are listed in workflow order.
- **Filters and select are merged** into the `filter` group, at the user's suggestion.
- **Unchanged:** options, global options and the `run --scheduled --purge` that launchd calls, so an installed schedule keeps working.
- **Messages** in the code now name the new commands (hints, `--help` texts, "next step" lines), as do the committed company plugins' docstrings.
- **Step 3's planned commands** follow the same pattern: `apply tailor | review | submit | manual | mark-applied | list`.

## Changes (files)
- `src/auto_app/cli.py`: six group Typer apps, the commands moved into them, split commands (`job list` / `show`, `source list` / `show`, `filter list` / `show` / `run` / `reparse`), and the workflow order.
- Messages: `pipeline.py`, `collect/report.py`, `collect/plan.py`, `collect/runner.py`, `collect/guard.py`, `process/normalize.py`, `process/jobs_view.py`, and docstrings.
- Tests: CLI calls updated. New `tests/test_cli_structure.py` checks:
  - every subcommand exists;
  - a bare group shows help in workflow order;
  - 19 old names are gone, and the legacy option aliases too.
- Docs:
  - `README.md`: Contents, a command tree, common workflows (with a Step 2 one), and the reference regrouped into one section per group. Every `auto-app …` command written in the README was checked against `--help` (27 commands).
  - `docs/step1-collect.md` §10, `docs/step2-select.md` §10, `docs/step3-apply.md` §8, `docs/README.md`.
- Earlier progress entries keep the names they had at the time.
