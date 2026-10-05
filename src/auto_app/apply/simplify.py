"""The automated Simplify applier, `simplify_auto` (docs/step3-apply.md §3) — the default first applier for every
platform except LinkedIn Easy Apply.

Starting a job (prepare) — a dry run in the Simplify browser (simplify_browser.py: Playwright's Chromium with the
Simplify Copilot extension and your logged-in profile), headless, nothing submitted:
  1. open the form; Simplify's panel appears (inside `div.simplify-jobs-shadow-root`);
  2. the one-time consent notice ("Before you autofill") is answered as `AUTO_APP_APPLY__SIMPLIFY_CONSENT` says —
     "Essentials only" by default;
  3. click "Autofill This Page" and wait until the form stops changing;
  4. read the form back (form.py); required fields Simplify left empty are answered, where possible, from your profile
     and answers.yaml (and Claude) and filled; the form is read again;
  5. what's still empty and required → needs input; otherwise → ready to submit. Every field is recorded with where
     its value came from (simplify / profile / bank / you).
Submitting: the form is opened again, Simplify autofills it, your answers fill the rest, then submit (or fill only).

Simplify fills from YOUR Simplify profile and resume. Its panel is undocumented and changes: when it can't be found,
the job goes to the next applier. Simplify's terms may not allow automated use — your choice.
"""

import re
import time

from auto_app.apply.appliers import Applier, Outcome
from auto_app.apply.questions import resolve

PANEL = "div.simplify-jobs-shadow-root"
AUTOFILL = re.compile(r"^\s*autofill this page\s*$", re.I)
START = re.compile(r"^\s*start application\s*$", re.I)  # on a job description page: go to the application form
APPLY = re.compile(r"^\s*apply( now| for this job| to this job)?\s*$", re.I)  # the site's own button, as a fallback
CONSENT = {"essentials": re.compile(r"^\s*essentials only\s*$", re.I), "accept": re.compile(r"^\s*accept\s*$", re.I)}
CONSENT_TEXT = re.compile(r"before you autofill", re.I)
AGAIN = re.compile(r"^\s*(autofill this page|run autofill again)\s*$", re.I)  # on a later step of the form
# the site's own buttons on a multi-step form (Workday, Phenom, …): go on, or the last step (never clicked in a dry run)
NEXT = re.compile(r"^\s*(next|continue|next step|proceed|save (and|&) (continue|next))\s*$", re.I)
FINAL = re.compile(r"^\s*(submit( (my |your )?application)?|send( application)?|apply( now)?|complete application|"
                   r"finish|review and submit)\s*$", re.I)
MAX_STEPS = 10
LATER_STEPS = "the next steps are filled when you submit (new questions there come back here)"
# an application form embedded from an ATS (e.g. a careers page with ?gh_jid=): opened directly, where Simplify fills it
EMBEDS = re.compile(r"greenhouse\.io/embed/job_app|jobs\.lever\.co/.+/apply|jobs\.ashbyhq\.com/.+/embed|"
                    r"apply\.workable\.com|smartrecruiters\.com/.+/apply|jobs\.jobvite\.com|icims\.com", re.I)


def _embedded_form(page) -> str | None:
    """The address of an application form embedded in an iframe, when the page itself isn't one."""
    for frame in page.frames[1:]:
        if EMBEDS.search(frame.url or ""):
            return frame.url
    return None


def _button(page, name):
    """A button inside Simplify's panel (Playwright's locators reach into open shadow roots)."""
    return page.locator(PANEL).get_by_role("button", name=name)


def _values(page) -> tuple:
    from auto_app.apply.form import scan_form

    return tuple((f["id"], f["value"]) for f in scan_form(page, read_options=False))


