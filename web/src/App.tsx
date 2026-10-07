import { Component, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type JobDetail, type JobQuery, type JobRow, type Meta } from "./api";

const POLL_MS = 30_000; // new jobs and verdicts from `serve` show up without a reload
const SECTIONS = [
  { id: "jobs", title: "Jobs", ready: true },
  { id: "apply", title: "Apply", ready: false, when: "W3" },
  { id: "audit", title: "Audit", ready: false, when: "W4" },
  { id: "status", title: "Status", ready: false, when: "W3" },
];
const POSTED = [
  { v: "", label: "any time" },
  { v: "1", label: "past day" },
  { v: "3", label: "past 3 days" },
  { v: "7", label: "past week" },
  { v: "14", label: "past 2 weeks" },
  { v: "30", label: "past month" },
];

// ---- the URL holds the view (tab, narrowing, the open job), so a reload or a bookmark comes back to it ----

function readUrl(): { query: JobQuery; job: number | null } {
  const p = new URLSearchParams(location.search);
  const query: JobQuery = { tab: p.get("tab") ?? "recommended" };
  for (const k of ["q", "source", "company", "filter", "verdict", "posted_within"] as const) {
    const v = p.get(k);
    if (v) query[k] = v;
  }
  const job = Number(p.get("job"));
  return { query, job: Number.isFinite(job) && job > 0 ? job : null };
}

function writeUrl(query: JobQuery, job: number | null) {
  const p = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
  if (job) p.set("job", String(job));
  history.replaceState(null, "", `${location.pathname}?${p}`);
}

// ---- small formatters ----

function day(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const thisYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(thisYear ? {} : { year: "numeric" }) });
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

function where(locations: string[]): string {
  if (!locations.length) return "—";
  return locations.length > 1 ? `${locations[0]} +${locations.length - 1}` : locations[0];
}

function labelMarks(labels: Record<string, boolean>): string {
  const v = Object.values(labels);
  return (v.includes(true) ? "👍" : "") + (v.includes(false) ? "👎" : "");
}

// ---- the app ----

