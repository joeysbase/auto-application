"""`auto-app web` (docs/web-frontend.md): FastAPI on 127.0.0.1 — the JSON API under /api, the React app (web/dist)
everywhere else.

The data side is the TUI's: one `JobBrowser` per server (tabs, narrowing, a job's detail with every verdict; in W2
decisions, labels and undo). The database is shared with `serve`, the TUI and Claude Code, so every request reads it
fresh. Only this Mac: the `Host` of every request, and the `Origin` of every write, must be this server.
"""

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from auto_app.process.job_browser import MARKABLE, TAB_TITLES, TABS, JobBrowser
from auto_app.process.jobs_view import STATE_LABEL, expiry, place
from auto_app.select import audit
from auto_app.select.job_details import _salary
from auto_app.select.parse import verdict_text

WEB_TABS = (*TABS, "archived")
ROW_FIELDS = ("job_id", "title", "company", "locations", "sources", "postings", "posted_at", "first_seen_at", "state",
              "step2", "decision", "decided_at", "expires_at", "archived_at", "notified_at")
EXTRAS = ("workplace_type", "employment_type", "salary", "seniority")


def _aware(v: str | None) -> datetime | None:
    if not v:
        return None
    d = datetime.fromisoformat(v)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def row_json(j: dict, marks: dict | None = None) -> dict:
    out = {k: j.get(k) for k in ROW_FIELDS}
    out["company"] = j.get("company") or j.get("company_norm")
    out["locations"] = list(dict.fromkeys(place(x) for x in j.get("locations") or []))  # "austin, tx" → "Austin, TX"
    out["state_label"] = STATE_LABEL.get(j.get("state"), j.get("state"))
    out["expires"] = expiry(j)
    out["warnings"] = len(j.get("warnings") or [])
    out["labels"] = marks or {}  # filter → should recommend (True) / shouldn't (False)
    return out


class Decide(BaseModel):
    decision: str  # want_to_apply | not_applied
    reason: str | None = None


class Label(BaseModel):
    should: bool  # should recommend (True) / shouldn't (False)
    filter: str | None = None  # None: every filter that judged the job ("no filter should" / "every filter should")
    reason: str | None = None


class Mark(BaseModel):
    state: str


class Reason(BaseModel):
    reason: str


