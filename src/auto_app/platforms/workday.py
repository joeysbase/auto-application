"""Workday platform base — the JSON search API career sites use (companies: e.g. NVIDIA, Adobe in plugins/companies/).

`board` (set by each company plugin) is the Workday careers URL, e.g.
    https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
Search is server-side (keywords, location and job-type facets); the list lacks descriptions,
so each new job's detail is fetched (counted against the company's hourly/daily limits).
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from auto_app.sources.api import US_ALIASES, ApiSource, RawJob, location_match, posted_within
from auto_app.sources.common.urls import workday_job_id
from auto_app.sources.filters import Local, Native
from auto_app.core.models import CommonPosting
from auto_app.sources.common.text import employment_of, html_to_text, salary_from_description, workplace_of
from auto_app.sources.parsing import ParseError

PAGE_SIZE = 20
MAX_RESULTS = 1000  # per keyword; Workday can't sort by date, so every page is read
LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
EMPLOYMENT = {"full_time": "full time", "part_time": "part time"}


class WorkdayFilters(BaseModel):
    """Any Workday facet by its parameter or label, with the values as shown on the site.
    e.g. {"Job Type": ["New College Graduate"], "Job Category": ["Engineering"]}"""

    model_config = ConfigDict(extra="forbid")
    facets: dict[str, list[str]] = {}


def parse_board(board: str) -> tuple[str, str, str]:
    """→ (host, tenant, site) from a careers URL."""
    u = urlparse(board if "://" in board else "https://" + board)
    host = u.hostname or ""
    if not host.endswith("myworkdayjobs.com"):
        raise ValueError(f"not a Workday careers URL: {board}")
    parts = [p for p in u.path.split("/") if p and not LOCALE.match(p)]
    if not parts:
        raise ValueError(f"Workday URL has no site name: {board} (expected https://<tenant>.wdN.myworkdayjobs.com/<site>)")
    return host, host.split(".")[0], parts[0]


def posted_from_text(text: str | None, now: datetime | None = None) -> datetime | None:
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    t = text.lower()
    if "today" in t or "just posted" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?", t)
    if m:
        return now - timedelta(days=int(m.group(1)) + (1 if "+" in t else 0))
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _same_place(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or (a in US_ALIASES and b in US_ALIASES)


def _facet_groups(facets: list[dict]):
    """Flatten nested facets → (parameter, label, values)."""
    for f in facets:
        vals = f.get("values") or []
        if vals and "facetParameter" in vals[0]:
            yield from _facet_groups(vals)
        else:
            yield f.get("facetParameter"), f.get("descriptor") or "", vals


class WorkdaySource(ApiSource):
    platform = "workday"
    needs_detail = True
    board_help = "the company's Workday careers URL, e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    SiteFilters = WorkdayFilters
    filters = {
        "keywords": Native("searchText"),
        "locations": Native("location facets", note="falls back to matching the location text"),
        "posted_within_days": Local(("postedOn",)),
        "employment_types": Native("timeType facet"),
    }

    def __init__(self):
        self._unresolved_locations: dict[str, list[str]] = {}

    def validate_board(self, board):
        board = super().validate_board(board)
        host, _, site = parse_board(board)
        return f"https://{host}/{site}"

    def _api(self, board: str) -> str:
        host, tenant, site = parse_board(board)
        return f"https://{host}/wday/cxs/{tenant}/{site}"

    async def _facets(self, ctx, board):
        body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
        data = await ctx.cached(f"workday-facets:{board}", lambda: ctx.post_json(self._api(board) + "/jobs", body))
        return data.get("facets") or []

    def applied_facets(self, facets: list[dict], search) -> tuple[dict[str, list[str]], list[str]]:
        """Workday facet ids for the search; also the locations no facet matched."""
        applied: dict[str, list[str]] = {}
        groups = list(_facet_groups(facets))
        unresolved = []
        for loc in search.locations:
            hits = [(p, v["id"]) for p, label, vals in groups if p and "location" in p.lower()
                    for v in vals if _same_place(v.get("descriptor", ""), loc)]
            if hits:
                for p, vid in hits[:1]:
                    applied.setdefault(p, []).append(vid)
            elif loc.strip():
                unresolved.append(loc)
        if search.employment_types:
            wanted = {EMPLOYMENT[e] for e in search.employment_types if e in EMPLOYMENT}
            for p, label, vals in groups:
                if p == "timeType":
                    ids = [v["id"] for v in vals if _norm(v.get("descriptor", "")) in wanted]
                    if ids:
                        applied[p] = ids
        for key, values in search.site_filters.facets.items():
            match = [(p, vals) for p, label, vals in groups if _norm(key) in (_norm(p or ""), _norm(label))]
            if not match:
                names = sorted({label or p for p, label, _ in groups if p})
                raise ValueError(f"Workday facet {key!r} not found; available: {names}")
            p, vals = match[0]
            ids = [v["id"] for v in vals if any(_norm(v.get("descriptor", "")) == _norm(x) for x in values)]
            missing = [x for x in values if not any(_norm(v.get("descriptor", "")) == _norm(x) for v in vals)]
            if missing:
                raise ValueError(f"Workday facet {key!r} has no value {missing}; available: {[v.get('descriptor') for v in vals]}")
            applied[p] = ids
        return applied, unresolved

    async def search(self, ctx, board, search):
        applied, unresolved = self.applied_facets(await self._facets(ctx, board), search)
        self._unresolved_locations[search.name] = unresolved
        host, _, site = parse_board(board)
        jobs: dict[str, RawJob] = {}
        for kw in search.keywords or [""]:
            offset = 0
            while offset < MAX_RESULTS:
                body = {"appliedFacets": applied, "limit": PAGE_SIZE, "offset": offset, "searchText": kw}
                data = await ctx.post_json(self._api(board) + "/jobs", body)
                postings = data.get("jobPostings") or []
                for p in postings:
                    job = self.to_job(p, host, site)
                    jobs.setdefault(job.id, job)
                if len(postings) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
        return list(jobs.values())

    def to_job(self, p: dict, host: str, site: str) -> RawJob:
        path = p["externalPath"]
        return RawJob(
            id=workday_job_id(path.rstrip("/").rsplit("/", 1)[-1]),
            url=f"https://{host}/{site}{path}",
            title=(p.get("title") or "").strip(),
            locations=[p.get("locationsText") or ""],
            posted_text=p.get("postedOn"),
            posted_at=posted_from_text(p.get("postedOn")),
            apply_url=f"https://{host}/{site}{path}",
            record=p,
        )

    def local_filter(self, job, search):
        if not posted_within(job.posted_at, search.posted_within_days):
            return "posted date"
        unresolved = self._unresolved_locations.get(search.name) or []
        text = " ".join(job.locations)
        if unresolved and not re.search(r"\d+ locations", text, re.I) and not location_match(job.locations, unresolved):
            return "location"
        return None

    async def fetch_detail(self, ctx, board, job):
        return await ctx.get_json(self._api(board) + job.record["externalPath"])

    # ---- parse -----------------------------------------------------------------------

    parser_version = "workday-2"

    def parse(self, raw) -> CommonPosting:
        if not raw.detail or "jobPostingInfo" not in raw.detail:
            raise ParseError("no job detail saved (the list record lacks the description)")
        info = raw.detail["jobPostingInfo"]
        desc_html = info.get("jobDescription") or ""
        desc = html_to_text(desc_html)
        workplace = workplace_of((info.get("remoteType") or "").replace("_", " "))
        if workplace == "unknown":  # companies tag the workplace for LinkedIn syndication: #LI-Hybrid, #LI-Remote …
            m = re.search(r"#LI-(Remote|Hybrid|Onsite|On-site)\b", desc, re.I)
            workplace = workplace_of(m.group(1)) if m else "unknown"
        start = info.get("startDate")
        posted_at = datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else posted_from_text(info.get("postedOn"))
        org = raw.detail.get("hiringOrganization") or {}
        bullets = raw.record.get("bulletFields") or []
        return CommonPosting(
            source=raw.source,
            source_job_id=raw.source_job_id,
            title=(info.get("title") or "").strip(),
            company=raw.company_hint or org.get("name"),  # config name; Workday's is the legal entity
            locations=[x for x in [info.get("location"), *(info.get("additionalLocations") or [])] if x],
            workplace_type=workplace,
            employment_type=employment_of(info.get("timeType")),
            salary=salary_from_description(desc),
            posted_at=posted_at,
            posted_at_precision="day" if start else "approx",
            posted_text=info.get("postedOn"),
            apply_url=info.get("externalUrl") or raw.capture.get("final_url"),
            apply_kind="external",
            url=info.get("externalUrl") or raw.capture.get("final_url"),
            req_id=info.get("jobReqId") or (bullets[0] if bullets else None),
            description_text=desc,
            description_html=desc_html or None,
            extras={"hiring_entity": org.get("name"), "time_type": info.get("timeType"), "remote_type": info.get("remoteType"),
                    "requisition_location": (info.get("jobRequisitionLocation") or {}).get("descriptor")},
        )
