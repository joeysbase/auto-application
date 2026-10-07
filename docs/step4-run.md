# Step 4 — Run: the scheduler and the manager agent

Status: **R1 ✅ R2 ✅** (2026-10-06: `auto-app serve` — as built §11; the manager in Claude Code — as built §12); R3–R4 📐 designed / parked. **2026-10-06, your call: the manager is Claude Code** (the CLI or the desktop
app, on this project) with project skills; Claude Code handles memory and the conversation. **Slack is deferred**
(§4's Slack parts, §6–§8). **Applying from Slack (§6, R4) is parked** while automated applying is off: *Apply* in Slack only queues the job (want to apply), and you apply by hand.

**Goal:** the app runs by itself and talks to you in Slack. It's a simple loop, like a person checking the job sites
regularly: **collect** (until caught up), then **filter the round's new jobs as one batch**, and repeat after a fixed
pause that starts when collecting ends (the next round also waits for the batch to be judged). Recommendations are sent to you. A **manager agent** takes your
feedback, starts applying when you say *Apply*, and submits only when you say *Submit*.

```
launchd (KeepAlive) ──► auto-app serve
                          loop:
                            wait: inside the window, not paused, no broken filter
                            collect      = auto-app run (collect → parse → normalize → dedup), until the round's goal
                            filter batch = filter run (every job not judged yet), started right after collecting
                            wait: the pause (from the end of collecting) AND the filter batch done
                          then: wake the manager (Claude with skills) if the batch recommended something
                                → it looks them up with the CLI and writes the digest ◄──► Slack DM (you)
```

**Why:** today a run is collection only (`auto-app run`: collect → parse → normalize → dedup), started at fixed daily
times by launchd (`auto-app schedule`). Filtering and reviewing are by hand (`filter run`, `auto-app ui`), and
notifications are macOS banners only.

---

## 1. One long-running process: `auto-app serve`

- **Started by launchd with KeepAlive** (`auto-app serve install`): it restarts after a crash, a reboot or a log-out /
  log-in. `serve status / pause / resume / stop` control it locally, and so does the manager in Slack. It replaces
  `auto-app schedule`'s fixed times (that command stays for one-off daily runs).
  The service's `PATH` is the folders where `uv` and `claude` were found at install time, `~/.local/bin`, then the
  system folders (launchd doesn't pass your shell's), so filters can run `claude`.
- **Simple by design (2026-10-06, your call: "isn't it just collect and filter?"):** the loop above, one step at a time,
  reusing the two existing commands (`auto-app run`, `filter run`). No per-job event pipeline, queue tables, control
  socket or extra concurrency.
- **It holds the run lock** (`data/run.lock`) while collecting, so a manual `auto-app run` refuses then. The TUI
  (`auto-app ui`) works alongside (the same SQLite database, WAL).
- **Pause / resume / status** through a small state file, `data/serve.json` (paused until, the last round's report,
  when the next round is due). `auto-app serve pause [--until …] / resume / status` write and read it; the loop
  checks it before each round. The Slack manager runs inside `serve`.
- **Keeps the Mac awake** during the collect window (`caffeinate -i` while collecting or applying); outside it, the Mac
  may sleep and `serve` wakes with it.
- **Logs:** `data/logs/serve.log`, plus the existing per-batch, per-agent-run and per-judgment logs.
- **Recovers from restarts:** postings are saved as they're collected, `filter run` picks up whatever isn't judged
  yet, and `data/serve.json` keeps the pause / next-round state, so a restart simply carries on.

## 2. The schedule: rounds (designed 2026-10-05)

Collecting is a repeated browsing session, like a person checking the job sites regularly. The unit is a **round**: one
session over every source's searches, with a **goal** that says when it's done. Rounds repeat with a **fixed pause that
starts when collecting ends**; the next round also waits until the round's jobs are judged. Inside your daily window.

```
round starts ─► collecting, every source in parallel (until the goal: caught up / time cap / …)
             ─► collecting ends ─► the pause starts (the interval)   ┐ judging runs during the pause
                                                                     ┘
             ─► next round: once the pause is over AND the filter batch is done (nothing left unjudged)
```

In `config/serve.yaml` (git-ignored; template `config/serve.example.yaml`), also changeable through the manager:

```yaml
window:   { days: mon-sun, start: "08:00", end: "23:00" }   # rounds and digests only in here (your time zone)
round:                                    # a round's goal: it ends at whichever comes first
  until: caught_up                        # every search has reached what's already been seen (below)
  time_limit: 30m                         # a safety cap (per source)
  # max_new_postings: 50                  # optional volume caps (filter cost, site load)
  # max_pages: 40
  stop_when_caught_up: true               # a timed round that's caught up early ends early (false: keep browsing)
interval: 30m                             # the pause after collecting ends (± 20% jitter); the next round also
                                          # waits for the round's filter batch to finish
sources:                                  # optional, per source: its own goal (or off)
  linkedin:  { time_limit: 20m }
  companies: { every: 6h }                # company career sites change slowly: one round in 6 h is plenty
filter:
  workers: 3                              # judgments at once (as `filter run --parallel`)
  tries_per_job: 3                        # then the job is unjudgeable for that filter
  broken_after: 3                         # different jobs failing in a row for a filter → the filter is broken
digest:   { min_gap: 60m }                # the manager is woken for a digest at most this often (§4)
```

### 2.1 A round's goal

A round ends at **the first goal reached**:

- **`caught_up`** (the default, and the natural "done"): **every search has seen everything new since the last
  round.** Results come newest first, and the collector checks each posting against the database. A search is caught
  up when it reaches postings already collected (`early_stop_known_streak`: 15 in a row), or postings older than
  `posted_within_days` (7), or the end of its results. For example: page 1 has 12 new postings and 13 known; page 2
  has 2 more known, making 15 in a row, so the search is caught up after 2 pages with 12 new. When every search is
  caught up, the round is done, whether that took 3 minutes or 25.
- **`time_limit`** (a cap per source, e.g. 30 min): the time is **spread across searches** (page by page, round-robin;
  searches still finding new postings get more turns), so the first search can't take it all. At the limit,
  collecting stops cleanly, using the collector's existing interrupt path: what's saved stays. Caught up early → it
  ends early (`stop_when_caught_up`), rather than paging through old results to fill the time.
- **`max_new_postings`, `max_pages`** (optional): volume caps, to keep filter cost or site load predictable.
- **Per-site safety limits always win** (Step 1's `SourceLimits`: delays, pages per hour / day, cooldowns after a
  block). A round can end with "LinkedIn's hourly limit" before reaching its goal.
- **Not caught up** (stopped by a cap or a limit) means there may be unseen postings. The next round starts from the
  top again and finds the rest (known postings are skipped quickly).

### 2.2 The pause starts when collecting ends; the next round waits for filtering too

- **The pause (`interval`) starts when collecting ends.** Judging the round's jobs runs during the pause.
- **The next round starts when both are true:** the pause is over, **and** the round's **filter batch** is done (§3) —
  the **backlog** is 0 (no collected job is
  waiting to be judged, unjudgeable ones aside). So the real gap between rounds **adapts by itself** to how much the
  round found:
  - a light round, judged within the pause, waits exactly the pause (e.g. 30 min);
  - a heavy round (many new jobs, or a slow filter) waits until judging finishes, longer than the pause.

  Collecting never outpaces filtering, with no extra setting.
- **A little jitter** on the pause (± 20%), so rounds don't start at the same minute every time.
- **Outside the window:** no round starts. Judging still finishes the backlog, so the first morning round starts clean.
- **Pause / resume** by hand: `serve pause [until <time>]` / `serve resume`, or from Slack ("pause until Monday 9am").
  Pausing stops new rounds; judging what's queued goes on.

### 2.3 When filtering can't keep up: the edge cases

| case | how it's detected | filtering | collecting | you |
|---|---|---|---|---|
| **No usage left** (Claude Code plan limit, API rate / credit limit, DeepSeek balance) | the runner's own error ("usage limit reached … resets at 3pm", HTTP 429 / 402, "insufficient balance") | pauses until the reset time it reports (none given: retry every 15 min) | **waits** (the backlog isn't 0), then goes on by itself | one notice: "Claude usage used up — resumes about 3pm" |
| **A job can't be judged** | it fails for a filter `tries_per_job` (3) times, while the same filter judges other jobs fine (a timeout, an unreadable description, an answer that won't parse) | marked **unjudgeable** for that filter, with the reason; it no longer counts as backlog | goes on | listed in the app (Jobs → Not evaluated, marked *unjudgeable*); retry with `filter run --job N` |
| **A filter is broken** | the filter itself fails: its config or command won't load, an unknown model, a login or API key rejected, or `broken_after` (3) different jobs in a row fail for it | that filter stops; the others keep judging what's queued | **stopped**: no new round until you fix it and resume | **notified instantly**: "filter ml-engineer is broken: <reason> — collecting stopped" |

