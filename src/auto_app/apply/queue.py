"""The application queue and the chain runner (docs/step3-apply.md §3, §5).

- `enqueue` opens an application for every `want_to_apply` job that has none, with its platform's applier chain.
- `advance` works an application through its chain: an applier that can't handle it (or isn't built yet) is
  skipped (`unsupported`); one that fails is retried once, then skipped; a hand-off applier parks it for you
  (`handoff`); a chain that runs out leaves it `failed`.
- An automated applier's prepare answers the form's questions (stored per application); unanswered required ones
  park it in `needs_input`, a complete one in `ready`. Your answers (`answer`, for one job or every job waiting with
  the same question, optionally saved to answers.yaml) move it on; `submit` fills and submits the form (or "fill
  only"); a submit that fails falls back to the next applier.
- Your answers on a hand-off — applied / couldn't / later / drop — and retries come through `Applications`.
On submission the job's decision becomes `applied`; dropping it sets `not_applied`.
"""

import threading
from datetime import datetime

from sqlalchemy import func, insert, select, update

from auto_app.apply.appliers import PLANNED, Applier, Outcome, platform_of
from auto_app.apply.policy import ApplyPolicy
from auto_app.apply.questions import Question, norm
from auto_app.core.db import tables as T
from auto_app.core.models import utcnow

OPEN = ("queued", "handoff", "needs_input", "action_needed", "ready", "submitting")  # still being worked
RETRIES = 1


DONE = ("submitted", "dropped")  # finished applications: history (the Done tab)

