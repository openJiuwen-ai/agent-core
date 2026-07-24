# coding: utf-8
"""Semantic helpers for dropdown and calendar browser widgets.

The helpers in this module intentionally execute inside Playwright page code
without relying on Node globals such as ``process``.  They return compact JSON
objects that are small enough for the browser worker to reason over.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


_DOM_HELPERS_JS = r"""
    const normalize = (value, limit = 220) => String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, limit);

    const attrEscape = (value) => String(value || '')
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"');

    const cssEscape = (value) => {
      const raw = String(value || '');
      if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(raw);
      return raw.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    };

    const visibleRect = (el) => {
      if (!el || !el.getBoundingClientRect) return null;
      const rect = el.getBoundingClientRect();
      if (!rect || rect.width < 2 || rect.height < 2) return null;
      const style = window.getComputedStyle(el);
      if (!style) return null;
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
        return null;
      }
      return rect;
    };

    const isVisible = (el, viewportOnly = true) => {
      const rect = visibleRect(el);
      if (!rect) return false;
      if (!viewportOnly) return true;
      return rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    };

    const bbox = (rect) => rect ? {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    } : null;

    const buildSelectorHint = (el) => {
      if (!el || !el.tagName) return '';
      const tag = el.tagName.toLowerCase();
      const testid = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy');
      if (testid) {
        if (el.getAttribute('data-testid')) return `[data-testid="${attrEscape(testid)}"]`;
        if (el.getAttribute('data-test')) return `[data-test="${attrEscape(testid)}"]`;
        return `[data-cy="${attrEscape(testid)}"]`;
      }
      const id = el.getAttribute('id');
      if (id) return `#${cssEscape(id)}`;
      const aria = el.getAttribute('aria-label');
      if (aria) return `${tag}[aria-label="${attrEscape(aria)}"]`;
      const name = el.getAttribute('name');
      if (name) return `${tag}[name="${attrEscape(name)}"]`;

      const path = [];
      let node = el;
      let depth = 0;
      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 7) {
        const nodeTag = node.tagName.toLowerCase();
        const nodeTestid = node.getAttribute('data-testid') ||
        node.getAttribute('data-test') ||
        node.getAttribute('data-cy');
        if (nodeTestid) {
          if (node.getAttribute('data-testid')) path.unshift(`[data-testid="${attrEscape(nodeTestid)}"]`);
          else if (node.getAttribute('data-test')) path.unshift(`[data-test="${attrEscape(nodeTestid)}"]`);
          else path.unshift(`[data-cy="${attrEscape(nodeTestid)}"]`);
          break;
        }
        const nodeId = node.getAttribute('id');
        if (nodeId) {
          path.unshift(`#${cssEscape(nodeId)}`);
          break;
        }
        let index = 1;
        let prev = node.previousElementSibling;
        while (prev) {
          if (prev.tagName.toLowerCase() === nodeTag) index += 1;
          prev = prev.previousElementSibling;
        }
        path.unshift(`${nodeTag}:nth-of-type(${index})`);
        node = node.parentElement;
        depth += 1;
      }
      return path.join(' > ');
    };

    const textOf = (el, limit = 220) => normalize(
      el.getAttribute('aria-label') ||
      el.getAttribute('title') ||
      el.getAttribute('data-value') ||
      el.getAttribute('value') ||
      el.innerText ||
      el.textContent ||
      '',
      limit
    );

    const clickLikeUser = (el) => {
      if (!el) return;
      const rect = visibleRect(el);
      const options = { bubbles: true, cancelable: true, view: window };
      if (rect) {
        options.clientX = rect.left + rect.width / 2;
        options.clientY = rect.top + rect.height / 2;
      }
      el.dispatchEvent(new MouseEvent('mouseover', options));
      el.dispatchEvent(new MouseEvent('mousedown', options));
      el.dispatchEvent(new MouseEvent('mouseup', options));
      el.click();
    };

    const disabledLike = (el) => {
      if (!el) return true;
      const aria = String(el.getAttribute('aria-disabled') || '').toLowerCase();
      const disabled = el.disabled || aria === 'true';
      const cls = String(el.getAttribute('class') || '').toLowerCase();
      return !!disabled || /\b(disabled|unavailable|inactive|is-disabled)\b/.test(cls);
    };
"""


_DROPDOWN_JS = r"""
    const optionSelectors = [
      '[role="option"]',
      '[role="menuitem"]',
      '[role="treeitem"]',
      '[role="listbox"] [role]',
      '.select2-results__option',
      '.select2-result-label',
      '.ui-autocomplete li a',
      '.dropdown-menu li a',
      '.multiselect-container li a',
      '[data-value]',
      '[data-testid*="option" i]',
      '[data-testid*="suggest" i]',
      '[class*="option" i]',
      '[class*="suggest" i]',
      '.select2-results li',
      '.ui-autocomplete li',
      '[class*="select2" i] li',
      '[class*="autocomplete" i] li',
      '[class*="dropdown" i] li',
      '[class*="popup" i] li',
      'option',
      'li'
    ];

    const collectDropdownOptions = (query, maxOptions, viewportOnly) => {
      const terms = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
      const seen = new Set();
      const options = [];
      const nodes = [];
      for (const selector of optionSelectors) {
        try {
          nodes.push(...Array.from(document.querySelectorAll(selector)));
        } catch (_) {}
      }

      for (const el of nodes) {
        if (!el || seen.has(el)) continue;
        seen.add(el);
        if (!isVisible(el, viewportOnly)) continue;
        const text = textOf(el, 260);
        if (!text) continue;
        const lower = text.toLowerCase();
        let score = 10;
        if (terms.length) {
          const matched = terms.filter((term) => lower.includes(term)).length;
          if (!matched) continue;
          score += matched * 25;
          if (lower === String(query || '').toLowerCase()) score += 40;
          if (lower.startsWith(String(query || '').toLowerCase())) score += 20;
        }
        const rect = visibleRect(el);
        options.push({
          text,
          role: el.getAttribute('role') || el.tagName.toLowerCase(),
          disabled: disabledLike(el),
          selected: String(el.getAttribute('aria-selected') || '').toLowerCase() === 'true' || !!el.selected,
          selector_hint: buildSelectorHint(el),
          bbox: bbox(rect),
          score
        });
      }

      options.sort((a, b) => b.score - a.score || a.text.length - b.text.length);
      return options.slice(0, maxOptions);
    };
