"""Step 2 review (docs/step2-select.md §8): your answers in `auto-app job browse` — one job's details with every
filter's verdict, your decision per job (want_to_apply / not_applied), your feedback per filter (its verdict was
right / wrong — a label for that filter's preference model), reasons, and undo. The app
(process/job_browser.py) only calls these; they're tested without a UI.
"""

from dataclasses import dataclass, field

@dataclass
class Verdict:
    """One filter's current judgment of a job, with your rating of it."""

    filter: str
    version: str
    recommend: bool | None  # None = its answer couldn't be read
    reason: str
    lookups: list[str]
    current: bool  # judged by the filter's current version
    rating: bool | None = None  # your feedback: right (True) / wrong (False) / not rated (None)
    rating_origin: str | None = None


@dataclass
class JobDetail:
    job: dict
    verdicts: list[Verdict]
    description: str
    text_for_labels: str  # title + company + description, stored with your answers


@dataclass
class Undo:
    kind: str  # decision | feedback
    job_id: int
    data: dict = field(default_factory=dict)


class Review:
    """only_filters: show (and rate) only these filters' verdicts."""

    def __init__(self, store, filters: list, ttl=None, only_filters: list[str] | None = None):
        self.store = store
        self.filters = {f.name: f for f in filters}
        self.ttl = ttl
        self.only = set(only_filters or [])
        self.undo_stack: list[Undo] = []

    def marks(self) -> dict[int, dict[str, bool]]:
        """job_id → {filter: right/wrong} — your ratings, for the list's 👍 / 👎."""
        return self.store.feedback_marks()

    # ---- one job ---------------------------------------------------------------------------------

    def detail(self, job_id: int, job: dict | None = None) -> JobDetail:
        """job: its row from a list (saves reloading every job); re-read from the database if not given."""
        job = job or self.store.job(job_id) or {"job_id": job_id}
        parsed, _ = self.store.job_inputs(job_id)
        description = (parsed or {}).get("description_text") or ""
        if not description and job.get("archived_at"):
            description = self.store.archived_description(job_id)  # kept only for jobs you applied to
        latest: dict[str, dict] = {}
        for r in self.store.judgment_rows(job_id=job_id, with_answer=True):  # ordered by time: last wins
            if r["status"] == "error":
                continue
            current = self.filters.get(r["filter"])
            # prefer the current version's judgment; otherwise the latest one
            if r["filter"] not in latest or (current and r["filter_version"] == current.version):
                latest[r["filter"]] = r
        ratings = self.store.feedback_of(job_id)
        verdicts = []
        for name, r in sorted(latest.items()):
            if self.only and name not in self.only:
                continue
            lookups = [f"{b.get('name')}: {_short(b.get('input'))}" for t in r["trace"] for b in t.get("blocks", [])
                       if b.get("type") in ("server_tool_use", "tool_use")]
            fb = ratings.get(name)
            cur = self.filters.get(name)
            verdicts.append(Verdict(name, r["filter_version"], r["recommend"], r["reason"] or "", lookups,
                                    bool(cur and cur.version == r["filter_version"]),
                                    fb["correct"] if fb else None, fb["origin"] if fb else None))
        text = "\n".join(x for x in (job.get("title"), job.get("company"), description) if x)
        return JobDetail(job, verdicts, description, text)

    # ---- answers ---------------------------------------------------------------------------------

    def decide(self, job_id: int, decision: str, reason: str | None = None) -> None:
        """want_to_apply (queued for Step 3) or not_applied. Choosing to apply on a job some filters said no to
        also rates those filters wrong — unless you've rated them yourself."""
        if decision not in ("want_to_apply", "not_applied"):
            raise ValueError(decision)
        job = self.store.job(job_id)
        if not job or job.get("archived_at") or job.get("decision") == "applied":
            raise ValueError(f"job #{job_id} can't be changed here ({job.get('state') if job else 'missing'})")
        d = self.detail(job_id)
        prev = {"step2": job.get("step2"), "decision": job.get("decision")}
        step2 = job.get("step2") if job.get("step2") in ("recommended", "un_recommended") else "recommended"
        self.store.set_job_state(job_id, step2=step2, decision=decision)
        verdicts = [{"filter": v.filter, "version": v.version, "recommend": v.recommend} for v in d.verdicts]
        review_id = self.store.add_review(job_id, decision, reason, verdicts, d.text_for_labels)
        implied = []
        if decision == "want_to_apply":
            for v in d.verdicts:
                if v.recommend is False and v.rating is None:
                    self._rate(job_id, v, False, "implied: you chose to apply", "implied", d.text_for_labels)
                    implied.append(v.filter)
        if self.ttl is not None:
            self.store.refresh_job_dates(self.ttl)
        self.undo_stack.append(Undo("decision", job_id, {"prev": prev, "review_id": review_id, "implied": implied}))

    def rate(self, job_id: int, filter: str, correct: bool, reason: str | None = None) -> None:
        """Your feedback on one filter's verdict: right (True) or wrong (False)."""
        d = self.detail(job_id)
        v = next((v for v in d.verdicts if v.filter == filter), None)
        if v is None:
            raise ValueError(f"{filter} didn't judge job #{job_id}")
        prev = self.store.feedback_of(job_id).get(filter)
        self._rate(job_id, v, correct, reason, "explicit", d.text_for_labels)
        self.undo_stack.append(Undo("feedback", job_id, {"filter": filter, "prev": prev}))

    def _rate(self, job_id, v: Verdict, correct: bool, reason, origin: str, text: str) -> None:
        self.store.set_feedback({"job_id": job_id, "filter": v.filter, "filter_version": v.version,
                                 "verdict": v.recommend, "correct": correct, "origin": origin,
                                 "reason": reason, "job_text": text})

    def add_reason(self, job_id: int, reason: str) -> str | None:
        """Attach a reason to your last answer on this job (its decision or a rating); returns what got it."""
        for u in reversed(self.undo_stack):
            if u.job_id != job_id:
                continue
            if u.kind == "decision":
                self.store.set_review_reason(u.data["review_id"], reason or None)
                return "decision"
            row = self.store.feedback_of(job_id).get(u.data["filter"])
            if row:
                self.store.set_feedback({**row, "reason": reason or None})
                return f"feedback ({u.data['filter']})"
        return None

    def undo(self) -> str | None:
        """Undo the last answer; returns what was undone (None if nothing)."""
        if not self.undo_stack:
            return None
        u = self.undo_stack.pop()
        if u.kind == "decision":
            prev = u.data["prev"]
            self.store.set_job_state(u.job_id, step2=prev["step2"], decision=prev["decision"])
            self.store.delete_review(u.data["review_id"])
            for name in u.data["implied"]:
                self.store.delete_feedback(u.job_id, name)
            if self.ttl is not None:
                self.store.refresh_job_dates(self.ttl)
            return f"decision on #{u.job_id}"
        prev = u.data["prev"]
        if prev:
            self.store.set_feedback(prev)
        else:
            self.store.delete_feedback(u.job_id, u.data["filter"])
        return f"feedback on #{u.job_id} ({u.data['filter']})"


def _short(value, n: int = 90) -> str:
    import json

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + "…"
