import pytest

from auto_app.collect.plan import ConfigError, Selection, build_tasks
from auto_app.core.config import AppConfig
from auto_app.platforms import AshbySource, WorkdaySource
from auto_app.sources.registry import Registry
from tests.helpers import TEST_PLUGINS, test_registry

REG = test_registry()  # built-in job sites + company sources uipath, nvidia (tests/plugins)

LINKEDIN = {"searches": [{"name": "ml", "keywords": ["machine learning engineer"], "locations": ["United States"],
                          "experience_levels": ["entry"]}]}
BASE = {"defaults": {"posted_within_days": 2, "card_filters": {"exclude_title_words": ["senior"]}}}


def cfg(**sources) -> AppConfig:
    return AppConfig.model_validate({**BASE, "sources": {"linkedin": LINKEDIN, **sources}})


def test_from_copies_another_sources_search():
    site, co = build_tasks(cfg(uipath={"searches": [{"from": "linkedin/ml"}]}), REG)
    assert site.source == "linkedin" and site.kind == "job_site" and site.searches[0].levels["experience_levels"] == "native"
    assert co.kind == "company" and co.display_name == "UiPath" and co.platform == "ashby" and co.board == "uipath"
    [s] = co.searches
    assert s.name == "ml"  # defaults to the copied search's name
    assert s.keywords == ["machine learning engineer"] and s.locations == ["United States"]
    assert s.posted_within_days == 2 and s.card_filters.exclude_title_words == ["senior"]
    assert s.levels == {"keywords": "local", "locations": "local", "posted_within_days": "local",
                        "experience_levels": "deferred"}
    assert any("experience_levels" in w and "linkedin/ml" in w for w in co.warnings)  # copied → deferred, with a notice


def test_own_fields_override_the_copy_and_site_filters_are_validated():
    [co] = build_tasks(cfg(uipath={"searches": [{"name": "research", "from": "linkedin/ml", "keywords": ["applied scientist"],
                                                 "experience_levels": [], "site_filters": {"departments": ["Engineering"]}}]}),
                       REG, Selection(job_sites=False))
    [s] = co.searches
    assert s.name == "research" and s.keywords == ["applied scientist"] and s.locations == ["United States"]
    assert s.experience_levels == [] and not co.warnings  # an empty list clears the copied field
    assert s.site_filters.departments == ["Engineering"]


def test_several_named_searches_per_source():
    [co] = build_tasks(cfg(nvidia={"searches": [{"name": "a", "keywords": ["x"]}, {"name": "b", "keywords": ["y"]}]}),
                       REG, Selection(sources=["nvidia"]))
    assert [s.name for s in co.searches] == ["a", "b"]
    only = build_tasks(cfg(nvidia={"searches": [{"name": "a"}, {"name": "b"}]}), REG,
                       Selection(sources=["nvidia"], search_names=["b"]))[0].searches
    assert [s.name for s in only] == ["b"]
    with pytest.raises(ValueError, match="unique"):
        cfg(nvidia={"searches": [{"name": "a"}, {"name": "a"}]})
    with pytest.raises(ValueError, match="needs a `name`"):
        cfg(nvidia={"searches": [{"keywords": ["x"]}]})


def test_explicit_unsupported_field_is_an_error():
    with pytest.raises(ConfigError, match="nvidia/s: `experience_levels` is not supported by nvidia"):
        build_tasks(cfg(nvidia={"searches": [{"name": "s", "experience_levels": ["entry"]}]}), REG)


def test_bad_site_filters_from_and_unknown_sources():
    with pytest.raises(ConfigError, match="site_filters.*departmentz"):
        build_tasks(cfg(uipath={"searches": [{"name": "s", "site_filters": {"departmentz": ["x"]}}]}), REG)
    with pytest.raises(ConfigError, match="linkedin has no search 'nope'"):
        build_tasks(cfg(uipath={"searches": [{"from": "linkedin/nope"}]}), REG)
    with pytest.raises(ConfigError, match="should be <source>/<search>"):
        build_tasks(cfg(uipath={"searches": [{"name": "s", "from": "ml"}]}), REG)
    with pytest.raises(ConfigError, match="`from` loops"):
        build_tasks(cfg(uipath={"searches": [{"name": "a", "from": "nvidia/b"}]},
                        nvidia={"searches": [{"name": "b", "from": "uipath/a"}]}), REG)
    with pytest.raises(ConfigError, match="no plugin named 'acme'"):
        build_tasks(cfg(acme={"searches": [{"name": "s"}]}), REG)
    with pytest.raises(ConfigError, match="site_url` is only for browser sources"):
        build_tasks(cfg(uipath={"searches": [{"name": "s", "site_url": "https://x"}]}), REG)


def test_selection_and_sources_without_searches():
    c = cfg(uipath={"searches": [{"from": "linkedin/ml"}]}, nvidia={})
    assert [t.source for t in build_tasks(c, REG)] == ["linkedin", "uipath"]  # nvidia: no searches → doesn't run
    assert [t.source for t in build_tasks(c, REG, Selection(sources=["uipath"]))] == ["uipath"]
    assert [t.source for t in build_tasks(c, REG, Selection(companies=False))] == ["linkedin"]
    assert [t.source for t in build_tasks(c, REG, Selection(job_sites=False))] == ["uipath"]
    with pytest.raises(ConfigError, match="'nvidia' has no searches"):
        build_tasks(c, REG, Selection(sources=["nvidia"]))
    with pytest.raises(ConfigError, match="no search named \\['typo'\\]"):
        build_tasks(c, REG, Selection(search_names=["typo"]))


