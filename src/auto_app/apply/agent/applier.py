"""The `agent` applier (docs/step3-agent.md).

Starting a job (prepare; `enter` / `R` / `r` in Apply, `apply run`) — a trial run: a hidden browser (your Chrome, a
dedicated profile `browser-profiles/agent/`; visible with AUTO_APP_APPLY__AGENT_HEADLESS=false) opens the apply link
and Claude, with the session's tools (session.py), signs in / creates the account if the site asks and fills every
step up to the last one — **never the final submit** (guards.py). Then a last check: a required field still empty
becomes a question for you. What you need to answer → needs input; otherwise → ready (it can be submitted).
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from auto_app.apply.appliers import Applier, Outcome

LATER_STEPS = "stopped before the last step — answer, then r to go on"


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
        """s: go through every step again (your answers known) in a visible browser, click the final submit, confirm.
        f (fill only): the same up to the last step, then the window stays open for you — check, submit, close it."""
        if ctx is None:
            return Outcome("unsupported", "no profile loaded for the agent")
        return asyncio.run(self._run({**app, "questions": questions}, ctx, "fill" if fill_only else "submit"))

    async def _run(self, app: dict, ctx, mode: str) -> Outcome:
        from playwright.async_api import async_playwright

        from auto_app.apply.agent.session import AgentSession

        limit = ctx.agent_time_limit_start_s if mode == "start" else ctx.agent_time_limit_submit_s
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log = (ctx.logs / "agent" / f"{app.get('application_id', 'x')}-{stamp}.jsonl") if ctx.logs else None
        t0 = time.monotonic()
        async with async_playwright() as pw:
            try:
                headless = getattr(ctx, "agent_headless", True) if mode == "start" else False  # f / s: you watch
                browser = await (self.open_browser(pw, headless) if self.open_browser
                                 else open_browser(pw, ctx.browser_profiles, headless=headless))
            except Exception as e:
                from auto_app.apply.fill import launch_problem

                return launch_problem(e)
            try:
                if self.route:
                    await self.route(browser)
                page = browser.pages[0] if browser.pages else await browser.new_page()
                s = AgentSession(page=page, mode=mode, app=app, me=ctx.me, matcher=ctx.matcher,
                                 threshold=ctx.threshold, time_limit_s=limit, log_path=log,
                                 progress=getattr(ctx, "progress", None),
                                 **({"get_password": ctx.get_password} if getattr(ctx, "get_password", None) else {}),
                                 **({"has_password": ctx.has_password} if getattr(ctx, "has_password", None) else {}),
                                 find_account=getattr(ctx, "find_account", None))
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
                if mode == "fill" and s.result.status == "filled":  # yours now: check, submit (or not), close it
                    s._say("filled — check it in the window, submit if you like, then close it")
                    try:
                        await s.page.wait_for_event("close", timeout=0)
                    except Exception:
                        pass
                out = await (self._outcome(s, time.monotonic() - t0) if mode == "start"
                             else self._submit_outcome(s, mode, time.monotonic() - t0))
                went_on = out.kind not in ("action_needed", "unsupported", "failed")
                created = bool(s.account.get("created"))
                # recorded only when it worked — or a new account waiting for your email verification; a failed
                # sign-in isn't an account (it would make the next run sign in instead of signing up)
                if s.account.get("site") and getattr(ctx, "record_account", None) and (went_on or created):
                    pending = None if went_on else "created — waiting for email verification (or check the site)"
                    try:
                        ctx.record_account(site=s.account["site"], email=s.account["email"],
                                           created=bool(s.account.get("created")),
                                           application_id=app.get("application_id"), note=pending)
                    except Exception:
                        pass  # recording is a convenience, never the run's failure
                return out
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _submit_outcome(self, s, mode: str, took: float) -> Outcome:
        """fill only / submit: what the run ended with; a submit counts only with a confirmation on the page."""
        from auto_app.apply.fill import CHALLENGE, CONFIRMATION

        r = s.result
        cost = f" · {took / 60:.1f} min" + (f" · {s.tokens['input'] + s.tokens['output']:,} tokens" if s.tokens else "")
        asked = {q.label for q in s.asked.values()}  # asked again in this run: open again, whatever it was
        questions = [q for q in s.app.get("questions") or [] if q.label not in asked]
        have = {q.id for q in questions} | {q.label for q in questions}
        new = [q for q in s.questions() if q.id not in have and q.label not in have]
        if r.status == "needs_input" or any(q.open for q in new):
            out = Outcome("needs_input", f"agent: a step asks something new — answer it, then {mode == 'fill' and 'f' or 's'} "
                                         f"again ({r.note})" + cost)
            out.questions = questions + new
            return out
        if r.status == "filled" and mode == "fill":
            return Outcome("filled", "filled in the browser — if you submitted it, press a" + cost)
        if r.status == "submitted" and mode == "submit":
            try:
                text = await s.page.content()
                ok = "confirmation" in s.page.url or bool(CONFIRMATION.search(text))
                captcha = await s.page.locator(CHALLENGE).count()
            except Exception:
                ok, captcha = False, 0
            if ok:
                return Outcome("submitted", "submitted — confirmation shown" + cost)
            if captcha:
                return Outcome("action_needed", "a CAPTCHA after submitting — solve it, then s again" + cost)
            # never "failed" here: the next applier could apply a second time
            return Outcome("action_needed", f"the agent clicked submit, but no confirmation showed — check whether it "
                                            f"went through (the site, your email), then a (applied) or s ({r.note})" + cost)
        if r.status == "action_needed":
            return Outcome("action_needed", f"agent: {r.note}" + cost)
        return Outcome("failed", f"agent: {r.note or r.status or 'stopped without finishing'}" + cost)

    async def _outcome(self, s, took: float) -> Outcome:
        r = s.result
        cost = f" · {took / 60:.1f} min" + (f" · {s.tokens['input'] + s.tokens['output']:,} tokens" if s.tokens else "")
        if s.account.get("site"):  # an account was used: say so (creating one sends your email to the site)
            done_ = r.status not in ("action_needed", "unsupported", "timeout", None)
            what = "created an account on" if s.account.get("created") else "signed in to"
            cost = (f" · {what} {s.account['site']}" if done_ else
                    f" · tried to {'create an account on' if s.account.get('created') else 'sign in to'} "
                    f"{s.account['site']} as {s.account['email']}") + cost
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
                      (f"{len(open_)} question(s) need you" if open_ else
                       "the agent filled every step and stopped before submitting")
                      + steps + note + cost)
        out.questions = questions
        return out
