"""Filling an application form in a browser (docs/step3-apply.md §3, §5 — the submit pass).

The form's fields are found by id (Greenhouse: the API's field names). Text and text areas are typed, the CV is
uploaded, dropdowns are chosen by option label (a plain <select>, or a custom combobox: type the label, press Enter).
Then, with submit=True, the submit button is clicked and a confirmation is awaited; with submit=False ("fill only")
the browser stays open for you to check and submit, until you close it.

Outcomes: submitted · filled (fill only, you closed the window) · action_needed (a CAPTCHA you didn't solve in time)
· failed (validation errors, no confirmation) · unsupported (the form doesn't have the expected fields → the next
applier takes over).
"""

import re

from auto_app.apply.appliers import Outcome

CONFIRMATION = re.compile(r"thank you for applying|application (?:was |has been )?(?:submitted|received)|"
                          r"we(?:'ve| have) received your application", re.I)
CHALLENGE = 'iframe[src*="recaptcha"][title*="challenge" i], iframe[src*="hcaptcha"][title*="challenge" i]'
SUBMIT = re.compile(r"submit( application)?", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", (text or "").lower()).strip()


def _pick(texts: list[str], label: str) -> int | None:
    """The option for a label: exact (ignoring case and punctuation), else one starting with it ("United States" →
    "United States +1"), else one containing it."""
    want = _norm(label)
    for test in (lambda t: t == want, lambda t: t.startswith(want), lambda t: want in t):
        hits = [i for i, t in enumerate(texts) if test(_norm(t))]
        if hits:
            return hits[0]
    return None


def _choose(page, el, labels: list[str]) -> bool:
    """Choose option(s) by label: a plain <select>, or a custom combobox (React Select — open it with the keyboard,
    type to filter if needed, click the matching option). False if an option couldn't be chosen."""
    tag = el.evaluate("e => e.tagName").upper()
    if tag == "SELECT":  # a plain dropdown: match each label against its options (exact, then "starts with" …)
        texts = el.evaluate("e => [...e.options].map(o => o.text)")
        picks = [_pick(texts, label) for label in labels]
        if any(i is None for i in picks):
            return False  # an answer that isn't one of its options: reported, never forced
        el.select_option(index=picks if len(picks) > 1 else picks[0])
        return True
    for label in labels:
        el.scroll_into_view_if_needed()
        el.focus()
        page.keyboard.press("ArrowDown")  # opens the menu
        options = page.locator('[role="option"]:visible')  # the menu's entries (not a plain <select>'s hidden options)
        try:
            options.first.wait_for(timeout=3000)
        except Exception:
            el.press_sequentially(label[:30], delay=20)  # some menus only list matches once you type
            try:
                options.first.wait_for(timeout=3000)
            except Exception:
                return False  # no options showed up: not chosen (reported, never assumed)
        texts = [options.nth(i).inner_text() for i in range(options.count())]
        i = _pick(texts, label)
        if i is None:
            page.keyboard.press("Escape")
            return False
        options.nth(i).click()
    return True


ALIASES = {"location": ["candidate-location"]}  # API field name → the id the hosted form uses (Greenhouse)


def find(page, field: str):
    """The form field for an API field name: by exact id (names like `question_1[]` aren't valid #selectors), else a
    known alias. None if it isn't on the page."""
    for fid in [field] + ALIASES.get(field, []):
        el = page.locator(f'[id="{fid}"]')
        if el.count():
            return el.first
    return None


def fill_page(page, questions, cv) -> list[str]:
    """Fill every answered question on an open page; returns the required fields that weren't found or set.

    The CV goes first: some forms (Greenhouse) read an uploaded CV and refill name / email from it, which would
    overwrite what was typed before. Text fields are checked again at the end and retyped if something changed them."""
    missing = []
    try:  # an upload or a choice made before the page has fully loaded is reset when its scripts take over
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    answered = [q for q in questions if q.source != "skipped" and q.answer not in (None, "", [])]
    files = [q for q in answered if q.type == "file"]
    for q in files:
        el = find(page, q.id)
        if el is None:
            if q.required:
                missing.append(q.label or q.id)
            continue
        el.set_input_files(str(cv or q.answer))
    if files:
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)  # let the form finish reading the CV
        except Exception:
            pass
        page.wait_for_timeout(1000)
    texts = []
    for q in answered:
        if q.type == "file":
            continue
        el = find(page, q.id)
        if el is None:
            if q.required:
                missing.append(q.label or q.id)
            continue
        if q.type == "checkbox":
            if str(q.answer).strip().lower() not in ("", "no", "false", "unchecked"):
                el.check()
            continue
        if q.type == "radio":
            group = page.locator(f'input[type=radio][name="{q.id}"]')
            labels = [(group.nth(i).evaluate("e => (document.querySelector(`label[for=\"${e.id}\"]`) || "
                                             "e.closest('label') || {innerText: e.value}).innerText"))
                      for i in range(group.count())]
            i = _pick(labels, str(q.answer))
            if i is None:
                if q.required:
                    missing.append(f"{q.label or q.id} (no option “{q.answer}”)")
            else:
                group.nth(i).check()
            continue
        combo = el.get_attribute("role") == "combobox"  # e.g. a city autocomplete behind a "text" question
        if q.type in ("select", "multiselect") or combo:
            labels = q.answer if isinstance(q.answer, list) else [str(q.answer)]
            ok = False
            for _ in range(3):  # React may re-render the field mid-action ("not attached to the DOM"): try again
                try:
                    ok = _choose(page, find(page, q.id), labels)
                    break
                except Exception as e:
                    if "not attached" not in str(e) and "detached" not in str(e):
                        raise
                    page.wait_for_timeout(300)
            if not ok and q.required:
                missing.append(f"{q.label or q.id} (couldn't choose “{q.answer}”)")
        else:
            el.fill(str(q.answer))
            texts.append(q)
    for q in texts:  # anything changed behind our back (e.g. by the CV parser)? type it again
        el = find(page, q.id)
        if el.input_value() != str(q.answer):
            el.fill(str(q.answer))
            if el.input_value() != str(q.answer) and q.required:
                missing.append(f"{q.label or q.id} (wouldn't keep the text)")
    return missing


