"""Step 1b parse: run each source's plugin parser over saved postings (docs/step1-collect.md §5).

fetched → parsed (parsed.json written) | parse_failed (raw kept; fix the parser and re-parse).
"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from auto_app.collect.store import RawPurged
from auto_app.core.config import AppConfig
from auto_app.core.models import CommonPosting
from auto_app.sources.parsing import ParseError
from auto_app.sources.registry import Registry

log = logging.getLogger(__name__)

# Fields reported in the coverage table: name → "is it filled?"
COVERAGE = {
    "title": lambda p: bool(p.title),
    "company": lambda p: bool(p.company),
    "location": lambda p: bool(p.locations),
    "description": lambda p: len(p.description_text) >= 200,
    "posted": lambda p: p.posted_at is not None,
    "workplace": lambda p: p.workplace_type != "unknown",
    "employment": lambda p: p.employment_type != "unknown",
    "salary": lambda p: p.salary is not None,
    "apply_url": lambda p: bool(p.apply_url),
}


@dataclass
class SourceParseStats:
    parsed: int = 0
    failed: int = 0
    skipped: int = 0
    raw_purged: int = 0  # skipped because purge removed their raw data
    filled: Counter = field(default_factory=Counter)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (job id, error)


def plugin_for(source: str, registry: Registry):
    """(plugin, company hint) for a stored source name; the hint is a company source's employer name."""
    return registry.create(source), registry.get(source).company


def parse_postings(
    store, cfg: AppConfig, registry: Registry, sources: list[str] | None = None, reparse: bool = False
) -> dict[str, SourceParseStats]:
    stats: dict[str, SourceParseStats] = defaultdict(SourceParseStats)
    plugins: dict[str, tuple] = {}
    names = sources or store.sources()
    for source in names:
        st = stats[source]
        try:
            plugins[source] = plugins.get(source) or plugin_for(source, registry)
        except KeyError as e:
            log.warning("%s: %s — skipping its postings", source, e.args[0])
            continue
        plugin, hint = plugins[source]
        parser = f"{getattr(plugin, 'platform', None) or getattr(plugin, 'name', source)}:{plugin.parser_version}"
        for ref in store.iter_postings(source):
            posting = store.get_posting(ref)
            if posting.get("state") == "archived":
                continue  # an archived job's posting: kept as an identity only (§9.2)
            done = posting.get("state") in ("parsed", "normalized", "linked") and posting.get("parser") == parser
            if not reparse and done:
                st.skipped += 1
                _count(st, CommonPosting.model_validate(store.get_parsed(ref)))
                continue
            try:
                raw = store.load_raw(ref, hint)
            except RawPurged:  # keep the earlier parse; the raw data needed to redo it is gone
                st.skipped += 1
                st.raw_purged += 1
                if store.get_parsed(ref):
                    _count(st, CommonPosting.model_validate(store.get_parsed(ref)))
                continue
            try:
                parsed = plugin.parse(raw)
                if not parsed.title:
                    raise ParseError("no title found")
            except Exception as e:  # a parser bug must not stop the others
                msg = f"{type(e).__name__}: {e}" if not isinstance(e, ParseError) else str(e)
                store.mark_parse_failed(ref, msg, parser)
                st.failed += 1
                st.errors.append((ref.job_id, msg))
                continue
            store.save_parsed(ref, parsed, parser)
            st.parsed += 1
            _count(st, parsed)
    return dict(stats)


def _count(st: SourceParseStats, p: CommonPosting) -> None:
    for name, filled in COVERAGE.items():
        if filled(p):
            st.filled[name] += 1


def format_coverage(stats: dict[str, SourceParseStats]) -> str:
    cols = list(COVERAGE)
    header = f"{'source':<10} {'ok':>4} {'fail':>4}  " + " ".join(f"{c[:10]:>10}" for c in cols)
    out = [header, "-" * len(header)]
    for source, st in stats.items():
        total = st.parsed + st.skipped
        pct = lambda c: f"{100 * st.filled[c] // total}%" if total else "-"  # noqa: E731
        out.append(f"{source:<10} {total:>4} {st.failed:>4}  " + " ".join(f"{pct(c):>10}" for c in cols))
    out.append("")
    out.append("% of parsed postings with each field (description = at least 200 characters)")
    purged = sum(st.raw_purged for st in stats.values())
    if purged:
        out.append(f"{purged} postings kept their earlier parse: their raw data was purged")
    for source, st in stats.items():
        for job_id, err in st.errors[:5]:
            out.append(f"  ! {source} {job_id}: {err}")
        if len(st.errors) > 5:
            out.append(f"  ! {source}: … {len(st.errors) - 5} more failures")
    return "\n".join(out)