def _visible(loc) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _to_application(page, appear_s: float):
    """On a job description page (no form yet), Simplify offers "Start Application" — or the site has an "Apply"
    button: click it and continue on the application form (the same tab, or the one it opens). Returns the page."""
    deadline = time.monotonic() + appear_s
    embedded_checked = False
    while time.monotonic() < deadline:
        if _visible(_button(page, AUTOFILL)):
            return page
        if not embedded_checked and time.monotonic() > deadline - appear_s + 3:  # frames have had a moment to load
            embedded_checked = True
            embed = _embedded_form(page)
            if embed:
                page.goto(embed, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                deadline = time.monotonic() + appear_s
                continue
        target = _button(page, START) if _visible(_button(page, START)) else None
        if target is None and time.monotonic() > deadline - appear_s / 2:  # no Simplify button after a while: the site's
            for loc in (page.get_by_role("button", name=APPLY), page.get_by_role("link", name=APPLY)):
                if _visible(loc):
                    target = loc
                    break
        if target is not None:
            before = len(page.context.pages)
            target.first.click()
            page.wait_for_timeout(3000)
            if len(page.context.pages) > before:  # it opened the form in a new tab
                page = page.context.pages[-1]
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            deadline = time.monotonic() + appear_s  # now wait for "Autofill This Page" on the form
        page.wait_for_timeout(1000)
    return page


def autofill(page, consent: str = "essentials", appear_s: float = 25, settle_s: float = 4, max_s: float = 90):
    """Get to the application form (clicking "Start Application" / "Apply" on a description page), click Simplify's
    "Autofill This Page" (answering the consent notice first), and wait until the form stops changing. Returns the page
    to continue on, or an `unsupported` Outcome when Simplify's autofill doesn't appear."""
    page = _to_application(page, appear_s)
    button = _button(page, AUTOFILL)
    if not _visible(button):
        return Outcome("unsupported", "Simplify's “Autofill This Page” didn't appear (no application form, not logged "
                                      "in, or its panel changed)")
    for _ in range(3):  # the consent notice can come before or after the first click
        notice = page.locator(PANEL).get_by_text(CONSENT_TEXT)
        if notice.count() and notice.first.is_visible():
            choice = _button(page, CONSENT[consent])
            if choice.count():
                choice.first.click()
                page.wait_for_timeout(500)
        if button.count() and button.first.is_visible():
            button.first.click()
            page.wait_for_timeout(1500)
        if not (notice.count() and notice.first.is_visible()):
            break
    _settle(page, settle_s, max_s)
    return page


def _settle(page, settle_s: float = 4, max_s: float = 90) -> None:
    """Wait for an autofill to finish: the form's values unchanged for settle_s (at most max_s)."""
    start = last_change = time.monotonic()
    before = _values(page)
    while time.monotonic() - start < max_s:
        page.wait_for_timeout(1000)
        now = _values(page)
        if now != before:
            before, last_change = now, time.monotonic()
        elif time.monotonic() - last_change >= settle_s:
            break


def _site_button(page, name):
    """A visible button of the page itself (not Simplify's panel, which lives in a shadow root), or None."""
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=name)
        for i in range(min(loc.count(), 10)):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.evaluate("e => e.getRootNode() === document"):
                    return el
            except Exception:
                continue
    return None


def _next_step(page) -> bool:
    """Click the form's Next / Continue; True when a new step showed (new fields or a new address)."""
    button = _site_button(page, NEXT)
    if button is None:
        return False
    before = (page.url, tuple(i for i, _ in _values(page)))
    button.click()
    for _ in range(15):
        page.wait_for_timeout(1000)
        if (page.url, tuple(i for i, _ in _values(page))) != before:
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            return True
    return False  # still the same step: the site refused (a required field, a validation error)


def walk(page, consent: str, known: dict, fill_gaps, max_steps: int = MAX_STEPS):
    """Go through a form step by step — Simplify autofills each step, `known` answers (by field id) and
    fill_gaps(page, questions) fill what's left — clicking Next until the last step. Nothing is submitted.
    Returns (page, the questions of every step, how it ended, the step reached) — "final" (the last step), "blocked"
    (questions for you on a step with more after it), "more" (max_steps reached, more steps follow), "stuck" (Next
    didn't go on) — or an Outcome when Simplify's autofill doesn't appear. Next sends the step to the site (Adobe:
    `applySubmit`), so a dry run stops at step 1 (max_steps=1)."""
    from auto_app.apply.form import field_question, reconcile, scan_form

    collected: list = []
    for step in range(1, max_steps + 1):
        if step == 1:
            page = autofill(page, consent)
            if isinstance(page, Outcome):
                return page
        else:
            again = _button(page, AGAIN)
            try:  # Simplify often autofills a new step by itself (its button disabled meanwhile): click only if enabled
                if _visible(again) and again.first.is_enabled():
                    again.first.click(timeout=5000)
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            _settle(page)
        fields = scan_form(page)
        qs = []
        for f in fields:
            k = known.get(f["id"])
            qs.append(k if k is not None else field_question(f, "simplify"))
        empty_ids = {f["id"] for f in fields if not f["value"]}
        fill_gaps(page, [q for q in qs if q.id in empty_ids])
        step_qs = reconcile(qs, scan_form(page))
        seen = {q.id for q in collected}
        collected += [q for q in step_qs if q.id not in seen]
        last = _site_button(page, FINAL) is not None or _site_button(page, NEXT) is None
        if last:
            return page, collected, "final", step  # the last step (open questions or not)
        if any(q.open for q in step_qs):
            return page, collected, "blocked", step  # Next would refuse: these need you first, then the form goes on
        if step == max_steps:
            return page, collected, "more", step
        if not _next_step(page):
            return page, collected, "stuck", step
    return page, collected, "stuck", max_steps


