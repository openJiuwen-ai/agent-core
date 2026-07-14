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
      el.dispatchEvent(new MouseEvent('click', options));
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
      'li',
      'option',
      '[data-value]',
      '[data-testid*="option" i]',
      '[data-testid*="suggest" i]',
      '[class*="option" i]',
      '[class*="suggest" i]',
      '[class*="autocomplete" i] li',
      '[class*="dropdown" i] li',
      '[class*="popup" i] li'
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
    query: str = "",
    option_text: str = "",
    exact: bool = False,
    timeout_ms: int = 5000,
    wait_after_type_ms: int = 250,
) -> str:
    """Build code for atomically typing into a dropdown and selecting an option."""

    params = {
        "field_selector": str(field_selector or "").strip(),
        "query": str(query or "").strip(),
        "option_text": str(option_text or "").strip(),
        "exact": bool(exact),
        "timeout_ms": _clamp_int(timeout_ms, default=5000, minimum=250, maximum=30000),
        "wait_after_type_ms": _clamp_int(wait_after_type_ms, default=250, minimum=0, maximum=5000),
    }
    return rf"""
async (page) => {{
  const params = {_json(params)};
  const timeout = Math.max(250, Math.min(Number(params.timeout_ms || 5000), 30000));
  if (params.field_selector) {{
    const target = page.locator(String(params.field_selector)).first();
    await target.click({{ timeout }});
    try {{ await page.keyboard.press('Control+A'); }}
    catch (_err) {{ await page.keyboard.press('Meta+A').catch(() => {{}}); }}
    await page.keyboard.press('Backspace').catch(() => {{}});
    if (params.query) {{
      await page.keyboard.type(String(params.query), {{ delay: 15 }});
    }}
    if (params.wait_after_type_ms > 0) await page.waitForTimeout(params.wait_after_type_ms);
  }} else if (params.query) {{
    await page.keyboard.type(String(params.query), {{ delay: 15 }});
    if (params.wait_after_type_ms > 0) await page.waitForTimeout(params.wait_after_type_ms);
  }}

  return await page.evaluate((params) => {{
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
    const chosen = matches[0] || candidates[0] || null;
    if (!chosen) {{
      return {{
        ok: false,
        error: 'dropdown_option_not_found',
        query: params.query,
        option_text: params.option_text,
        options
      }};
    }}
    let element = null;
    try {{ element = document.querySelector(chosen.selector_hint); }} catch (_) {{ element = null; }}
    if (!element) {{
      const all = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], li, [data-value], option'));
      element = all.find((el) => textOf(el, 260) === chosen.text) ||
        all.find((el) => textOf(el, 260).toLowerCase().includes(wanted));
    }}
    if (!element) return {{ ok: false, error: 'dropdown_option_element_lost', chosen, options }};
    clickLikeUser(element);
    return {{
      ok: true,
      error: null,
      selected: {{ text: chosen.text, selector_hint: chosen.selector_hint }},
      query: params.query,
      option_text: params.option_text,
      url: window.location.href,
      title: document.title
    }};
  }}, params);
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
    """Build code for selecting an exact date from an opened or openable date picker."""

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
  const targetIso = [
    targetDate.getFullYear(),
    String(targetDate.getMonth() + 1).padStart(2, '0'),
    String(targetDate.getDate()).padStart(2, '0')
  ].join('-');
  const timeout = Math.max(250, Math.min(Number(params.timeout_ms || 5000), 30000));
  let directInputAttempted = false;
  let directInputOk = false;

  if (params.field_selector) {{
    const target = page.locator(String(params.field_selector)).first();
    await target.click({{ timeout }});
    if (params.try_direct_input) {{
      directInputAttempted = true;
      directInputOk = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
        let el = null;
        try {{ el = document.querySelector(params.field_selector); }} catch (_) {{ el = null; }}
        if (!el || !/^(input|textarea)$/i.test(el.tagName || '') || el.readOnly || el.disabled) return false;
        const value = params.date;
        el.focus();
        el.value = value;
        el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: value }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        el.blur();
        return String(el.value || '').includes(value);
      }}, params).catch(() => false);
      if (directInputOk) {{
        return {{
          ok: true,
          error: null,
          selected_date: targetIso,
          method: 'direct_input',
          field_selector: params.field_selector
        }};
      }}
    }}
    await page.waitForTimeout(150);
  }}

  for (let attempt = 0; attempt <= params.max_month_clicks; attempt += 1) {{
    const result = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
      const target = parseDateish(params.date) || {{
        year: Number(params.date.slice(0, 4)),
        month: Number(params.date.slice(5, 7)) - 1,
        day: Number(params.date.slice(8, 10))
      }};
      const targetIso = [
        target.year,
        String(target.month + 1).padStart(2, '0'),
        String(target.day).padStart(2, '0')
      ].join('-');
      const days = collectCalendarDays(240, true);
      const exact = days.find((day) =>
        day.date === targetIso && !day.disabled && !day.outside_month
      ) || days.find((day) => day.date === targetIso && !day.disabled);
      if (exact && exact._el) {{
        const safe = {{ ...exact }};
        delete safe._el;
        clickLikeUser(exact._el);
        return {{ ok: true, error: null, selected_date: targetIso, day: safe, visible_days: days.length }};
      }}
      const visibleMonths = findCalendarRoots(true).map((root) => inferRootMonth(root));
      return {{
        ok: false,
        error: 'date_not_visible',
        selected_date: targetIso,
        visible_months: visibleMonths,
        days: days.length
      }};
    }}, params);
    if (result && result.ok) {{
      await page.waitForTimeout(150);
      return {{
        ...result,
        method: 'calendar_click',
        direct_input_attempted: directInputAttempted,
        direct_input_ok: directInputOk,
        url: page.url(),
        title: await page.title().catch(() => '')
      }};
    }}

    const nav = await page.evaluate((params) => {{
{_DOM_HELPERS_JS}
{_CALENDAR_JS}
      const target = parseDateish(params.date) || {{
        year: Number(params.date.slice(0, 4)),
        month: Number(params.date.slice(5, 7)) - 1,
        day: Number(params.date.slice(8, 10))
      }};
      const months = findCalendarRoots(true).map((root) => inferRootMonth(root));
      const first = months.find((item) => item.month >= 0 && item.year) || null;
      let direction = 'next';
      if (first) {{
        const currentIndex = first.year * 12 + first.month;
        const targetIndex = target.year * 12 + target.month;
        direction = targetIndex < currentIndex ? 'prev' : 'next';
      }}
      const button = findMonthNavButton(direction, direction === 'next' ? params.next_selector : params.prev_selector);
      if (!button) return {{
        ok: false,
        error: `calendar_${{direction}}_button_not_found`,
        direction,
        visible_months: months
      }};
      clickLikeUser(button);
      return {{ ok: true, direction, visible_months: months }};
    }}, params);
    if (!nav || !nav.ok) {{
      return {{
        ok: false,
        error: (nav && nav.error) || 'calendar_navigation_failed',
        selected_date: targetIso,
        last_probe: result,
        nav,
        direct_input_attempted: directInputAttempted,
        direct_input_ok: directInputOk
      }};
    }}
    await page.waitForTimeout(250);
  }}

  return {{
    ok: false,
    error: `date_not_found_after_${{params.max_month_clicks}}_month_clicks`,
    selected_date: targetIso,
    direct_input_attempted: directInputAttempted,
    direct_input_ok: directInputOk,
    url: page.url(),
    title: await page.title().catch(() => '')
  }};
}}
"""
