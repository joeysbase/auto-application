// The server's JSON API (src/auto_app/web/app.py)

export interface Tab { id: string; title: string; count: number }
export interface Meta {
  db: string;
  tabs: Tab[];
  sources: string[];
  filters: { name: string; version: string | null }[];
  states: { id: string; title: string }[];
  can_undo: boolean;
}

export interface JobRow {
  job_id: number;
  title: string | null;
  company: string | null;
  locations: string[];
  sources: string[];
  postings: number;
  posted_at: string | null;
  first_seen_at: string | null;
  state: string;
  state_label: string;
  step2: string | null;
  decision: string | null;
  expires: string;
  expires_at: string | null;
  archived_at: string | null;
  warnings: number;
  labels: Record<string, boolean>;
}

export interface Verdict {
  filter: string;
  version: string;
  current: boolean;
  recommend: boolean | null;
  fit: boolean | null;
  dealbreaker: boolean | null;
  says: string;
  reason: string;
  lookups: string[];
  label: boolean | null;
  label_origin: string | null;
  right: boolean | null;
}

export interface Posting {
  source: string;
  source_job_id: string;
  url: string | null;
  title: string | null;
  company: string | null;
  posted_at: string | null;
  last_seen_at: string | null;
}

export interface JobDetail extends Omit<JobRow, "warnings" | "postings"> {
  postings: Posting[];
  apply_url: string | null;
  workplace_type: string | null;
  employment_type: string | null;
  salary: string | null;
  seniority: string | null;
  warnings: string[];
  verdicts: Verdict[];
  description: string;
}

export interface JobQuery {
  tab: string;
  q?: string;
  source?: string;
  company?: string;
  filter?: string;
  verdict?: string;
  posted_within?: string;
  keep?: string; // job ids answered in this tab during this visit: they stay listed
}

/** What every answer returns: a line to show, the job as it is now, and whether undo has something. */
export interface Answer {
  message: string;
  job: JobDetail | null;
  can_undo: boolean;
  job_id?: number; // undo: the job it changed
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
  }
  return r.json();
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  jobs: (q: JobQuery) => {
    const p = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][]);
    return get<JobRow[]>(`/api/jobs?${p}`);
  },
  job: (id: number) => get<JobDetail>(`/api/jobs/${id}`),
  decide: (id: number, decision: "want_to_apply" | "not_applied", reason?: string) =>
    post(`/api/jobs/${id}/decide`, { decision, reason }),
  label: (id: number, should: boolean, filter?: string, reason?: string) =>
    post(`/api/jobs/${id}/label`, { should, filter, reason }),
  mark: (id: number, state: string) => post(`/api/jobs/${id}/mark`, { state }),
  reason: (id: number, reason: string) => post(`/api/jobs/${id}/reason`, { reason }),
  undo: () => post("/api/undo", {}),
};

function post(path: string, body: object): Promise<Answer> {
  return get<Answer>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
