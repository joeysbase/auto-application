# 2026-10-05 — Automated applying off: the Apply section is a queue you apply from by hand

## What
At your request ("unplug the submit part … I need to think about it"), the app now **collects, filters and recommends**. Applying is by hand.

- **A new setting:** `AUTO_APP_APPLY__AUTOMATED`, **false by default**.
- **Apply section (F3): only the Queued tab.** Jobs you mark `a` in Jobs go there.
  - `enter` / `o` opens the apply link in your browser and your CV in Finder.
  - `a` marks it **applied**. The application is recorded with the manual applier, and the job shows in Jobs → Applied.
  - `d` marks it **not applying** (Jobs → Not applied).
  - The key bar shows just those. Nothing starts, fills or submits, and the section bar counts only queued jobs.
- **`auto-app apply run`** says automated applying is off and does nothing.
- **Kept, unplugged:** the browser agent (trial runs, fill only, submit, site accounts), the Greenhouse applier and automated Simplify all come back with `AUTO_APP_APPLY__AUTOMATED=true`. Applications prepared earlier in your scratch database stay in it, and show again then.
- **Step 4 design:** applying from Slack (R4) is parked. *Apply* in Slack only queues the job.

## Files
- `core/config.py` (`apply.automated`);
- `apply/inbox.py`: Queued only, the manual keys and detail;
- `apply/queue.py`: `applied_by_hand`;
- `ui.py`; `cli.py` (`apply run`, passing the setting);
- `.env.example`, README, `docs/step3-apply.md`, `docs/step4-run.md`.

## Tests
- Manual applying from the queue: only Queued, the keys, `enter` opens the link and CV, `a` records it as applied (manual), `d` drops it.
- `apply run` is off by default.
- The automated tests run with the setting on.
