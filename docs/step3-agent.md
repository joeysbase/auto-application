# Step 3 — the browser-agent applier (`agent`, milestone A7)

Status: **A7.1 ✅ A7.3 ✅** (2026-10-05: tools, guards, start mode — `src/auto_app/apply/agent/`; as built, §14); **accounts ✅**
(pulled forward from A7.3, §15) and live progress; A7.2–A7.4 📐 designed. Part of [Step 3 — Apply](step3-apply.md); it replaces the "generic (later)" row
of §3 there and moves it up the chain.

**Goal:** apply through any application form, however it's built, without per-site rules. A Claude agent looks at the
page and decides what to do next; our code keeps control of **your answers** and the **safety rules**.

**Why:** the scripted approach (automated Simplify plus per-case rules) broke on one site quirk after another on
2026-10-04: an iframe-embedded form (Quantifind), a five-step wizard whose Next posts each step (Adobe), Simplify's
varying and sometimes disabled buttons, forms without labels (Rippling). Each fix covered one case. Filling a field
was never the hard part; working out *what this page is and what to do next* was. That's what an agent is for.

---

## 1. Where it fits

A new applier, `agent`, joins the chains (`config/apply.yaml`); nothing is removed:

| platform | chain |
|---|---|
| greenhouse | `greenhouse → agent → simplify → manual` (the API applier stays first: predictable, no model) |
| every other platform | `agent → simplify → manual` |
| linkedin_easy_apply | `simplify → manual`: never automated |

- `simplify_auto` stays available (and can be put back in a chain) until the comparison (§10) decides its place.
- The **Apply** section (F3) is unchanged: Queued → start → Needs input / Ready to submit → `s` / `f`, with the same
  outcomes (§3 of step3-apply.md). Only what fills the form changes.

## 2. How it runs

- **The model is Claude Opus 5.5** (`claude-opus-5-5`), set by `AUTO_APP_APPLY__AGENT_MODEL`. Messy forms are where a
  stronger model pays off.
- **It runs through the Claude Agent SDK** (Python), on your **Claude Code login** like Step 2's runner and the question
  matcher: no API key. To confirm in A7.1: the SDK uses that login when `ANTHROPIC_API_KEY` is removed from its
  environment, as the matcher does.
- **The agent's tools are ours, in process** (an SDK MCP server): only the tools in §3, with no shell, files, web search
  or other built-in Claude Code tools.
- **The browser is ours too:** the same launch as today, with a dedicated profile, `DYLD_*` stripped
  (`core/browser_env.py`), and one browser at a time (`Applications.browser_lock`). Hidden in start mode, visible for
  fill only / submit.
- **It's a loop:** the prompt holds the job, the mode and the rules, but **no page content**. Claude reads the page
  with `snapshot`, acts, reads again, until it calls `done`.

## 3. Tools

