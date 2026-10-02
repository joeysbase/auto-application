# Step 2 — Select

**Goal:** from the pool of collected jobs, recommend the ones worth applying to — the way you would by reading each description against your CV and preferences — as **a list for you to decide on**, keeping the reasons so the recommendations can be checked and tuned.

```
pool (step2 = —) ─► A. hard filters ─► B. LLM fit scoring ─► recommended | un_recommended ─► you decide on recommended jobs
                     (free, rules)      (CV + preferences)      (scores + reasons kept)       (apply → Step 3, or not_applied)
```

Status: 📐 designed. Depends on Step 1 parse + dedup (jobs with clean fields and descriptions).

---

## 1. Inputs

| Input | Where | Notes |
|---|---|---|
| Jobs | `jobs` + parsed fields of the primary posting, all `job_locations` | active, not yet evaluated (`step2` empty); warnings from Step 1 ("looks like archived job #N you applied to") are passed to the scorer and shown in review |
| Master CV | `profile/master_cv.yaml` (git-ignored) | shared with Step 3; see step3-apply.md §2 |
| Preferences | `config/preferences.yaml` (git-ignored; example committed) | below |

```yaml
# config/preferences.yaml
target_roles: ["machine learning engineer", "applied scientist", "ML infrastructure"]
seniority: { accept: [entry, new_grad, associate], max_years_required: 2 }
locations:
  accept: ["remote-us", "new york, ny", "boston, ma", "san francisco bay area", "seattle, wa"]
  relocate: true
work_authorization: { needs_sponsorship: true, citizenship_required_ok: false }
employment_types: [full_time]
salary: { min_base_usd: 100000, unknown_ok: true }
reposted: skip                     # skip | penalize | keep — how to treat jobs the site marks as reposted
exclude:
  companies: ["SomeAgency"]
  industries: ["defense"]          # example
  keywords: ["security clearance", "TS/SCI"]
priorities: >                      # free text given to the scorer
  Prefer ML systems / applied ML over pure research; value mentorship and
  production work; avoid pure data-analyst roles.
```

---

## 2. Stage A — Hard filters (rules, no LLM)

Cheap, explainable rules that remove obvious mismatches. Each failed rule is recorded as a reason.

| Rule | Checks |
|---|---|
| Location | at least one of the job's `job_locations` (or remote scope) is acceptable |
| Employment type | in `employment_types` |
| Seniority / years | title words; regex on description for "N+ years" > `max_years_required` |
| Sponsorship / citizenship | phrases like "unable to sponsor", "must be a U.S. citizen", "clearance" vs. `work_authorization` |
| Salary | stated max below `min_base_usd` (unknown passes if `unknown_ok`) |
| Exclusions | companies, industries, keywords |
| Age | posted older than N days (stale) |
| Reposted | `reposted: skip` → drop jobs marked reposted (reason "reposted"); `penalize` → pass them on, and Stage B is told the job is a repost and when it was first seen; `keep` → no effect |

**Why reposts are handled here, not in Step 1:** LinkedIn only shows "Reposted" on the job page, not on the card, so skipping at collection would save few page opens (~5% of jobs) while losing the posting that dedup needs to link a repost to a job you've already seen. Whether a repost is worth applying to is a preference, so it lives in `preferences.yaml` and can be changed and re-run without re-collecting.

Rules are conservative: when text is ambiguous ("sponsorship may be available"), the job passes to Stage B, which reads it in context.

---

## 3. Stage B — LLM fit scoring

One call per job, structured output.

**Prompt layout** (for prompt caching — the prefix is identical for every job):
1. *System + rubric* — how to score, what counts as a red flag, output schema.
2. *CV* — rendered from `master_cv.yaml`.
3. *Preferences* — the YAML above, including `priorities`.
4. *Job* — title, company, locations, workplace/employment type, salary, repost status (with first-seen date), and the description.

