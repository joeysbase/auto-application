"""Reading an application form back from the page (docs/step3-apply.md §5 — starting a job is a dry run).

`scan_form(page)` lists every field the form actually has — its id, label, type, whether it's required, what's in it
now, and its options — whatever the API said and whoever filled it (us, Simplify). `reconcile` merges that with the
questions collected so far: a required field that's still empty becomes an open question (needs input), with the
form's own label and options; an answer that didn't take on the form is reopened; a field Simplify filled is recorded
(source `simplify`) so you can review it.
"""

from auto_app.apply.questions import Question, classify

SCAN_JS = r"""() => {
  const root = document.querySelector('form') ? [...document.querySelectorAll('form')] : [document.body];
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' ');
              if (t.trim()) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const l = el.closest('label'); if (l) return l.innerText;
    const fs = el.closest('fieldset'); if (fs && fs.querySelector('legend')) return fs.querySelector('legend').innerText;
    const near = nearbyText(el); if (near) return near;
    const ph = el.getAttribute('placeholder');
    return (ph && !/^(search|select)\b/i.test(ph)) ? ph : (el.name || el.id || '');
  };
  // No label element (e.g. Rippling): the question text in the field's own container — the largest ancestor that
  // holds no other field. A field that shares its container (a phone number's country-code picker) is named after
  // the container's first line.
  const controls = (n) => [...n.querySelectorAll('input, textarea, select')].filter(c => c.type !== 'hidden').length;
  const firstLine = (n) => (n.innerText || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  const nearbyText = (el) => {
    let a = el.parentElement, own = '';
    for (let i = 0; i < 6 && a && a !== document.body && a.tagName !== 'FORM'; i++, a = a.parentElement) {
      const n = controls(a);
      if (n > 1) {
        if (own || n > 3) return own;  // a section of the form, not one question
        const line = firstLine(a); if (!line) continue;  // shared, but no text yet: keep looking up
        if (el.getAttribute('role') === 'combobox' && /phone/i.test(line)) return line.replace(/\*\s*$/, '') + ' country code';
        const ph = el.getAttribute('placeholder') || 'part';
        return line.toLowerCase().includes(ph.toLowerCase()) ? line : `${line} (${ph})`;
      }
      const t = firstLine(a); if (t && t.length <= 300) own = t;
    }
    return own;
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const out = []; const seenRadio = new Set();
  for (const r of root) for (const el of r.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) continue;
    const id = el.id || el.name; if (!id) continue;  // aria-hidden helpers without an id/name: skip
    // in a hidden section (a later step of a multi-step form): not on the form yet. The field itself may be hidden
    // (a file input behind a styled button), so its parent is what counts
    if (!el.parentElement || el.parentElement.getClientRects().length === 0) continue;
    if (el.getAttribute('role') === 'combobox' && /^search\b/i.test(el.getAttribute('placeholder') || '')
        && /^(search\b.*)?$/i.test(clean([...(el.labels || [])].map(l => l.innerText).join(' ') + ' ' + (el.getAttribute('aria-label') || '')))
        && !nearbyText(el)) continue;  // a dropdown's own search box
    let label = clean(labelOf(el));
    let required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
    let kind = type, value = '', options = [];
    if (el.tagName === 'SELECT') {
      kind = el.multiple ? 'multiselect' : 'select';
      options = [...el.options].map(o => clean(o.text)).filter(Boolean);
      const chosen = [...el.selectedOptions].filter(o => o.value !== '' && clean(o.text));
      value = chosen.map(o => clean(o.text)).join(', ');
    } else if (el.getAttribute('role') === 'combobox') {
      kind = 'select';
      const ctl = el.closest('[class*="control"]') || el.parentElement;
      const shown = clean(ctl ? ctl.innerText : '');
      value = (!shown || /^select\b/i.test(shown)) ? '' : shown;
    } else if (type === 'file') {
      const box = el.closest('[class*="field"], [class*="upload"], [class*="file"]') || el.parentElement;
      value = (el.files && el.files.length) ? el.files[0].name
            : ((box && /\.(pdf|docx?|txt|rtf)\b/i.test(box.innerText)) ? 'attached' : '');
    } else if (type === 'checkbox') {
      value = el.checked ? 'checked' : '';
    } else if (type === 'radio') {
      if (seenRadio.has(el.name)) continue; seenRadio.add(el.name);
      const group = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)];
      options = group.map(g => clean(labelOf(g)));
      const on = group.find(g => g.checked); value = on ? clean(labelOf(on)) : '';
      kind = 'radio';
      if (options.includes(label) || !label) {  // named after its first option: the question is the group's container text
        let a = el.parentElement;
        for (let i = 0; i < 10 && a && a.tagName !== 'FORM'; i++, a = a.parentElement) {
          if (controls(a) > group.length) break;
          const line = clean(firstLine(a));
          if (line && !options.some(o => o && line.startsWith(o))) { label = line; break; }  // not the options' own text
        }
        const rg = el.closest('[role=radiogroup]');
        required = required || (rg && rg.getAttribute('aria-required') === 'true') || /\*\s*$/.test(label);
      }
    } else if (el.tagName === 'TEXTAREA') {
      kind = 'textarea'; value = el.value;
    } else {
      kind = 'text'; value = el.value;
    }
    out.push({id: el.tagName === 'INPUT' && type === 'radio' ? el.name : id, label, type: kind, required,
              value: clean(value), options});
  }
  return out;
}"""