**The hands are generic browser tools**, the standard set browser agents use (as in Playwright's MCP server), written
for no site in particular, so a widget we've never met is handled the way a person would: click it, look, pick.

| tool | what it does |
|---|---|
| `snapshot()` | the page's accessibility tree: every field, button, label and message with a **ref** per element, **in every frame** (embedded forms included), plus the address and title |
| `click(ref)` | click an element (guarded, §5) |
| `type(ref, text)` | type into a field (guarded: sensitive fields, §5) |
| `select(ref, option)` | pick an option of a native or custom dropdown |
| `check(ref, on)` | tick or untick a checkbox / radio |
| `upload_cv(ref)` | attach your CV (`profile/cv.pdf`); Claude never handles the file |
| `press(key)` · `scroll(direction)` · `wait(seconds)` | keyboard, scrolling, waiting for a page to settle |
| `screenshot()` | an image of the page, for when the tree isn't enough (an unlabeled widget, a layout question) |

**Your data comes through our tools:**

| tool | what it does |
|---|---|
| `answer_for(label, options?, kind?)` | the existing resolver (`questions.resolve`): profile → answers.yaml → matcher. **Sensitive** questions (work authorization, sponsorship, citizenship, demographics, legal consent) get only an answers.yaml answer, or "ask the user" |
| `draft(label)` | free text ("Why this company?") drafted from your profile and the job; marked ✎ for your review |
| `ask_user(label, question, options?)` | record a question only you can answer → **Needs input** (a pick list in the Apply section when there are options) |
| `fill_secret(ref, what)` | types your stored account email or password (§6) **without Claude seeing it** |
| `done(status, note)` | finish: `needs_input` / `ready` / `filled` / `submitted` / `action_needed` / `unsupported`, with a note |

Every answer the agent puts on the form is recorded as a question row (label, value, source), as today, so the Ready
to submit review shows what was filled and where it came from.

## 4. Modes

The same three passes as today (§5 of step3-apply.md):

| mode | browser | steps | final submit | ends with |
|---|---|---|---|---|
| **start** (`enter` / `R` / `r`, `apply run`) — a **trial run** (2026-10-05, your call: "check the agent can actually fill the form, so that when I say submit it submits without issue") | hidden (`AUTO_APP_APPLY__AGENT_HEADLESS=false`: visible) | **every step**: signs in / creates the account if asked, Next / Continue allowed. Each step is saved on the site as a draft, as when you fill it by hand | **blocked**, stops on the last step | ready (it can be submitted) / needs_input (which step) / action_needed |
| **fill only** (`f`) | visible | every step, Next allowed | **stops** there for you | filled |
| **submit** (`s`) | visible | every step | clicked; our existing check waits for the confirmation page | submitted / action_needed / failed |

- **On later steps** (fill only / submit), a required question with no answer sends the job back to **Needs input**,
  with every answer so far kept. After you answer, `s` / `f` goes through the steps again.
- **Your answers are reused:** a re-run gets them as known answers, so `answer_for` returns yours first.

## 5. Guards, enforced in code, not just in the prompt

The prompt states the rules; the tools enforce them, so a confused or misled agent can't break them:

| guard | how |
|---|---|
| **No submit outside `s`** | `click` refuses a button that looks like a final submit (submit / send / apply / complete / finish) unless the mode is submit |
| **No Next in start mode** | `click` refuses Next / Continue / Save and continue in start mode |
| **Stay on the application** | navigation to another site is blocked (the job's domains plus a list of known ATS / login domains); a blocked attempt is reported to the agent |
| **Sensitive fields: your answers only** | `type` / `select` / `check` on a field whose label is sensitive accepts only the value `answer_for` gave; anything else is refused with "ask the user" |
| **Page text is data, not instructions** | the agent has only these tools, the prompt says so, and the guards above hold whatever a page says |
| **Secrets stay out of the model** | passwords are typed by `fill_secret`; snapshots mask password fields' values |
| **Your CV is the only file** | `upload_cv` attaches `profile/cv.pdf`; there's no general file tool |

## 6. Accounts (Workday-style sites)

**The agent creates accounts when a site requires one**, with **the information you provide**, in every mode,
including the trial run when you start a job (so the trial covers the whole application).

- **Email:** one for every company's account, set in `profile.yaml` (`accounts.email`, defaulting to your email).
- **Password:** yours, in `profile/profile.yaml`: `accounts.password` for every site, `accounts.passwords: {host: …}`
  for a site where you already have an account (2026-10-05, your choice; `profile/` is git-ignored, and the matcher's
  prompt leaves `accounts:` out, so it never reaches Claude). Or in the **macOS Keychain**, with `auto-app apply
  account set [--site HOST]`. `profile.yaml` wins.
- **The agent never sees the password:** it calls `fill_secret(ref, "password")` / `fill_secret(ref, "email")`, and the
  tool types the value. Snapshots show password fields as `••••`.
- **Recorded:** each account the agent creates or signs in to (site, email, date) is saved with the application, and
  shown in its detail and in `apply accounts`.
- **Email verification / SMS codes:** the agent has no mailbox, so it ends with **action_needed** ("verify your email
  for workday.example.com, then `s` / `r`"). After you verify, the next run signs in with the same account.
- **Sign in or sign up (2026-10-05, your rule):**
  - The agent **signs in only with account info for that site**: an account on record, or a password you set for it (`--site`).
  - **Otherwise it signs up** with your email and password. If the email is already registered, it signs in with the same pair; a wrong password → action_needed.
  - The new tool `account_info()` tells it which, for the site now open. It needs **at least a valid email and a valid password**: missing either → it doesn't try, and the job is action_needed ("set …").
  - `apply account set` refuses a password a sign-up would reject (8+ characters, upper and lower case, a number, a symbol).

## 7. Time limit (no token budget)

- **No token or cost cap.** Tokens are still counted and shown, to see what applying costs.
- **A time limit per run:** start 10 min, fill only / submit 20 min (`AUTO_APP_APPLY__AGENT_TIME_LIMIT_START_MIN`,
  `…_SUBMIT_MIN`). Past it, the run stops. Start mode moves to the next applier with the reason ("the agent didn't
  finish in 10 min"); fill only / submit keeps the job in Ready with the reason.
- The time includes waiting for pages; a page that never loads ends the run the same way.

## 8. CAPTCHAs, logins, blocks

- **A CAPTCHA, a sign-in it can't do, or a block page** → **action_needed**, with what it saw. In a visible run you
  solve it in that window and the agent continues; in start mode the job waits for you.
- **Bot-detection refusals** (as with Adobe's invisible reCAPTCHA in a hidden browser) are reported as
  action_needed, not retried in a loop.

## 9. Visibility

- **Live progress** on the Apply status line, from the agent's tool calls: "job 5: step 2 of 5 — filling 'Phone
  number'", "job 5: asking you 'preferred location'".
- **A log per run:** `data/logs/agent/<application>-<time>.jsonl`, with every tool call, its result (secrets masked),
  timing and tokens. The job's attempts get a summary: outcome, note, steps reached, time, tokens.
- **Review before submitting** stays: Ready to submit lists every field the agent filled, with its source
  (profile / `bank:<key>` / claude / ✎ draft / you / agent's choice of option).

## 10. Testing and the comparison

- **Offline (A7.1):** the agent loop with a **scripted stand-in for the model** (a fixed sequence of tool calls) on fake
  forms served by Playwright routes, like `tests/apply/test_dry_run.py`. It covers the tools, every guard (submit,
  Next, navigation, sensitive fields, secrets), the modes, and the time limit. No model calls in tests.
- **Real runs (A7.2, A7.3)** on a scratch copy of the database, in start mode and fill only, **never submitting**:
  - Adobe (multi-step; Next posts each step);
  - Quantifind (the form in an iframe);
  - Rippling (no labels);
  - 2–3 Greenhouse jobs;
  - a Workday job (account creation), if one is queued.
- **Comparison with `simplify_auto` (A7.4)** on the same jobs, measuring:
  - reached the final step;
  - required fields correct;
  - questions asked of you;
  - time and tokens per job.

  Then you set the chain order (§1).

## 11. Milestones

| # | Deliverable |
|---|---|
| A7.1 ✅ | Tools (§3), guards (§5), start mode; the agent loop on the Agent SDK with Opus 5.5 on your Claude Code login (verified); offline tests with a scripted model |
| A7.2 | Real start-mode runs on the test set; logs, progress, attempt summaries; fixes from what it hits |
| A7.3 ✅ (2026-10-05) | Fill only / submit through every step; later-step questions back to Needs input; accounts (§6: `apply account set`, Keychain, `fill_secret`, action_needed for verification) |
| A7.4 | The comparison (§10); you decide the chains; README / docs updated with the results |

## 12. Risks

| risk | mitigation |
|---|---|
| A wrong answer on an eligibility question | sensitive fields accept only your saved answers (guard); everything filled is shown in review before `s` |
| The agent clicks submit too early | refused by `click` outside submit mode |
| A page tries to instruct the agent (prompt injection) | only our tools; the guards hold whatever the page says; navigation limited to the application |
| Slow or stuck runs | time limit per run (§7); start mode falls back to the next applier |
| Cost | no cap (your choice); tokens shown per job, so it's visible |
| Accounts created on many sites | one email; your password; each account recorded (§6) |
| Bot detection on hidden browsers | action_needed, not retries; fill only / submit run visible |

## 13. Decisions (2026-10-05)

1. **A browser agent, not computer use:** web forms read as an accessibility tree are faster, cheaper and more precise
   than screenshots and mouse coordinates.
2. **Generic browser tools for the hands; our code only for the guards and your data.** Our fill functions are not the
   agent's tools.
3. **Model: Claude Opus 5.5.**
4. **No token budget; a time limit per run.**
5. **The agent creates and signs in to accounts with the email and password you provide** (Keychain; typed by
   `fill_secret`, never seen by the model). Email / SMS verification is yours (action_needed).
6. **Starting a job is a trial run of the whole application:** sign in / create the account, every step (each saved on
   the site as a draft), stop before the final submit. Only `s` submits. (Briefly, the same day, a send-nothing dry run
   limited to step 1; you chose the trial run.)

## 14. As built (A7.1, 2026-10-05)

**Code:** `src/auto_app/apply/agent/`:
- `snapshot.py`: the page view (JS run in every frame);
- `session.py`: the tools, the guards' enforcement, what was filled and asked, the last check;
- `guards.py`: what may be clicked, where the browser may go;
- `driver.py`: Claude through the Agent SDK, with the prompt;
- `applier.py`: `AgentApplier`, the browser, the outcome.

**Verified on your Claude Code login:** the Agent SDK (`claude-agent-sdk` 0.2.163) runs Opus 5.5 with only our tools (`tools=[]`, `setting_sources=[]`, `ANTHROPIC_API_KEY` emptied). Claude Code also uses a little Haiku internally.

**First real-model run** (on a fake page, so no real site): Opus 5.5 started on a job description and clicked "Apply now". It asked `answer_for` about every field at once, typed the name and email, picked "Yes" for sponsorship (from answers.yaml), and attached the CV. It sent the 4 questions nobody could answer to you, including one inside an embedded frame. It didn't click Next. 28 s, about 34k tokens.

**Changes from the design:**
- **The agent's browser runs on Playwright's async API.** The sync API can't run inside the SDK's event loop. The browser is your Chrome with a dedicated profile, `browser-profiles/agent/` (Playwright's Chromium if Chrome is missing), started without `DYLD_*` and with the automation markers hidden.
- **`draft` is part of `answer_for`:** free-text questions come back as drafts (source `draft`, ✎).
- **`fill_secret` and accounts come with A7.3**, together with fill only / submit. In A7.1, `submit` answers "not yet". Fill only keeps the job in Ready with that reason, and `s` hands it to the next applier.
- **The default since 2026-10-05** (your call, before A7.2): the chains of §1 are the defaults — `greenhouse → agent → simplify → manual`, `agent → simplify → manual` elsewhere; no `config/apply.yaml` needed. Until A7.3, `s` on a job the agent prepared hands it to the Simplify hand-off, and `f` says "not yet". In tests, an agent without a scripted model never runs (`tests/conftest.py`).
- **The snapshot is our own JS, not Playwright MCP's.** It shows roles, accessible names (from `<label>`, aria, and the text around a field for forms without labels), required / invalid / disabled, values, a native select's options, checked state, and a radio's or grouped checkbox's question (`of “…”`). It also includes headings, alerts and step markers. Refs are `e12` in the page and `f2e5` in frame 2. Password values show as `••••`. Hidden sections (later steps) aren't listed.
- **Guards:** `click` refuses a final submit outside submit mode, with one nuance: an "Apply" link or button is final only as the button of a filled form, otherwise it leads to the form. `click` also refuses Next in start mode. Navigation off the job's site (and the ATS list in `guards.py`) is aborted and the tab goes back. Sensitive fields take only the answer `answer_for` gave; anything else is refused with "ask the user".
- **The last check:** after `done`, a final snapshot. A required field still empty and not asked becomes a question ("required and still empty on the form", or "“X” didn't take").
- **Answers by label:** `questions.standard_id` maps labels like "First Name" / "Email address" / "Phone number" to the profile's standard fields.
- **Outcomes in start mode:** done(ready / needs_input) becomes ready or needs_input, with "step 1 of a multi-step form; the next steps are filled when you submit" when `more_steps`. A timeout, a crash, or no `done` becomes `unsupported`, so the next applier takes over without a retry. action_needed and unsupported pass through. The reason ends with the minutes and tokens.
- **Settings:** `AUTO_APP_APPLY__AGENT_MODEL` (claude-opus-5-5), `…_AGENT_TIME_LIMIT_START_MIN` (10), `…_AGENT_TIME_LIMIT_SUBMIT_MIN` (20).
- **Logs:** `data/logs/agent/<application>-<time>.jsonl`, one line per tool call, with secrets masked.

**Tests:** `tests/apply/test_agent.py`, offline (Playwright routes, a scripted model). It covers:
- the snapshot: labels, the text around a field, radio groups, frames, secrets, hidden sections;
- the tools, and what they record;
- every guard, including going back after a blocked navigation;
- a description page leading to the form;
- the time limit;
- start mode end to end through the queue: needs_input, the last check catching a missed required field;
- the SDK tool handlers: errors returned to the model, screenshots, logs.

## 15. As built: accounts, live progress, clicks (2026-10-05)

Pulled forward from A7.3, because every Workday job stopped at "Sign In" (NVIDIA, 2026-10-05).

**Accounts (§6):**
- **The email:** `profile.yaml` → `accounts.email` (default: `email`).
- **The password:**
  - `auto-app apply account set [--site HOST]` saves it in the macOS Keychain via `security` (service `auto-app`, account `default:<email>` or `<host>:<email>`).
  - A site's own password wins over the default.
  - `apply accounts` lists the accounts.
- **`fill_secret(ref, "email" | "password")`:**
  - The tool types the value; the model only sees "password typed (not shown)".
  - It refuses a non-password field for the password.
  - `type` refuses password fields, so the model can't type a password itself.
  - With no password set, it answers "set an account password: auto-app apply account set" and the agent ends with action_needed.
- **In every mode, the trial run included:** sign-in and create-account clicks and `fill_secret` are allowed. A sign-in form's own Submit / Continue / Sign In isn't the application's submit (Workday labels its sign-in button "Submit"; a sign-in form = a password field among at most 5 fields, or an email-only first step on a page titled sign in / create account). Only the application's final submit is refused outside submit mode.
  - The terms / privacy box on such a form may be ticked only with `accounts.accept_terms: true`; otherwise it's a sensitive field (your saved answer or you).
- **Honeypots:** a field "for robots only" ("do not enter if you're human", as on Workday) is refused by `type`.
- **Recording:** each account used goes into the `accounts` table (migration `4bc3cd20fcd6`):
  - keyed by the **full host**, since Workday accounts are per company;
  - created by the agent (two password fields on the form) or signed in;
  - the application, and "verification pending" when the run ended action_needed.
  
  The job's note says "created an account on …" / "signed in to …".
- **The prompt:** sign in first, create an account only if there's none, never type or reset a password, and verification or codes → action_needed.

**Live progress:** the session reports what it's doing ("reading the page", "looking up answers for 6 field(s)", "clicked “Apply”", "filling “Email”", "attaching your CV", "signing in" / "creating an account", "asking you “…”"). The Apply section shows it on the status line as "job N: … · k done, m more after it".

**Clicks:** a click blocked by something on top (an overlay, a cookie banner, a sticky header) is retried as a direct click on the element, and the tool says so. On NVIDIA, the agent had lost 40 s to three timeouts and a keyboard workaround.

**Tests:** in `tests/apply/test_agent.py`:
- sign in with the email and password unseen, and the password guards;
- creating an account, with the terms box with and without `accept_terms`;
- no password → action_needed;
- clicking through an overlay, and progress;
- the Keychain calls (faked) and the account records.

## 16. As built: fill only and submit (A7.3, 2026-10-05)

- **Visible browser:** `f` and `s` run the same walk as the trial run (the same profile, so a sign-in from the trial is reused), with your answers known, in a **visible** browser.
- **`f`:** the agent stops on the last step (`done("filled")`), and the window stays open until you close it. The job stays Ready: "if you submitted it, press a".
- **`s`:** the final submit is allowed. A submit counts only with a confirmation on the page ("thank you for applying" / "application received" / a confirmation URL) → submitted, and the job becomes applied.
  - Agent says submitted but no confirmation → **action_needed** ("check whether it went through, then a or s"), never `failed`, because the next applier could apply a second time.
  - A CAPTCHA after submitting → action_needed.
- **A new or re-asked question on the way** → needs_input. Questions asked in this run replace earlier ones with the same label, and your other answers are kept.
- **The Apply section:** `f` and `s` work on **Action needed** too (e.g. NVIDIA after a sign-in step). The agent's progress shows on the status line ("application 13: signing in").

Tests (scripted model, a fake two-step form): the trial run → ready; `s` → submitted with the confirmation; `f` → filled, waits for the window to close; no confirmation → action_needed; a question asked again on submit → needs_input.
