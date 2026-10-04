"""Audit (docs/step2-select.md §7.4): random samples of each filter's "no" jobs, labelled by you, to measure how
often a filter wrongly rejects (its noise).

A filter's pool = the jobs whose latest verdict from it is "no" (active and archived — archived judged jobs are
training data), that you haven't labelled for it, and that aren't in one of its samples already. A draw is
stored with the pool's size, so a sample can't be cherry-picked and draws from different weeks combine correctly.
"""

import random
from dataclasses import dataclass

from sqlalchemy import func, insert, select

from auto_app.core.db import tables as T
from auto_app.core.models import utcnow


def latest_verdicts(store, filter: str | None = None, version: str | None = None) -> dict[tuple[int, str], bool | None]:
    """(job, filter) → its latest readable verdict: True recommend / False no / None couldn't read (errors skipped)."""
    out: dict[tuple[int, str], bool | None] = {}
    for r in store.judgment_rows(filter=filter):  # ordered by job, filter, time: the last one wins
        if r["status"] == "error" or (version and r["filter_version"] != version):
            continue
        out[(r["job_id"], r["filter"])] = r["recommend"]
    return out


def latest_dealbreakers(store, version: str | None = None) -> dict[tuple[int, str], bool | None]:
    """(job, filter) → whether its latest readable answer named a dealbreaker (None: not given)."""
    out: dict[tuple[int, str], bool | None] = {}
    for r in store.judgment_rows():
        if r["status"] == "error" or (version and r["filter_version"] != version):
            continue
        out[(r["job_id"], r["filter"])] = r["dealbreaker"]
    return out


def labels(store) -> dict[tuple[int, str], bool]:
    """(job, filter) → your label (good / bad)."""
    fb = T.feedback
    with store.engine.connect() as conn:
        return {(j, f): lab for j, f, lab in conn.execute(select(fb.c.job_id, fb.c.filter, fb.c.label)
                                                          .where(fb.c.label.is_not(None)))}


def sampled(store, filter: str | None = None) -> dict[tuple[int, str], int]:
    """(job, filter) → the draw it's in."""
    a = T.audit_items
    q = select(a.c.job_id, a.c.filter, a.c.draw_id)
    if filter:
        q = q.where(a.c.filter == filter)
    with store.engine.connect() as conn:
        return {(j, f): d for j, f, d in conn.execute(q)}


def pool(store, filter: str) -> list[int]:
    verdicts = latest_verdicts(store, filter)
    have, taken = labels(store), sampled(store, filter)
    return sorted(j for (j, f), v in verdicts.items()
                  if v is False and (j, f) not in have and (j, f) not in taken)


@dataclass
class Draw:
    draw_id: int
    filter: str
    pool_size: int
    jobs: list[int]


def draw(store, filter: str, n: int, version: str | None = None, rng: random.Random | None = None) -> Draw | None:
    """A uniform random sample of n jobs from the filter's pool (fewer if the pool is smaller); None if it's empty."""
    candidates = pool(store, filter)
    if not candidates:
        return None
    picked = sorted((rng or random.SystemRandom()).sample(candidates, min(n, len(candidates))))
    with store.engine.begin() as conn:
        draw_id = conn.execute(insert(T.audit_draws).values(
            filter=filter, filter_version=version, pool_size=len(candidates), size=len(picked),
            drawn_at=utcnow())).inserted_primary_key[0]
        conn.execute(insert(T.audit_items), [{"draw_id": draw_id, "job_id": j, "filter": filter} for j in picked])
    return Draw(draw_id, filter, len(candidates), picked)


def audit_status(store, filters: set[str] | None = None) -> dict[int, dict[str, bool | None]]:
    """job → {filter: your label (None = not yet)} for every sampled (job, filter)."""
    have = labels(store)
    out: dict[int, dict[str, bool | None]] = {}
    for (j, f) in sampled(store):
        if filters and f not in filters:
            continue
        out.setdefault(j, {})[f] = have.get((j, f))
    return out


def unlabeled_count(store, filter: str) -> int:
    have = labels(store)
    return sum(1 for key in sampled(store, filter) if key not in have)


def draws_of(store, filter: str) -> list[dict]:
    d = T.audit_draws
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(d).where(d.c.filter == filter).order_by(d.c.draw_id)).mappings()]


def total_items(store, filter: str | None = None) -> int:
    a = T.audit_items
    q = select(func.count()).select_from(a)
    if filter:
        q = q.where(a.c.filter == filter)
    with store.engine.connect() as conn:
        return conn.execute(q).scalar() or 0
