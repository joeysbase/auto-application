"""Platform parsers against saved real API records."""

import json
from pathlib import Path

import pytest

from auto_app.core.models import RawPosting
from auto_app.sources.common.text import html_to_text, parse_salary
from auto_app.sources.parsing import ParseError
from auto_app.platforms import PLATFORMS

FIX = Path(__file__).parents[1] / "fixtures"


def load(rel):
    return json.loads((FIX / rel).read_text())


def raw(source, job_id, record, detail=None, hint=None, url="https://example.com/job"):
    return RawPosting(source=source, source_job_id=job_id, kind="api", posting={}, company_hint=hint,
                      capture={"final_url": url, "apply_kind": "external"}, card={}, record=record, detail=detail)


def test_salary_and_html_helpers():
    assert parse_salary("116,000 USD - 184,000 USD for Level 2", "d").max == 184_000
    s = parse_salary("$90/yr - $120/yr", "header")
    assert s.max == 120 and s.plausible is False  # kept as shown, flagged
    assert html_to_text("&lt;p&gt;a&lt;/p&gt;&lt;ul&gt;&lt;li&gt;b&lt;/li&gt;&lt;/ul&gt;") == "a\n\n- b"


def test_ashby_structured_salary():
    job = next(j for j in load("ashby/uipath.json")["jobs"] if j["title"].strip() == "Principal Applied Scientist")
    p = PLATFORMS["ashby"]().parse(raw("uipath", job["id"], job, hint="UiPath"))
    assert p.company == "UiPath" and p.title == "Principal Applied Scientist"
    assert (p.salary.min, p.salary.max, p.salary.period, p.salary.source) == (200_000, 250_000, "year", "structured")
    assert p.employment_type == "full_time" and p.posted_at_precision == "exact"
    assert len(p.description_text) > 1000 and p.extras["department"] == "Engineering"


def test_greenhouse_unescapes_content_and_reads_location_type():
    job = load("greenhouse/anthropic.json")["jobs"][0]
    p = PLATFORMS["greenhouse"]().parse(raw("anthropic", str(job["id"]), job, hint="Anthropic"))
    assert p.company == "Anthropic" and p.req_id == job["requisition_id"]
    assert "&lt;" not in p.description_text and "<" not in p.description_text
    assert p.workplace_type == "onsite"  # metadata "Location Type: On-Site"


def test_lever_joins_description_lists():
    job = load("lever/palantir.json")[2]
    p = PLATFORMS["lever"]().parse(raw("palantir", job["id"], job, hint="Palantir"))
    assert p.title.startswith("Backend Software Engineer")
    assert "What We Require" in p.description_text or "Core Responsibilities" in p.description_text
    assert p.employment_type == "full_time" and p.url == job["hostedUrl"]


def test_workday_detail_salary_and_company_name():
    record = load("workday/nvidia_search.json")["jobPostings"][0]
    detail = load("workday/nvidia_detail.json")
    p = PLATFORMS["workday"]().parse(raw("nvidia", "JR2016809", record, detail, hint="NVIDIA"))
    assert p.company == "NVIDIA" and p.extras["hiring_entity"]  # config name over the legal entity
    assert p.req_id == detail["jobPostingInfo"]["jobReqId"]
    assert p.employment_type == "full_time" and p.posted_at_precision == "day"
    assert len(p.description_text) > 1000


def test_workday_without_detail_fails_clearly():
    with pytest.raises(ParseError, match="no job detail"):
        PLATFORMS["workday"]().parse(raw("nvidia", "x", {"externalPath": "/job/x_1"}))
