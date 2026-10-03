# Step 2: DeepSeek models with a DeepSeek API key — 2026-10-03
**Step:** 2   **Status:** done. Tested offline with a mock HTTP transport; not run against DeepSeek yet (no key here).

## What
- **A DeepSeek runner** (`src/auto_app/select/runner_deepseek.py`) for filters whose `model` starts with `deepseek-` (e.g. `deepseek-chat`, `deepseek-reasoner`).
  - It calls `POST {AUTO_APP_SELECT__DEEPSEEK_BASE_URL}/chat/completions` with `DEEPSEEK_API_KEY`, OpenAI-style.
  - It uses the same loop as the Claude API runner: per-job budget, a wrap-up with `tool_choice: none`, and a trace.
  - Retries on 429 / 5xx / network errors; other HTTP errors become run errors, retried on the next `select`.
  - The assistant's `reasoning_content` isn't sent back.
- **Tools, all run by the app:**
  - `read_file`: the filter's folder only.
  - `web_fetch`, our own:
    - public http(s) only, with linkedin.com refused;
    - loopback, private, link-local, reserved and multicast addresses refused, so a manipulated posting can't probe your network;
    - every redirect hop checked again;
    - 2 MB and 20 s limits; scripts and styles stripped, HTML turned into text, 20k characters returned.
  - `web_search`: not available with DeepSeek. It's skipped, with a note from `select` and in the trace.
- **Routing:** per filter, by model. DeepSeek filters always use DeepSeek; Claude filters keep `AUTO_APP_SELECT__RUNNER` (`auto` / `api` / `claude-cli`). Mixed filters run in the same `select`. A DeepSeek filter without `DEEPSEEK_API_KEY` is a clear error.
- **Config / `.env`:** `DEEPSEEK_API_KEY` (empty in your `.env`, for you to fill in) and `AUTO_APP_SELECT__DEEPSEEK_BASE_URL`. DeepSeek prices aren't preset: add them to `AUTO_APP_SELECT__PRICES` for cost tracking and `--max-cost`, which refuses unpriced DeepSeek models.

## How to use
1. Put `DEEPSEEK_API_KEY=…` in `.env`.
2. In a filter's `filter.yaml`, set `model: deepseek-chat`. Leave `tools: [web_fetch]`, since `web_search` would be skipped. If the command relies on searching (e.g. sponsorship history), point it at specific pages to fetch instead.
3. Optionally add the price: `AUTO_APP_SELECT__PRICES={"deepseek-chat": {"input_per_mtok": …, "output_per_mtok": …}, …}`.
4. Run `auto-app select --limit 5`.

## Tests
- `tests/select/test_runner_deepseek.py`, 6 tests:
  - the request shape (URL, auth, messages, tools, no `web_search`);
  - the tool loop (`read_file`, `web_fetch`, an outside path refused, `reasoning_content` dropped);
  - the budget wrap-up;
  - retries, and an error on 401;
  - `web_fetch` refusing LinkedIn, private and loopback addresses and the cloud metadata IP, stripping scripts, and checking redirects;
  - the CLI: missing key, the `web_search` note, `--max-cost` needing a price.
