# Global settings and the API key in `.env` — 2026-10-03
**Step:** all   **Status:** done

## What
- **A `.env` file** at the project root (git-ignored; template `.env.example`, committed) holds `ANTHROPIC_API_KEY` and every global setting:
  - job TTL, retention, Step 2 (`parallel`, `prices`, web-search price);
  - run window, minimum hours between runs, retry, `debug_files`;
  - optionally `AUTO_APP_HOME`.
- **Format:**
  - names are `AUTO_APP_<SETTING>`, nested with `__` (`AUTO_APP_JOBS__TTL_DAYS__UNFILTERED=14`);
  - maps are JSON on one line (`AUTO_APP_SELECT__PRICES={"claude-haiku-4-5-20251001": {…}}`);
  - inline `# comments` are allowed.
- **`config/searches.yaml` now holds only what to search** (`defaults`, `sources`). The global sections were moved out of your config and the example.
- **Precedence:** shell variables > `.env` > anything still in `searches.yaml` > built-in defaults. Global settings left in `searches.yaml` still work, with a warning to move them.
- **How it's implemented:**
  - `core/config.py`: `GlobalSettings` (pydantic-settings, prefix `AUTO_APP_`, delimiter `__`), `load_env_file` (python-dotenv, no override), `env_settings`, and a deep merge in `load_config`.
  - The CLI loads `<home>/.env` at startup, so the key reaches the Anthropic SDK through the environment. Commands that run without `searches.yaml` still get the `.env` settings (e.g. `debug_files`).
- **Your `.env`** was created from the template with the same values your config had (permissions 600). `ANTHROPIC_API_KEY` is left empty for you to fill in.

## Why
The user wanted the API key, the TTL and the other global settings in one global settings file, separate from the search config.

## Tests
- `tests/core/test_env_settings.py`, 5 tests:
  - the environment wins over yaml, with nesting, JSON maps and the move-to-`.env` warning;
  - shell variables win over `.env`;
  - invalid values are errors;
  - the template equals the defaults;
  - the CLI loads the project's `.env`.
- 178 tests pass in total.

## Docs
README (setup, configuration, the `purge`, `select`, `--scheduled` and `--debug-files` references, data layout), `docs/README.md` §3, `docs/step1-collect.md` §8–§10, `docs/step2-select.md` §4.
