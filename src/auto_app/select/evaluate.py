"""Evaluation (docs/step2-select.md §7.6): how well each filter's agent judges, against your labels.

Per filter:
- precision — of the jobs it picked (recommend, or couldn't read: those are shown to you too) that you labelled,
  the share you said it should recommend;
- noise — of its audited "no" jobs (random samples, §7.4) that you labelled, the share you said it should have
  recommended (its misses);
- missed in all ≈ noise × the number of jobs it said no to;
- recall ≈ right picks ÷ (right picks + missed in all), right picks ≈ precision × picks;
- review load — picks ÷ judged;
- by cause — its "no"s split into dealbreaker rejections and "doesn't fit" ones, with the audited misses of each:
  a wrong dealbreaker call hides a job however well it fits;
- training data — whether its unlabelled "no" jobs may train its preference model: yes when the noise's one-sided
  95% exact (Clopper–Pearson) upper bound is under the limit; if not, how many more clean audits would get there.

Ranges are 95% Wilson intervals (for describing a rate); the yes / no training-data test uses the one-sided exact
bound (study.md has the worked example). Nothing is cached: every call reads the current labels.
"""

import math
from dataclasses import dataclass

from auto_app.select.audit import labels, latest_dealbreakers, latest_verdicts, sampled

Z = 1.959964  # 95%


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p, z2 = k / n, Z * Z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _binom_cdf(k: int, n: int, p: float) -> float:
    # P(X <= k), X ~ Binomial(n, p), in log space (stable for large n)
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    return min(1.0, sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
                        for i in range(k + 1)))


