"""Greenhouse job boards — public API, full list with HTML content (e.g. Anthropic: board `anthropic`)."""

import html

from pydantic import BaseModel, ConfigDict

from auto_app.sources.api import ApiSource, RawJob, in_any, parse_iso
from auto_app.sources.filters import Local
from auto_app.core.models import CommonPosting
from auto_app.sources.common.text import html_to_text, salary_from_description, workplace_of


API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


class GreenhouseFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departments: list[str] = []
    offices: list[str] = []


class GreenhouseSource(ApiSource):
    platform = "greenhouse"
    board_help = "the name in boards.greenhouse.io/<board> or job-boards.greenhouse.io/<board>"
    SiteFilters = GreenhouseFilters
    filters = {
        "keywords": Local(("title",)),
        "locations": Local(("location.name", "offices")),
        "posted_within_days": Local(("first_published",)),
    }

    async def search(self, ctx, board, search):
        data = await ctx.cached(f"greenhouse:{board}", lambda: ctx.get_json(API.format(board=board), {"content": "true"}))
        return [self.to_job(j) for j in data.get("jobs", [])]

    def to_job(self, j: dict) -> RawJob:
        locs = [(j.get("location") or {}).get("name") or ""]
        for o in j.get("offices") or []:
            locs += [o.get("name") or "", o.get("location") or ""]
        name = locs[0].lower()
        return RawJob(
            id=str(j["id"]),
            url=j["absolute_url"],
            title=(j.get("title") or "").strip(),
            locations=[x for x in locs if x],
            posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
            apply_url=j["absolute_url"],
            remote="remote" in name or None,
            record=j,
        )

    def local_filter(self, job, search):
        if reason := super().local_filter(job, search):
            return reason
        j, sf = job.record, search.site_filters
        if sf.departments and not any(in_any(d.get("name"), sf.departments) for d in j.get("departments") or []):
            return "department"
        if sf.offices and not any(in_any(o.get("name"), sf.offices) for o in j.get("offices") or []):
            return "office"
        return None

    # ---- parse -----------------------------------------------------------------------

    parser_version = "greenhouse-2"

    def parse(self, raw) -> CommonPosting:
        j = raw.record
        desc_html = html.unescape(j.get("content") or "")
        desc = html_to_text(desc_html)
        locations = [(j.get("location") or {}).get("name") or ""] + [o.get("location") or o.get("name") or ""
                                                                     for o in j.get("offices") or []]
        meta = {m.get("name"): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
        workplace = workplace_of(str(meta.get("Location Type") or ""))
        if workplace == "unknown" and "remote" in locations[0].lower():
            workplace = "remote"
        posted = j.get("first_published") or j.get("updated_at")
        return CommonPosting(
            source=raw.source,
            source_job_id=raw.source_job_id,
            title=(j.get("title") or "").strip(),
            company=raw.company_hint or j.get("company_name"),
            locations=list(dict.fromkeys(x for x in locations if x)),
            workplace_type=workplace,
            salary=salary_from_description(desc),
            posted_at=parse_iso(posted),
            posted_at_precision="exact" if posted else None,
            apply_url=j.get("absolute_url"),
            apply_kind="external",
            url=j.get("absolute_url"),
            req_id=j.get("requisition_id") or None,
            description_text=desc,
            description_html=desc_html or None,
            extras={"departments": [d.get("name") for d in j.get("departments") or []],
                    "offices": [o.get("name") for o in j.get("offices") or []], "metadata": meta},
        )
