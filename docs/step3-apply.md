# Step 3 — Apply

**Goal:** work through the jobs **you chose to apply to** — the `want_to_apply` queue filled by Step 2's review — producing a CV tailored to each description, without inventing anything, and submitting the application; then mark the job `applied`. How the queue is worked through is **yours to configure** (§0).

```
want_to_apply queue ─► (your queue policy picks the next job) ─► tailor CV (+ cover letter) ─► verify (no fabrication)
                                                                                                    │
      applied ◄── submit (fill form, you confirm) ◄── approve ◄── render PDF / review (you) ◄────────┘
```

Status: 📐 designed (how applications are tailored and submitted is still open; the queue and its configurable handling below are fixed). Depends on Step 2's review (`want_to_apply`).

---

## 0. The want-to-apply queue

- **Input:** jobs with `decision = want_to_apply`, set by you in Step 2's review (`auto-app job browse`) or by `auto-app job mark --as want_to_apply`. Queued jobs don't expire (overview §3).
- **Output:** each job ends as `applied` (Step 3 submitted it, or you applied by hand and said so) or `not_applied` (you withdrew it, or its posting is gone and you dropped it).
- **How the queue is worked is your choice — a queue policy**, not something fixed in the app. Examples of what it should cover:

| You may want … | e.g. |
|---|---|
| one at a time, each with your approval | the default |
| a pace | one application every 5 minutes; at most 10 a day; only between 9:00 and 18:00 |
| an order | newest posting first; by filter; by the order you queued them |
| batching | tailor 10 CVs now, review them together, submit later |
| per-platform handling | Greenhouse / Lever / Ashby automated; Workday and everything else by hand (`apply manual`) |

  The exact form (a settings block, or a policy file per user like Step 2's filters) is decided with the rest of Step 3; the requirement is that these choices are configurable without code changes.
- Before working on a job, Step 3 checks its posting is still open; if it's gone, it tells you instead of applying.

---

## 1. Principles

1. **Select, reorder, rephrase — never invent.** Every line on a tailored CV traces back to the master CV.
2. **A human approves every application** by default. Auto-submit is an opt-in per platform, only after it has proven reliable.
3. **Unsure = ask.** Legal/eligibility questions (work authorization, sponsorship, EEO) come only from your answer bank; anything else the system isn't sure of is flagged for you.
4. **CAPTCHAs and login walls are handed to you**, never worked around.

---

## 2. Master CV

`profile/master_cv.yaml` (git-ignored) — a superset of everything you could put on a CV:

```yaml
contact: { name: …, email: …, phone: …, links: [github, linkedin, site] }
education:
  - { id: edu-neu, school: Northeastern University, degree: "M.S. …", dates: …, details: [...] }
experience:
  - id: exp-acme
    org: Acme, role: ML Engineer Intern, dates: …, location: …
    bullets:
      - { id: acme-1, text: "Built … reducing latency 38%", skills: [pytorch, triton, latency], metrics: ["38%"] }
      - { id: acme-2, text: "…", skills: [...] }
projects:
  - { id: proj-x, name: …, bullets: [ { id: x-1, text: …, skills: [...] } ] }
skills: { languages: [...], ml: [...], infra: [...] }
```

Every bullet has a stable `id`, its `skills`, and any `metrics`, which is what verification checks against.

---

## 3. Tailoring

**Input:** master CV + job (title, company, description, Step 2's matched/missing requirements).
**LLM output (structured):**
```json
{
  "sections": [
    {"section": "experience", "entry": "exp-acme",
     "bullets": [{"source_id": "acme-1", "text": "Built … reducing inference latency 38%"}]}
  ],
  "skills": ["pytorch", "triton", "…"],
  "summary_line": "…",
  "notes": "why these were chosen"
}
```
Rules given to the model: use only listed entries and bullets; may shorten/rephrase and reorder; must keep facts and numbers; one page.

**Model:** a strong model (Claude Opus 5.5 or Sonnet 5) — this is where quality matters most; cached master-CV prefix.

### 3.1 Verification (automatic, before you see it)

| Check | Fails when |
|---|---|
| Traceability | a bullet's `source_id` doesn't exist, or an entry isn't in the master CV |
| Numbers | a number/percentage appears that isn't in the source bullet |
| Skills | a skill is listed that isn't in the master CV's skills or bullet `skills` |
| Entities | an organization, title, date, or degree differs from the master CV |
| Drift | rephrased text too far from the source (similarity below threshold) → flagged, not rejected |
| Length | rendered PDF > 1 page |

Failures are fixed by one automatic retry with the violations listed; still failing → the job goes to review with violations highlighted.

### 3.2 Rendering

Template (Typst, or HTML → PDF) → `data/output/tailored/<job_id>/cv.pdf`, plus `cv.json` (the structured output) and `diff.md` (what changed vs. your default CV). Consistent, ATS-friendly formatting (single column, real text, standard headings).

### 3.3 Cover letter (optional, per job or per company)

Same flow and verification (claims must trace to the master CV); saved next to the CV.

---

## 4. Application questions

`profile/answers.yaml` (git-ignored):
```yaml
work_authorization: "Authorized to work in the US on F-1 OPT"   # example
requires_sponsorship: true
willing_to_relocate: true
earliest_start: "2027-01"
salary_expectation: "Open / market rate"
years_experience: { python: 4, pytorch: 3, machine_learning: 3 }
eeo: decline_to_answer
links: { github: …, linkedin: …, portfolio: … }
```
- Known questions (matched by label patterns) → answered from the bank.
- Free-text questions ("Why this company?") → drafted by the LLM from the job + master CV, **always flagged for review**.
- Eligibility/legal questions not in the bank → never guessed; the application waits for you.

---

## 5. Review queue

`auto-app apply review` — for each tailored application: job summary, CV diff and PDF, cover letter, every form answer (source: bank / drafted / missing), verification results, Step 1 warnings. Actions: **approve**, **edit** (opens the file; re-verified on save), **drop** (the job's `decision` becomes `not_applied`). Progress is the application's status (`tailored → approved`); the job stays `want_to_apply` until submission.

---

## 6. Submission

| Platform | Method | Plan |
|---|---|---|
| Greenhouse, Lever, Ashby | browser form fill on the public application page | first submitters |
| LinkedIn Easy Apply | browser, in the LinkedIn session (multi-step modal) | after the above |
| Workday | per-company accounts, multi-page forms | manual fallback first |
| Anything else | manual fallback | — |

**Submitter flow (visible browser):** open apply URL → fill fields from the approved package → upload `cv.pdf` → screenshot each step → **stop before the final Submit** and wait for you to click it (default). After submit: capture the confirmation page/screenshot, set `applied`.

**Manual fallback:** `auto-app apply manual <job_id>` opens the apply URL and the CV folder; you apply; `auto-app apply mark-applied <job_id>` records it.

**Safety limits:** max applications per day (e.g. 20) and per company per month; CAPTCHA or login wall → hand to you (same mechanism as Step 1's manual checks); never auto-submit on unknown questions.

---

## 7. Storage

```sql
applications (application_id PK, job_id FK, platform, apply_url, status,  -- tailored | approved | submitted | failed | manual | dropped
              cv_path, cv_json, cover_letter_path, answers_json, verification_json,
              model, prompt_version, created_at, approved_at, submitted_at,
              confirmation_path, error)
```
On submission (`submitted`, or `mark-applied`) the job's `decision` changes from `want_to_apply` to `applied`, with `decided_at`; a dropped application sets `not_applied`. From then on the job follows the recommended TTL (archived after 30 days unseen, kept as a record with its description — [overview §3](README.md#3-job-state)); while it was queued it never expired. Submitters only take `want_to_apply` jobs and refuse ones that are archived or already `applied`.

---

## 8. CLI

```
auto-app apply queue                             # the want_to_apply queue, in your policy's order
auto-app apply tailor [--job ID | --limit N]
auto-app apply review
auto-app apply submit [--job ID] [--platform P]  # fills forms, waits for your final click
auto-app apply manual <job_id> ; auto-app apply mark-applied <job_id>
auto-app apply list [--status S]                 # history
```

---

## 9. Milestones

| # | Deliverable |
|---|---|
| A1 | `master_cv.yaml` schema + loader + default CV render |
| A2 | Tailor + verification + render for one job, end to end |
| A3 | `answers.yaml` + question matching; review queue |
| A4 | Manual fallback (`apply manual`, `apply mark-applied`) — usable from day one |
| A5 | Greenhouse / Lever / Ashby submitters (stop before Submit) |
| A6 | LinkedIn Easy Apply submitter |
| A7 | Optional per-platform auto-submit after N successful supervised submissions |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Fabricated or inflated claims | traceable bullets, number/skill/entity checks, human review |
| Wrong eligibility answer disqualifies you | eligibility only from the answer bank; never guessed |
| Form changes break submitters | stop-before-submit, screenshots, manual fallback |
| Duplicate applications | the job's `decision` is the source of truth; submitters refuse jobs already `applied`; reposts of archived applied jobs carry a "you applied to job #N" warning (Step 1 §7.5) |
| Account flags from automated applying | daily caps, visible browser, human final click |

## 11. Open questions

1. CV template preference (existing LaTeX/Word CV to match?).
2. Cover letters: always, never, or only when requested by the form?
3. Daily application cap.
