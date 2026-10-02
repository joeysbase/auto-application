"""Human-readable summaries of saved data (`inspect`, `stats`), from any store."""

import html
import json
import re

from auto_app.collect.store import PostingRef
from auto_app.core.models import RawPosting


def _jobposting_title(embedded: dict) -> str | None:
    for item in embedded.get("jsonld", []):
        for node in item if isinstance(item, list) else [item]:
            if isinstance(node, dict):
                graph = node.get("@graph")
                for n in graph if isinstance(graph, list) else [node]:
                    if isinstance(n, dict) and n.get("@type") == "JobPosting":
                        return n.get("title")
    return None


TAG = re.compile(r"<[^>]+>")
DESCRIPTION_KEYS = ("descriptionPlain", "descriptionBodyPlain", "content", "description", "descriptionHtml")


def api_description(raw: RawPosting) -> str:
    """Best description text in an API record (or its separately fetched detail)."""
    texts = []
    if raw.detail:
        texts.append((raw.detail.get("jobPostingInfo") or {}).get("jobDescription") or "")
    texts += [raw.record.get(k) or "" for k in DESCRIPTION_KEYS if isinstance(raw.record.get(k), str)]
    best = max(texts, key=len, default="")
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", html.unescape(best)))).strip()


def posting_summary(store, ref: PostingRef, raw: RawPosting | None = None) -> dict:
    raw = raw or store.load_raw(ref)
    posting, capture, card = raw.posting, raw.capture, raw.card
    base = {
        "ref": ref, "kind": raw.kind, "source": ref.source, "id": ref.job_id,
        "state": posting.get("state", "fetched"), "last_seen_at": posting.get("last_seen_at"),
        "card_title": card.get("title"), "http_status": capture.get("http_status"),
        "final_url": capture.get("final_url"), "apply_kind": capture.get("apply_kind", "unknown"),
        "apply_text": capture.get("apply_text"), "apply_href": capture.get("apply_href"),
        "apply_redirects": capture.get("apply_redirects") or [],
    }
    if raw.kind == "api":
        desc = api_description(raw)
        size = len(json.dumps(raw.record)) + (len(json.dumps(raw.detail)) if raw.detail else 0)
        return {**base, "platform": posting.get("platform"), "board": posting.get("board"), "jsonld_title": None,
                "locations": card.get("locations") or [], "posted": card.get("posted_text") or card.get("posted_at"),
                "ready": True, "html_kb": size // 1024, "text_chars": len(desc), "description": desc, "jsonld": 0,
                "next_data": False, "json_scripts": 0, "code_blocks": 0, "payloads": 0, "payload_urls": [],
                "screenshot": False, "has_detail": raw.detail is not None}
    emb = raw.embedded
    return {**base, "jsonld_title": _jobposting_title(emb), "ready": capture.get("ready"),
            "html_kb": len(raw.html.encode()) // 1024, "text_chars": (capture.get("meta") or {}).get("text_chars"),
            "jsonld": len(emb.get("jsonld", [])), "next_data": emb.get("next_data") is not None,
            "json_scripts": len(emb.get("json_scripts", [])), "code_blocks": len(emb.get("code_blocks", [])),
            "payloads": len(raw.payloads), "payload_urls": [p["url"] for p in raw.payloads],
            "screenshot": store.screenshot_path(ref) is not None, "page_text": raw.page_text}


STATE_MARK = {"fetched": "f", "parsed": "p", "normalized": "n", "parse_failed": "X"}


