"""Starting a job is a dry run (docs/step3-apply.md §5): the form is filled headless and read back. Fake pages served
through Playwright routes (offline): the form reader, reconcile, the Greenhouse dry run, and simplify_auto with a fake
Simplify panel (consent notice → "Essentials only", "Autofill This Page")."""

import json

import httpx
import pytest

from auto_app.apply.appliers import ApplyContext, registry
from auto_app.apply.fill import dry_run
from auto_app.apply.form import reconcile
from auto_app.apply.greenhouse import GreenhouseApplier
from auto_app.apply.policy import ApplyPolicy
from auto_app.apply.profile import load_me
from auto_app.apply.questions import Question
from auto_app.apply.queue import Applications
from auto_app.apply.simplify import SimplifyAutoApplier
from auto_app.core.db import tables as T
from auto_app.settings import PROJECT_ROOT
from tests.apply.test_apply_a1 import me_home
from tests.select.test_review import TTL, env  # noqa: F401 — env is a fixture

FORM = """<!doctype html><html><body><form>
  <label for="first_name">First Name*</label><input id="first_name" required>
  <label for="email">Email*</label><input id="email" required>
  <label for="sponsor">Will you now or in the future require visa sponsorship?*</label>
  <select id="sponsor" required><option value=""></option><option>Yes</option><option>No</option></select>
  <label for="color">What is your favorite color?*</label><input id="color" required>
  <label for="nickname">Nickname</label><input id="nickname">
  <label><input type="checkbox" id="agree" required> I agree to the terms*</label>
  <button type="button" id="go">Submit application</button>
</form>
<script>
  document.getElementById('go').onclick = () => {
    const ok = ['first_name', 'email', 'color'].every(i => document.getElementById(i).value)
      && document.getElementById('sponsor').value && document.getElementById('agree').checked;
    if (ok) location.href = '/apply/1/confirmation';
  };
  // a fake Simplify panel: a shadow root with the consent notice and "Autofill This Page"
  if (SIMPLIFY) setTimeout(() => {
    const host = document.createElement('div'); host.className = 'simplify-jobs-shadow-root';
    document.body.appendChild(host);
    const root = host.attachShadow({mode: 'open'});
    root.innerHTML = '<div id="notice">Before you autofill<button id="ess">Essentials only</button><button>Accept</button></div>'
                   + '<button id="fill">Autofill This Page</button>';
    let consented = false;
    root.getElementById('ess').onclick = () => { consented = 'essentials'; root.getElementById('notice').remove();
                                                 window.CONSENT = consented; };
    root.getElementById('fill').onclick = () => {
      if (!consented) return;  // Simplify asks first
      document.getElementById('first_name').value = 'Sam';  // from YOUR Simplify profile
      document.getElementById('email').value = 'me@simplify.example';
      document.getElementById('agree').checked = true;
    };
  }, 300);
</script></body></html>"""


def routes(simplify=False):
    def route(ctx):
        ctx.route("**/apply/1", lambda r: r.fulfill(content_type="text/html",
                                                   body=FORM.replace("SIMPLIFY", "true" if simplify else "false")))
        ctx.route("**/apply/1/confirmation", lambda r: r.fulfill(content_type="text/html",
                                                                body="<h1>Thank you for applying</h1>"))
    return route


def chrome(pw, headless=True):
    return pw.chromium.launch(channel="chrome", headless=True).new_context()


URL = "https://jobs.example.com/apply/1"
SIMPLIFY = ApplyPolicy(appliers={"*": ["simplify_auto", "simplify", "manual"]})  # (the agent is the default now)


