# Design: Step 3 — parallel appliers with fallback, a needs-input queue — 2026-10-04
**Step:** 3   **Status:** designed; not implemented. Replaces the earlier "tailor, then submit one by one" design.

## What was decided
- **Several applications at a time.** Workers prepare and submit jobs from the `want_to_apply` queue in parallel, within a pace you set (per hour, per day, per company, an hours window).
- **A needs-input queue.** Anything a worker can't answer confidently goes to a queue, and the job waits there while the others go on. You get a notification and answer in an interactive app, `apply inbox`. One answer can cover every waiting job with the same question, and you can save it to `answers.yaml`.
- **Profile and answers in YAML, filled in up front:** `profile/profile.yaml`, `profile/answers.yaml` and `profile/cv.pdf`. The app asks only when an answer is missing.
  - **Sensitive questions** (work authorization, sponsorship, demographics, legal) come only from `answers.yaml` or from you.
  - **Drafted free text** is always shown to you before submitting.
- **Review or auto-submit:** your choice, per platform (`config/apply.yaml`).
- **Appliers with fallback.** Each platform has an ordered chain, e.g. `greenhouse → simplify → manual`. When an applier can't handle a form or fails twice, the next one takes over. When all are exhausted, the job goes to Failed.
- **Simplify first:** a hand-off that opens the apply link in your Chrome, where Simplify autofills; you submit and confirm. It covers every platform from day one. Greenhouse (questions from its public API), Lever and Ashby come next; Workday and a generic LLM browser agent later.
- **Two passes per application:** prepare (collect every question; nothing submitted), then submit. No browser is held open while a job waits for you.
- **LinkedIn Easy Apply is never automated.** Easy Apply-only jobs go to Simplify or to you.
- **Your own CV as is;** tailored CVs deferred (the earlier design is kept as the plan).

## Docs
- `docs/step3-apply.md` rewritten: principles, apply policy, profile and answers, appliers and outcomes, answering questions, the two passes, `apply inbox`, safety limits, storage, CLI, milestones A1–A7, the deferred tailoring.
- The overview's Step 3 row.

## Decided the same day (step 3 §13)
1. **Answer matching and drafting:** Claude through the Claude Code CLI, locked down like Step 2's runner (a fresh session per call, no tools, nothing saved). `AUTO_APP_APPLY__MODEL` and `__MATCH_THRESHOLD`; start strict, then tune from your corrections.
2. **Workday:** one email for every company's account; passwords in the macOS Keychain, never in files.
3. **"Action needed" pauses only that job;** the site's other jobs carry on. A site that keeps blocking goes into cooldown.

## Added: free-text answers in auto mode
- **Saved free-text answers:** answers such as "why this company" or "about me" in `answers.yaml` (`kind: free_text`) are matched by meaning and count as yours, so they don't stop auto mode.
  - `{company}` and `{title}` are filled in.
  - With `adapt: true`, Claude may tailor the answer lightly; an adapted answer counts as a draft.
- **Claude-drafted answers** are used when no saved answer fits. They're shown before submitting, unless `auto_drafts: true` is set for that platform (off by default), which lets auto mode submit them unseen.
  - Every submitted draft is kept with its application.
  - A draft can be saved from the inbox, becoming your saved answer next time.