class SimplifyAutoApplier(Applier):
    name = "simplify_auto"

    def __init__(self, open_context=None, route=None):
        self.open_context = open_context  # (pw, headless) -> context; default: the Simplify browser (extension, profile)
        self.route = route  # tests: serve a fake page

    def _context(self, ctx, headless: bool):
        if self.open_context:
            return lambda pw: self.open_context(pw, headless)
        from auto_app.apply.simplify_browser import launch

        return lambda pw: launch(pw, ctx.browser_profiles, headless=headless)

    def can_apply(self, app: dict) -> Outcome | None:
        if app.get("platform") == "linkedin_easy_apply":
            return Outcome("unsupported", "LinkedIn Easy Apply is never automated")
        if not app.get("apply_url"):
            return Outcome("unsupported", "no apply link")
        return None

    def _known(self, app: dict) -> dict:
        """Answers from earlier passes (yours, profile, answers.yaml, Claude) by field id — filled again on the form."""
        return {q.id: q for q in app.get("questions") or []
                if q.answer not in (None, "", []) and q.source not in ("simplify", "form", "skipped")}

    def _fill_gaps(self, ctx, job):
        from auto_app.apply.fill import fill_page

        def fill(page, empty):
            mine = [q for q in empty if q.answer not in (None, "", [])]  # known answers for fields still empty
            ask = [q for q in empty if q.answer in (None, "", []) and q.required]
            if ask:  # what Simplify left empty: from your profile / answers.yaml (and Claude)
                resolve(ask, ctx.me, job, ctx.matcher, ctx.threshold)
                mine += [q for q in ask if q.answer not in (None, "", [])]
            if mine:
                fill_page(page, mine, ctx.me.cv)
        return fill

    def prepare(self, app: dict, ctx=None) -> Outcome:
        from auto_app.apply.fill import dry_run
        from auto_app.apply.form import reconcile

        if ctx is None or (self.open_context is None and not getattr(ctx, "browser_profiles", None)):
            return Outcome("unsupported", "the Simplify browser isn't set up (auto-app apply login simplify)")
        if self.open_context is None and not (ctx.browser_profiles / "simplify" / "Default").is_dir():
            return Outcome("unsupported", "not logged in to Simplify yet (auto-app apply login simplify)")
        job = {"title": app.get("title"), "company": app.get("company"), "description": ""}
        consent = getattr(ctx, "simplify_consent", "essentials")
        result: dict = {}

        def before_fill(page):
            # step 1 only: Next would send the step to the site — the other steps are filled when you submit
            walked = walk(page, consent, self._known(app), self._fill_gaps(ctx, job), max_steps=1)
            if isinstance(walked, Outcome):
                return walked
            page, result["questions"], result["end"], result["step"] = walked
            return page  # the step it stopped on (maybe in a new tab)

        fields, problem = dry_run(app["apply_url"], [], ctx.me.cv, open_context=self._context(ctx, True),
                                  before_fill=before_fill, route=self.route)
        if problem is not None:
            return problem
        questions = reconcile(result.get("questions", []), fields)
        open_ = [q for q in questions if q.open]
        steps = f" — step 1 of a multi-step form; {LATER_STEPS}" if result.get("end") in ("blocked", "more") else ""
        if open_:
            out = Outcome("needs_input", f"{len(open_)} question(s) need you{steps}")
        else:
            out = Outcome("ready", f"Simplify filled every required field{steps}")
        out.questions = questions
        return out

    def submit(self, app: dict, questions: list, ctx, fill_only: bool = False) -> Outcome:
        from auto_app.apply.fill import fill_form

        known = {q.id: q for q in questions if q.answer not in (None, "", []) and q.source not in ("simplify", "form", "skipped")}
        consent = getattr(ctx, "simplify_consent", "essentials")
        headless = bool(ctx.headless) and not fill_only
        job = {"title": app.get("title"), "company": app.get("company"), "description": ""}

        def before_fill(page):  # every step filled up to the last one; fill_form submits there (or leaves it to you)
            walked = walk(page, consent, known, self._fill_gaps(ctx, job))
            if isinstance(walked, Outcome):
                return walked
            page, found, end, step = walked
            have = {q.id for q in questions}
            new = [q for q in found if q.id not in have]
            if end == "final" and not any(q.open for q in new):
                return page
            out = Outcome("needs_input", f"step {step} asks something new — answer it, then submit again") \
                if end in ("blocked", "final") else Outcome("failed", f"couldn't get past step {step} of the form")
            out.questions = questions + new
            return out

        return fill_form(app["apply_url"], [], ctx.me.cv, submit=not fill_only, headless=headless,
                         open_context=self._context(ctx, headless), before_fill=before_fill, route=self.route)
