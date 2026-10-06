"""The browser-agent applier, milestone A7.1 (docs/step3-agent.md): the snapshot, the tools, the guards and the start
mode — offline, on fake pages, with a scripted stand-in for the model (no model calls)."""

import asyncio

import pytest

from auto_app.apply.agent.applier import AgentApplier
from auto_app.apply.agent.guards import allowed_navigation, allowed_sites, click_refusal, site
from auto_app.apply.agent.session import AgentSession
from auto_app.apply.appliers import ApplyContext, registry
from auto_app.apply.policy import ApplyPolicy
from auto_app.apply.profile import load_me
from auto_app.apply.queue import Applications
from auto_app.core.db import tables as T
from tests.apply.test_apply_a1 import me_home
from tests.select.test_review import TTL, env  # noqa: F401 — env is a fixture

URL = "https://careers.example.com/apply/7"

FORM = """<!doctype html><html><head><title>Apply — ML Engineer</title></head><body>
<h2>My Information</h2>
<div class="step">Step 1 of 3</div>
<form>
  <label for="fn">First Name*</label><input id="fn" required>
  <label for="em">Email*</label><input id="em" type="email" required>
  <div><div>What is your favorite color?*</div><div><input id="color" required></div></div>
  <label for="sp">Will you now or in the future require visa sponsorship?*</label>
  <select id="sp" required><option value=""></option><option>Yes</option><option>No</option></select>
  <div role="radiogroup" aria-labelledby="prior-q"><span id="prior-q">Have you worked here before?*</span>
    <label><input type="radio" name="prior" value="y" required> Yes</label>
    <label><input type="radio" name="prior" value="n"> No</label></div>
  <label><input type="checkbox" id="terms" required> I certify the information is true*</label>
  <label for="pw">Password</label><input id="pw" type="password" value="hunter2">
  <label for="cv">Resume</label><input id="cv" type="file">
  <div style="display:none"><label for="later">Later question</label><input id="later"></div>
  <a href="https://elsewhere.example.org/">Our partner site</a>
  <button type="button" id="next">Next</button>
  <button type="submit">Submit application</button>
</form>
<iframe srcdoc="<label for='q'>Embedded question*</label><input id='q' required>"></iframe>
</body></html>"""

DESCRIPTION = """<!doctype html><html><body><h1>ML Engineer</h1><p>About the role…</p>
<a href="/apply/7">Apply now</a></body></html>"""


def route_for(pages: dict):
    def serve(body):  # (a handler taking two arguments would be given the request as the second)
        return lambda r: r.fulfill(content_type="text/html; charset=utf-8", body=body)

    async def route(ctx):
        for path, body in pages.items():
            await ctx.route(f"**{path}", serve(body))
        await ctx.route("https://elsewhere.example.org/**", lambda r: r.fulfill(content_type="text/html", body="away"))
    return route


async def browser(pw, headless=True):
    import tempfile

    return await pw.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True)


async def open_session(tmp_path, mode="start", pages=None, url=URL, **kw):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    ctx = await browser(pw)
    await route_for(pages or {"/apply/7": FORM})(ctx)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    me = load_me(me_home(tmp_path / "home"))
    me.profile.accounts.password = None  # the template's example password: tests give their own (get_password)
    kw.setdefault("has_password", lambda email, site: False)  # never the real Keychain in tests
    s = AgentSession(page=page, mode=mode, app={"apply_url": url, "title": "ML Engineer", "company": "Example",
                                                **kw.pop("app", {})},
                     me=me, matcher=lambda *a: {}, log_path=tmp_path / "log.jsonl", **kw)
    await s.install_guards()
    await page.goto(url)
    return pw, ctx, s


def ref(s, name, role=None):
    return next(r for r, it in s.items.items()
                if name.lower() in (it.get("name") or "").lower() and (role is None or it.get("role") == role))


def run(coro):
    return asyncio.run(coro)