"""


_CALENDAR_JS = r"""
    const monthNames = [
      ['january', 'jan'], ['february', 'feb'], ['march', 'mar'], ['april', 'apr'],
      ['may'], ['june', 'jun'], ['july', 'jul'], ['august', 'aug'],
      ['september', 'sep', 'sept'], ['october', 'oct'], ['november', 'nov'], ['december', 'dec']
    ];

    const monthIndexFromText = (text) => {
      const lower = String(text || '').toLowerCase();
      for (let i = 0; i < monthNames.length; i += 1) {
        if (monthNames[i].some((name) => new RegExp(`\\b${name}\\b`).test(lower))) return i;
      }
      return -1;
    };

    const yearFromText = (text) => {
      const match = String(text || '').match(/\b(20\d{2}|19\d{2})\b/);
      return match ? Number(match[1]) : null;
    };

    const parseDateish = (value) => {
      const text = String(value || '').trim();
      if (!text) return null;
      const iso = text.match(/\b(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b/);
      if (iso) {
        return { year: Number(iso[1]), month: Number(iso[2]) - 1, day: Number(iso[3]) };
      }
      const month = monthIndexFromText(text);
      const year = yearFromText(text);
      const dayMatch = text.match(/\b([0-3]?\d)(?:st|nd|rd|th)?\b/i);
      if (month >= 0 && year && dayMatch) {
        const day = Number(dayMatch[1]);
        if (day >= 1 && day <= 31) return { year, month, day };
      }
      return null;
    };

    const findCalendarRoots = (viewportOnly = true) => {
      const selectors = [
        '[role="dialog"]',
        '[role="grid"]',
        '[aria-modal="true"]',
        '[class*="calendar" i]',
        '[class*="datepicker" i]',
        '[class*="date-picker" i]',
        '[class*="daterange" i]',
        '[class*="picker" i]',
        '[class*="popup" i]'
      ];
      const roots = [];
      const seen = new Set();
      for (const selector of selectors) {
        try {
          for (const el of Array.from(document.querySelectorAll(selector))) {
            if (seen.has(el) || !isVisible(el, viewportOnly)) continue;
            const text = normalize(el.innerText || el.textContent || '', 2200);
            if (!text) continue;
            if (/\b(20\d{2}|19\d{2})\b/.test(text) || monthIndexFromText(text) >= 0) {
              seen.add(el);
              roots.push(el);
            }
          }
        } catch (_) {}
      }
      if (!roots.length && document.body) roots.push(document.body);
      return roots.slice(0, 8);
    };

    const inferRootMonth = (root) => {
      const candidates = [];
      const selector = 'h1,h2,h3,h4,[role="heading"],[class*="month" i],[class*="header" i]';
      try {
        for (const el of Array.from(root.querySelectorAll(selector))) {
          if (!isVisible(el, false)) continue;
          const text = textOf(el, 140);
          const month = monthIndexFromText(text);
          const year = yearFromText(text);
          if (month >= 0 || year) candidates.push({ text, month, year });
        }
      } catch (_) {}
      const rootText = normalize(root.innerText || root.textContent || '', 1200);
      const rootMonth = monthIndexFromText(rootText);
      const rootYear = yearFromText(rootText);
      if (rootMonth >= 0 || rootYear) candidates.push({ text: rootText, month: rootMonth, year: rootYear });
      const best = candidates.find((item) => item.month >= 0 && item.year) || candidates[0] || {};
      return { month: best.month ?? -1, year: best.year ?? null, label: best.text || '' };
    };

    const candidateDayNodes = (root) => {
      const selectors = [
        '[role="gridcell"]',
        'button',
        'td',
        'div',
        'span',
        '[aria-label]',
        '[data-date]',
        '[data-day]',
        '[datetime]'
      ];
      const nodes = [];
      const seen = new Set();
      for (const selector of selectors) {
        try {
          for (const el of Array.from(root.querySelectorAll(selector))) {
            if (seen.has(el)) continue;
            seen.add(el);
            nodes.push(el);
          }
        } catch (_) {}
      }
      return nodes;
    };

    const collectCalendarDays = (maxDays, viewportOnly) => {
      const days = [];
      const roots = findCalendarRoots(viewportOnly);
      for (const root of roots) {
        const rootMonth = inferRootMonth(root);
        for (const el of candidateDayNodes(root)) {
          if (!isVisible(el, viewportOnly)) continue;
          const text = textOf(el, 80);
          const directDate = parseDateish([
            el.getAttribute('aria-label'),
            el.getAttribute('title'),
            el.getAttribute('data-date'),
            el.getAttribute('data-day'),
            el.getAttribute('datetime'),
            el.getAttribute('data-value')
          ].filter(Boolean).join(' '));
          const bare = text.match(/^([0-3]?\d)$/);
          let date = directDate;
          if (!date && bare && rootMonth.month >= 0 && rootMonth.year) {
            date = { year: rootMonth.year, month: rootMonth.month, day: Number(bare[1]) };
          }
          if (!date || date.day < 1 || date.day > 31) continue;
          const rect = visibleRect(el);
          const iso = `${date.year}-${String(date.month + 1).padStart(2, '0')}-${String(date.day).padStart(2, '0')}`;
          const cls = String(el.getAttribute('class') || '').toLowerCase();
          const outside = /\b(outside|other-month|prev|next|muted|off)\b/.test(cls);
          days.push({
            date: iso,
            day: date.day,
            month: date.month + 1,
            year: date.year,
            text,
            disabled: disabledLike(el),
            outside_month: outside,
            selected: String(el.getAttribute('aria-selected') || '').toLowerCase() === 'true' ||
              /\b(selected|active|current)\b/.test(cls),
            selector_hint: buildSelectorHint(el),
            bbox: bbox(rect),
            month_label: rootMonth.label,
            _el: el
          });
        }
      }
      const unique = [];
      const seen = new Set();
      for (const day of days) {
        const key = `${day.date}|${day.selector_hint}`;
        if (seen.has(key)) continue;
        seen.add(key);
        unique.push(day);
      }
      return unique.slice(0, maxDays);
    };

    const findMonthNavButton = (direction, explicitSelector = '') => {
      if (explicitSelector) {
        try {
          const found = document.querySelector(explicitSelector);
          if (found && isVisible(found, true) && !disabledLike(found)) return found;
        } catch (_) {}
      }
      const words = direction === 'next'
        ? ['next', 'forward', 'following', '下', '后一', 'next month']
        : ['prev', 'previous', 'back', '上一', '前一', 'previous month'];
      const selectors = ['button', 'a', '[role="button"]', '[aria-label]', '[title]'];
      const candidates = [];
      for (const selector of selectors) {
        try { candidates.push(...Array.from(document.querySelectorAll(selector))); } catch (_) {}
      }
      for (const el of candidates) {
        if (!isVisible(el, true) || disabledLike(el)) continue;
        const text = normalize([
          el.getAttribute('aria-label'),
          el.getAttribute('title'),
          el.getAttribute('data-testid'),
          el.innerText,
          el.textContent
        ].filter(Boolean).join(' '), 180).toLowerCase();
        if (words.some((word) => text.includes(word))) return el;
      }
      return null;
    };
"""




def build_form_fields_probe_js(
    *,
    max_fields: int = 80,
    viewport_only: bool = True,
    query: str = "",
    include_options: bool = True,
) -> str:
    """Build code for probing visible form fields before batch filling."""

    params = {
        "max_fields": _clamp_int(max_fields, default=80, minimum=1, maximum=160),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip(),
        "include_options": bool(include_options),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  return await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
    const terms = String(params.query || '').toLowerCase().split(/\s+/).filter(Boolean);

    const labelFor = (el) => {{
      const id = el.getAttribute('id');
      const labels = [];
      if (id) {{
        try {{
          const direct = document.querySelector(`label[for="${{attrEscape(id)}}"]`);
          if (direct) labels.push(textOf(direct, 180));
        }} catch (_) {{}}
      }}
      if (el.labels) {{
        for (const label of Array.from(el.labels)) labels.push(textOf(label, 180));
      }}
      let parent = el.parentElement;
      for (let depth = 0; parent && depth < 4; depth += 1, parent = parent.parentElement) {{
        const candidate = parent.querySelector('label,[class*="label" i],[class*="title" i],[class*="name" i]');
        if (candidate) labels.push(textOf(candidate, 180));
      }}
      return normalize(labels.filter(Boolean).join(' | '), 260);
    }};

    const surroundingText = (el) => {{
      const parts = [];
      const parent = el.parentElement;
      if (parent) parts.push(normalize(parent.innerText || parent.textContent || '', 260));
      const previous = el.previousElementSibling;
      const next = el.nextElementSibling;
      if (previous) parts.push(textOf(previous, 160));
      if (next) parts.push(textOf(next, 160));
      return normalize(parts.filter(Boolean).join(' | '), 360);
    }};

    const fieldSelectors = [
      'input',
      'textarea',
      'select',
      '[contenteditable="true"]',
      '[role="textbox"]',
      '[role="combobox"]',
      '[role="spinbutton"]',
      '[aria-haspopup="listbox"]',
      '[aria-haspopup="menu"]'
    ];

    const nodes = [];
    const seen = new Set();
    for (const selector of fieldSelectors) {{
      try {{
        for (const el of Array.from(document.querySelectorAll(selector))) {{
          if (!seen.has(el)) {{
            seen.add(el);
            nodes.push(el);
          }}
        }}
      }} catch (_) {{}}
    }}

    const fields = [];
    for (const el of nodes) {{
      if (!isVisible(el, params.viewport_only !== false)) continue;
      const tag = el.tagName ? el.tagName.toLowerCase() : '';
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'input' && ['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
      const label = labelFor(el);
      const placeholder = normalize(el.getAttribute('placeholder') || '', 180);
      const aria = normalize(el.getAttribute('aria-label') || '', 180);
      const name = normalize(el.getAttribute('name') || '', 180);
      const id = normalize(el.getAttribute('id') || '', 180);
      const testid = normalize(
        el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
        180
      );
      const value = normalize(el.value || el.getAttribute('value') || '', 180);
      const role = normalize(el.getAttribute('role') || tag, 60);
      const describedBy = normalize(el.getAttribute('aria-describedby') || '', 120);
      const text = normalize(
        [label, placeholder, aria, name, id, testid, surroundingText(el), describedBy]
          .filter(Boolean)
          .join(' | '),
        520
      );
      const lower = text.toLowerCase();
      if (terms.length && !terms.some((term) => lower.includes(term))) continue;

      let options = [];
      if (params.include_options && tag === 'select') {{
        options = Array.from(el.options || []).slice(0, 80).map((option) => ({{
          text: normalize(option.text || option.label || option.value || '', 160),
          value: normalize(option.value || '', 160),
          selected: !!option.selected,
          disabled: !!option.disabled,
        }}));
      }}

      fields.push({{
        tag,
        type,
        role,
        label,
        placeholder,
        aria_label: aria,
        name,
        id,
        testid,
        value,
        disabled: disabledLike(el),
        readonly: !!el.readOnly || String(el.getAttribute('aria-readonly') || '').toLowerCase() === 'true',
        required: !!el.required || String(el.getAttribute('aria-required') || '').toLowerCase() === 'true',
        autocomplete: normalize(el.getAttribute('autocomplete') || '', 80),
        inputmode: normalize(el.getAttribute('inputmode') || '', 80),
        selector_hint: buildSelectorHint(el),
        bbox: bbox(visibleRect(el)),
        options,
        text_context: text,
      }});
    }}

    fields.sort((a, b) => (a.bbox?.y || 0) - (b.bbox?.y || 0) || (a.bbox?.x || 0) - (b.bbox?.x || 0));
    return {{
      ok: true,
      error: null,
      url: window.location.href,
      title: document.title,
      query: params.query || '',
      fields: fields.slice(0, params.max_fields || 80),
    }};
  }}, params);
}}
"""



def build_semantic_form_fill_js(
    *,
    fields: Dict[str, Any],
    max_fields: int = 120,
    viewport_only: bool = True,
    clear_existing: bool = True,
) -> str:
    """Build code for filling visible form fields by semantic field names."""

    safe_fields = fields if isinstance(fields, dict) else {}
    params = {
        "fields": {str(key): str(value) for key, value in safe_fields.items()},
        "max_fields": _clamp_int(max_fields, default=120, minimum=1, maximum=200),
        "viewport_only": bool(viewport_only),
        "clear_existing": bool(clear_existing),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  return await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
    const tokenise = (value) => String(value || '')
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .toLowerCase()
      .match(/[a-z0-9]+/g) || [];

    const tokenSet = (value) => new Set(
      tokenise(value).filter((token) => !['input', 'field', 'form', 'data', 'testid'].includes(token))
    );

    const labelFor = (el) => {{
      const id = el.getAttribute('id');
      const labels = [];
      if (id) {{
        try {{
          const direct = document.querySelector(`label[for="${{attrEscape(id)}}"]`);
          if (direct) labels.push(textOf(direct, 180));
        }} catch (_) {{}}
      }}
      if (el.labels) {{
        for (const label of Array.from(el.labels)) labels.push(textOf(label, 180));
      }}
      let parent = el.parentElement;
      for (let depth = 0; parent && depth < 4; depth += 1, parent = parent.parentElement) {{
        const selector = 'label,[class*="label" i],[class*="title" i],[class*="name" i]';
        const candidate = parent.querySelector(selector);
        if (candidate) labels.push(textOf(candidate, 180));
      }}
      return normalize(labels.filter(Boolean).join(' | '), 260);
    }};

    const surroundingText = (el) => {{
      const parts = [];
      const parent = el.parentElement;
      if (parent) parts.push(normalize(parent.innerText || parent.textContent || '', 260));
      const previous = el.previousElementSibling;
      const next = el.nextElementSibling;
      if (previous) parts.push(textOf(previous, 160));
      if (next) parts.push(textOf(next, 160));
      return normalize(parts.filter(Boolean).join(' | '), 360);
    }};

    const collectFields = () => {{
      const selectors = [
        'input',
        'textarea',
        'select',
        '[contenteditable="true"]',
        '[role="textbox"]',
        '[role="combobox"]'
      ];
      const nodes = [];
      const seen = new Set();
      for (const selector of selectors) {{
        try {{
          for (const el of Array.from(document.querySelectorAll(selector))) {{
            if (!seen.has(el)) {{
              seen.add(el);
              nodes.push(el);
            }}
          }}
        }} catch (_) {{}}
      }}

      const fields = [];
      for (const el of nodes) {{
        if (!isVisible(el, params.viewport_only !== false)) continue;
        const tag = el.tagName ? el.tagName.toLowerCase() : '';
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (tag === 'input' && ['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
        const label = labelFor(el);
        const placeholder = normalize(el.getAttribute('placeholder') || '', 180);
        const aria = normalize(el.getAttribute('aria-label') || '', 180);
        const name = normalize(el.getAttribute('name') || '', 180);
        const id = normalize(el.getAttribute('id') || '', 180);
        const testid = normalize(
          el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy') || '',
          180
        );
        const textContext = normalize(
          [label, placeholder, aria, name, id, testid, surroundingText(el)].filter(Boolean).join(' | '),
          520
        );
        fields.push({{
          el,
          tag,
          type,
          role: normalize(el.getAttribute('role') || tag, 60),
          label,
          placeholder,
          aria_label: aria,
          name,
          id,
          testid,
          text_context: textContext,
          selector_hint: buildSelectorHint(el),
          disabled: disabledLike(el),
          readonly: !!el.readOnly || String(el.getAttribute('aria-readonly') || '').toLowerCase() === 'true',
          bbox: bbox(visibleRect(el)),
        }});
      }}
      fields.sort((a, b) => (a.bbox?.y || 0) - (b.bbox?.y || 0) || (a.bbox?.x || 0) - (b.bbox?.x || 0));
      return fields.slice(0, params.max_fields || 120);
    }};

    const setNativeValue = (el, value) => {{
      const tag = el.tagName ? el.tagName.toLowerCase() : '';
      const currentValue = String(el.value || el.textContent || '');
      const nextValue = params.clear_existing === false && currentValue ? currentValue + String(value || '') :
        String(value || '');
      if (tag === 'select') {{
        const wanted = String(value || '').trim().toLowerCase();
        const options = Array.from(el.options || []);
        const option = options.find((item) => String(item.text || '').trim().toLowerCase() === wanted) ||
          options.find((item) => String(item.value || '').trim().toLowerCase() === wanted) ||
          options.find((item) => String(item.text || '').toLowerCase().includes(wanted));
        if (!option) return false;
        el.value = option.value;
      }} else if (el.isContentEditable) {{
        el.textContent = nextValue;
      }} else {{
        const prototype = Object.getPrototypeOf(el);
        const descriptor = prototype ? Object.getOwnPropertyDescriptor(prototype, 'value') : null;
        if (descriptor && descriptor.set) descriptor.set.call(el, nextValue);
        else el.value = nextValue;
      }}
      for (const eventName of ['input', 'change', 'blur']) {{
        el.dispatchEvent(new Event(eventName, {{ bubbles: true }}));
      }}
      return true;
    }};

    const scoreField = (requestedKey, candidate) => {{
      const keyLower = String(requestedKey || '').toLowerCase().trim();
      const keyTokens = tokenSet(requestedKey);
      const targetText = [
        candidate.label,
        candidate.placeholder,
        candidate.aria_label,
        candidate.name,
        candidate.id,
        candidate.testid,
        candidate.text_context,
      ].filter(Boolean).join(' ').toLowerCase();
      const targetTokens = tokenSet(targetText);
      let score = 0;
      if (keyLower && targetText.includes(keyLower)) score += 12;
      for (const token of keyTokens) if (targetTokens.has(token)) score += 3;
      if (candidate.label && candidate.label.toLowerCase().includes(keyLower)) score += 6;
      if (candidate.placeholder && candidate.placeholder.toLowerCase().includes(keyLower)) score += 4;
      if (candidate.disabled || candidate.readonly) score -= 100;
      return score;
    }};

    const candidates = collectFields();
    const filled = [];
    const failed = [];
    const used = new Set();
    for (const [requestedKey, requestedValue] of Object.entries(params.fields || {{}})) {{
      const ranked = candidates
        .map((candidate, index) => ({{ candidate, index, score: scoreField(requestedKey, candidate) }}))
        .filter((item) => item.score > 0 && !used.has(item.index))
        .sort((a, b) => b.score - a.score || a.index - b.index);
      const best = ranked[0];
      if (!best) {{
        failed.push({{ field: requestedKey, error: 'field_not_found' }});
        continue;
      }}
      const ok = setNativeValue(best.candidate.el, requestedValue);
      if (!ok) {{
        failed.push({{
          field: requestedKey,
          error: 'field_value_not_set',
          selector_hint: best.candidate.selector_hint,
        }});
        continue;
      }}
      used.add(best.index);
      filled.push({{
        field: requestedKey,
        selector_hint: best.candidate.selector_hint,
        label: best.candidate.label,
        placeholder: best.candidate.placeholder,
        score: best.score,
      }});
    }}

    return {{
      ok: failed.length === 0,
      error: failed.length ? 'semantic_form_fill_incomplete' : null,
      url: window.location.href,
      title: document.title,
      filled,
      failed,
      fields_considered: candidates.length,
    }};
  }}, params);
}}
"""


def build_dropdown_probe_js(
    *,
    max_options: int = 30,
    viewport_only: bool = True,
    query: str = "",
) -> str:
    """Build code for probing the currently open dropdown/autocomplete menu."""

    params = {
        "max_options": _clamp_int(max_options, default=30, minimum=1, maximum=80),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip(),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  return await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
{_DROPDOWN_JS}
    const active = document.activeElement;
    return {{
      ok: true,
      error: null,
      url: window.location.href,
      title: document.title,
      query: params.query || '',
      active_element: active ? {{
        tag: active.tagName ? active.tagName.toLowerCase() : '',
        text: textOf(active, 180),
        selector_hint: buildSelectorHint(active)
      }} : null,
      options: collectDropdownOptions(params.query || '', params.max_options || 30, params.viewport_only !== false)
    }};
  }}, params);
}}
"""


