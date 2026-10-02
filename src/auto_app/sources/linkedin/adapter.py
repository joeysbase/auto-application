import re
from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict

from auto_app.core.search import ResolvedSearch
from auto_app.sources.base import SourceAdapter, SourceSpec
from auto_app.sources.filters import Native
from auto_app.sources.linkedin import selectors as S
from auto_app.sources.linkedin.parser import PARSER_VERSION, parse_linkedin

# LinkedIn's newer search page, /jobs/search-results/ (what the search bar on linkedin.com/jobs opens), verified
# 2026-10-01 by applying each filter in the browser and reading the URL it produced:
#   keywords=…                      the query
#   geoId=<id>                      location (United States = 103644278); the `location=` text param is ignored
#   f_TPR=r86400|r604800|r2592000   date posted: past 24 hours / week / month
#   f_SAL=f_SA_id_<filter>:<value>,<value>$f_SA_id_<filter>:<value>   every multi-choice filter (not only salary):
#        225001 workplace (only "Remote", 272001), 226001 employment type, 227001 experience level
#   f_AL=true  Easy Apply · f_EA=true  Under 10 applicants · f_JIYN=true  In my network
# The classic page's f_WT / f_JT / f_E / sortBy are ignored; results are ordered by relevance only.
GEO_IDS = {"united states": "103644278", "usa": "103644278", "us": "103644278"}
WORKPLACE_SEG = "225001"
EMPLOYMENT_SEG = "226001"
EXPERIENCE_SEG = "227001"
REMOTE = "272001"
EMPLOYMENT_IDS = {"full_time": "272015", "part_time": "273001", "contract": "274001", "internship": "275001",
                  "volunteer": "272002"}
EXPERIENCE_IDS = {"entry_level": "276001", "senior": "277001", "manager": "278001", "director": "272003",
                  "executive": "279001"}
GENERIC_EXPERIENCE = {"entry": "entry_level", "director": "director", "executive": "executive"}  # exact matches only
DATE_WINDOWS = (1, 7, 30)  # the page's "Date posted" options, in days
_AFTER_EXACT = r"""
({texts}) => {
  const note = [...document.querySelectorAll('p, span, div')].find(e => e.children.length === 0
                 && texts.some(t => (e.textContent || '').toLowerCase().includes(t)));
  if (!note) return null;
  return [...document.querySelectorAll('[componentkey^="job-card-component-ref-"]')]
    .filter(c => note.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)
    .map(c => c.getAttribute('componentkey').replace('job-card-component-ref-', ''));
}
"""
VERIFIED = re.compile(r"\s*\(Verified job\)\s*$")

EmploymentType = Literal["full_time", "part_time", "contract", "internship", "volunteer"]
LinkedInExperience = Literal["entry_level", "senior", "manager", "director", "executive"]


class LinkedInFilters(BaseModel):
    """LinkedIn's own search filters (the buttons under its search bar). Each one, when set, replaces what the
    search's generic field would map to. e.g. {employment_type: [full_time], experience_level: [entry_level],
    easy_apply: true, under_10_applicants: true}"""

    model_config = ConfigDict(extra="forbid")
    employment_type: list[EmploymentType] = []
    experience_level: list[LinkedInExperience] = []  # LinkedIn's own levels (no associate / mid-senior)
    remote: bool = False
    easy_apply: bool = False
    under_10_applicants: bool = False
    in_my_network: bool = False
    geo_id: str | None = None  # LinkedIn location id: pick a location in the browser, copy geoId from the URL


SPEC = SourceSpec(
    name="linkedin",
    base_url="https://www.linkedin.com",
    site_hosts=("linkedin.com",),
    login_url="{base}/login",
    session_check_url="{base}/feed/",
    detail_url_template="{base}/jobs/view/{id}/",
    logged_in_selectors=S.LOGGED_IN,
    pagination="url",
    page_param="start",
    page_param_kind="offset",
    page_size=25,
    list_selectors=S.LIST,
    card_container_selectors=S.CARD_CONTAINERS,
    card_id_attributes=S.CARD_ID_ATTRIBUTES,
    job_link_patterns=S.JOB_LINK_PATTERNS,
    promoted_texts=S.PROMOTED_TEXTS,
    detail_ready_selectors=S.DETAIL_READY,
    capture_url_patterns=S.CAPTURE_URL_PATTERNS,
    apply_selectors=S.APPLY,
    selector_version=S.SELECTOR_VERSION,
)