def format_table(store, source: str | None) -> str:
    rows = []
    for ref in store.iter_postings(source):
        try:
            rows.append(posting_summary(store, ref))
        except KeyError:  # raw data purged
            rows.append({"source": ref.source, "id": ref.job_id, "missing": True})
    if not rows:
        return "no postings saved yet"
    header = (f"{'source':<10} {'id':<22} {'st':<2} {'ready':<5} {'http':<4} {'dataKB':>6} {'text':>6} {'ld':>2} "
              f"{'nd':>2} {'pl':>3} {'apply':<10} title")
    lines = [header, "-" * len(header)]
    for r in rows:
        if r.get("missing"):
            lines.append(f"{r['source']:<10} {r['id'][:22]:<22} (raw data not available)")
            continue
        title = (r["jsonld_title"] or r["card_title"] or "")[:50]
        lines.append(
            f"{r['source']:<10} {r['id'][:22]:<22} {STATE_MARK.get(r['state'], '?'):<2} {'y' if r['ready'] else 'N':<5} "
            f"{str(r['http_status'] or ''):<4} {r['html_kb']:>6} {r['text_chars'] or 0:>6} {r['jsonld']:>2} "
            f"{'y' if r['next_data'] else '-':>2} {r['payloads']:>3} {r['apply_kind']:<10} {title}"
        )
    lines.append("")
    lines.append("st: f=fetched p=parsed n=normalized X=parse failed · ready=detail selector found · "
                 "dataKB=HTML (browser) or record (API) · ld=JSON-LD · nd=__NEXT_DATA__ · pl=captured JSON")
    return "\n".join(lines)


def format_parsed(store, ref: PostingRef) -> list[str]:
    """The parsed fields block for `inspect <source> <id>` (empty if not parsed yet)."""
    posting = store.get_posting(ref) or {}
    if posting.get("state") == "parse_failed":
        return ["", f"--- parse failed ({posting.get('parser')}) ---", posting.get("parse_error", "")]
    p = store.get_parsed(ref)
    if not p:
        return ["", "--- not parsed yet (run `auto-app parse`) ---"]
    sal = p.get("salary") or {}
    salary = (f"{sal.get('min'):,.0f}–{sal.get('max'):,.0f} {sal.get('currency') or ''}/{sal.get('period') or '?'}"
              f" ({sal.get('source')}{'' if sal.get('plausible', True) else ', implausible'})") if sal.get("min") else "-"
    extras = {k: v for k, v in (p.get("extras") or {}).items() if v not in (None, False, [], {}, "")}
    return [
        "",
        f"--- parsed ({posting.get('parser')}) ---",
        f"title        {p['title']}",
        f"company      {p.get('company')}",
        f"locations    {'; '.join(p.get('locations') or []) or '-'}",
        f"workplace    {p.get('workplace_type')} · employment {p.get('employment_type')}"
        + (f" · seniority {p['seniority']}" if p.get("seniority") else ""),
        f"salary       {salary}",
        f"posted       {p.get('posted_at') or '-'} ({p.get('posted_at_precision') or '-'})"
        + (" · reposted" if p.get("reposted") else "") + (f" · {p['posted_text']}" if p.get("posted_text") else ""),
        f"applicants   {p.get('applicants_text') or '-'}",
        f"apply        {p.get('apply_kind')} · {p.get('apply_url') or '-'}",
        f"req id       {p.get('req_id') or '-'}",
        f"description  {len(p.get('description_text') or '')} chars",
        f"extras       {extras or '-'}",
    ]


def format_normalized(store, ref: PostingRef) -> list[str]:
    n = store.get_normalized(ref)
    if not n:
        return []
    return [
        "",
        f"--- normalized (v{n['version']}) ---",
        f"company      {n['company_norm']}  (via {n['company_via']})",
        f"title        {n['title_norm']}",
        f"employer key {n.get('employer_key') or '-'}" + (f"  (from {n['employer_key_from']})" if n.get("employer_key") else ""),
        f"req id       {n.get('req_id') or '-'}",
        f"locations    {'; '.join(n.get('location_norm') or []) or '-'}",
        f"description  {n['desc_chars']} chars after boilerplate removal · {n['shingle_count']} shingles · "
        f"hash {(n.get('desc_hash') or '-')[:12]}",
    ]


