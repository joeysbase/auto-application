"""The Greenhouse applier (docs/step3-apply.md §3, A3).

prepare (starting a job): the job's application questions come from Greenhouse's public job board API
  (`boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true`) and are answered from your profile and
  answers.yaml (and Claude). Then a dry run: the real form is opened headless, filled with those answers and read back
  (form.py) — a required field the API didn't list, or an answer that didn't take, becomes a question for you. Nothing
  is submitted. What's left goes to you (needs input); otherwise the job is ready to submit.
submit:  the hosted form (`job-boards.greenhouse.io/<board>/jobs/<id>`) is filled in a browser — its field ids are
  the API's field names — your CV uploaded, and submitted (or, "fill only", left for you to check and submit).
"""

import re
from urllib.parse import urlparse

import httpx

from auto_app.apply.appliers import Applier, Outcome
from auto_app.apply.questions import Question, resolve

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"
TYPES = {"input_text": "text", "textarea": "textarea", "input_file": "file", "multi_value_single_select": "select",
         "multi_value_multi_select": "multiselect"}  # input_hidden: skipped


def parse_job_url(url: str | None) -> tuple[str, str] | None:
    """(board, job id) from a Greenhouse job-board link."""
    u = urlparse(url or "")
    if "greenhouse.io" not in (u.hostname or ""):
        return None
    m = re.match(r"^/(?:embed/job_app\?for=)?([^/?#]+)/jobs/(\d+)", u.path)
    return (m.group(1), m.group(2)) if m else None


def questions_from_api(data: dict) -> list[Question]:
    """The API's questions (+ EEOC compliance questions) as Questions — one per form field."""
    out: list[Question] = []
    seen: set[str] = set()

    def add(label: str, required: bool, fields: list[dict]) -> None:
        first = True
        for f in fields:
            kind = TYPES.get(f.get("type"))
            if kind is None or f.get("name") in seen:
                continue
            seen.add(f["name"])
            out.append(Question(id=f["name"], label=(label or "").strip(), type=kind, required=bool(required) and first,
                                options=[{"label": str(v.get("label")), "value": v.get("value")}
                                         for v in f.get("values") or []]))
            first = False  # "Resume/CV": the file is required, the "enter manually" text box is the alternative

    for q in data.get("questions") or []:
        add(q.get("label"), q.get("required"), q.get("fields") or [])
    for q in data.get("location_questions") or []:
        add(q.get("label"), q.get("required"), q.get("fields") or [])
    for group in data.get("compliance") or []:
        for q in group.get("questions") or []:
            add(q.get("label"), q.get("required"), q.get("fields") or [])
    if "phone" in seen and "country" not in seen:  # the hosted form asks the phone's country; the API doesn't list it
        out.insert(next(i for i, q in enumerate(out) if q.id == "phone"),
                   Question(id="country", label="Country", type="select", required=True))
    return out


class GreenhouseApplier(Applier):
    name = "greenhouse"

    def __init__(self, http: httpx.Client | None = None, filler=None, dry_runner=None):
        self.http = http
        self.filler = filler  # (url, questions, cv, submit, headless, profile_dir) -> Outcome; default: the browser filler
        self.dry_runner = dry_runner  # (url, questions, cv) -> (fields, problem); default: fill.dry_run (headless Chrome)

    def can_apply(self, app: dict) -> Outcome | None:
        if app.get("platform") != "greenhouse" or not parse_job_url(app.get("apply_url")):
            return Outcome("unsupported", "not a Greenhouse job-board link")
        return None

    def prepare(self, app: dict, ctx=None) -> Outcome:
        board, job_id = parse_job_url(app["apply_url"])
        http = self.http or httpx.Client(timeout=30, follow_redirects=True)
        try:
            r = http.get(API.format(board=board, job=job_id))
        finally:
            if self.http is None:
                http.close()
        if r.status_code == 404:
            return Outcome("closed", "the posting is closed (Greenhouse: 404)")
        if r.status_code >= 400:
            return Outcome("failed", f"Greenhouse API HTTP {r.status_code}")
        data = r.json()
        job = {"title": data.get("title") or app.get("title"), "company": data.get("company_name") or app.get("company"),
               "description": re.sub(r"<[^>]+>", " ", data.get("content") or "")}
        res = resolve(questions_from_api(data), ctx.me, job, ctx.matcher, ctx.threshold)
        url = data.get("absolute_url") or app["apply_url"]
        questions = res.questions
        if getattr(ctx, "dry_run", True):  # fill the real form headless and read it back (nothing submitted)
            from auto_app.apply.fill import ALIASES, dry_run
            from auto_app.apply.form import reconcile

            fields, problem = (self.dry_runner or dry_run)(url, questions, ctx.me.cv)
            if problem is not None and problem.kind == "unsupported":
                return problem  # not a form we can fill: the next applier
            if fields:
                questions = reconcile(questions, fields, aliases=ALIASES, filled_by="form")
        open_ = [q for q in questions if q.open]
        out = Outcome("needs_input" if open_ else "ready",
                      f"{len(open_)} question(s) need you" if open_ else "every question answered (form checked)")
        out.questions = questions
        out.apply_url = url
        return out

    def submit(self, app: dict, questions: list[Question], ctx, fill_only: bool = False) -> Outcome:
        from auto_app.apply.fill import fill_form

        filler = self.filler or fill_form
        return filler(app["apply_url"], questions, ctx.me.cv, submit=not fill_only, headless=ctx.headless and not fill_only,
                      profile_dir=ctx.browser_profile)