- **A usage limit never counts against a job or a filter:** it isn't their fault, and it fixes itself at the reset.
- **Job vs filter:** a job is marked unjudgeable only if its filter is working on other jobs meanwhile. If several
  different jobs fail in a row, the filter is broken, and those jobs stay in the backlog to be judged once it's fixed.
- **After "broken", collecting resumes only when you say so** (`serve resume`, or "resume" in Slack). The filter
  first catches up on the backlog; then the next round starts. The loop checks for a pending backlog at every tick,
  so `resume` takes effect in a `serve` that's already running (no restart needed).
- **Notifications** go to macOS for now, and to Slack once the manager exists (R2), each once rather than repeatedly.

### 2.4 The round report

Each round ends with a report, in the log, in `serve status`, and for the manager. It covers each source's new
postings, pages and time, and what ended it. For example:

```
round 14:30–14:41 · linkedin: caught up in 11 min, 37 new / 6 pages · companies: caught up, 4 new
                  · 41 judged (5 recommended) in 9 min · next round ≈ 15:11 (pause 30 min; judged in time)
```

## 3. Filtering: one batch after each round's collecting

- **After collecting ends**, `serve` runs **`filter run`** once: it judges **every job not judged yet** (the round's new
  ones, plus anything left from before), as one batch, with `filter.workers` judgments at once (`--parallel`). It uses
  the same runners as today (API, the Claude Code CLI or DeepSeek) and the same cost limits. Jobs become recommended or
  un-recommended as now.
