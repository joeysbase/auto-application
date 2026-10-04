# Study guide: the statistics and ML behind Step 2's evaluation

What to search, and what to read, to understand the evaluation, audit and preference-model design in [step2-select.md §7](step2-select.md).

**If you only have an hour:** read the Wikipedia article "Binomial proportion confidence interval", then Hanley & Lippman-Hand (1983). That covers why 60 clean audits are needed to show the noise is below 5%.

---

## The worked example: why 60 audits?

You audit *n* jobs a filter rejected and find **zero** you'd have wanted. How high could the true noise rate *p* be?

If the noise were *p*, the chance that all *n* audited jobs are clean is (1 − *p*)ⁿ. The one-sided 95% upper bound is the *p* where that chance drops to 5%:

  (1 − *p*)ⁿ = 0.05  →  *p* = 1 − 0.05^(1/n) ≈ 3 / *n*  (because ln 0.05 ≈ −3: the **rule of three**)

For *n* = 60, *p* ≈ 4.9%. Checking it directly: if the noise really were 5%, the chance of 60 clean jobs in a row is 0.95⁶⁰ ≈ 4.6%, too unlikely to believe.

| audited, 0 misses | noise is below (one-sided exact, 95%) | two-sided 95% Wilson upper end |
|---|---|---|
| 10 | 25.9% | 27.8% |
| 30 | 9.5% | 11.4% |
| 60 | 4.9% | 6.0% |
| 100 | 3.0% | 3.7% |

Each miss you find raises the number of audits needed to show the noise is below 5% (one-sided exact):

| misses found | audits needed |
|---|---|
| 0 | 59 |
| 1 | 93 |
| 2 | 124 |
| 3 | 153 |

**Takeaways:**
- The rule of three only applies when zero misses are found.
- A yes/no question ("below 5%?") calls for a **one-sided** bound. A two-sided range is wider and is for *describing* a rate, as in the panel.

---

## 1. Confidence bounds for a proportion

| Search | What you'll learn |
|---|---|
| "rule of three" statistics | the 3/n shortcut for zero observed events |
| "binomial proportion confidence interval" | the general problem; the Wikipedia article compares every method |
| "Clopper–Pearson interval" | the exact interval behind the 59 / 93 / 124 numbers |
| "Wilson score interval" | the range the panel displays, and why it beats the textbook ±1.96·√(p(1−p)/n) |
| "one-sided vs two-sided confidence interval" | why "is it below 5%?" needs the one-sided bound |
| "zero-failure acceptance sampling" / "success run theorem" | the same question in quality control and reliability testing |

Read:
- Hanley & Lippman-Hand (1983), *"If nothing goes wrong, is everything all right? Interpreting zero numerators"*, JAMA. The rule of three in 4 pages.
- Brown, Cai & DasGupta (2001), *"Interval estimation for a binomial proportion"*, Statistical Science. Why the textbook interval fails and Wilson works.
- Agresti & Coull (1998), *"Approximate is better than 'exact' for interval estimation of binomial proportions"*, The American Statistician.

## 2. Estimating misses and recall from audit samples

| Search | Why it matters here |
|---|---|
| "simple random sampling estimate population total" | noise × pool size = good jobs lost |
| "stratified sampling estimator" | combining audit draws taken from pools of different sizes |
| "finite population correction" | when a sample is a large share of a small pool |
| "estimating recall with sampling" / "e-discovery recall estimation" | legal document review estimates missed relevant documents by sampling the rejected pile — the same problem |

## 3. Training on un-recommended jobs: biased and noisy labels

| Search | Why it matters here |
|---|---|
| "positive-unlabeled learning" (PU learning) | learning when only some positives are labelled |
| "learning with noisy labels" / "class-conditional label noise" | treating un-recommended jobs as "no" with a known flip rate (the measured noise) |
| "sample selection bias" / "missing not at random" | why labels only on recommended jobs mislead a model |
| "active learning uncertainty sampling" | the idea behind the Second look list |

Read:
- Elkan & Noto (2008), *"Learning classifiers from only positive and unlabeled data"*, KDD.
- Natarajan et al. (2013), *"Learning with noisy labels"*, NeurIPS.
- Frénay & Verleysen (2014), *"Classification in the presence of label noise: a survey"*, IEEE TNNLS.

## 4. Evaluating a model that keeps retraining

| Search | Why it matters here |
|---|---|
| "prequential evaluation" / "rolling-origin evaluation" / "time series cross-validation" | the replay test: train on the past, score the next batch |
| "data leakage near-duplicates train test split" | why linked duplicate jobs stay on one side of a split |
| "precision-recall curve vs ROC imbalanced" | why precision-recall AUC (not ROC) when good jobs are rare |
| "probability calibration reliability diagram" / "Platt scaling" | checking that a 70% score means 70% |

## 5. Shadow mode, switching on, and drift

| Search | Why it matters here |
|---|---|
| "shadow deployment machine learning" / "champion challenger model" | shadow mode, and the probation for new model versions |
| "concept drift detection" (DDM, ADWIN, Page-Hinkley) | principled versions of the drift check |
| "two-proportion test" / "Fisher's exact test" | testing "recent precision is lower than before" properly |

Read:
- Gama et al. (2014), *"A survey on concept drift adaptation"*, ACM Computing Surveys.
- Chip Huyen (2022), *Designing Machine Learning Systems*, O'Reilly. Chapters on data distribution shifts, monitoring, continual learning, and shadow deployment cover sections 4–5 in practical terms.