def build_dropdown_select_js(
    *,
    field_selector: str = "",
    field_label: str = "",
    query: str = "",
    option_text: str = "",
    option_texts: list[str] | None = None,
    exact: bool = False,
    preserve_existing: bool = True,
    selection_mode: str = "add",
    timeout_ms: int = 5000,
    wait_after_type_ms: int = 250,
) -> str:
    """Build code for selecting native, Select2, and custom dropdown options."""

    params = {
        "field_selector": str(field_selector or "").strip(),
        "field_label": str(field_label or "").strip(),
        "query": str(query or "").strip(),
        "option_text": str(option_text or "").strip(),
        "option_texts": [str(item).strip() for item in (option_texts or []) if str(item).strip()],
        "exact": bool(exact),
        "preserve_existing": bool(preserve_existing),
        "selection_mode": str(selection_mode or "add").strip().lower(),
        "timeout_ms": _clamp_int(timeout_ms, default=5000, minimum=250, maximum=30000),
        "wait_after_type_ms": _clamp_int(wait_after_type_ms, default=250, minimum=0, maximum=5000),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  const timeout = Math.max(250, Math.min(Number(params.timeout_ms || 5000), 30000));
  const opened = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
    const normalizeLower = (value) => normalize(value, 260).toLowerCase();
    const requestedTexts = Array.isArray(params.option_texts) && params.option_texts.length
      ? params.option_texts.map((item) => normalize(item, 260)).filter(Boolean)
      : [normalize(params.option_text || params.query || '', 260)].filter(Boolean);
    const targetIdentity = (field) => {{
      if (!field) return {{
        field_selector: params.field_selector || '',
        resolved_field_selector: '',
        field_id: '',
        field_name: '',
        field_kind: 'unknown',
        target_family: `semantic_dropdown:${{window.location.href}}:${{
          params.field_selector || params.field_label || 'unknown'
        }}`
      }};
      const selector = buildSelectorHint(field);
      const tag = field.tagName ? field.tagName.toLowerCase() : 'custom';
      const multiple = !!(tag === 'select' && field.multiple);
      const id = field.getAttribute ? String(field.getAttribute('id') || '') : '';
      const name = field.getAttribute ? String(field.getAttribute('name') || '') : '';
      const stable = id ? `#${{id}}` : (name ? `${{tag}}[name="${{name}}"]` : selector);
      return {{
        field_selector: params.field_selector || selector,
        resolved_field_selector: selector,
        field_id: id,
        field_name: name,
        field_kind: multiple ? 'native_select_multiple' : tag,
        target_family: `semantic_dropdown:${{window.location.href}}:${{stable || params.field_label || 'unknown'}}`
      }};
    }};
    const resolveByLabel = () => {{
      const wanted = normalizeLower(params.field_label);
      if (!wanted) return null;
      const labels = Array.from(document.querySelectorAll('label'));
      const labelText = (item) => normalizeLower(textOf(item, 260));
      const label = labels.find((item) => labelText(item) === wanted) ||
        labels.find((item) => labelText(item).includes(wanted));
      if (!label) return null;
      const htmlFor = label.getAttribute('for');
      if (htmlFor) {{
        const linked = document.getElementById(htmlFor);
        if (linked) return linked;
      }}
      const group = label.closest('.form-group, .form-row, .row, fieldset, td, tr') || label.parentElement;
      if (!group) return null;
      return group.querySelector(
        'select, input, textarea, [role="combobox"], .select2-container, .ui-autocomplete-input, #msdd'
      );
    }};
    const resolveField = () => {{
      if (params.field_selector) {{
        try {{
          const selected = document.querySelector(params.field_selector);
          if (selected) return selected;
        }} catch (_) {{}}
      }}
      return resolveByLabel();
    }};
    const field = resolveField();
    if (!field) {{
      return {{
        ok: false,
        error: 'dropdown_field_not_found',
        field_selector: params.field_selector,
        field_label: params.field_label,
        resolved_field_selector: '',
        field_id: '',
        field_name: '',
        field_kind: 'unknown',
        target_family: `semantic_dropdown:${{window.location.href}}:${{
          params.field_selector || params.field_label || 'unknown'
        }}`
      }};
    }}
    const identity = targetIdentity(field);

    if (field.tagName && field.tagName.toLowerCase() === 'select') {{
      const options = Array.from(field.options || []);
      const findMatch = (wantedText) => {{
        const wanted = normalizeLower(wantedText);
        const exactMatch = options.find((item) => normalizeLower(item.text) === wanted);
        const partialMatch = params.exact
          ? null
          : options.find((item) => normalizeLower(item.text).includes(wanted));
        return exactMatch || partialMatch || null;
      }};
      const matches = requestedTexts.map((text) => ({{ text, option: findMatch(text) }}));
      const missing = matches.filter((item) => !item.option).map((item) => item.text);
      if (missing.length) {{
        return {{
          ok: false,
          error: 'dropdown_option_not_found',
          option_text: params.option_text,
          option_texts: requestedTexts,
          missing_options: missing,
          options: options.slice(0, 100).map((item) => normalize(item.text || item.value || '', 160)),
          multiple: !!field.multiple,
          selected_values: Array.from(field.selectedOptions || []).map((item) => String(item.value || '')),
          selected_texts: Array.from(field.selectedOptions || []).map((item) => normalize(item.text || '', 160)),
          ...identity
        }};
      }}
      if (matches.length) {{
        const existing = Array.from(field.selectedOptions || []);
        const replace = params.selection_mode === 'replace' || !params.preserve_existing || !field.multiple;
        if (replace) options.forEach((item) => {{ item.selected = false; }});
        for (const item of matches) item.option.selected = true;
        if (!field.multiple && matches[0]) field.value = matches[0].option.value;
        field.dispatchEvent(new Event('input', {{ bubbles: true }}));
        field.dispatchEvent(new Event('change', {{ bubbles: true }}));
        const selected = Array.from(field.selectedOptions || []);
        const requestedValues = matches.map((item) => String(item.option.value || ''));
        const existingValues = existing.map((item) => String(item.value || ''));
        return {{
          ok: true,
          native_select: true,
          multiple: !!field.multiple,
          selected_text: matches.length === 1 ? normalize(matches[0].option.text, 260) : '',
          selected_value: matches.length === 1 ? String(matches[0].option.value || '') : '',
          selected_texts: selected.map((item) => normalize(item.text || '', 260)),
          selected_values: selected.map((item) => String(item.value || '')),
          requested_texts: requestedTexts,
          requested_values: requestedValues,
          added_values: requestedValues.filter((value) => !existingValues.includes(value)),
          preserved_values: selected
            .map((item) => String(item.value || ''))
            .filter((value) => existingValues.includes(value)),
          selection_mode: replace ? 'replace' : 'add',
          preserve_existing: !replace,
          ...identity
        }};
      }}
    }}

    const id = field.getAttribute && field.getAttribute('id');
    const customCandidates = [];
    if (field.matches && field.matches('.select2-container, [role="combobox"], #msdd')) {{
      customCandidates.push(field);
    }}
    if (id) {{
      const escaped = cssEscape(id);
      const selectors = [
        `#s2id_${{escaped}}`,
        `.select2-container[aria-labelledby*="${{attrEscape(id)}}"]`,
        `[aria-controls*="${{attrEscape(id)}}"]`
      ];
      for (const selector of selectors) {{
        try {{ customCandidates.push(...document.querySelectorAll(selector)); }} catch (_) {{}}
      }}
    }}
    if (field.nextElementSibling) customCandidates.push(field.nextElementSibling);
    if (field.previousElementSibling) customCandidates.push(field.previousElementSibling);
    const group = field.closest && field.closest('.form-group, .form-row, .row, fieldset, td, tr');
    if (group) {{
      customCandidates.push(...group.querySelectorAll(
        '.select2-container, .ui-autocomplete-input, #msdd, [role="combobox"]'
      ));
    }}
    const isDropdownTrigger = (item) => !!(
      item && item.matches && (
        item.matches('.select2-container, [role="combobox"], #msdd, .ui-autocomplete-input') ||
        item.querySelector('.select2-selection, .select2-choice, [role="combobox"]')
      )
    );
    const trigger = customCandidates.find((item) => isDropdownTrigger(item) && isVisible(item, false)) ||
      (isVisible(field, false) ? field : null);
    if (!trigger || disabledLike(trigger)) {{
      return {{ ok: false, error: 'dropdown_field_disabled', ...identity }};
    }}
    try {{ trigger.scrollIntoView({{ block: 'center', inline: 'nearest' }}); }} catch (_) {{}}
    clickLikeUser(trigger);
    if (trigger.focus) trigger.focus();
    return {{
      ok: true,
      native_select: false,
      ...identity,
      trigger_selector: buildSelectorHint(trigger)
    }};
  }}, params);

  if (!opened || !opened.ok) return opened || {{ ok: false, error: 'dropdown_open_failed' }};
  if (opened.native_select) {{
    return {{
      ...opened,
      error: null,
      selected: {{ text: opened.selected_text, selector_hint: opened.resolved_field_selector }},
      verified: true,
      method: 'native_select',
      display_value: opened.selected_texts && opened.selected_texts.length
        ? opened.selected_texts.join(', ')
        : opened.selected_text,
      dropdown_closed: true,
      url: page.url(),
      title: await page.title().catch(() => '')
    }};
  }}

  await page.waitForTimeout(80);
  let typedInto = null;
  if (params.query) {{
    typedInto = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
      const visibleInputs = Array.from(document.querySelectorAll(
        '.select2-search__field, .select2-input, [role="searchbox"], '
        + '[role="listbox"] input, .ui-autocomplete-input, '
        + '.dropdown-menu input, input[aria-autocomplete]'
      )).filter((item) => isVisible(item, true));
      const active = document.activeElement;
      const target = active && /^(input|textarea)$/i.test(active.tagName || '') && isVisible(active, true)
        ? active
        : visibleInputs[0];
      if (!target) return null;
      const proto = target.tagName.toLowerCase() === 'textarea'
        ? window.HTMLTextAreaElement && window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement && window.HTMLInputElement.prototype;
      const descriptor = proto && Object.getOwnPropertyDescriptor(proto, 'value');
      target.focus();
      if (descriptor && descriptor.set) descriptor.set.call(target, String(params.query || ''));
      else target.value = String(params.query || '');
      target.dispatchEvent(new InputEvent('input', {{
        bubbles: true,
        inputType: 'insertText',
        data: String(params.query || '')
      }}));
      target.dispatchEvent(new Event('change', {{ bubbles: true }}));
      target.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true, key: 'a' }}));
      return buildSelectorHint(target);
    }}, params);
    if (!typedInto) {{
      await page.keyboard.type(String(params.query), {{ delay: 15 }}).catch(() => {{}});
    }}
    if (params.wait_after_type_ms > 0) await page.waitForTimeout(params.wait_after_type_ms);
  }}

  const selection = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
{_DROPDOWN_JS}
    const wanted = String(params.option_text || params.query || '').trim().toLowerCase();
    const options = collectDropdownOptions(wanted, 80, true);
    const candidates = options.filter((item) => !item.disabled);
    const matches = candidates.filter((item) => {{
      const text = String(item.text || '').toLowerCase();
      if (!wanted) return true;
      return params.exact ? text === wanted : text.includes(wanted) || wanted.includes(text);
    }});
    const chosen = matches[0] || (!wanted ? candidates[0] : null);
    if (!chosen) {{
      return {{
        ok: false,
        error: 'dropdown_option_not_found',
        query: params.query,
        option_text: params.option_text,
        options,
        field_selector: params.field_selector || '',
        resolved_field_selector: params.resolved_field_selector || '',
        target_family: params.target_family || ''
      }};
    }}
    let element = null;
    try {{ element = document.querySelector(chosen.selector_hint); }} catch (_) {{ element = null; }}
    if (!element) {{
      const selectors = [
        '[role="option"]', '[role="menuitem"]', 'li', '[data-value]', 'option',
        '.select2-results__option', '.select2-result-label', '.ui-autocomplete li a'
      ];
      const all = selectors.flatMap((selector) => {{
        try {{ return Array.from(document.querySelectorAll(selector)); }} catch (_) {{ return []; }}
      }});
      element = all.find((item) => textOf(item, 260) === chosen.text) ||
        all.find((item) => textOf(item, 260).toLowerCase().includes(wanted));
    }}
    if (!element) return {{ ok: false, error: 'dropdown_option_element_lost', chosen, options }};
    clickLikeUser(element);
    return {{
      ok: true,
      error: null,
      selected: {{ text: chosen.text, selector_hint: chosen.selector_hint }},
      query: params.query,
      option_text: params.option_text
    }};
  }}, {{
    ...params,
    resolved_field_selector: opened.resolved_field_selector || '',
    target_family: opened.target_family || ''
  }});
  if (!selection || !selection.ok) {{
    return {{
      ...(selection || {{ ok: false, error: 'dropdown_selection_failed' }}),
      field_selector: opened.field_selector || params.field_selector || '',
      resolved_field_selector: opened.resolved_field_selector || '',
      field_id: opened.field_id || '',
      field_name: opened.field_name || '',
      field_kind: opened.field_kind || 'custom',
      trigger_selector: opened.trigger_selector || '',
      target_family: opened.target_family || ''
    }};
  }}

  await page.waitForTimeout(120);
  const verification = await page.evaluate((verificationParams) => {{
{_DOM_HELPERS_JS}
    const wanted = String(
      verificationParams.option_text || verificationParams.query || ''
    ).trim().toLowerCase();
    let field = null;
    const selector = verificationParams.resolved_field_selector || verificationParams.field_selector;
    if (selector) {{
      try {{ field = document.querySelector(selector); }} catch (_) {{ field = null; }}
    }}
    const texts = [];
    if (field) {{
      texts.push(String(field.value || ''));
      if (field.selectedOptions) texts.push(...Array.from(field.selectedOptions).map((item) => item.text));
      const group = field.closest && field.closest('.form-group, .form-row, .row, fieldset, td, tr');
      if (group) texts.push(normalize(group.innerText || group.textContent || '', 500));
    }}
    const selectedNodes = document.querySelectorAll(
      '.select2-selection__rendered, .select2-chosen, .select2-selection__choice, '
      + '.ui-autocomplete-multiselect-item, .token, [aria-selected="true"]'
    );
    texts.push(...Array.from(selectedNodes).filter((item) => isVisible(item, false)).map((item) => textOf(item, 260)));
    const displayValue = normalize(texts.filter(Boolean).join(' | '), 600);
    return {{
      verified: !wanted || displayValue.toLowerCase().includes(wanted),
      display_value: displayValue
    }};
  }}, {{ ...params, resolved_field_selector: opened.field_selector || '' }});

  await page.keyboard.press('Escape').catch(() => {{}});
  await page.waitForTimeout(60);
  const dropdownClosed = await page.evaluate(() => {{
    const selectors = [
      '[role="listbox"]', '.select2-drop', '.select2-dropdown', '.select2-results',
      '.ui-autocomplete', '.dropdown-menu'
    ];
    const visible = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((item) => {{
        const rect = item.getBoundingClientRect();
        const style = window.getComputedStyle(item);
        return rect.width > 1 && rect.height > 1 && style.display !== 'none' && style.visibility !== 'hidden';
      }});
    return visible.length === 0;
  }}).catch(() => false);

  return {{
    ...opened,
    ...selection,
    method: 'custom_dropdown',
    typed_into: typedInto,
    verified: !!(verification && verification.verified),
    display_value: verification ? verification.display_value : '',
    dropdown_closed: dropdownClosed,
    url: page.url(),
    title: await page.title().catch(() => '')
  }};
}}
"""


def build_calendar_probe_js(
    *,
    max_days: int = 120,
    viewport_only: bool = True,
    query: str = "",
) -> str:
    """Build code for probing visible calendar/date-picker days."""

    params = {
        "max_days": _clamp_int(max_days, default=120, minimum=1, maximum=240),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip(),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  return await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
    const days = collectCalendarDays(params.max_days || 120, params.viewport_only !== false)
      .map((item) => {{ const {{ _el, ...safe }} = item; return safe; }});
    const months = [];
    for (const root of findCalendarRoots(params.viewport_only !== false)) {{
      const current = inferRootMonth(root);
      if (current.label) months.push(current);
    }}
    return {{
      ok: true,
      error: null,
      url: window.location.href,
      title: document.title,
      query: params.query || '',
      visible_months: months,
      days
    }};
  }}, params);
}}
"""