def format_one(store, ref: PostingRef, text_chars: int = 1200) -> str:
    try:
        r = posting_summary(store, ref)
    except KeyError as e:  # raw data purged: show what's kept
        return "\n".join([f"posting      {ref.source} {ref.job_id}", f"stored in    {store.location(ref)}",
                          f"raw data     {e.args[0]}", *format_parsed(store, ref), *format_normalized(store, ref)])
    parsed = store.get_parsed(ref) or {}
    parsed_desc = parsed.get("description_text")
    where = store.location(ref)
    posting = store.get_posting(ref) or {}
    if posting.get("job_id"):
        how = f"duplicate of {posting['duplicate_of']}, " if posting.get("duplicate_of") else ""
        where += f"\njob          #{posting['job_id']} ({how}{posting.get('match_rule')}) — `auto-app jobs {posting['job_id']}`"
    blocks = format_parsed(store, ref) + format_normalized(store, ref) + [""]
    if r["kind"] == "api":
        return "\n".join([
            f"posting      {r['source']} {r['id']}  ({r['platform']}, board {r['board']})",
            f"stored in    {where}",
            f"url          {r['final_url']}",
            f"title        {r['card_title']}",
            f"locations    {'; '.join(r['locations'])}",
            f"posted       {r['posted']}",
            f"apply        {r['apply_href']}",
            f"record       {r['html_kb']} KB · detail fetched: {'yes' if r['has_detail'] else 'no (list had everything)'}"
            f" · description {r['text_chars']} chars",
            *blocks,
            f"--- description (first {text_chars} chars) ---",
            (parsed_desc or r["description"])[:text_chars],
        ])
    shot = store.screenshot_path(ref)
    out = [
        f"posting      {r['source']} {r['id']}",
        f"stored in    {where}",
        f"final_url    {r['final_url']}  (HTTP {r['http_status']}, ready={r['ready']})",
        f"card title   {r['card_title']}",
        f"JSON-LD      {r['jsonld']} block(s); JobPosting title: {r['jsonld_title']}",
        f"__NEXT_DATA__ {'yes' if r['next_data'] else 'no'} · json scripts {r['json_scripts']} · code blocks {r['code_blocks']}",
        f"payloads     {r['payloads']}",
        *[f"             - {u[:140]}" for u in r["payload_urls"][:20]],
        f"apply        {r['apply_kind']} · text={r['apply_text']!r} · href={r['apply_href']}",
        *([f"redirects    {' → '.join(r['apply_redirects'])}"] if r["apply_redirects"] else []),
        f"html         {r['html_kb']} KB · visible text {r['text_chars']} chars · screenshot {shot or 'no'}",
        *blocks,
    ]
    if parsed_desc:
        out += [f"--- description, parsed (first {text_chars} chars) ---", parsed_desc[:text_chars]]
    else:
        out += [f"--- visible text (first {text_chars} chars) ---", r["page_text"][:text_chars]]
    return "\n".join(out)


def format_batch(store, batch_id: str | None) -> str:
    batch = store.get_batch(batch_id)
    if not batch:
        return "no batches yet" if not batch_id else f"no batch {batch_id}"
    runs = store.get_search_runs(batch["batch_id"])
    ended = batch.get("finished_at") or "-"
    note = "  (interrupted)" if batch.get("interrupted") else ""
    out = [f"batch {batch['batch_id']}  started {batch['started_at']}  finished {ended}{note}", ""]
    for src, s in batch.get("sources", {}).items():
        out.append(
            f"{src:<10} status={s['status']:<16} pages={s['pages_loaded']:<4} details={s['details_fetched']:<4} "
            f"outcomes={s['outcomes']} stops={s['stop_reasons']}"
        )
        if s.get("rate_pauses"):
            out.append(f"{'':<10} rate pauses: {s['rate_pauses']} ({s['rate_paused_minutes']} min)")
        if s.get("error"):
            out.append(f"{'':<10} error: {s['error']}")
    out.append("")
    out.append(f"{'run':>3} {'source':<10} {'search':<26} {'stop':<11} {'pages':>5} outcomes")
    for r in runs:
        out.append(
            f"{r['search_run_id']:>3} {r['source']:<10} {(r.get('search_name') or '')[:26]:<26} "
            f"{str(r.get('stop_reason') or 'running'):<11} {r.get('pages_loaded') or 0:>5} {r.get('outcomes') or {}}"
        )
    out.append("")
    out.append(f"stored in: {store.describe()} · log: {store.log_path(batch['batch_id'])}")
    return "\n".join(out)