- **Not one by one:** a job collected early in a round waits until collecting is done (usually a few minutes with
  `caught_up`, at most the time cap). That's your choice, for simplicity.
- **The batch runs during the pause.** The next round starts when the pause is over **and** the batch is done (§2.2).
- **Its results feed the edge cases** (§2.3): a usage-limit stop → wait for the reset, then run the batch again; a job
  failing 3 times → unjudgeable (a small per-job, per-filter tries count, so `filter run` skips it); a broken filter →
  notify and stop the loop.
- **Recommended jobs → the manager's digest** (§4): `serve` wakes it after the batch.

## 4. The digest: written by the manager (designed 2026-10-06)

**`serve` decides *when*, the manager writes *what*** (your call). The manager finds the new recommendations itself
with the CLI, so `serve` has no digest logic of its own.

1. **The trigger:** after a round's filter batch finishes, if it recommended something and you're inside the window,
   `serve` wakes the manager with a short prompt ("a round just finished: tell the user what's new"). It wakes it at
   most once per `digest.min_gap` (default 60 min). What came in meanwhile waits for the next wake-up. Nothing new →
   no wake-up, no message.
2. **The lookup:** the manager runs `job list --state recommended --not-notified --json`, then `job show` /
   `filter why` for details (its `job-briefing` skill, §5.4).
3. **The digest:** one message, written for you. For example:
   - grouped ("3 ML engineering, 1 research");
   - the strongest match first, with the filter's reason in a line and the link;
   - your stated preferences applied ("2 are in NYC: skip them?");
   - anything else that needs you: a broken filter, a login to renew, jobs queued but not applied to.
4. **Marked as sent:** `job notified N…` marks each job it told you about (a per-job `notified_at`). A job judged late,
   after a digest, is still announced next time, and nothing is repeated.
5. **Your reply** goes to the same manager in the same conversation, e.g. "apply to the first two, skip the NYC
   ones". It answers with `job mark`, `filter label`, … as in §5.