def test_snapshot_sees_labels_groups_frames_and_hides_secrets(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path)
        try:
            text = await s.snapshot()
            assert "# My Information" in text and "# Step 1 of 3" in text
            assert 'textbox "First Name*" required value=""' in text
            assert 'textbox "What is your favorite color?*" required' in text  # no <label>: the text around it
            assert 'select "Will you now or in the future require visa sponsorship?*" required' in text and "options: Yes | No" in text
            assert 'radio "Yes" of “Have you worked here before?*”' in text
            assert 'checkbox "I certify the information is true*" required unchecked' in text
            assert 'value="••••"' in text and "hunter2" not in text  # a password's value is never shown
            assert "Later question" not in text  # a hidden section (a later step) isn't on the form yet
            assert "── frame 1:" in text and 'textbox "Embedded question*"' in text  # an embedded form: its own refs
            assert any(r.startswith("f1e") for r in s.items)
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_tools_fill_the_form_and_record_what_they_put_there(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path)
        try:
            await s.snapshot()
            got = await s.answer_for([{"label": "First Name*"}, {"label": "Email*"}, {"label": "What is your favorite color?*"},
                                      {"label": "Will you now or in the future require visa sponsorship?*",
                                       "type": "select", "options": ["Yes", "No"]}])
            assert "First Name: 'Alex' (from profile)" in got and "favorite color?: no answer" in got
            assert "sponsorship?: 'Yes' (from bank:requires_sponsorship)" in got
            assert await s.type(ref(s, "First Name"), "Alex") == "typed"
            assert await s.select(ref(s, "sponsorship"), "yes") == "selected “Yes”"
            assert (await s.select(ref(s, "First Name"), "x")).startswith("not a native dropdown")
            assert await s.check(ref(s, "No", "radio")) == "checked"
            assert (await s.upload_cv(ref(s, "Resume"))).startswith("CV attached")
            assert (await s.ask_user("What is your favorite color?*", options=None)).startswith("recorded")
            frame_ref = ref(s, "Embedded question")
            assert await s.type(frame_ref, "inside the frame") == "typed"
            assert await ctx.pages[0].frames[1].locator("#q").input_value() == "inside the frame"
            filled = {q.label: q for q in s.questions()}
            assert filled["First Name"].source == "profile" and filled["Will you now or in the future require visa sponsorship?"].answer == "Yes"
            assert filled["Have you worked here before?"].answer == "No"  # a radio: its group's question, the option
            assert filled["What is your favorite color?"].open  # asked: for the user
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_guards_hold_whatever_is_asked(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path)
        try:
            await s.snapshot()
            assert "submits the application" in await s.click(ref(s, "Submit application"))  # starting a job: never
            # sensitive: only the user's saved answer
            assert "call answer_for first" in await s.select(ref(s, "sponsorship"), "No")
            assert "call answer_for first" in await s.check(ref(s, "I certify"))
            await s.answer_for([{"label": "Will you now or in the future require visa sponsorship?*", "type": "select",
                                 "options": ["Yes", "No"]}])
            assert "only the user's saved answer ('Yes')" in await s.select(ref(s, "sponsorship"), "No")
            assert await s.select(ref(s, "sponsorship"), "Yes") == "selected “Yes”"
            # leaving the application's site is blocked
            assert "leads off the application" in await s.click(ref(s, "partner site"))
            assert s.page.url == URL and "elsewhere.example.org" in s.blocked[0]
            assert "blocked navigation" in await s.snapshot()
        finally:
            await ctx.close()
            await pw.stop()
    run(go())

    assert click_refusal("fill", "Next", "button", True) is None  # fill only: Next is fine
    assert "only when the user presses s" in click_refusal("fill", "Submit application", "button", True)
    assert click_refusal("submit", "Submit application", "button", True) is None
    assert click_refusal("start", "Apply now", "link", False) is None  # to the form, not a submit
    assert "submits" in click_refusal("start", "Apply", "button", True)  # a filled form's Apply button is final
    assert site("careers.adobe.com") == "adobe.com" and site("jobs.example.co.uk") == "example.co.uk"
    allowed = allowed_sites("https://careers.adobe.com/us/en/job/1")
    assert allowed_navigation("https://adobe.wd5.myworkdayjobs.com/x", allowed)
    assert not allowed_navigation("https://evil.example.net/", allowed) and allowed_navigation("about:blank", allowed)


