"""The browser filler on a fake Greenhouse-style form (served through Playwright routes, offline): text, file,
a <select>, a custom combobox, submit → confirmation; a missing field → unsupported; a CAPTCHA → action needed."""

from auto_app.apply.fill import fill_form
from auto_app.apply.questions import Question

FORM = """<!doctype html><html><body>
<form id="application" onsubmit="return false">
  <label for="first_name">First Name*</label><input id="first_name" required>
  <label for="email">Email*</label><input id="email" required>
  <label for="resume">Attach</label><input id="resume" type="file">
  <label for="country">Country*</label><select id="country"><option></option><option>Canada</option><option>United States</option></select>
  <label for="question_1">Sponsorship?</label>
  <div class="select__control"><input id="question_1" role="combobox"><div id="menu"></div></div>
  <label for="question_2">Why us?</label><textarea id="question_2"></textarea>
  <label for="question_4[]">Clearances</label><select id="question_4[]" multiple><option>Secret</option><option>None</option></select>
  <label for="candidate-location">Location (City)</label><input id="candidate-location" role="combobox"><div id="cities"></div>
  <button type="button" id="go">Submit application</button>
</form>
<script>
  // like React Select: ArrowDown opens a menu of role=option entries; clicking one chooses it
  const combo = document.getElementById('question_1'); combo.dataset.chosen = '';
  const menu = document.getElementById('menu');
  combo.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown') return;
    menu.innerHTML = '';
    for (const o of ['Yes', 'No']) {
      const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = o;
      d.onclick = () => { combo.dataset.chosen = o; menu.innerHTML = ''; }; menu.appendChild(d);
    }
  });
  // a city autocomplete (like Greenhouse's candidate-location): options appear once you type
  const city = document.getElementById('candidate-location'); city.dataset.chosen = '';
  city.addEventListener('input', () => {
    const box = document.getElementById('cities'); box.innerHTML = '';
    if (city.value.length < 3) return;
    const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = 'Boston, Massachusetts, United States';
    d.onclick = () => { city.dataset.chosen = d.textContent; box.innerHTML = ''; }; box.appendChild(d);
  });
  document.getElementById('go').onclick = () => {
    const ok = document.getElementById('first_name').value && document.getElementById('email').value
      && document.getElementById('country').value === 'United States' && combo.dataset.chosen === 'Yes'
      && document.getElementById('resume').files.length === 1 && document.getElementById('question_2').value
      && city.dataset.chosen.startsWith('Boston') && document.getElementById('question_4[]').value === 'None';
    if (CAPTCHA) { const f = document.createElement('iframe'); f.src = 'https://www.google.com/recaptcha/api2/bframe';
      f.title = 'recaptcha challenge expires in two minutes'; document.body.appendChild(f); return; }
    if (ok) location.href = '/acme/jobs/1/confirmation'; else document.getElementById('first_name').setAttribute('aria-invalid', 'true');
  };
</script></body></html>"""


def routes(captcha=False, form=FORM):
    def route(ctx):
        ctx.route("**/acme/jobs/1", lambda r: r.fulfill(content_type="text/html",
                                                      body=form.replace("CAPTCHA", "true" if captcha else "false")))
        ctx.route("**/acme/jobs/1/confirmation", lambda r: r.fulfill(
            content_type="text/html", body="<html><body><h1>Thank you for applying.</h1></body></html>"))
        ctx.route("**/recaptcha/**", lambda r: r.fulfill(content_type="text/html", body="<html></html>"))
    return route


def answers(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    return cv, [Question("first_name", "First Name", "text", True, answer="Alex", source="profile"),
                Question("email", "Email", "text", True, answer="alex@example.com", source="profile"),
                Question("resume", "Resume/CV", "file", True, answer=str(cv), source="profile"),
                Question("country", "Country", "select", True, answer="United States", source="profile"),
                Question("question_1", "Sponsorship?", "select", True, answer="Yes", source="bank:requires_sponsorship"),
                Question("question_2", "Why us?", "textarea", True, answer="Because.", source="you"),
                Question("question_3", "Optional", "text", False, source="skipped"),
                Question("question_4[]", "Clearances", "multiselect", True, answer=["None"], source="bank:security_clearance"),
                Question("location", "Location (City)", "text", True, answer="Boston, Massachusetts", source="profile")]


URL = "https://job-boards.greenhouse.io/acme/jobs/1"


def test_fills_and_submits(tmp_path):
    cv, qs = answers(tmp_path)
    out = fill_form(URL, qs, cv, submit=True, headless=True, route=routes())
    assert (out.kind, out.reason) == ("submitted", "confirmation shown")


def test_a_wrong_answer_is_not_a_confirmation(tmp_path):
    cv, qs = answers(tmp_path)
    qs[4].answer = "No"
    out = fill_form(URL, qs, cv, submit=True, headless=True, route=routes(), confirm_wait_s=3)
    assert out.kind == "failed" and "1 field(s) flagged" in out.reason


def test_a_missing_field_hands_it_to_the_next_applier(tmp_path):
    cv, qs = answers(tmp_path)
    out = fill_form(URL, qs, cv, submit=True, headless=True,
                    route=routes(form=FORM.replace('<select id="country">', '<select id="nation">')))
    assert out.kind == "unsupported" and "Country" in out.reason and "couldn't fill" in out.reason


def test_a_captcha_headless_needs_you(tmp_path):
    cv, qs = answers(tmp_path)
    out = fill_form(URL, qs, cv, submit=True, headless=True, route=routes(captcha=True))
    assert out.kind == "action_needed" and "CAPTCHA" in out.reason
