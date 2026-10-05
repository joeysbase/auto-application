# Step 3 — the browser-agent applier (`agent`, milestone A7)

Status: 📐 **designed** (2026-10-05). Part of [Step 3 — Apply](step3-apply.md); it replaces the "generic (later)" row
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
| **start** (`enter` / `R`, `apply run`) | hidden | **step 1 only**: Next is blocked, because Next sends the step to the site (Adobe: `POST applySubmit`) | blocked | needs_input / ready / action_needed |
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

**The agent creates accounts when a site requires one**, with **the information you provide**:

- **Email:** one for every company's account, set in `profile.yaml` (`accounts.email`, defaulting to your email).
- **Password:** yours, set once with `auto-app apply account set` and kept in the **macOS Keychain**, never in files
  (the 2026-10-04 decision in step3-apply.md §13). Optionally one per site.
- **The agent never sees the password:** it calls `fill_secret(ref, "password")` / `fill_secret(ref, "email")`, and the
  tool types the value. Snapshots show password fields as `••••`.
- **Recorded:** each account the agent creates or signs in to (site, email, date) is saved with the application, and
  shown in its detail and in `apply accounts`.
- **Email verification / SMS codes:** the agent has no mailbox, so it ends with **action_needed** ("verify your email
  for workday.example.com, then `s` / `r`"). After you verify, the next run signs in with the same account.
- **Sign-in:** an existing account is used (`fill_secret`) before ever creating a second one.

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
| A7.1 | Tools (§3), guards (§5), start mode; the agent loop on the Agent SDK with Opus 5.5 on your Claude Code login (verified); offline tests with a scripted model |
| A7.2 | Real start-mode runs on the test set; logs, progress, attempt summaries; fixes from what it hits |
| A7.3 | Fill only / submit through every step; later-step questions back to Needs input; accounts (§6: `apply account set`, Keychain, `fill_secret`, action_needed for verification) |
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
6. **Start mode never clicks Next or submit**; fill only and submit go through every step; only `s` submits.
