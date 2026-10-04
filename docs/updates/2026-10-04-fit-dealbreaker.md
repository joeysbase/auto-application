# The agent answers fit + dealbreaker; preference model deferred — 2026-10-04
**Step:** 2   **Status:** done. Tested offline; the migration checked on a copy of the real database.

## What
- **Output convention:** `{"fit": true | false, "dealbreaker": true | false, "reason": "…"}`.
  - **fit:** does the job meet your requirements? If unsure, true.
  - **dealbreaker:** does a deal-breaker clearly apply (the posting or a lookup shows it)? If unsure, false.
  - **recommend = fit AND NOT dealbreaker.** A dealbreaker alone is enough to say no (`fit` may be left out); couldn't read still means recommend.
  - **Backward compatible:** the older `{"recommend", "reason"}` is still read (fit = recommend, dealbreaker not given). `parser.py` may return either shape.
- **Stored:** `judgments.fit` and `judgments.dealbreaker` (migration `a828cc3c8dc2`; existing rows get fit = recommend). `filter reparse` compares all three fields.
- **Shown:**
  - `filter run` lines: `no — dealbreaker — …` / `no — doesn't fit — …`, and the totals count dealbreaker "no"s;
  - `filter why`: `fit: yes · dealbreaker: no`;
  - the app's detail pane and status line: the same wording.
- **Evaluation "by cause":** a filter's "no"s are split into dealbreaker and "doesn't fit" rejections, with the audited misses of each. A wrong dealbreaker call is the costlier mistake.
- **`filter list`** warns about commands that still ask for `{"recommend": …}`.
- **Examples:** `filters.example/ml-engineer` asks for fit + dealbreaker. `verdict-format` adds a `Dealbreaker: yes | no` line, and its `parser.py` returns the new shape.
- **Preference model (S5) deferred.** With few labels and low volume, it may not beat a good command, and it can't judge dealbreakers. Evaluation decides. If it's ever built, it judges fit only, the agent's dealbreaker vetoes it, and it uses a fixed encoder with a per-filter head (step 2 §7.5).

## Your filters
`filters/ml-engineer/command.md` still asks for `{"recommend": …}`, so `filter list` warns. Update its Answer section the way the example does. That changes its version, so `filter run --rescore` re-judges jobs with the split (costs agent runs); until then, old answers count as "no" without a cause.

## Files
- `src/auto_app/select/parse.py` — `ParsedOutput.of`, `from_dict`, `verdict_text`.
- `src/auto_app/core/db/tables.py`, `migrations/versions/a828cc3c8dc2_fit_and_dealbreaker.py`, `core/db/store.py`.
- `src/auto_app/select/engine.py` — verdict lines, dealbreaker count, reparse.
- `src/auto_app/select/filters.py` — the convention check.
- `src/auto_app/select/audit.py`, `evaluate.py` — "by cause".
- `src/auto_app/select/review.py`, `process/job_browser.py` — the verdict text.
- `src/auto_app/cli.py` — `filter why`, run totals.
- `filters.example/` — both examples.

## Tests
- **Parser:** both parts, a dealbreaker alone, fit alone, the older shape, errors, the verdict wording.
- **End to end:** a fitting job with a dealbreaker is un_recommended; the parts are stored.
- **Evaluation:** misses split by cause.
- **Updated:** filter fixtures and the example tests.

251 tests pass.
