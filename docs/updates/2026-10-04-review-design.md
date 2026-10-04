# Step 2 review (S3) and the want-to-apply queue (design) — 2026-10-04
**Step:** 2–3   **Status:** designed. The result is in [step2 §7–10](../step2-select.md#8-review), [overview §3](../README.md#3-job-state) and [step3 §0](../step3-apply.md).

## Decisions
- **Review is an interactive full-screen terminal app**, `auto-app filter review`, built with Textual.
  - **Tabs:** Recommended (to decide on) · Un-recommended (to catch wrong rejections) · Want to apply (your queue).
  - **A detail pane** shows each filter's verdict, reason and lookups, the warnings and the full description. `o` opens the link.
  - Search, and marks for what's been answered.
  - Every answer is saved the moment you press a key; `u` undoes.
- **Two separate answers:**
  - **Your decision, per job:** `a` → **want to apply**, `n` → **not applied**.
  - **Feedback, per filter:** `g` the verdict was right, `b` it was wrong. With several filters, `←→` or a number picks which one you're rating.
  - Per filter, because different filters stand for different preferences of the same user, each needing its own preference model. Each rating is a label for that filter's model only.
  - The decision is stored too, as a weaker, per-job signal.
- **A new decision state, `want_to_apply`:** "apply" in review doesn't mark the job applied. It queues it for Step 3, which applies and then sets `applied`. You can withdraw a job from the queue (`not_applied`).
- **Queued jobs never expire** while waiting. They follow the recommended TTL again once applied or withdrawn, and Step 3 checks a posting is still open before applying.
- **How the queue is worked is user-configurable** (Step 3, "queue policy"): one at a time, a pace (e.g. one every 5 minutes, N a day, time windows), an order, batching, per-platform handling. The form is decided with the rest of Step 3, which is still open.
- **Storage:** `reviews` (decision per job) and a new `feedback` table (per job and filter: the rated verdict, right or wrong, reason, filter version, job text). Neither has a foreign key to `jobs`, so labels outlive the TTL.

## Docs updated
- `docs/README.md` §3: the states table with `want_to_apply`, the lifecycle diagram, the TTL table (queued jobs: no expiry), review and the queue.
- `docs/step2-select.md`: principle 6, §7 (per-filter feedback as labels), §8 rewritten (the review app, with a mockup and keys), §9 (`feedback` table), §10 (`filter review`), milestone S3.
- `docs/step3-apply.md`: the goal and flow from the queue; new §0 (the want-to-apply queue and the configurable queue policy); review and storage wording; `apply queue` in the CLI.
- `docs/step1-collect.md`: the `decision` column values and the TTL table row.

## Next
Implement S3: the `want_to_apply` state and no-expiry rule (`job mark`, `job list --state`), the data layer (lists, decisions, feedback, undo), the Textual app, tests, README.