def scan_form(page, read_options: bool = True) -> list[dict]:
    """Every field on the page's form(s). read_options: open each required, empty dropdown to read its options."""
    seen, fields = set(), []
    for f in page.evaluate(SCAN_JS):  # one entry per field id (a field can be reached twice, e.g. inside nested forms)
        if f["id"] not in seen:
            seen.add(f["id"])
            fields.append(f)
    if read_options:
        for f in fields:
            if f["type"] == "select" and not f["options"] and f["required"] and not f["value"]:
                f["options"] = combobox_options(page, f["id"])
    return fields


def combobox_options(page, field_id: str) -> list[str]:
    """A custom dropdown's options (React Select): open it with the keyboard, read the menu, close it."""
    el = page.locator(f'[id="{field_id}"]')
    if not el.count():
        return []
    try:
        el.first.focus()
        page.keyboard.press("ArrowDown")
        opts = page.locator('[role="option"]:visible')
        opts.first.wait_for(timeout=2500)
        texts = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 300))]
        page.keyboard.press("Escape")
        return [t for t in texts if t]
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return []


TYPES = {"text": "text", "textarea": "textarea", "file": "file", "select": "select", "multiselect": "multiselect",
         "checkbox": "checkbox", "radio": "radio", "email": "text", "tel": "text", "url": "text", "number": "text",
         "date": "text"}


def field_question(f: dict, source: str | None = None) -> Question:
    q = Question(id=f["id"], label=(f["label"] or f["id"]).rstrip("* ").strip(), type=TYPES.get(f["type"], "text"),
                 required=bool(f["required"]), options=[{"label": o} for o in f.get("options") or []],
                 answer=f["value"] or None, source=source if f["value"] else None)
    q.kind = classify(q)
    return q


def reconcile(questions: list[Question], fields: list[dict], aliases: dict[str, list[str]] | None = None,
              filled_by: str = "simplify") -> list[Question]:
    """Merge what the form shows with the questions collected so far (in place; returns the full list).

    - a required field that's empty on the form: its question is (re)opened — "didn't take on the form" when we had an
      answer for it, or a new question with the form's label and options;
    - a field that's filled on the form but unknown to us: recorded with source `filled_by` (e.g. Simplify);
    - options the form lists are added to questions that had none."""
    aliases = aliases or {}
    by_id = {q.id: q for q in questions}
    for alias_target, ids in aliases.items():  # e.g. API "location" ↔ form "candidate-location"
        for fid in ids:
            if alias_target in by_id:
                by_id.setdefault(fid, by_id[alias_target])
    for f in fields:
        q = by_id.get(f["id"])
        if q is None:
            q = field_question(f, filled_by)
            questions.append(q)
            by_id[f["id"]] = q
            if q.required and q.answer is None:
                q.note = "on the form, not answered yet"
            continue
        if f.get("options") and not q.options:
            q.options = [{"label": o} for o in f["options"]]
        if f["required"] and not f["value"] and q.type != "file" and q.source != "skipped":
            if q.answer not in (None, "", []):
                q.note = f"“{q.answer}” didn't take on the form"
            q.answer, q.source, q.required = None, None, True
    return questions
