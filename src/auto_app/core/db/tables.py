"""Database schema (docs/step1-collect.md §8.1). Changes go through Alembic migrations (migrations/)."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})

Ts = DateTime(timezone=True)

batches = Table(
    "batches", metadata,
    Column("batch_id", String, primary_key=True),
    Column("started_at", Ts, nullable=False),
    Column("finished_at", Ts),
    Column("interrupted", Boolean, nullable=False, default=False),
    Column("config_hash", String),
    Column("data", JSON, nullable=False),  # options + per-source summaries, as shown by `stats`
)

search_runs = Table(
    "search_runs", metadata,
    Column("search_run_id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", String, ForeignKey("batches.batch_id"), nullable=False, index=True),
    Column("search_name", String),
    Column("source", String, nullable=False),
    Column("query", JSON),
    Column("search_url", Text),
    Column("started_at", Ts),
    Column("finished_at", Ts),
    Column("status", String),
    Column("stop_reason", String),
    Column("pages_loaded", Integer),
    Column("outcomes", JSON),
)

search_results = Table(
    "search_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("search_run_id", Integer, ForeignKey("search_runs.search_run_id"), nullable=False, index=True),
    Column("batch_id", String, nullable=False),
    Column("search_name", String),
    Column("source", String, nullable=False),
    Column("source_job_id", String, nullable=False),
    Column("page", Integer),
    Column("rank", Integer),
    Column("is_promoted", Boolean),
    Column("title", Text),
    Column("url", Text),
    Column("card", JSON),  # the card as recorded (lines, attrs …)
    Column("card_html", LargeBinary),  # zstd
    Column("outcome", String, nullable=False),
    Column("outcome_detail", Text),
    Column("seen_at", Ts, nullable=False),
    Index("ix_search_results_source_job", "source", "source_job_id"),
    Index("ix_search_results_source_outcome_seen", "source", "outcome", "seen_at"),
)

source_health = Table(
    "source_health", metadata,
    Column("source", String, primary_key=True),
    Column("status", String, nullable=False, default="ok"),
    Column("data", JSON, nullable=False),  # cooldown_until, last_block_at, last_block_reason, last_ok_at, last_run_at
)

postings = Table(
    "postings", metadata,
    Column("posting_id", Integer, primary_key=True, autoincrement=True),
    Column("source", String, nullable=False),
    Column("source_job_id", String, nullable=False),
    Column("source_url", Text),
    Column("kind", String, nullable=False),  # browser | api
    Column("platform", String),
    Column("board", Text),
    Column("batch_id", String),
    Column("first_search_run_id", Integer),
    # lifecycle: fetched → parsed | parse_failed → normalized → (C8c) l1_done → linked
    Column("state", String, nullable=False, default="fetched"),
    Column("parser", String),
    Column("parsed_at", String),
    Column("parse_error", Text),
    Column("first_seen_at", Ts, nullable=False),
    Column("last_seen_at", Ts, nullable=False),
    # parse output: the full CommonPosting + a few columns for queries
    Column("parsed", JSON),
    Column("title", Text),
    Column("company", Text),
    Column("posted_at", Ts),
    Column("apply_url", Text),
    Column("apply_kind", String),
    # normalize output: the full record + indexed keys (kept forever, for Level 1a/2 and tombstones)
    Column("normalized", JSON),
    Column("company_norm", String, index=True),
    Column("title_norm", Text),
    Column("employer_key", String, index=True),
    Column("req_id", String),
    Column("desc_hash", String, index=True),
    # dedup (C8c)
    Column("duplicate_of", Integer, ForeignKey("postings.posting_id")),
    Column("job_id", Integer, ForeignKey("jobs.job_id"), index=True),
    Column("match_rule", String),
    Column("match_confidence", Float),
    Column("purged", Boolean, nullable=False, default=False),
    UniqueConstraint("source", "source_job_id"),  # Level 1a
    Index("ix_postings_source_state", "source", "state"),
)

posting_raw = Table(  # heavy, purged ~7 days after a successful parse (C9)
    "posting_raw", metadata,
    Column("posting_id", Integer, ForeignKey("postings.posting_id", ondelete="CASCADE"), primary_key=True),
    Column("fetched_at", Ts),
    Column("capture", JSON),  # final URL, status, ready, apply link/kind, meta
    Column("card", JSON),
    Column("page_text", LargeBinary),  # zstd
    Column("detail_html", LargeBinary),  # zstd
    Column("payloads", LargeBinary),  # zstd JSON
    Column("embedded", LargeBinary),  # zstd JSON
    Column("record", LargeBinary),  # zstd JSON (API sources)
    Column("detail", LargeBinary),  # zstd JSON (API sources, e.g. Workday)
    Column("screenshot_path", Text),
)

# ---- C8c (created now, filled by dedup later) -------------------------------------------------

jobs = Table(
    "jobs", metadata,
    Column("job_id", Integer, primary_key=True, autoincrement=True),
    Column("primary_posting_id", Integer, ForeignKey("postings.posting_id", use_alter=True)),
    Column("company_norm", String, index=True),
    Column("title_norm", Text),
    Column("canonical_url", Text),
    # state (docs/README.md §3): Step 2's verdict and your decision; NULL = not yet
    Column("step2", String),  # recommended | un_recommended
    Column("step2_at", Ts),
    Column("decision", String),  # applied | not_applied
    Column("decided_at", Ts),
    Column("first_seen_at", Ts),
    Column("last_seen_at", Ts),  # latest last-seen of any posting (refreshed by dedup and purge)
    Column("expires_at", Ts),  # last_seen_at + the TTL of the job's state (jobs.ttl_days)
    # archived (step1 §9.2): a compact record, excluded from dedup
    Column("archived_at", Ts, index=True),
    Column("archive", JSON),
    Column("archive_description", LargeBinary),  # zstd; applied jobs only
    Column("warnings", JSON),  # e.g. [{"kind": "repost", "job_id": 12, "text": "looks like job #12, …"}]
)

job_locations = Table(
    "job_locations", metadata,
    Column("job_id", Integer, ForeignKey("jobs.job_id"), primary_key=True),
    Column("location_norm", String, primary_key=True),
    Column("workplace_type", String),
    Column("apply_url", Text),
    Column("posting_id", Integer, ForeignKey("postings.posting_id")),
)

job_keys = Table(  # kept while the job exists, archived jobs included (repost warnings)
    "job_keys", metadata,
    Column("key_type", String, primary_key=True),
    Column("key_value", String, primary_key=True),
    Column("job_id", Integer, ForeignKey("jobs.job_id"), primary_key=True),
)

company_aliases = Table(
    "company_aliases", metadata,
    Column("alias_norm", String, primary_key=True),
    Column("company_norm", String, nullable=False),
    Column("origin", String),  # learned | config
)

# ---- Step 2 (docs/step2-select.md §9) ----------------------------------------------------------

filter_versions = Table(  # what each filter version ran: the exact command, files and settings
    "filter_versions", metadata,
    Column("filter_version", String, primary_key=True),
    Column("filter", String, nullable=False, index=True),
    Column("command", Text, nullable=False),
    Column("files", JSON),  # {relative path: text}
    Column("settings", JSON),  # filter.yaml's versioned settings
    Column("created_at", Ts, nullable=False),
)

judgments = Table(  # one agent run of one filter version on one job
    "judgments", metadata,
    Column("job_id", Integer, ForeignKey("jobs.job_id"), primary_key=True),
    Column("filter_version", String, ForeignKey("filter_versions.filter_version"), primary_key=True),
    Column("filter", String, nullable=False, index=True),
    Column("model", String),
    Column("status", String, nullable=False),  # ok | unparsed | error
    Column("answer", Text),  # the raw final answer
    Column("recommend", Boolean),  # parsed output; NULL = couldn't read (or error)
    Column("reason", Text),
    Column("parser", String),  # "convention" or "parser.py:<hash>"
    Column("parse_error", Text),
    Column("error", Text),  # the run's error, when status = error
    Column("trace", LargeBinary),  # zstd JSON: every model turn and tool call
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("tool_calls", Integer),
    Column("cost_usd", Float),  # NULL when the model has no price configured
    Column("created_at", Ts, nullable=False),
    Column("parsed_at", Ts),
)

reviews = Table(  # your decisions in review = training labels (S3)
    "reviews", metadata,
    Column("review_id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer),  # no FK: labels outlive the job (TTL)
    Column("decision", String, nullable=False),  # want_to_apply | not_applied (review), applied (Step 3)
    Column("reason", Text),
    Column("filters", JSON),  # the filters that judged it, with their verdicts
    Column("job_text", Text),  # title + company + description, kept for training
    Column("created_at", Ts, nullable=False),
)

feedback = Table(  # your rating of one filter's verdict on one job = a label for that filter's preference model
    "feedback", metadata,
    Column("job_id", Integer, primary_key=True),  # no FK: labels outlive the job (TTL)
    Column("filter", String, primary_key=True),
    Column("filter_version", String),  # the version whose verdict you rated
    Column("verdict", Boolean),  # what the filter said: recommend (True) / no (False) / couldn't read (NULL)
    Column("correct", Boolean, nullable=False),  # you: the verdict was right / wrong
    Column("origin", String, nullable=False, default="explicit"),  # explicit (g/b) | implied (by your decision)
    Column("reason", Text),
    Column("job_text", Text),  # title + company + description, kept for training
    Column("created_at", Ts, nullable=False),
)
