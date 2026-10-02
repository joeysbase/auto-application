# `--debug-files` is a file-only mode — 2026-10-01
**Step:** 1   **Status:** done

## What
`--debug-files` (or `debug_files: true` in the config) now makes every command use the **file store** (`data/raw/`) **instead of** the database — postings, batches, sightings, parse and normalize results are written as files, and nothing is written to the database. Before, it wrote both (the database plus a mirrored file copy).

- Jobs live only in the database, so in file mode `dedup` can only `--preview`; `run` previews its dedup step and skips purge; `jobs`, `purge`, `export` and `import-files` stop with a message explaining why.
- Files from a file-mode run can be loaded into the database later with `auto-app import-files`.

## Why
Requested: debug runs should produce plain files without touching the database.

## Changes
- `cli.py`: `_store()` returns the `FileStore` in debug mode (else the `DbStore`); `_require_db()` guards database-only commands.
- `core/db/store.py`, `core/db/purge.py`: the file-mirroring code removed (no longer used).
- `collect/store.py`: `FileStore.posting_links()` (empty — no jobs in file mode), so `dedup --preview` works on files.
- `pipeline.py`, `process/link.py`: dedup runs as a preview when the store has no jobs.
- README (global options, workflows, where data goes), config comments, Step 1 doc §8.1.

## Tests
115 tests: store choice (flag and config), a full `run` in file mode on the fake site writes files and no database (dedup previewed, purge skipped), database-only commands refuse in file mode while `dedup --preview` works. The old mirroring test was removed. Lint clean.
