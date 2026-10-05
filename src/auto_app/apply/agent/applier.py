"""The `agent` applier (docs/step3-agent.md, milestone A7.1: starting a job).

Starting a job (prepare) — a dry run: a hidden browser (your Chrome, a dedicated profile `browser-profiles/agent/`)
opens the apply link and Claude fills step 1 of the form with the session's tools (session.py); Next and submit are
refused (guards.py). Then a last check: a required field still empty becomes a question for you. What you need to
answer → needs input; otherwise → ready. Fill only / submit through every step come with A7.3.
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from auto_app.apply.appliers import Applier, Outcome

LATER_STEPS = "step 1 of a multi-step form; the next steps are filled when you submit"


async def open_browser(pw, profiles: Path | None, headless: bool):
    """Your Chrome with a dedicated profile (Playwright's Chromium if Chrome isn't installed); no DYLD_*, automation
    markers hidden (sites check them)."""
    import tempfile

    from auto_app.apply.simplify_browser import QUIET
    from auto_app.core.browser_env import browser_env

    profile = (profiles / "agent") if profiles else Path(tempfile.mkdtemp())
    kw = dict(headless=headless, env=browser_env(), args=QUIET["args"], ignore_default_args=QUIET["ignore_default_args"],
              viewport={"width": 1280, "height": 900})
    try:
        return await pw.chromium.launch_persistent_context(str(profile), channel="chrome", **kw)
    except Exception:
        return await pw.chromium.launch_persistent_context(str(profile), **kw)


class AgentApplier(Applier):
    name = "agent"

    def __init__(self, driver=None, open_browser=None, route=None):
        self.driver = driver  # async (session) -> None; default: Claude through the Agent SDK (driver.py)
        self.open_browser = open_browser  # async (pw, headless) -> context; default: open_browser above
        self.route = route  # tests: async (context) -> None, serving a fake page

    def can_apply(self, app: dict) -> Outcome | None:
        if app.get("platform") == "linkedin_easy_apply":
            return Outcome("unsupported", "LinkedIn Easy Apply is never automated")
        if not app.get("apply_url"):
            return Outcome("unsupported", "no apply link")
        return None

    def prepare(self, app: dict, ctx=None) -> Outcome:
        if ctx is None:
            return Outcome("unsupported", "no profile loaded for the agent")
        return asyncio.run(self._run(app, ctx, "start"))

    def submit(self, app: dict, questions: list, ctx, fill_only: bool = False) -> Outcome:
        return Outcome("unsupported", "the agent fills and submits from milestone A7.3 on — not yet")

    async def _run(self, app: dict, ctx, mode: str) -> Outcome:
        from playwright.async_api import async_playwright

        from auto_app.apply.agent.session import AgentSession

        limit = ctx.agent_time_limit_start_s if mode == "start" else ctx.agent_time_limit_submit_s
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log = (ctx.logs / "agent" / f"{app.get('application_id', 'x')}-{stamp}.jsonl") if ctx.logs else None
        t0 = time.monotonic()
        async with async_playwright() as pw:
            try:
                browser = await (self.open_browser(pw, True) if self.open_browser
                                 else open_browser(pw, ctx.browser_profiles, headless=True))
            except Exception as e:
                from auto_app.apply.fill import launch_problem

                return launch_problem(e)
            try:
                if self.route:
                    await self.route(browser)
                page = browser.pages[0] if browser.pages else await browser.new_page()
                s = AgentSession(page=page, mode=mode, app=app, me=ctx.me, matcher=ctx.matcher,
                                 threshold=ctx.threshold, time_limit_s=limit, log_path=log,
                                 progress=getattr(ctx, "progress", None))
                await s.install_guards()
                try:
                    await page.goto(app["apply_url"], wait_until="domcontentloaded", timeout=45_000)
                except Exception as e:
                    return Outcome("failed", f"couldn't open the apply link: {type(e).__name__}")
                if self.driver:
                    await self.driver(s)
                else:
                    from auto_app.apply.agent.driver import claude_driver

                    await claude_driver(s, ctx.agent_model)
                return await self._outcome(s, time.monotonic() - t0)
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _outcome(self, s, took: float) -> Outcome:
        r = s.result
        cost = f" · {took / 60:.1f} min" + (f" · {s.tokens['input'] + s.tokens['output']:,} tokens" if s.tokens else "")
        if r.status in (None, "timeout"):
            why = r.note or "the agent stopped without finishing"
            return Outcome("unsupported", f"agent: {why}{cost}")  # start mode: on to the next applier
        if r.status in ("unsupported", "action_needed"):
            return Outcome(r.status, f"agent: {r.note or r.status}{cost}")
        if r.status not in ("ready", "needs_input"):
            return Outcome("unsupported", f"agent ended with {r.status!r} in a dry run{cost}")
        await s.unfilled_required()  # a required field still empty → a question for you
        questions = s.questions()
        open_ = [q for q in questions if q.open]
        steps = f" — {LATER_STEPS}" if r.more_steps else ""
        note = f" ({r.note})" if r.note else ""
        out = Outcome("needs_input" if open_ else "ready",
                      (f"{len(open_)} question(s) need you" if open_ else "the agent filled every required field")
                      + steps + note + cost)
        out.questions = questions
        return out
