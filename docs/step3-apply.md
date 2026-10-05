# Step 3 — Apply

**Goal:** work through the jobs **you chose to apply to** — the `want_to_apply` queue filled in `job browse` — **several at a time**, filling each application from your profile and answer bank, asking you only what they can't answer, and marking each job `applied` once it's submitted.

```
want_to_apply ──► workers (several jobs at a time, paced by your apply policy)
                    │ each job: its platform's applier chain, e.g. greenhouse → simplify → manual
                    │ prepare: read every question, answer from profile.yaml / answers.yaml
                    ├── something only you can answer ──► NEEDS INPUT ──► you answer in `apply inbox`
                    │                                                     (saved to answers.yaml)
                    ├── a login / CAPTCHA / code ───────► ACTION NEEDED ─► you, in a visible browser
                    ├── applier can't / fails ──────────► next applier in the chain
                    └── all answered ──► READY ──► you review & submit  (or auto-submit, your choice)
                                                         └──► submitted ──► job: applied
```

Status: **A1 ✅ A2 ✅ A3 ✅**, automated Simplify ✅, starting a job = a headless dry run ✅ (2026-10-04: profile / answers / policy, the queue and chain runner, the Simplify hand-off and manual appliers; questions answered from profile / answers.yaml / Claude with the sensitive rule, the Needs input tab; the **Greenhouse** applier — questions from the API, the form filled and submitted in a browser, verified on a real form without submitting — with Ready to submit and Action needed — `src/auto_app/apply/`); A4–A6 designed; **A7, the browser-agent applier, designed in detail in [step3-agent.md](step3-agent.md)** (2026-10-05) — the next to build. Replaces the earlier tailor-then-submit design; CV tailoring deferred (§11). Depends on Step 2's review (`want_to_apply`).

---

## 0. Principles

