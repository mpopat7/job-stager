// Runs inside a posting's frame. `scan()` describes every control for the server; filling
// is the other half of this file. Injected once per frame and kept on globalThis, so a
// second injection reuses it instead of redefining everything.
//
// The label rules are ported from core/adapters/base.py and the lessons behind them are
// in its comments: only ever read a label *element*, never a container's innerText or a
// neighbour's, because both put answers into the wrong field without any error.

(() => {
  if (globalThis.__jobstager) return;

  const REF = 'data-jobstager-ref';
  const OPTION = 'data-jobstager-option';
  const MAX_PAGE_TEXT = 8000;
  const SKIP_BUTTON = /upload|attach|submit|apply|dropbox|google drive|manually|remove|delete/i;

  const text = (el) => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();

  // A <label> that wraps its control reads the control too: a wrapped <select> adds every
  // option to the question ("Gender Select ... Male Female"). Read it with controls removed.
  function ownText(label) {
    if (!label) return '';
    if (!label.querySelector('input, select, textarea')) return text(label);
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input, select, textarea, option').forEach((c) => c.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  }

  // An option's label wraps exactly one radio or checkbox. Lever wraps a whole question,
  // options and all, in one outer <label>, so "inside a label" alone is not the test.
  function isOptionLabel(label) {
    const inputs = label ? label.querySelectorAll('input') : [];
    return inputs.length === 1 && ['radio', 'checkbox'].includes(inputs[0].type);
  }

  // A question element names a group only if it is not itself an option's label.
  function questionIn(container) {
    const candidates = container.querySelectorAll('legend, label, [class*="label" i], h3, h4');
    for (const q of candidates) {
      // Text inside a link or button is an upload widget's own chrome ("ATTACH RESUME").
      if (q.querySelector('input, select, textarea') || isOptionLabel(q.closest('label'))
          || q.closest('a, button') || !rendered(q)) continue;
      if (text(q)) return text(q);
    }
    return '';
  }

  function rendered(el) {
    if (!el || !el.isConnected) return false;
    if (el.closest('[aria-hidden="true"], [hidden]')) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    return el.getClientRects().length > 0;
  }

  // A styled radio or checkbox often hides the real input and shows its label instead,
  // so either one being on screen counts.
  function choiceRendered(el) {
    return rendered(el) || rendered(el.closest('label')) || rendered(labelFor(el));
  }

  function labelFor(el) {
    return el.id ? el.ownerDocument.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  }

  function labelOf(el) {
    const forLabel = labelFor(el);
    if (forLabel && text(forLabel)) return text(forLabel);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const joined = labelledBy.split(/\s+/)
        .map((id) => text(el.ownerDocument.getElementById(id))).filter(Boolean).join(' ');
      if (joined) return joined;
    }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    const own = el.closest('label');
    if (own && ownText(own)) return ownText(own);
    // Climb to the nearest wrapper that holds this control alone; a wrapper around
    // several fields cannot name any one of them. Lever names fields with a div that
    // carries a label-ish class rather than a <label>.
    let n = el.parentElement;
    for (let i = 0; i < 6 && n; i++) {
      const others = [...n.querySelectorAll('input:not([type=hidden]), textarea, select')]
        .filter((c) => c !== el && c.type !== 'radio' && c.type !== 'checkbox');
      if (others.length) break;
      const l = n.querySelector('label, legend, .application-label, .card-field-title, [class*="label" i]');
      if (l && text(l)) return text(l);
      n = n.parentElement;
    }
    return el.placeholder || '';
  }

  function groupQuestion(el) {
    // The question a choice belongs to lives on the wrapper around every option, not on
    // the option's own label. Lever's `.application-field` wraps only the options, with
    // the question in a sibling, so a boundary with no question keeps the climb going.
    let n = el.parentElement;
    for (let i = 0; i < 8 && n; i++) {
      const cls = String(n.className || '');
      if (n.tagName === 'FIELDSET' || n.getAttribute('role') === 'radiogroup'
          || n.getAttribute('role') === 'group' || /fieldentry|question|application-field/i.test(cls)) {
        const q = questionIn(n);
        if (q) return q;
      }
      n = n.parentElement;
    }
    const container = el.closest('fieldset, [class*="question" i], [class*="field" i], .application-question');
    return container ? questionIn(container) : '';
  }

  function optionLabel(el) {
    // Lever follows each option's name with a description inside the same label; when the
    // value is how the label begins, the value is the option's name on its own.
    const value = (el.value || '').trim();
    const own = el.closest('label');
    if (value.length > 1 && value !== 'on' && own && ownText(own).startsWith(value)) return value;
    const forLabel = labelFor(el);
    if (forLabel && text(forLabel)) return text(forLabel);
    if (own && ownText(own)) return ownText(own);
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    const parent = text(el.parentElement);
    return parent.length <= 120 ? parent : (el.value || '');
  }

  function isRequired(el, question) {
    return el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(question);
  }

  function isCombobox(el) {
    return el.getAttribute('role') === 'combobox'
      || !!el.closest('.select__control, [class*="select__control"]')
      || el.getAttribute('aria-autocomplete') === 'list';
  }

  // Choice button groups: Ashby's Yes/No pills, segmented options. Reverse document
  // order visits a descendant before its ancestor, so the tightest wrapper wins and a
  // page-level container cannot lump every question's buttons into one group.
  function buttonGroups(doc, claimed) {
    const groups = [];
    const containers = [...doc.querySelectorAll(
      'fieldset, [class*="question" i], [class*="field" i], [role="group"], [role="radiogroup"], .application-question'
    )].reverse();
    for (const container of containers) {
      const qEl = container.querySelector('legend, label, [class*="label" i], h3, h4, [class*="title" i]');
      const question = text(qEl);
      if (!question) continue;
      const buttons = [...container.querySelectorAll('button, [role="button"], [role="radio"]')].filter((b) => {
        const t = text(b);
        if (!t || t.length > 60 || SKIP_BUTTON.test(t)) return false;
        // A <button> in a form defaults to submit; clicking it sends the application.
        if (b.tagName === 'BUTTON' && b.type === 'submit' && b.form) return false;
        if (b.tagName === 'INPUT') return false;
        return rendered(b);
      });
      if (buttons.length < 2 || buttons.every((b) => claimed.has(b))) continue;
      buttons.forEach((b) => claimed.add(b));
      groups.push({ question, buttons });
    }
    return groups;
  }

  function scan() {
    const doc = document;
    doc.querySelectorAll(`[${REF}]`).forEach((e) => { e.removeAttribute(REF); e.removeAttribute(OPTION); });
    const fields = [];
    const files = [];
    let n = 0;
    const next = () => String(n++);
    const claimed = new Set();

    for (const group of buttonGroups(doc, claimed)) {
      const ref = next();
      group.buttons.forEach((b, i) => { b.setAttribute(REF, ref); b.setAttribute(OPTION, String(i)); });
      fields.push({
        ref, kind: 'button', question: group.question,
        offered: group.buttons.map(text), required: /\*\s*$/.test(group.question), max_length: null,
      });
    }

    const seenGroups = new Set();
    const controls = doc.querySelectorAll('input, textarea, select');
    for (const el of controls) {
      if (el.hasAttribute(REF)) continue;
      const type = (el.type || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search'].includes(type)) continue;
      if (el.disabled || el.readOnly && type !== 'file') continue;

      if (type === 'file') {
        const ref = next();
        el.setAttribute(REF, ref);
        // An upload's own label is usually its button ("Attach"); the question is the group's.
        files.push({ ref, question: groupQuestion(el) || labelOf(el), accept: el.accept || '' });
        continue;
      }

      if (type === 'radio' || type === 'checkbox') {
        if (!choiceRendered(el)) continue;
        const members = el.name
          ? [...doc.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)].filter(choiceRendered)
          : [el];
        const key = `${type}:${el.name}`;
        if (el.name && seenGroups.has(key)) continue;
        if (el.name) seenGroups.add(key);
        const ref = next();
        if (type === 'checkbox' && members.length === 1) {
          // A lone checkbox: its own label is the question, and ticking it is the answer.
          el.setAttribute(REF, ref);
          const question = optionLabel(el) || groupQuestion(el);
          fields.push({ ref, kind: 'checkbox', question, offered: [], required: isRequired(el, question), max_length: null });
          continue;
        }
        members.forEach((m, i) => { m.setAttribute(REF, ref); m.setAttribute(OPTION, String(i)); });
        const question = groupQuestion(el) || labelOf(el);
        fields.push({
          ref, kind: type, question, offered: members.map(optionLabel),
          required: members.some((m) => isRequired(m, question)), max_length: null,
        });
        continue;
      }

      if (!rendered(el)) continue;
      const ref = next();
      el.setAttribute(REF, ref);
      const question = labelOf(el);

      if (el.tagName === 'SELECT') {
        const offered = [...el.options]
          .filter((o) => o.value !== '' && !/^\s*(select|choose|please select|--)/i.test(o.text))
          .map((o) => o.text.trim());
        fields.push({ ref, kind: 'select', question, offered, required: isRequired(el, question), max_length: null });
        continue;
      }

      fields.push({
        ref,
        kind: isCombobox(el) ? 'combobox' : 'text',
        question,
        offered: [],
        required: isRequired(el, question),
        max_length: el.maxLength > 0 ? el.maxLength : null,
      });
    }

    return {
      url: location.href,
      pageText: text(doc.body).slice(0, MAX_PAGE_TEXT),
      fields,
      files,
    };
  }

  globalThis.__jobstager = { scan };
})();
