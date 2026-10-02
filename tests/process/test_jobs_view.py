from auto_app.process.jobs_view import format_job, format_job_list, place, places


def job(i, title, company, posted, locs, sources, state="not_evaluated", **kw):
    return {"job_id": i, "title": title, "company": company, "company_norm": company.lower(), "posted_at": posted,
            "first_seen_at": posted, "last_seen_at": posted, "locations": locs, "sources": sources, "state": state,
            "canonical_url": "https://x/1", "postings": len(sources), "expires_at": None, "warnings": [], **kw}


def test_places_read_naturally():
    assert place("austin, tx") == "Austin, TX"
    assert place("ca, us") == "CA, US"
    assert place("new york city metropolitan area") == "New York City Metropolitan Area"
    assert place("remote-us") == "Remote (US)" and place("us") == "United States"
    four = ["austin, tx", "santa clara, ca", "seattle, wa", "westford, ma"]
    assert places(four, 32) == "Austin, TX; Santa Clara, CA +2" and places(four, 16) == "Austin, TX +3"


def test_list_is_newest_first_fits_the_width_and_hides_a_uniform_status():
    rows = [job(1, "Old Job", "Acme", "2026-09-01T00:00:00+00:00", ["boston, ma"], ["linkedin"]),
            job(2, "Research Scientist, ML Systems - PhD New College Grad 2026 " + "x" * 80, "NVIDIA",
                "2026-09-28T00:00:00+00:00", ["austin, tx", "santa clara, ca"], ["linkedin", "nvidia"])]
    text = format_job_list(rows, width=120)
    lines = text.splitlines()
    assert lines[0] == "2 jobs · 1 found on more than one source · newest first"
    body = lines[4:6]  # after the summary, a blank line, the header and the rule
    assert [b.split()[0] for b in body] == ["2", "1"]  # newest first
    assert all(len(line) <= 120 for line in lines) and "…" in body[0]
    assert "state" not in lines[2]  # every job in the same state: no state column
    rows[0]["state"] = "applied"
    assert "state" in format_job_list(rows, width=120).splitlines()[2]


def test_job_details_explain_each_posting():
    j = job(28, "Research Scientist", "NVIDIA", "2026-02-05T00:00:00+00:00", ["austin, tx"], ["nvidia", "linkedin"])
    postings = [
        {"source": "nvidia", "source_job_id": "JR1", "apply_kind": "external", "match_rule": "new_job",
         "match_confidence": None, "duplicate_of": None, "title": "Research Scientist"},
        {"source": "linkedin", "source_job_id": "44", "apply_kind": "external", "match_rule": "employer_key",
         "match_confidence": 1.0, "duplicate_of": None, "title": "Research Scientist (AI)"},
        {"source": "linkedin", "source_job_id": "45", "apply_kind": "external", "match_rule": "1b:employer_key",
         "match_confidence": 1.0, "duplicate_of": "linkedin/44", "title": "Research Scientist"},
    ]
    text = format_job(j, postings, [("employer_key", "workday:nvidia/x:JR1")])
    assert "company  NVIDIA" in text and "where    Austin, TX" in text and "posted   2026-02-05" in text
    assert "first posting of this job" in text and "same job id on the employer's system (1.00)" in text
    assert "repeat of linkedin/44 on the same source" in text
    assert "title: Research Scientist (AI)" in text  # a posting titled differently from the job is shown
