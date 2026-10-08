# Web frontend — design (2026-10-07)

A browser app for auto-app, next to the TUI (`auto-app ui` stays). Better for browsing and reading jobs: a wide list
with columns and filters, a readable detail pane, links that open in tabs.

## 1. Decisions

| | |
|---|---|
| Scope | The full app: Jobs, Apply (queue + history), Audit (with live evaluation), Status (`serve`). The TUI stays. |
| Stack | **FastAPI** JSON API (`src/auto_app/web/`) + **React / Vite / TypeScript** SPA (`web/`). |
| Access | **This Mac only**: bound to `127.0.0.1`, no login. |
| Build | `web/dist` is git-ignored; `auto-app web` builds it on first run (or with `--build`) when `npm` is found. Node is needed only to build. |
| Rules | The same code as the TUI and CLI: decisions and labels go through `Review` / `JobBrowser`, states and expiry come from the store. No submit / automated-apply endpoint. Nothing from `profile/` or `.env` is served. |

## 2. Shape

```
auto-app web [--port 8765] [--db PATH] [--open] [--build] [--dev]
  └─ uvicorn → FastAPI (src/auto_app/web/app.py)
       ├─ /api/…   JSON (one JobBrowser per server: filters, undo stack)
       └─ /        web/dist (index.html for every non-API path)
web/   React + Vite + TS; `npm run dev` proxies /api to the server (`auto-app web --dev` runs both)
```

- **One user, one process:** the server holds one `JobBrowser` (the TUI's data side: tabs, narrowing, detail,
  decide, label, mark, undo, audit draws, evaluation). Its undo stack is the web session's.
- **The database is shared** with `serve`, the TUI and Claude Code (SQLite WAL, busy timeout). The web app reads fresh on every
  request; the page polls every ~30 s so new jobs and verdicts appear.
- **Security on localhost:** the server binds to 127.0.0.1 only. Every request's `Host` must be `127.0.0.1:<port>` or
  `localhost:<port>` (against DNS rebinding), and every write (`POST`) must come with an `Origin` of the same (no
  other site can drive it from your browser).

## 3. API

| | |
|---|---|
| `GET /api/meta` | tabs with titles and counts, sources, filters (name, version), the database's name |
| `GET /api/jobs?tab=&q=&source=&company=&filter=&verdict=&posted_within=` | the rows of one tab, newest first, narrowed |
| `GET /api/jobs/{id}` | one job: fields, state and expiry, parsed extras (workplace, employment, salary, seniority), postings, verdicts (reason, lookups, fit / dealbreaker, current version, your label), warnings, description |
| W2: `POST /api/jobs/{id}/decide` `{decision, reason?}` | want_to_apply / not_applied (`Review.decide`) |
| W2: `POST /api/jobs/{id}/label` `{filter?, should, reason?}` | one filter, or every filter (`label_all`) |
| W2: `POST /api/jobs/{id}/mark` `{state}` · `POST /api/jobs/{id}/reason` · `POST /api/undo` | |
| W3: `GET /api/queue` · `POST /api/queue/{id}/applied` · `…/not_applying` | the Apply section (manual applying only) |
| W3: `GET /api/status` · `POST /api/serve/pause` `{until?}` · `POST /api/serve/resume` | `serve status`, source health, unjudgeable jobs |
| W4: `GET /api/audit` · `POST /api/audit/draw` · `GET /api/eval` | random draws per filter, the live evaluation |

## 4. Pages

