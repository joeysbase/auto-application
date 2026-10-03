"""Storage interface for all Step 1 stages, and its file implementation.

Two implementations share one interface (the methods of `FileStore` below):
    DbStore   (core/db/store.py)  SQLite — the default; can mirror its writes to files for debugging
    FileStore (this module)       plain folders — the debug copy, easy to browse in an editor

Postings are addressed by `PostingRef(source, job_id)`. File layout under <root>:

    postings/<source>/<source_job_id>/
        posting.json      identity + lifecycle (written last: its presence = "known")
        card.json         the card / list entry that led to the fetch
        capture.json      final URL, status, apply link, meta
        browser sources:  detail.html, page_text.txt, payloads.json, embedded.json, screenshot.png
        API sources:      record.json, detail.json
        parsed.json       step 1b parse output
        normalized.json   step 1b normalize output
    batches/<batch_id>/
        batch.json, search_runs.jsonl, search_results.jsonl, cards/<source>/<id>.html, failures/, collect.log
    source_health.json
    company_aliases.json
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, NamedTuple

from auto_app.core.models import Card, Outcome, RawCapture, RawPosting, utcnow


class RawPurged(KeyError):
    """The posting's raw data was removed by `auto-app data purge` (its parsed/normalized data remain)."""


class PostingRef(NamedTuple):
    source: str
    job_id: str

    def __str__(self) -> str:
        return f"{self.source}/{self.job_id}"


def _default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o)}")


def dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False, indent=indent)