def test_a_description_page_leads_to_the_form(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path, pages={"/apply/7": FORM, "/jobs/7": DESCRIPTION},
                                        url="https://careers.example.com/jobs/7")
        try:
            await s.snapshot()
            assert (await s.click(ref(s, "Apply now", "link"))).startswith("clicked")
            assert "First Name" in await s.snapshot()
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_time_limit(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path, time_limit_s=0)
        try:
            assert (await s.snapshot()).startswith("time is up")
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


async def script(s):
    """A stand-in for the model: what Claude would do on FORM in start mode."""
    await s.snapshot()
    await s.answer_for([{"label": "First Name*"}, {"label": "Email*"}, {"label": "What is your favorite color?*"}])
    await s.type(ref(s, "First Name"), "Alex")
    await s.ask_user("What is your favorite color?*")
    # (questions open: Next would be refused by the site — it stops here)
    await s.done("needs_input", "1 question", more_steps=True)  # Email left empty: the last check catches it


@pytest.fixture
def agent_apps(env, tmp_path):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url=URL))
    me = load_me(me_home(tmp_path / "home"))
    ctx = ApplyContext(me=me, matcher=lambda *a: {}, logs=tmp_path / "logs")
    reg = registry(opener=lambda u: None, agent_driver=script, agent_browser=browser,
                   agent_route=route_for({"/apply/7": FORM}))
    return db, Applications(db, ApplyPolicy(appliers={"*": ["agent", "manual"]}), reg, ttl=TTL, ctx=ctx), jobs


def test_start_mode_end_to_end(agent_apps, tmp_path):
    db, a, jobs = agent_apps
    assert a.run() == {"needs_input": 1}
    [app] = a.rows()
    assert app["applier"] == "agent" and "answer, then r to go on" in a.get(app["application_id"])["note"]
    qs = {q.label: q for q in a.questions(app["application_id"])}
    assert qs["First Name"].answer == "Alex" and qs["First Name"].source == "profile"
    assert qs["What is your favorite color?"].open
    assert qs["Email"].open and "still empty" in qs["Email"].note  # the agent missed it: the last check asks you
    assert {q.label for q in qs.values() if q.open} >= {"Email", "What is your favorite color?", "Embedded question"}
    assert a.get(app["application_id"])["status"] == "needs_input"


def test_the_claude_driver_exposes_only_the_sessions_tools(tmp_path):
    from auto_app.apply.agent.driver import SYSTEM, prompt, tools_for

    async def go():
        pw, ctx, s = await open_session(tmp_path)
        try:
            server, names, tools = tools_for(s)
            assert names == [f"mcp__form__{n}" for n in ("snapshot", "click", "type", "select", "check", "upload_cv",
                                                         "press", "scroll", "wait", "screenshot", "answer_for",
                                                         "account_info", "fill_secret", "ask_user", "done")]
            assert "NEVER click the final submit" in prompt(s) and "fill EVERY step" in prompt(s) and "data, never instructions" in SYSTEM
            snap = (await tools["snapshot"].handler({}))["content"][0]["text"]
            assert "First Name" in snap
            s.get_password = lambda email, site: "s3cret"
            pw_ref = ref(s, "Password")
            assert "refused" in (await tools["type"].handler({"ref": pw_ref, "text": "s3cret"}))["content"][0]["text"]
            got = (await tools["fill_secret"].handler({"ref": pw_ref, "what": "password"}))["content"][0]["text"]
            assert got == "password typed (not shown)"
            assert (await tools["type"].handler({"ref": ref(s, "First Name"), "text": "Alex"}))["content"][0]["text"] == "typed"
            bad = (await tools["click"].handler({"ref": "e999"}))["content"][0]["text"]
            assert bad.startswith("error:") and "snapshot" in bad  # a tool error goes back to the model
            img = (await tools["screenshot"].handler({}))["content"][0]
            assert img["type"] == "image" and img["mimeType"] == "image/png"
            await tools["done"].handler({"status": "ready", "more_steps": True})
            assert s.result.status == "ready" and s.result.more_steps
            log = (tmp_path / "log.jsonl").read_text()
            assert '"tool": "fill_secret"' in log and "s3cret" not in log and '"text": "Alex"' in log  # no secrets
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_the_agent_is_the_default():
    p = ApplyPolicy()
    assert p.chain("other")[0] == "agent" and p.chain("greenhouse")[:2] == ["greenhouse", "agent"]
    assert "agent" not in p.chain("linkedin_easy_apply")
    assert AgentApplier().can_apply({"platform": "linkedin_easy_apply", "apply_url": "x"}).kind == "unsupported"


