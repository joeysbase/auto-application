import { Component, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Answer, type JobDetail, type JobQuery, type JobRow, type Meta } from "./api";

const POLL_MS = 30_000; // new jobs and verdicts from `serve` show up without a reload
const TOAST_MS = 7_000;
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

interface Toast { text: string; error?: boolean; undo?: boolean; reasonFor?: number }

// ---- the app ----

export default function App() {
  const initial = useMemo(readUrl, []);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [query, setQuery] = useState<JobQuery>(initial.query);
  const [keep, setKeep] = useState<number[]>([]); // answered in this tab: they stay listed until you leave it
  const [rows, setRows] = useState<JobRow[]>([]);
  const [selected, setSelected] = useState<number | null>(initial.job);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [pick, setPick] = useState(0); // the filter g / b label (← / → or 1–9)
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<Toast | null>(null);
  const [reasonFor, setReasonFor] = useState<number | null>(null); // the reason box is open for this job
  const [lastAnswered, setLastAnswered] = useState<number | null>(null);
  const [menu, setMenu] = useState(false); // the state menu (m)
  const [busy, setBusy] = useState(false);
  const search = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([api.meta(), api.jobs({ ...query, keep: keep.join(",") })]);
      setMeta(m);
      setRows(r);
      setError(null);
      return r;
    } catch (e) {
      setError(String((e as Error).message ?? e));
      return null;
    } finally {
      setLoading(false);
    }
  }, [query, keep]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => setKeep([]), [query.tab]); // leaving a tab lets the jobs you answered there go

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

  useEffect(() => setPick(0), [selected]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  const set = (patch: Partial<JobQuery>) => setQuery((q) => ({ ...q, ...patch }));

  /** Send one answer; show what happened (with undo); keep the job listed here; maybe move on to the next job. */
  const answer = useCallback(
    async (jobId: number, send: () => Promise<Answer>, opts: { advance?: boolean; reasonable?: boolean } = {}) => {
      if (busy) return;
      setBusy(true);
      const i = rows.findIndex((r) => r.job_id === jobId);
      const next = rows[i + 1]?.job_id;
      try {
        const a = await send();
        setKeep((k) => (k.includes(jobId) ? k : [...k, jobId]));
        setLastAnswered(jobId);
        if (a.job && a.job.job_id === selected) setDetail(a.job);
        setToast({ text: a.message, undo: a.can_undo, reasonFor: opts.reasonable ? jobId : undefined });
        if (opts.advance && next) setSelected(next);
      } catch (e) {
        setToast({ text: String((e as Error).message ?? e), error: true });
      } finally {
        setBusy(false);
        load();
      }
    },
    [busy, rows, selected, load],
  );

  const undo = useCallback(async () => {
    try {
      const a = await api.undo();
      setToast({ text: a.message });
      if (a.job_id) {
        setSelected(a.job_id);
        if (a.job) setDetail(a.job);
      }
    } catch (e) {
      setToast({ text: String((e as Error).message ?? e), error: true });
    } finally {
      load();
    }
  }, [load]);

  const decide = (decision: "want_to_apply" | "not_applied") =>
    detail && answer(detail.job_id, () => api.decide(detail.job_id, decision), { advance: true, reasonable: true });
  const label = (should: boolean, filter?: string) =>
    detail && answer(detail.job_id, () => api.label(detail.job_id, should, filter), { reasonable: true });
  const mark = (state: string) => {
    setMenu(false);
    if (detail) answer(detail.job_id, () => api.mark(detail.job_id, state));
  };
  const openReason = (jobId: number | null) => {
    if (jobId === null) setToast({ text: "decide on or label a job first, then r adds a reason", error: true });
    else setReasonFor(jobId);
  };

  // keys — as in the TUI: ↑/↓ j/k move · [ ] tab · / search · o open link · a want to apply · n not apply ·
  // ← → (or 1–9) pick a filter · g / b it should / shouldn't recommend · B no filter should · m set state ·
  // r reason · u undo · esc leave a box
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
      if (e.key === "Escape") {
        if (typing) (e.target as HTMLElement).blur();
        setMenu(false);
        return;
      }
      if (typing || reasonFor !== null || e.metaKey || e.ctrlKey || e.altKey) return;
      if (menu) {
        const n = Number(e.key);
        if (meta && n >= 1 && n <= meta.states.length) mark(meta.states[n - 1].id);
        return;
      }
      const i = rows.findIndex((r) => r.job_id === selected);
      const verdicts = detail?.verdicts ?? [];
      const keys: Record<string, () => void> = {
        ArrowDown: () => rows[i + 1] && setSelected(rows[i + 1].job_id),
        j: () => rows[i + 1] && setSelected(rows[i + 1].job_id),
        ArrowUp: () => i > 0 && setSelected(rows[i - 1].job_id),
        k: () => i > 0 && setSelected(rows[i - 1].job_id),
        "/": () => search.current?.focus(),
        ArrowLeft: () => verdicts.length && setPick((p) => (p + verdicts.length - 1) % verdicts.length),
        ArrowRight: () => verdicts.length && setPick((p) => (p + 1) % verdicts.length),
        a: () => decide("want_to_apply"),
        n: () => decide("not_applied"),
        g: () => verdicts[pick] && label(true, verdicts[pick].filter),
        b: () => verdicts[pick] && label(false, verdicts[pick].filter),
        B: () => verdicts.length && label(false),
        m: () => detail && setMenu(true),
        r: () => openReason(lastAnswered),
        u: () => undo(),
        o: () => {
          const url = detail?.apply_url ?? detail?.postings[0]?.url;
          if (url) window.open(url, "_blank", "noopener");
        },
      };
      if ((e.key === "]" || e.key === "[") && meta) {
        const t = meta.tabs.findIndex((x) => x.id === query.tab);
        set({ tab: meta.tabs[(t + (e.key === "]" ? 1 : meta.tabs.length - 1)) % meta.tabs.length].id });
      } else if (/^[1-9]$/.test(e.key) && Number(e.key) <= verdicts.length) {
        setPick(Number(e.key) - 1);
      } else if (keys[e.key]) {
        e.preventDefault();
        keys[e.key]();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

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
                <th className="num">#</th><th>posted</th><th className="company">company</th><th>title</th>
                <th className="where">where</th><th>state</th><th>expires</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.job_id} data-job={r.job_id}
                    className={`${r.job_id === selected ? "sel" : ""} ${keep.includes(r.job_id) ? "answered" : ""}`}
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
          {detail ? (
            <Guard key={detail.job_id}>
              <Detail job={detail} pick={pick} setPick={setPick} busy={busy} onDecide={decide} onLabel={label}
                      onMenu={() => setMenu(true)} onReason={() => openReason(detail.job_id)} />
            </Guard>
          ) : <p className="empty">Pick a job.</p>}
        </section>
      </main>

      <footer className="keys">
        <span><kbd>a</kbd> want to apply</span><span><kbd>n</kbd> not apply</span>
        <span><kbd>←</kbd><kbd>→</kbd> filter</span><span><kbd>g</kbd> should</span><span><kbd>b</kbd> shouldn't</span>
        <span><kbd>B</kbd> no filter should</span><span><kbd>m</kbd> state</span><span><kbd>r</kbd> reason</span>
        <span><kbd>u</kbd> undo</span>
        <span className="later"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>[</kbd><kbd>]</kbd> tab · <kbd>/</kbd> search ·
          {" "}<kbd>o</kbd> open link</span>
      </footer>

      {menu && meta && detail && (
        <div className="overlay" onClick={() => setMenu(false)}>
          <div className="menu" role="dialog" aria-label="set state" onClick={(e) => e.stopPropagation()}>
            <h3>#{detail.job_id}: set state</h3>
            {meta.states.map((s, n) => (
              <button key={s.id} disabled={s.id === detail.state} onClick={() => mark(s.id)}>
                <kbd>{n + 1}</kbd> {s.title}{s.id === detail.state ? " (now)" : ""}
              </button>
            ))}
            <p className="muted">esc closes</p>
          </div>
        </div>
      )}

      {reasonFor !== null && (
        <ReasonBox jobId={reasonFor} onClose={() => setReasonFor(null)}
                   onSave={(text) => {
                     setReasonFor(null);
                     answer(reasonFor, () => api.reason(reasonFor, text));
                   }} />
      )}

      {toast && (
        <div className={`toast ${toast.error ? "bad" : ""}`} role="status">
          <span>{toast.text}</span>
          {toast.reasonFor !== undefined && (
            <button onClick={() => openReason(toast.reasonFor!)}>add a reason <kbd>r</kbd></button>
          )}
          {toast.undo && <button onClick={undo}>undo <kbd>u</kbd></button>}
        </div>
      )}
    </div>
  );
}

