# 2026-10-05 — The agent fills (f) and submits (s); starting a job is a trial run

## What
- **Starting a job (`enter` / `R` / `r`) is a trial run of the whole application**, your call ("check that the agent can actually fill the form for me, so that when I say submit it submits without issue"). The agent signs in or creates the account if the site asks, then fills every step (Next allowed; each step saved on the site as a draft). It stops on the last step **without the final submit**.
  - Ready to submit: it can be submitted.
  - Needs input: a question blocked a step. Answer it, then `r`.
  - Hidden browser by default; `AUTO_APP_APPLY__AGENT_HEADLESS=false` shows it.
- **`f` (fill only)** for agent jobs: the same walk in a **visible** window, which stays on the last step for you to check and submit. Close it, then `a` if you submitted.
- **`s` (submit)** for agent jobs: the same walk, then the final submit. It counts only with a confirmation page. Without one → **Action needed** ("check whether it went through"), never another applier, so you can't apply twice.
- **A new question on the way** → Needs input, with your answers kept.
- **`f` and `s` on the Action needed tab** too. Before, `f` did nothing there (NVIDIA).
- The agent's progress shows on the status line during `f` / `s` as well.
- **Accounts: sign up by default** (your rule). Where an application needs an account, the agent signs in only with account info for that site: an account on record, or a password you set for it with `--site`. Otherwise it **signs up** with your email and password, falling back to sign-in if the email is already registered.
  - It needs at least a **valid email** and a **valid password**. `apply account set` refuses weak passwords (8+ characters, upper and lower case, a number, a symbol). Missing either → the job waits in Action needed.
  - The new agent tool `account_info()` gives the decision for the site now open.
  - `apply profile` shows "site accounts: email ✓ · password set ✓".
- **The password can be in `profile/profile.yaml`** (your choice). `accounts.password` is for signing up anywhere; `accounts.passwords: {host: …}` is for sites where you already have an account, and those get a sign-in.
  - `profile/` is git-ignored, and **the `accounts:` section is left out of the matcher's prompt**, so it never reaches Claude (tested).
  - The Keychain (`apply account set`) still works as a fallback.
  - `apply profile` checks the password ("in profile.yaml ✓", or what a sign-up would refuse).
- **Fixed: NVIDIA signed in instead of signing up.**
  - **The cause:** an earlier failed sign-in had been recorded as an account ("not confirmed"), and marked as created, because Workday's sign-in page also holds the sign-up form's *hidden* password boxes. So `account_info` said "sign in", which failed with "wrong email or password".
  - **The fixes:** only a sign-in / sign-up that went through is recorded (or a new account waiting for email verification); old "not confirmed" records are ignored; only visible password boxes count.

## Files
- `src/auto_app/apply/agent/applier.py`: `submit`, `_submit_outcome`, waiting for the window in fill only.
- `guards.py`: only the final submit is refused outside `s`.
- `driver.py`: the trial-run prompt.
- `session.py`; `apply/inbox.py`; `core/config.py` (`agent_headless`); `.env.example`; docs.

## Tests
`tests/apply/test_agent.py` (scripted model, a fake two-step form) covers:
- the trial run → ready;
- submit → submitted with the confirmation;
- fill only waits for you;
- no confirmation → action_needed;
- a question asked again → needs_input.

Full suite: 318 passed.