1. **Jobs:** tabs as in the TUI (All, Not evaluated, New recommended, All recommended, Un-recommended, Applied,
   Not applied, Archived) with counts; a filter bar (search, source, company, a filter's verdict, posted within);
   a table (#, posted, company, title, where, sources, state, expires, ⚠ / 👍 / 👎); the detail pane on the right.
   Keys: ↑/↓ or j/k move, `/` search, `o` open the job's link, `tab` next tab; W2 adds the TUI's `a n g b B m r u`.
2. **Apply** (W3): the queue (open the link, applied / not applying, days waiting) and the history.
3. **Audit** (W4): pick a filter, label its random draws, the evaluation panel updates after each label.
4. **Status** (W3): `serve` state, the last rounds, the next round, pause / resume, source health, unjudgeable
   jobs, the cleanup line.

## 5. Milestones

| | What | Status |
|---|---|---|
| W1 | API (meta, jobs, job) + Jobs read-only (tabs, filters, table, detail) + `auto-app web` | ✅ 2026-10-07 |
| W2 | Decisions, labels, reasons, set state, undo; the keys | ✅ 2026-10-07 |
| W3 | Apply and Status (pause / resume) | |
| W4 | Audit with the live evaluation | |

Tests: the API with pytest (`fastapi.testclient`, the review fixtures); the page with a Playwright check against a
built `web/dist` (skipped when it isn't built).

## 6. As built: W1 (2026-10-07)

- **Server:**
  - `src/auto_app/web/app.py`: `create_app(browser, dist, port, db_name, allowed_hosts)`, with a middleware for the
    `Host` / `Origin` guard;
  - `GET /api/meta`, `GET /api/jobs` and `GET /api/jobs/{id}`; any other `/api/…` is a 404, and every other path
    serves `web/dist` (`index.html` for the app's routes, never a file outside `dist`).
- **The data:**
  - **Lists:** `JobBrowser.jobs(tab, q)` (the TUI's), then source, company, filter verdict
    (`audit.latest_verdicts`) and posted-within;
  - **Rows:** locations in display form (`place`), `expiry()` text, warning count, your labels;
  - **A job:** `Review.detail`, plus the verdict wording (`verdict_text`), salary as text (`job_details._salary`),
    and the postings.
- **Build:** `src/auto_app/web/build.py`:
  - `npm install` when `node_modules` is missing or older than `package-lock.json`, then `npm run build`
    (`tsc -b && vite build`);
  - `--dev` starts Vite with `AUTO_APP_API` pointing at the server and allows its origin.
- **Frontend** (`web/`): React 19, Vite 8, TypeScript; no UI library.
  - `src/api.ts` holds the types and fetchers;
  - `src/App.tsx` holds the Jobs page: tabs, filter bar, table, detail pane, keys, and the URL state. It polls every
    30 s, and a `Guard` error boundary means one job that can't render doesn't blank the page;
  - `src/styles.css` holds colour tokens for light and dark; under 900 px the list and detail stack, and the
    company, where and keys columns hide.
- **Found while testing:** `salary` is an object (min / max / currency / period / text). It crashed the detail pane
  until the API sent it as text.
- **Tests** (`tests/test_web.py`):
  - the API: tabs and counts, each kind of narrowing, a job's verdicts, 404s;
  - the localhost guard (a foreign `Host` → 403, a foreign `Origin` on a write → 403);
  - serving the build (app routes, assets, no path escape);
  - a Playwright run of the built page: list, search, URL state, the `]` key; skipped without `web/dist`.
- **Checked by hand on `data/try.db`:** all 130 jobs' detail panes render; light, dark and narrow layouts.

## 7. As built: W2 (2026-10-07)

- **API:**
  - `POST /api/jobs/{id}/decide` `{decision, reason?}` → `Review.decide`;
  - `POST /api/jobs/{id}/label` `{should, filter?, reason?}`: with a filter → `Review.label`; without →
    `JobBrowser.label_all`, then the reason on each label;
  - `POST /api/jobs/{id}/mark` `{state}`, `POST /api/jobs/{id}/reason` `{reason}` (`add_reason`: your last
    answer on that job), `POST /api/undo`.
  - Every answer returns `{message, job, can_undo}` (undo adds `job_id`). A refused answer is a 400 with the reason:
    archived jobs can't be decided on or changed, an applied job can't be decided again.
  - One lock serializes answers, keeping the undo stack and the database in step.
  - `GET /api/jobs?keep=…` keeps the jobs answered in this tab listed (`JobBrowser.jobs(keep=)`); `meta` adds
    `states` and `can_undo`.
- **Fixed in `JobBrowser.add_reason`:** after `B` (every filter labelled at once) a reason crashed, because that undo
  entry has no single filter. It now goes on each of those labels (the TUI's `r` too).
- **The page:**
  - **Buttons:** Want to apply / Not applying / Set state… / Reason… under the job's facts; 👍 should recommend /
    👎 shouldn't on each verdict; "no filter should" by the Filters heading.
  - **Keys** (the TUI's): `a`, `n`, `←` `→` or `1`–`9` to pick a filter (its card is outlined), `g`, `b`, `B`, `m`
    (a menu, `1`–`6`), `r` (a reason box for your last answer), `u`.
  - **After `a` / `n`:** the next job opens. The answered job stays in the tab, dimmed and showing its new state,
    until you leave the tab.
  - **Every answer shows a toast** with the message, "add a reason" and "undo"; errors show in red.
- **Tests:**
  - API: decide with a reason (implied labels; `keep`; undo; refusals), labels for one filter or all with reasons
    (and `reason` after `B`), mark and undo;
  - the browser test: `n`, then `r`, then `u` on the built page, checked against the database.
- **Checked by hand on a copy of `try.db`:** `a` + next job, `r`, `u`, `g`, the state menu, leaving the tab.
