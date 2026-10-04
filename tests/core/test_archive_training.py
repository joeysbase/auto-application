"""S4 part 1: judged jobs are archived as training data (never deleted); never-judged jobs are deleted; a listing
seen again after its job was archived becomes a new job; archived jobs are deduplicated among themselves."""

from auto_app.core.config import AppConfig
from auto_app.core.db.purge import purge
from tests.core.test_job_ttl import seen_days_ago
from tests.process.test_link import add, job_of, run, wd
from tests.select.test_review import TTL, env  # noqa: F401 — env is a fixture


def test_judged_jobs_are_archived_never_deleted(env):  # noqa: F811
    db, _, jobs, _ = env  # three jobs, judged by two filters: ml (recommended), sales (un_recommended), analyst
    add(db, "nvidia", "JR9", url=wd("JR9"), title="Never Judged")
    run(db)
    never = job_of(db, "nvidia/JR9")
    for ref in ("nvidia/JR1", "nvidia/JR2", "linkedin/L3", "nvidia/JR9"):
        seen_days_ago(db, ref, 60)
    rep = purge(db, AppConfig().retention, ttl=TTL)  # used to fail: FOREIGN KEY constraint (judgments → jobs)
    assert sorted(e.job_id for e in rep.jobs.archived) == sorted(jobs.values())
    assert [e.job_id for e in rep.jobs.deleted] == [never] and db.job(never) is None
    sales = db.job(jobs["sales"])
    assert sales["state"] == "archived" and sales["step2"] == "un_recommended"
    parsed, locations = db.job_inputs(jobs["sales"])  # what a filter reads is kept
    assert parsed["title"] == "Sales Lead" and parsed["description_text"]
    assert len(db.judgment_rows(job_id=jobs["sales"])) == 2  # both filters' verdicts kept


def test_a_listing_seen_again_becomes_a_new_job_and_archived_copies_are_linked(env):  # noqa: F811
    db, _, jobs, _ = env
    seen_days_ago(db, "nvidia/JR2", 60)
    purge(db, AppConfig().retention, ttl=TTL)
    old = jobs["sales"]
    assert not db.posting_exists("nvidia", "JR2")  # the identity is free: collect fetches the listing again

    add(db, "nvidia", "JR2", url=wd("JR2"), title="Sales Lead",
        desc=db.job_inputs(old)[0]["description_text"])  # the same listing, seen again
    run(db)
    new = job_of(db, "nvidia/JR2")
    assert new != old and db.job(new)["archived_at"] is None  # a new active job; the archived one is untouched
    [w] = db.job(new)["warnings"]
    assert w["job_id"] == old and "which Step 2 left out" in w["text"]

    # once the new copy is judged and expires too, the archive links it to the first copy (one training example)
    db.set_job_state(new, step2="un_recommended")
    from auto_app.core.db import tables as T

    with db.engine.begin() as conn:  # a judgment row, as `filter run` would write
        row = dict(conn.execute(T.judgments.select().where(T.judgments.c.job_id == old)).mappings().first())
        conn.execute(T.judgments.insert().values(**{**row, "job_id": new}))
    seen_days_ago(db, "nvidia/JR2", 60)
    rep = purge(db, AppConfig().retention, ttl=TTL)
    assert rep.jobs.linked == {new: old} and db.job(new)["duplicate_of"] == old
    assert "1 linked to an earlier archived copy" in "\n".join(rep.jobs.format())


def test_a_different_title_is_not_linked_in_the_archive(env):  # noqa: F811
    db, _, jobs, _ = env
    seen_days_ago(db, "nvidia/JR1", 60)
    purge(db, AppConfig().retention, ttl=TTL)
    add(db, "nvidia", "JR1", url=wd("JR1"), title="ML Engineer II")  # same employer job id, different title
    run(db)
    new = job_of(db, "nvidia/JR1")
    db.set_job_state(new, step2="recommended", decision="not_applied")
    seen_days_ago(db, "nvidia/JR1", 60)
    rep = purge(db, AppConfig().retention, ttl=TTL)
    assert new in [e.job_id for e in rep.jobs.archived] and rep.jobs.linked == {}
