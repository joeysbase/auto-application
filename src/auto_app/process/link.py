"""C8c: dedup that writes jobs (docs/step1-collect.md §7.4). Incremental:

1. New postings = normalized postings not yet linked to a job.
2. Level 1b: a new posting that is an exact same-source copy of an earlier posting (existing or new)
   becomes its duplicate and joins that posting's job.
3. Level 2: the remaining new postings are grouped among themselves (dedup rules + merge vetoes),
   then each group joins the existing job it matches best — vetoes checked against every posting of
   that job — or becomes a new job.
4. Existing jobs keep their ids and state; their primary posting, keys, locations and dates are updated.
5. Archived jobs (§9.2) take no part: their postings have no parsed/normalized data, so they aren't loaded.
   New jobs that share a matching key with an archived job get a repost warning (§7.5).

`plan_links` computes everything without writing (`dedup --preview`); `apply_plan` writes it.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from auto_app.process.dedup import (
    MIN_HASH_CHARS,
    Item,
    _candidates,
    _Groups,
    _primary,
    employment_conflict,
    l1b_keys,
    load_items,
    match,
    near_miss,
    posting_order,
    veto,
)


@dataclass
class Assign:
    pid: str
    job: int | str  # an existing job id, or "new:<n>" for a job this run creates
    rule: str
    confidence: float | None
    duplicate_of: str | None = None
    detail: str = ""


@dataclass
class LinkPlan:
    items: dict[str, Item]
    existing_links: dict[str, dict]
    assigns: list[Assign] = field(default_factory=list)
    new_jobs: dict[str, list[str]] = field(default_factory=dict)  # "new:<n>" → postings
    joined: dict[int, list[str]] = field(default_factory=dict)  # existing job id → new postings
    near_misses: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def new_postings(self) -> int:
        return len(self.assigns)

    @property
    def duplicates(self) -> int:
        return sum(1 for a in self.assigns if a.duplicate_of)


def plan_links(items: dict[str, Item], links: dict[str, dict]) -> LinkPlan:
    plan = LinkPlan(items, links)
    existing = [p for p in items if p in links]
    new = sorted((p for p in items if p not in links), key=lambda p: posting_order(items[p]))
    job_of = {p: links[p]["job_id"] for p in existing}
    members: dict[int, list[str]] = defaultdict(list)
    for p in existing:
        members[job_of[p]].append(p)

    # ---- Level 1b: same-source exact copies (against existing canonical postings, then earlier new ones)
    seen: dict[tuple, str] = {}
    for p in sorted((p for p in existing if not links[p].get("duplicate_of")), key=lambda p: posting_order(items[p])):
        for k in l1b_keys(items[p]):
            seen.setdefault(k, p)
    dup: dict[str, tuple[str, str]] = {}
    for p in new:
        keys = l1b_keys(items[p])
        hit = next((k for k in keys if k in seen), None)
        if hit and not employment_conflict(items[p], items[seen[hit]]):
            dup[p] = (seen[hit], hit[0])
        else:
            for k in keys:
                seen.setdefault(k, p)
    new_canon = [p for p in new if p not in dup]

    # ---- Level 2 among new canonical postings
    new_dups = {d: v for d, v in dup.items() if v[0] in new_canon}
    uf = _Groups(items, new_canon, new_dups)
    joined_by: dict[str, tuple[str, float, str]] = {}  # member → the in-group link that brought it in
    level2 = []
    for a, b in sorted(_candidates([items[p] for p in new_canon])):
        m = match(items[a], items[b])
        if m:
            level2.append((m[1], a, b, m))
        elif why := near_miss(items[a], items[b]):
            plan.near_misses.append((a, b, why))
    for conf, a, b, (rule, c, detail) in sorted(level2, key=lambda x: -x[0]):
        blocked = uf.try_union(a, b)
        if blocked:
            plan.near_misses.append((a, b, f"{rule} match {blocked}"))
        else:
            joined_by.setdefault(b, (rule, c, f"~ {a}: {detail}"))
    groups: dict[str, list[str]] = defaultdict(list)
    for p in new_canon:
        groups[uf.find(p)].append(p)

    # ---- each group: join the best-matching existing job, or become a new job
    block: dict[tuple, list[str]] = defaultdict(list)
    for p in existing:
        it = items[p]
        for key in (("k", it.employer_key), ("c", it.company), ("h", it.desc_hash if it.desc_chars >= MIN_HASH_CHARS else None)):
            if key[1]:
                block[key].append(p)
    dups_of: dict[str, list[str]] = defaultdict(list)
    for d, (c, _) in dup.items():
        dups_of[c].append(d)

    n_new = 0
    for root, group in groups.items():
        group_all = [x for m in group for x in (m, *dups_of[m])]
        best: dict[int, tuple[float, str, str, str, str]] = {}  # job → (conf, rule, detail, member, partner)
        for m in group:
            it = items[m]
            partners = {e for key in (("k", it.employer_key), ("c", it.company), ("h", it.desc_hash))
                        for e in block.get(key, [])}
            for e in partners:
                r = match(it, items[e])
                if r and (job_of[e] not in best or r[1] > best[job_of[e]][0]):
                    best[job_of[e]] = (r[1], r[0], r[2], m, e)
        chosen = None
        for job in sorted(best, key=lambda j: (-best[j][0], j)):
            conflict = next(((x, y, v) for x in group_all for y in members[job] if (v := veto(items[x], items[y]))), None)
            if conflict:
                x, y, v = conflict
                plan.near_misses.append((best[job][3], best[job][4], f"{best[job][1]} match to job #{job} blocked: {x} and {y} — {v}"))
            elif chosen is None:
                chosen = job
            else:
                plan.near_misses.append((best[job][3], best[job][4], f"also matches job #{job} ({best[job][1]}); joined job #{chosen}"))
        if chosen is not None:
            conf, rule, detail, member, partner = best[chosen]
            target: int | str = chosen
            plan.joined.setdefault(chosen, []).extend(group_all)
            first = (member, rule, conf, f"~ {partner}: {detail}")
        else:
            n_new += 1
            target = f"new:{n_new}"
            plan.new_jobs[target] = list(group_all)
            first = (root, "new_job", None, "")
        for m in group:
            if m == first[0]:
                plan.assigns.append(Assign(m, target, first[1], first[2], detail=first[3]))
            else:
                rule, c, detail = joined_by.get(m, ("group", None, ""))
                plan.assigns.append(Assign(m, target, rule, c, detail=detail))
            for d in dups_of[m]:
                plan.assigns.append(Assign(d, target, f"1b:{dup[d][1]}", 1.0 if dup[d][1] == "employer_key" else 0.95,
                                           duplicate_of=m))

    # ---- new duplicates of postings that already have a job
    for d, (c, rule) in dup.items():
        if c in job_of:
            plan.assigns.append(Assign(d, job_of[c], f"1b:{rule}", 1.0 if rule == "employer_key" else 0.95, duplicate_of=c))
            plan.joined.setdefault(job_of[c], []).append(d)
    return plan


def job_keys_for(items: dict[str, Item], postings: list[str]) -> set[tuple[str, str]]:
    keys = set()
    for p in postings:
        it = items[p]
        if it.employer_key:
            keys.add(("employer_key", it.employer_key))
        if it.req_id and it.company:
            keys.add(("req_id", f"{it.company}|{it.req_id}"))
        if it.desc_hash and it.desc_chars >= MIN_HASH_CHARS:
            keys.add(("desc_hash", it.desc_hash))
        if it.company and it.title_norm:
            keys.add(("fingerprint", f"{it.company}|{it.title_norm}"))
    return keys


def apply_plan(store, plan: LinkPlan) -> dict[str, int]:
    """Write the plan in one transaction. Returns {"new:<n>": job id} for the jobs it created."""
    items = plan.items
    created: dict[str, int] = {}
    with store.transaction():
        for key, postings in plan.new_jobs.items():
            created[key] = store.create_job()
        for a in plan.assigns:
            job = created[a.job] if isinstance(a.job, str) else a.job
            store.link_posting(a.pid, job, a.rule, a.confidence, a.duplicate_of)
        touched: dict[int, list[str]] = defaultdict(list)
        for p, link in plan.existing_links.items():
            touched[link["job_id"]].append(p)
        for a in plan.assigns:
            touched[created[a.job] if isinstance(a.job, str) else a.job].append(a.pid)
        affected = set(created.values()) | set(plan.joined)
        for job in affected:
            postings = [p for p in touched[job] if p in items]
            primary = _primary(items, postings)
            head = items[primary]
            seen = [items[p] for p in postings]
            store.update_job(
                job,
                primary_posting=primary,
                company_norm=head.company,
                title_norm=head.title_norm,
                canonical_url=head.url,
                first_seen_at=_ts(min((i.first_seen_at for i in seen if i.first_seen_at), default=None)),
                last_seen_at=_ts(max((i.last_seen_at for i in seen if i.last_seen_at), default=None)),
            )
            keys = job_keys_for(items, postings)
            store.add_job_keys(job, keys)
            if job in created.values():
                store.add_repost_warnings(job, keys)
            store.add_job_locations(job, [
                {"location_norm": loc, "posting": p, "apply_url": items[p].url}
                for p in sorted(postings, key=lambda p: p != primary) for loc in items[p].locations
            ])
    return created


def _ts(value):
    from datetime import datetime

    return datetime.fromisoformat(value) if isinstance(value, str) else value


def link_jobs(store, registry, dry_run: bool = False, ttl=None) -> tuple[LinkPlan, dict[str, int]]:
    """ttl (jobs.ttl_days): also refresh every active job's last-seen and expiry dates."""
    if not dry_run and not hasattr(store, "create_job"):
        raise RuntimeError("writing jobs needs the database store")
    plan = plan_links(load_items(store, registry), store.posting_links())
    created = {} if dry_run else apply_plan(store, plan)
    if not dry_run and ttl is not None:
        store.refresh_job_dates(ttl)
    return plan, created


