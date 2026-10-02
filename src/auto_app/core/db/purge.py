"""`auto-app purge` (docs/step1-collect.md §9): first the job TTL (§9.1–9.2, job_ttl.py) deletes or archives
expired jobs; then posting-data retention (§9.3) drops heavy data from live postings — raw data after parse,
old sightings and logs — never their identity or dedup keys."""

import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, update

from auto_app.core.config import JobTTL, RetentionConfig
from auto_app.core.db import tables as T
from auto_app.core.models import utcnow

RAW_BLOBS = ("page_text", "detail_html", "payloads", "embedded", "record", "detail")


@dataclass
class PurgeReport:
    dry_run: bool
    raw_postings: int = 0
    raw_bytes: int = 0
    screenshots: int = 0
    sightings: int = 0
    log_files: int = 0
    failure_dirs: int = 0
    notes: list[str] = field(default_factory=list)
    jobs: object = None  # job_ttl.TTLReport, when the job TTL ran

    def format(self) -> str:
        verb = "would remove" if self.dry_run else "removed"
        return "\n".join([
            f"purge {verb}:" if not self.dry_run else "purge (dry run — nothing changed):",
            *(self.jobs.format() if self.jobs else []),
            f"  raw data        {self.raw_postings} postings ({self.raw_bytes / 1_048_576:.1f} MB compressed), "
            f"{self.screenshots} screenshots",
            f"  sightings       {self.sightings} search results",
            f"  logs/artifacts  {self.log_files} log files, {self.failure_dirs} failure folders",
            *[f"  {n}" for n in self.notes],
            "kept: active jobs and every live posting's identity, parsed fields and dedup keys; archived jobs as records",
        ])


def purge(store, retention: RetentionConfig, *, ttl: JobTTL | None = None, now: datetime | None = None,
          dry_run: bool = False, vacuum: bool = False) -> PurgeReport:
    """ttl: the job TTL (`jobs.ttl_days`); None = skip it (posting-data retention only)."""
    from auto_app.core.db.job_ttl import apply_job_ttl

    now = now or utcnow()
    rep = PurgeReport(dry_run)
    if ttl is not None:
        rep.jobs = apply_job_ttl(store, ttl, now=now, dry_run=dry_run)
    p, r = T.postings, T.posting_raw
    with store.engine.begin() as conn:
        # 1. raw data, some days after a successful parse (failed parses keep their raw data for a fix)
        raw_cut = now - timedelta(days=retention.raw_days)
        size = sum((func.coalesce(func.length(r.c[c]), 0) for c in RAW_BLOBS), start=0)
        rows = conn.execute(
            select(r.c.posting_id, r.c.screenshot_path, size.label("bytes"))
            .join(p, p.c.posting_id == r.c.posting_id)
            .where(p.c.state.in_(["parsed", "normalized", "linked"]), r.c.fetched_at < raw_cut,
                   or_(*(r.c[c].is_not(None) for c in RAW_BLOBS), r.c.screenshot_path.is_not(None)))
        ).all()
        rep.raw_postings = len(rows)
        rep.raw_bytes = sum(b or 0 for _, _, b in rows)
        shots = [Path(s) for _, s, _ in rows if s]
        rep.screenshots = sum(1 for s in shots if s.exists())
        if not dry_run and rows:
            ids = [i for i, _, _ in rows]
            conn.execute(update(r).where(r.c.posting_id.in_(ids)).values(**{c: None for c in RAW_BLOBS},
                                                                          screenshot_path=None))
            for shot in shots:
                shot.unlink(missing_ok=True)

        # 2. card sightings
        sight_cut = now - timedelta(days=retention.sightings_days)
        sr = T.search_results
        rep.sightings = conn.execute(select(func.count()).select_from(sr).where(sr.c.seen_at < sight_cut)).scalar()
        if not dry_run and rep.sightings:
            conn.execute(sr.delete().where(sr.c.seen_at < sight_cut))

    # 3. logs and block artifacts on disk
    age = retention.logs_days * 86400
    old = lambda path: time.time() - path.stat().st_mtime > age  # noqa: E731
    logs = [f for f in store.paths.logs.glob("*.log") if old(f)] if store.paths.logs.exists() else []
    fails = [d for d in store.paths.failures.glob("*") if d.is_dir() and old(d)] if store.paths.failures.exists() else []
    rep.log_files, rep.failure_dirs = len(logs), len(fails)
    if not dry_run:
        for f in logs:
            f.unlink(missing_ok=True)
        for d in fails:
            shutil.rmtree(d, ignore_errors=True)
        if vacuum:
            with store.engine.connect() as conn:
                conn.exec_driver_sql("VACUUM")
            rep.notes.append("database compacted (VACUUM)")
    return rep
