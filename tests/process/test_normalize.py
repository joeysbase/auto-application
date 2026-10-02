import json
from pathlib import Path

import pytest

from auto_app.collect.store import PostingRef
from auto_app.process.normalize import (
    CompanyAliases,
    clean_description,
    company_basic,
    location_norm,
    normalize_postings,
    title_norm,
)
from auto_app.process.similarity import containment, containment_estimate, jaccard_estimate, minhash, shingles
from auto_app.sources.common.urls import employer_job_key
from auto_app.platforms import PLATFORMS
from tests.helpers import test_registry

FIX = Path(__file__).parents[1] / "fixtures"

# Real resolved LinkedIn apply links (from collected data) → expected keys
LINKEDIN_APPLY = {
    "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-TX-Austin/ASIC-Verification-Engineer---New-College-Grad-2027_JR2026343?source=jobboardlinkedin":
        "workday:nvidia/nvidiaexternalcareersite:JR2026343",
    "https://draper.wd5.myworkdayjobs.com/en-US/Draper_Careers/job/Cambridge-MA/Requirements-Engineer_JR002937?source=LinkedIn":
        "workday:draper/draper_careers:JR002937",
    "https://gladstone.wd503.myworkdayjobs.com/careers/job/San-Francisco/Research-Associate-I---Theodoris-Lab_REQ-3930?source=LinkedIn":
        "workday:gladstone/careers:REQ-3930",
    "https://psu.wd1.myworkdayjobs.com/PSU_Academic/job/Penn-State-University-Park/Assistant-Professor_REQ_0000082877-1?x=1":
        "workday:psu/psu_academic:REQ_0000082877-1",
    "https://jobs.ashbyhq.com/seqholdings/91eed659-a4e6-49f8-9b1f-e92759deb9b9/application?utm_source=GznzgD5z39":
        "ashby:seqholdings:91eed659-a4e6-49f8-9b1f-e92759deb9b9",
    "https://boards.greenhouse.io/spacex/jobs/8854394002?gh_jid=8854394002&gh_src=3c0864192": "greenhouse:*:8854394002",
    "https://www.example-company.com/careers/listing?gh_jid=4461450008": "greenhouse:*:4461450008",
    "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply": "lever:palantir:6ed76ce8-4156-4b60-b120-403538bd66cd",
}


@pytest.mark.parametrize("url,key", LINKEDIN_APPLY.items())
def test_employer_job_key_on_real_apply_links(url, key):
    assert employer_job_key(url).key == key


def test_employer_key_not_found_for_other_sites():
    assert employer_job_key("https://careers.leidos.com/jobs/123") is None
    assert employer_job_key(None) is None


def test_company_posting_urls_give_the_same_key_as_linkedin_links():
    """The property dedup depends on: both sides of a duplicate produce the identical key."""
    wd = PLATFORMS["workday"]()
    p = json.loads((FIX / "workday/nvidia_search.json").read_text())["jobPostings"][0]
    job = wd.to_job(p, "nvidia.wd5.myworkdayjobs.com", "NVIDIAExternalCareerSite")
    linkedin_style = job.url.replace("/NVIDIAExternalCareerSite/", "/en-US/NVIDIAExternalCareerSite/") + "?source=LinkedIn"
    assert employer_job_key(job.url) == employer_job_key(linkedin_style)
    assert employer_job_key(job.url).job_id == job.id  # plugin ids and key ids agree
    a = json.loads((FIX / "ashby/uipath.json").read_text())["jobs"][0]
    assert employer_job_key(a["jobUrl"]) == employer_job_key(a["applyUrl"])
    g = json.loads((FIX / "greenhouse/anthropic.json").read_text())["jobs"][0]
    assert employer_job_key(g["absolute_url"]).job_id == str(g["id"])


def test_company_names_and_aliases():
    assert company_basic("Vendra (YC S24)") == "vendra"
    assert company_basic("NVIDIA Corporation") == "nvidia"
    assert company_basic("Acme, Inc.") == "acme"
    al = CompanyAliases(test_registry().companies())
    key = employer_job_key(next(iter(LINKEDIN_APPLY)))
    assert al.resolve("NVIDIA Corp", key, None) == ("nvidia", "board")
    assert al.learned == {}  # "nvidia corp" already normalizes to "nvidia": nothing to learn
    assert al.resolve("NVIDIA Graphics", key, None) == ("nvidia", "board")
    assert al.learned == {"nvidia graphics": "nvidia"}  # learned from the board match
    assert al.resolve("NVIDIA Graphics", None, None) == ("nvidia", "learned")  # e.g. an Easy Apply posting later
    assert al.resolve("Nvidia", None, None) == ("nvidia", "alias")
    assert al.resolve("Someone Else LLC", None, None) == ("someone else", "name")


