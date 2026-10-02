# One plugin = one source; per-source searches (C12) — 2026-10-01
**Step:** 1   **Status:** done

## What
- **Each plugin is one source.** The source name (config key, storage name, `-s NAME`) is the plugin's `name`.
- **Platform code became base classes** in `src/auto_app/platforms/`: `WorkdaySource`, `AshbySource`, `GreenhouseSource`, `LeverSource` (moved from `plugins/platforms/`). A base has no `name`, so it is not a source.
- **Each company is a small plugin file** in `plugins/companies/` that subclasses a base and sets `name`, `company` (display name) and `board`. NVIDIA, Adobe and UiPath were converted:
  ```python
  class Nvidia(WorkdaySource):
      name = "nvidia"
      company = "NVIDIA"
      board = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
  ```
- **"Single employer" comes from the plugin** (`company` set), not from the config.
- **Config:** `sources.<plugin name>` holds only `searches` (named; unique per source) and `limits`. `job_sites:` and `companies:` are gone.
  - `from: <source>/<search>` copies another search's generic fields. Fields set in the search override the copy, and an empty list clears a copied field.
  - `site_url` and `site_filters` belong to one source and are never copied.
  - A source with no searches doesn't run.
- **Validation is per plugin, the same for every source:**
  - `site_filters` are checked against the plugin's schema;
  - `site_url` is accepted only on browser sources;
  - a field the source can't apply is an error when written in the search itself, and a notice (deferred to Step 2) when copied with `from`;
  - also errors: a bad, unknown or looping `from`, a config entry without a plugin, `-s X` when X has no searches, and `--search` names that match nothing.
- **Plugins are checked when loaded:**
  - names must be valid and unique;
  - platform-based plugins need a `company` and a valid `board` (normalized once);
  - a broken plugin file is reported by `auto-app plugins` and in config errors, and doesn't stop the other sources.
- **`add-company`:**
  - writes `plugins/companies/<name>.py`, with options `--name` (alias `--key`) and `--company`;
  - adds a commented search stub under `sources: <name>:`, pointing at your first search for `from:`.
- **`plugins`:** lists sources (job site / company + platform, file), the available platforms and any broken files. `plugins <source>` and `plugins <platform>` show search support and `site_filters`.
- **CLI:** `-s/--source NAME` (alias `--site`) for every source. `-c/--company` is kept as an alias for old scripts. `--job-sites` and `--companies` select by kind.
- **Processing reads company info from the registry:** parse (company hint), normalize (company aliases, boards) and dedup (company-source flag). Parser ids keep the platform name (`workday:workday-2`), so nothing re-parses.
- **Migration:** the `job_sites` + `companies` layout (and the older top-level `searches` layout) still load:
  - job-site searches become one search per site;
  - `search_from` becomes `from:` entries;
  - a company's `search` becomes the search `main`;
  - a company without a plugin file gets an error that prints the `add-company … --name … --company …` command creating it.
- Your `config/searches.yaml` was rewritten in the new layout; the NVIDIA search is now `nvidia/new-grad`. The old file is in the session scratchpad and isn't needed (the plans are identical).

## Why
- Sites offer different search features, so each source needs its own searches.
- Companies needed named searches like the sites.
- One concept ("a source is a plugin") replaces two config sections.
- What a source is (stable code: platform, board, name) is now separate from what you search there (your settings).
- Company quirks now have a home: the company's own plugin file, instead of the shared platform code.

## Changes (files)
- `src/auto_app/platforms/` (new; moved from `plugins/platforms/`), `plugins/companies/{nvidia,adobe,uipath}.py` (new).
- `src/auto_app/sources/api.py`, `sources/base.py`: `company`, `platform`, `board` on plugins.
- `src/auto_app/sources/registry.py`: source detection, load-time checks, `broken`, `companies()`, `add_class`.
- `src/auto_app/core/config.py`: `SourceSearch`, `SourceConfig`, `sources`, migration, `limits(source, plugin)`.
- `src/auto_app/collect/plan.py`: rewritten (`from` chains, per-plugin validation, `Selection(sources, search_names, job_sites, companies)`).
- `src/auto_app/collect/runner.py`, `process/{parse,normalize,dedup,link}.py`, `pipeline.py`, `cli.py`.
- `config/searches.yaml`, `config/searches.example.yaml`.
- Tests: `tests/plugins/companies/` (test-only company plugins), `tests/helpers.py`, `tests/collect/test_plan.py` (rewritten), `tests/test_add_company.py` (new), the others updated.
- Docs: `README.md`, `docs/step1-collect.md` (§1, §2.1–2.6, §5, §6, §10, milestone C12).

## How to use
```bash
uv run auto-app plugins                                    # sources (one plugin each) + platforms
uv run auto-app plugins nvidia                             # what nvidia's searches support
uv run auto-app add-company https://jobs.lever.co/palantir --company Palantir   # → plugins/companies/palantir.py + config stub
uv run auto-app plan -s nvidia
uv run auto-app collect -s linkedin -s nvidia --search new-grad
```

## Tests / verification
- 120 tests pass; lint clean. New tests cover:
  - `from` copying, overrides, clearing and loops;
  - named searches and selection;
  - errors for unsupported fields, bad `site_filters` / `site_url` and unknown sources;
  - migration of both older layouts, including the `add-company` hint;
  - load-time plugin checks (missing board or company, duplicates, broken files);
  - the generated plugin file and config stub.
- On the real setup:
  - `plan` gives the same plans as before the change;
  - `parse`, `normalize` and `dedup --preview` work on the 38 NVIDIA postings in `data/raw` (company resolved "via config = company source");
  - a live `add-company https://job-boards.greenhouse.io/anthropic` (637 jobs) wrote a valid plugin and stub against a scratch config. The plugin was removed afterwards.

## Follow-ups
- LinkedIn `site_filters` (Easy Apply, under 10 applicants, salary, companies, distance) — confirm which ones, then verify live.
- `auto-app new-plugin` scaffold for companies with their own career sites.
