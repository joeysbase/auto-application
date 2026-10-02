"""Job TTL (C11, docs/step1-collect.md §9.1–9.2), applied by `auto-app purge`.

A job expires `jobs.ttl_days[state]` days after any of its postings was last seen:
- not evaluated / un_recommended → **deleted**: the job, its keys and locations, and every linked posting
  (Level 1b duplicates included) with its raw data and screenshot. A listing seen again later is new.
- recommended (undecided, applied, not applied) → **archived**: a compact record (summary, Step 2 result,
  your decision, posting identity, matching keys; the description for applied jobs). Archived jobs are
  excluded from dedup; their postings stay as identities, so collect still skips the same listing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update

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
    deleted_postings: int = 0

    def format(self, limit: int = 15) -> list[str]:
        verb = ("would delete", "would archive") if self.dry_run else ("deleted", "archived")
        out = [f"  jobs (TTL)      {verb[0]} {len(self.deleted)} jobs ({self.deleted_postings} postings), "
               f"{verb[1]} {len(self.archived)}"]
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
    now = now or utcnow()
    rep = TTLReport(dry_run)
    if not dry_run:
        store.refresh_job_dates(ttl, now)
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
        for job_id, step2, decision, last, title, company in rows:
            seen = latest.get(job_id) or last
            if seen is None:
                continue
            seen = seen.replace(tzinfo=None) if seen.tzinfo else seen
            if seen + timedelta(days=ttl.for_state(step2)) >= cutoff:
                continue
            e = ExpiredJob(job_id, _state(step2, decision), title, company, seen, counts.get(job_id, 0))
            (rep.archived if (step2 == "recommended" or decision) else rep.deleted).append(e)
        rep.deleted_postings = sum(e.postings for e in rep.deleted)
        if dry_run:
            return rep
        shots: list[Path] = []
        if rep.deleted:
            shots += _delete_jobs(conn, [e.job_id for e in rep.deleted])
        for e in rep.archived:
            shots += _archive_job(conn, e.job_id, now)
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
    """Keep only what's needed (§9.2): a summary record, posting identities, keys; drop the heavy data."""
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
        "postings": [{"source": x["source"], "source_job_id": x["source_job_id"], "url": x["source_url"],
                      "apply_url": x["apply_url"], "title": x["title"], "first_seen_at": _iso(x["first_seen_at"]),
                      "last_seen_at": _iso(x["last_seen_at"])} for x in postings],
    }
    description = None
    if job["decision"] == "applied" and primary and primary["parsed"]:
        description = z(primary["parsed"].get("description_text") or None)
    conn.execute(update(j).where(j.c.job_id == job_id).values(
        archived_at=now, archive=record, archive_description=description, expires_at=None))
    pids = [x["posting_id"] for x in postings]
    conn.execute(update(p).where(p.c.posting_id.in_(pids)).values(parsed=None, normalized=None, state="archived"))
    conn.execute(T.job_locations.delete().where(T.job_locations.c.job_id == job_id))
    return _drop_raw(conn, pids)