interface DetailProps {
  job: JobDetail;
  pick: number;
  setPick: (i: number) => void;
  busy: boolean;
  onDecide: (d: "want_to_apply" | "not_applied") => void;
  onLabel: (should: boolean, filter?: string) => void;
  onMenu: () => void;
  onReason: () => void;
}

function Detail({ job, pick, setPick, busy, onDecide, onLabel, onMenu, onReason }: DetailProps) {
  const link = job.apply_url ?? job.postings[0]?.url;
  const extras = [job.workplace_type, job.employment_type, job.seniority, job.salary]
    .filter((x): x is string => typeof x === "string" && x !== "");
  const locked = Boolean(job.archived_at); // a record: labels yes (training data), decisions and states no
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

      <div className="actions">
        <button className="primary" disabled={busy || locked || job.state === "applied"}
                onClick={() => onDecide("want_to_apply")}>
          Want to apply <kbd>a</kbd>
        </button>
        <button disabled={busy || locked || job.state === "applied"} onClick={() => onDecide("not_applied")}>
          Not applying <kbd>n</kbd>
        </button>
        <button disabled={busy || locked} onClick={onMenu}>Set state… <kbd>m</kbd></button>
        <button disabled={busy} onClick={onReason}>Reason… <kbd>r</kbd></button>
        {locked && <span className="muted">archived: labels only</span>}
      </div>

      <h2>
        Filters
        {job.verdicts.length > 1 && (
          <button className="link" disabled={busy} onClick={() => onLabel(false)}>no filter should <kbd>B</kbd></button>
        )}
      </h2>
      {job.verdicts.length === 0 && <p className="muted">Not judged yet.</p>}
      {job.verdicts.map((v, i) => (
        <div key={v.filter} onClick={() => setPick(i)}
             className={`verdict ${v.recommend ? "yes" : v.recommend === false ? "no" : "unread"} ${i === pick ? "picked" : ""}`}>
          <div className="vhead">
            {job.verdicts.length > 1 && <kbd>{i + 1}</kbd>}
            <strong>{v.filter}</strong>
            <span className="says">{v.says}</span>
            {!v.current && <span className="muted" title={`judged by version ${v.version}`}>older version</span>}
            <span className="you">
              {v.label === null ? "" : v.label ? "you: 👍 should recommend" : "you: 👎 shouldn't recommend"}
              {v.label === null ? "" : v.right === null ? "" : v.right ? " → it was right" : v.label ? " → it missed it" : " → a bad pick"}
              {v.label_origin === "implied" ? " (implied by your decision)" : ""}
            </span>
          </div>
          {v.reason && <p className="reason">{v.reason}</p>}
          {v.lookups.length > 0 && (
            <details>
              <summary>{v.lookups.length} lookup{v.lookups.length === 1 ? "" : "s"}</summary>
              <ul>{v.lookups.map((l, n) => <li key={n}>{l}</li>)}</ul>
            </details>
          )}
          <div className="vbtns">
            <button disabled={busy} className={v.label === true ? "on" : ""}
                    onClick={(e) => { e.stopPropagation(); setPick(i); onLabel(true, v.filter); }}>
              👍 should recommend {i === pick && <kbd>g</kbd>}
            </button>
            <button disabled={busy} className={v.label === false ? "on" : ""}
                    onClick={(e) => { e.stopPropagation(); setPick(i); onLabel(false, v.filter); }}>
              👎 shouldn't {i === pick && <kbd>b</kbd>}
            </button>
          </div>
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

function ReasonBox({ jobId, onSave, onClose }: { jobId: number; onSave: (t: string) => void; onClose: () => void }) {
  const [text, setText] = useState("");
  return (
    <div className="overlay" onClick={onClose}>
      <form className="menu reasonbox" onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => { e.preventDefault(); if (text.trim()) onSave(text.trim()); }}>
        <h3>A reason for your last answer on #{jobId}</h3>
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => e.key === "Escape" && onClose()}
               placeholder="e.g. too senior · it's sales, not engineering" />
        <p className="muted">enter saves · esc closes · your own words help the filters learn</p>
      </form>
    </div>
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
