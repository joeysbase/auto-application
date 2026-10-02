from urllib.parse import urlencode

from auto_app.core.search import ResolvedSearch
from auto_app.sources.base import SourceAdapter, SourceSpec
from auto_app.sources.filters import Native
from auto_app.sources.jobright import selectors as S

SPEC = SourceSpec(
    name="jobright",
    base_url="https://jobright.ai",
    site_hosts=("jobright.ai",),
    login_url="{base}/",
    session_check_url="{base}/jobs/recommend",
    detail_url_template="{base}/jobs/info/{id}",
    logged_in_selectors=S.LOGGED_IN,
    pagination="scroll",
    list_selectors=S.LIST,
    card_container_selectors=S.CARD_CONTAINERS,
    card_id_attributes=S.CARD_ID_ATTRIBUTES,
    job_link_patterns=S.JOB_LINK_PATTERNS,
    detail_ready_selectors=S.DETAIL_READY,
    capture_url_patterns=S.CAPTURE_URL_PATTERNS,
    apply_selectors=S.APPLY,
    selector_version=S.SELECTOR_VERSION,
)


class JobrightAdapter(SourceAdapter):
    filters = {"keywords": Native("value")}  # other filters unverified → deferred

    def __init__(self):
        super().__init__(SPEC)

    def search_url(self, keywords: str, location: str, search: ResolvedSearch) -> str:
        # Search URL format unverified; Jobright filters are mostly set in the UI.
        # Use `site_url: {jobright: …}` or `collect --interactive` until the spike confirms it.
        return self.spec.url("{base}/jobs/search") + "?" + urlencode({"value": keywords})