export default function App() {
  const initial = useMemo(readUrl, []);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [query, setQuery] = useState<JobQuery>(initial.query);
  const [rows, setRows] = useState<JobRow[]>([]);
  const [selected, setSelected] = useState<number | null>(initial.job);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const search = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([api.meta(), api.jobs(query)]);
      setMeta(m);
      setRows(r);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // keep a job selected: the one in the URL if it's in the list, else the first
  useEffect(() => {
    if (loading) return;
    if (selected === null || !rows.some((r) => r.job_id === selected)) setSelected(rows[0]?.job_id ?? null);
  }, [rows, loading]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => writeUrl(query, selected), [query, selected]);

  useEffect(() => {
    if (selected === null) {
      setDetail(null);
      return;
    }
    let live = true;
    api.job(selected).then((d) => live && setDetail(d)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [selected]);

  const set = (patch: Partial<JobQuery>) => setQuery((q) => ({ ...q, ...patch }));

  // keys: ↑/↓ or j/k move · [ / ] previous / next tab · / search · o open the job's link · esc leave the search box
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
      if (e.key === "Escape" && typing) return (e.target as HTMLElement).blur();
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const i = rows.findIndex((r) => r.job_id === selected);
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        if (rows[i + 1]) setSelected(rows[i + 1].job_id);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        if (i > 0) setSelected(rows[i - 1].job_id);
      } else if (e.key === "/") {
        e.preventDefault();
        search.current?.focus();
      } else if ((e.key === "]" || e.key === "[") && meta) {
        const t = meta.tabs.findIndex((x) => x.id === query.tab);
        const next = meta.tabs[(t + (e.key === "]" ? 1 : meta.tabs.length - 1)) % meta.tabs.length];
        set({ tab: next.id });
      } else if (e.key === "o" && detail) {
        const url = detail.apply_url ?? detail.postings[0]?.url;
        if (url) window.open(url, "_blank", "noopener");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selected, meta, query.tab, detail]);

  useEffect(() => {
    document.querySelector(`[data-job="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <div className="app">
      <header className="top">
        <span className="brand">auto-app</span>
        <nav className="sections">
          {SECTIONS.map((s) => (
            <button key={s.id} className={s.id === "jobs" ? "on" : ""} disabled={!s.ready}
                    title={s.ready ? undefined : `coming in ${s.when}`}>
              {s.title}
            </button>
          ))}
        </nav>
        <span className="db" title="the database this server reads">{meta?.db}</span>
      </header>

      <nav className="tabs" aria-label="job lists">
        {meta?.tabs.map((t) => (
          <button key={t.id} className={t.id === query.tab ? "on" : ""} onClick={() => set({ tab: t.id })}>
            {t.title} <span className="count">{t.count}</span>
          </button>
        ))}
      </nav>

      <div className="filters">
        <input ref={search} type="search" placeholder="Search title, company, source, #  ( / )" value={query.q ?? ""}
               onChange={(e) => set({ q: e.target.value })} />
        <select value={query.source ?? ""} onChange={(e) => set({ source: e.target.value })} aria-label="source">
          <option value="">every source</option>
          {meta?.sources.map((s) => <option key={s}>{s}</option>)}
        </select>
        <input placeholder="company" value={query.company ?? ""} onChange={(e) => set({ company: e.target.value })}
               className="narrow" />
        <select value={query.filter ?? ""} onChange={(e) => set({ filter: e.target.value, verdict: "" })}
                aria-label="filter">
          <option value="">any filter</option>
          {meta?.filters.map((f) => <option key={f.name}>{f.name}</option>)}
        </select>
        {query.filter && (
          <select value={query.verdict ?? ""} onChange={(e) => set({ verdict: e.target.value })} aria-label="verdict">
            <option value="">judged it</option>
            <option value="yes">recommended it</option>
            <option value="no">didn't recommend it</option>
            <option value="unreadable">couldn't be read</option>
          </select>
        )}
        <select value={query.posted_within ?? ""} onChange={(e) => set({ posted_within: e.target.value })}
                aria-label="posted">
          {POSTED.map((p) => <option key={p.v} value={p.v}>{p.label}</option>)}
        </select>
        <span className="shown">{loading ? "loading…" : `${rows.length} job${rows.length === 1 ? "" : "s"}`}</span>
      </div>

      {error && <div className="error">⚠ {error}</div>}

      <main className="split">
        <section className="list">
          <table>
            <thead>
              <tr>
                <th className="num">#</th><th>posted</th><th className="company">company</th><th>title</th><th className="where">where</th>
                <th>state</th><th>expires</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.job_id} data-job={r.job_id} className={r.job_id === selected ? "sel" : ""}
                    onClick={() => setSelected(r.job_id)}>
                  <td className="num">{r.job_id}</td>
                  <td className="nowrap">{day(r.posted_at ?? r.first_seen_at)}</td>
                  <td className="company">{r.company ?? "—"}</td>
                  <td className="title">
                    {r.title ?? "—"}
                    <span className="srcs">{r.sources.join(" · ")}{r.postings > 1 ? ` · ${r.postings} postings` : ""}</span>
                  </td>
                  <td className="where" title={r.locations.join(" · ")}>{where(r.locations)}</td>
                  <td><span className={`state s-${r.state}`}>{r.state_label}</span></td>
                  <td className={`nowrap ${r.expires.endsWith("!") || r.expires === "due" ? "warn" : "muted"}`}>
                    {r.expires}
                  </td>
                  <td className="marks">{r.warnings ? "⚠" : ""}{labelMarks(r.labels)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && !rows.length && <p className="empty">No jobs here{query.q ? ` matching “${query.q}”` : ""}.</p>}
        </section>

        <section className="detail" aria-live="polite">
          {detail ? <Guard key={detail.job_id}><Detail job={detail} /></Guard> : <p className="empty">Pick a job.</p>}
        </section>
      </main>

      <footer className="keys">
        <kbd>↑</kbd><kbd>↓</kbd> move · <kbd>[</kbd><kbd>]</kbd> tab · <kbd>/</kbd> search · <kbd>o</kbd> open link
        <span className="later">deciding and labelling come in W2: use <code>auto-app ui</code> or Claude Code</span>
      </footer>
    </div>
  );
}

function Detail({ job }: { job: JobDetail }) {
  const link = job.apply_url ?? job.postings[0]?.url;
  const extras = [job.workplace_type, job.employment_type, job.seniority, job.salary]
    .filter((x): x is string => typeof x === "string" && x !== "");
  return (
    <article>
      <h1>{job.title ?? "—"}</h1>
      <p className="sub">
        <strong>{job.company ?? "—"}</strong> · {job.locations.join(" · ") || "—"}
      </p>
      <p className="facts">
        <span className={`state s-${job.state}`}>{job.state_label}</span>
        <span>#{job.job_id}</span>
        <span>posted {day(job.posted_at)} {job.posted_at && <em>({ago(job.posted_at)})</em>}</span>
        <span>{job.archived_at ? `archived ${day(job.archived_at)}` : `expires ${job.expires}`}</span>
        {link && <a href={link} target="_blank" rel="noopener noreferrer">open the job ↗</a>}
      </p>
      {extras.length > 0 && <p className="chips">{extras.map((x) => <span key={x}>{x}</span>)}</p>}
      {job.warnings.map((w) => <p key={w} className="warning">⚠ {w}</p>)}

      <h2>Filters</h2>
      {job.verdicts.length === 0 && <p className="muted">Not judged yet.</p>}
      {job.verdicts.map((v) => (
        <div key={v.filter} className={`verdict ${v.recommend ? "yes" : v.recommend === false ? "no" : "unread"}`}>
          <div className="vhead">
            <strong>{v.filter}</strong>
            <span className="says">{v.says}</span>
            {!v.current && <span className="muted" title={`judged by version ${v.version}`}>older version</span>}
            <span className="you">
              {v.label === null ? "" : v.label ? "you: 👍 should recommend" : "you: 👎 shouldn't recommend"}
              {v.label_origin === "implied" ? " (implied)" : ""}
            </span>
          </div>
          {v.reason && <p className="reason">{v.reason}</p>}
          {v.lookups.length > 0 && (
            <details>
              <summary>{v.lookups.length} lookup{v.lookups.length === 1 ? "" : "s"}</summary>
              <ul>{v.lookups.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </details>
          )}
        </div>
      ))}

      <h2>Postings <span className="muted">({job.postings.length})</span></h2>
      <ul className="postings">
        {job.postings.map((p) => (
          <li key={`${p.source}/${p.source_job_id}`}>
            <span className="src">{p.source}</span>
            {p.url ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.title ?? p.source_job_id}</a>
                   : (p.title ?? p.source_job_id)}
            <span className="muted"> · posted {day(p.posted_at)} · last seen {day(p.last_seen_at)}</span>
          </li>
        ))}
      </ul>

      <h2>Description</h2>
      {job.description ? <div className="desc">{job.description}</div>
                       : <p className="muted">No description saved for this job.</p>}
    </article>
  );
}

/** One job that can't be shown mustn't take the whole page down. */
class Guard extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) {
    return { error: e.message };
  }
  render() {
    return this.state.error ? <p className="error">This job couldn't be shown: {this.state.error}</p> : this.props.children;
  }
}