def test_scan_and_reconcile(tmp_path):
    qs = [Question("first_name", "First Name", "text", True, answer="Alex", source="profile"),
          Question("sponsor", "Sponsorship", "select", True, answer="Maybe", source="bank:requires_sponsorship")]
    fields, problem = dry_run(URL, qs, None, open_context=chrome, route=routes())
    assert problem is None
    by = {f["id"]: f for f in fields}
    assert by["first_name"]["value"] == "Alex" and by["first_name"]["required"]
    assert by["sponsor"]["options"] == ["Yes", "No"] and by["sponsor"]["value"] == ""  # "Maybe" isn't an option
    assert by["agree"]["type"] == "checkbox" and by["agree"]["required"] and not by["nickname"]["required"]
    merged = {q.id: q for q in reconcile(qs, fields)}
    assert merged["first_name"].answer == "Alex" and not merged["first_name"].open
    assert merged["sponsor"].open and "didn't take" in merged["sponsor"].note  # our answer didn't take: back to you
    assert merged["color"].open and merged["color"].label == "What is your favorite color?"  # not in our list: asked
    assert merged["agree"].open and merged["agree"].kind == "sensitive"  # terms: only from answers.yaml or you
    assert "nickname" in merged and not merged["nickname"].open  # optional: never asked


def test_greenhouse_start_checks_the_real_form(tmp_path):
    fixture = json.loads((PROJECT_ROOT / "tests" / "fixtures" / "greenhouse" / "job_questions.json").read_text())
    api = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=fixture)))
    seen = {}

    def fake_dry_run(url, questions, cv):  # the form has a required field the API didn't list
        seen["filled"] = {q.id for q in questions if q.answer}
        return [{"id": "first_name", "label": "First Name*", "type": "text", "required": True, "value": "Alex", "options": []},
                {"id": "pronouns", "label": "Pronouns*", "type": "select", "required": True, "value": "",
                 "options": ["she/her", "he/him", "they/them", "Prefer not to say"]}], None
    me = load_me(me_home(tmp_path))
    ctx = ApplyContext(me=me, matcher=lambda *a: {})
    out = GreenhouseApplier(http=api, dry_runner=fake_dry_run).prepare(
        {"platform": "greenhouse", "apply_url": "https://job-boards.greenhouse.io/anthropic/jobs/4461450008"}, ctx)
    q = {q.id: q for q in out.questions}["pronouns"]
    assert out.kind == "needs_input" and q.open and [o["label"] for o in q.options][0] == "she/her"
    assert "first_name" in seen["filled"]  # the dry run got our answers to fill


@pytest.fixture
def simplify_apps(env, tmp_path):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url=URL))
    me = load_me(me_home(tmp_path / "home"))
    ctx = ApplyContext(me=me, matcher=lambda *a: {}, browser_profiles=tmp_path)
    reg = registry(opener=lambda u: None, simplify_context=chrome, simplify_route=routes(simplify=True))
    return db, Applications(db, SIMPLIFY, reg, ttl=TTL, ctx=ctx), jobs


def test_simplify_auto_start_needs_input_then_submit(simplify_apps):
    db, a, jobs = simplify_apps
    assert a.run() == {"needs_input": 1}  # platform "other": simplify_auto first
    [app] = a.rows()
    assert app["applier"] == "simplify_auto"
    qs = {q.id: q for q in a.questions(app["application_id"])}
    assert (qs["first_name"].answer, qs["first_name"].source) == ("Sam", "simplify")  # Simplify's own profile
    assert qs["sponsor"].answer == "Yes" and qs["sponsor"].source == "bank:requires_sponsorship"  # the gap: ours
    assert qs["agree"].answer == "checked" and qs["agree"].source == "simplify"
    assert [q.id for q in qs.values() if q.open] == ["color"]  # only what nobody could answer
    a.answer(app["application_id"], "color", "Blue")
    assert a.get(app["application_id"])["status"] == "ready"
    assert a.submit(app["application_id"]) == "submitted" and db.job(jobs["ml"])["state"] == "applied"


