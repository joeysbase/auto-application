"""SQLite implementation of the storage interface (see collect/store.py for the interface).
The default store; `--debug-files` uses the FileStore instead (files only, no database)."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import zstandard
from sqlalchemy import and_, func, insert, or_, select, update

from auto_app.collect.store import PostingRef, RawPurged, api_capture, api_card, dumps, safe_name, select_retry
from auto_app.core.db import tables as T
from auto_app.core.db.engine import make_engine, upgrade
from auto_app.core.models import Card, Outcome, RawCapture, RawPosting, utcnow
from auto_app.settings import Paths

_zc = zstandard.ZstdCompressor(level=10)
_zd = zstandard.ZstdDecompressor()


def z(data: str | None) -> bytes | None:
    return _zc.compress(data.encode()) if data else None


def unz(blob: bytes | None) -> str:
    return _zd.decompress(blob).decode() if blob else ""


def zjson(obj) -> bytes | None:
    return z(dumps(obj)) if obj is not None else None


def unzjson(blob: bytes | None, default=None):
    return json.loads(unz(blob)) if blob else default


def ts(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes; all stored values are UTC
    return value.isoformat() if isinstance(value, datetime) else value


def job_state(row) -> str:
    """One label for a job's state (docs/README.md §3)."""
    if row.get("archived_at"):
        return "archived"
    step2, decision = row.get("step2"), row.get("decision")
    if decision:
        return decision  # applied | not_applied
    return {"recommended": "recommended", "un_recommended": "un_recommended"}.get(step2 or "", "not_evaluated")


def _jsonable(obj):
    return json.loads(dumps(obj))


