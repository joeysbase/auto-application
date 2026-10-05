# 2026-10-05 — Design: the browser-agent applier (A7)

## What
The design of Step 3's **browser-agent applier**, `agent`, in [`docs/step3-agent.md`](../step3-agent.md). It's the next thing to build.

- **Why:** the scripted approach (automated Simplify plus per-case rules) broke on site after site on 2026-10-04: an iframe form, a multi-step wizard, Simplify's changing buttons, unlabeled fields. The hard part was always working out what the page is and what to do next. That's what an agent does.
- **Chains:** `greenhouse → agent → simplify → manual` for Greenhouse; `agent → simplify → manual` everywhere else; LinkedIn Easy Apply stays unautomated. `simplify_auto` stays available until the comparison.
- **Engine:** Claude **Opus 5.5** in a loop (look, act, look again), through the Claude Agent SDK on your Claude Code login (to verify in A7.1).
- **Hands:** **generic browser tools** (accessibility snapshot with refs in every frame, click, type, select, check, upload CV, press, scroll, screenshot). They aren't our site-specific fill functions.
- **Your data:** `answer_for` (profile → answers.yaml → matcher; sensitive questions only from answers.yaml), `draft` (✎ free text), `ask_user` (→ Needs input), `fill_secret`, and `done`.
- **Modes:**
  - start: hidden, step 1 only, Next and submit blocked;
  - fill only: visible, every step, stops before submit;
  - submit: every step, then submit and the confirmation check.
- **Guards in code:** no submit outside `s`; no Next in start mode; navigation kept to the application; sensitive fields accept only your saved answers; passwords never shown to the model; the CV is the only file.
- **Accounts:** created and signed in to with the email and password **you provide**. The password is in the macOS Keychain and typed by `fill_secret`. Email / SMS verification goes to you (action_needed).
- **Limits:** no token budget (tokens shown); a **time limit** per run, 10 min start and 20 min fill / submit.
- **Visibility:** live progress on the Apply status line; a JSONL log per run; attempt summaries with time and tokens.
- **Milestones:**
  - A7.1: tools, guards, start mode; offline tests with a scripted model;
  - A7.2: real start-mode runs on Adobe, Quantifind, Rippling, Greenhouse and Workday;
  - A7.3: fill only / submit through every step, accounts;
  - A7.4: comparison with `simplify_auto`, then you set the chain order.

## Decisions
Browser agent rather than computer use. Generic tools for the hands, our code for guards and data. Opus 5.5. No token budget, but a time limit. Accounts created with your information. Start mode never clicks Next or submit.

## Files
- New: `docs/step3-agent.md`.
- Updated: `docs/step3-apply.md` (§3 appliers, §10 milestones, §13 decisions, status), `README.md` (the applier table).