def exact_upper(k: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper–Pearson upper bound: the largest rate under which seeing ≤ k in n still has ≥ 5% chance."""
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _binom_cdf(k, n, mid) > 1 - conf:
            lo = mid
        else:
            hi = mid
    return hi


def clean_audits_needed(k: int, n: int, limit: float, cap: int = 100_000) -> int | None:
    """More audits with no miss needed until the exact upper bound is under the limit (None: beyond the cap)."""
    if exact_upper(k, n) < limit:
        return 0
    lo, hi = 1, 1
    while exact_upper(k, n + hi) >= limit:
        hi *= 2
        if hi > cap:
            return None
    while lo < hi:
        mid = (lo + hi) // 2
        if exact_upper(k, n + mid) < limit:
            hi = mid
        else:
            lo = mid + 1
    return lo


@dataclass
class Rate:
    k: int
    n: int

    @property
    def p(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def range(self) -> tuple[float, float]:
        return wilson(self.k, self.n)

    def fmt(self) -> str:
        if not self.n:
            return "—"
        lo, hi = self.range
        return f"{self.p:.0%} ({lo:.0%}–{hi:.0%})"


@dataclass
class FilterEval:
    filter: str
    version: str | None  # None = as used (every version's latest verdicts)
    judged: int
    picks: int  # recommend or couldn't read
    no: int
    precision: Rate  # good of your labels on its picks
    noise: Rate  # good of your labels on its audited "no" jobs
    audited_open: int  # sampled but not yet labelled
    limit: float
    no_dealbreaker: int = 0  # of its "no"s, those with a dealbreaker
    noise_dealbreaker: Rate | None = None  # misses among audited dealbreaker rejections
    noise_fit: Rate | None = None  # … among audited "doesn't fit" rejections

    @property
    def review_load(self) -> float | None:
        return self.picks / self.judged if self.judged else None

    @property
    def good_picks(self) -> float | None:
        return self.precision.p * self.picks if self.precision.n else None

    def lost(self, which: str = "p") -> float | None:
        if not self.noise.n:
            return None
        lo, hi = self.noise.range
        rate = {"p": self.noise.p, "lo": lo, "hi": hi}[which]
        return rate * self.no

    def recall(self, which: str = "p") -> float | None:
        good = self.good_picks
        lost = self.lost({"p": "p", "lo": "hi", "hi": "lo"}[which])  # low recall ↔ high losses
        if good is None or lost is None:
            return None
        return good / (good + lost) if good + lost else None

    @property
    def noise_upper(self) -> float:
        return exact_upper(self.noise.k, self.noise.n)

    @property
    def training_ok(self) -> bool:
        return self.noise.n > 0 and self.noise_upper < self.limit

    @property
    def more_audits(self) -> int | None:
        return clean_audits_needed(self.noise.k, self.noise.n, self.limit)

    def lines(self) -> list[str]:
        ver = f"v {self.version}" if self.version else "as used"
        load = f"{self.review_load:.0%}" if self.review_load is not None else "—"
        out = [f"{self.filter}  ({ver})  judged {self.judged} · picks {self.picks} (review load {load}) · no {self.no}",
               f"  precision      {self.precision.fmt():<16} {self.precision.n} of its picks labelled, "
               f"{self.precision.k} should be recommended"]
        if self.noise.n:
            lost, lo, hi = self.lost(), self.lost("lo"), self.lost("hi")
            out.append(f"  noise          {self.noise.fmt():<16} {self.noise.n} audited, {self.noise.k} missed "
                       f"→ ≈{lost:.0f} missed in all ({lo:.0f}–{hi:.0f}) of {self.no} said no")
        else:
            out.append("  noise          —                no audited \"no\" jobs labelled yet"
                       + (f" ({self.audited_open} waiting)" if self.audited_open else " — run `filter audit`"))
        if self.no_dealbreaker:
            nd, nf = self.noise_dealbreaker, self.noise_fit
            out.append(f"  by cause       dealbreaker: {self.no_dealbreaker} said no, {nd.n} audited, {nd.k} missed"
                       + (f" ({nd.fmt()})" if nd.n else "")
                       + f" · doesn't fit: {self.no - self.no_dealbreaker} said no, {nf.n} audited, {nf.k} missed"
                       + (f" ({nf.fmt()})" if nf.n else ""))
        r = self.recall()
        if r is not None:
            out.append(f"  recall         ≈{r:.0%} ({self.recall('lo'):.0%}–{self.recall('hi'):.0%})")
        else:
            out.append("  recall         —                needs labelled picks and audits")
        if self.training_ok:
            out.append(f"  training data  YES — noise is below {self.limit:.0%} (one-sided 95% bound "
                       f"{self.noise_upper:.1%}): its unlabelled \"no\" jobs may train its model")
        else:
            more = self.more_audits
            why = (f"noise could be {self.noise_upper:.0%} > {self.limit:.0%} limit" if self.noise.n
                   else "no audits yet")
            need = f"~{more} more clean audits" if more else "more audits"
            out.append(f"  training data  NO — {why} ({need}); your labels only")
        return out


def evaluate(store, filters: list[str] | None, limit: float, version: str | None = None) -> list[FilterEval]:
    """filters: names to evaluate (None: every filter that has judged a job). version: one filter version only."""
    verdicts = latest_verdicts(store, version=version)
    breakers = latest_dealbreakers(store, version=version)
    have = labels(store)
    items = sampled(store)
    draw_version: dict[int, str | None] = {}
    if version:
        from auto_app.select.audit import draws_of

        for f in {f for _, f in items}:
            for d in draws_of(store, f):
                draw_version[d["draw_id"]] = d["filter_version"]
    names = sorted(filters if filters else {f for _, f in verdicts})
    out = []
    for name in names:
        mine = {j: v for (j, f), v in verdicts.items() if f == name}
        picks = [j for j, v in mine.items() if v is not False]
        nos = [j for j, v in mine.items() if v is False]
        lab_picks = [have[(j, name)] for j in picks if (j, name) in have]
        audited = [j for (j, f), d in items.items() if f == name and (not version or draw_version.get(d) == version)]
        lab_audit = [have[(j, name)] for j in audited if (j, name) in have]
        db = {j for j in nos if breakers.get((j, name))}
        lab_db = [have[(j, name)] for j in audited if (j, name) in have and j in db]
        lab_fit = [have[(j, name)] for j in audited if (j, name) in have and j not in db]
        out.append(FilterEval(
            filter=name, version=version, judged=len(mine), picks=len(picks), no=len(nos),
            precision=Rate(sum(lab_picks), len(lab_picks)), noise=Rate(sum(lab_audit), len(lab_audit)),
            audited_open=sum(1 for j in audited if (j, name) not in have), limit=limit,
            no_dealbreaker=len(db), noise_dealbreaker=Rate(sum(lab_db), len(lab_db)),
            noise_fit=Rate(sum(lab_fit), len(lab_fit))))
    return out


def format_eval(evals: list[FilterEval]) -> str:
    if not evals:
        return "no filter has judged a job yet — run `auto-app filter run`"
    return "\n\n".join("\n".join(e.lines()) for e in evals)
