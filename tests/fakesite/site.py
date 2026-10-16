"""A tiny job site served through Playwright routes, for exercising the collect engine offline."""

import json
from urllib.parse import parse_qs, urlparse

from playwright.async_api import BrowserContext, Route

from auto_app.core.search import ResolvedSearch
from auto_app.sources.base import SourceAdapter, SourceSpec
from auto_app.sources.filters import Native

BASE = "http://fake.test"

JOBS = {
    "101": {"title": "Machine Learning Engineer", "company": "Acme"},
    "102": {"title": "Senior Staff ML Engineer", "company": "Beta"},
    "103": {"title": "ML Engineer", "company": "Gamma"},
    "104": {"title": "Data Scientist", "company": "Delta"},
    "105": {"title": "Research Engineer", "company": "Epsilon"},
    "404": {"title": "Removed Job", "company": "Zeta"},
}
PAGES = [["101", "102", "103"], ["104", "105", "404"], []]

SPEC = SourceSpec(
    name="fake",
    base_url=BASE,
    site_hosts=("fake.test",),
    login_url="{base}/login",
    session_check_url="{base}/home",
    detail_url_template="{base}/job/{id}",
    logged_in_selectors=("#me",),
    pagination="url",
    page_param="p",
    page_param_kind="page",
    list_selectors=("ul#results",),
    card_container_selectors=("li",),
    job_link_patterns=(r"/job/(\d+)",),
    promoted_texts=("Promoted",),
    detail_ready_selectors=("h1",),
    capture_url_patterns=("/api/job/",),
)


class FakeAdapter(SourceAdapter):
    filters = {"keywords": Native("q")}

    def __init__(self):
        super().__init__(SPEC)

    def search_url(self, keywords: str, location: str, search: ResolvedSearch) -> str:
        return f"{BASE}/search?q={keywords}"


class FakeSite:
    def __init__(self):
        self.captcha = False
        self.challenge = False  # Cloudflare-style interstitial like Jobright's
        self.challenge_once = False  # show it once, then "the user completes it" and it redirects back
        self.challenge_passed = False
        self.logged_in = True
        self.offline_paths: tuple[str, ...] = ()  # these paths fail as if the internet were down
        self.requests: list[str] = []

    async def install(self, context: BrowserContext) -> None:
        await context.route(f"{BASE}/**", self.handle)

    async def handle(self, route: Route) -> None:
        url = urlparse(route.request.url)
        self.requests.append(url.path + ("?" + url.query if url.query else ""))
        html = lambda body, status=200: route.fulfill(status=status, content_type="text/html", body=body)  # noqa: E731
        if url.path.startswith(self.offline_paths):
            return await route.abort("internetdisconnected")
        if url.path == "/home":
            if not self.logged_in:
                return await html("<nav><a href='/login'>Sign in</a></nav>")
            return await html('<div id="me">me</div>')
        if url.path == "/login":
            return await html("<a href='#'>Sign in</a>")
        if url.path == "/security/challenge":
            ret = parse_qs(url.query).get("return", ["/"])[0]
            passes = "setTimeout(() => location.replace(%s), 800);" % json.dumps(ret) if self.challenge_once else ""
            return await html('<h2>One quick security check</h2><p>Preparing secure check…</p>'
                              '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
                              f"<script>{passes}</script>")
        if url.path == "/search":
            if "passed" in url.query:
                self.challenge_passed = True  # a real site would set a clearance cookie
            if self.challenge_once and not self.challenge_passed:
                return await html("<script>location.replace('/security/challenge?return=' + "
                                  "encodeURIComponent('/search?q=ml&passed=1'))</script>")
            if self.challenge:
                return await html("<script>location.replace('/security/challenge?return=%2Fsearch')</script>")
            if self.captcha:
                return await html('<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>')
            page = int(parse_qs(url.query).get("p", ["1"])[0]) - 1
            ids = PAGES[page] if page < len(PAGES) else []
            items = "".join(
                f'<li><a href="/job/{i}?ref=search">{JOBS[i]["title"]}</a><div>{JOBS[i]["company"]}</div>'
                f'<div>Boston, MA</div>{"<span>Promoted</span>" if i == "103" else ""}</li>'
                for i in ids
            )
            return await html(f'<ul id="results">{items}</ul>')
        if url.path.startswith("/job/"):
            jid = url.path.rsplit("/", 1)[1]
            if jid == "404":
                return await html("<h1>Not found</h1>", 404)
            job = JOBS[jid]
            ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": job["title"],
                  "hiringOrganization": {"@type": "Organization", "name": job["company"]}}
            return await html(
                f"""<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head>
                <body><h1>{job["title"]}</h1><div id="d"></div>
                <a class="apply" href="https://careers.{job["company"].lower()}.example/jobs/{jid}">Apply</a>
                <script>fetch('/api/job/{jid}').then(r => r.json()).then(j => {{
                  document.querySelector('#d').textContent = j.description; }});</script></body></html>"""
            )
        if url.path.startswith("/api/job/"):
            jid = url.path.rsplit("/", 1)[1]
            body = json.dumps({"id": jid, "description": f"Description of job {jid}"})
            return await route.fulfill(status=200, content_type="application/json", body=body)
        return await route.fulfill(status=404, body="")