class Applications:
    def __init__(self, store, policy: ApplyPolicy, appliers: dict[str, Applier], ttl=None, ctx=None):
        # one browser at a time: the Simplify profile can be open in only one (a start and a fill-only window
        # would collide), so starting and submitting take turns — an RLock, as a failed submit moves on (advance)
        self.browser_lock = threading.RLock()
        self.store = store
        self.policy = policy
        self.appliers = appliers
        self.ttl = ttl
        self.ctx = ctx  # ApplyContext — needed by the automated appliers (without it, they're skipped)

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
                     posted_at=j.get("posted_at"), job_state=j.get("state"), job_decision=j.get("decision"))
        return rows

    def attempts(self, application_id: int) -> list[dict]:
        t = T.attempts
        with self.store.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(t).where(t.c.application_id == application_id)
                                                  .order_by(t.c.attempt_id)).mappings()]

    def queue(self) -> list[dict]:
        """The want_to_apply jobs in the policy's order, each with its platform, chain and latest application (if any —
        a failed one stays until you retry or drop it in `apply inbox`). A job you set back to want to apply after it was
        submitted or dropped is queued again: its finished application stays in Done as history."""
        open_apps = {r["job_id"]: r for r in self.rows() if r["status"] not in DONE}  # by id: the latest wins
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
        with self.browser_lock:
            return self._advance(application_id)

    def _advance(self, application_id: int) -> str:
        app = self.get(application_id)
        job = self.store.job(app["job_id"]) or {}
        app.update(title=job.get("title"), company=job.get("company") or job.get("company_norm"),
                   questions=self.questions(application_id))  # earlier answers: a form continued after you answered
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
            if out is None and applier.handoff is False and self.ctx is None:
                out = Outcome("unsupported", "no profile loaded for the automated appliers")
            tries = 0
            while out is None or out.kind == "failed":
                try:
                    out = applier.prepare(app, self.ctx)
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
            if out.kind == "closed":  # the posting is gone: no point trying other appliers
                self._set(application_id, chain_pos=pos, applier=name, status="failed", note=out.reason)
                return "failed"
            if out.questions is not None:
                self.save_questions(application_id, out.questions)
            extra = {"apply_url": out.apply_url} if out.apply_url else {}
            self._set(application_id, chain_pos=pos, applier=name, status=out.kind, note=out.reason, **extra)
            return out.kind
        self._set(application_id, chain_pos=pos, applier=None, status="failed", note="every applier in the chain was tried")
        return "failed"

    def run(self, job_ids: list[int] | None = None, progress=None) -> dict[str, int]:
        """`apply run` (A1): open applications for queued jobs and move each to its first working applier.
        progress(job_id, status, done, total): before each job (status None) and after it (its new status)."""
        self.enqueue(job_ids)
        todo = [app for app in self.rows(("queued",)) if not job_ids or app["job_id"] in job_ids]
        counts: dict[str, int] = {}
        for i, app in enumerate(todo):
            if app["chain_pos"] == 0:  # starting from the top (new, or sent back with r): today's policy's chain
                self._set(app["application_id"], chain=self.policy.chain(app.get("platform") or platform_of(app.get("apply_url"))))
            if progress:
                progress(app["job_id"], None, i, len(todo))
            status = self.advance(app["application_id"])
            counts[status] = counts.get(status, 0) + 1
            if progress:
                progress(app["job_id"], status, i + 1, len(todo))
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

    def applied_by_hand(self, job_id: int) -> int:
        """You applied yourself (manual applying): the application is recorded (manual) and the job marked applied."""
        app = next((j["application"] for j in self.queue() if j["job_id"] == job_id and j["application"]), None)
        if app is None:
            [new] = self.enqueue([job_id]) or [None]
            app = self.get(new) if new else None
        if app is None:
            raise ValueError(f"job {job_id} isn't queued")
        self._set(app["application_id"], applier="manual", chain=["manual"], chain_pos=0)
        self.applied(app["application_id"])
        return app["application_id"]

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

    def requeue(self, application_id: int) -> None:
        """Back to the queue (Queued tab): the chain starts over from its first applier at the next start — the chain
        the policy has now (an older application keeps the one it was created with otherwise, e.g. from before
        simplify_auto); the attempts so far stay."""
        app = self.get(application_id)
        chain = self.policy.chain(app.get("platform") or platform_of(app.get("apply_url")))
        self._set(application_id, chain=chain, chain_pos=0, status="queued", applier=None)

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

    # ---- questions (automated appliers) -------------------------------------------------------------

    def questions(self, application_id: int) -> list[Question]:
        t = T.questions
        with self.store.engine.connect() as conn:
            rows = conn.execute(select(t).where(t.c.application_id == application_id).order_by(t.c.ord)).mappings().all()
        return [Question(id=r["question_id"], label=r["label"] or "", type=r["type"] or "text", required=bool(r["required"]),
                         options=r["options"] or [], kind=r["kind"] or "known", answer=r["answer"], source=r["source"],
                         confidence=r["confidence"], seen=bool(r["seen"]), note=r["note"] or "") for r in rows]

    def save_questions(self, application_id: int, questions: list[Question]) -> None:
        t = T.questions
        unique: dict[str, Question] = {}
        for q in questions:  # one row per field id (the first wins)
            unique.setdefault(q.id, q)
        questions = list(unique.values())
        with self.store.engine.begin() as conn:
            conn.execute(t.delete().where(t.c.application_id == application_id))
            if questions:
                conn.execute(insert(t), [{"application_id": application_id, "question_id": q.id, "ord": i, "label": q.label,
                                          "type": q.type, "kind": q.kind, "required": q.required, "options": q.options,
                                          "answer": q.answer, "source": q.source, "confidence": q.confidence,
                                          "seen": q.seen, "note": q.note} for i, q in enumerate(questions)])

    def resume(self, application_id: int) -> str:
        """Re-check an application's questions after you answered: needs_input → ready when nothing required is open."""
        app = self.get(application_id)
        if app["status"] not in ("needs_input", "ready"):
            return app["status"]
        open_ = [q for q in self.questions(application_id) if q.open]
        status = "needs_input" if open_ else "ready"
        self._set(application_id, status=status,
                  note=f"{len(open_)} question(s) need you" if open_ else "every question answered")
        return status

    def answer(self, application_id: int, question_id: str, value, everywhere: bool = False,
               save: bool = False) -> list[int]:
        """Your answer to one question — for this application, or every application waiting with the same question;
        optionally saved to answers.yaml (so it's answered by itself next time). Returns the applications changed."""
        target = next((q for q in self.questions(application_id) if q.id == question_id), None)
        if target is None:
            raise ValueError(f"no question {question_id!r} on application {application_id}")
        changed = []
        apps = [self.get(application_id)] + ([a for a in self.rows(("needs_input", "ready"))
                                              if a["application_id"] != application_id] if everywhere else [])
        for app in apps:
            qs = self.questions(app["application_id"])
            hit = False
            for q in qs:
                if q.id == question_id if app["application_id"] == application_id else norm(q.label) == norm(target.label):
                    answer = value
                    if q.options:
                        from auto_app.apply.questions import to_option

                        if isinstance(value, list):  # a multi-select: every ticked option
                            answer = [to_option(v, q.options) for v in value]
                            if None in answer:
                                continue
                        else:
                            answer = to_option(value, q.options)
                        if answer is None:
                            continue  # that application's options don't include this answer
                    q.answer, q.source, q.confidence, q.seen, q.note = answer, "you", 1.0, True, ""
                    hit = True
            if hit:
                self.save_questions(app["application_id"], qs)
                self.resume(app["application_id"])
                changed.append(app["application_id"])
        if save and self.ctx and self.ctx.answers_file:
            from auto_app.apply.profile import save_answer

            save_answer(self.ctx.answers_file, None, target.label, value,
                        kind="free_text" if target.type == "textarea" else "fact")
        return changed

    def mark_seen(self, application_id: int) -> None:
        """You've seen the application in review: its drafts are no longer unseen."""
        qs = self.questions(application_id)
        if any(q.source == "draft" and not q.seen for q in qs):
            for q in qs:
                q.seen = True
            self.save_questions(application_id, qs)

    def submit(self, application_id: int, fill_only: bool = False) -> str:
        """Fill and submit the form (or fill only, for you to submit); returns the new status. Waits while another
        job uses the browser."""
        with self.browser_lock:
            return self._submit(application_id, fill_only)

    def _submit(self, application_id: int, fill_only: bool = False) -> str:
        app = self.get(application_id)
        applier = self.appliers.get(app.get("applier") or "")
        if applier is None or not hasattr(applier, "submit"):
            raise ValueError("this application has no automated applier")
        qs = self.questions(application_id)
        if any(q.open for q in qs):
            raise ValueError("some required questions are still unanswered")
        out = applier.submit(app, qs, self.ctx, fill_only=fill_only)
        self._attempt(application_id, applier.name, "submit", out)
        if out.kind == "submitted":
            self._set(application_id, status="submitted", submitted_at=utcnow(), note=out.reason)
            self._job(app["job_id"], "applied")
            return "submitted"
        if out.kind == "needs_input":  # a step asked something new on the way (a multi-step form)
            if out.questions is not None:
                self.save_questions(application_id, out.questions)
            self._set(application_id, status="needs_input", note=out.reason)
            return "needs_input"
        if out.kind in ("filled", "action_needed"):
            self._set(application_id, status="ready" if out.kind == "filled" else "action_needed", note=out.reason)
            return "ready" if out.kind == "filled" else "action_needed"
        if fill_only:  # you're in the middle of it: stays ready (the reason in its note), no other applier
            self._set(application_id, status="ready", note=f"fill only stopped: {out.reason}"[:300])
            return f"fill only stopped — {out.reason}"[:200]
        # failed / unsupported: the next applier in the chain (e.g. Simplify) takes over
        self._set(application_id, chain_pos=app["chain_pos"] + 1)
        return self.advance(application_id)

    def submitted_since(self, since: datetime) -> int:
        a = T.applications
        with self.store.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(a).where(a.c.status == "submitted",
                                                                          a.c.submitted_at >= since)).scalar() or 0