def build_calendar_select_js(
    *,
    date: str,
    field_selector: str = "",
    next_selector: str = "",
    prev_selector: str = "",
    max_month_clicks: int = 18,
    timeout_ms: int = 5000,
    try_direct_input: bool = True,
) -> str:
    """Build code for selecting an exact date and verifying the stable result."""

    params = {
        "date": str(date or "").strip(),
        "field_selector": str(field_selector or "").strip(),
        "next_selector": str(next_selector or "").strip(),
        "prev_selector": str(prev_selector or "").strip(),
        "max_month_clicks": _clamp_int(max_month_clicks, default=18, minimum=0, maximum=60),
        "timeout_ms": _clamp_int(timeout_ms, default=5000, minimum=250, maximum=30000),
        "try_direct_input": bool(try_direct_input),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  const targetDate = new Date(`${{params.date}}T00:00:00`);
  if (Number.isNaN(targetDate.getTime())) {{
    return {{ ok: false, error: `invalid_date: ${{params.date}}` }};
  }}
  const targetParts = {{
    year: targetDate.getFullYear(),
    month: targetDate.getMonth(),
    day: targetDate.getDate()
  }};
  const targetIso = [
    targetParts.year,
    String(targetParts.month + 1).padStart(2, '0'),
    String(targetParts.day).padStart(2, '0')
  ].join('-');
  const timeout = Math.max(250, Math.min(Number(params.timeout_ms || 5000), 30000));
  let directInputAttempted = false;
  let directInputOk = false;
  let directInputVerification = null;
  const nativeSelection = {{ attempted: false, year: null, month: null }};
  let preResetState = null;

  const closeCalendarOverlay = async () => {{
    const frameworkHide = async (forceHideKnownRoots = false) => await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
      const active = document.activeElement;
      if (active && active.blur) active.blur();
      const jq = window.jQuery;
      if (jq && jq.fn && typeof jq.fn.datepicker === 'function') {{
        document.querySelectorAll('input.hasDatepicker').forEach((field) => {{
          try {{ jq(field).datepicker('hide'); }} catch (_) {{ /* best effort */ }}
        }});
        try {{
          if (jq.datepicker && typeof jq.datepicker._hideDatepicker === 'function') {{
            jq.datepicker._hideDatepicker();
          }}
        }} catch (_) {{ /* best effort */ }}
      }}
      if (payload.force_hide) {{
        document.querySelectorAll('#ui-datepicker-div, .ui-datepicker, .react-datepicker-popper').forEach((root) => {{
          if (root && root.style) root.style.display = 'none';
          if (root) root.setAttribute('aria-hidden', 'true');
        }});
      }}
      const roots = findCalendarRoots(false).filter(
        (root) => root !== document.body && isVisible(root, false)
      );
      return {{ closed: roots.length === 0, visible_calendar_count: roots.length }};
    }}, {{ force_hide: forceHideKnownRoots }}).catch(() => ({{
      closed: false,
      visible_calendar_count: -1
    }}));

    let state = await frameworkHide(false);
    if (state.closed) return state;
    await page.keyboard.press('Escape').catch(() => {{}});
    await page.waitForTimeout(80);
    state = await frameworkHide(false);
    if (state.closed) return state;
    await page.keyboard.press('Escape').catch(() => {{}});
    await page.waitForTimeout(100);
    return await frameworkHide(true);
  }};

  const verifyFieldDate = async () => {{
    if (!params.field_selector) {{
      return {{
        field_verification_skipped: true,
        field_matches_target: true,
        field_value: '',
        field_date: targetIso
      }};
    }}
    await page.waitForTimeout(180);
    return await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
      let field = null;
      try {{ field = document.querySelector(payload.field_selector); }} catch (_) {{ field = null; }}
      if (!field) {{
        return {{
          field_verification_skipped: false,
          field_matches_target: false,
          field_value: '',
          field_date: null,
          verification_error: 'calendar_field_not_found_after_selection'
        }};
      }}
      const values = [
        field.value,
        field.getAttribute('value'),
        field.getAttribute('aria-valuetext'),
        field.getAttribute('aria-label'),
        field.textContent
      ].map((value) => normalize(value || '', 240)).filter(Boolean);
      const matchesTarget = (value) => {{
        const parsed = parseDateish(value);
        if (parsed && parsed.year === payload.year && parsed.month === payload.month && parsed.day === payload.day) {{
          return {{ matched: true, date: payload.target_iso }};
        }}
        const numeric = String(value || '').match(/\b(\d{{1,2}})[-/.](\d{{1,2}})[-/.](19\d{{2}}|20\d{{2}})\b/);
        if (numeric && Number(numeric[3]) === payload.year) {{
          const first = Number(numeric[1]);
          const second = Number(numeric[2]);
          const month = payload.month + 1;
          if ((first === month && second === payload.day) || (first === payload.day && second === month)) {{
            return {{ matched: true, date: payload.target_iso }};
          }}
        }}
        const nativeDate = new Date(value);
        if (!Number.isNaN(nativeDate.getTime()) &&
            nativeDate.getFullYear() === payload.year &&
            nativeDate.getMonth() === payload.month &&
            nativeDate.getDate() === payload.day) {{
          return {{ matched: true, date: payload.target_iso }};
        }}
        return {{ matched: false, date: null }};
      }};
      for (const value of values) {{
        const checked = matchesTarget(value);
        if (checked.matched) {{
          return {{
            field_verification_skipped: false,
            field_matches_target: true,
            field_value: value,
            field_date: checked.date,
            verification_error: null
          }};
        }}
      }}
      return {{
        field_verification_skipped: false,
        field_matches_target: false,
        field_value: values[0] || '',
        field_date: null,
        verification_error: 'selected_date_not_persisted_after_cleanup'
      }};
    }}, {{
      field_selector: params.field_selector,
      year: targetParts.year,
      month: targetParts.month,
      day: targetParts.day,
      target_iso: targetIso
    }}).catch((error) => ({{
      field_verification_skipped: false,
      field_matches_target: false,
      field_value: '',
      field_date: null,
      verification_error: String(error && error.message ? error.message : error)
    }}));
  }};

  const finalizeSelection = async (base, method) => {{
    await page.waitForTimeout(120);
    const closeState = await closeCalendarOverlay();
    const verification = await verifyFieldDate();
    let error = null;
    if (!closeState.closed) error = 'calendar_overlay_still_open_after_selection';
    else if (!verification.field_matches_target) error = verification.verification_error ||
      'selected_date_not_persisted_after_cleanup';
    return {{
      ...base,
      ok: !error,
      error,
      selected_date: targetIso,
      method,
      direct_input_attempted: directInputAttempted,
      direct_input_ok: directInputOk,
      direct_input_verification: directInputVerification,
      native_selection: nativeSelection,
      pre_reset: preResetState,
      calendar_closed: !!closeState.closed,
      visible_calendar_count: closeState.visible_calendar_count,
      ...verification,
      url: page.url(),
      title: await page.title().catch(() => '')
    }};
  }};

  const finalizeFailure = async (base) => {{
    const closeState = await closeCalendarOverlay();
    const verification = await verifyFieldDate();
    return {{
      ...base,
      selected_date: targetIso,
      direct_input_attempted: directInputAttempted,
      direct_input_ok: directInputOk,
      direct_input_verification: directInputVerification,
      native_selection: nativeSelection,
      pre_reset: preResetState,
      calendar_closed: !!closeState.closed,
      visible_calendar_count: closeState.visible_calendar_count,
      ...verification,
      url: page.url(),
      title: await page.title().catch(() => '')
    }};
  }};

  const clickExactVisibleDate = async () => await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
    const days = collectCalendarDays(240, true);
    const exact = days.find((day) =>
      day.date === payload.target_iso && !day.disabled && !day.outside_month
    ) || days.find((day) => day.date === payload.target_iso && !day.disabled);
    if (exact && exact._el) {{
      const safe = {{ ...exact }};
      delete safe._el;
      clickLikeUser(exact._el);
      return {{ ok: true, error: null, selected_date: payload.target_iso, day: safe, visible_days: days.length }};
    }}
    return {{
      ok: false,
      error: 'date_not_visible',
      selected_date: payload.target_iso,
      visible_months: findCalendarRoots(true).map((root) => inferRootMonth(root)),
      days: days.length
    }};
  }}, {{ target_iso: targetIso }});

  const selectNativeCalendarPart = async (kind) => await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
    const roots = findCalendarRoots(false);
    const selects = Array.from(document.querySelectorAll('select')).filter((select) => {{
      if (!isVisible(select, false) || select.disabled) return false;
      return roots.some((root) => root === document.body || root.contains(select));
    }});
    const scored = selects.map((select) => {{
      const descriptor = normalize([
        select.id,
        select.name,
        select.className,
        select.getAttribute('aria-label'),
        select.getAttribute('title'),
        Array.from(select.options || []).slice(0, 20).map((option) => option.textContent).join(' ')
      ].filter(Boolean).join(' '), 1200).toLowerCase();
      let score = 0;
      if (payload.kind === 'year' && select.matches(
        '.ui-datepicker-year, .react-datepicker__year-select, select[name*="year" i], select[id*="year" i]'
      )) score += 120;
      if (payload.kind === 'month' && select.matches(
        '.ui-datepicker-month, .react-datepicker__month-select, select[name*="month" i], select[id*="month" i]'
      )) score += 120;
      if (descriptor.includes(payload.kind)) score += 60;
      if (descriptor.includes('date')) score += 10;
      if (payload.kind === 'year' && descriptor.includes(String(payload.year))) score += 30;
      if (payload.kind === 'month' && monthIndexFromText(descriptor) >= 0) score += 20;
      return {{ select, descriptor, score }};
    }}).sort((a, b) => b.score - a.score);
    const candidate = scored.find((item) => item.score > 0);
    if (!candidate) return {{ ok: false, error: `calendar_${{payload.kind}}_select_not_found` }};
    const select = candidate.select;
    const options = Array.from(select.options || []);
    let option = null;
    if (payload.kind === 'year') {{
      option = options.find((item) => String(item.value).trim() === String(payload.year)) ||
        options.find((item) => normalize(item.textContent || '', 80).includes(String(payload.year)));
    }} else {{
      option = options.find((item) => monthIndexFromText(item.textContent || '') === payload.month);
      if (!option) {{
        const numericValues = options
          .map((item) => Number(item.value))
          .filter((value) => Number.isFinite(value));
        const desired = numericValues.includes(0) ? payload.month : payload.month + 1;
        option = options.find((item) => Number(item.value) === desired);
      }}
    }}
    if (!option) {{
      return {{
        ok: false,
        error: `calendar_${{payload.kind}}_option_not_found`,
        selector_hint: buildSelectorHint(select),
        available: options.slice(0, 40).map((item) => normalize(item.textContent || item.value || '', 80))
      }};
    }}
    select.focus();
    select.value = option.value;
    option.selected = true;
    select.dispatchEvent(new Event('input', {{ bubbles: true }}));
    select.dispatchEvent(new Event('change', {{ bubbles: true }}));
    return {{
      ok: true,
      error: null,
      kind: payload.kind,
      selected_value: String(option.value),
      selected_text: normalize(option.textContent || '', 100),
      selector_hint: buildSelectorHint(select)
    }};
  }}, {{ kind, year: targetParts.year, month: targetParts.month }}).catch((error) => ({{
    ok: false,
    error: String(error && error.message ? error.message : error),
    kind
  }}));

  const tryJqueryUiSetDate = async () => await page.evaluate((payload) => {{
    let field = null;
    try {{ field = document.querySelector(payload.field_selector); }} catch (_) {{ field = null; }}
    const jq = window.jQuery;
    if (!field || !jq || !jq.fn || typeof jq.fn.datepicker !== 'function') {{
      return {{ ok: false, error: 'jquery_ui_datepicker_not_available' }};
    }}
    const wrapped = jq(field);
    if (!wrapped.hasClass('hasDatepicker') && !wrapped.data('datepicker')) {{
      return {{ ok: false, error: 'field_not_managed_by_jquery_ui_datepicker' }};
    }}
    try {{
      wrapped.datepicker('setDate', new Date(payload.year, payload.month, payload.day));
      wrapped.trigger('input');
      wrapped.trigger('change');
      wrapped.datepicker('hide');
      if (jq.datepicker && typeof jq.datepicker._hideDatepicker === 'function') {{
        jq.datepicker._hideDatepicker();
      }}
      if (field.blur) field.blur();
      return {{ ok: true, error: null, field_selector: payload.field_selector }};
    }} catch (error) {{
      return {{
        ok: false,
        error: String(error && error.message ? error.message : error),
        field_selector: payload.field_selector
      }};
    }}
  }}, {{
    field_selector: params.field_selector,
    year: targetParts.year,
    month: targetParts.month,
    day: targetParts.day
  }}).catch((error) => ({{
    ok: false,
    error: String(error && error.message ? error.message : error)
  }}));

  let target = null;
  if (params.field_selector) {{
    target = page.locator(String(params.field_selector)).first();
    preResetState = await closeCalendarOverlay();
    const jqueryResult = await tryJqueryUiSetDate();
    if (jqueryResult && jqueryResult.ok) {{
      return await finalizeSelection(jqueryResult, 'jquery_ui_set_date');
    }}
    if (params.try_direct_input) {{
      directInputAttempted = true;
      directInputOk = await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
        let el = null;
        try {{ el = document.querySelector(payload.field_selector); }} catch (_) {{ el = null; }}
        if (!el || !/^(input|textarea)$/i.test(el.tagName || '') || el.readOnly || el.disabled) return false;
        const proto = el.tagName.toLowerCase() === 'textarea'
          ? window.HTMLTextAreaElement && window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement && window.HTMLInputElement.prototype;
        const descriptor = proto && Object.getOwnPropertyDescriptor(proto, 'value');
        el.focus();
        if (descriptor && descriptor.set) descriptor.set.call(el, payload.date);
        else el.value = payload.date;
        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: payload.date }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        if (el.blur) el.blur();
        return true;
      }}, params).catch(() => false);
      if (directInputOk) {{
        const directResult = await finalizeSelection({{ field_selector: params.field_selector }}, 'direct_input');
        directInputVerification = {{
          ok: directResult.ok,
          field_matches_target: directResult.field_matches_target,
          field_value: directResult.field_value,
          calendar_closed: directResult.calendar_closed,
          error: directResult.error
        }};
        if (directResult.ok || directResult.field_matches_target) return directResult;
      }}
    }}
    await target.click({{ timeout }});
    await page.waitForTimeout(120);
  }}

  nativeSelection.attempted = true;
  nativeSelection.year = await selectNativeCalendarPart('year');
  if (nativeSelection.year && nativeSelection.year.ok) await page.waitForTimeout(160);
  nativeSelection.month = await selectNativeCalendarPart('month');
  if (nativeSelection.month && nativeSelection.month.ok) await page.waitForTimeout(180);
  if ((nativeSelection.year && nativeSelection.year.ok) || (nativeSelection.month && nativeSelection.month.ok)) {{
    const nativeExact = await clickExactVisibleDate();
    if (nativeExact && nativeExact.ok) return await finalizeSelection(nativeExact, 'native_year_month_select');
  }}

  for (let attempt = 0; attempt <= params.max_month_clicks; attempt += 1) {{
    const result = await clickExactVisibleDate();
    if (result && result.ok) return await finalizeSelection(result, 'calendar_click');

    const nav = await page.evaluate((payload) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
      const months = findCalendarRoots(true).map((root) => inferRootMonth(root));
      const first = months.find((item) => item.month >= 0 && item.year) || null;
      let direction = 'next';
      if (first) {{
        const currentIndex = first.year * 12 + first.month;
        const targetIndex = payload.year * 12 + payload.month;
        direction = targetIndex < currentIndex ? 'prev' : 'next';
      }}
      const selector = direction === 'next' ? payload.next_selector : payload.prev_selector;
      const button = findMonthNavButton(direction, selector);
      if (!button) return {{
        ok: false,
        error: `calendar_${{direction}}_button_not_found`,
        direction,
        visible_months: months
      }};
      clickLikeUser(button);
      return {{ ok: true, direction, visible_months: months }};
    }}, {{
      year: targetParts.year,
      month: targetParts.month,
      next_selector: params.next_selector,
      prev_selector: params.prev_selector
    }});
    if (!nav || !nav.ok) {{
      return await finalizeFailure({{
        ok: false,
        error: (nav && nav.error) || 'calendar_navigation_failed',
        last_probe: result,
        nav
      }});
    }}
    await page.waitForTimeout(250);
  }}

  return await finalizeFailure({{
    ok: false,
    error: `date_not_found_after_${{params.max_month_clicks}}_month_clicks`
  }});
}}
"""
