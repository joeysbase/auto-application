# 2026-10-05 — The agent signs in / creates site accounts; live progress; clicks through overlays

## What
After NVIDIA's Workday job stopped at "Sign In" (the agent correctly ended with action needed: no accounts yet), and at your request:

- **Site accounts**, with the email and password **you** provide ([docs/step3-agent.md §6, §15](../step3-agent.md)):
  - The email: `accounts.email` in `profile.yaml` (default: your email).
  - The password: `auto-app apply account set` (optionally `--site HOST`) saves it in the **macOS Keychain**, never in a file.
  - The agent's new `fill_secret` tool types the email or password. **Claude never sees the password**, the logs mask it, and the agent can't type a password itself.
  - The agent signs in first, and creates an account only if there's none.
  - A terms / privacy box on a create-account form is ticked only if you set `accounts.accept_terms: true`.
  - Email verification or codes → **Action needed** (verify, then `r`).
  - Each account is recorded (site = the full host, since Workday accounts are per company; created or signed in; "verification pending"). `auto-app apply accounts` lists them. The job's note says "created an account on …" or "signed in to …".
  - In every mode, including the trial run when you start a job.
- **Live progress** on the Apply status line: "job 59: clicked “Apply” · 0 done, 3 more after it", "job 59: signing in", "job 59: asking you “…”".
- **Clicks through overlays:** a click blocked by an overlay / banner is retried directly on the element, instead of timing out.
- **Fixed (from the first real runs):**
  - **Stale refs.** Each snapshot now clears the old ref tags. Before, NVIDIA's "Sign in with email" (the new `e4`) clicked the job page's old `e4`, a header link to nvidia.com, which the guard blocked.
  - **Account buttons and pages aren't "Next".** Amazon's "Proceed to create account" was refused as a Next in a dry run. Now account buttons (create account / sign in / sign up / log in / register) and Continue on a page titled sign in / create account / verify email are allowed.
  - **Workday's sign-in button is labeled "Submit".** In fill only / submit it signs you in on a sign-in form, and isn't taken for the application's submit. In a dry run, signing in is refused like everything else that sends data (your rule).
  - **Honeypots:** a field "for robots only" (Workday: "do not enter if you're human") is refused by `type` and named in the prompt.
  - **Account notes are accurate:** "signed in to …" / "created an account on …" only when the run went on. Otherwise "tried to sign in to … as <email>", and the record says "not confirmed".
- **Action needed has `r` (start over)**, e.g. after you verified your email or set the account password.
- **Starting a job (`enter`) is a trial run of the whole application** (your call, after briefly limiting it to step 1 with nothing sent). The agent signs in / creates the account if asked, and fills every step: Next / Continue is allowed, and each step is saved on the site as a draft. It stops on the last step **without the final submit**, which only `s` does. Ready to submit then means it can be submitted. A step blocked by a question for you ends as needs_input: answer, then `r` to go on. `AUTO_APP_APPLY__AGENT_HEADLESS=false` shows the browser.

## Before trying it on a real site
`profile/profile.yaml` is still the fake Alex Doe profile, so `accounts.email` would be `alex@example.com`. Put your real details (or at least `accounts.email`) there first, then run `auto-app apply account set`.

## Files
- New:
  - `src/auto_app/apply/accounts.py`;
  - the migration `4bc3cd20fcd6` (`accounts` table).
- Changed:
  - `apply/agent/session.py`: `fill_secret`, the password and terms guards, sign-in Next, progress, clicks;
  - `apply/agent/guards.py`, `driver.py` (the tool, the prompt), `applier.py` (the account record);
  - `apply/profile.py`: `Accounts`;
  - `apply/inbox.py`: the agent's progress;
  - `apply/appliers.py`, `cli.py`: `apply account set`, `apply accounts`;
  - `core/db/tables.py`;
  - `profile.example/profile.yaml`, README.

## Tests
`tests/apply/test_agent.py` covers:
- sign-in;
- account creation and the terms box;
- no password;
- the overlay click and progress;
- the Keychain (faked) and the records.

Full suite: 312 passed.