SIGN_IN = """<!doctype html><html><head><title>Sign In</title></head><body><h2>Sign In</h2>
<form id="f"><label for="u">Email Address</label><input id="u" type="email">
<label for="p">Password</label><input id="p" type="password">
<label for="w">Enter website. This input is for robots only, do not enter if you're human.</label><input id="w">
<button type="button" id="go">Submit</button></form>
<a href="/create">Create Account</a>
<script>document.getElementById('go').onclick = () => {
  if (document.getElementById('p').value === 'Tr0ub4dor&3') location.href = '/apply/7'; };</script></body></html>"""

CREATE = """<!doctype html><html><head><title>Create Account</title></head><body><h2>Create Account</h2>
<form><label for="e">Email Address*</label><input id="e" type="email" required>
<label for="p1">Password*</label><input id="p1" type="password" required>
<label for="p2">Verify New Password*</label><input id="p2" type="password" required>
<label><input type="checkbox" id="t" required> I agree to the Terms and Privacy Policy*</label>
<button type="button">Create Account</button></form></body></html>"""

COVERED = """<!doctype html><html><body><button id="b" onclick="document.title='clicked'">Apply</button>
<div style="position:fixed;inset:0;background:rgba(0,0,0,.01)"></div></body></html>"""


def test_sign_in_with_your_email_and_password_unseen(tmp_path):
    pages = {"/apply/7": FORM, "/login": SIGN_IN, "/create": CREATE}

    async def go():
        pw, ctx, s = await open_session(tmp_path, mode="fill", pages=pages, url="https://careers.example.com/login",
                                        get_password=lambda email, site: "Tr0ub4dor&3" if email == "alex@example.com" else None)
        try:
            await s.snapshot()
            u, p, go_ = ref(s, "Email Address"), ref(s, "Password"), ref(s, "Submit")
            assert "honeypot" in await s.type(ref(s, "robots only"), "x")  # a field for bots: never filled
            assert "refused: a password field" in await s.type(p, "guess")  # the model never types a password
            assert await s.fill_secret(u, "email") == "typed the account email (alex@example.com)"
            assert "refused: not a password field" in await s.fill_secret(u, "password")
            assert await s.fill_secret(p, "password") == "password typed (not shown)"
            assert (await s.click(go_)).startswith("clicked")  # fill only: Workday's "Submit" on a sign-in form signs in
            assert "/apply/7" in s.page.url
            assert s.account == {"site": "careers.example.com", "email": "alex@example.com", "created": False}
            text = await s.snapshot()
            assert "Tr0ub4dor" not in text
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_creating_an_account_and_the_terms_box(tmp_path):
    pages = {"/create": CREATE}

    async def go(accept):
        pw, ctx, s = await open_session(tmp_path / str(accept), mode="fill", pages=pages, url="https://careers.example.com/create",
                                        get_password=lambda e, site: "pw-123456")
        s.me.profile.accounts.accept_terms = accept
        try:
            await s.snapshot()
            await s.fill_secret(ref(s, "Email Address"), "email")
            await s.fill_secret(ref(s, "Password*"), "password")
            await s.fill_secret(ref(s, "Verify New Password"), "password")
            assert s.account["created"] is True  # two password fields: a new account
            return await s.check(ref(s, "I agree to the Terms"))
        finally:
            await ctx.close()
            await pw.stop()
    assert "sensitive" in run(go(False)) or "answer_for" in run(go(False))  # not without your say-so …
    assert run(go(True)) == "checked"  # … accounts.accept_terms: true lets it tick the box on account forms


def test_no_password_set_means_action_needed(tmp_path):
    async def go():
        pw, ctx, s = await open_session(tmp_path, mode="fill", pages={"/login": SIGN_IN},
                                        url="https://careers.example.com/login", get_password=lambda e, site: None)
        try:
            await s.snapshot()
            return await s.fill_secret(ref(s, "Password"), "password")
        finally:
            await ctx.close()
            await pw.stop()
    assert "auto-app apply account set" in run(go())


