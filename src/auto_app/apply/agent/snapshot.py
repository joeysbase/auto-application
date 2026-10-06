"""The agent's view of a page (docs/step3-agent.md §3): every field, button, link and message, in reading order, each
interactive element with a ref — in every frame (an embedded form included). Generic: nothing in it knows a site.

`SNAPSHOT_JS(prefix)` runs in one frame: it tags each visible interactive element with `data-aa-ref="<prefix>e<n>"`
and returns one item per element — role, accessible name, required, invalid, value, options, checked — plus
headings, legends, alerts and step markers for context. Password values are never returned.
"""

SNAPSHOT_JS = r"""(prefix) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    const r = el.getBoundingClientRect();
    // a checkbox / radio / file input is often hidden behind a styled label: its parent's box counts
    if (r.width > 0 || r.height > 0) return true;
    const p = el.parentElement; return !!p && p.getClientRects().length > 0 && /^(checkbox|radio|file)$/.test(el.type || '');
  };
  const controls = (n) => [...n.querySelectorAll('input, textarea, select')].filter(c => c.type !== 'hidden').length;
  const firstLine = (n) => (n.innerText || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  const nearby = (el) => {  // the question text in the field's own container (forms without <label>)
    let a = el.parentElement, own = '';
    for (let i = 0; i < 6 && a && a !== document.body; i++, a = a.parentElement) {
      if (controls(a) > 1) return own;
      const t = firstLine(a); if (t && t.length <= 300) own = t;
    }
    return own;
  };
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' ');
              if (clean(t)) return clean(t); }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && clean(l.innerText)) return clean(l.innerText); }
    const l = el.closest('label'); if (l && clean(l.innerText)) return clean(l.innerText);
    const tag = el.tagName;
    if (tag === 'BUTTON' || tag === 'A' || /^(button|link|tab|option|menuitem)$/.test(el.getAttribute('role') || ''))
      return clean(el.innerText || el.value || el.title);
    if (tag === 'INPUT' && /^(submit|button)$/.test(el.type)) return clean(el.value);
    const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
    const near = nearby(el); if (near) return near;
    if (lg && clean(lg.innerText)) return clean(lg.innerText);
    return clean(el.getAttribute('placeholder') || el.title || el.name || '');
  };
  const roleOf = (el) => {
    const r = el.getAttribute('role'); if (r) return r;
    const tag = el.tagName, type = (el.type || '').toLowerCase();
    if (tag === 'SELECT') return el.multiple ? 'listbox' : 'select';
    if (tag === 'TEXTAREA') return 'textarea';
    if (tag === 'BUTTON') return 'button';
    if (tag === 'A') return 'link';
    if (tag === 'INPUT') {
      if (/^(submit|button|reset|image)$/.test(type)) return 'button';
      if (type === 'checkbox' || type === 'radio' || type === 'file') return type;
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return tag.toLowerCase();
  };
  const groupOf = (el) => {  // a radio's / grouped checkbox's question: the group's legend or container text
    const g = el.closest('[role=radiogroup], [role=group], fieldset');
    if (g) {
      const by = g.getAttribute('aria-labelledby');
      if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' ');
                if (clean(t)) return clean(t); }
      if (g.getAttribute('aria-label')) return clean(g.getAttribute('aria-label'));
      const lg = g.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText);
    }
    const mine = el.name ? [...document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)] : [el];
    const opts = mine.map(nameOf);
    let a = el.parentElement;
    for (let i = 0; i < 10 && a && a !== document.body; i++, a = a.parentElement) {
      if (controls(a) > mine.length) break;
      const line = clean(firstLine(a));
      if (line && !opts.some(o => o && line.startsWith(o))) return line;
    }
    return '';
  };
  const INTERACTIVE = 'input:not([type=hidden]), textarea, select, button, a[href], [role=button], [role=link], ' +
    '[role=checkbox], [role=radio], [role=combobox], [role=listbox], [role=option], [role=tab], [role=switch], ' +
    '[role=menuitem], [role=textbox], [contenteditable=true]';
  const CONTEXT = 'h1, h2, h3, h4, legend, [role=alert], [role=heading], [aria-current=step], [aria-live=assertive], ' +
    '[class*=error]:not(input):not(select):not(textarea), [class*=step]:not(:has(input, select, textarea, button))';
  // refs from an earlier snapshot must not linger (a stale e4 would point at an old element)
  for (const old of document.querySelectorAll('[data-aa-ref]')) old.removeAttribute('data-aa-ref');
  const out = []; let n = 0; const seen = new Set();
  for (const el of document.querySelectorAll(INTERACTIVE + ', ' + CONTEXT)) {
    if (!visible(el)) continue;
    if (el.closest('[aria-hidden=true]') && !el.matches('input, select, textarea')) continue;
    if (el.matches(INTERACTIVE)) {
      if (el.closest('a[href], button') && el.closest('a[href], button') !== el) continue;  // the link, not its parts
      const ref = prefix + 'e' + (++n);
      el.setAttribute('data-aa-ref', ref);
      const role = roleOf(el), type = (el.type || '').toLowerCase();
      const item = {ref, role, name: nameOf(el).slice(0, 300)};
      if (el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(item.name)) item.required = true;
      if (el.getAttribute('aria-invalid') === 'true') item.invalid = true;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') item.disabled = true;
      if (type === 'password') item.value = el.value ? '••••' : '';
      else if (role === 'checkbox' || role === 'radio' || role === 'switch')
        item.checked = el.checked ?? (el.getAttribute('aria-checked') === 'true');
      else if (el.tagName === 'SELECT') {
        item.options = [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 40);
        item.value = [...el.selectedOptions].filter(o => o.value !== '').map(o => clean(o.text)).join(', ');
      } else if (type === 'file') item.value = el.files && el.files.length ? el.files[0].name : '';
      else if (role === 'combobox') {
        const ctl = el.closest('[class*=control]') || el.parentElement;
        const shown = clean(ctl ? ctl.innerText : '') || clean(el.value);
        item.value = /^select\b/i.test(shown) ? '' : shown.slice(0, 120);
        item.expanded = el.getAttribute('aria-expanded') === 'true';
      } else if ('value' in el && el.tagName !== 'BUTTON' && role !== 'option' && role !== 'link') item.value = clean(el.value).slice(0, 200);
      if (role === 'option') item.selected = el.getAttribute('aria-selected') === 'true';
      if (role === 'radio' || (role === 'checkbox' && el.name && document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1)) {
        const g = groupOf(el); if (g) item.group = g.slice(0, 300);
        if (!item.required && /\*\s*$/.test(item.group || '')) item.required = true;
      }
      out.push(item);
    } else {
      const text = clean(el.innerText).slice(0, 200);
      if (text && !seen.has(text)) { seen.add(text); out.push({role: 'text', name: text, alert: el.matches('[role=alert], [class*=error]')}); }
    }
  }
  return {title: document.title, items: out};
}"""


def render(frames: list[dict]) -> str:
    """The snapshot as text for the model: one line per element, frame by frame."""
    lines = []
    for f in frames:
        lines.append(f"── frame {f['index']}: {f['url'][:150]}" + (f"  “{f['title'][:80]}”" if f.get("title") else ""))
        for it in f["items"]:
            if it["role"] == "text":
                lines.append(("  ! " if it.get("alert") else "  # ") + it["name"])
                continue
            bits = [f"[{it['ref']}] {it['role']} \"{it['name']}\""]
            if it.get("group"):
                bits.append(f"of “{it['group']}”")
            for flag in ("required", "invalid", "disabled", "expanded"):
                if it.get(flag):
                    bits.append(flag)
            if "checked" in it:
                bits.append("checked" if it["checked"] else "unchecked")
            if it.get("selected"):
                bits.append("selected")
            if "value" in it:
                bits.append(f"value=\"{it['value']}\"")
            if it.get("options"):
                bits.append("options: " + " | ".join(it["options"]))
            lines.append("  " + " ".join(bits))
    return "\n".join(lines)