class LinkedInAdapter(SourceAdapter):
    filters = {
        "keywords": Native("keywords", note="matched by meaning (broad), not exact words"),
        "locations": Native("geoId", note="United States built in; other places: site_filters.geo_id"),
        "posted_within_days": Native("f_TPR", note="rounded up to 24 hours / week / month"),
        "workplace_types": Native("f_SAL Remote", note="only [remote] can be filtered; other combinations → Step 2"),
        "employment_types": Native("f_SAL employment type", note="full_time, part_time, contract, internship"),
        "experience_levels": Native("f_SAL experience level", note="entry, director, executive; others → Step 2"),
    }
    SiteFilters = LinkedInFilters
    sorted_by_date = False  # relevance order only → no early stop; max_pages limits depth

    def __init__(self):
        super().__init__(SPEC)

    # ---- generic fields + site_filters → LinkedIn's filters ----------------------------------------

    @staticmethod
    def _employment(search: ResolvedSearch) -> list[str] | None:
        sf = search.site_filters
        if sf and sf.employment_type:
            return [EMPLOYMENT_IDS[e] for e in sf.employment_type]
        wanted = search.employment_types
        if wanted and all(e in EMPLOYMENT_IDS for e in wanted):
            return [EMPLOYMENT_IDS[e] for e in wanted]
        return None  # none asked, or a value LinkedIn can't filter → the whole field goes to Step 2

    @staticmethod
    def _experience(search: ResolvedSearch) -> list[str] | None:
        sf = search.site_filters
        if sf and sf.experience_level:
            return [EXPERIENCE_IDS[e] for e in sf.experience_level]
        wanted = search.experience_levels
        if wanted and all(e in GENERIC_EXPERIENCE for e in wanted):
            return [EXPERIENCE_IDS[GENERIC_EXPERIENCE[e]] for e in wanted]
        return None

    @staticmethod
    def _remote(search: ResolvedSearch) -> bool:
        sf = search.site_filters
        return bool(sf and sf.remote) or list(dict.fromkeys(search.workplace_types)) == ["remote"]

    @staticmethod
    def _geo(location: str, search: ResolvedSearch) -> str | None:
        sf = search.site_filters
        if sf and sf.geo_id:
            return sf.geo_id
        return GEO_IDS.get(location.strip().lower()) if location else None

    def check_search(self, search: ResolvedSearch) -> tuple[list[str], list[str]]:
        """(errors, notices) for one resolved search — what LinkedIn can't filter exactly."""
        errors, notes = [], []
        sf = search.site_filters
        if not (sf and sf.geo_id):
            unknown = [loc for loc in search.locations if loc.strip() and loc.strip().lower() not in GEO_IDS]
            if unknown:
                errors.append(f"LinkedIn needs a location id for {unknown}: choose the location on linkedin.com/jobs, "
                              f"copy `geoId=` from the URL into `site_filters: {{geo_id: \"…\"}}` (one search per place)")
        if search.employment_types and not (sf and sf.employment_type) and self._employment(search) is None:
            notes.append(f"employment_types {search.employment_types}: LinkedIn has no filter for "
                         f"{[e for e in search.employment_types if e not in EMPLOYMENT_IDS]} → the field is applied in Step 2")
        if search.experience_levels and not (sf and sf.experience_level) and self._experience(search) is None:
            notes.append(f"experience_levels {search.experience_levels}: LinkedIn's levels are entry-level, senior, manager, "
                         f"director, executive → the field is applied in Step 2 (or set site_filters.experience_level)")
        places = set(search.workplace_types)
        if places and places != {"remote"} and places != {"remote", "hybrid", "onsite"}:
            notes.append(f"workplace_types {search.workplace_types}: LinkedIn only filters Remote → applied in Step 2")
        days = search.posted_within_days
        if days and days not in DATE_WINDOWS:
            window = next((w for w in DATE_WINDOWS if w >= days), None)
            notes.append(f"posted_within_days {days}: LinkedIn offers 1 / 7 / 30 days → "
                         + (f"searching the past {window} days" if window else "no date filter"))
        return errors, notes

    def search_url(self, keywords: str, location: str, search: ResolvedSearch) -> str:
        params = {"keywords": keywords}
        geo = self._geo(location, search)
        if geo:
            params["geoId"] = geo
        days = search.posted_within_days
        window = next((w for w in DATE_WINDOWS if days and w >= days), None)
        if window:  # None = "all", or more than a month: no "date posted" filter
            params["f_TPR"] = f"r{window * 86400}"
        segments = []
        if self._remote(search):
            segments.append(f"f_SA_id_{WORKPLACE_SEG}:{REMOTE}")
        for seg, ids in ((EMPLOYMENT_SEG, self._employment(search)), (EXPERIENCE_SEG, self._experience(search))):
            if ids:
                segments.append(f"f_SA_id_{seg}:{','.join(dict.fromkeys(ids))}")
        if segments:
            params["f_SAL"] = "$".join(segments)
        sf = search.site_filters
        if sf:
            for flag, key in ((sf.easy_apply, "f_AL"), (sf.under_10_applicants, "f_EA"), (sf.in_my_network, "f_JIYN")):
                if flag:
                    params[key] = "true"
        return self.spec.url("{base}/jobs/search-results/") + "?" + urlencode(params, quote_via=quote)

    async def _ids_after_exact(self, page) -> list[str] | None:
        """Job ids listed after LinkedIn's "may not be exact matches" note (None = no note on the page)."""
        return await page.evaluate(_AFTER_EXACT, {"texts": list(S.NOT_EXACT_TEXTS)})

    async def end_of_results(self, page) -> bool:
        return await self._ids_after_exact(page) is not None

    async def read_cards(self, page, page_index):
        cards = await super().read_cards(page, page_index)
        related = await self._ids_after_exact(page)
        if related:  # only the exact matches: the rest ignore the search's filters
            cards = [c for c in cards if c.source_job_id not in set(related)]
        for c in cards:  # the newer search page appends a screen-reader label to verified titles
            if c.title:
                c.title = VERIFIED.sub("", c.title).strip()
        return cards

    parser_version = PARSER_VERSION

    def parse(self, raw):
        return parse_linkedin(raw)

    def classify_apply(self, text: str | None, href: str | None) -> str:
        # LinkedIn's non-Easy-Apply button leads off-site even when it has no href.
        return "easy_apply" if "easy apply" in (text or "").lower() else "external"