def test_clicks_go_through_an_overlay_and_progress_is_reported(tmp_path):
    said = []

    async def go():
        pw, ctx, s = await open_session(tmp_path, pages={"/apply/9": COVERED}, url="https://careers.example.com/apply/9",
                                        progress=said.append)
        try:
            await s.snapshot()
            out = await s.click(ref(s, "Apply"))
            assert "covered" in out and await s.page.title() == "clicked"
        finally:
            await ctx.close()
            await pw.stop()
    run(go())
    assert said[0] == "reading the page" and any(x.startswith("clicked") for x in said)


def test_accounts_keychain_and_records(env, tmp_path):  # noqa: F811
    from auto_app.apply import accounts

    calls = []

    class R:
        def __init__(self, code=0, out=""):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def fake(args, **kw):
        calls.append(args)
        if args[1] == "find-generic-password":
            return R(0, "pw\n") if args[5] == "default:a@b.c" else R(44)
        return R()
    accounts.set_password("a@b.c", "pw", run=fake)
    assert calls[0][:2] == ["security", "add-generic-password"] and "default:a@b.c" in calls[0]
    assert accounts.get_password("a@b.c", "x.myworkdayjobs.com", run=fake) == "pw"  # the site's own, else the default
    assert [c[5] for c in calls[1:]] == ["x.myworkdayjobs.com:a@b.c", "default:a@b.c"]
    db = env[0]
    accounts.record(db, site="nvidia.wd5.myworkdayjobs.com", email="a@b.c", created=True, application_id=3,
                    note="verification pending")
    accounts.record(db, site="nvidia.wd5.myworkdayjobs.com", email="a@b.c", created=False)
    [row] = accounts.rows(db)
    assert row["created_by_agent"] and row["note"] == "verification pending" and row["application_id"] == 3
    assert accounts.account_site("https://nvidia.wd5.myworkdayjobs.com/en-US/x") == "nvidia.wd5.myworkdayjobs.com"


def test_refs_from_an_old_snapshot_dont_linger(tmp_path):
    page = """<!doctype html><html><body><a href="https://elsewhere.example.org/">Careers home</a>
    <button onclick="document.querySelector('a').style.display='none';
      document.body.insertAdjacentHTML('beforeend', '<button id=em onclick=&quot;document.title=\\'email\\'&quot;>Sign in with email</button>')">
      Apply</button></body></html>"""

    async def go():
        pw, ctx, s = await open_session(tmp_path, pages={"/apply/9": page}, url="https://careers.example.com/apply/9")
        try:
            await s.snapshot()
            await s.click(ref(s, "Apply"))
            await s.snapshot()  # the link is gone; the new button gets a ref the link once had
            out = await s.click(ref(s, "Sign in with email"))
            assert out.startswith("clicked") and await s.page.title() == "email" and not s.blocked
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_starting_a_job_goes_through_every_step_but_never_submits():
    for mode in ("start", "fill"):
        for name, auth in (("Proceed to create account", False), ("Sign In", False), ("Create Account", False),
                           ("Continue", True), ("Submit", True), ("Next", False), ("Continue", False)):
            assert click_refusal(mode, name, "button", False, auth=auth) is None  # sign in, Next: fine
        assert "submits the application" in click_refusal(mode, "Submit", "button", True)  # the final submit: only s
        assert "submits the application" in click_refusal(mode, "Submit application", "button", False)
    assert click_refusal("submit", "Submit application", "button", True) is None


WIZARD = """<!doctype html><html><body><form>
<div id="s1"><h2>Step 1 of 2</h2><label for="fn">First Name*</label><input id="fn" required>
  <button type="button" id="next">Next</button></div>
<div id="s2" style="display:none"><h2>Step 2 of 2</h2><label for="city">City*</label><input id="city" required>
  <button type="button" id="go">Submit</button></div></form>
<script>
const $ = i => document.getElementById(i);
$('next').onclick = () => { if ($('fn').value) { $('s1').remove(); $('s2').style.display = ''; } };
$('go').onclick = () => { if ($('city').value) location.href = CONFIRM_URL; };
</script></body></html>"""


