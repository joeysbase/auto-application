---
name: filter-feedback
description: Teach the user's auto-app filters from their feedback — label a filter's verdict on a job as should / shouldn't recommend with a reason, check how good a filter is, and propose a filter edit when the same feedback keeps coming back. Use for "this shouldn't have been recommended", "the ml filter keeps missing research roles", "how good is my filter?".
---

# Filter feedback

Each filter is a folder, `filters/<name>/`: `command.md` (its instructions), `filter.yaml` (model, tools, which jobs it
applies to), and optionally `parser.py`. A label says whether that filter **should recommend** that job, whatever it
actually said. Labels drive `filter eval` and the audit.

## Label a verdict

```
uv run auto-app filter label <job_id> -f <filter> --should   --reason "<why>"   # it should recommend this job
uv run auto-app filter label <job_id> -f <filter> --shouldnt --reason "<why>"   # it shouldn't
uv run auto-app filter label <job_id> --shouldnt --reason "<why>"               # no filter should (every filter that judged it)
```

Check what each filter said first: `uv run auto-app filter why <job_id> --json`. Always include the user's reason.

## How good is a filter?

- `uv run auto-app filter eval [-f <filter>]` gives precision and noise against the user's labels. Explain it plainly:
  "of the jobs it recommended that you labelled, N were right".
- Few labels mean the numbers are rough. Suggest labelling a sample with `uv run auto-app filter audit` (opens the app).

## Proposing a filter edit

When the same reason keeps coming back (e.g. 5 rejections for "needs a security clearance"):

1. Read the filter: `filters/<name>/command.md` and `filter.yaml`.
2. Propose a **small** change to `command.md` (e.g. add a dealbreaker), shown as a diff.
3. Edit **only after the user says OK.**
4. Then offer to re-judge what's affected, e.g. `uv run auto-app filter run --rescore -f <name> --limit 20`. That costs
   Claude usage, so say so.

## Rules

- Never edit a filter without the user's explicit OK. Never delete one.
- Labels and reasons are the user's opinion: don't invent them.
