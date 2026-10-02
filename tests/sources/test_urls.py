from urllib.parse import parse_qs, urlparse

from auto_app.collect.plan import Selection, build_tasks
from auto_app.core.config import AppConfig, SourceConfig, SourceSearch
from auto_app.sources import get_adapter
from auto_app.sources.registry import load_registry


def resolved(search: SourceSearch, site: str):
    cfg = AppConfig(sources={site: SourceConfig(searches=[search])})
    [task] = build_tasks(cfg, load_registry(None), Selection(sources=[site]))
    return task.plugin, task.searches[0]


def qs(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_linkedin_search_url_uses_the_newer_pages_real_filters():
    search = SourceSearch(name="s", keywords=["ml engineer"], locations=["United States"], posted_within_days=7,
                          workplace_types=["remote"], employment_types=["full_time", "contract"], experience_levels=["entry"])
    li, rs = resolved(search, "linkedin")
    [q] = li.build_queries(rs)
    assert q.url.startswith("https://www.linkedin.com/jobs/search-results/?")
    # nothing is written into the keywords; every filter is one of LinkedIn's own URL parameters
    assert qs(q.url) == {"keywords": "ml engineer", "geoId": "103644278", "f_TPR": "r604800",
                         "f_SAL": "f_SA_id_225001:272001$f_SA_id_226001:272015,274001$f_SA_id_227001:276001"}
    assert qs(li.page_url(q.url, 2))["start"] == "50"
    assert li.detail_url("123") == "https://www.linkedin.com/jobs/view/123/"
    assert not li.sorted_by_date  # relevance order → no early stop


def test_linkedin_site_filters_and_what_goes_to_step2():
    search = SourceSearch(name="s", keywords=["applied scientist"], posted_within_days=3,
                          workplace_types=["remote", "hybrid"], employment_types=["full_time", "temporary"],
                          experience_levels=["entry"],
                          site_filters={"experience_level": ["senior", "manager"], "easy_apply": True,
                                        "under_10_applicants": True, "in_my_network": True, "geo_id": "90000084"})
    cfg = AppConfig(sources={"linkedin": SourceConfig(searches=[search])})
    [task] = build_tasks(cfg, load_registry(None), Selection(sources=["linkedin"]))
    [q] = task.plugin.build_queries(task.searches[0])
    assert qs(q.url) == {"keywords": "applied scientist", "geoId": "90000084", "f_TPR": "r604800",  # 3 days → past week
                         "f_SAL": "f_SA_id_227001:277001,278001",  # site_filters level replaces `entry`
                         "f_AL": "true", "f_EA": "true", "f_JIYN": "true"}
    notes = " | ".join(task.warnings)
    assert "temporary" in notes and "only filters Remote" in notes and "past 7 days" in notes


def test_linkedin_unknown_location_needs_a_geo_id():
    import pytest

    from auto_app.collect.plan import ConfigError

    search = SourceSearch(name="s", keywords=["x"], locations=["Seattle, WA"])
    with pytest.raises(ConfigError, match="location id for \\['Seattle, WA'\\].*geo_id"):
        resolved(search, "linkedin")


def test_site_url_override_replaces_generated_queries():
    search = SourceSearch(
        name="s", keywords=["a", "b"], site_url="https://jobright.ai/jobs/search?x=1"
    )
    jr, rs = resolved(search, "jobright")
    [q] = jr.build_queries(rs)
    assert q.url == "https://jobright.ai/jobs/search?x=1"


def test_handshake_base_url_override_and_job_ids():
    hs = get_adapter("handshake", "https://northeastern.joinhandshake.com")
    assert hs.detail_url("42") == "https://northeastern.joinhandshake.com/jobs/42"
    assert hs.is_site_host("https://northeastern.joinhandshake.com/jobs/42")
    assert hs.job_id_from_url("https://app.joinhandshake.com/job-search/9876?page=1") == "9876"
    assert qs(hs.page_url(hs.spec.url("{base}/job-search?query=ml&page=1"), 1))["page"] == "2"


def test_job_id_from_url():
    assert get_adapter("linkedin").job_id_from_url("https://www.linkedin.com/jobs/view/4012345/?refId=x") == "4012345"
    assert get_adapter("jobright").job_id_from_url("https://jobright.ai/jobs/info/66f1a2b3c4") == "66f1a2b3c4"
