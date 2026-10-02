"""Ashby job boards — public API, full list with descriptions (e.g. UiPath: board `uipath`)."""

from pydantic import BaseModel, ConfigDict

from auto_app.sources.api import ApiSource, RawJob, in_any, mapped_mismatch, parse_iso
from auto_app.sources.filters import Local
from auto_app.core.models import CommonPosting, Salary
from auto_app.sources.common.text import html_to_text, parse_salary, salary_from_description


API = "https://api.ashbyhq.com/posting-api/job-board/{board}"
WORKPLACE = {"OnSite": "onsite", "Remote": "remote", "Hybrid": "hybrid"}
EMPLOYMENT = {"FullTime": "full_time", "PartTime": "part_time", "Intern": "internship",
              "Contract": "contract", "Temporary": "temporary"}


class AshbyFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departments: list[str] = []
    teams: list[str] = []


def _address_parts(addr: dict | None) -> list[str]:
    pa = (addr or {}).get("postalAddress") or {}
    return [pa[k] for k in ("addressLocality", "addressRegion", "addressCountry") if pa.get(k)]


class AshbySource(ApiSource):
    platform = "ashby"
    board_help = "the name in jobs.ashbyhq.com/<board>"
    SiteFilters = AshbyFilters
    filters = {
        "keywords": Local(("title",)),
        "locations": Local(("location", "address", "secondaryLocations", "isRemote")),
        "posted_within_days": Local(("publishedAt",)),
        "workplace_types": Local(("workplaceType",)),
        "employment_types": Local(("employmentType",)),
    }

    async def search(self, ctx, board, search):
        data = await ctx.cached(
            f"ashby:{board}", lambda: ctx.get_json(API.format(board=board), {"includeCompensation": "true"})
        )
        return [self.to_job(j) for j in data.get("jobs", []) if j.get("isListed", True)]

    def to_job(self, j: dict) -> RawJob:
        locs = [j.get("location") or ""] + _address_parts(j.get("address"))
        for s in j.get("secondaryLocations") or []:
            locs += [s.get("location") or ""] + _address_parts(s.get("address"))
        return RawJob(
            id=j["id"],
            url=j["jobUrl"],
            title=(j.get("title") or "").strip(),
            locations=[x for x in locs if x],
            posted_at=parse_iso(j.get("publishedAt")),
            apply_url=j.get("applyUrl"),
            remote=j.get("isRemote"),
            record=j,
        )

    def local_filter(self, job, search):
        if reason := super().local_filter(job, search):
            return reason
        j, sf = job.record, search.site_filters
        if mapped_mismatch(j.get("workplaceType"), WORKPLACE, search.workplace_types):
            return "workplace type"
        if mapped_mismatch(j.get("employmentType"), EMPLOYMENT, search.employment_types):
            return "employment type"
        if not in_any(j.get("department"), sf.departments):
            return "department"
        if not in_any(j.get("team"), sf.teams):
            return "team"
        return None

    # ---- parse -----------------------------------------------------------------------

    parser_version = "ashby-1"

    def parse(self, raw) -> CommonPosting:
        j = raw.record
        addr = ", ".join(_address_parts(j.get("address")))
        locations = [j.get("location") or "", addr] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))
        return CommonPosting(
            source=raw.source,
            source_job_id=raw.source_job_id,
            title=(j.get("title") or "").strip(),
            company=raw.company_hint,
            locations=list(dict.fromkeys(x for x in locations if x)),
            workplace_type=WORKPLACE.get(j.get("workplaceType"), "unknown"),
            employment_type=EMPLOYMENT.get(j.get("employmentType"), "unknown"),
            salary=_salary(j.get("compensation") or {}) or salary_from_description(desc),
            posted_at=parse_iso(j.get("publishedAt")),
            posted_at_precision="exact" if j.get("publishedAt") else None,
            apply_url=j.get("applyUrl"),
            apply_kind="external",
            url=j.get("jobUrl"),
            description_text=desc.strip(),
            description_html=j.get("descriptionHtml"),
            extras={"department": j.get("department"), "team": j.get("team"), "is_remote": j.get("isRemote")},
        )


def _salary(comp: dict) -> Salary | None:
    for c in comp.get("summaryComponents") or []:
        if c.get("compensationType") == "Salary" and (c.get("minValue") or c.get("maxValue")):
            period = {"1 YEAR": "year", "1 HOUR": "hour", "1 MONTH": "month"}.get(c.get("interval") or "")
            return Salary(min=c.get("minValue"), max=c.get("maxValue"), currency=c.get("currencyCode"),
                          period=period, text=comp.get("compensationTierSummary"), source="structured")
    return parse_salary(comp.get("compensationTierSummary"), "structured")