6. **If Claude isn't available** (usage used up, the CLI failing), `serve` sends a plain fallback itself: "4 new
   recommendations — the manager can't run right now (Claude usage resets about 3pm)". The jobs stay not-notified, so
   the next manager digest still covers them.

- **Conversation first, buttons later:** you reply in words. *Apply / Not for me / 👍 / 👎* buttons need a posting
  command that sends Slack cards (`auto-app slack post …`) and handlers that save the same way as the TUI. They're
  optional, after R2.
- **Everything is saved as in the TUI** (`reviews`, `feedback`), so `job browse`, `filter audit` and `filter eval` see
  it.
- **Cost:** one manager call per digest, plus your conversations. Waking only after rounds that recommended something,
  and at most once per `digest.min_gap`, keeps it small.
- **A quiet day:** an evening summary only if you ask for it (`digest.daily_summary: true`; the manager writes it too).

## 5. The manager agent: Claude with skills (designed 2026-10-06)

**The idea (yours):** the manager is **Claude, taught by skills how to use the app**. The app already has a full CLI,
so a skill tells Claude *when* to use which command, *how to read its output*, and *what the rules are*. We don't code
a tool for every action, and the manager gets more capable as skills are added. Claude Opus 5.5 (your choice), on your
Claude Code login. `serve` (§1) *starts* conversations (the digest §4, alerts §2.3); the manager *answers* and acts.

### 5.1 What you'd do with it

1. **Stay informed:** "what's new since this morning?", "anything from NVIDIA this week?", "why was #41 recommended?",
   "compare #41 and #57", "which of today's sponsor visas / are remote?", a daily or weekly summary.