def test_simplify_auto_falls_back_when_its_panel_is_missing(env, tmp_path):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url=URL))
    me = load_me(me_home(tmp_path / "home"))
    import auto_app.apply.simplify as S
    S_autofill = S.autofill
    S.autofill = lambda page, consent="essentials", **kw: S_autofill(page, consent, appear_s=2)
    try:
        reg = registry(opener=lambda u: None, simplify_context=chrome, simplify_route=routes(simplify=False))
        a = Applications(db, SIMPLIFY, reg, ttl=TTL, ctx=ApplyContext(me=me, browser_profiles=tmp_path))
        assert a.run() == {"handoff": 1}
        [app] = a.rows()
        tried = a.attempts(app["application_id"])
        assert tried[0]["applier"] == "simplify_auto" and "didn't appear" in tried[0]["reason"]
        assert app["applier"] == "simplify"  # the hand-off took over
    finally:
        S.autofill = S_autofill
    assert SimplifyAutoApplier().can_apply({"platform": "linkedin_easy_apply", "apply_url": "x"}).kind == "unsupported"


RIPPLING = """<!doctype html><html><body><form>
  <div><div><span>First name*</span></div><div><input id="field-12" placeholder="First name" required></div></div>
  <div><div>Phone number*</div>
    <div><div><input id="field-31" role="combobox" placeholder="Search" aria-required="true"></div>
         <div><input id="field-28" placeholder="Phone number" required></div></div></div>
  <div><div>What is your salary range expectation?*</div><div><input id="field-162"></div></div>
  <div><input id="x-7Hq2" placeholder="Search"></div>
  <div><div><input id="cc-search" role="combobox" placeholder="Search" aria-required="true"></div></div>
  <div><div>How many years of experience do you have?*</div>
    <div role="radiogroup"><label><input type="radio" name="q.years" value="1"> Less than 2 years</label>
      <label><input type="radio" name="q.years" value="2"> 2-4 years</label></div></div>
</form></body></html>"""


def test_labels_without_label_elements():  # Rippling: the question is the text in the field's own container
    from playwright.sync_api import sync_playwright

    from auto_app.apply.form import scan_form

    with sync_playwright() as pw:
        ctx = chrome(pw)
        page = ctx.new_page()
        page.set_content(RIPPLING)
        by = {f["id"]: f for f in scan_form(page, read_options=False)}
        ctx.close()
    assert by["field-12"]["label"] == "First name*"
    assert by["field-31"]["label"] == "Phone number country code" and by["field-31"]["required"]
    assert by["field-28"]["label"] == "Phone number*"  # the shared container's question
    assert by["field-162"]["label"] == "What is your salary range expectation?*" and by["field-162"]["required"]
    assert by["x-7Hq2"]["label"] == "x-7Hq2"  # nothing better than the id ("Search" isn't a question)
    assert "cc-search" not in by  # a dropdown's own search box (Rippling's phone country list): not a question
    years = by["q.years"]
    assert years["label"] == "How many years of experience do you have?*" and years["required"]
    assert years["options"] == ["Less than 2 years", "2-4 years"]


def test_a_browser_that_cant_start_says_why(tmp_path, monkeypatch):
    from auto_app.apply import fill

    monkeypatch.setattr("auto_app.settings.get_paths", lambda: type("P", (), {"logs": tmp_path})())

    def crash(pw, headless=True):
        raise RuntimeError("TargetClosedError: launch_persistent_context ... [0.1] Mach rendezvous failed ... signal=SIGBUS")
    fields, problem = dry_run(URL, [], None, open_context=crash)
    assert fields == [] and problem.kind == "failed" and "DYLD" in problem.reason
    assert "Mach rendezvous" in (tmp_path / "browser-launch.log").read_text()
    assert "couldn't start" in fill.launch_problem(OSError("boom")).reason
    busy = fill.launch_problem(RuntimeError("Failed to create a ProcessSingleton for your profile directory"))
    assert "already open" in busy.reason


