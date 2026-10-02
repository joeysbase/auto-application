"""Lever job boards — public API, full list with descriptions (e.g. Palantir: board `palantir`)."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from auto_app.sources.api import ApiSource, RawJob, in_any, mapped_mismatch
from auto_app.sources.filters import Local
from auto_app.core.models import CommonPosting, Salary
from auto_app.sources.common.text import html_to_text, salary_from_description


API = "https://api.lever.co/v0/postings/{board}"
WORKPLACE = {"onsite": "onsite", "on-site": "onsite", "remote": "remote", "hybrid": "hybrid"}
EMPLOYMENT = {"Full-time": "full_time", "Full Time": "full_time", "Part-time": "part_time", "Intern": "internship",
              "Internship": "internship", "Contract": "contract", "Contractor": "contract", "Temporary": "temporary"}


class LeverFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teams: list[str] = []
    departments: list[str] = []


class LeverSource(ApiSource):
    platform = "lever"
    board_help = "the name in jobs.lever.co/<board>"
    SiteFilters = LeverFilters
    filters = {
        "keywords": Local(("text",)),
        "locations": Local(("categories.location", "categories.allLocations", "country")),
        "posted_within_days": Local(("createdAt",)),
        "workplace_types": Local(("workplaceType",)),
        "employment_types": Local(("categories.commitment",)),
    }

    async def search(self, ctx, board, search):
        data = await ctx.cached(f"lever:{board}", lambda: ctx.get_json(API.format(board=board), {"mode": "json"}))
        return [self.to_job(j) for j in data]

    def to_job(self, j: dict) -> RawJob:
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        country = j.get("country")
        if country:
            locs.append("United States" if country == "US" else country)
        created = j.get("createdAt")
        return RawJob(
            id=j["id"],
            url=j["hostedUrl"],
            title=(j.get("text") or "").strip(),
            locations=[x for x in locs if x],
            posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
            apply_url=j.get("applyUrl"),
            remote=(j.get("workplaceType") == "remote") or None,
            record=j,
        )

    def local_filter(self, job, search):
        if reason := super().local_filter(job, search):
            return reason
        j, sf = job.record, search.site_filters
        cats = j.get("categories") or {}
        if mapped_mismatch(j.get("workplaceType"), WORKPLACE, search.workplace_types):
            return "workplace type"
        if mapped_mismatch(cats.get("commitment"), EMPLOYMENT, search.employment_types):
            return "employment type"
        if not in_any(cats.get("team"), sf.teams):
            return "team"
        if not in_any(cats.get("department"), sf.departments):
            return "department"
        return None

    # ---- parse -----------------------------------------------------------------------

    parser_version = "lever-1"

    def parse(self, raw) -> CommonPosting:
        j = raw.record
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
        parts.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
        desc = "\n\n".join(p.strip() for p in parts if p and p.strip())
        created = j.get("createdAt")
        sr = j.get("salaryRange") or {}
        salary = None
        if sr.get("min") or sr.get("max"):
            period = {"per-year-salary": "year", "per-hour-wage": "hour", "per-month-salary": "month"}.get(sr.get("interval"))
            salary = Salary(min=sr.get("min"), max=sr.get("max"), currency=sr.get("currency"), period=period,
                            source="structured")
        return CommonPosting(
            source=raw.source,
            source_job_id=raw.source_job_id,
            title=(j.get("text") or "").strip(),
            company=raw.company_hint,
            locations=list(dict.fromkeys(x for x in [cats.get("location") or "", *(cats.get("allLocations") or [])] if x)),
            workplace_type=WORKPLACE.get(j.get("workplaceType") or "", "unknown"),
            employment_type=EMPLOYMENT.get(cats.get("commitment") or "", "unknown"),
            salary=salary or salary_from_description(desc),
            posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
            posted_at_precision="exact" if created else None,
            apply_url=j.get("applyUrl"),
            apply_kind="external",
            url=j.get("hostedUrl"),
            description_text=desc,
            description_html=j.get("description"),
            extras={"team": cats.get("team"), "department": cats.get("department"), "country": j.get("country")},
        )
