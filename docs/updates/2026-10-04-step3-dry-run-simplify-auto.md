# 2026-10-04 — Step 3: starting a job is a dry run; automated Simplify built

## What
At your request ("when press start on a job, it should try to fill the form and collect all the info needed"), starting a job — `enter` / `R` in the Inbox, or `apply run` — now **fills the real form in a headless browser and reads it back**. Nothing is submitted.
- A required field still empty → a question for you → **Needs input**. This covers a field the API didn't list, and an answer that didn't take (e.g. not one of the dropdown's options; the note says "“X” didn't take on the form").
- Every required field filled → **Ready to submit**.
- Every field is recorded with where its value came from: `simplify`, `form`, profile, `bank:<key>`, claude, you.

**`simplify_auto` (automated Simplify) is built** and is first in every default chain except LinkedIn Easy Apply. It runs in Playwright's Chromium with the Simplify Copilot extension and your logged-in profile (`browser-profiles/simplify/`). Starting a job:
1. On a job description page, click Simplify's "Start Application" (or the site's Apply button), following a new tab if one opens.
2. Answer Simplify's one-time cookie notice ("Before you autofill") with **Essentials only**. `AUTO_APP_APPLY__SIMPLIFY_CONSENT=accept` changes that.
3. Click **Autofill This Page** and wait until the form stops changing.
4. Read the form. Required fields Simplify left empty are answered from your profile and answers.yaml (with Claude where needed) and filled.
5. Read the form again and put the job in Needs input or Ready to submit.

Submitting repeats the autofill in a browser, fills in your own answers, then submits (or `f` fills only).

If Simplify's panel doesn't appear (not logged in, no form, panel changed), the job goes to the next applier: Greenhouse, then the Simplify hand-off, then manual.

**Greenhouse** answers from the API as before, then runs the same dry run on its hosted form.

## The form reader (`apply/form.py`)
- `scan_form` reads every field's id, label, type, whether it's required, its current value, and options for required, empty dropdowns.
- Labels come from, in order: `label[for]`, aria-labelledby / aria-label, an enclosing label, a fieldset legend, then **the text around the field**. That last one is for forms without labels, like Rippling: the text of the field's own container, or for a field sharing a container (a phone's country-code picker), its first line.
- Radio groups take their question's text, not their first option.
- A dropdown's own search box ("Search", with no question around it) is skipped.
- Fields are deduplicated by id, both on the page and when saving questions.
- `reconcile` merges the read-back with the questions collected so far.

## Checked on real forms (nothing submitted)
- **Rippling job #72** (on a scratch copy of the DB):
  - Simplify filled 9 fields from your real Simplify profile.
  - The 2 questions left for you are both genuine: "Were you referred … by an employee?" and "What is your salary range expectation?". So it lands in Needs input.
  - Before the label fixes, that list was 3 unreadable entries ("Search", two random ids).
- The 4 Greenhouse jobs from earlier fill as before.

## Also
- `apply login simplify`: the temporary debug output was removed.
- **The browser can't start → a clear reason.** Previously a launch crash reached the queue as a cut-off `TargetClosedError` and the job silently fell through to the Simplify hand-off.
  - Now the attempt says why: a crash at start, Chromium not installed, or the first line of the error.
  - The full log goes to `data/logs/browser-launch.log`.
  - The job still moves on to the next applier.