STEPS = """<!doctype html><html><body><form>
  <div id="s1">
    <label for="first_name">First Name*</label><input id="first_name" required>
    <label for="color">What is your favorite color?*</label><input id="color" required>
    <button type="button" id="next">Next</button>
  </div>
  <div id="s2" style="display:none">
    <label for="sponsor">Will you now or in the future require visa sponsorship?*</label>
    <select id="sponsor" required><option value=""></option><option>Yes</option><option>No</option></select>
    <button type="button" id="go">Submit</button>
  </div>
</form>
<script>
  const $ = (i) => document.getElementById(i);
  $('next').onclick = () => {  // like a real wizard: refuses while a required field is empty
    if (!$('first_name').value || !$('color').value) return;
    $('s1').remove(); $('s2').style.display = '';
  };
  $('go').onclick = () => { if ($('sponsor').value) location.href = '/apply/2/confirmation'; };
  setTimeout(() => {  // the fake Simplify panel (consent already given), fills what it knows on the step shown
    const host = document.createElement('div'); host.className = 'simplify-jobs-shadow-root';
    document.body.appendChild(host);
    const root = host.attachShadow({mode: 'open'});
    root.innerHTML = '<button id="fill">Autofill This Page</button>';
    root.getElementById('fill').onclick = () => { if ($('first_name')) $('first_name').value = 'Sam'; };
  }, 300);
</script></body></html>"""


def test_simplify_auto_goes_through_a_multi_step_form(env, tmp_path):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url="https://jobs.example.com/apply/2"))

    def route(ctx):
        ctx.route("**/apply/2", lambda r: r.fulfill(content_type="text/html", body=STEPS))
        ctx.route("**/apply/2/confirmation", lambda r: r.fulfill(content_type="text/html", body="<h1>Thank you for applying</h1>"))
    me = load_me(me_home(tmp_path / "home"))
    reg = registry(opener=lambda u: None, simplify_context=chrome, simplify_route=route)
    a = Applications(db, SIMPLIFY, reg, ttl=TTL, ctx=ApplyContext(me=me, matcher=lambda *a: {}, browser_profiles=tmp_path))
    assert a.run() == {"needs_input": 1}
    [app] = a.rows()
    i = app["application_id"]
    assert "step 1 of a multi-step form" in a.get(i)["note"] and [q.id for q in a.questions(i) if q.open] == ["color"]
    assert "sponsor" not in {q.id for q in a.questions(i)}  # Next sends the step to the site: not in a dry run
    a.answer(i, "color", "Blue")
    assert a.get(i)["status"] == "ready"
    assert a.submit(i) == "submitted" and db.job(jobs["ml"])["state"] == "applied"  # both steps; step 2 from answers.yaml


def test_a_new_question_on_a_later_step_comes_back_to_you(env, tmp_path):  # noqa: F811
    db, _, jobs, _ = env
    db.mark_job(jobs["ml"], "want_to_apply")
    with db.engine.begin() as conn:
        conn.execute(T.jobs.update().where(T.jobs.c.job_id == jobs["ml"]).values(canonical_url="https://jobs.example.com/apply/3"))
    page = STEPS.replace('<button type="button" id="go">Submit</button>',
                         '<label for="mascot">Your favorite mascot?*</label><input id="mascot" required>'
                         '<button type="button" id="go">Submit</button>')

    def route(ctx):
        ctx.route("**/apply/3", lambda r: r.fulfill(content_type="text/html", body=page))
    me = load_me(me_home(tmp_path / "home"))
    reg = registry(opener=lambda u: None, simplify_context=chrome, simplify_route=route)
    a = Applications(db, SIMPLIFY, reg, ttl=TTL, ctx=ApplyContext(me=me, matcher=lambda *a: {}, browser_profiles=tmp_path))
    a.run()
    [app] = a.rows()
    i = app["application_id"]
    a.answer(i, "color", "Blue")
    assert a.submit(i) == "needs_input"  # step 2 asked something new: back to you, not to the next applier
    assert [q.id for q in a.questions(i) if q.open] == ["mascot"] and "step 2 asks something new" in a.get(i)["note"]
    assert {q.id: q for q in a.questions(i)}["color"].answer == "Blue"  # your answers stay