1. **Several applications at a time; you only see what needs you.** Workers fill what they can; anything they can't answer goes to a **needs-input queue**, and the job waits there while the others go on.
2. **Never guess what only you can answer.** Work authorization, sponsorship, eligibility, demographic and legal questions, salary, start date — answered only from your `answers.yaml` or by you. Free-text answers you saved in `answers.yaml` are yours; ones Claude drafts are shown to you before submitting — unless you allow drafts in auto mode for a platform (`auto_drafts`, §1).
3. **You choose how applications are submitted:** **review** each filled application and press submit, or **auto-submit** once every question is answered — per platform (§1).
4. **Appliers fall back.** Each platform has an ordered chain of appliers; when one can't handle a form or fails, the next takes over (§3).
5. **Logins, CAPTCHAs and verification codes are handed to you**, never worked around.
6. **LinkedIn Easy Apply is never automated** (LinkedIn bans accounts for it, and Step 1's collection depends on your login). LinkedIn jobs use the employer's own apply link when dedup found one; Easy Apply–only jobs go to the Simplify hand-off or to you.
7. **Your own CV, as is:** `profile/cv.pdf` is uploaded unchanged. Tailored CVs are deferred (§11).

---

## 1. The apply policy — `config/apply.yaml` (git-ignored; template `config/apply.example.yaml`)

How the queue is worked is yours to configure, without code changes:

```yaml
workers: 3                      # applications prepared / submitted at once
pace:
  per_hour: 6                   # at most this many submissions per hour …
  per_day: 20                   # … and per day
  hours: "09:00-18:00"          # only submit in this window (local time); preparing runs any time
  per_company_per_month: 3
order: queued                   # queued (the order you chose) | newest_posting | by_filter
appliers:                       # per platform: the chain, tried in order (§3)
  greenhouse: [greenhouse, simplify, manual]
  lever:      [lever, simplify, manual]
  ashby:      [ashby, simplify, manual]
  workday:    [simplify, manual]
  linkedin_easy_apply: [simplify, manual]     # never automated
  "*":        [simplify, manual]              # any other platform
submit:                         # per applier that can submit by itself: review | auto
  greenhouse: review
  lever: review
  ashby: review
auto_drafts:                    # auto mode only: may Claude-drafted free text be submitted unseen? (default: no)
  greenhouse: false
notify: true                    # a desktop notification when something needs you
```

- `filter`-style checks: `auto-app apply policy` validates the file (unknown appliers, bad windows) and prints the effective policy.
- Before preparing a job, its posting is checked (still open? the apply link works?); a closed posting goes to you ("posting gone — drop it?") instead of being applied to.

---

## 2. Your profile and answers — filled in up front, asked when missing

All git-ignored, in `profile/` (templates committed as `profile.example/`):

**`profile/profile.yaml`** — what every application asks:
```yaml
name: { first: …, last: …, preferred: … }
email: …
phone: …
location: { city: Boston, state: MA, country: United States }
links: { linkedin: …, github: …, portfolio: … }
education:
  - { school: Northeastern University, degree: "M.S. Computer Science", start: 2024-09, end: 2026-05, gpa: … }
experience:
  - { company: …, title: …, start: …, end: …, location: … }
cv: profile/cv.pdf
```

**`profile/answers.yaml`** — your answer bank, keyed by question, growing as you answer in `apply inbox`:
```yaml
work_authorization:            # sensitive: only ever answered from here or by you
  question: "Are you legally authorized to work in the United States?"
  answer: "Yes"
requires_sponsorship:
  question: "Will you now or in the future require visa sponsorship?"
  answer: "Yes"
salary_expectation:
  question: "What are your salary expectations?"
  answer: "Open; market rate for the role"
earliest_start: { question: "When can you start?", answer: "January 2027" }
why_company:                   # free text, saved once: counts as your answer (doesn't stop auto mode)
  question: "Why do you want to work at {company}?"
  answer: "I want to build ML systems that ship to production, and {company}'s work on … "
  kind: free_text
  adapt: true                  # Claude may tailor it lightly to the job (names, the role) — still shown in review
about_me:
  question: "Tell us about yourself"
  answer: "M.S. CS student at Northeastern focused on ML systems …"
  kind: free_text
relocation: { question: "Are you willing to relocate?", answer: "Yes" }
eeo_gender: { question: "Gender", answer: "Decline to self-identify" }
# … added by the app when you answer a new question and choose "save"
```

- **Fill once, up front;** `auto-app apply profile` checks both files and lists what's missing for the common questions.
- **Asked when missing:** a question with no confident answer from either file goes to the needs-input queue; your answer is used for every waiting job with the same question, and — if you choose — saved to `answers.yaml` for next time (§4, §6).

---

## 3. Appliers — one interface, chained per platform

An **applier** knows how to apply to one kind of application form. All share one interface, like Step 1's source plugins:

```
can_apply(job) -> bool                       # e.g. the apply URL is a Greenhouse form
prepare(job, profile, answers) -> Prepared   # read every question; answer what it can; nothing submitted
submit(job, answers) -> Outcome              # fill and submit (or stop before submit in review mode)
```

**Outcomes** decide what happens next:

| outcome | meaning | next |
|---|---|---|
| `ready` | every question answered | review (or auto-submit) |
| `needs_input` | questions only you can answer | needs-input queue; same applier resumes after |
| `action_needed` | a login, CAPTCHA, email / SMS code | action-needed queue: you resolve it in a visible browser, or skip to the next applier. **Only that job waits** — other jobs on the same site carry on (a site that keeps blocking goes into cooldown, §7) |
| `unsupported` | this applier can't handle this form (unknown field type, unexpected page) | **next applier** in the chain, at once |
| `failed` | an error, a timeout, the form changed | retried once, then **next applier** |
| `submitted` | done (confirmation captured) | job → `applied` |

When every applier in the chain is exhausted, the job lands in **Failed** in `apply inbox` with each attempt's reason and screenshot; you retry, apply by hand, or drop it (`not_applied`).

**The appliers:**

| applier | how | parallel | submits |
|---|---|---|---|
| **simplify_auto** (default first, 2026-10-04) | **automated**: Playwright's Chromium (branded Chrome 137+ won't load extensions under automation) with the Simplify Copilot extension copied from your Chrome and a dedicated profile you log in to once (`apply login simplify`); clicks Simplify's autofill, scans the form for required fields still empty (→ needs input), then review / submit like the Greenhouse applier. Your choice despite the risks: Simplify's terms may not allow automated use; its panel is undocumented and changes. Built (2026-10-04): starting a job is a headless dry run (§5) — "Start Application" on a description page, the consent notice answered "Essentials only" (`AUTO_APP_APPLY__SIMPLIFY_CONSENT`), "Autofill This Page", the gaps filled from profile / answers, the form read back; no panel → next applier | one at a time (one profile) | review / auto |
| **simplify** (hand-off) | **hand-off**: opens the job's apply link in **your** Chrome, where the [Simplify](https://simplify.jobs) extension autofills it; you check, attach anything missing, submit; then press *applied* / *couldn't* in `apply inbox` | no — one at a time, you at the keyboard | you |
| **manual** (always last) | opens the apply link and your CV; you apply; you press *applied* | no | you |
| **greenhouse** | questions from the public job board API (`?questions=true`) — so preparing needs no browser; submits by filling the hosted form in an automated browser | yes | review / auto |
| **lever** | reads and fills `jobs.lever.co/<company>/<id>/apply` (plain HTML form) | yes | review / auto |
| **ashby** | reads the form definition from the page's data; fills it | yes | review / auto |
| **workday** (later) | per-company account — **one email for every company** (`profile.yaml`), each company's password generated and kept in the **macOS Keychain** (never in files); email verification → action needed; multi-page wizard | yes, slow | review / auto |
| **agent** (A7, designed 2026-10-05 — [step3-agent.md](step3-agent.md)) | a **Claude browser agent** (Opus 5.5, on your Claude Code login): generic browser tools (accessibility snapshot, click, type, select, upload) as its hands, our code for the guards (no submit outside `s`, no Next in start mode, stay on the application, sensitive fields only from answers.yaml) and your answers; creates / signs in to accounts with your email and Keychain password; first in every chain except Greenhouse (after the API applier) and LinkedIn Easy Apply | one at a time (one browser) | review / auto |