**Output schema:**
```json
{
  "fit_score": 0-100,
  "recommendation": "apply | maybe | skip",
  "matched_requirements": ["…"],
  "missing_requirements": ["…"],
  "years_required": 0,
  "seniority_fit": "under | fit | over",
  "sponsorship": "ok | unclear | blocked",
  "red_flags": ["…"],
  "summary": "one sentence a human can act on"
}
```

**Model choice:** a small, fast model (Claude Haiku 4.5) for every job; optionally re-score borderline jobs (`maybe`, or score within ±10 of the threshold) with a stronger model (Claude Sonnet 5). The model id and a `prompt_version` are stored with every score.

**Result:** `apply` or score ≥ threshold (start at 70) → `step2 = recommended`; `skip` or score < threshold − 20 → `un_recommended`; otherwise `recommended` with a `maybe` flag. Jobs failing Stage A's hard filters are `un_recommended` with the failed rule as the reason. Step 2 never sets `decision` — that's yours.

**Re-scoring:** changing the CV, preferences, or rubric bumps `prompt_version`; `auto-app select --rescore` re-evaluates active jobs **without a decision** (recommended-undecided and un_recommended). Jobs you decided on (`applied`, `not_applied`) and archived jobs are never touched.

---

## 4. Review and calibration

- `auto-app review` lists **recommended, undecided** jobs (score, summary, matched/missing, red flags, warnings, link, "expires in N days") and lets you mark each **apply** (→ Step 3 creates an application; the job's `decision` becomes `applied` when it's submitted) or **no** (`decision = not_applied`). Undecided jobs stay in the list until you decide or they're archived by the job TTL (30 days unseen).
- `auto-app jobs --un-recommended` shows what Step 2 left out and why; you can **promote** one to `recommended` (recorded as a disagreement for calibration). un_recommended jobs are deleted by the job TTL after 7 days unseen.
- **Calibration period (first ~2 weeks):** also review a sample of `un_recommended` jobs. Disagreements are recorded (`job_reviews`) and used to adjust rules, `priorities`, and the threshold.
- A weekly summary: counts per stage, top rejection reasons, agreement rate with your reviews.

---

## 5. Storage

```sql
job_scores  (job_id FK, stage,           -- 'rules' | 'llm'
             model, prompt_version, result_json, fit_score, recommendation, created_at,
             PK(job_id, stage, prompt_version))
job_reviews (job_id FK, reviewer_decision, model_decision, note, created_at)
```
Step 2 sets `jobs.step2` (`recommended` | `un_recommended`) and `step2_at`; your review sets `jobs.decision = not_applied` (Step 3 sets `applied`). Job state and TTL: [overview §3](README.md#3-job-state).

---

## 6. CLI

```
auto-app select [--limit N] [--rescore] [--model MODEL]
auto-app review [--include-filtered SAMPLE]
auto-app why <job_id>            # show rules + LLM result for a job
```

---

## 7. Milestones

| # | Deliverable |
|---|---|
| S1 | `preferences.yaml` + master CV loader (shared with Step 3) |
| S2 | Stage A rules with reasons; tests on real parsed postings |
| S3 | Stage B scorer (structured output, prompt caching, stored results) — first tried on ~30 saved LinkedIn jobs |
| S4 | `review` + `why`; calibration log |
| S5 | Borderline re-scoring with a stronger model; weekly summary |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Over-aggressive filtering hides good jobs | conservative rules; calibration sampling of `un_recommended`; reasons stored; promote from `jobs --un-recommended` |
| LLM inconsistency | structured output, fixed rubric, stored `prompt_version`, borderline re-scoring |
| Cost growth | cached prefix, small model by default, only unscored jobs, hard filters first |
| Sponsorship language is subtle | Stage B reads it in context; `unclear` goes to review, not auto-reject |

## 9. Open questions

1. LLM provider and budget (default: Claude API).
2. Starting threshold and whether `maybe` jobs go to Step 3 automatically.
3. Preferences you want encoded beyond the example (company size, industry, team type).
