"""Dedup rules and grouping on constructed postings (real overlap data is still thin)."""

from datetime import datetime, timedelta, timezone

from auto_app.process.dedup import Item, dedup, format_preview, title_similarity
from auto_app.process.normalize import clean_description
from auto_app.process.similarity import minhash, shingles

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
JOB_A = " ".join(f"gpu{i} kernels cuda team{i % 7}" for i in range(120))  # one job's description
JOB_B = " ".join(f"sales{i} quota region{i % 5}" for i in range(120))  # a different team's description


def item(pid, *, company="nvidia", title="machine learning engineer", desc=JOB_A, key=None, req=None,
         employment="full_time", apply_kind="external", days_ago=0, locations=("santa clara, ca",)):
    source, jid = pid.split("/")
    cleaned = clean_description(desc)
    sh = shingles(cleaned)
    import hashlib
    return Item(pid=pid, source=source, source_job_id=jid, is_company_source=source not in ("linkedin", "handshake"),
                title=title.title(), company=company, title_norm=title, employer_key=key, req_id=req,
                desc_hash=hashlib.sha1(cleaned.encode()).hexdigest() if cleaned else None, desc_chars=len(cleaned),
                shingle_set=sh, minhash=minhash(sh), shingle_count=len(sh), employment_type=employment,
                locations=list(locations), apply_kind=apply_kind, posted_at=NOW - timedelta(days=days_ago), url=None)


def groups_of(result):
    return sorted(sorted(g.all_postings) for g in result.merged)


def test_linkedin_and_company_posting_merge_on_employer_key_company_is_primary():
    items = {i.pid: i for i in [
        item("nvidia/JR1", key="workday:nvidia/site:JR1", apply_kind="external"),
        item("linkedin/111", key="workday:nvidia/site:JR1", title="ml engineer", locations=("austin, tx",)),
    ]}
    r = dedup(items)
    [g] = r.merged
    assert g.primary == "nvidia/JR1"  # the company's own posting is the source of truth
    assert [(l.rule, l.confidence) for l in g.links] == [("employer_key", 1.0)]
    assert g.locations == ["austin, tx", "santa clara, ca"]


def test_easy_apply_copy_merges_on_description_and_same_title():
    items = {i.pid: i for i in [
        item("nvidia/JR2", key="workday:nvidia/site:JR2"),
        item("linkedin/222", apply_kind="easy_apply"),  # no employer key; same title, same description
    ]}
    [g] = dedup(items).merged
    assert g.links[0].rule == "description" and g.links[0].confidence == 0.9
    assert g.primary == "nvidia/JR2"


def test_different_titles_are_different_jobs_even_with_the_same_description():
    """The poster titled them differently, so they're different openings (NVIDIA: Grad 2026 vs 2027)."""
    items = {i.pid: i for i in [
        item("nvidia/JR2012573", key="workday:nvidia/site:JR2012573",
             title="asic verification engineer new college grad 2026", days_ago=100),
        item("nvidia/JR2026343", key="workday:nvidia/site:JR2026343",
             title="asic verification engineer new college grad 2027"),
        item("linkedin/333", title="asic verification engineer new college grad 2027"),  # same title as JR2026343
    ]}
    r = dedup(items)
    assert groups_of(r) == [["linkedin/333", "nvidia/JR2026343"]]  # only the same-titled pair merges
    assert any("titles differ" in why for _, _, why in r.near_misses)


def test_same_employer_key_overrides_a_title_difference():
    items = {i.pid: i for i in [
        item("nvidia/JR9", key="workday:nvidia/site:JR9", title="machine learning engineer"),
        item("linkedin/999", key="workday:nvidia/site:JR9", title="machine learning engineer ai"),  # retitled copy
    ]}
    assert groups_of(dedup(items)) == [["linkedin/999", "nvidia/JR9"]]


def test_same_title_different_team_is_not_merged_and_reported():
    items = {i.pid: i for i in [item("nvidia/JR3", key="k3"), item("nvidia/JR4", key="k4", desc=JOB_B)]}
    r = dedup(items)
    assert r.merged == []
    assert any("same title" in why and "descriptions differ" in why for _, _, why in r.near_misses)


def test_veto_blocks_chaining_across_groups():
    # A ~ B (same employer key) and B ~ C (same req id, no conflict between B and C) —
    # but A (full time) and C (internship) conflict, so C is refused from A's group.
    items = {i.pid: i for i in [
        item("nvidia/A", key="K", req="R1", employment="full_time"),
        item("linkedin/B", key="K", req="R9", employment="unknown"),
        item("handshake/C", req="R9", employment="internship"),
    ]}
    r = dedup(items)
    assert groups_of(r) == [["linkedin/B", "nvidia/A"]]
    assert any("blocked" in why and "handshake/C" in why for _, _, why in r.near_misses)


def test_level1b_duplicates_take_part_in_merge_vetoes():
    # C is a same-source duplicate of B (identical LinkedIn description) but labelled an internship;
    # merging B's group with the full-time company posting A must be checked against C too.
    items = {i.pid: i for i in [
        item("nvidia/A", key="K", employment="full_time"),
        item("linkedin/B", key="K", employment="unknown"),
        item("linkedin/C", employment="internship"),
    ]}
    r = dedup(items)
    assert groups_of(r) == [["linkedin/B", "linkedin/C"]]  # A is not merged with the internship
    assert any("linkedin/C and nvidia/A" in why and "internship" in why for _, _, why in r.near_misses)


def test_employment_type_veto():
    items = {i.pid: i for i in [item("nvidia/X"), item("linkedin/Y", employment="internship")]}
    assert dedup(items).merged == []


def test_level1b_repost_and_probable_match():
    items = {i.pid: i for i in [
        item("linkedin/old", days_ago=10),
        item("linkedin/new", days_ago=0),  # same company + title + description, new id → repost
        item("adobe/R1", company="adobe", title="data scientist", desc=""),  # thin posting
        item("linkedin/555", company="adobe", title="data scientist", desc="", days_ago=3),
    ]}
    r = dedup(items)
    by = {tuple(sorted(g.all_postings)): g for g in r.merged}
    repost = by[("linkedin/new", "linkedin/old")]
    assert repost.duplicates == {"linkedin/new": ("linkedin/old", "desc_hash")}  # the earlier posting is canonical
    thin = by[("adobe/R1", "linkedin/555")]
    assert thin.links[0].rule == "probable" and thin.links[0].confidence == 0.6


def test_title_similarity_and_report():
    assert title_similarity("machine learning engineer", "senior machine learning engineer") == 100
    assert title_similarity("data scientist", "software engineer") == 0
    items = {i.pid: i for i in [item("nvidia/JR1", key="K"), item("linkedin/1", key="K")]}
    text = format_preview(dedup(items))
    assert "2 postings → 2 after Level 1b (same source) → 1 jobs after Level 2" in text
    assert "★ nvidia/JR1" in text and "employer_key (1.0)" in text


def test_description_rule_needs_overlap_of_095():
    words = JOB_A.split()
    almost = " ".join(words[:-30] + [f"other{i}" for i in range(30)])  # overlap ≈ 0.94
    items = {i.pid: i for i in [item("nvidia/JR5"), item("linkedin/555", desc=almost)]}
    r = dedup(items)
    assert r.merged == []
    assert any("between 0.5 and 0.95" in why for _, _, why in r.near_misses)
    items["linkedin/555"] = item("linkedin/555")  # identical description → merges
    assert groups_of(dedup(items)) == [["linkedin/555", "nvidia/JR5"]]