def _confirmed(page) -> bool:
    return "confirmation" in page.url or bool(CONFIRMATION.search(page.content()))


def open_form(page, url, require: bool = True) -> Outcome | None:
    """Go to the form and wait for it; an `unsupported` outcome if there's no form. require=False: just open the page
    (Simplify's step finds the form itself — on a description page, in an embedded frame)."""
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("form, input, textarea", timeout=20_000)
    except Exception:
        if require:
            return Outcome("unsupported", "no application form on the page")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    return None


def run_on_page(page, url, questions, cv, submit=True, headless=False, challenge_wait_s=300, confirm_wait_s=30,
                before_fill=None) -> Outcome:
    """Open, (before_fill(page): e.g. Simplify's autofill), fill, then submit and wait for the confirmation — or, fill
    only, leave it to you until you close the window."""
    bad = open_form(page, url, require=before_fill is None)
    if bad:
        return bad
    if before_fill:
        result = before_fill(page)
        if isinstance(result, Outcome):  # e.g. Simplify's autofill didn't appear, a step needs you
            return result
        page = result or page  # e.g. the application form opened in a new tab
    missing = fill_page(page, questions, cv)
    if missing:
        return Outcome("unsupported", "couldn't fill: " + ", ".join(missing)[:200])
    if not submit:
        page.wait_for_event("close", timeout=0)  # you check, submit (or not), and close the window
        return Outcome("filled", "filled in the browser — if you submitted it, press a")
    return submit_and_confirm(page, headless, challenge_wait_s, confirm_wait_s)


def submit_and_confirm(page, headless=False, challenge_wait_s=300, confirm_wait_s=30) -> Outcome:
    page.get_by_role("button", name=SUBMIT).last.click()
    deadline_ms = confirm_wait_s * 1000
    waited = 0
    while waited < deadline_ms:
        if _confirmed(page):
            return Outcome("submitted", "confirmation shown")
        if page.locator(CHALLENGE).count():
            if headless:
                return Outcome("action_needed", "a CAPTCHA — solve it in a visible browser")
            deadline_ms = max(deadline_ms, challenge_wait_s * 1000)  # visible: give you time to solve it
        page.wait_for_timeout(500)
        waited += 500
    if page.locator(CHALLENGE).count():
        return Outcome("action_needed", "a CAPTCHA wasn't solved in time")
    errors = page.locator('[aria-invalid="true"]').count()
    return Outcome("failed", f"no confirmation after submitting ({errors} field(s) flagged)" if errors
                   else "no confirmation after submitting")