- The **Simplify hand-off** covers every platform from day one; automated appliers take over platform by platform, each falling back to Simplify, then to manual.
- Automated appliers use a dedicated, git-ignored Chrome profile in the project folder (`browser-profiles/apply/`), headless where the site allows, visible when something needs you.
- **Simplify has no API**: it can't be driven automatically (it works inside your browser, while you're on the page) and isn't — driving it would be brittle, likely against its terms, and would trip sites' bot detection. Its own application tracker may list the same jobs; ours is the one Step 3 uses.

---

## 4. Answering questions

Each question gets a **kind**, a **source** and a **confidence**:

| kind | examples | answered from |
|---|---|---|
| standard field | name, email, phone, links, CV upload, education, last employer | `profile.yaml` (exact) |
| known question | sponsorship, start date, relocation, years of Python | `answers.yaml`, matched **by meaning** ("Do you need visa support?" ≈ `requires_sponsorship`) — Claude matches with a confidence (below); below the threshold → needs input |
| **sensitive** | work authorization, sponsorship, citizenship, demographics (gender, ethnicity, disability, veteran), criminal history | **only** `answers.yaml` or you — never drafted, never a low-confidence match |
| free text | "Why do you want to work here?", "Tell us about a project" | 1. a **saved free-text answer** in `answers.yaml`, matched by meaning — your own words, so it counts as yours and doesn't stop auto mode. `{company}` / `{title}` are filled in; with `adapt: true` Claude may tailor it lightly to the job (an adapted text counts as a draft for the rule below). 2. otherwise **drafted by Claude** from your profile and the job. A draft is shown to you before submitting — in review mode always; in auto mode it waits in Ready, unless `auto_drafts: true` for that platform lets it be submitted unseen (off by default). Drafts can be saved to `answers.yaml` from the inbox, becoming your saved answer next time |
| choice | dropdowns, radio buttons, checkboxes | the answer mapped onto the listed options; no option fits → needs input |

- **The model: Claude through the Claude Code CLI** (your login, no API key) — the same headless, locked-down way as Step 2's `claude-cli` runner: a brand-new session per call, no tools at all (it only reads the question, the options, your answer bank entries and the job, and answers in JSON), none of your settings, nothing saved. It does two jobs: **matching** a question to an `answers.yaml` entry (and mapping the answer onto the listed options), and **drafting** free-text answers. Settings: `AUTO_APP_APPLY__MODEL`, `AUTO_APP_APPLY__MATCH_THRESHOLD` (start strict, e.g. 0.9; every match you correct in review is counted, so the threshold can be tuned from data).
- One answer from you can resolve the **same question for every waiting job**; you choose whether to save it to `answers.yaml`.
- Every filled application keeps, per question: the answer, its source (profile / bank / drafted / you) and the match confidence — shown in review.

---

## 5. Two passes and the application's life

Holding a browser open while a job waits for you would time out and block the workers. So each application runs in **two passes**:

1. **Prepare** — open the form (or the API), collect **every** question, answer what's possible, queue the rest. **Nothing is submitted.** As built (2026-10-04) this is a **dry run**: the automated applier fills the real form headless (Simplify's autofill, or the Greenhouse API's answers) and reads it back (`apply/form.py`): a required field still empty — unknown to the API, or an answer that didn't take — becomes an open question (needs input); all filled → ready. A multi-step form is read up to its first step only — Next sends the step to the site (Adobe: `POST applySubmit`), so the later steps are walked at submission, where a new question returns the job to needs input. Fields in a hidden section (a later step) aren't read. Labels come from `<label>`, aria, legend, or the text around a field (forms without labels, e.g. Rippling); radio groups take their question's text; a dropdown's own search box is skipped.
2. **Submit** — once everything is answered (and reviewed, in review mode): open the form fresh, fill it in one go, upload the CV, submit, capture the confirmation.

Multi-page forms (Workday) can reveal new questions on later pages; then the job goes back to needs input with just those, and resumes.

```
queued ─► preparing ─┬─► needs_input ──(you answer)──┐
                     ├─► action_needed ─(you)────────┤
                     └─► ready ◄─────────────────────┘
ready ─(review: you press submit / auto)─► submitting ─► submitted ─► job decision = applied
any step ─(unsupported / failed twice)─► next applier ─► … ─► failed (all exhausted)
```
The job stays `want_to_apply` (and doesn't expire) until submitted; then `applied`, with the date and the applier. Dropping it sets `not_applied`.

---

## 6. `auto-app apply inbox` — the interactive app

The **Apply** section (F3) of `auto-app ui` (called the Inbox until 2026-10-05) — the one app for every interactive part (F1 Jobs, F2 Audit); `apply inbox` opens it there. Tabs for what needs you, each with a count:

| tab | each item | your keys |
|---|---|---|
| **Needs input** | a job and its open questions (text, options, the job's context, why it wasn't answered) | type the answer · `enter` save for this job · `A` use for every waiting job with this question · `s` also save to `answers.yaml` |
| **Action needed** | login / CAPTCHA / code, with the site | `o` open the visible browser and resolve · `n` skip to the next applier |
| **Ready to submit** (review mode) | the filled application: every answer with its source, the CV, drafted texts | `enter` submit · `e` edit an answer · `d` drop (not applied) |
| **Hand-offs** | Simplify / manual jobs, one at a time | `o` open in Chrome · `a` applied · `c` couldn't (next applier) · `l` later |
| **Failed** | all appliers exhausted: attempts, reasons, screenshots | `r` retry · `m` apply by hand · `d` drop |
| **Done** | submitted applications (today / all) | `o` open the confirmation |

- **Notifications:** when a job enters Needs input, Action needed or Ready, a desktop notification (`notify: true`) says so, so the workers can run while you do something else.
- The footer shows only the keys of the current tab (as in `filter audit`).

---

## 7. Safety limits

- Pace from the policy (per hour / day / company, hours window); at most `workers` at once.
- A site that shows a block, CAPTCHA wall or HTTP 403 / 429 stops that applier on that site (cooldown, as in Step 1) and falls back.
- Never submit with an unanswered required question, a sensitive answer not from `answers.yaml` / you, or an unseen drafted text (unless `auto_drafts: true` for that platform). Every submitted draft is kept with the application, so you can see exactly what was sent.
- A job already `applied` (or archived) is never applied to again; reposts of a job you applied to carry Step 1's warning ("looks like job #12, which you applied to").

---

## 8. Storage

```sql
applications  (application_id PK, job_id, platform, apply_url, status,       -- queued | preparing | needs_input |
               applier, submit_mode, created_at, updated_at, submitted_at,    -- action_needed | ready | submitting |
               confirmation_path)                                             -- submitted | failed | dropped
attempts      (attempt_id PK, application_id, applier, pass, outcome, reason, screenshot_path, started_at, ended_at)
questions     (application_id, question_id, text, kind, options JSON, required,
               answer, source, confidence, PRIMARY KEY (application_id, question_id))
```
`answers.yaml` stays the source of truth for your answers (you can edit it by hand); the database keeps what was filled into each application and why. On submission the job's `decision` becomes `applied`; it then follows the recommended TTL (archived after 30 days unseen, kept as training data).

---

## 9. CLI

```
auto-app apply policy                      # check config/apply.yaml; print the effective policy
auto-app apply profile                     # check profile.yaml / answers.yaml; list missing common answers
auto-app apply queue                       # the want_to_apply queue, in the policy's order, with each job's chain
auto-app apply run [--job N]…              # start applications (A1: each to its first working applier — a hand-off); later: the workers, within the pace
auto-app apply inbox                       # the interactive app: needs input, action needed, ready, hand-offs, failed, done
auto-app apply list [--status S]           # application history
```

---

## 10. Milestones

| # | Deliverable |
|---|---|
| A1 ✅ | `profile.yaml` / `answers.yaml` / `apply.yaml` loaders + checks (`apply profile`, `apply policy`, templates); `applications` / `attempts` / `questions` tables; `apply queue`; the applier interface and chain runner (fallback, retries, outcomes); **Simplify hand-off** and **manual** appliers; `apply inbox` with Hand-offs, Failed, Done; job → `applied` — usable from day one for every platform. As built: `apply run` is one-shot (opens applications, moves each to its first working applier); the posting-still-open check and the pace apply to automated submission, so they come with A3 / A5 (a hand-off shows you the page); a chain naming an applier that isn't built yet skips it, recorded as "not built yet"; a failed application isn't reopened by `apply run` — retry it in the inbox; the `questions` table comes with A2 |
| A2 ✅ | Questions: kinds, answer matching by meaning (confidence), sensitive-only-from-bank, choice mapping; **Needs input** tab (answer once for all, save to `answers.yaml`); notifications. As built (A2): one Claude CLI call per application (no tools); a question on `answers.yaml` matched exactly needs no call; optional unanswered questions are left blank; Claude may also choose a dropdown option the profile clearly answers (source `claude`, shown in review); desktop notifications not yet |
| A3 ✅ | **Greenhouse** applier: prepare from the API, submit in the browser; **Ready to submit** review; Action needed. As built: submission only when you press `s` (auto mode is A5); `f` fill only (a visible browser for you to submit); the CV is uploaded first (Greenhouse refills name / email from it) and text is re-checked at the end; React dropdowns opened with the keyboard and the option clicked ("United States" matches "United States +1"); a closed posting (API 404) stops without fallback; a form it can't fill falls back to the next applier. Checked on a real Greenhouse form, filled and **not** submitted; the submit path is tested offline (fake form: confirmation, validation failure, CAPTCHA → action needed) |
| A4 | **Lever** and **Ashby** appliers |
| A5 | Auto-submit per platform (policy `submit: auto`), pace window and per-company limits enforced |
| A6 | **Workday** applier (accounts, multi-page, new questions mid-way) |
| A7 | **Browser-agent applier** — [step3-agent.md](step3-agent.md): A7.1 tools, guards, start mode (offline tests) · A7.2 real start-mode runs · A7.3 fill only / submit through every step, accounts · A7.4 comparison with `simplify_auto`, then the chain order |
| — | Deferred: tailored CVs and cover letters (§11) |

## 11. Deferred: tailored CVs

Not now — your own `profile/cv.pdf` is uploaded as is. The earlier design stays as the plan for later: a master CV (`profile/master_cv.yaml`, every bullet with an id, skills and metrics), an LLM that selects / reorders / rephrases but never invents, automatic verification (traceability, numbers, skills, entities, one page), rendering to PDF, optional cover letters with the same checks. When added, it slots in before the prepare pass (the CV to upload becomes per job).

## 12. Risks

| Risk | Mitigation |
|---|---|
| A wrong eligibility answer disqualifies you | sensitive questions only from `answers.yaml` or you; never matched with low confidence |
| Form changes break an applier | outcome `unsupported` / `failed` → next applier; screenshots per attempt; review mode by default |
| Duplicate applications | the job's `decision` is the source of truth; appliers refuse jobs already `applied`; repost warnings |
| Account flags from automated applying | pace limits, no LinkedIn automation, cooldowns on blocks, visible browser when needed |
| Drafted text says something untrue | always shown before submitting; drawn only from your profile and the job |
| Simplify and our tracker disagree | ours is authoritative for Step 3; hand-offs are confirmed by you |

## 13. Decisions (2026-10-04) and open questions

Decided:
1. **Answer matching and drafting use Claude through the Claude Code CLI** (§4), locked down like Step 2's runner; strict threshold to start, tuned from your corrections.
2. **Workday:** one email for every company's account; passwords in the macOS Keychain.
3. **"Action needed" pauses only that job**; the site's other jobs carry on.

Decided (2026-10-05): the **browser-agent applier** (A7) comes next — generic browser tools, our guards, Opus 5.5, no token budget but a time limit, accounts created with your information; see [step3-agent.md §13](step3-agent.md#13-decisions-2026-10-05).

Open: none yet — new ones are added as A1–A3 are built.
