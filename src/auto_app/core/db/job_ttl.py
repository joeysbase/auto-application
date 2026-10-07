"""Job TTL (C11, docs/step1-collect.md §9.1–9.2). Applied whenever job dates are refreshed (after collecting, judging,
a decision — `DbStore.refresh_job_dates`), whoever runs it; `auto-app data purge` applies it too, with a report.

A job expires `jobs.ttl_days[state]` days after any of its postings was last seen:
- **judged by Step 2** (any filter ran on it), recommended, or decided → **archived** — it's training data
  (docs/step2-select.md §7.8): a record (summary, Step 2 result, your decision, posting identities, matching keys)
  plus what a filter reads (the primary posting's parsed fields); raw data, normalized data and the other
  postings' parsed data are dropped. Archived jobs never merge with active ones; their postings' identities are
  freed (renamed `<id>#archived<job>`), so a listing seen again becomes a new job. Among themselves archived
  jobs are deduplicated: one that shares a matching key and the title with an earlier archived job is linked
  to it (`duplicate_of`).
- **never judged** (not evaluated, no decision) → **deleted**: the job, its keys and locations, and every linked
  posting (Level 1b duplicates included) with its raw data and screenshot. A listing seen again later is new.
- want to apply → never expires while queued.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, func, or_, select, update

from auto_app.core.config import JobTTL
from auto_app.core.db import tables as T
from auto_app.core.models import utcnow


@dataclass
class ExpiredJob:
    job_id: int
    state: str  # not_evaluated | un_recommended | recommended | applied | not_applied
    title: str | None
    company: str | None
    last_seen_at: datetime | None
    postings: int


@dataclass
class TTLReport:
    dry_run: bool
    deleted: list[ExpiredJob] = field(default_factory=list)
    archived: list[ExpiredJob] = field(default_factory=list)
    linked: dict[int, int] = field(default_factory=dict)  # archived job → the earlier archived job it duplicates
    deleted_postings: int = 0

    def format(self, limit: int = 15) -> list[str]:
        verb = ("would delete", "would archive") if self.dry_run else ("deleted", "archived")
        linked = f" ({len(self.linked)} linked to an earlier archived copy)" if self.linked else ""
        out = [f"  jobs (TTL)      {verb[0]} {len(self.deleted)} never-judged jobs ({self.deleted_postings} postings), "
               f"{verb[1]} {len(self.archived)}{linked}"]
        for label, rows in ((verb[0], self.deleted), (verb[1], self.archived)):
            for e in rows[:limit]:
                seen = e.last_seen_at.strftime("%Y-%m-%d") if e.last_seen_at else "?"
                out.append(f"    {label:<13} #{e.job_id:<5} {e.state:<14} last seen {seen}  "
                           f"{(e.company or '')[:20]} — {(e.title or '')[:60]}")
            if len(rows) > limit:
                out.append(f"    … and {len(rows) - limit} more")
        return out


def _state(step2: str | None, decision: str | None) -> str:
    return decision or step2 or "not_evaluated"


def apply_job_ttl(store, ttl: JobTTL, *, now: datetime | None = None, dry_run: bool = False) -> TTLReport:
    """Refresh the job dates, then expire (`data purge`)."""
    now = now or utcnow()
    if not dry_run:
        store.refresh_job_dates(ttl, now, expire=False)
    return expire_jobs(store, ttl, now=now, dry_run=dry_run)


def expire_jobs(store, ttl: JobTTL, *, now: datetime | None = None, dry_run: bool = False) -> TTLReport:
    """Archive or delete every active job past its TTL (the dates as they are)."""
    now = now or utcnow()
    rep = TTLReport(dry_run)
    j, p = T.jobs, T.postings
    cutoff = now.replace(tzinfo=None) if now.tzinfo else now  # SQLite stores naive UTC
    with store.engine.begin() as conn:
        latest = dict(conn.execute(select(p.c.job_id, func.max(p.c.last_seen_at))
                                   .where(p.c.job_id.is_not(None)).group_by(p.c.job_id)).all())
        counts = dict(conn.execute(select(p.c.job_id, func.count()).where(p.c.job_id.is_not(None))
                                   .group_by(p.c.job_id)).all())
        rows = conn.execute(
            select(j.c.job_id, j.c.step2, j.c.decision, j.c.last_seen_at, p.c.title, p.c.company)
            .select_from(j.outerjoin(p, p.c.posting_id == j.c.primary_posting_id))
            .where(j.c.archived_at.is_(None))
        ).all()
        judged = {jid for (jid,) in conn.execute(select(T.judgments.c.job_id).distinct())}
        for job_id, step2, decision, last, title, company in rows:
            if decision == "want_to_apply":
                continue  # queued for Step 3: never expires while waiting (docs/README.md §3)
            seen = latest.get(job_id) or last
            if seen is None:
                continue
            seen = seen.replace(tzinfo=None) if seen.tzinfo else seen
            if seen + timedelta(days=ttl.for_state(step2)) >= cutoff:
                continue
            e = ExpiredJob(job_id, _state(step2, decision), title, company, seen, counts.get(job_id, 0))
            keep = job_id in judged or step2 == "recommended" or decision  # training data / your decisions
            (rep.archived if keep else rep.deleted).append(e)
        rep.deleted_postings = sum(e.postings for e in rep.deleted)
        if dry_run:
            return rep
        shots: list[Path] = []
        if rep.deleted:
            shots += _delete_jobs(conn, [e.job_id for e in rep.deleted])
        for e in rep.archived:
            shots += _archive_job(conn, e.job_id, now)
            dup = _link_archived(conn, e.job_id)
            if dup:
                rep.linked[e.job_id] = dup
    for shot in shots:
        shot.unlink(missing_ok=True)
    return rep


def _postings_of(conn, job_ids: list[int]) -> list[int]:
    return [pid for (pid,) in conn.execute(select(T.postings.c.posting_id).where(T.postings.c.job_id.in_(job_ids)))]


def _drop_raw(conn, pids: list[int]) -> list[Path]:
    r = T.posting_raw
    shots = [Path(s) for (s,) in conn.execute(select(r.c.screenshot_path).where(r.c.posting_id.in_(pids),
                                                                               r.c.screenshot_path.is_not(None)))]
    conn.execute(r.delete().where(r.c.posting_id.in_(pids)))
    return shots


def _delete_jobs(conn, job_ids: list[int]) -> list[Path]:
    """The jobs and everything linked to them, in one transaction (the caller's)."""
    j, p = T.jobs, T.postings
    pids = _postings_of(conn, job_ids)
    conn.execute(update(j).where(j.c.job_id.in_(job_ids)).values(primary_posting_id=None))
    conn.execute(update(p).where(p.c.posting_id.in_(pids)).values(duplicate_of=None))
    shots = _drop_raw(conn, pids)
    conn.execute(T.job_locations.delete().where(T.job_locations.c.job_id.in_(job_ids)))
    conn.execute(T.job_keys.delete().where(T.job_keys.c.job_id.in_(job_ids)))
    conn.execute(p.delete().where(p.c.posting_id.in_(pids)))
    conn.execute(j.delete().where(j.c.job_id.in_(job_ids)))
    return shots


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _archive_job(conn, job_id: int, now: datetime) -> list[Path]:
    """Keep the record and what a filter reads (§9.2); drop the heavy data; free the postings' identities."""
    from auto_app.core.db.store import z

    j, p = T.jobs, T.postings
    job = conn.execute(select(j).where(j.c.job_id == job_id)).mappings().one()
    postings = conn.execute(select(p).where(p.c.job_id == job_id).order_by(p.c.first_seen_at)).mappings().all()
    primary = next((x for x in postings if x["posting_id"] == job["primary_posting_id"]), postings[0] if postings else None)
    locations = [loc for (loc,) in conn.execute(select(T.job_locations.c.location_norm)
                                                 .where(T.job_locations.c.job_id == job_id)
                                                 .order_by(T.job_locations.c.location_norm))]
    record = {
        "title": primary["title"] if primary else None,
        "company": primary["company"] if primary else job["company_norm"],
        "company_norm": job["company_norm"],
        "locations": locations,
        "apply_url": job["canonical_url"],
        "posted_at": _iso(primary["posted_at"]) if primary else None,
        "first_seen_at": _iso(job["first_seen_at"]),
        "last_seen_at": _iso(job["last_seen_at"]),
        "step2": {"verdict": job["step2"], "at": _iso(job["step2_at"])},
        "decision": {"value": job["decision"], "at": _iso(job["decided_at"])},
        "postings": [{"source": x["source"], "source_job_id": original_id(x["source_job_id"]), "url": x["source_url"],
                      "apply_url": x["apply_url"], "title": x["title"], "first_seen_at": _iso(x["first_seen_at"]),
                      "last_seen_at": _iso(x["last_seen_at"])} for x in postings],
    }
    description = None
    if job["decision"] == "applied" and primary and primary["parsed"]:
        description = z(primary["parsed"].get("description_text") or None)
    conn.execute(update(j).where(j.c.job_id == job_id).values(
        archived_at=now, archive=record, archive_description=description, expires_at=None))
    pids = [x["posting_id"] for x in postings]
    keep = primary["posting_id"] if primary else None
    for x in postings:
        conn.execute(update(p).where(p.c.posting_id == x["posting_id"]).values(
            parsed=x["parsed"] if x["posting_id"] == keep else None,  # what a filter reads: training data
            normalized=None, state="archived",
            source_job_id=archived_id(x["source_job_id"], job_id)))  # the listing can come back as a new job
    conn.execute(T.job_locations.delete().where(T.job_locations.c.job_id == job_id))
    return _drop_raw(conn, pids)


ARCHIVED_MARK = "#archived"


def archived_id(source_job_id: str, job_id: int) -> str:
    return source_job_id if ARCHIVED_MARK in source_job_id else f"{source_job_id}{ARCHIVED_MARK}{job_id}"


def original_id(source_job_id: str) -> str:
    return source_job_id.split(ARCHIVED_MARK, 1)[0]


DEDUP_KEYS = ("employer_key", "req_id", "desc_hash", "fingerprint")


def _link_archived(conn, job_id: int) -> int | None:
    """Dedup within the archive (step1 §7.5): a newly archived job that shares a matching key with an earlier
    archived job — and has the same title (a different title is a different job) — is linked to that job's group
    (its first member). Returns the job it was linked to."""
    j, k = T.jobs, T.job_keys
    me = conn.execute(select(j.c.title_norm).where(j.c.job_id == job_id)).scalar()
    keys = conn.execute(select(k.c.key_type, k.c.key_value).where(k.c.job_id == job_id,
                                                                   k.c.key_type.in_(DEDUP_KEYS))).all()
    if not keys or not me:
        return None
    hits = conn.execute(
        select(j.c.job_id, j.c.duplicate_of, j.c.archived_at).join(k, k.c.job_id == j.c.job_id)
        .where(j.c.archived_at.is_not(None), j.c.job_id != job_id, j.c.title_norm == me,
               or_(*(and_(k.c.key_type == t, k.c.key_value == v) for t, v in keys)))
    ).all()
    roots = {(dup or other) for other, dup, _ in hits}
    roots.discard(job_id)
    if not roots:
        return None
    root = min(roots)  # the oldest group
    conn.execute(update(j).where(j.c.job_id == job_id).values(duplicate_of=root))
    return root