def create_app(browser: JobBrowser, dist: Path | None = None, port: int = 8765, db_name: str = "",
               allowed_hosts: set[str] | None = None) -> FastAPI:
    """browser: the TUI's data side over the database; dist: the built React app (None: API only)."""
    store = browser.store
    hosts = allowed_hosts or {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = FastAPI(title="auto-app", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def only_this_mac(request: Request, call_next):
        if request.headers.get("host") not in hosts:  # DNS rebinding: a page on another name reaching 127.0.0.1
            return JSONResponse({"detail": "wrong host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin and origin.split("://", 1)[-1] not in hosts:
            return JSONResponse({"detail": "wrong origin"}, status_code=403)  # another site posting to this one
        return await call_next(request)

    # ---- the API ----------------------------------------------------------------------------------

    lock = threading.Lock()  # one answer at a time: the undo stack and the database stay in step

    def answered(job_id: int, message: str) -> dict:
        return {"message": message, "job": job(job_id) if store.job(job_id) else None, "can_undo": bool(browser.undo_stack)}

    def act(fn):
        with lock:
            try:
                return fn()
            except ValueError as e:
                raise HTTPException(400, str(e))

    @app.get("/api/meta")
    def meta():
        counts = browser.counts()
        jobs = store.jobs(archived=None)
        return {
            "db": db_name,
            "states": [{"id": s, "title": STATE_LABEL[s]} for s in MARKABLE],
            "can_undo": bool(browser.undo_stack),
            "tabs": [{"id": t, "title": TAB_TITLES[t], "count": counts.get(t, 0)} for t in WEB_TABS],
            "sources": sorted({s for j in jobs for s in j.get("sources") or []}),
            "filters": [{"name": f.name, "version": f.version} for f in browser.filters.values()]
                       or [{"name": n, "version": None} for n in browser.audit_filters()],
        }

    @app.get("/api/jobs")
    def jobs(tab: str = "recommended", q: str = "", source: str = "", company: str = "", filter: str = "",
             verdict: str = "", posted_within: int | None = None, keep: str = ""):
        """keep: job ids answered in this tab during this visit — they stay in it (as in the TUI) until you leave."""
        if tab not in WEB_TABS:
            raise HTTPException(400, f"unknown tab {tab!r}: one of {', '.join(WEB_TABS)}")
        rows = browser.jobs(tab, q, keep={int(x) for x in keep.split(",") if x.strip().isdigit()})
        if source:
            rows = [j for j in rows if source in (j.get("sources") or [])]
        if company:
            c = company.lower()
            rows = [j for j in rows if c in (j.get("company") or j.get("company_norm") or "").lower()]
        if filter:
            said = {jid: rec for (jid, _), rec in audit.latest_verdicts(store, filter).items()}
            want = {"yes": True, "no": False, "unreadable": None}.get(verdict)
            rows = [j for j in rows if j["job_id"] in said and (not verdict or said[j["job_id"]] is want)]
        if posted_within:
            cut = datetime.now(timezone.utc) - timedelta(days=posted_within)
            rows = [j for j in rows if (d := _aware(j.get("posted_at") or j.get("first_seen_at"))) and d >= cut]
        marks = browser.marks()
        return [row_json(j, marks.get(j["job_id"])) for j in rows]

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int):
        j = store.job(job_id)
        if not j:
            raise HTTPException(404, f"no job #{job_id}")
        d = browser.detail(job_id, j)
        parsed, _ = store.job_inputs(job_id)
        out = row_json(j, browser.marks().get(job_id))
        out.update({k: (parsed or {}).get(k) for k in EXTRAS})
        out["salary"] = _salary(out["salary"]) if isinstance(out["salary"], dict) else out["salary"]  # as filters see it
        out["apply_url"] = j.get("canonical_url")
        out["warnings"] = [w.get("text") for w in j.get("warnings") or []]
        out["postings"] = [{k: p.get(k) for k in ("source", "source_job_id", "url", "title", "company", "posted_at",
                                                   "last_seen_at")} for p in store.job_postings(job_id)]
        out["verdicts"] = [{"filter": v.filter, "version": v.version, "current": v.current, "recommend": v.recommend,
                            "fit": v.fit, "dealbreaker": v.dealbreaker, "says": verdict_text(v.recommend, v.fit, v.dealbreaker),
                            "reason": v.reason, "lookups": v.lookups,
                            "label": v.label, "label_origin": v.label_origin, "right": v.right} for v in d.verdicts]
        out["description"] = d.description
        return out

    # ---- answers (W2): through Review / JobBrowser, as in the TUI --------------------------------------

    @app.post("/api/jobs/{job_id}/decide")
    def decide(job_id: int, body: Decide):
        def go():
            browser.decide(job_id, body.decision, body.reason or None)
            what = "want to apply — queued" if body.decision == "want_to_apply" else "not applying"
            return answered(job_id, f"#{job_id}: {what}" + (f" ({body.reason})" if body.reason else ""))
        return act(go)

    @app.post("/api/jobs/{job_id}/label")
    def label(job_id: int, body: Label):
        def go():
            should = "should" if body.should else "shouldn't"
            if body.filter:
                browser.label(job_id, body.filter, body.should, body.reason or None)
                return answered(job_id, f"#{job_id}: {body.filter} {should} recommend it")
            names = browser.label_all(job_id, body.should)
            if not names:
                raise ValueError(f"no filter judged job #{job_id}")
            if body.reason:
                browser.add_reason(job_id, body.reason)
            who = f"{', '.join(names)} should" if body.should else "no filter should"
            return answered(job_id, f"#{job_id}: {who} recommend it")
        return act(go)

    @app.post("/api/jobs/{job_id}/mark")
    def mark(job_id: int, body: Mark):
        def go():
            if body.state not in MARKABLE:
                raise ValueError(f"unknown state {body.state!r}: one of {', '.join(MARKABLE)}")
            browser.mark(job_id, body.state)
            return answered(job_id, f"#{job_id}: now {STATE_LABEL.get(body.state, body.state)}")
        return act(go)

    @app.post("/api/jobs/{job_id}/reason")
    def reason(job_id: int, body: Reason):
        def go():
            got = browser.add_reason(job_id, body.reason.strip())
            if not got:
                raise ValueError(f"nothing to attach a reason to: decide on or label #{job_id} first")
            return answered(job_id, f"#{job_id}: reason added to your {got}")
        return act(go)

    @app.post("/api/undo")
    def undo():
        def go():
            if not browser.undo_stack:
                raise ValueError("nothing to undo")
            job_id = browser.undo_stack[-1].job_id
            what = browser.undo()
            return {**answered(job_id, f"undone: {what}"), "job_id": job_id}
        return act(go)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    def unknown(rest: str):
        raise HTTPException(404, f"no API /api/{rest}")

    # ---- the app ------------------------------------------------------------------------------------

    @app.get("/{path:path}")
    def page(path: str):
        if dist is None or not (dist / "index.html").is_file():
            return JSONResponse({"detail": "the web app isn't built: run `auto-app web --build`"}, status_code=503)
        f = (dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(dist.resolve()):
            return FileResponse(f)
        return FileResponse(dist / "index.html")  # the app's own routes

    return app