class DbStore:
    kind = "db"

    def __init__(self, db_path: Path, paths: Paths):
        self.db_path = db_path
        self.paths = paths
        self.engine = make_engine(db_path)
        upgrade(self.engine)
        self._tx = None  # an open connection while inside transaction()

    def describe(self) -> str:
        return f"database {self.db_path}"

    # ---- postings: collection -------------------------------------------------------

    def _pid(self, conn, source: str, job_id: str) -> int | None:
        return conn.execute(
            select(T.postings.c.posting_id).where(T.postings.c.source == source, T.postings.c.source_job_id == job_id)
        ).scalar()

    def location(self, ref: PostingRef) -> str:
        return f"{self.db_path.name}: postings {ref}"

    def posting_exists(self, source: str, job_id: str) -> bool:
        """Level 1a — one indexed lookup."""
        with self.engine.connect() as conn:
            return self._pid(conn, source, job_id) is not None

    def touch(self, source: str, job_id: str, when: datetime | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(T.postings).where(T.postings.c.source == source, T.postings.c.source_job_id == job_id)
                         .values(last_seen_at=when or utcnow()))

    def _upsert_posting(self, conn, row: dict, raw_row: dict) -> int:
        pid = self._pid(conn, row["source"], row["source_job_id"])
        if pid is None:
            pid = conn.execute(insert(T.postings).values(**row)).inserted_primary_key[0]
        else:  # re-fetch (probe / --ignore-known): keep first_seen_at, reset processing
            row = {k: v for k, v in row.items() if k != "first_seen_at"}
            conn.execute(update(T.postings).where(T.postings.c.posting_id == pid).values(
                **row, parsed=None, normalized=None, parse_error=None, parser=None, parsed_at=None))
            conn.execute(T.posting_raw.delete().where(T.posting_raw.c.posting_id == pid))
        conn.execute(insert(T.posting_raw).values(posting_id=pid, **raw_row))
        return pid

    def _screenshot(self, source: str, job_id: str, png: bytes | None) -> str | None:
        if not png:
            return None
        path = self.paths.screenshots / source / f"{safe_name(job_id)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return str(path)

    def save_posting(self, card: Card, raw: RawCapture, *, batch_id: str, search_run_id: int) -> str:
        now = raw.fetched_at
        row = dict(source=card.source, source_job_id=card.source_job_id, source_url=raw.final_url, kind="browser",
                   batch_id=batch_id, first_search_run_id=search_run_id, state="fetched",
                   first_seen_at=now, last_seen_at=now)
        raw_row = dict(
            fetched_at=now, capture=_jsonable(raw.model_dump()), card=_jsonable(card.model_dump()),
            page_text=z(raw.page_text), detail_html=z(raw.detail_html),
            payloads=zjson([p.model_dump() for p in raw.payloads]), embedded=zjson(raw.embedded),
            screenshot_path=self._screenshot(card.source, card.source_job_id, raw.screenshot),
        )
        with self.engine.begin() as conn:
            self._upsert_posting(conn, row, raw_row)
        return self.location(PostingRef(card.source, card.source_job_id))

    def save_api_posting(self, source: str, job, detail: dict | None, *, platform: str, board: str | None,
                         batch_id: str, search_run_id: int) -> str:
        now = utcnow()
        row = dict(source=source, source_job_id=job.id, source_url=job.url, kind="api", platform=platform, board=board,
                   batch_id=batch_id, first_search_run_id=search_run_id, state="fetched",
                   first_seen_at=now, last_seen_at=now)
        raw_row = dict(fetched_at=now, capture=_jsonable(api_capture(source, job, detail, platform, board, now.isoformat())),
                       card=_jsonable(api_card(source, job)), record=zjson(job.record), detail=zjson(detail))
        with self.engine.begin() as conn:
            self._upsert_posting(conn, row, raw_row)
        return self.location(PostingRef(source, job.id))

    def import_posting(self, posting: dict, raw: RawPosting, *, screenshot: Path | None, parsed: dict | None,
                       normalized: dict | None, search_run_id: int | None) -> bool:
        """Insert a posting exactly as another store has it (import-files). False if it already exists."""
        with self.engine.begin() as conn:
            if self._pid(conn, raw.source, raw.source_job_id) is not None:
                return False
            row = dict(
                source=raw.source, source_job_id=raw.source_job_id, kind=raw.kind,
                source_url=posting.get("source_url"), platform=posting.get("platform"), board=posting.get("board"),
                batch_id=posting.get("batch_id"), first_search_run_id=search_run_id,
                state=posting.get("state", "fetched"), parser=posting.get("parser"), parsed_at=posting.get("parsed_at"),
                parse_error=posting.get("parse_error"),
                first_seen_at=ts(posting.get("first_seen_at")), last_seen_at=ts(posting.get("last_seen_at")),
            )
            if parsed:
                row.update(parsed=parsed, title=parsed.get("title"), company=parsed.get("company"),
                           posted_at=ts(parsed.get("posted_at")), apply_url=parsed.get("apply_url"),
                           apply_kind=parsed.get("apply_kind"))
            if normalized:
                row.update(normalized=normalized, company_norm=normalized.get("company_norm"),
                           title_norm=normalized.get("title_norm"), employer_key=normalized.get("employer_key"),
                           req_id=normalized.get("req_id"), desc_hash=normalized.get("desc_hash"))
            pid = conn.execute(insert(T.postings).values(**row)).inserted_primary_key[0]
            conn.execute(insert(T.posting_raw).values(
                posting_id=pid, fetched_at=ts(raw.capture.get("fetched_at")), capture=_jsonable(raw.capture),
                card=_jsonable(raw.card), page_text=z(raw.page_text), detail_html=z(raw.html),
                payloads=zjson(raw.payloads) if raw.kind == "browser" else None,
                embedded=zjson(raw.embedded) if raw.kind == "browser" else None,
                record=zjson(raw.record) if raw.kind == "api" else None, detail=zjson(raw.detail),
                screenshot_path=str(screenshot) if screenshot else None))
        return True

    def import_batch(self, batch: dict, runs: list[dict], sightings: list[dict], card_html) -> dict[int, int] | None:
        """Insert a batch with its runs and sightings; returns {file run id: db run id}, None if present."""
        bid = batch["batch_id"]
        with self.engine.begin() as conn:
            if conn.execute(select(T.batches.c.batch_id).where(T.batches.c.batch_id == bid)).scalar():
                return None
            conn.execute(insert(T.batches).values(
                batch_id=bid, started_at=ts(batch["started_at"]), finished_at=ts(batch.get("finished_at")),
                interrupted=bool(batch.get("interrupted")), config_hash=batch.get("config_hash"), data=_jsonable(batch)))
            ids: dict[int, int] = {}
            for r in runs:
                cols = {k: r.get(k) for k in ("search_name", "source", "search_url", "status", "pages_loaded")}
                ids[r["search_run_id"]] = conn.execute(insert(T.search_runs).values(
                    batch_id=bid, query=_jsonable(r.get("query")), outcomes=_jsonable(r.get("outcomes")),
                    started_at=ts(r.get("started_at")), finished_at=ts(r.get("finished_at")),
                    stop_reason=str(r["stop_reason"]) if r.get("stop_reason") else None, **cols)).inserted_primary_key[0]
            for rec in sightings:
                run = ids.get(rec["search_run_id"])
                if run is None:
                    continue
                card = {k: v for k, v in rec.items() if k in ("lines", "attrs", "locations", "posted_text")}
                conn.execute(insert(T.search_results).values(
                    search_run_id=run, batch_id=bid, search_name=rec.get("search_name"), source=rec["source"],
                    source_job_id=rec["source_job_id"], page=rec.get("page"), rank=rec.get("rank"),
                    is_promoted=rec.get("is_promoted"), title=rec.get("title"), url=rec.get("url"), card=_jsonable(card),
                    card_html=z(card_html(rec)), outcome=str(rec["outcome"]), outcome_detail=rec.get("outcome_detail"),
                    seen_at=ts(rec["seen_at"])))
        return ids

    # ---- postings: processing -------------------------------------------------------

    def sources(self) -> list[str]:
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(select(T.postings.c.source).distinct().order_by(T.postings.c.source))]

    def iter_postings(self, source: str | None = None) -> Iterator[PostingRef]:
        q = select(T.postings.c.source, T.postings.c.source_job_id).order_by(T.postings.c.source, T.postings.c.source_job_id)
        if source:
            q = q.where(T.postings.c.source == source)
        with self.engine.connect() as conn:
            rows = conn.execute(q).all()
        for s, j in rows:
            yield PostingRef(s, j)

    def _row(self, ref: PostingRef):
        with self.engine.connect() as conn:
            return conn.execute(select(T.postings).where(T.postings.c.source == ref.source,
                                                        T.postings.c.source_job_id == ref.job_id)).mappings().first()

    def get_posting(self, ref: PostingRef) -> dict | None:
        r = self._row(ref)
        if r is None:
            return None
        keys = ("source", "source_job_id", "source_url", "kind", "platform", "board", "batch_id", "first_search_run_id",
                "state", "parser", "parsed_at", "parse_error", "first_seen_at", "last_seen_at", "job_id", "match_rule",
                "match_confidence")
        out = {k: iso(r[k]) for k in keys if r[k] is not None}
        if r["purged"]:
            out["purged"] = True
        if r["duplicate_of"] is not None:
            out["duplicate_of"] = self._ref_of(r["duplicate_of"])
        return out

    def _ref_of(self, posting_id: int) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(T.postings.c.source, T.postings.c.source_job_id)
                               .where(T.postings.c.posting_id == posting_id)).first()
        return f"{row[0]}/{row[1]}" if row else None

    def load_raw(self, ref: PostingRef, company_hint: str | None = None) -> RawPosting:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(T.postings, T.posting_raw).join(T.posting_raw, T.posting_raw.c.posting_id == T.postings.c.posting_id)
                .where(T.postings.c.source == ref.source, T.postings.c.source_job_id == ref.job_id)
            ).mappings().first()
        if r is None:
            raise KeyError(f"no raw data for {ref} (never fetched)")
        if (r["kind"] == "api" and r["record"] is None) or (r["kind"] != "api" and r["detail_html"] is None and r["page_text"] is None):
            raise RawPurged(f"raw data for {ref} was purged (parsed and normalized data are kept)")
        return RawPosting(
            source=r["source"], source_job_id=r["source_job_id"], kind=r["kind"],
            posting=self.get_posting(ref) or {}, capture=r["capture"] or {}, card=r["card"] or {},
            company_hint=company_hint, page_text=unz(r["page_text"]), html=unz(r["detail_html"]),
            payloads=unzjson(r["payloads"], []), embedded=unzjson(r["embedded"], {}),
            record=unzjson(r["record"], {}), detail=unzjson(r["detail"], None),
        )

    def screenshot_path(self, ref: PostingRef) -> Path | None:
        with self.engine.connect() as conn:
            p = conn.execute(
                select(T.posting_raw.c.screenshot_path).join(T.postings, T.postings.c.posting_id == T.posting_raw.c.posting_id)
                .where(T.postings.c.source == ref.source, T.postings.c.source_job_id == ref.job_id)
            ).scalar()
        return Path(p) if p and Path(p).exists() else None

    def _where(self, ref: PostingRef):
        return (T.postings.c.source == ref.source) & (T.postings.c.source_job_id == ref.job_id)

    def get_parsed(self, ref: PostingRef) -> dict | None:
        r = self._row(ref)
        return r["parsed"] if r is not None else None

    def save_parsed(self, ref: PostingRef, parsed, parser: str) -> None:
        data = _jsonable(parsed.model_dump())
        with self.engine.begin() as conn:
            conn.execute(update(T.postings).where(self._where(ref)).values(
                parsed=data, state="parsed", parser=parser, parsed_at=utcnow().isoformat(), parse_error=None,
                title=parsed.title, company=parsed.company, posted_at=parsed.posted_at,
                apply_url=parsed.apply_url, apply_kind=parsed.apply_kind))

    def mark_parse_failed(self, ref: PostingRef, error: str, parser: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(T.postings).where(self._where(ref)).values(
                state="parse_failed", parser=parser, parsed_at=utcnow().isoformat(), parse_error=error))

    def get_normalized(self, ref: PostingRef) -> dict | None:
        r = self._row(ref)
        return r["normalized"] if r is not None else None

    def save_normalized(self, ref: PostingRef, norm: dict) -> None:
        with self.engine.begin() as conn:
            linked = conn.execute(select(T.postings.c.job_id).where(self._where(ref))).scalar() is not None
            conn.execute(update(T.postings).where(self._where(ref)).values(
                normalized=norm, state="linked" if linked else "normalized", company_norm=norm.get("company_norm"),
                title_norm=norm.get("title_norm"), employer_key=norm.get("employer_key"),
                req_id=norm.get("req_id"), desc_hash=norm.get("desc_hash")))

    def set_state(self, ref: PostingRef, **fields) -> None:
        cols = {k: v for k, v in fields.items() if k in T.postings.c}
        with self.engine.begin() as conn:
            conn.execute(update(T.postings).where(self._where(ref)).values(**cols))

    # ---- jobs (C8c) --------------------------------------------------------------------------

    @contextmanager
    def transaction(self):
        """Group job writes into one transaction (all or nothing)."""
        with self.engine.begin() as conn:
            self._tx = conn
            try:
                yield
            finally:
                self._tx = None

    @contextmanager
    def _conn(self):
        if self._tx is not None:
            yield self._tx
        else:
            with self.engine.begin() as conn:
                yield conn

    def _posting_id(self, conn, ref: str) -> int:
        source, job_id = ref.split("/", 1)
        pid = self._pid(conn, source, job_id)
        if pid is None:
            raise KeyError(ref)
        return pid

    def posting_links(self) -> dict[str, dict]:
        """For every posting already linked to a job: {"source/id": {job_id, duplicate_of, match_rule, match_confidence}}."""
        p, d = T.postings.alias("p"), T.postings.alias("d")
        q = (select(p.c.source, p.c.source_job_id, p.c.job_id, p.c.match_rule, p.c.match_confidence,
                    d.c.source.label("dsrc"), d.c.source_job_id.label("djid"))
             .select_from(p.outerjoin(d, p.c.duplicate_of == d.c.posting_id)).where(p.c.job_id.is_not(None)))
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
        return {f"{r['source']}/{r['source_job_id']}": {
            "job_id": r["job_id"], "match_rule": r["match_rule"], "match_confidence": r["match_confidence"],
            "duplicate_of": f"{r['dsrc']}/{r['djid']}" if r["dsrc"] else None} for r in rows}

    def create_job(self, **fields) -> int:
        with self._conn() as conn:
            return conn.execute(insert(T.jobs).values(**fields)).inserted_primary_key[0]

    def update_job(self, job_id: int, **fields) -> None:
        if "primary_posting" in fields:
            ref = fields.pop("primary_posting")
            with self._conn() as conn:
                fields["primary_posting_id"] = self._posting_id(conn, ref)
        with self._conn() as conn:
            conn.execute(update(T.jobs).where(T.jobs.c.job_id == job_id).values(**fields))

    def link_posting(self, ref: str, job_id: int, rule: str, confidence: float, duplicate_of: str | None) -> None:
        with self._conn() as conn:
            dup = self._posting_id(conn, duplicate_of) if duplicate_of else None
            conn.execute(update(T.postings).where(T.postings.c.posting_id == self._posting_id(conn, ref)).values(
                job_id=job_id, duplicate_of=dup, match_rule=rule, match_confidence=confidence, state="linked"))

    def add_job_keys(self, job_id: int, keys: set[tuple[str, str]]) -> None:
        with self._conn() as conn:
            have = {(t, v) for t, v in conn.execute(select(T.job_keys.c.key_type, T.job_keys.c.key_value)
                                                    .where(T.job_keys.c.job_id == job_id))}
            rows = [{"job_id": job_id, "key_type": t, "key_value": v} for t, v in keys - have]
            if rows:
                conn.execute(insert(T.job_keys), rows)

    def add_job_locations(self, job_id: int, rows: list[dict]) -> None:
        with self._conn() as conn:
            have = {r[0] for r in conn.execute(select(T.job_locations.c.location_norm)
                                               .where(T.job_locations.c.job_id == job_id))}
            new = []
            for r in rows:
                if r["location_norm"] in have:
                    continue
                have.add(r["location_norm"])
                new.append({"job_id": job_id, "location_norm": r["location_norm"], "workplace_type": r.get("workplace_type"),
                            "apply_url": r.get("apply_url"), "posting_id": self._posting_id(conn, r["posting"])})
            if new:
                conn.execute(insert(T.job_locations), new)

    def jobs(self, archived: bool | None = False) -> list[dict]:
        """Jobs with their primary posting's title/company/date, locations and sources.
        archived: False = active jobs (default), True = archived records only, None = both."""
        j, p = T.jobs, T.postings
        counts = (select(p.c.job_id, func.count().label("n")).where(p.c.job_id.is_not(None))
                  .group_by(p.c.job_id).subquery())
        q = (select(j, counts.c.n, p.c.source.label("primary_source"), p.c.title.label("title"),
                    p.c.company.label("company"), p.c.posted_at.label("posted_at"))
             .select_from(j.outerjoin(counts, counts.c.job_id == j.c.job_id)
                          .outerjoin(p, p.c.posting_id == j.c.primary_posting_id))
             .order_by(j.c.job_id))
        if archived is not None:
            q = q.where(j.c.archived_at.is_not(None) if archived else j.c.archived_at.is_(None))
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
            locs: dict[int, list[str]] = {}
            for job_id, loc in conn.execute(select(T.job_locations.c.job_id, T.job_locations.c.location_norm)
                                            .order_by(T.job_locations.c.location_norm)):
                locs.setdefault(job_id, []).append(loc)
            srcs: dict[int, list[str]] = {}
            for job_id, src in conn.execute(select(p.c.job_id, p.c.source).where(p.c.job_id.is_not(None))
                                            .distinct().order_by(p.c.source)):
                srcs.setdefault(job_id, []).append(src)
        out = []
        for r in rows:
            row = {k: iso(v) for k, v in r.items() if k != "archive_description"}
            row.update(postings=r["n"] or 0, locations=locs.get(r["job_id"], []), sources=srcs.get(r["job_id"], []),
                       warnings=r["warnings"] or [], state=job_state(r))
            arch = r["archive"]
            if arch:  # archived: the summary lives in the record (postings and locations were compacted)
                row.update(title=row.get("title") or arch.get("title"), company=row.get("company") or arch.get("company"),
                           locations=row["locations"] or arch.get("locations", []), postings=len(arch.get("postings", [])),
                           sources=row["sources"] or sorted({x["source"] for x in arch.get("postings", [])}),
                           posted_at=row.get("posted_at") or arch.get("posted_at"))
            out.append(row)
        return out

    def job(self, job_id: int) -> dict | None:
        return next((j for j in self.jobs(archived=None) if j["job_id"] == job_id), None)

    def job_inputs(self, job_id: int) -> tuple[dict | None, list[str]]:
        """What Step 2 sends about a job: its primary posting's parsed fields and the job's merged locations."""
        j, p = T.jobs, T.postings
        with self.engine.connect() as conn:
            parsed = conn.execute(select(p.c.parsed).join(j, j.c.primary_posting_id == p.c.posting_id)
                                  .where(j.c.job_id == job_id)).scalar()
            locs = [x for (x,) in conn.execute(select(T.job_locations.c.location_norm)
                                               .where(T.job_locations.c.job_id == job_id)
                                               .order_by(T.job_locations.c.location_norm))]
        return parsed, locs

    def archived_description(self, job_id: int) -> str:
        with self.engine.connect() as conn:
            return unz(conn.execute(select(T.jobs.c.archive_description).where(T.jobs.c.job_id == job_id)).scalar())

    def set_job_state(self, job_id: int, *, step2: str | None = ..., decision: str | None = ...,
                      now: datetime | None = None) -> None:
        """Set Step 2's verdict and/or your decision (`...` = leave unchanged, None = clear)."""
        now = now or utcnow()
        values = {}
        if step2 is not ...:
            values.update(step2=step2, step2_at=now if step2 else None)
        if decision is not ...:
            values.update(decision=decision, decided_at=now if decision else None)
        with self._conn() as conn:
            conn.execute(update(T.jobs).where(T.jobs.c.job_id == job_id).values(**values))

    def refresh_job_dates(self, ttl, now: datetime | None = None) -> int:
        """Active jobs: last_seen_at = the latest last-seen of any of their postings; expires_at = that + the
        TTL of their state. Returns how many jobs changed."""
        j, p = T.jobs, T.postings
        changed = 0
        with self._conn() as conn:
            latest = dict(conn.execute(select(p.c.job_id, func.max(p.c.last_seen_at))
                                       .where(p.c.job_id.is_not(None)).group_by(p.c.job_id)).all())
            for job_id, step2, last, exp in conn.execute(select(j.c.job_id, j.c.step2, j.c.last_seen_at, j.c.expires_at)
                                                         .where(j.c.archived_at.is_(None))).all():
                seen = latest.get(job_id) or last
                expires = seen + timedelta(days=ttl.for_state(step2)) if seen else None
                if (seen, expires) != (last, exp):
                    conn.execute(update(j).where(j.c.job_id == job_id).values(last_seen_at=seen, expires_at=expires))
                    changed += 1
        return changed

    def add_repost_warnings(self, job_id: int, keys: set[tuple[str, str]]) -> list[dict]:
        """Warn on a new job that shares a matching key with an archived job you acted on (§7.5)."""
        if not keys:
            return []
        j, k = T.jobs, T.job_keys
        with self._conn() as conn:
            hits = conn.execute(
                select(j.c.job_id, j.c.decision, j.c.decided_at, j.c.step2, j.c.archive, k.c.key_type)
                .join(k, k.c.job_id == j.c.job_id)
                .where(j.c.archived_at.is_not(None), j.c.job_id != job_id,
                       or_(*(and_(k.c.key_type == t, k.c.key_value == v) for t, v in keys)))
            ).all()
            found: dict[int, dict] = {}
            for old, decision, decided, step2, arch, key_type in hits:
                w = found.setdefault(old, {"kind": "repost", "job_id": old, "decision": decision, "keys": []})
                w["keys"].append(key_type)
                when = f" on {iso(decided)[:10]}" if decided else ""
                what = {"applied": f"which you applied to{when}", "not_applied": f"which you declined{when}"}.get(
                    decision, "which Step 2 recommended" if step2 == "recommended" else "archived")
                w["text"] = f"looks like job #{old} ({(arch or {}).get('title', '')}), {what}"
            if not found:
                return []
            current = conn.execute(select(j.c.warnings).where(j.c.job_id == job_id)).scalar() or []
            have = {(x.get("kind"), x.get("job_id")) for x in current}
            new = [w for w in found.values() if ("repost", w["job_id"]) not in have]
            if new:
                conn.execute(update(j).where(j.c.job_id == job_id).values(warnings=current + new))
            return new

    def job_postings(self, job_id: int) -> list[dict]:
        p, d = T.postings, T.postings.alias("d")
        q = (select(p.c.source, p.c.source_job_id, p.c.title, p.c.apply_kind, p.c.match_rule, p.c.match_confidence,
                    p.c.first_seen_at, p.c.last_seen_at, d.c.source.label("dsrc"), d.c.source_job_id.label("djid"))
             .select_from(p.outerjoin(d, p.c.duplicate_of == d.c.posting_id))
             .where(p.c.job_id == job_id).order_by(p.c.first_seen_at))
        with self.engine.connect() as conn:
            return [{**{k: iso(v) for k, v in r.items()},
                     "duplicate_of": f"{r['dsrc']}/{r['djid']}" if r["dsrc"] else None} for r in conn.execute(q).mappings()]

    def job_keys_of(self, job_id: int) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            return [(t, v) for t, v in conn.execute(select(T.job_keys.c.key_type, T.job_keys.c.key_value)
                                                    .where(T.job_keys.c.job_id == job_id).order_by(T.job_keys.c.key_type))]

    def clear_jobs(self) -> int:
        """Unlink every posting and delete all jobs (dedup --rebuild). Returns the number of jobs removed."""
        with self.engine.begin() as conn:
            n = conn.execute(select(func.count()).select_from(T.jobs)).scalar()
            conn.execute(update(T.postings).where(T.postings.c.job_id.is_not(None)).values(
                job_id=None, duplicate_of=None, match_rule=None, match_confidence=None))
            conn.execute(update(T.postings).where(T.postings.c.state == "linked").values(state="normalized"))
            conn.execute(T.job_locations.delete())
            conn.execute(T.job_keys.delete())
            conn.execute(T.jobs.delete())
        return n

    # ---- company aliases -------------------------------------------------------------------

    def load_aliases(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            return {a: c for a, c in conn.execute(select(T.company_aliases.c.alias_norm, T.company_aliases.c.company_norm))}

    def save_aliases(self, aliases: dict[str, str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(T.company_aliases.delete().where(T.company_aliases.c.origin == "learned"))
            if aliases:
                conn.execute(insert(T.company_aliases), [
                    {"alias_norm": a, "company_norm": c, "origin": "learned"} for a, c in aliases.items()])

    # ---- batches / search runs / sightings ------------------------------------------------

    def log_path(self, batch_id: str) -> Path:
        return self.paths.logs / f"{batch_id}.log"

    def failure_dir(self, batch_id: str, source: str) -> Path:
        return self.paths.failures / batch_id / source

    def write_batch(self, batch_id: str, data: dict) -> None:
        row = dict(started_at=ts(data["started_at"]), finished_at=ts(data.get("finished_at")),
                   interrupted=bool(data.get("interrupted")), config_hash=data.get("config_hash"), data=_jsonable(data))
        with self.engine.begin() as conn:
            if conn.execute(select(T.batches.c.batch_id).where(T.batches.c.batch_id == batch_id)).scalar():
                conn.execute(update(T.batches).where(T.batches.c.batch_id == batch_id).values(**row))
            else:
                conn.execute(insert(T.batches).values(batch_id=batch_id, **row))

    def start_search_run(self, batch_id: str, record: dict) -> int:
        cols = {k: v for k, v in record.items() if k in T.search_runs.c and k not in ("search_run_id", "batch_id")}
        cols["started_at"] = ts(cols.get("started_at"))
        cols["query"] = _jsonable(cols.get("query"))
        with self.engine.begin() as conn:
            run_id = conn.execute(insert(T.search_runs).values(batch_id=batch_id, **cols)).inserted_primary_key[0]
        return run_id

    def finish_search_run(self, batch_id: str, run_id: int, record: dict) -> None:
        cols = {k: v for k, v in record.items() if k in T.search_runs.c and k not in ("search_run_id", "batch_id", "source")}
        cols["finished_at"] = ts(cols.get("finished_at"))
        cols["outcomes"] = _jsonable(cols.get("outcomes"))
        cols["stop_reason"] = str(cols["stop_reason"]) if cols.get("stop_reason") is not None else None
        with self.engine.begin() as conn:
            conn.execute(update(T.search_runs).where(T.search_runs.c.search_run_id == run_id).values(**cols))

    def add_sighting(self, batch_id: str, record: dict, card_html: str | None) -> None:
        card = {k: v for k, v in record.items() if k in ("lines", "attrs", "locations", "posted_text")}
        with self.engine.begin() as conn:
            conn.execute(insert(T.search_results).values(
                search_run_id=record["search_run_id"], batch_id=batch_id, search_name=record.get("search_name"),
                source=record["source"], source_job_id=record["source_job_id"], page=record.get("page"),
                rank=record.get("rank"), is_promoted=record.get("is_promoted"), title=record.get("title"),
                url=record.get("url"), card=_jsonable(card), card_html=z(card_html),
                outcome=str(record["outcome"]), outcome_detail=record.get("outcome_detail"), seen_at=ts(record["seen_at"])))

    def batch_ids(self) -> list[str]:
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(select(T.batches.c.batch_id).order_by(T.batches.c.batch_id))]

    def get_batch(self, batch_id: str | None = None) -> dict | None:
        with self.engine.connect() as conn:
            q = select(T.batches.c.data)
            q = q.where(T.batches.c.batch_id == batch_id) if batch_id else q.order_by(T.batches.c.batch_id.desc())
            return conn.execute(q.limit(1)).scalar()

    def get_search_runs(self, batch_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(T.search_runs).where(T.search_runs.c.batch_id == batch_id)
                                .order_by(T.search_runs.c.search_run_id)).mappings().all()
        return [{k: iso(v) for k, v in r.items()} for r in rows]

    def get_sightings(self, batch_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(T.search_results).where(T.search_results.c.batch_id == batch_id)
                                .order_by(T.search_results.c.id)).mappings().all()
        return [self._sighting(r) for r in rows]

    def _sighting(self, r) -> dict:
        d = {k: iso(r[k]) for k in ("search_run_id", "batch_id", "search_name", "source", "source_job_id", "page", "rank",
                                     "is_promoted", "title", "url", "outcome", "outcome_detail", "seen_at")}
        d.update(r["card"] or {})
        return d

    def fetch_times(self, source: str, since: datetime) -> list[datetime]:
        c = T.search_results.c
        with self.engine.connect() as conn:
            rows = conn.execute(select(c.seen_at).where(
                c.source == source, c.outcome.in_([str(Outcome.FETCHED), str(Outcome.FETCH_FAILED)]), c.seen_at >= since))
        return [ts(iso(r[0])) for r in rows]

    def retry_candidates(self, source: str, *, within_days: int, max_attempts: int) -> list[dict]:
        c = T.search_results.c
        with self.engine.connect() as conn:
            rows = conn.execute(select(T.search_results).where(
                c.source == source, c.outcome.in_([str(Outcome.FETCH_FAILED), str(Outcome.BUDGET_SKIPPED)])
            ).order_by(c.id)).mappings().all()
        return select_retry([self._sighting(r) for r in rows], within_days, max_attempts,
                            lambda jid: self.posting_exists(source, jid))

    def counts(self) -> dict[str, int]:
        with self.engine.connect() as conn:
            return {t.name: conn.execute(select(func.count()).select_from(t)).scalar()
                    for t in (T.postings, T.posting_raw, T.batches, T.search_runs, T.search_results)}

    # ---- source health --------------------------------------------------------------------

    def health(self) -> dict[str, dict]:
        with self.engine.connect() as conn:
            return {s: {"status": st, **(d or {})} for s, st, d in
                    conn.execute(select(T.source_health.c.source, T.source_health.c.status, T.source_health.c.data))}

    def source_health(self, source: str) -> dict:
        return self.health().get(source, {"status": "ok"})

    def update_health(self, source: str, **fields) -> None:
        entry = self.source_health(source)
        entry.update({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()})
        status = entry.pop("status", "ok")
        with self.engine.begin() as conn:
            conn.execute(T.source_health.delete().where(T.source_health.c.source == source))
            conn.execute(insert(T.source_health).values(source=source, status=status, data=entry))