def write_atomic(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def safe_name(job_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in job_id)


def merge_runs(lines: list[dict]) -> list[dict]:
    """search_runs.jsonl has a start and a finish line per run; merge them by id."""
    runs: dict[int, dict] = {}
    for r in lines:
        runs.setdefault(r["search_run_id"], {}).update({k: v for k, v in r.items() if v is not None})
    return [runs[k] for k in sorted(runs)]


class FileStore:
    kind = "files"

    def __init__(self, root: Path):
        self.root = root

    def describe(self) -> str:
        return f"debug files at {self.root} (no database)"

    # ---- postings: collection -------------------------------------------------------

    def posting_dir(self, source: str, job_id: str) -> Path:
        return self.root / "postings" / source / safe_name(job_id)

    def _dir(self, ref: PostingRef) -> Path:
        return self.posting_dir(ref.source, ref.job_id)

    def location(self, ref: PostingRef) -> str:
        return str(self._dir(ref))

    def posting_exists(self, source: str, job_id: str) -> bool:
        """Level 1a: a posting is known once its page was fetched and saved."""
        return (self.posting_dir(source, job_id) / "posting.json").exists()

    def touch(self, source: str, job_id: str, when: datetime | None = None) -> None:
        path = self.posting_dir(source, job_id) / "posting.json"
        posting = read_json(path)
        posting["last_seen_at"] = (when or utcnow()).isoformat()
        write_atomic(path, dumps(posting, indent=2))

    def save_posting(self, card: Card, raw: RawCapture, *, batch_id: str, search_run_id: int) -> str:
        d = self.posting_dir(card.source, card.source_job_id)
        now = raw.fetched_at.isoformat()
        first_seen = read_json(d / "posting.json").get("first_seen_at", now) if (d / "posting.json").exists() else now
        write_atomic(d / "card.json", dumps(card.model_dump(), indent=2))
        write_atomic(d / "capture.json", dumps(raw.model_dump(), indent=2))
        write_atomic(d / "detail.html", raw.detail_html)
        write_atomic(d / "page_text.txt", raw.page_text)
        write_atomic(d / "payloads.json", dumps([p.model_dump() for p in raw.payloads], indent=2))
        write_atomic(d / "embedded.json", dumps(raw.embedded, indent=2))
        if raw.screenshot:
            write_atomic(d / "screenshot.png", raw.screenshot)
        self._write_posting(d, {
            "source": card.source, "source_job_id": card.source_job_id, "source_url": raw.final_url, "kind": "browser",
            "batch_id": batch_id, "first_search_run_id": search_run_id, "state": "fetched",
            "first_seen_at": first_seen, "last_seen_at": now,
        })
        return str(d)

    def save_api_posting(self, source: str, job, detail: dict | None, *, platform: str, board: str | None,
                         batch_id: str, search_run_id: int) -> str:
        """An API-sourced job: the platform's record (and detail, if fetched separately), stored as-is."""
        d = self.posting_dir(source, job.id)
        now = utcnow().isoformat()
        first_seen = read_json(d / "posting.json").get("first_seen_at", now) if (d / "posting.json").exists() else now
        write_atomic(d / "card.json", dumps(api_card(source, job), indent=2))
        write_atomic(d / "record.json", dumps(job.record, indent=2))
        if detail is not None:
            write_atomic(d / "detail.json", dumps(detail, indent=2))
        write_atomic(d / "capture.json", dumps(api_capture(source, job, detail, platform, board, now), indent=2))
        self._write_posting(d, {
            "source": source, "source_job_id": job.id, "source_url": job.url, "kind": "api",
            "platform": platform, "board": board, "batch_id": batch_id, "first_search_run_id": search_run_id,
            "state": "fetched", "first_seen_at": first_seen, "last_seen_at": now,
        })
        return str(d)

    def _write_posting(self, d: Path, posting: dict) -> None:
        write_atomic(d / "posting.json", dumps(posting, indent=2))  # commit marker, written last

    # ---- postings: processing -------------------------------------------------------

    def sources(self) -> list[str]:
        base = self.root / "postings"
        return sorted(p.name for p in base.glob("*") if p.is_dir())

    def iter_postings(self, source: str | None = None) -> Iterator[PostingRef]:
        for s in [source] if source else self.sources():
            for d in sorted((self.root / "postings" / s).glob("*")):
                if (d / "posting.json").exists():
                    yield PostingRef(s, read_json(d / "posting.json")["source_job_id"])

    def get_posting(self, ref: PostingRef) -> dict | None:
        p = self._dir(ref) / "posting.json"
        return read_json(p) if p.exists() else None

    def load_raw(self, ref: PostingRef, company_hint: str | None = None) -> RawPosting:
        """Everything saved for one posting, as a RawPosting for a parser."""
        d = self._dir(ref)
        posting = read_json(d / "posting.json")
        opt = lambda name, default: read_json(d / name) if (d / name).exists() else default  # noqa: E731
        text = lambda name: (d / name).read_text() if (d / name).exists() else ""  # noqa: E731
        return RawPosting(
            source=posting["source"],
            source_job_id=posting["source_job_id"],
            kind="api" if (d / "record.json").exists() else "browser",
            posting=posting,
            capture=read_json(d / "capture.json"),
            card=opt("card.json", {}),
            company_hint=company_hint,
            page_text=text("page_text.txt"),
            html=text("detail.html"),
            payloads=opt("payloads.json", []),
            embedded=opt("embedded.json", {}),
            record=opt("record.json", {}),
            detail=opt("detail.json", None),
        )

    def screenshot_path(self, ref: PostingRef) -> Path | None:
        p = self._dir(ref) / "screenshot.png"
        return p if p.exists() else None

    def get_parsed(self, ref: PostingRef) -> dict | None:
        p = self._dir(ref) / "parsed.json"
        return read_json(p) if p.exists() else None

    def save_parsed(self, ref: PostingRef, parsed, parser: str) -> None:
        write_atomic(self._dir(ref) / "parsed.json", dumps(parsed.model_dump(), indent=2))
        self.set_state(ref, state="parsed", parser=parser, parsed_at=utcnow().isoformat(), parse_error=None)

    def mark_parse_failed(self, ref: PostingRef, error: str, parser: str) -> None:
        self.set_state(ref, state="parse_failed", parser=parser, parsed_at=utcnow().isoformat(), parse_error=error)

    def get_normalized(self, ref: PostingRef) -> dict | None:
        p = self._dir(ref) / "normalized.json"
        return read_json(p) if p.exists() else None

    def save_normalized(self, ref: PostingRef, norm: dict) -> None:
        write_atomic(self._dir(ref) / "normalized.json", dumps(norm, indent=2))
        self.set_state(ref, state="normalized")

    def set_state(self, ref: PostingRef, **fields) -> None:
        path = self._dir(ref) / "posting.json"
        posting = read_json(path)
        for k, v in fields.items():
            if v is None:
                posting.pop(k, None)
            else:
                posting[k] = v
        write_atomic(path, dumps(posting, indent=2))

    def posting_links(self) -> dict[str, dict]:
        """Jobs live only in the database; in file mode nothing is linked yet (dedup can only preview)."""
        return {}

    # ---- company aliases (learned) ------------------------------------------------------

    def load_aliases(self) -> dict[str, str]:
        p = self.root / "company_aliases.json"
        return read_json(p) if p.exists() else {}

    def save_aliases(self, aliases: dict[str, str]) -> None:
        write_atomic(self.root / "company_aliases.json", dumps(aliases, indent=2))

    # ---- batches / search runs / sightings --------------------------------------------

    def batch_dir(self, batch_id: str) -> Path:
        return self.root / "batches" / batch_id

    def log_path(self, batch_id: str) -> Path:
        return self.batch_dir(batch_id) / "collect.log"

    def failure_dir(self, batch_id: str, source: str) -> Path:
        return self.batch_dir(batch_id) / "failures" / source

    def write_batch(self, batch_id: str, data: dict) -> None:
        write_atomic(self.batch_dir(batch_id) / "batch.json", dumps(data, indent=2))

    def start_search_run(self, batch_id: str, record: dict) -> int:
        runs = read_jsonl(self.batch_dir(batch_id) / "search_runs.jsonl")
        run_id = max((r["search_run_id"] for r in runs), default=0) + 1
        self._append(self.batch_dir(batch_id) / "search_runs.jsonl", {"search_run_id": run_id, **record})
        return run_id

    def finish_search_run(self, batch_id: str, run_id: int, record: dict) -> None:
        self._append(self.batch_dir(batch_id) / "search_runs.jsonl", {"search_run_id": run_id, **record})

    def add_sighting(self, batch_id: str, record: dict, card_html: str | None) -> None:
        self._append(self.batch_dir(batch_id) / "search_results.jsonl", record)
        if card_html:
            path = self.batch_dir(batch_id) / "cards" / record["source"] / f"{safe_name(record['source_job_id'])}.html"
            if not path.exists():
                write_atomic(path, card_html)

    def _append(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(dumps(record) + "\n")

    def batch_ids(self) -> list[str]:
        return sorted(p.name for p in (self.root / "batches").glob("*") if (p / "batch.json").exists())

    def get_batch(self, batch_id: str | None = None) -> dict | None:
        ids = self.batch_ids()
        if not ids:
            return None
        bid = batch_id or ids[-1]
        p = self.batch_dir(bid) / "batch.json"
        return read_json(p) if p.exists() else None

    def get_search_runs(self, batch_id: str) -> list[dict]:
        return merge_runs(read_jsonl(self.batch_dir(batch_id) / "search_runs.jsonl"))

    def get_sightings(self, batch_id: str) -> list[dict]:
        return read_jsonl(self.batch_dir(batch_id) / "search_results.jsonl")

    def _all_sightings(self, source: str):
        for path in sorted((self.root / "batches").glob("*/search_results.jsonl")):
            for r in read_jsonl(path):
                if r["source"] == source:
                    yield r

    def fetch_times(self, source: str, since: datetime) -> list[datetime]:
        """When job pages were opened for `source` (fetched or failed), across runs."""
        out = []
        for r in self._all_sightings(source):
            if r["outcome"] in (Outcome.FETCHED, Outcome.FETCH_FAILED):
                t = datetime.fromisoformat(r["seen_at"])
                if t >= since:
                    out.append(t)
        return out

    def retry_candidates(self, source: str, *, within_days: int, max_attempts: int) -> list[dict]:
        """Cards whose fetch failed or was skipped for budget, and that still have no posting."""
        return select_retry(list(self._all_sightings(source)), within_days, max_attempts,
                            lambda jid: self.posting_exists(source, jid))

    # ---- source health ------------------------------------------------------------------

    @property
    def _health_path(self) -> Path:
        return self.root / "source_health.json"

    def health(self) -> dict[str, dict]:
        return read_json(self._health_path) if self._health_path.exists() else {}

    def source_health(self, source: str) -> dict:
        return self.health().get(source, {"status": "ok"})

    def update_health(self, source: str, **fields) -> None:
        data = self.health()
        entry = data.get(source, {"status": "ok"})
        entry.update({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()})
        data[source] = entry
        write_atomic(self._health_path, dumps(data, indent=2))


# ---- shared helpers (both stores) -----------------------------------------------------------

def api_card(source: str, job) -> dict:
    return {"source": source, "source_job_id": job.id, "url": job.url, "title": job.title,
            "locations": job.locations, "posted_at": job.posted_at, "posted_text": job.posted_text}


def api_capture(source: str, job, detail: dict | None, platform: str, board: str | None, now: str) -> dict:
    return {
        "source": source, "source_job_id": job.id, "kind": "api", "platform": platform, "board": board,
        "requested_url": job.url, "final_url": job.url, "http_status": 200, "fetched_at": now, "ready": True,
        "apply_text": None, "apply_href": job.apply_url, "apply_kind": "external", "apply_redirects": [],
        "meta": {"has_detail": detail is not None},
    }


def select_retry(sightings: list[dict], within_days: int, max_attempts: int, exists) -> list[dict]:
    cutoff = utcnow() - timedelta(days=within_days)
    latest: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    for r in sightings:
        if datetime.fromisoformat(r["seen_at"]) < cutoff:
            continue
        jid = r["source_job_id"]
        if r["outcome"] == Outcome.FETCH_FAILED:
            attempts[jid] = attempts.get(jid, 0) + 1
        if r["outcome"] in (Outcome.FETCH_FAILED, Outcome.BUDGET_SKIPPED):
            latest[jid] = r
    return [
        r for jid, r in sorted(latest.items(), key=lambda kv: kv[1]["seen_at"], reverse=True)
        if attempts.get(jid, 0) < max_attempts and not exists(jid)
    ]
