# `auto-app ui`: every interactive part in one app — 2026-10-04
**Step:** 2–3   **Status:** done. Tested offline (Textual pilot).

## What
- **`auto-app ui`** opens one full-screen app with three sections, switched with the function keys:
  - **F1 Jobs:** the job pool and your review (`job browse`);
  - **F2 Audit:** random samples of each filter's "no" jobs, with the evaluation panel (`filter audit`);
  - **F3 Inbox:** Step 3's hand-offs, failed and done applications (`apply inbox`).
- **A section bar at the top** shows what's waiting in each, e.g. `Audit (8 left)`, `Inbox (2 waiting, 1 failed)`. The footer lists only the current section's keys. The command palette (`ctrl+p`) has "Go to Jobs / Audit / Inbox".
- **Each section keeps its state** while you're elsewhere, and reloads when you come back. Switching is ignored while a pop-up is open.
- **The three commands are shortcuts** that open the app on their section with their options: `job browse` (`--state`, `-f`, `-s`, `-c`), `filter audit` (draws samples first), `apply inbox`. Jobs and Audit use separate data views, so narrowing Jobs never narrows the audit.
- **A section that can't open** (e.g. a broken `config/apply.yaml`) says why.

## Internal
- The two apps became screens:
  - `process/job_browser.py`: `JobsScreen`, also used for Audit with `audit_mode`;
  - `apply/inbox.py`: `InboxScreen`.
- `src/auto_app/ui.py` (new): `AutoApp` installs the three screens, switches with `switch_screen`, keeps the theme (follows the system) and the section bar.
- Each screen reloads on `on_screen_resume`.
- `cli.py`: `ui`, and `_run_ui` behind the three shortcuts.

## Tests
- `tests/test_ui.py`:
  - F1 / F2 / F3 switch sections; a search in Jobs survives a trip to Audit and Inbox;
  - a draw shows in the bar;
  - a job queued in Jobs appears in Inbox when you come back;
  - switching is ignored while the state menu is open;
  - a missing section explains itself;
  - the palette commands;
  - `ui` needs a terminal.
- The review, audit, job-browse and inbox tests now run the sections inside `AutoApp` (`tests/ui_helpers.py`).

263 tests pass (264 with the addition below).

## Added the same day: the mouse, and `[` / `]`
- **Clicks:** a section in the top bar or a tab in a section's tab bar switches to it (clicking a row already selected it).
- **`[` / `]`** go to the previous / next section, for terminals where F-keys are taken (F3 = find next in VS Code's terminal; media keys on a Mac). They aren't priority keys, so in a search or reason box they're typed as usual.
- The bar's hint reads "click, F1 / F2 / F3, or [ ] to switch".
- Test: `test_click_a_section_or_a_tab_and_brackets_cycle`. 264 tests pass.

## Added the same day: a compact footer and `?` for every key
- **Every section's footer is compact,** and the less-used keys are kept off it: `tab`, `←` `→` / `1`–`9`, `r` reason, `o` open link, `s` / `c` narrowing. Measured: Jobs ≈ 143 characters, Audit ≈ 115, Inbox ≈ 96, so one line in a normal-width terminal (otherwise the footer scrolls with the mouse wheel).
- **`?`** opens a panel listing every key the current section offers in its current tab or mode, each with a one-line explanation (each screen's `KEY_HELP`). It closes with `Esc`. In a search box `?` is typed as usual, and switching sections is ignored while the panel is open.
- Test: `test_question_mark_lists_the_sections_keys`. 265 tests pass.