2. **Decide quickly:** "apply to the two ML ones, skip the rest", bulk rules ("skip anything in NYC" or "needing 5+
   years": previewed, then confirmed), "remind me about #41 tomorrow".
3. **Teach the filters:** "this shouldn't have been recommended: it's sales" → a 👎 label with the reason. Repeated
   feedback becomes a **proposed filter edit** ("6 rejections for needing a clearance: add it as a dealbreaker?",
   shown as a diff of the filter's command, applied on your OK). "How good is my ml-engineer filter?" "Show me
   rejected jobs that might be good."
4. **Run the pipeline:** "pause until Monday", "make rounds 20 minutes", "also search for *applied scientist* in
   Seattle", "add Stripe's careers page", "why no new jobs today?" (health, blocks, cooldowns, an expired login, a
   broken filter, usage).
5. **Track applications** (manual for now): "what's in my queue?", "mark #41 applied", "did I apply to Adobe
   before?", nudges ("5 jobs queued 3 days ago").
6. **Insights:** which sources find the jobs you apply to, new roles per week, the companies that post the most.

### 5.2 What the app already offers for it

✅ built · 📐 designed (not built) · ✗ missing

| demand | what covers it |
|---|---|
| **1. Informed:** what's new, by company | ✅ `job list --state recommended` (`--company`, `--source`, `--limit`; newest first) |
| job details, comparisons | ✅ `job show N --description`: company, places, dates, apply link, postings, description |
| why it was recommended | ✅ `filter why N` (`--trace`: every lookup): each filter's verdict, reason, what it looked up |
| sponsorship, remote, … | ✅ partly: read from `job show` / `filter why`. ✗ no field filters on `job list` |
| daily / weekly summary | 📐 round reports (§2.4) and the digest (§4). ✗ no `--since` on `job list` |
| **2. Decide:** apply / skip, several at once | ✅ `job mark N… --as want_to_apply \| not_applied` |
| bulk rules | ✅ `job list` to find them, then `job mark`; the skill does preview → confirm |
| a reason with the decision | ✗ TUI only (`r` after `a` / `n`): `job mark` has no `--reason` |
| snooze / remind | ✗ |
| **3. Teach:** 👍 / 👎 per filter, with a reason | ✗ TUI only (`g` / `b` / `B`): no command line |
| how good a filter is | ✅ `filter eval [-f F]`: precision and noise against your labels |
| rejected jobs that might be good | ✅ `filter audit` draws samples (labelling them: TUI only, as above) |
| edit a filter from feedback | ✅ filters are folders (`filters/<name>/command.md`, settings, parser) Claude can read and propose a diff for; `filter show` checks one; `filter run --rescore` re-judges |
| re-judge a job | ✅ `filter run --job N` |
| **4. Pipeline:** collect / filter now | ✅ `auto-app run`, `filter run` |
| schedule | ✅ `schedule install / show / remove` (fixed daily times); 📐 `serve` rounds, `serve pause [--until] / resume / status` (§1–§2) |
| add a search | ✅ partly: `config/searches.yaml` (edited, shown to you first); `source plan` shows what would run |
| add a company | ✅ `source add <careers URL>` (detects the platform, writes the plugin); `source show` |
| why nothing new | ✅ `source health` (ok / needs_login / cooldown; `--reset`), `collect stats [batch]`, `data/logs/`; 📐 broken-filter and usage alerts (§2.3) |
| an expired login | ✅ `source login <source>` opens Chrome for **you** to log in (Claude tells you; it can't log in) |
| **5. Applications:** the queue | ✅ `apply queue` (or `job list --state want_to_apply`) |
| mark applied / not applying | ✅ `job mark N --as applied \| not_applied` |
| history | ✅ `apply list`, `job list --state applied --company …` |
| nudges | ✅ the data (decision dates); 📐 posted by the digest / manager |
| **6. Insights** | ✅ partly: `data inspect`, `collect stats`, `filter eval`. ✗ no summary or JSON to compute from |

### 5.3 What's missing (built with R3)

1. **`--json` output** on `job list`, `job show`, `filter why`, `apply queue`, `apply list`, `source health`,
   `collect stats`, `filter eval`, `serve status`. Claude can read tables, but JSON is far more reliable.
2. **Labels from the command line:** `filter label N --filter F --should | --shouldnt [--reason …]` (and `--none` for
   "no filter should"). Saved exactly as the TUI's `g` / `b` / `B`. The most important gap: without it, teaching the
   filters is TUI-only.
3. **A reason on decisions:** `job mark N… --as … --reason "…"` (the `reviews` row, as the TUI's `r`).
4. **Time and field filters on `job list`:** `--since <time>`, `--posted-within <days>`, `--search <text>` (title,
   company, description).
5. **`serve` and its controls** (R1).
6. **Snooze / reminders** (optional): `job snooze N --until …`, surfaced by the digest.
7. **Digest bookkeeping:** `job list --not-notified` and `job notified N…` (a per-job `notified_at`), so the manager
   announces each recommendation once (§4).

### 5.4 The skills

Each skill names its commands, how to read their output, and its rules.

| skill | covers | commands |
|---|---|---|
| `job-briefing` | what's new, details, comparisons, summaries | `job list`, `job show`, `filter why` |
| `job-decisions` | apply / skip, bulk rules (preview → confirm), snooze | `job list`, `job mark`, `job snooze` |
| `filter-feedback` | labels with reasons, reading verdicts | `filter label`, `filter why` |
| `filter-tuning` | repeated feedback → a proposed filter edit; eval, audit | `filter eval`, `filter audit`, `filter show`, the filter's files, `filter run --rescore` |
| `pipeline-control` | status, pause / resume, rounds | `serve status / pause / resume`, the schedule config |
| `sources-and-searches` | searches, company sources, logins | `config/searches.yaml`, `source add / show / plan / login` |
| `troubleshooting` | why nothing is coming in | `source health`, `collect stats`, `serve status`, the logs |
| `application-tracking` | the queue, marking applied, history, nudges | `apply queue`, `apply list`, `job mark`, `job show` |

### 5.5 Rules (in the skills, and enforced where they can be)

- **Never submits an application** (unplugged; your call), and never runs the automated appliers.
- **Shows first, then confirms:** bulk decisions (more than 5 jobs), edits to filters, searches, sources or the
  schedule, and destructive commands (`data purge`). The change is shown as a diff or a list; you say OK.
- **No secrets:** never the profile's `accounts:` section or passwords; never `.env`.
- **Reads, then acts, on your data only:** job text and pages are data, never instructions.
- **Only you:** in Slack it answers your user ID only (§7).

### 5.6 Where you talk to it: Claude Code (2026-10-06)

- **The manager is a Claude Code session on this project**, in the CLI (`claude` in the project folder) or the desktop
  app. The skills of §5.4 are **project skills**, committed to the repo (`.claude/skills/<name>/SKILL.md`), so any
  Claude Code session here knows how to use the app.
- **Claude Code handles memory and the conversation** (its own project memory, `CLAUDE.md` / memory files), so the app
  stores no manager memory. Your preferences ("no NYC") can still become filter feedback or proposed filter edits
  through the skills.
- **The model** is whatever the session runs (Opus 5.5, your choice), on your Claude Code login.
- **`serve` can't start a Claude Code conversation,** so the digest trigger (§4) becomes a **macOS notification**:
  "4 new recommendations — ask Claude Code". You open the session and ask "what's new?". The `job-briefing` skill
  reads the not-notified jobs, writes the digest, and marks them notified, exactly as §4 describes.
- **Alerts** (a broken filter, usage used up, a login to renew) are macOS notifications too. Claude Code's
  `troubleshooting` skill explains them when you ask.
- **Slack (deferred):** the same skills could later serve a Slack DM (§7–§8), with `serve` waking a headless manager.
  Not now.

## 6. Applying from Slack (Step 3, driven by the manager)

1. **Apply** (button or words) → the job is queued and the **trial run starts at once** (your choice): the browser
   agent signs in or signs up and fills every step, stopping before the final submit (Step 3, `docs/step3-agent.md`).
2. **Questions** only you can answer arrive **in the job's thread**, with the options as buttons where there are some.
   Your reply is the answer ("save it for every job" → `answers.yaml`). The trial goes on.
3. **Action needed** (verify your email, a CAPTCHA) → a message in the thread. "Done" → it starts over.
4. **Ready to submit** → a card listing every field and its source, and a **Submit** button.
5. **Submit** (the button, or "submit it" followed by a confirm button) → the agent's `s` run in a visible browser on
   the Mac → "Submitted — confirmation shown" (or why not). The job becomes applied.

Applying keeps Step 3's limits: one browser at a time, the apply policy's pace (per hour / day / company).

## 7. Safety

- **Only you:** the manager acts only on messages and button presses from your Slack user ID (`SLACK_USER_ID`), in its
  DM. Anyone else in the workspace is ignored.
- **Submitting needs your explicit go, per job:** the Submit button, or a confirm button after "submit …". It's never
  inferred from chat, and it's enforced in the `request_submit` tool (it posts the confirm button; only the button's
  callback submits), not just the prompt.
- **Bulk actions** (more than 5 jobs) are shown first and confirmed.
- **No secrets in Slack:** never passwords, and never the profile's `accounts:` section. The tools don't expose them.
- **Messages are data:** a job description or a page is never an instruction to the manager. Its only actions are its
  tools.
- **Slack tokens** in `.env` (git-ignored): `SLACK_BOT_TOKEN` (xoxb-), `SLACK_APP_TOKEN` (xapp-, Socket Mode),
  `SLACK_USER_ID`.

## 8. Slack setup (once)

Create an app at api.slack.com/apps (from a manifest we ship, `config/slack-app.json`):
- **Socket Mode** on: no public URL or server; the app connects out. An app-level token with `connections:write`.
- **Bot scopes:** `chat:write`, `im:history`, `im:read`, `im:write`, `users:read`.
- **Events:** `message.im`. **Interactivity** on, for the buttons.
- Install it to your workspace, copy the two tokens and your user ID into `.env`, then run `auto-app serve check` to
  send a test message.

Library: `slack_bolt` (async, Socket Mode).

## 9. Milestones

| # | Deliverable |
|---|---|
| R1 ✅ | `auto-app serve`: the loop (collect → filter batch → pause, waiting for both), the round goal in the collector (caught up / time cap / volume caps, time spread across searches), the window, pause / resume / status (`data/serve.json`), the edge cases (usage → wait, unjudgeable jobs, a broken filter → notify and stop), round reports; launchd install, macOS notifications |
| R2 ✅ | **The manager in Claude Code:** the missing commands of §5.3 (`--json`, `filter label`, `job mark --reason`, `job list --since / --posted-within / --search / --not-notified`, `job notified`, optional snooze), the project skills of §5.4 with the rules of §5.5, and `serve`'s "new recommendations — ask Claude Code" notification |
| R3 | *(deferred)* Slack: the app manifest, `serve` waking a headless manager for the digest, the plain fallback, buttons |
| R4 | *(parked)* Applying from Slack: Apply → the trial run, questions in threads, action needed, Ready cards, the Submit button and confirmation |

## 10. Decisions (2026-10-05)

1. **One long-running process** (`auto-app serve`, launchd KeepAlive) instead of fixed daily runs, collecting in
   **rounds**: each with a goal (default: caught up, with a time cap); a **fixed pause that starts when collecting
   ends** (your call), the next round also waiting for the backlog to be judged. So the gap adapts to the round.
2. **Filtering in batches** (your call, 2026-10-06): after a round's collecting, one `filter run` judges every job not
   judged yet. No per-job event pipeline; the design is just collect → filter → repeat, with the existing commands.
3. **The digest is the manager's** (your call, 2026-10-06): `serve` wakes it after a round that recommended something
   (at most once per `digest.min_gap`, 60 min); it looks the jobs up with the CLI and writes the message; each job is
   marked notified; a plain fallback when Claude is unavailable. Conversation first, buttons later.
4. **The manager agent is Claude Code** (CLI or desktop app) **with project skills** (your call, 2026-10-06), using the
   app's CLI plus the few commands it lacks (§5.3). Claude Code handles memory; Slack is deferred.
5. **Apply in Slack starts the trial run right away**; submitting needs your explicit Submit per job.
6. **The next round waits for filtering** (your call): no round starts while collected jobs wait to be judged. No usage
   left → wait (resumes by itself); a job that can't be judged → marked unjudgeable; a broken filter → you're notified
   at once and collecting stops until you resume.

Open: the defaults (`time_limit` 30 min, `interval` 30 min, 3 tries, 3 jobs in a row); the digest's `min_gap` (60 min)
and quiet-day summary; per-source goals for company career sites; whether
the manager may change filters (e.g. "stop recommending NYC jobs" → feedback, or a filter edit you confirm).

## 11. As built: R1 (2026-10-06)

- **Round goals in the collector** (`collect/goal.py`): `RoundGoal(time_limit_s, max_new_postings, max_pages)` gives
  each source a `Budget`.
  - Each search may use **the time left ÷ the searches left**, so what a quick search doesn't need flows on.
  - Two new stop reasons, `time_limit` and `goal`. Each source's summary has `caught_up` (every search ended
    `done` / `early_stop`, none skipped) and `goal_hit`.
  - It works on browser and API sources. `auto-app run --time-limit / --max-new / --round-pages` use it too.
  - No goal → unchanged behaviour.
- **The filter batch for `serve`:**
  - The runner setup moved out of the `filter run` command into `select/setup.py`.
  - `run_select`'s report lists each failure, and which filters worked.
  - **`select/health.py`** sorts failures into usage (with the reset time from the error, e.g. "resets 3pm", or the
    retry interval), broken (a login / key / model / CLI error, or `broken_after` different jobs failing with none
    working), or a job failure, counted in a new **`judge_tries`** table (migration `d49888c2fe1b`). After
    `tries_per_job`, the job is **unjudgeable** for that filter: `filter run` skips it, and its decision ignores that
    filter.
  - `filter run --job N` clears it. `unparsed` answers still count as judged (as "recommend", for high recall), not as
    failed tries.
- **The loop** (`serve.py`, `Serve`):
  - `blocked()` says why no round may start (stopped / paused / outside the window / pausing / batch pending).
  - `run_round()` collects (per-source goals, `every`, `enabled`), sets `next_round_at` = the end of collecting + the
    pause (± jitter), then runs the batch.
  - The batch repeats after a usage reset, retries failed jobs within the batch (at most `tries_per_job` times), and
    stops on a broken filter.
  - `finish()` writes the report (`data/serve.json`, `data/logs/serve-rounds.jsonl`) and sends the notifications.
  - A restart finishes a pending batch first.
- **Commands:** `serve run [--once]` (a single instance via `data/serve.lock`; `caffeinate -i` while running),
  `serve status`, `serve pause [--until]`, `serve resume` (after a broken filter: the backlog first),
  `serve install / remove` (launchd `com.auto-app.serve`, KeepAlive).
- **Settings:** `config/serve.yaml` (template `config/serve.example.yaml`, which equals the defaults).
- **Tests:** `tests/collect/test_goal.py`, the goal on both collector kinds, `tests/select/test_health.py`, and
  `tests/test_serve.py`. The serve tests use a fake clock: the pause from the end of collecting, a slow batch holding
  the next round, the window and pause, usage → wait and judge again, a broken filter → stop until resume, unjudgeable
  jobs, per-source cycles, `--once`, and the template.

## 12. As built: R2, the manager in Claude Code (2026-10-06)

- **The missing commands of §5.3:**
  - `--json` on `job list`, `job show` (`-d`: the description, plus verdicts and your labels), `filter why` (each
    judgment: verdict, reason, lookups, current version, your label), `serve status` (state + the effective config),
    `source health`, `apply queue`, `apply list`, `collect stats`;
  - `job list --since` (a duration or a date; with `--state recommended`: recommended since), `--posted-within`,
    `--search`, `--not-notified`;
  - **`job mark --reason`**: `want_to_apply` / `not_applied` now go through the same path as the app's `a` / `n`
    (a `reviews` row with the filters' verdicts and the reason; implied labels on applying);
  - **`filter label`** (`--should / --shouldnt`, `-f`, `--reason`; without `-f`: every filter, as `B`);
  - **`job notified`** (a `notified_at` column on jobs, migration `4d6750b14a8d`).
  - Snooze is not built (optional).
  - `filter eval --json` is still to do: its table is read as text for now.
- **Project skills** (`.claude/skills/<name>/SKILL.md`): `job-briefing` (the digest of §4, then `job notified`),
  `job-decisions`, `filter-feedback`, `pipeline-control`, `troubleshooting`, `application-tracking`. Each has its
  commands, how to read them, and its rules (§5.5). `filter-tuning` and `sources-and-searches` are folded into
  `filter-feedback` and `pipeline-control` for now.
- **`.claude/settings.json`:** read-only commands allowed without a prompt (`job list / show / notified`,
  `filter why / eval / list`, `source health --json`, `collect stats`, `serve status`, `apply queue / list`); every
  change asks. **Reading `.env` and `profile/` is denied.**
- **Checked live:** a headless Claude Code session in this project, asked "what's new?" (on the scratch database), used
  `job-briefing` by itself. It listed the 4 new recommendations, strongest first, with the filter's reasons, places,
  onsite / remote and the links, and asked whether to skip the weakest one.
- **Tests:** `tests/test_manager_cli.py`: the JSON outputs, `--since / --search / --not-notified`, `job notified`,
  `job mark --reason` recorded like the app, `filter label` per filter and for every filter.

## 13. Cleanup: expiry and retention (2026-10-06)

The Step 1 daily `schedule` ran `run --purge`, so every run expired jobs and dropped old data. `serve` replaced it
without the purge, so with `serve` alone **nothing expired**: closed jobs stayed in the lists, reposts merged into
old jobs, `data/` grew. Fixed:
- **Expiry is part of the job model, not of a runner.** `DbStore.refresh_job_dates`, which every write path already
  calls (dedup after collecting, judging, decisions, the apps, the queue), now also archives or deletes the jobs past
  their date (`job_ttl.expire_jobs`, the same rules as `data purge`):
  - never judged → deleted;
  - judged or decided → archived;
  - `want_to_apply` never expires.

  With `serve` running, a job goes in the round where its date passes. Read-only commands (`job list`) don't write,
  so between rounds a job can show as `due`.
- **Retention** (raw pages, sightings, logs: disk housekeeping) runs in `serve` after a round's batch, once per
  `retention` (`config/serve.yaml`, default `24h`; `null` = never). The round report and `serve status` show it.
- `data purge` and `run --purge` are unchanged (both passes, with a report).
- **Decided: no safeguard** against expiring a job that is still posted but not seen. That happens when a source
  can't collect for days, or with LinkedIn's early stop: a job deep in the results is never reached again
  (§9 caveat in step1-collect.md). Expiry stays purely time-since-last-seen. Archived jobs keep everything, and a
  posting seen again comes back as a new job with a repost warning.
