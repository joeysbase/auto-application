"""In-page JavaScript used by all adapters. Each snippet only reads the DOM."""

# Find job cards in the results list. Two strategies, merged and kept in DOM order:
#   1. elements carrying a job-id attribute (e.g. LinkedIn's data-occludable-job-id), or an attribute
#      whose value contains the id ("attr~regex", e.g. componentkey="job-card-component-ref-<id>")
#   2. links whose href matches a job-URL regex (first capture group = job id)
READ_CARDS = r"""
({listSelectors, containerSelectors, idAttributes, linkPatterns, promotedTexts, maxHtml}) => {
  let root = document;
  for (const s of listSelectors) { const el = document.querySelector(s); if (el) { root = el; break; } }
  const regs = linkPatterns.map(p => new RegExp(p));
  const found = new Map();

  const cardFor = (el) => {
    for (const s of containerSelectors) { const c = el.closest(s); if (c) return c; }
    return el.parentElement || el;
  };
  const add = (id, el, link) => {
    if (!id || found.has(id)) return;
    const text = (el.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const lines = text.filter((l, i) => l !== text[i - 1]);
    const linkText = link ? (link.innerText || '').trim().split('\n')[0].trim() : '';
    const attrs = {};
    for (const a of el.attributes) if (a.name.startsWith('data-')) attrs[a.name] = a.value.slice(0, 500);
    const lower = lines.map(l => l.toLowerCase());
    const promoted = promotedTexts.some(t => lower.some(l => l.includes(t.toLowerCase())));
    found.set(id, {el, id, href: link ? link.href : null, title: linkText || lines[0] || null,
                   lines: lines.slice(0, 40), attrs, promoted, html: el.outerHTML.slice(0, maxHtml)});
  };

  for (const spec of idAttributes) {
    // "attr" (the value is the id) or "attr~regex" (the first capture group is the id)
    const cut = spec.indexOf('~');
    const attr = cut < 0 ? spec : spec.slice(0, cut);
    const re = cut < 0 ? null : new RegExp(spec.slice(cut + 1));
    for (const el of root.querySelectorAll(`[${attr}]`)) {
      const value = el.getAttribute(attr);
      const id = re ? ((value.match(re) || [])[1] || null) : value;
      add(id, el, el.querySelector('a[href]'));
    }
  }
  for (const a of root.querySelectorAll('a[href]')) {
    for (const r of regs) {
      const m = a.href.match(r);
      if (m) { add(m[1], cardFor(a), a); break; }
    }
  }
  const items = [...found.values()];
  items.sort((x, y) => (x.el.compareDocumentPosition(y.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
  return items.map(({el, ...rest}) => rest);
}
"""

# Scroll the results list (or the window) by one step. Returns whether it moved.
SCROLL_STEP = r"""
({listSelectors, fraction}) => {
  const scrollable = (el) => {
    if (!el) return false;
    const st = getComputedStyle(el);
    return el.scrollHeight > el.clientHeight + 40 && /(auto|scroll)/.test(st.overflowY);
  };
  let target = null;
  for (const s of listSelectors) {
    const el = document.querySelector(s);
    if (!el) continue;
    if (scrollable(el)) { target = el; break; }
    const inner = [...el.querySelectorAll('*')].slice(0, 300).find(scrollable);
    if (inner) { target = inner; break; }
    let p = el.parentElement;
    while (p && !scrollable(p)) p = p.parentElement;
    if (p) { target = p; break; }
  }
  target = target || document.scrollingElement;
  const before = target.scrollTop;
  target.scrollBy(0, Math.max(200, target.clientHeight * fraction));
  return target.scrollTop !== before;
}
"""

# Structured data embedded in the page, returned as raw strings.
EMBEDDED = r"""
() => {
  const out = {jsonld: [], next_data: null, json_scripts: [], code_blocks: []};
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) out.jsonld.push(s.textContent);
  const nd = document.querySelector('script#__NEXT_DATA__');
  if (nd) out.next_data = nd.textContent;
  for (const s of document.querySelectorAll('script[type="application/json"]')) {
    if (s.id !== '__NEXT_DATA__') out.json_scripts.push({id: s.id || null, text: s.textContent.slice(0, 3000000)});
  }
  for (const c of document.querySelectorAll('code[id^="bpr-guid"], code[id^="datalet"]')) {
    out.code_blocks.push({id: c.id, text: c.textContent.slice(0, 3000000)});
  }
  return out;
}
"""

# Locate the apply control without clicking it.
FIND_APPLY = r"""
({selectors}) => {
  const cands = [];
  for (const s of selectors) for (const el of document.querySelectorAll(s)) cands.push(el);
  if (!cands.length) {
    for (const el of document.querySelectorAll('a, button')) {
      const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
      if (t.length < 60 && /^(easy apply|apply\b)|apply (now|on|externally)/i.test(t)) cands.push(el);
    }
  }
  const el = cands.find(e => e.offsetParent !== null) || cands[0];
  if (!el) return null;
  const a = el.tagName === 'A' ? el : el.closest('a[href]');
  return {text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 200),
          href: a ? a.href : null};
}
"""

DETECT_CAPTCHA = r"""
() => {
  const iframe = [...document.querySelectorAll('iframe')].some(f =>
    /captcha|recaptcha|hcaptcha|challenges\.cloudflare|arkoselabs|funcaptcha/i.test(f.src || ''));
  if (iframe) return 'captcha iframe';
  if (document.querySelector('#captcha-internal, #px-captcha, .g-recaptcha, .h-captcha, .cf-turnstile')) return 'captcha element';
  const script = [...document.querySelectorAll('script[src], link[href]')].some(e =>
    /challenges\.cloudflare\.com|\/cdn-cgi\/challenge-platform/i.test(e.src || e.href || ''));
  if (script) return 'cloudflare challenge';
  const t = (document.body ? document.body.innerText : '').slice(0, 3000).toLowerCase();
  for (const p of ['verify you are human', 'are you a robot', 'security check', 'checking your browser',
                   'unusual activity from your']) {
    if (t.includes(p)) return 'challenge text: ' + p;
  }
  return null;
}
"""

ANY_SELECTOR_PRESENT = r"""
(selectors) => selectors.some(s => document.querySelector(s) !== null)
"""

VISIBLE_TEXT_PRESENT = r"""
(texts) => {
  const els = [...document.querySelectorAll('a, button')].filter(e => e.offsetParent !== null);
  const labels = els.map(e => (e.innerText || '').trim().toLowerCase());
  return texts.some(t => labels.includes(t.toLowerCase()));
}
"""
