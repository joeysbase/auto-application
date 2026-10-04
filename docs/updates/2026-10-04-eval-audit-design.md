# Design: evaluation, audit and the preference model's data — 2026-10-04
**Step:** 2   **Status:** designed (S4 evaluation + audit, S5 preference model); not implemented.

## What was decided
- **Evaluation first.** Before training anything, measure each filter's agent and find out whether the cheap labels — un-recommended jobs nobody reviewed — are clean enough to train on.
- **The label** (§7.1), per job and filter: *should this filter have recommended it?* It comes from the verdict plus your `g` / `b`; jobs whose answer couldn't be read get a direct `y` / `n`. It's about the job, so it stays valid across command edits.
- **The bias** (§7.2):
  - recommended jobs are labelled reliably, but carry only hard negatives;
  - un-recommended jobs are many and unread, so treating them as "no" adds noise equal to the agent's false-negative rate;
  - that noise is measured first, and those jobs are only used for training if it's low.
- **Audit** (§7.3): `auto-app filter audit` opens the full-screen app in audit mode.
  - It shows a stored random sample of each filter's "no" pool.
  - Keys: `g` / `b` per filter, `G` all right, `d` draw more.
  - An **evaluation panel** updates live as you label (`e` toggles it, in `job browse` too).
- **`filter eval`** (§7.4): per filter — precision, noise, good jobs lost, recall, review load, and whether its weak negatives are usable.
  - Every figure comes with a 95% range.
  - "Usable" means the noise's upper bound is below `AUTO_APP_SELECT__NOISE_LIMIT` (default 5%).
  - The numbers are recomputed from the database on every label, so they're always current.
- **Preference model** (§7.5–7.6), one per filter:
  - a binary classifier (local embeddings + calibrated logistic regression first), OR-ed with the agent;
  - a "Second look" list of the model's top picks among unreviewed un-recommended jobs;
  - training data weighted by trust; unreviewed "no"s only once evaluation allows them.
- **Training records** (§7.7): judged jobs keep a compact record (text + verdicts) past their TTL.

## Bug found while designing
TTL deletion of a judged un_recommended job fails on the `judgments` foreign key, so `data purge` stops. It was reproduced on a test database. It will be fixed first in S4, by the training records above.

## Docs
- step 2: §7 rewritten (7.1–7.7), §9 storage (`audit_draws`, `audit_samples`, `training_jobs`), §10 CLI, §11 milestones S4 / S5;
- step 1 §9.1: the bug note.

## Revised the same day: archived jobs are training data
- **Training data must only grow**, since the model retrains as labels arrive. So every expired **judged** job (recommended or un_recommended) is archived, never deleted. Jobs never judged are **not** training data and are still deleted. Archived jobs keep what a filter reads (description included), their verdicts and your labels; raw captures are dropped (~3 KB per job).
- **Archived jobs never merge with active ones.** A listing seen again after its job was archived becomes a new job and is judged again.
- **The archive is deduplicated within itself** when jobs are archived, using the Level 2 rules. Duplicates are linked, not merged, so each job is one training example and copies can't leak across a train / test split.
- **This replaces the separate "training record" table**, and fixes the purge bug.
- Docs: overview §3, step 1 §7.5 / §9.1 / §9.2, step 2 §7.7 / §9 / §11.

## Revised again: the preference model as a second judge, with shadow mode
- **Two judges per filter, both evaluated the same way.** Each job keeps the agent's verdict (per command version) and the model's (per model version, frozen once made). A filter recommends a job if either judge does.
- **The label is now a direct per-filter answer:** `g` good job / `b` bad job for this filter, replacing "this verdict was right / wrong". Each judge's right or wrong is derived from it, and existing ratings convert exactly.
- **Four regions per filter.** Where the judges disagree, the job is recommended, so you label it anyway. Only the **both-no** region needs random audits, so one audit stream measures the agent, the model and the combination.
- **Shadow mode.** A new model judges every job but recommends nothing. Its extra picks go to a random-order Second look tab. It's switched on automatically when its extra picks are good enough (default: ≥ 20 labels, precision ≥ 50%), and back to shadow if they stop being good enough.
- **Retraining.** Each new model version passes an offline replay test against baselines before it judges.
- **Un-recommended jobs are training data only while the both-no region's noise is below `AUTO_APP_SELECT__NOISE_LIMIT`** (upper bound of its range, default 5%). This is re-checked at every retrain. Until then the model trains on your labels only, and never on its own verdicts.
- Docs: step 2 §7 rewritten (7.1–7.8), §8 note, §9 storage (`feedback.label`, `model_versions`, `model_verdicts`), §10 CLI (`filter model on|off`), §11 S4 / S5; step 1 open question 6 decided.

## Added: when the model retrains, and the drift check
- **Batch retraining:** a filter's model retrains when it has ≥ 10 new labels (`AUTO_APP_SELECT__RETRAIN_MIN_LABELS`), checked when you leave the app and at the start of `filter run`; also `filter train`. It never retrains in the background or after every single label, because each version is evaluated on its own calls.
- **One retrain:**
  - training data (noise-gated);
  - fit on stored embeddings;
  - the replay test (a worse version is discarded);
  - the new version inherits shadow / on;
  - it scores the active jobs;
  - the switch rules are re-checked.
- **Drift check:** the model's recent precision on its own extra picks (last 20, `AUTO_APP_SELECT__DRIFT_WINDOW`) is compared with its earlier precision. A drop counts only when it's beyond chance (non-overlapping 95% ranges). Then the model retrains at once, goes back to shadow if it's still below the rule, and the panel flags it. Recall and noise are too slow to trigger on.
- **Two views in `filter eval`:** "as used" (all versions pooled, each job counted with the verdict current at the time) and per version.
- Docs: step 2 §7.5, §7.6, §10 (`filter train`), §11 S5.
