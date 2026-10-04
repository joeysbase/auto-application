"""The application queue and the chain runner (docs/step3-apply.md §3, §5).

- `enqueue` opens an application for every `want_to_apply` job that has none, with its platform's applier chain.
- `advance` works an application through its chain: an applier that can't handle it (or isn't built yet) is
  skipped (`unsupported`); one that fails is retried once, then skipped; a hand-off applier parks it for you
  (`handoff`); a chain that runs out leaves it `failed`.
- Your answers on a hand-off — applied / couldn't / later / drop — and retries come through `Applications`.
On submission the job's decision becomes `applied`; dropping it sets `not_applied`.
"""

from datetime import datetime

from sqlalchemy import func, insert, select, update

from auto_app.apply.appliers import PLANNED, Applier, Outcome, platform_of
from auto_app.apply.policy import ApplyPolicy
from auto_app.core.db import tables as T
from auto_app.core.models import utcnow

OPEN = ("queued", "handoff", "needs_input", "action_needed", "ready", "submitting")  # still being worked
RETRIES = 1


class Applications:
    def __init__(self, store, policy: ApplyPolicy, appliers: dict[str, Applier], ttl=None):
        self.store = store
        self.policy = policy
        self.appliers = appliers
        self.ttl = ttl

    # ---- reading ----------------------------------------------------------------------------------

    def get(self, application_id: int) -> dict | None:
        with self.store.engine.connect() as conn:
            r = conn.execute(select(T.applications).where(T.applications.c.application_id == application_id)).mappings().first()
        return dict(r) if r else None

    def rows(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        a = T.applications
        q = select(a).order_by(a.c.application_id)
        if statuses:
            q = q.where(a.c.status.in_(statuses))
        with self.store.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(q).mappings()]
        jobs = {j["job_id"]: j for j in self.store.jobs(archived=None)}
        for r in rows:
            j = jobs.get(r["job_id"]) or {}
            r.update(title=j.get("title"), company=j.get("company") or j.get("company_norm"),
                     posted_at=j.get("posted_at"), job_state=j.get("state"))
        return rows

    def attempts(self, application_id: int) -> list[dict]:
        t = T.attempts
        with self.store.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(t).where(t.c.application_id == application_id)
                                                  .order_by(t.c.attempt_id)).mappings()]

    def queue(self) -> list[dict]:
        """The want_to_apply jobs in the policy's order, each with its platform, chain and latest application (if any —
        a failed one stays until you retry or drop it in `apply inbox`)."""
        open_apps = {r["job_id"]: r for r in self.rows()}  # ordered by id: the latest wins
        jobs = [j for j in self.store.jobs() if j.get("decision") == "want_to_apply"]
        if self.policy.order == "newest_posting":
            jobs.sort(key=lambda j: (j.get("posted_at") or j.get("first_seen_at") or ""), reverse=True)
        else:  # queued: in the order you chose to apply (by_filter: same, until filters rank jobs)
            jobs.sort(key=lambda j: (j.get("decided_at") or "", j["job_id"]))
        out = []
        for j in jobs:
            platform = platform_of(j.get("canonical_url"))
            out.append({**j, "platform": platform, "chain": self.policy.chain(platform), "application": open_apps.get(j["job_id"])})
        return out

    # ---- writing ----------------------------------------------------------------------------------

    def _set(self, application_id: int, **values) -> None:
        with self.store.engine.begin() as conn:
            conn.execute(update(T.applications).where(T.applications.c.application_id == application_id)
                         .values(updated_at=utcnow(), **values))

    def _attempt(self, application_id: int, applier: str, pass_: str, outcome: Outcome) -> None:
        now = utcnow()
        with self.store.engine.begin() as conn:
            conn.execute(insert(T.attempts).values(application_id=application_id, applier=applier, **{"pass": pass_},
                                                   outcome=outcome.kind, reason=outcome.reason, started_at=now, ended_at=now))

    def enqueue(self, job_ids: list[int] | None = None) -> list[int]:
        """Open an application for each queued job without one (only these jobs, if given). Returns the new ids."""
        new = []
        for j in self.queue():
            if j["application"] or (job_ids and j["job_id"] not in job_ids):
                continue
            now = utcnow()
            with self.store.engine.begin() as conn:
                new.append(conn.execute(insert(T.applications).values(
                    job_id=j["job_id"], platform=j["platform"], apply_url=j.get("canonical_url"), status="queued",
                    chain=j["chain"], chain_pos=0, created_at=now, updated_at=now)).inserted_primary_key[0])
        return new

    def advance(self, application_id: int) -> str:
        """Work the application through its chain from the current applier; returns its new status."""
        app = self.get(application_id)
        pos = app["chain_pos"]
        while pos < len(app["chain"]):
            name = app["chain"][pos]
            applier = self.appliers.get(name)
            if applier is None:
                why = f"{name} applier not built yet ({PLANNED[name]})" if name in PLANNED else f"unknown applier {name!r}"
                self._attempt(application_id, name, "prepare", Outcome("unsupported", why))
                pos += 1
                continue
            out = applier.can_apply(app)
            tries = 0
            while out is None or out.kind == "failed":
                try:
                    out = applier.prepare(app)
                except Exception as e:  # an applier's crash is a failure, never the runner's
                    out = Outcome("failed", f"{type(e).__name__}: {e}"[:300])
                if out.kind != "failed" or tries >= RETRIES:
                    break
                self._attempt(application_id, name, "prepare", out)
                tries += 1
            self._attempt(application_id, name, "handoff" if out.kind == "handoff" else "prepare", out)
            if out.kind in ("unsupported", "failed"):
                pos += 1
                continue
            status = {"handoff": "handoff"}.get(out.kind, out.kind)
            self._set(application_id, chain_pos=pos, applier=name, status=status, note=out.reason)
            return status
        self._set(application_id, chain_pos=pos, applier=None, status="failed", note="every applier in the chain was tried")
        return "failed"

    def run(self, job_ids: list[int] | None = None) -> dict[str, int]:
        """`apply run` (A1): open applications for queued jobs and move each to its first working applier."""
        self.enqueue(job_ids)
        counts: dict[str, int] = {}
        for app in self.rows(("queued",)):
            if job_ids and app["job_id"] not in job_ids:
                continue
            status = self.advance(app["application_id"])
            counts[status] = counts.get(status, 0) + 1
        return counts

    # ---- your answers (apply inbox) -----------------------------------------------------------------

    def open(self, application_id: int, cv=None) -> str:
        app = self.get(application_id)
        applier = self.appliers.get(app.get("applier") or "")
        if not applier or not applier.handoff:
            raise ValueError("not a hand-off")
        msg = applier.open(app, cv)
        self._attempt(application_id, applier.name, "handoff", Outcome("handoff", "opened"))
        return msg

    def applied(self, application_id: int) -> None:
        app = self.get(application_id)
        self._set(application_id, status="submitted", submitted_at=utcnow())
        self._attempt(application_id, app.get("applier") or "?", "handoff", Outcome("submitted", "you applied"))
        self._job(app["job_id"], "applied")

    def couldnt(self, application_id: int, reason: str = "") -> str:
        """The current applier didn't work for you → the next one in the chain."""
        app = self.get(application_id)
        self._attempt(application_id, app.get("applier") or "?", "handoff",
                      Outcome("failed", reason or "you couldn't apply this way"))
        self._set(application_id, chain_pos=app["chain_pos"] + 1)
        return self.advance(application_id)

    def later(self, application_id: int) -> None:
        self._set(application_id)  # to the back of the line (ordered by updated_at)

    def drop(self, application_id: int) -> None:
        app = self.get(application_id)
        self._set(application_id, status="dropped")
        self._job(app["job_id"], "not_applied")

    def retry(self, application_id: int, applier: str | None = None) -> str:
        """Start the chain over (or jump straight to one applier, e.g. manual)."""
        chain = list(self.get(application_id)["chain"])
        if applier and applier not in chain:
            chain.append(applier)
        pos = chain.index(applier) if applier else 0
        self._set(application_id, chain=chain, chain_pos=pos, status="queued")
        return self.advance(application_id)

    def _job(self, job_id: int, decision: str) -> None:
        job = self.store.job(job_id)
        if job and not job.get("archived_at"):
            self.store.mark_job(job_id, decision)
            if self.ttl is not None:
                self.store.refresh_job_dates(self.ttl)

    def submitted_since(self, since: datetime) -> int:
        a = T.applications
        with self.store.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(a).where(a.c.status == "submitted",
                                                                          a.c.submitted_at >= since)).scalar() or 0