- **Fixed: a job set back to Want to apply after it was applied or dropped never came back.** Its finished application kept it on Done. It's now queued again (Queued tab), and the old application stays on Done as history, marked "re-queued".
- **Inbox messages say where the job went.** `a` gives "applied → moved to the Done tab (and Jobs, F1 → Applied)"; `d` gives "dropped → …".
- **Jobs screen: a job you change stays in the tab you changed it in until you quit the app** (so you can see it and undo). Before, nothing marked it; its state now shows with an arrow, e.g. "→ Want to apply" in the Applied tab.
- **Fixed: Playwright's Chromium crashed at start in your terminal** (every `simplify_auto` start, `apply login simplify`).
  - **Cause, from the macOS crash reports:** ~/.zshrc exports `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. Chrome for Testing isn't a hardened app, so macOS honours the variable. ImageIO then loads Homebrew's libpng / libjpeg / libtiff / libgif in place of its own, and its decoder setup crashes (SIGBUS `EXC_ARM_DA_ALIGN at 0xbad4007`). The "Mach rendezvous failed" lines are its helper processes dying afterwards. It isn't a sandbox, as first thought.
  - **Why it worked from Claude Code's shell and for the LinkedIn login:** macOS drops `DYLD_*` when it starts system shells like `/bin/zsh`, so Claude Code's shell never had the variable. Your Google Chrome is hardened and ignores it.
  - **Fix:** `core/browser_env.py` starts every browser (Simplify, the form filler, the job-site sessions) with your environment minus `DYLD_*`. Reproduced with the variable set: it crashed before the fix and starts after it.
- **Added: `r` on Hand-offs (and Failed) starts over.** The application goes back to the queue (`Applications.requeue`; its attempts stay) and is started again in the background, the chain from the top. Before, a hand-off couldn't be retried, and `r` on Failed ran the chain on the UI thread, which would freeze the Inbox during a dry run. An application sent back while another start is running waits in Queued (R starts it).
- **Fixed: `r` looked like it did nothing.** Applications created before `simplify_auto` existed kept the chain they started with (`simplify → manual`), so starting over went straight back to the hand-off. `requeue` now takes the chain the policy has today.
- **Fixed: forms embedded in an iframe went to the hand-off** ("no application form on the page"), e.g. a careers page with `?gh_jid=`.
  - The "no form" check no longer blocks Simplify; its own step finds the form.
  - An application form embedded from an ATS (Greenhouse `embed/job_app`, Lever, Ashby, Workable, SmartRecruiters, Jobvite, iCIMS) is opened directly, where Simplify shows "Autofill This Page".
  - Checked on Quantifind (job #3): Simplify filled 18 fields and answers.yaml 1; 2 questions are left for you.
- **Fixed: `enter` / `r` did nothing while another job was starting.** Only one start runs at a time (one Simplify profile), and a second one was silently ignored. Now it waits its turn and starts automatically afterwards.
  - The status line says "job N: starts when the current one is done (k waiting)".
  - The Queued list shows *starting…* or *waiting* per job, and the tab bar shows "starting… (k more waiting)".
  - Jobs that finish meanwhile appear in their tabs right away.
- **Fixed: the section bar's "N queued" missed jobs sent back with `r`.** It now uses the same count as the Queued tab.
- **Added: progress per job while starting several.**
  - `Applications.run(progress=…)` reports each job's start and its result.
  - The status line says "starting job N — about a minute … k done, m more after it", then "job N → Needs input · k done, m to go".
  - In Queued, the job being filled says *starting…*, the rest *next* / *waiting*. Each finished job appears in its tab right away.
- **Added: answering moves on by itself.**
  - After an answer in Needs input, the prompt opens on the job's next open question. When the job is complete ("job N is ready to submit"), it moves to the first question of the next job.
  - An answer that isn't one of the options re-opens the same question. Esc stops.
  - Chained answers apply to that job only. `A` / `S` still apply to every job / save.
- **Added: multi-step forms (Adobe, Workday-like wizards).**
  - Before, the dry run read step 1 only and called the job ready, and filling stopped at step 1.
  - Starting a job still fills and reads **step 1 only**: on Adobe, Next posts the step to the site (`applySubmit`, behind an invisible reCAPTCHA that also blocks headless browsers), which isn't something a dry run should do. Its note says "step 1 of a multi-step form; the next steps are filled when you submit".
  - Submit / fill only walk every step (`simplify.walk`): Simplify autofills each one, your answers fill the rest, then Next, up to the step with the final button. A new required question on the way sends the job back to **Needs input** with your answers kept, instead of to the next applier.
  - Fields in hidden sections (later steps already in the page) are no longer read.
  - Tested on a fake two-step form (offline). On Adobe, step 1 is fully filled; going further needs the visible window (`f`).
- **Added: questions with fixed answers are picked from a list**, not typed (your request).
  - Dropdowns and radios get an option list (↑↓ enter). A checkbox gets "Yes — check it" / "No — leave it unchecked". A multi-select gets a tick list (space ticks, enter saves).
  - `e` on a choice question takes just its number, then the list.
- **Clearer reason when the Simplify browser is already open** (e.g. a fill-only window): "one window at a time: close it, then r".
- **Fixed: `f` went straight to Hand-offs while a job was starting in the background.** Both needed the Simplify profile, and only one browser can open it. Starting and submitting now take turns (`Applications.browser_lock`): `s` / `f` wait for the current start ("a browser opens after job N has started"), and starts wait while a fill-only window is open.
- **Fixed: pressing `enter` again on a job that was already starting or waiting added it to the line again.** Now it's ignored ("already starting or waiting — it starts as soon as the browser is free").
- **Fixed: `f` on Adobe went to Hand-offs after step 1.** On step 2, Simplify's "Autofill This Page" button was disabled (Simplify autofills a new step by itself), and the click waited 30 s, then failed. Later steps now click it only when it's enabled, else just wait for the step to settle.
- **Fill only that stops partway keeps the job in Ready** (the reason in its note: "fill only stopped: …") instead of handing it to the next applier.

## Files
- New: `src/auto_app/apply/form.py`, `src/auto_app/apply/simplify.py`.
- Changed:
  - `apply/fill.py`: `dry_run`, `before_fill`, `_pick` for native selects.
  - `apply/greenhouse.py`: dry run + reconcile.
  - `apply/appliers.py`: `ApplyContext.simplify_consent` / `dry_run`, the registry.
  - `apply/queue.py`: the job's title and company passed to appliers; questions deduplicated.
  - `core/config.py`: `simplify_consent`.
  - `.env.example`, `cli.py`.

## Tests
- `tests/apply/test_dry_run.py` (offline, fake pages via Playwright routes):
  - scan + reconcile;
  - Greenhouse with a field the API didn't list;
  - `simplify_auto` with a fake Simplify panel (consent → Essentials only → autofill → the gap filled from answers.yaml → one open question → answer → submit);
  - fallback when the panel is missing;
  - Rippling-style labels (container text, phone country code, radio group question, skipped search box).
- Full suite: 289 passed.
