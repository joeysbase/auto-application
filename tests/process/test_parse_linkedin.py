"""LinkedIn parser against real pages (trimmed to header + description, personal data removed)."""

import json
from pathlib import Path

import pytest

from auto_app.core.models import RawPosting
from auto_app.sources.linkedin.parser import parse_linkedin
from auto_app.sources.parsing import ParseError

FIX = Path(__file__).parents[1] / "fixtures" / "linkedin"


def raw(job_id: str, page_text: str | None = None) -> RawPosting:
    d = FIX / job_id
    return RawPosting(
        source="linkedin",
        source_job_id=job_id,
        kind="browser",
        posting={},
        capture=json.loads((d / "capture.json").read_text()),
        card=json.loads((d / "card.json").read_text()),
        page_text=page_text if page_text is not None else (d / "page_text.txt").read_text(),
    )


def test_reposted_job_with_header_salary_and_external_apply():
    p = parse_linkedin(raw("4456554369"))
    assert p.title == "Spacecraft Mechanical Engineer (Early)"
    assert p.company == "Apex"
    assert p.locations == ["Los Angeles, CA"]
    assert p.reposted and p.reposted_at == p.posted_at and p.posted_text == "Reposted 1 hour ago"
    assert p.applicants == 86
    assert (p.salary.min, p.salary.max, p.salary.period, p.salary.source) == (100_000, 140_000, "year", "header")
    assert (p.workplace_type, p.employment_type) == ("onsite", "full_time")
    assert p.apply_kind == "external" and p.apply_url.startswith("https://jobs.ashbyhq.com/apex-technology-inc/")
    assert p.extras["responses_managed_off_linkedin"] is True
    assert p.description_text.startswith("Company Description") or len(p.description_text) > 1000
    assert "… more" not in p.description_text and "Set alert" not in p.description_text


def test_easy_apply_hybrid():
    p = parse_linkedin(raw("4471239042"))
    assert p.company == "Saragossa" and p.locations == ["Chicago, IL"]
    assert (p.workplace_type, p.employment_type, p.apply_kind) == ("hybrid", "full_time", "easy_apply")
    assert p.applicants == 29 and not p.reposted
    assert p.extras["promoted"] is True


def test_hourly_salary_and_already_applied():
    p = parse_linkedin(raw("4471214120"))
    assert p.title == "Entry-Level AI/ML Engineer" and p.company == "Enhance IT"
    assert (p.salary.min, p.salary.max, p.salary.period) == (30, 35, "hour")
    assert p.applicants == 100 and p.applicants_text == "Over 100 applicants"
    assert p.extras["already_applied"] is True


def test_missing_section_is_a_parse_error():
    with pytest.raises(ParseError, match="About the job"):
        parse_linkedin(raw("4471239042", page_text="Sign in to view this job"))
