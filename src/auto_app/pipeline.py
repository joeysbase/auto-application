"""`auto-app run`: collect → parse → normalize → dedup (→ purge), one command (docs/step1-collect.md §10).

A lock file (data/run.lock) keeps a scheduled run and a manual run from overlapping."""

import fcntl
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from auto_app.collect.plan import Selection
from auto_app.collect.runner import CollectionRunner, CollectOptions
from auto_app.core.config import AppConfig
from auto_app.core.db.purge import PurgeReport, purge
from auto_app.process.link import LinkPlan, link_jobs
from auto_app.process.normalize import normalize_postings
from auto_app.process.parse import parse_postings


class RunLocked(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip() or "another run"
        f.close()
        raise RunLocked(f"another `auto-app run` is in progress ({holder})") from None
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()}")
    f.flush()
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


@dataclass
class PipelineResult:
    batch: dict | None = None
    parse: dict = field(default_factory=dict)
    normalize: dict = field(default_factory=dict)
    plan: LinkPlan | None = None
    created: dict = field(default_factory=dict)
    purge: PurgeReport | None = None

    @property
    def new_jobs(self) -> int:
        return len(self.created)

    @property
    def problems(self) -> list[str]:
        out = []
        for name, s in ((self.batch or {}).get("sources") or {}).items():
            if s.get("status") not in ("ok", "interrupted"):
                out.append(f"{name}: {s.get('status')}{' — ' + s['error'] if s.get('error') else ''}")
        for name, st in self.parse.items():
            if st.failed:
                out.append(f"{name}: {st.failed} postings failed to parse")
        return out


async def run_pipeline(cfg: AppConfig, paths, store, opts: CollectOptions, sel: Selection, registry, *,
                       collect: bool = True, do_purge: bool = False) -> PipelineResult:
    res = PipelineResult()
    if collect:
        res.batch = await CollectionRunner(cfg, paths, store, opts, registry).run(sel)
    res.parse = parse_postings(store, cfg, registry)  # every source: only new or changed postings are parsed
    res.normalize = normalize_postings(store, registry)
    db = hasattr(store, "create_job")
    res.plan, res.created = link_jobs(store, registry, dry_run=not db, ttl=cfg.jobs.ttl_days)  # file mode: preview only (jobs need the DB)
    if do_purge and db:
        res.purge = purge(store, cfg.retention, ttl=cfg.jobs.ttl_days)
    return res


def format_pipeline(res: PipelineResult, store) -> str:
    from auto_app.collect.report import format_batch

    out = []
    if res.batch:
        out += ["== collect", format_batch(store, res.batch["batch_id"]), ""]
    parsed = sum(st.parsed for st in res.parse.values())
    failed = sum(st.failed for st in res.parse.values())
    out.append(f"== parse      {parsed} parsed, {failed} failed")
    normalized = sum(st.normalized for st in res.normalize.values())
    out.append(f"== normalize  {normalized} normalized")
    if res.plan:
        p = res.plan
        joined = sum(len(v) for v in p.joined.values())
        out.append(f"== dedup      {p.new_postings} new postings → {len(res.created)} new jobs, {joined} joined existing jobs "
                   f"({p.duplicates} same-source duplicates), {len(p.near_misses)} near misses")
    if hasattr(store, "jobs"):
        out.append(f"   jobs now   {len(store.jobs())}  ·  `auto-app job list`")
    else:
        out.append("   (debug files only: dedup was a preview — jobs and purge need the database; "
                   "load these files with `auto-app data import`)")
    if res.purge:
        out += ["", "== " + res.purge.format()]
    if res.problems:
        out += ["", "problems:", *[f"  ! {x}" for x in res.problems]]
    return "\n".join(out)
