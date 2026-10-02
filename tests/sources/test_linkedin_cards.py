"""LinkedIn card reading on both search pages, on minimal copies of their markup (no network)."""

import pytest
from playwright.async_api import async_playwright

from auto_app.sources import get_adapter

CLASSIC = """<div class="scaffold-layout__list"><ul>
  <li data-occludable-job-id="111"><a href="/jobs/view/111/?trk=x">ML Engineer</a><div>Acme</div></li>
  <li data-occludable-job-id="222"><a href="/jobs/view/222/">Applied Scientist</a><div>Beta</div></li>
</ul></div>"""

# /jobs/search-results/ (2026-10): cards are div[role=button] with the job id only in `componentkey`
NEWER = """<div data-testid="lazy-column">
  <div role="button" componentkey="job-card-component-ref-4455900105"><div componentkey="job-card-component-ref-4455900105">
    <p><span>Applied Scientist - Computational Modeling (Verified job)</span></p><p>Amazon</p><p>Middlesex County, MA</p>
  </div></div>
  <div role="button" componentkey="job-card-component-ref-4472343533"><p>ML Researcher</p><p>Gamma</p></div>
  <div componentkey="search-filters-bar">not a card</div>
</div>"""


# exact matches end at LinkedIn's note; the cards after it ignore the filters
WITH_RELATED = """<div data-testid="lazy-column">
  <div role="button" componentkey="job-card-component-ref-1001"><p>Exact Match One</p></div>
  <div role="button" componentkey="job-card-component-ref-1002"><p>Exact Match Two</p></div>
  <div><p>We found more results related to your search that may not be exact matches, but could still be a great fit.</p></div>
  <div role="button" componentkey="job-card-component-ref-1003"><p>Related Only</p></div>
</div>"""


async def _cards(html, end=False):
    li = get_adapter("linkedin")
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        await page.goto("https://www.linkedin.com/jobs/search/")  # served locally: an origin so hrefs resolve
        cards = await li.read_cards(page, 0)
        ended = await li.end_of_results(page)
        await browser.close()
    return (cards, ended) if end else cards


@pytest.mark.asyncio
async def test_classic_search_page_cards():
    cards = await _cards(CLASSIC)
    assert [(c.source_job_id, c.title) for c in cards] == [("111", "ML Engineer"), ("222", "Applied Scientist")]


@pytest.mark.asyncio
async def test_newer_search_page_cards_take_the_id_from_componentkey():
    cards = await _cards(NEWER)
    assert [c.source_job_id for c in cards] == ["4455900105", "4472343533"]  # nested duplicate + non-card ignored
    assert cards[0].title == "Applied Scientist - Computational Modeling"  # "(Verified job)" label removed
    assert cards[0].url == "https://www.linkedin.com/jobs/view/4455900105/"  # no link on the card → job page URL


@pytest.mark.asyncio
async def test_newer_page_cards_stop_at_the_related_results_note():
    cards, ended = await _cards(WITH_RELATED, end=True)
    assert [c.source_job_id for c in cards] == ["1001", "1002"] and ended
    _, ended = await _cards(NEWER, end=True)
    assert not ended