def wizard(confirm=True):
    body = WIZARD.replace("CONFIRM_URL", "'/apply/7/confirmation'" if confirm else "'/apply/7/thanks'")
    return route_for({"/apply/7": body, "/apply/7/confirmation": "<h1>Thank you for applying</h1>",
                      "/apply/7/thanks": "<h1>Hmm</h1>"})


async def walk(s, ask_city=False):
    """A stand-in for the model on WIZARD: every step; the final submit only when the mode allows it."""
    await s.snapshot()
    await s.answer_for([{"label": "First Name*"}])
    await s.type(ref(s, "First Name"), "Alex")
    await s.click(ref(s, "Next"))
    await s.snapshot()
    if ask_city:
        await s.ask_user("City*")
        await s.done("needs_input", "step 2")
        return
    await s.type(ref(s, "City"), "Boston")
    out = await s.click(ref(s, "Submit"))
    if out.startswith("refused"):
        await s.done("filled" if s.mode == "fill" else "ready")
        if s.mode == "fill":
            asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(s.page.close()))  # you close it
        return
    await s.page.wait_for_url("**/apply/7/**")
    await s.done("submitted")


def agent_queue(env, tmp_path, script, confirm=True):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url=URL))
    me = load_me(me_home(tmp_path / "home"))
    ctx = ApplyContext(me=me, matcher=lambda *a: {}, logs=tmp_path / "logs")
    reg = registry(opener=lambda u: None, agent_driver=script, agent_browser=browser, agent_route=wizard(confirm))
    return db, Applications(db, ApplyPolicy(appliers={"*": ["agent", "manual"]}), reg, ttl=TTL, ctx=ctx), jobs


def test_trial_run_then_submit(env, tmp_path):  # noqa: F811
    db, a, jobs = agent_queue(env, tmp_path, walk)
    assert a.run() == {"ready": 1}  # the trial run: both steps filled, the final submit refused
    [app] = a.rows()
    i = app["application_id"]
    assert "filled every step and stopped before submitting" in a.get(i)["note"]
    got = {q.label: q.answer for q in a.questions(i)}
    assert got["First Name"] == "Alex" and got["City"] == "Boston"
    assert a.submit(i) == "submitted" and db.job(jobs["ml"])["state"] == "applied"  # s: submitted, confirmed


def test_fill_only_leaves_the_last_step_to_you(env, tmp_path):  # noqa: F811
    db, a, jobs = agent_queue(env, tmp_path, walk)
    a.run()
    [app] = a.rows()
    assert a.submit(app["application_id"], fill_only=True) == "ready"  # filled; the window waited for you to close it
    assert "if you submitted it, press a" in a.get(app["application_id"])["note"]
    assert db.job(jobs["ml"])["state"] == "want_to_apply"


def test_submit_without_a_confirmation_never_goes_to_another_applier(env, tmp_path):  # noqa: F811
    db, a, jobs = agent_queue(env, tmp_path, walk, confirm=False)
    a.run()
    [app] = a.rows()
    assert a.submit(app["application_id"]) == "action_needed"  # check it yourself — no second application
    assert "no confirmation showed" in a.get(app["application_id"])["note"] and a.get(app["application_id"])["applier"] == "agent"


def test_a_new_question_on_submit_comes_back_to_you(env, tmp_path):  # noqa: F811
    calls = []

    async def script(s):
        calls.append(s.mode)
        await walk(s, ask_city=(s.mode == "submit"))
    db, a, jobs = agent_queue(env, tmp_path, script)
    a.run()
    [app] = a.rows()
    assert a.submit(app["application_id"]) == "needs_input" and calls == ["start", "submit"]
    assert [q.label for q in a.questions(app["application_id"]) if q.open] == ["City"]
    assert a.get(app["application_id"])["status"] == "needs_input"