def chrome_context(pw, headless=False, profile_dir=None):
    """Your installed Chrome (a dedicated profile), else Playwright's Chromium."""
    from auto_app.core.browser_env import browser_env

    kwargs = {"headless": headless, "env": browser_env()}
    try:
        if profile_dir:
            return pw.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **kwargs)
        return pw.chromium.launch(channel="chrome", **kwargs).new_context()
    except Exception:
        return (pw.chromium.launch_persistent_context(str(profile_dir), **kwargs) if profile_dir
                else pw.chromium.launch(**kwargs).new_context())


def launch_problem(e: Exception) -> Outcome:
    """The browser didn't start: a readable reason (the full log goes to data/logs/browser-launch.log)."""
    from auto_app.settings import get_paths

    text = f"{type(e).__name__}: {e}"
    try:
        log = get_paths().logs / "browser-launch.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text)
        where = f" (log: {log})"
    except Exception:
        where = ""
    if re.search(r"mach rendezvous|sigbus|bootstrap_check_in", text, re.I):
        why = ("the browser crashed at start (SIGBUS in ImageIO / \"Mach rendezvous failed\") — usually a library "
               "override in the environment (DYLD_*, e.g. DYLD_LIBRARY_PATH in ~/.zshrc)")
    elif re.search(r"ProcessSingleton|profile (directory )?is already in use", text, re.I):
        why = ("the Simplify browser is already open (a fill-only window?) — one window at a time: close it, then "
               "r to start this job again")
    elif re.search(r"executable doesn't exist|playwright install", text, re.I):
        why = "Chromium isn't installed — run: uv run playwright install chromium"
    else:
        why = "the browser couldn't start: " + text.splitlines()[0][:200]
    return Outcome("failed", why + where)


def _open(open_context, pw):
    try:
        return open_context(pw), None
    except Exception as e:
        return None, launch_problem(e)


def dry_run(url, questions, cv, open_context=None, before_fill=None, route=None) -> tuple[list[dict], Outcome | None]:
    """Starting a job (§5): open the form headless, (before_fill: e.g. Simplify's autofill), fill what we have, and read
    every field back — nothing is submitted. Returns (the form's fields, a problem outcome or None)."""
    from playwright.sync_api import sync_playwright

    from auto_app.apply.form import scan_form

    with sync_playwright() as pw:
        ctx, problem = _open(open_context or (lambda pw_: chrome_context(pw_, headless=True)), pw)
        if problem:
            return [], problem
        try:
            if route:
                route(ctx)
            page = ctx.new_page()
            bad = open_form(page, url, require=before_fill is None)
            if bad:
                return [], bad
            if before_fill:
                result = before_fill(page)
                if isinstance(result, Outcome):
                    return [], result
                if result is not None:
                    page = result  # e.g. the application form opened in a new tab
            fill_page(page, questions, cv)
            return scan_form(page), None
        except Exception as e:
            return [], Outcome("failed", f"{type(e).__name__}: {e}"[:300])
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def fill_form(url, questions, cv, submit=True, headless=False, profile_dir=None, route=None, confirm_wait_s=30,
              open_context=None, before_fill=None) -> Outcome:
    """Open the form in Chrome (a dedicated profile; or open_context(pw) — e.g. the Simplify browser), fill it, submit
    (or leave it to you). `route(context)` lets tests serve a fake form."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx, problem = _open(open_context or (lambda pw_: chrome_context(pw_, headless, profile_dir)), pw)
        if problem:
            return problem
        try:
            if route:
                route(ctx)
            page = ctx.new_page()
            return run_on_page(page, url, questions, cv, submit=submit, headless=headless, confirm_wait_s=confirm_wait_s,
                               before_fill=before_fill)
        except Exception as e:
            return Outcome("failed", f"{type(e).__name__}: {e}"[:300])
        finally:
            try:
                ctx.close()
            except Exception:
                pass