def test_titles_and_locations():
    assert title_norm("Sr. ML Engineer (Remote)") == "senior machine learning engineer"
    assert title_norm("AI/ML Engineer") == "ai machine learning engineer"
    assert title_norm("AI Engineer II | Remote") == "ai engineer ii"
    assert title_norm("Data Engineer/AI Solution Engineer") == "data engineer ai solution engineer"
    assert location_norm("US, CA, Santa Clara") == location_norm("Santa Clara, CA") == "santa clara, ca"
    assert location_norm("New York, NY") == "new york, ny"
    assert location_norm("California, United States") == "ca, us"
    assert location_norm("Washington, D.C.") == "washington, dc"
    assert location_norm("Remote-Friendly, United States") == "remote-us"


def test_boilerplate_does_not_affect_comparison():
    job = "We build GPUs.\nYou will write CUDA kernels.\nRequirements: C++, 2 years."
    a = job + "\nWe are an equal opportunity employer and value diversity."
    b = job.replace("\n", "\n\n") + "\nApplicants with disabilities may request reasonable accommodation."
    assert clean_description(a) == clean_description(b)


def test_similarity_signals():
    text = " ".join(f"word{i}" for i in range(300))
    other = " ".join(f"other{i}" for i in range(300))
    s1, s2, s3 = shingles(text), shingles(" ".join(text.split()[:150])), shingles(other)
    m1, m2, m3 = minhash(s1), minhash(s2), minhash(s3)
    assert jaccard_estimate(m1, m1) == 1.0 and jaccard_estimate(m1, m3) < 0.05
    assert containment(s1, s2) == 1.0  # a truncated copy is fully contained
    assert containment_estimate(m1, m2, len(s1), len(s2)) > 0.8


def test_stage_links_linkedin_to_company_and_versions(store):
    from auto_app.core.models import CommonPosting
    from auto_app.sources.api import RawJob

    reg = test_registry()
    url = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-TX-Austin/X_JR2026343"
    desc = "About the role\n" + " ".join(f"duty{i}" for i in range(200))
    for source, jid, parsed in (
        ("nvidia", "JR2026343", {"title": "ASIC Verification Engineer", "company": "NVIDIA", "url": url,
                                 "apply_url": url, "description_text": desc, "locations": ["US, TX, Austin"]}),
        ("linkedin", "4471585027", {"title": "ASIC Verification Engineer", "company": "NVIDIA Graphics",
                                    "apply_url": url + "?source=jobboardlinkedin", "description_text": desc,
                                    "locations": ["Austin, TX"]}),
        ("linkedin", "999", None),
    ):
        store.save_api_posting(source, RawJob(id=jid, url=url, title="x", record={}), None, platform="t", board=None,
                               batch_id="b", search_run_id=1)
        if parsed:
            store.save_parsed(PostingRef(source, jid), CommonPosting(source=source, source_job_id=jid, **parsed), "t:1")

    stats = normalize_postings(store, reg)
    assert stats["nvidia"].normalized == 1 and stats["linkedin"].normalized == 1 and stats["linkedin"].not_parsed == 1
    n_li = store.get_normalized(PostingRef("linkedin", "4471585027"))
    n_co = store.get_normalized(PostingRef("nvidia", "JR2026343"))
    assert (n_li["company_norm"], n_li["company_via"]) == ("nvidia", "board")
    assert n_li["employer_key"] == n_co["employer_key"] and n_li["desc_hash"] == n_co["desc_hash"]
    assert n_li["location_norm"] == n_co["location_norm"] == ["austin, tx"]
    assert store.load_aliases() == {"nvidia graphics": "nvidia"}

    again = normalize_postings(store, reg)
    assert again["linkedin"].skipped == 1 and again["linkedin"].normalized == 0  # up to date
    assert normalize_postings(store, reg, again=True)["linkedin"].normalized == 1