def test_sign_up_by_default_sign_in_with_account_info():
    from auto_app.apply.accounts import password_problems, plan

    site = "nvidia.wd5.myworkdayjobs.com"
    up = plan("me@mail.com", site, None, site_password=False, default_password=True)
    assert "CREATE AN ACCOUNT" in up and "already registered" in up  # no account info for this site: sign up
    known = {"created_at": "2026-10-05 12:00"}
    assert "SIGN IN" in plan("me@mail.com", site, known, False, True)  # an account on record
    assert "SIGN IN" in plan("me@mail.com", site, None, site_password=True, default_password=False)  # its own password
    missing = plan(None, site, None, False, False)
    assert missing.startswith("missing:") and "account email" in missing and "a password" in missing
    assert "a password" in plan("me@mail.com", site, None, False, False) and "email" not in plan(
        "me@mail.com", site, None, False, False).split("—")[0].replace("missing: a password (auto-app apply account set) ", "")
    assert password_problems("Tr0ub4dor&3") == []
    assert set(password_problems("password")) == {"an uppercase letter", "a number", "a symbol (e.g. ! @ # $)"}
    assert "at least 8 characters" in password_problems("Ab1!")


def test_account_info_reads_the_site_and_your_records(tmp_path):
    n = iter(range(10))

    async def go(find, has):
        pw, ctx, s = await open_session(tmp_path / f"run{next(n)}", mode="fill",
                                        pages={"/login": SIGN_IN}, url="https://careers.example.com/login",
                                        find_account=find, has_password=has)
        try:
            return await s.account_info()
        finally:
            await ctx.close()
            await pw.stop()
    assert "CREATE AN ACCOUNT" in run(go(lambda site: None, lambda e, site: site is None))
    assert "SIGN IN" in run(go(lambda site: {"created_at": "2026-10-05"} if site == "careers.example.com" else None,
                               lambda e, site: site is None))
    assert run(go(lambda site: None, lambda e, site: False)).startswith("missing: a password")


def test_the_password_can_live_in_profile_yaml_and_never_reaches_claude(tmp_path):
    from auto_app.apply.matcher import build_prompt
    from auto_app.apply.questions import Question

    home = me_home(tmp_path / "home")
    p = home / "profile" / "profile.yaml"
    p.write_text(p.read_text().replace('password: "Change-me-1!"', 'password: "Pr0file-Secret!"').replace(
        "passwords: {}", "passwords: {careers.example.com: 'S1te-Secret!'}"))
    me = load_me(home)
    assert me.profile.accounts.password == "Pr0file-Secret!"
    prompt = build_prompt([Question("q1", "Why us?", "textarea")], me, {"title": "x", "company": "y"})
    assert "Secret" not in prompt and "passwords" not in prompt  # the matcher's prompt to Claude: no accounts

    async def go():
        pw, ctx, s = await open_session(tmp_path / "s", mode="fill", pages={"/login": SIGN_IN},
                                        url="https://careers.example.com/login")
        s.me, s.get_password = me, None
        s.__post_init__()
        try:
            assert s.get_password("alex@example.com", "careers.example.com") == "S1te-Secret!"  # the site's own
            assert s.get_password("alex@example.com", "other.example.org") == "Pr0file-Secret!"  # every site
            assert "SIGN IN" in await s.account_info()  # a password for this site: sign in there
        finally:
            await ctx.close()
            await pw.stop()
    run(go())


def test_a_failed_sign_in_isnt_an_account(tmp_path):
    from auto_app.apply.accounts import plan

    old = {"created_at": "2026-10-05", "note": "not confirmed — the run ended with action needed"}
    assert "CREATE AN ACCOUNT" in plan("me@mail.com", "x.myworkdayjobs.com", old, False, True)

    hidden_signup = SIGN_IN.replace("</form>", '<div style="display:none"><input type="password" id="new1">'
                                               '<input type="password" id="new2"></div></form>')

    async def go():
        pw, ctx, s = await open_session(tmp_path, mode="fill", pages={"/login": hidden_signup},
                                        url="https://careers.example.com/login", get_password=lambda e, site: "pw-1A!xyz")
        try:
            await s.snapshot()
            await s.fill_secret(ref(s, "Password"), "password")
            return s.account
        finally:
            await ctx.close()
            await pw.stop()
    assert run(go())["created"] is False  # hidden sign-up password boxes (Workday) don't make it a sign-up