def test_limits_per_source():
    c = cfg(nvidia={"limits": {"max_details_per_hour": 7}})
    assert c.limits("nvidia").max_details_per_hour == 7 and c.limits("nvidia").max_details_per_day == 600
    li = AppConfig.model_validate({"sources": {"linkedin": {"limits": {"max_details_per_hour": 5}}}}).limits("linkedin")
    assert li.max_details_per_hour == 5 and li.max_details_per_day == 300  # built-in LinkedIn default kept


def test_job_sites_and_companies_layout_is_migrated():
    old = AppConfig.model_validate({
        "job_sites": {"searches": [{"name": "ml", "sites": ["linkedin", "handshake"], "keywords": ["x"],
                                    "site_url": {"handshake": "https://hs"}}],
                      "limits": {"linkedin": {"max_details_per_hour": 5}}},
        "companies": {
            "uipath": {"platform": "ashby", "board": "uipath", "search_from": ["ml"]},
            "nvidia": {"platform": "workday", "board": "https://n.wd5.myworkdayjobs.com/S", "search": {"keywords": ["y"]}},
            "acme": {"platform": "greenhouse", "board": "acme", "display_name": "Acme"},
        },
    })
    assert [s.search_name for s in old.sources["linkedin"].searches] == ["ml"]
    assert old.sources["handshake"].searches[0].site_url == "https://hs" and old.sources["linkedin"].searches[0].site_url is None
    assert old.limits("linkedin").max_details_per_hour == 5
    assert old.sources["uipath"].searches[0].from_ == "linkedin/ml"
    assert old.sources["nvidia"].searches[0].name == "main" and old.sources["nvidia"].searches[0].keywords == ["y"]
    assert old.sources["acme"].searches[0].from_ == "linkedin/ml"  # no search_from: copies every job-site search
    # a company without a plugin file: the error says how to create it
    with pytest.raises(ConfigError, match="auto-app add-company acme --name acme --company 'Acme'"):
        build_tasks(old, REG, Selection(sources=["acme"]))


def test_oldest_layout_is_migrated():
    old = AppConfig.model_validate({
        "searches": [{"name": "s", "sources": ["linkedin"], "keywords": ["x"], "site_urls": {"linkedin": "https://u"}}],
        "sources": {"linkedin": {"max_details_per_hour": 5}},
    })
    [s] = old.sources["linkedin"].searches
    assert s.name == "s" and s.site_url == "https://u"
    assert old.limits("linkedin").max_details_per_hour == 5
    assert old.limits("linkedin").max_details_per_day == 300


def test_registry_checks_plugins_when_loading(tmp_path):
    reg = test_registry()
    assert set(reg.companies()) == {"uipath", "nvidia"}
    assert reg.get("nvidia").board == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"  # normalized
    assert "WorkdaySource" not in {i.cls.__name__ for i in reg.plugins.values()}  # bases aren't sources

    class NoBoard(WorkdaySource):
        name = "noboard"
        company = "X"

    class NoCompany(AshbySource):
        name = "nocompany"
        board = "x"

    for cls, msg in ((NoBoard, "`board` is required"), (NoCompany, "set `company`")):
        with pytest.raises(ValueError, match=msg):
            Registry().add_class(cls)

    (tmp_path / "companies").mkdir()
    (tmp_path / "companies" / "dup.py").write_text((TEST_PLUGINS / "companies" / "uipath.py").read_text())
    (tmp_path / "companies" / "uipath.py").write_text((TEST_PLUGINS / "companies" / "uipath.py").read_text())
    (tmp_path / "companies" / "bad.py").write_text("import nonexistent_module\n")
    from auto_app.sources.registry import load_registry

    loaded = load_registry(tmp_path)
    assert "uipath" in loaded.plugins  # the first file wins; the duplicate and the broken file are reported
    assert any("defined twice" in v for v in loaded.broken.values())
    assert any("nonexistent_module" in v for v in loaded.broken.values())


def test_posted_within_days_all_means_no_date_limit():
    from datetime import datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlparse

    import pydantic

    from auto_app.collect.plan import format_plan
    from auto_app.sources.api import RawJob

    c = AppConfig.model_validate({**BASE, "defaults": {"posted_within_days": "all"},
                                  "sources": {"linkedin": LINKEDIN,
                                              "uipath": {"searches": [{"name": "s", "keywords": ["engineer"]}]}}})
    site, co = build_tasks(c, REG)
    assert site.searches[0].posted_within_days is None and co.searches[0].posted_within_days is None
    assert "posted_within_days" not in site.searches[0].levels
    assert "all (no date limit)" in format_plan([site, co])
    [q] = site.plugin.build_queries(site.searches[0])
    assert "f_TPR" not in parse_qs(urlparse(q.url).query)  # LinkedIn: no "date posted" filter
    old = RawJob(id="1", url="u", title="Engineer", record={}, posted_at=datetime.now(timezone.utc) - timedelta(days=900))
    assert co.plugin.local_filter(old, co.searches[0]) is None  # an old company job still matches

    # a number still overrides "all" in one search, and 0 is rejected
    c2 = AppConfig.model_validate({**BASE, "defaults": {"posted_within_days": "all"},
                                   "sources": {"uipath": {"searches": [{"name": "s", "posted_within_days": 3}]}}})
    assert build_tasks(c2, REG)[0].searches[0].posted_within_days == 3
    with pytest.raises(pydantic.ValidationError):
        AppConfig.model_validate({"defaults": {"posted_within_days": 0}})
