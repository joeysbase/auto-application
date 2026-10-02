"""Move data between the file store and the database: `import-files` (files → DB) and `export` (DB → files)."""

import shutil
from pathlib import Path

from auto_app.collect.store import FileStore, PostingRef, dumps, safe_name, write_atomic


def import_file_store(src: FileStore, db) -> str:
    """Idempotent: batches and postings already in the database are skipped."""
    run_ids: dict[tuple[str, int], int] = {}
    b_new = b_skip = 0
    for bid in src.batch_ids():
        bdir = src.batch_dir(bid)

        def card_html(rec, bdir=bdir):
            p = bdir / "cards" / rec["source"] / f"{safe_name(rec['source_job_id'])}.html"
            return p.read_text() if p.exists() else None

        ids = db.import_batch(src.get_batch(bid), src.get_search_runs(bid), src.get_sightings(bid), card_html)
        if ids is None:
            b_skip += 1
            continue
        b_new += 1
        run_ids.update({(bid, old): new for old, new in ids.items()})
        log = bdir / "collect.log"
        if log.exists() and not db.log_path(bid).exists():
            db.log_path(bid).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log, db.log_path(bid))
        if (bdir / "failures").exists():
            shutil.copytree(bdir / "failures", db.paths.failures / bid, dirs_exist_ok=True)

    p_new = p_skip = 0
    for ref in src.iter_postings():
        posting = src.get_posting(ref)
        run = run_ids.get((posting.get("batch_id"), posting.get("first_search_run_id")))
        added = db.import_posting(posting, src.load_raw(ref), screenshot=src.screenshot_path(ref),
                                  parsed=src.get_parsed(ref), normalized=src.get_normalized(ref), search_run_id=run)
        p_new, p_skip = p_new + added, p_skip + (not added)

    for source, entry in src.health().items():
        db.update_health(source, **entry)
    aliases = {**db.load_aliases(), **src.load_aliases()}
    if aliases:
        db.save_aliases(aliases)
    return (f"imported from {src.root} into {db.describe()}:\n"
            f"  batches   {b_new} new, {b_skip} already there\n"
            f"  postings  {p_new} new, {p_skip} already there\n"
            f"  source health and learned company aliases merged")


def export_posting(db, ref: PostingRef, dest: FileStore) -> Path:
    """One posting from the database as files, in the file store's layout."""
    raw = db.load_raw(ref)
    d = dest.posting_dir(ref.source, ref.job_id)
    write_atomic(d / "card.json", dumps(raw.card, indent=2))
    write_atomic(d / "capture.json", dumps(raw.capture, indent=2))
    if raw.kind == "api":
        write_atomic(d / "record.json", dumps(raw.record, indent=2))
        if raw.detail is not None:
            write_atomic(d / "detail.json", dumps(raw.detail, indent=2))
    else:
        write_atomic(d / "detail.html", raw.html)
        write_atomic(d / "page_text.txt", raw.page_text)
        write_atomic(d / "payloads.json", dumps(raw.payloads, indent=2))
        write_atomic(d / "embedded.json", dumps(raw.embedded, indent=2))
        shot = db.screenshot_path(ref)
        if shot:
            shutil.copy2(shot, d / "screenshot.png")
    for name, data in (("parsed.json", db.get_parsed(ref)), ("normalized.json", db.get_normalized(ref))):
        if data:
            write_atomic(d / name, dumps(data, indent=2))
    write_atomic(d / "posting.json", dumps(db.get_posting(ref), indent=2))
    return d
