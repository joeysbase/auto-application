"""After a filter batch: why judgments failed, and what that means (docs/step4-run.md §2.3).

- **usage** — no usage left (a Claude Code plan limit, an API rate / credit limit, DeepSeek's balance): nobody's fault;
  wait until the reset (from the error when it says, else a retry interval) and run the batch again.
- **broken** — the filter itself can't work: a login or API key rejected, an unknown model, the CLI missing, a config
  error, or every judgment of it failing (`broken_after` different jobs, none working). Stop collecting; tell you.
- **job** — this job failed for a filter that otherwise works (a timeout, an unreadable page): one try more; after
  `tries_per_job`, the job is unjudgeable for that filter.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import insert, select, update

USAGE = re.compile(r"usage limit|rate.?limit|too many requests|\b429\b|\b529\b|overloaded|credit balance|"
                   r"insufficient.?balance|\b402\b|quota|limit reached|out of (extra )?usage|reached your .*limit", re.I)
BROKEN = re.compile(r"invalid.{0,20}(api.?key|x-api-key)|\b401\b|authentication|unauthori[sz]ed|not logged in|"
                    r"please (run )?/?login|log ?in again|permission denied|model.{0,40}not found|unknown model|"
                    r"invalid model|no such model|command not found|no such file or directory|not installed", re.I)
RESET = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.I)


def kind_of(error: str) -> str:
    if USAGE.search(error or ""):
        return "usage"
    if BROKEN.search(error or ""):
        return "broken"
    return "job"


def reset_time(error: str, now: datetime, retry_s: float) -> datetime:
    """When usage comes back: the time the error names (today, or tomorrow if past), else now + retry_s."""
    m = RESET.search(error or "")
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h < 24:
            at = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            return at if at > now else at + timedelta(days=1)
    return now + timedelta(seconds=retry_s)


@dataclass
class BatchHealth:
    usage_until: datetime | None = None  # no usage left: wait until then
    usage_error: str = ""
    broken: dict[str, str] = field(default_factory=dict)  # filter → why
    unjudgeable: list[tuple[int, str, str]] = field(default_factory=list)  # (job, filter, last error) given up now
    retrying: int = 0  # job failures that get another try


def assess(rep, now: datetime, tries_per_job: int = 3, broken_after: int = 3, retry_s: float = 900,
           store=None) -> BatchHealth:
    """What the batch's failures mean. With a store, job failures are counted (judge_tries) and given up after
    tries_per_job; a usage limit or a broken filter never counts against a job."""
    h = BatchHealth()
    by_filter: dict[str, list[tuple[int, str]]] = {}
    for job_id, flt, err in rep.failures:
        k = kind_of(err)
        if k == "usage":
            until = reset_time(err, now, retry_s)
            h.usage_until = max(h.usage_until or until, until)
            h.usage_error = err[:200]
        elif k == "broken":
            h.broken.setdefault(flt, err[:300])
        else:
            by_filter.setdefault(flt, []).append((job_id, err))
    for flt, fails in by_filter.items():
        if flt in h.broken:
            continue
        distinct = {j for j, _ in fails}
        if len(distinct) >= broken_after and not rep.succeeded.get(flt):  # it works on nothing: the filter is broken
            h.broken[flt] = f"{len(distinct)} different jobs failed, none worked — last: {fails[-1][1][:200]}"
            continue
        for job_id, err in fails:
            if store is None:
                h.retrying += 1
                continue
            tries = _count_try(store, job_id, flt, err, now)
            if tries >= tries_per_job:
                _give_up(store, job_id, flt, now)
                h.unjudgeable.append((job_id, flt, err[:200]))
            else:
                h.retrying += 1
    return h


def _count_try(store, job_id: int, flt: str, err: str, now: datetime) -> int:
    from auto_app.core.db import tables as T

    t = T.judge_tries
    with store.engine.begin() as conn:
        row = conn.execute(select(t.c.tries).where(t.c.job_id == job_id, t.c.filter == flt)).first()
        if row is None:
            conn.execute(insert(t).values(job_id=job_id, filter=flt, tries=1, last_error=err[:500], updated_at=now))
            return 1
        conn.execute(update(t).where(t.c.job_id == job_id, t.c.filter == flt)
                     .values(tries=row.tries + 1, last_error=err[:500], updated_at=now))
        return row.tries + 1


def _give_up(store, job_id: int, flt: str, now: datetime) -> None:
    from auto_app.core.db import tables as T

    t = T.judge_tries
    with store.engine.begin() as conn:
        conn.execute(update(t).where(t.c.job_id == job_id, t.c.filter == flt).values(unjudgeable_at=now))


def unjudgeable_rows(store) -> list[dict]:
    from auto_app.core.db import tables as T

    t = T.judge_tries
    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(t).where(t.c.unjudgeable_at.is_not(None))).mappings()]


def clear(store, job_ids: list[int]) -> None:
    """`filter run --job N`: a fresh start for those jobs (their tries and unjudgeable mark go)."""
    from auto_app.core.db import tables as T

    with store.engine.begin() as conn:
        conn.execute(T.judge_tries.delete().where(T.judge_tries.c.job_id.in_(job_ids)))