def format_link_plan(plan: LinkPlan, created: dict[str, int], dry_run: bool, limit: int = 50, show_near: bool = True) -> str:
    items = plan.items
    verb = "would" if dry_run else ""
    n_joined = sum(len(v) for v in plan.joined.values())
    out = [
        f"{plan.new_postings} new postings ({len(plan.existing_links)} already linked) → "
        f"{len(plan.new_jobs)} new jobs, {n_joined} {verb + ' ' if verb else ''}join existing jobs; "
        f"{plan.duplicates} are same-source duplicates (Level 1b); {len(plan.near_misses)} near misses",
        "",
    ]
    shown = 0
    for key, postings in plan.new_jobs.items():
        if len(postings) < 2 or shown >= limit:
            continue
        shown += 1
        label = f"job #{created[key]}" if key in created else "new job"
        out += _group_lines(plan, label, postings)
    for job, postings in plan.joined.items():
        if shown >= limit:
            break
        shown += 1
        out += _group_lines(plan, f"job #{job} (existing) {verb} gets", postings)
    if not shown:
        out.append("no job has more than one posting among the new postings")
        out.append("")
    if show_near and plan.near_misses:
        out.append("near misses (not merged — check these when calibrating):")
        for a, b, why in plan.near_misses[:limit]:
            out.append(f"   {a} ↔ {b}: {why}")
            out.append(f"        {items[a].title[:60]}  |  {items[b].title[:60]}")
    return "\n".join(out)


def _group_lines(plan: LinkPlan, label: str, postings: list[str]) -> list[str]:
    items = plan.items
    by_pid = {a.pid: a for a in plan.assigns}
    head = items[_primary(items, postings)]
    lines = [f"{label}: {head.title}  —  {head.company}"]
    for p in postings:
        a = by_pid.get(p)
        how = ""
        if a:
            how = f"duplicate of {a.duplicate_of} ({a.rule})" if a.duplicate_of else (
                "" if a.rule == "new_job" else f"{a.rule} ({a.confidence}) {a.detail}")
        lines.append(f"     {p:<34} {items[p].apply_kind:<10} {items[p].title[:44]:<44} {how}")
    lines.append("")
    return lines
