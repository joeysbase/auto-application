from urllib.parse import urlencode

from auto_app.core.search import ResolvedSearch
from auto_app.sources.base import SourceAdapter, SourceSpec
from auto_app.sources.filters import Native
from auto_app.sources.handshake import selectors as S

SPEC = SourceSpec(
    name="handshake",
    # Many schools use <school>.joinhandshake.com; override with sources.handshake.limits.base_url.
    base_url="https://app.joinhandshake.com",
    site_hosts=("joinhandshake.com",),
    login_url="{base}/login",
    session_check_url="{base}/job-search",
    detail_url_template="{base}/jobs/{id}",
    logged_in_selectors=S.LOGGED_IN,
    pagination="url",
    page_param="page",
    page_param_kind="page",
    page_size=25,
    list_selectors=S.LIST,
    card_container_selectors=S.CARD_CONTAINERS,
    card_id_attributes=S.CARD_ID_ATTRIBUTES,
    job_link_patterns=S.JOB_LINK_PATTERNS,
    detail_ready_selectors=S.DETAIL_READY,
    capture_url_patterns=S.CAPTURE_URL_PATTERNS,
    apply_selectors=S.APPLY,
    selector_version=S.SELECTOR_VERSION,
)


class HandshakeAdapter(SourceAdapter):
    filters = {"keywords": Native("query")}  # other filters unverified → deferred

    def __init__(self):
        super().__init__(SPEC)

    def search_url(self, keywords: str, location: str, search: ResolvedSearch) -> str:
        # Only the keyword param is assumed; set other filters via `site_url: {handshake: …}`
        # or `collect --interactive` until the spike confirms the URL format.
        params = {"query": keywords, "page": "1", "per_page": str(self.spec.page_size)}
        return self.spec.url("{base}/job-search") + "?" + urlencode(params)

    def classify_apply(self, text: str | None, href: str | None) -> str:
        t = (text or "").lower()
        if "external" in t or (href and not self.is_site_host(href)):
            return "external"
        return "onsite"
