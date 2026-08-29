# coding: utf-8
"""Compact browser page probes for Playwright runtime."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def build_browser_state_metadata_js() -> str:
    """Build Playwright code for fresh page, tab, and position state."""

    return r"""
async (page) => {
  const pages = page.context().pages();

  const tabs = await Promise.all(
    pages.map(async (tab, index) => {
      let title = '';
      try {
        title = await tab.title();
      } catch (_error) {
        title = '';
      }
      return {
        index,
        current: tab === page,
        url: tab.url(),
        title,
      };
    })
  );

  const pageMetadata = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    const viewportWidth = Math.max(0, window.innerWidth || root?.clientWidth || 0);
    const viewportHeight = Math.max(0, window.innerHeight || root?.clientHeight || 0);
    const pageWidth = Math.max(
      viewportWidth,
      root?.scrollWidth || 0,
      body?.scrollWidth || 0
    );
    const pageHeight = Math.max(
      viewportHeight,
      root?.scrollHeight || 0,
      body?.scrollHeight || 0
    );
    const scrollX = Math.max(0, window.scrollX || window.pageXOffset || 0);
    const scrollY = Math.max(0, window.scrollY || window.pageYOffset || 0);

    const pagePosition = {
      viewport_width: viewportWidth,
      viewport_height: viewportHeight,
      page_width: pageWidth,
      page_height: pageHeight,
      scroll_x: scrollX,
      scroll_y: scrollY,
      pixels_above: scrollY,
      pixels_below: Math.max(0, pageHeight - scrollY - viewportHeight),
      pixels_left: scrollX,
      pixels_right: Math.max(0, pageWidth - scrollX - viewportWidth),
    };

    const compact = (value, limit = 160) => String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, limit);
    const visible = (element) => {
      if (!element || !element.isConnected) return false;
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return false;
      const style = window.getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0;
    };
    const elementKey = (element, index) => compact(
      element.getAttribute('name') ||
      element.getAttribute('id') ||
      element.getAttribute('aria-label') ||
      element.getAttribute('placeholder') ||
      compact(element.innerText || element.textContent || '', 80) ||
      `${element.tagName.toLowerCase()}:${element.getAttribute('type') || index}`,
      120
    ).toLowerCase();

    const formValues = [];
    const formElements = Array.from(document.querySelectorAll(
      'input:not([type="password"]):not([type="hidden"]),select,textarea,[contenteditable="true"]'
    )).slice(0, 80);
    formElements.forEach((element, index) => {
      if (!visible(element) || element.disabled) return;
      const type = String(element.getAttribute('type') || '').toLowerCase();
      let value = '';
      if (type === 'checkbox' || type === 'radio') {
        if (!element.checked) return;
        value = element.value || true;
      } else if (element.tagName.toLowerCase() === 'select') {
        value = Array.from(element.selectedOptions || []).map((option) => compact(option.text || option.value));
      } else if (element.isContentEditable) {
        value = compact(element.innerText || element.textContent || '');
      } else {
        value = compact(element.value || '');
      }
      if (value === '' || (Array.isArray(value) && value.length === 0)) return;
      formValues.push({ key: elementKey(element, index), value });
    });

    const selectedFilters = [];
    const selectedCandidates = Array.from(document.querySelectorAll(
      'input:checked,select,[aria-selected="true"],[aria-checked="true"],' +
      '[class*="filter" i] .selected,[class*="filter" i] .active,' +
      '[class*="facet" i] .selected,[class*="facet" i] .active,' +
      '[class*="sort" i] .selected,[class*="sort" i] .active,' +
      '[class*="price" i] .selected,[class*="price" i] .active,' +
      '[class*="rating" i] .selected,[class*="rating" i] .active'
    )).slice(0, 120);
    selectedCandidates.forEach((element, index) => {
      if (!visible(element)) return;
      const descriptor = compact([
        element.getAttribute('id') || '',
        element.getAttribute('class') || '',
        element.getAttribute('role') || '',
        element.getAttribute('aria-label') || '',
        element.parentElement?.getAttribute('class') || '',
      ].join(' '), 300).toLowerCase();
      const nativeSelected = element.matches('input:checked,select,[aria-checked="true"]');
      const filterContext = /(filter|facet|sort|order|price|rating|score|star|category|筛选|排序|价格|评分|星级|分类)/.test(descriptor);
      if (!nativeSelected && !filterContext) return;
      let value = compact(
        element.getAttribute('aria-label') || element.innerText || element.textContent || element.value || ''
      );
      if (element.tagName.toLowerCase() === 'select') {
        value = Array.from(element.selectedOptions || []).map((option) => compact(option.text || option.value)).join('|');
      }
      if (!value) return;
      selectedFilters.push({ key: elementKey(element, index), value });
    });

    let resultCount = null;
    const countCandidates = Array.from(document.querySelectorAll(
      '[data-total],[data-count],[aria-rowcount],[class*="result-count" i],[class*="total-count" i]'
    )).slice(0, 40);
    for (const element of countCandidates) {
      const raw = element.getAttribute('data-total') || element.getAttribute('data-count') ||
        element.getAttribute('aria-rowcount') || compact(element.textContent || '', 120);
      const match = String(raw || '').replace(/,/g, '').match(/\d+/);
      if (!match) continue;
      resultCount = Number(match[0]);
      break;
    }
    if (resultCount === null) {
      const resultCandidates = new Set();
      for (const selector of [
        'main article', 'main [role="article"]', 'main [role="row"]',
        '[data-testid*="result" i]', '[data-test*="result" i]',
        '[class*="search-result" i]', '[class*="result-item" i]'
      ]) {
        document.querySelectorAll(selector).forEach((element) => {
          if (visible(element)) resultCandidates.add(element);
        });
      }
      if (resultCandidates.size > 0) resultCount = resultCandidates.size;
    }

    return {
      page_position: pagePosition,
      semantic_state: {
        form_values: formValues.slice(0, 32),
        selected_filters: selectedFilters.slice(0, 32),
        result_count: resultCount,
      },
    };
  });

  let title = '';
  try {
    title = await page.title();
  } catch (_error) {
    title = '';
  }

  return {
    ok: true,
    url: page.url(),
    title,
    tabs,
    page_position: pageMetadata.page_position,
    semantic_state: pageMetadata.semantic_state,
  };
}
""".strip()


def build_interactive_probe_js(
    *,
    max_items: int = 50,
    viewport_only: bool = True,
    query: str = "",
    site_profiles: Optional[List[Dict[str, Any]]] = None,
    generation_id: str = "g0",
) -> str:
    """Build browser_run_code JavaScript for compact interactive-element probing."""

    params: Dict[str, Any] = {
        "max_items": _clamp_int(max_items, default=50, minimum=1, maximum=100),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip().lower(),
        "site_profiles": site_profiles or [],
        "generation_id": str(generation_id or "g0"),
    }
    params_json = json.dumps(params, ensure_ascii=False)

    return f"""
async (page) => {{
  const params = {params_json};

  return await page.evaluate((params) => {{
    const maxItems = Math.max(1, Math.min(Number(params.max_items || 50), 100));
    const viewportOnly = params.viewport_only !== false;
    const query = String(params.query || '').trim().toLowerCase();
    const generationId = String(params.generation_id || 'g0');
    const host = String(window.location.hostname || '').toLowerCase();
    const activeControlSemantics = Array.isArray(params.site_profiles)
      ? params.site_profiles
          .filter((profile) => (profile.domains || []).some((domain) => {{
            const value = String(domain || '').toLowerCase();
            return host === value || host.endsWith(`.${{value}}`);
          }}))
          .map((profile) => profile.semantic_rules?.control_semantics)
          .filter(Boolean)
      : [];

    const matchesProfilePattern = (value, patterns) => {{
      return Array.isArray(patterns) && patterns.some((pattern) => {{
        try {{
          return new RegExp(String(pattern), 'i').test(String(value || ''));
        }} catch (_) {{
          return false;
        }}
      }});
    }};

    const selectors = [
      'button',
      'a[href]',
      'input',
      'select',
      'textarea',
      '[role="button"]',
      '[role="link"]',
      '[role="textbox"]',
      '[role="searchbox"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="tab"]',
      '[role="option"]',
      '[role="gridcell"]',
      '[role="menuitem"]',
      '[aria-selected]',
      '[aria-checked]',
      '[aria-sort]',
      '[data-date]',
      '[data-day]',
      '[class*="calendar" i] [class*="day" i]',
      '[class*="calendar" i] [class*="date" i]',
      '[class*="datepicker" i] [class*="day" i]',
      '[class*="date-picker" i] [class*="day" i]',
      '[class*="sort" i] [class*="item" i]',
      '[class*="sort" i] [class*="option" i]',
      '[class*="rating" i] [class*="item" i]',
      '[class*="rating" i] [class*="option" i]',
      '[contenteditable="true"]',
      '[aria-label]',
      '[placeholder]',
      '[name]',
      '[data-testid]',
      '[data-test]',
      '[data-cy]'
    ];

    const normalize = (value, limit = 140) => {{
      return String(value || '')
        .replace(/\\s+/g, ' ')
        .trim()
        .slice(0, limit);
    }};

    const attrEscape = (value) => {{
      return String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    }};

    const cssEscape = (value) => {{
      const raw = String(value || '');
      if (window.CSS && typeof window.CSS.escape === 'function') {{
        return window.CSS.escape(raw);
      }}
      return raw.replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
    }};

    const roleFromTag = (el) => {{
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;

      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();

      if (tag === 'button') return 'button';
      if (tag === 'a') return 'link';
      if (el.getAttribute('contenteditable') === 'true') return 'textbox';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'input') {{
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'submit' || type === 'button') return 'button';
        return 'textbox';
      }}
      return '';
    }};

    const isVisible = (el, rect) => {{
      if (!el || !rect) return false;
      if (rect.width < 2 || rect.height < 2) return false;

      const style = window.getComputedStyle(el);
      if (!style) return false;
      if (style.display === 'none') return false;
      if (style.visibility === 'hidden') return false;
      if (Number(style.opacity) === 0) return false;

      if (viewportOnly) {{
        if (rect.bottom < 0) return false;
        if (rect.right < 0) return false;
        if (rect.top > window.innerHeight) return false;
        if (rect.left > window.innerWidth) return false;
      }}

      return true;
    }};

    const validateSelectorHint = (el, selector) => {{
      if (!el || !selector) return '';
      try {{
        const matches = Array.from(document.querySelectorAll(selector));
        return matches.length === 1 && matches[0] === el ? selector : '';
      }} catch (_) {{
        return '';
      }}
    }};

    const buildSelectorHint = (el) => {{
      const tag = el.tagName.toLowerCase();

      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy');
      if (testid) {{
        let candidate = `[data-cy="${{attrEscape(testid)}}"]`;
        if (el.getAttribute('data-testid')) candidate = `[data-testid="${{attrEscape(testid)}}"]`;
        if (el.getAttribute('data-test')) candidate = `[data-test="${{attrEscape(testid)}}"]`;
        const validated = validateSelectorHint(el, candidate);
        if (validated) return validated;
      }}

      const id = el.getAttribute('id');
      if (id) {{
        const validated = validateSelectorHint(el, `#${{cssEscape(id)}}`);
        if (validated) return validated;
      }}

      const aria = el.getAttribute('aria-label');
      if (aria) {{
        const validated = validateSelectorHint(
          el,
          `${{tag}}[aria-label="${{attrEscape(aria)}}"]`
        );
        if (validated) return validated;
      }}

      const name = el.getAttribute('name');
      if (name) {{
        const validated = validateSelectorHint(el, `${{tag}}[name="${{attrEscape(name)}}"]`);
        if (validated) return validated;
      }}

      const placeholder = el.getAttribute('placeholder');
      if (placeholder) {{
        const validated = validateSelectorHint(
          el,
          `${{tag}}[placeholder="${{attrEscape(placeholder)}}"]`
        );
        if (validated) return validated;
      }}

      const path = [];
      let node = el;
      let depth = 0;

      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 4) {{
        const nodeTag = node.tagName.toLowerCase();
        let index = 1;
        let prev = node.previousElementSibling;
        while (prev) {{
          if (prev.tagName.toLowerCase() === nodeTag) index += 1;
          prev = prev.previousElementSibling;
        }}
        path.unshift(`${{nodeTag}}:nth-of-type(${{index}})`);
        node = node.parentElement;
        depth += 1;
      }}

      return validateSelectorHint(el, path.join(' > '));
    }};

    const isActionable = (el, rect) => {{
      if (!el || !rect) return false;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
      if (el.hasAttribute('inert')) return false;
      const style = window.getComputedStyle(el);
      if (!style || style.pointerEvents === 'none') return false;

      const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      const hit = document.elementFromPoint(x, y);
      return !hit || hit === el || el.contains(hit) || hit.contains(el);
    }};

    const elementText = (el) => {{
      const tag = el.tagName.toLowerCase();
      if (tag === 'input' || tag === 'textarea') {{
        return normalize(el.value || el.getAttribute('value') || '');
      }}
      return normalize(el.innerText || el.textContent || '');
    }};

    const accessibleName = (el) => {{
      return normalize(
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        el.getAttribute('placeholder') ||
        el.getAttribute('alt') ||
        el.getAttribute('name') ||
        ''
      );
    }};

    const semanticContext = (el) => {{
      const parts = [];
      let node = el;
      let depth = 0;
      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 5) {{
        parts.push(
          node.tagName || '',
          node.getAttribute('id') || '',
          node.getAttribute('class') || '',
          node.getAttribute('role') || '',
          node.getAttribute('aria-label') || '',
          node.getAttribute('data-testid') || '',
          node.getAttribute('data-date') || '',
          node.getAttribute('data-day') || ''
        );
        node = node.parentElement;
        depth += 1;
      }}
      return normalize(parts.join(' '), 700).toLowerCase();
    }};

    const classifyRegion = (el, searchable) => {{
      const semantic = semanticContext(el);
      const searchableText = String(searchable || '').toLowerCase();
      const inPageChrome = Boolean(el.closest && el.closest('header,nav,[class*="header" i]'));
      for (const profile of activeControlSemantics) {{
        const matchesContext = matchesProfilePattern(
          `${{semantic}} ${{searchableText}}`,
          profile.context_patterns
        );
        if (matchesContext && !inPageChrome) return String(profile.region || 'main');
        if (inPageChrome && matchesProfilePattern(searchableText, profile.global_search_patterns)) {{
          return 'global_search';
        }}
      }}
      if (el.closest && el.closest('aside,[role="complementary"]')) return 'sidebar';
      const own = normalize([
        el.tagName || '', el.getAttribute('id') || '', el.getAttribute('class') || '',
        el.getAttribute('role') || '', el.getAttribute('aria-label') || '',
        el.getAttribute('data-testid') || ''
      ].join(' '), 360).toLowerCase();
      const context = `${{own}} ${{String(searchable || '').toLowerCase()}}`;
      if (/(sidebar|side-bar|aside|right-rail|right-panel|complementary)/.test(context)) return 'sidebar';
      if (/(hot[-_ ]?(search|list|rank|topic)|hotlist|hotrank|toplist|trending)/.test(context) || /\u70ed\u641c|\u70ed\u699c/.test(context)) return 'hot_search';
      if (/(account|profile|user-card|creator-card)/.test(context)) return 'account';
      if (/(shop|store|seller)/.test(context) || /\u5e97\u94fa|\u5356\u5bb6/.test(context)) return 'shop';
      if (/(chat|message|wangwang|aliim|contact-seller)/.test(context) || /\u65fa\u65fa|\u8054\u7cfb\u5356\u5bb6/.test(context)) return 'chat';
      return 'main';
    }};

    const classifyControlKind = (el, text, name) => {{
      const role = String(el.getAttribute('role') || '').toLowerCase();
      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const own = normalize([
        el.getAttribute('id') || '', el.getAttribute('class') || '', role,
        el.getAttribute('aria-label') || '', el.getAttribute('data-testid') || '',
        el.getAttribute('data-date') || '', el.getAttribute('data-day') || '',
        el.getAttribute('name') || '', el.getAttribute('placeholder') || '',
        text || '', name || ''
      ].join(' '), 520).toLowerCase();
      const ancestor = semanticContext(el);
      for (const profile of activeControlSemantics) {{
        if (!matchesProfilePattern(`${{ancestor}} ${{own}}`, profile.context_patterns)) continue;
        for (const candidate of profile.kinds || []) {{
          if (!matchesProfilePattern(own, candidate.patterns)) continue;
          const shapeRestrictions = [candidate.tags, candidate.roles, candidate.types]
            .some((values) => Array.isArray(values) && values.length);
          const shapeMatches = !shapeRestrictions ||
            (candidate.tags || []).includes(tag) ||
            (candidate.roles || []).includes(role) ||
            (candidate.types || []).includes(type);
          if (shapeMatches) return String(candidate.kind || '');
        }}
      }}
      if (
        ['input', 'textarea', 'select'].includes(tag) ||
        el.isContentEditable ||
        ['textbox', 'searchbox', 'combobox'].includes(role) ||
        ['text', 'search', 'email', 'number', 'password'].includes(type)
      ) return '';
      const calendarAncestor = /(calendar|date[-_ ]?picker|datepicker)/.test(ancestor) || /\u65e5\u5386|\u9009\u62e9\u65e5\u671f/.test(ancestor);
      const directDateEvidence = role === 'gridcell' || el.hasAttribute('data-date') ||
        el.hasAttribute('data-day') || /(date-cell|day-cell|calendar-day)/.test(own);
      if (directDateEvidence && calendarAncestor && /[0-9]/.test(own)) {{
        return 'calendar_date';
      }}
      const sortAncestor = /(sort|order|sort-list|sort-tabs)/.test(ancestor) || /\u6392\u5e8f/.test(ancestor);
      if ((role === 'tab' || /(sort-item|sort-option|sort-tab)/.test(own)) && sortAncestor) return 'sort_tab';
      const ratingAncestor = /(rating|score|star|rating-filter)/.test(ancestor) || /\u8bc4\u5206|\u661f\u7ea7/.test(ancestor);
      if ((role === 'option' || /(rating-item|rating-option|star-item)/.test(own)) && ratingAncestor) return 'rating_filter';
      if (role === 'tab') return 'tab';
      if (role === 'option') return 'option';
      return '';
    }};

    const queryAliases = (value) => {{
      const raw = String(value || '').trim().toLowerCase();
      if (!raw) return [];

      const aliases = new Set([raw]);
      const add = (items) => items.forEach((item) => aliases.add(item));

      if (['search', 'find', 'query', 'keyword', 'keywords',
        '搜索', '搜尋', '查询', '查找', '关键词', '关键字', '搜寻', '検索']
        .some((term) => raw.includes(term))) {{
        add(['search', 'find', 'query', 'keyword', 'keywords', 'searchbox', 'search-box',
          'search_input', 'search-input',
          '搜索', '搜', '搜尋', '查询', '查找', '关键词', '关键字', '搜寻', '検索']);
      }}

      if (['input', 'textbox', 'text box', 'field', '输入', '輸入', '输入框', '文字框']
        .some((term) => raw.includes(term))) {{
        add(['input', 'textbox', 'text box', 'field', 'textarea', 'keyword',
          'search', 'query', '输入', '輸入', '搜索', '搜尋', '关键词', '关键字']);
      }}

      if (['next', 'pagination', 'page', '下一页', '下一頁', '下页', '下頁', '更多']
        .some((term) => raw.includes(term))) {{
        add(['next', 'pagination', 'page', '下一页', '下一頁', '下页', '下頁',
          '更多', '加载更多', '載入更多', 'load more']);
      }}

      if (['login', 'sign in', 'signin', '登录', '登入', '登陆']
        .some((term) => raw.includes(term))) {{
        add(['login', 'sign in', 'signin', 'log in', '登录', '登入', '登陆']);
      }}

      return Array.from(aliases).filter(Boolean);
    }};

    const queryTerms = queryAliases(query);
    const exactQuery = String(query || '').trim().toLowerCase();

    const classifyActionLikelihood = (el, searchable, kind = '') => {{
      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const role = roleFromTag(el);
      const text = String(searchable || '').toLowerCase();

      if (kind === 'calendar_date') return 'date';
      if (kind === 'sort_tab') return 'sort';
      if (kind === 'rating_filter') return 'rating';
      if (['hotel_checkin', 'hotel_checkout'].includes(kind)) return 'date';
      if (kind === 'hotel_destination') return 'input';
      if (kind === 'hotel_search_submit') return 'search';
      if (kind === 'hotel_filter') return 'filter';

      if (
        type === 'search' ||
        role === 'searchbox' ||
        /\b(search|query|keyword|kw|wd)\b/i.test(text) ||
        /(搜索|搜尋|查询|查找|关键词|关键字|検索)/.test(text)
      ) {{
        return 'search';
      }}

      if (['input', 'textarea'].includes(tag) || role === 'textbox') return 'input';
      if (/\b(next|pagination|page)\b/i.test(text) || /(下一页|下一頁|下页|下頁|更多|加载更多|載入更多)/.test(text)) return 'pagination';
      if (/\b(login|sign in|signin|log in)\b/i.test(text) || /(登录|登入|登陆)/.test(text)) return 'login';
      if (/\b(filter|sort|category)\b/i.test(text) || /(筛选|篩選|排序|分类|分類)/.test(text)) return 'filter';
      if (/\b(cart|basket|buy|checkout)\b/i.test(text) || /(购物车|購物車|加入购物车|加入購物車|购买|購買)/.test(text)) return 'commerce';

      if (tag === 'button') return 'button';
      if (tag === 'a') return 'link';
      return role || tag;
    }};

    const queryMatches = (searchable) => {{
      if (!queryTerms.length) return true;
      const haystack = String(searchable || '').toLowerCase();
      return queryTerms.some((term) => haystack.includes(term));
    }};

    const exactQueryMatches = (searchable) => {{
      if (!exactQuery) return true;
      return String(searchable || '').toLowerCase().includes(exactQuery);
    }};

    const scoreElement = (el, rect, text, name, actionLikelihood) => {{
      let score = 0;
      const tag = el.tagName.toLowerCase();
      const role = roleFromTag(el);

      if (el.getAttribute('data-testid')) score += 40;
      if (el.getAttribute('data-test') || el.getAttribute('data-cy')) score += 30;
      if (el.getAttribute('aria-label')) score += 25;
      if (tag === 'button') score += 25;
      if (tag === 'input' || tag === 'select' || tag === 'textarea') score += 22;
      if (el.getAttribute('contenteditable') === 'true') score += 18;
      if (tag === 'a') score += 18;
      if (role) score += 15;
      if (actionLikelihood === 'search') score += 35;
      if (['date', 'sort', 'rating'].includes(actionLikelihood)) score += 30;
      if (query && queryMatches(`${{actionLikelihood}} ${{tag}} ${{role}}`)) score += 20;
      if (text) score += Math.min(20, text.length / 4);
      if (name) score += Math.min(15, name.length / 5);

      if (rect.top >= 0 && rect.top <= window.innerHeight) score += 15;
      if (rect.left >= 0 && rect.left <= window.innerWidth) score += 5;

      if (el.disabled || el.getAttribute('aria-disabled') === 'true') score -= 50;

      return score;
    }};

    const all = Array.from(document.querySelectorAll(selectors.join(',')));
    const seen = new Set();
    const candidates = [];
    const widenedCandidates = [];

    for (const el of all) {{
      if (!el || seen.has(el)) continue;
      seen.add(el);

      const rect = el.getBoundingClientRect();
      if (!isVisible(el, rect)) continue;

      const tag = el.tagName.toLowerCase();
      const role = roleFromTag(el);
      const text = elementText(el);
      const name = accessibleName(el);
      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy') ||
        '';
      const type = el.getAttribute('type') || '';
      const id = el.getAttribute('id') || '';
      const nameAttr = el.getAttribute('name') || '';
      const placeholder = el.getAttribute('placeholder') || '';
      const className = el.getAttribute('class') || '';
      const title = el.getAttribute('title') || '';

      const searchable = `${{tag}} ${{role}} ${{type}} ${{id}} ${{nameAttr}} ${{className}} ${{text}} ${{name}} ${{placeholder}} ${{title}} ${{testid}}`.toLowerCase();
      const exactMatch = exactQueryMatches(searchable);
      if (!exactMatch && !queryMatches(searchable)) continue;

      const kind = classifyControlKind(el, text, name);
      const actionLikelihood = classifyActionLikelihood(el, searchable, kind);
      const selectorHint = buildSelectorHint(el);
      const actionable = isActionable(el, rect);
      const matchCount = selectorHint
        ? document.querySelectorAll(selectorHint).length
        : 0;
      const enabled = !(
        el.disabled ||
        el.getAttribute('aria-disabled') === 'true' ||
        el.hasAttribute('inert')
      );
      const clickable = Boolean(
        actionable && enabled && selectorHint && matchCount === 1
      );

      const candidate = {{
        tag,
        role,
        action_likelihood: actionLikelihood,
        region: classifyRegion(el, searchable),
        kind: kind || actionLikelihood,
        text,
        accessible_name: name,
        aria_label: normalize(el.getAttribute('aria-label') || ''),
        testid: normalize(testid),
        input_type: normalize(type),
        name: normalize(nameAttr),
        placeholder: normalize(placeholder),
        href: normalize(el.getAttribute('href') || '', 180),
        disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
        selected: Boolean(
          el.getAttribute('aria-selected') === 'true' ||
          el.getAttribute('aria-checked') === 'true' ||
          /(^|\\s)(active|selected|checked)(\\s|$)/i.test(className)
        ),
        visible: true,
        enabled,
        actionable,
        clickable,
        match_count: matchCount,
        generation_id: generationId,
        bbox: [
          Math.round(rect.x),
          Math.round(rect.y),
          Math.round(rect.width),
          Math.round(rect.height)
        ],
        selector_hint: clickable ? selectorHint : '',
        selector_hint_validated: clickable,
        score: scoreElement(el, rect, text, name, actionLikelihood)
      }};
      (exactMatch ? candidates : widenedCandidates).push(candidate);
    }}

    const selectedCandidates = candidates.length ? candidates : widenedCandidates;
    selectedCandidates.sort((a, b) => {{
      if (b.score !== a.score) return b.score - a.score;
      if (a.bbox[1] !== b.bbox[1]) return a.bbox[1] - b.bbox[1];
      return a.bbox[0] - b.bbox[0];
    }});

    const elements = selectedCandidates.slice(0, maxItems).map((item, index) => {{
      const copy = {{ ...item }};
      copy.id = `e${{index + 1}}`;
      delete copy.score;
      return copy;
    }});

    return {{
      ok: true,
      url: window.location.href,
      title: document.title,
      viewport: {{
        width: window.innerWidth,
        height: window.innerHeight,
        scroll_x: window.scrollX,
        scroll_y: window.scrollY
      }},
      query,
      viewport_only: viewportOnly,
      generation_id: generationId,
      total_candidates: selectedCandidates.length,
      query_widened: Boolean(exactQuery && !candidates.length && widenedCandidates.length),
      returned: elements.length,
      elements,
      error: null
    }};
  }}, params);
}}
""".strip()


def build_card_probe_js(
    *,
    max_cards: int = 20,
    viewport_only: bool = True,
    include_buttons: bool = True,
    query: str = "",
    site_profiles: Optional[List[Dict[str, Any]]] = None,
    selector_cache_records: Optional[List[Dict[str, Any]]] = None,
    generation_id: str = "g0",
) -> str:
    """Build browser_run_code_unsafe JavaScript for compact repeated-card probing."""

    params: Dict[str, Any] = {
        "max_cards": _clamp_int(max_cards, default=20, minimum=1, maximum=50),
        "viewport_only": bool(viewport_only),
        "include_buttons": bool(include_buttons),
        "query": str(query or "").strip().lower(),
        "site_profiles": site_profiles or [],
        "selector_cache_records": selector_cache_records or [],
        "generation_id": str(generation_id or "g0"),
    }
    params_json = json.dumps(params, ensure_ascii=False)

    template = r"""
async (page) => {
  const params = __PARAMS__;

  return await page.evaluate((params) => {
    const maxCards = Math.max(1, Math.min(Number(params.max_cards || 20), 50));
    const viewportOnly = params.viewport_only !== false;
    const includeButtons = params.include_buttons !== false;
    const query = String(params.query || '').trim().toLowerCase();
    const generationId = String(params.generation_id || 'g0');
    const host = String(window.location.hostname || '').toLowerCase();
    const path = String(window.location.pathname || '/').toLowerCase();

    const unique = (items, limit = 80) => {
      const result = [];
      const seen = new Set();

      for (const item of items || []) {
        const value = String(item || '').trim();
        if (!value || seen.has(value)) continue;
        seen.add(value);
        result.push(value);
        if (result.length >= limit) break;
      }

      return result;
    };

    const routeMatches = (patterns) => {
      if (!Array.isArray(patterns) || patterns.length === 0) return true;

      return patterns.some((pattern) => {
        try {
          return new RegExp(String(pattern), 'i').test(path);
        } catch(_) {
          return false;
        }
      });
    };
    
    const domainMatches = (domains) => {
      if (!Array.isArray(domains) || domains.length === 0) return false;

      return domains.some((domain) => {
        const value = String(domain || '').toLowerCase();
        return host === value || host.endsWith(`.${value}`);
      });
    };

    const activeProfiles = Array.isArray(params.site_profiles)
      ? params.site_profiles.filter((profile) => {
          return domainMatches(profile.domains) && routeMatches(profile.route_patterns);
        })
      : [];

    const normalizeRouteSignature = (value) => {
      let route = String(value || '/').toLowerCase();
      route = route.replace(/\d+/g, '*').replace(/\/+/g, '/')
      if (route !== '/' && route.endsWith('/')) route = route.slice(0, -1);
      return route || '/';
    };

    const currentRouteSignature = normalizeRouteSignature(path);

    const activeCacheRecords = Array.isArray(params.selector_cache_records)
      ? params.selector_cache_records.filter((record) => {
          const kind = String(record.kind || 'card_probe').toLowerCase();
          if (kind !== 'card_probe') return false;

          const domain = String(record.domain || '').toLowerCase();
          if (!domain) return false;
          const domainOk = host === domain || host.endsWith(`.${domain}`);
          if (!domainOk) return false;

          const route = String(record.route_signature || '').toLowerCase();
          return !route || route === currentRouteSignature;
        })
      : [];
    
    const cacheSelectors = (name) => {
      const values = [];

      for (const record of activeCacheRecords) {
        const selectors = record.selectors || {};
        if (Array.isArray(selectors[name])) {
          values.push(...selectors[name]);
        }
      }

      return unique(values);
    };

    const siteProfileSelectors = (name) => {
      const values = [];

      for (const profile of activeProfiles) {
        if (Array.isArray(profile[name])) {
          values.push(...profile[name]);
        }
      }

      return unique(values);
    };

    const profileSelectors = (name) => {
      return unique([
        ...cacheSelectors(name),
        ...siteProfileSelectors(name)
      ]);
    };

    const mergeSelectors = (...groups) => {
      const values = [];
      for (const group of groups) {
        values.push(...(Array.isArray(group) ? group : []));
      }
      return unique(values);
    };

    const normalize = (value, limit = 180) => {
      return String(value || '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, limit);
    };

    const attrEscape = (value) => {
      return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    };

    const cssEscape = (value) => {
      const raw = String(value || '');
      if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(raw);
      }
      return raw.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    };

    const isVisible = (el, rect) => {
      if (!el || !rect) return false;
      if (rect.width < 20 || rect.height < 20) return false;

      const style = window.getComputedStyle(el);
      if (!style) return false;
      if (style.display === 'none') return false;
      if (style.visibility === 'hidden') return false;
      if (Number(style.opacity) === 0) return false;

      if (viewportOnly) {
        if (rect.bottom < 0) return false;
        if (rect.right < 0) return false;
        if (rect.top > window.innerHeight) return false;
        if (rect.left > window.innerWidth) return false;
      }

      return true;
    };

    const isElementVisible = (el, rect) => {
      if (!el || !rect || rect.width < 2 || rect.height < 2) return false;
      const style = window.getComputedStyle(el);
      if (!style) return false;
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      if (Number(style.opacity) === 0) return false;
      if (rect.bottom < 0 || rect.right < 0) return false;
      if (rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
      return true;
    };

    const isEnabled = (el) => {
      return Boolean(
        el &&
        !el.disabled &&
        el.getAttribute('aria-disabled') !== 'true' &&
        !el.hasAttribute('inert')
      );
    };

    const isActionable = (el, rect) => {
      if (!isElementVisible(el, rect) || !isEnabled(el)) return false;
      const style = window.getComputedStyle(el);
      if (!style || style.pointerEvents === 'none') return false;
      const tag = el.tagName.toLowerCase();
      const role = String(el.getAttribute('role') || '').toLowerCase();
      const hasActionSemantics = (
        ['a', 'button', 'input', 'select', 'textarea'].includes(tag) ||
        ['button', 'link', 'checkbox', 'radio', 'option', 'menuitem'].includes(role) ||
        el.hasAttribute('onclick') ||
        el.hasAttribute('tabindex')
      );
      if (!hasActionSemantics) return false;
      const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      const hit = document.elementFromPoint(x, y);
      return !hit || hit === el || el.contains(hit) || hit.contains(el);
    };

    const selectorMetadata = (selector) => {
      if (!selector) {
        return { match_count: 0, visible: false, enabled: false };
      }
      try {
        const matches = Array.from(document.querySelectorAll(selector));
        const first = matches[0] || null;
        const rect = first ? first.getBoundingClientRect() : null;
        return {
          match_count: matches.length,
          visible: Boolean(first && isElementVisible(first, rect)),
          enabled: Boolean(first && isEnabled(first))
        };
      } catch (_) {
        return { match_count: 0, visible: false, enabled: false };
      }
    };

    const selectorDescriptor = (selector) => {
      const metadata = selectorMetadata(selector);
      return {
        selector_hint: selector || '',
        match_count: metadata.match_count,
        visible: metadata.visible,
        enabled: metadata.enabled,
        generation_id: generationId
      };
    };

    const validateSelectorHint = (el, selector) => {
      if (!el || !selector) return '';
      try {
        const matches = Array.from(document.querySelectorAll(selector));
        return matches.length === 1 && matches[0] === el ? selector : '';
      } catch (_) {
        return '';
      }
    };

    const buildSelectorHint = (el) => {
      if (!el || !el.tagName) return '';

      const tag = el.tagName.toLowerCase();

      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy');
      if (testid) {
        let candidate = `[data-cy="${attrEscape(testid)}"]`;
        if (el.getAttribute('data-testid')) candidate = `[data-testid="${attrEscape(testid)}"]`;
        if (el.getAttribute('data-test')) candidate = `[data-test="${attrEscape(testid)}"]`;
        const validated = validateSelectorHint(el, candidate);
        if (validated) return validated;
      }

      const id = el.getAttribute('id');
      if (id) {
        const validated = validateSelectorHint(el, `#${cssEscape(id)}`);
        if (validated) return validated;
      }

      const stableClasses = normalize(el.getAttribute('class') || '', 160)
        .split(' ')
        .filter(Boolean)
        .filter((item) => !/^(active|selected|disabled|hover|focus|show|hide|open|closed)$/i.test(item))
        .slice(0, 3);

      const simple = stableClasses.length
        ? `${tag}${stableClasses.map((item) => `.${cssEscape(item)}`).join('')}`
        : tag;

      const validatedSimple = validateSelectorHint(el, simple);
      if (validatedSimple) return validatedSimple;

      const path = [];
      let node = el;
      let depth = 0;

      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 8) {
        const nodeTag = node.tagName.toLowerCase();

        const nodeTestid =
          node.getAttribute('data-testid') ||
          node.getAttribute('data-test') ||
          node.getAttribute('data-cy');

        if (nodeTestid) {
          let candidate = `[data-cy="${attrEscape(nodeTestid)}"]`;
          if (node.getAttribute('data-testid')) {
            candidate = `[data-testid="${attrEscape(nodeTestid)}"]`;
          } else if (node.getAttribute('data-test')) {
            candidate = `[data-test="${attrEscape(nodeTestid)}"]`;
          }
          if (validateSelectorHint(node, candidate)) {
            path.unshift(candidate);
            break;
          }
        }

        const nodeId = node.getAttribute('id');
        const idCandidate = nodeId ? `#${cssEscape(nodeId)}` : '';
        if (idCandidate && validateSelectorHint(node, idCandidate)) {
          path.unshift(idCandidate);
          break;
        }

        let index = 1;
        let prev = node.previousElementSibling;
        while (prev) {
          if (prev.tagName.toLowerCase() === nodeTag) index += 1;
          prev = prev.previousElementSibling;
        }

        const cls = normalize(node.getAttribute('class') || '', 100)
          .split(' ')
          .filter(Boolean)
          .filter((item) => !/^(active|selected|disabled|hover|focus|show|hide|open|closed)$/i.test(item))
          .slice(0, 2)
          .map((item) => `.${cssEscape(item)}`)
          .join('');

        path.unshift(`${nodeTag}${cls}:nth-of-type(${index})`);

        const parentNode = node.parentElement;
        if (
          parentNode &&
          ['ol', 'ul', 'main', 'section', 'body'].includes(parentNode.tagName.toLowerCase()) &&
          depth >= 3
        ) {
          const parentTag = parentNode.tagName.toLowerCase();
          let parentIndex = 1;
          let parentPrev = parentNode.previousElementSibling;
          while (parentPrev) {
            if (parentPrev.tagName.toLowerCase() === parentTag) parentIndex += 1;
            parentPrev = parentPrev.previousElementSibling;
          }
          path.unshift(`${parentTag}:nth-of-type(${parentIndex})`);
          break;
        }

        node = parentNode;
        depth += 1;
      }

      return validateSelectorHint(el, path.join(' > '));
    };

    const directText = (el) => {
      const clone = el.cloneNode(true);
      clone.querySelectorAll('script, style, noscript, svg').forEach((node) => node.remove());
      return normalize(clone.innerText || clone.textContent || '', 600);
    };

    const findFirst = (root, selectors, predicate = null) => {
      for (const selector of selectors) {
        try {
          const nodes = Array.from(root.querySelectorAll(selector));
          for (const found of nodes) {
            if (found && (!predicate || predicate(found))) return found;
          }
        } catch (_) {
          // Ignore invalid browser-specific selector handling.
        }
      }
      return null;
    };

    const textOf = (el, limit = 180) => {
      if (!el) return '';
      return normalize(
        el.getAttribute('title') ||
        el.getAttribute('aria-label') ||
        el.getAttribute('alt') ||
        el.innerText ||
        el.textContent ||
        '',
        limit
      );
    };

    const elementHref = (el) => {
      if (!el) return '';
      const target = el.matches && el.matches('a[href]') ? el : el.querySelector && el.querySelector('a[href]');
      if (!target) return '';
      return normalize(target.href || target.getAttribute('href') || '', 260).toLowerCase();
    };

    const elementDescriptor = (el) => {
      if (!el) return '';
      return normalize([
        el.tagName || '',
        el.getAttribute('id') || '',
        el.getAttribute('class') || '',
        el.getAttribute('role') || '',
        el.getAttribute('aria-label') || '',
        el.getAttribute('title') || '',
        el.getAttribute('data-testid') || '',
        el.getAttribute('data-test') || ''
      ].join(' '), 360).toLowerCase();
    };

    const isArticleHref = (href) => {
      const value = String(href || '').toLowerCase();
      return Boolean(
        value.includes('/article/details/') ||
        value.includes('/articles/') ||
        value.includes('/post/') ||
        value.includes('/posts/') ||
        value.includes('/blog/') ||
        (value.includes('blog.csdn.net') && value.includes('/article/'))
      );
    };

    const isAuthorProfileHref = (href) => {
      const value = String(href || '').toLowerCase();
      if (!value) return false;
      if (isArticleHref(value)) return false;
      return Boolean(
        value.includes('/user/') ||
        value.includes('/users/') ||
        value.includes('/profile') ||
        value.includes('/people/') ||
        value.includes('/u/') ||
        value.includes('passport.') ||
        value.includes('mp.csdn.net') ||
        /https?:\/\/blog\.csdn\.net\/[^/?#]+\/?(?:[?#].*)?$/.test(value)
      );
    };

    const isAuthorProfileElement = (el) => {
      if (!el) return false;
      const desc = elementDescriptor(el);
      const href = elementHref(el);
      if (isAuthorProfileHref(href)) return true;
      if (/\\b(author|byline|user|profile|avatar|nickname|nick|name-text|btm-rt)\\b/i.test(desc)) {
        if (!isArticleHref(href)) return true;
      }
      return false;
    };

    const isArticleLinkElement = (el) => {
      if (!el) return false;
      const desc = elementDescriptor(el);
      const href = elementHref(el);
      if (isAuthorProfileElement(el)) return false;
      return Boolean(
        isArticleHref(href) ||
        desc.includes('block-title') ||
        desc.includes('so-item-report') ||
        desc.includes('result-title') ||
        desc.includes('article-title') ||
        desc.includes('post-title') ||
        desc.includes('headline') ||
        desc.includes('subject') ||
        (el.closest && el.closest('h1,h2,h3,h4,[role="heading"]'))
      );
    };

    const titleCandidateOk = (el) => {
      return Boolean(el && !isAuthorProfileElement(el) && textOf(el, 220).length >= 2);
    };

    const articleTitleSelectors = [
      'a.block-title.so-item-report[href]',
      'h1 a[href]', 'h2 a[href]', 'h3 a[href]', 'h4 a[href]',
      '[role="heading"] a[href]',
      'a[href*="/article/details/"]',
      'a[href*="/article/"]',
      'a[href*="blog.csdn.net"][href*="/article/"]',
      '[class*="title" i] a[href]',
      '[class*="headline" i] a[href]',
      '[class*="subject" i] a[href]',
      '[data-testid*="title" i] a[href]',
      '[data-test*="title" i] a[href]'
    ];

    const extractTitle = (root) => {
      const articleLink = findFirst(root, articleTitleSelectors, isArticleLinkElement);
      const titleEl = articleLink || findFirst(root, mergeSelectors(
        profileSelectors('title_selectors'),
        [
          'h1', 'h2', 'h3', 'h4',
          '[role="heading"]',
          '[class*="title" i]',
          '[class*="headline" i]',
          '[class*="subject" i]',
          '[class*="article" i][class*="name" i]',
          '[data-testid*="title" i]',
          '[data-testid*="headline" i]',
          '[data-test*="title" i]',
          '[data-test*="headline" i]',
          'a[title]',
          'img[alt]',
          '[class*="name" i]',
          'a'
        ]
      ), titleCandidateOk);

      let title = textOf(titleEl, 220);
      if (!title) {
        const link = findFirst(root, ['a'], titleCandidateOk);
        title = textOf(link, 220);
      }

      return {
        value: title,
        selector_hint: titleEl ? buildSelectorHint(titleEl) : ''
      };
    };

    const semanticRules = activeProfiles
      .map((profile) => profile.semantic_rules || {})
      .filter((rules) => rules && typeof rules === 'object');

    const matchesProfilePattern = (value, patterns) => {
      return Array.isArray(patterns) && patterns.some((pattern) => {
        try {
          return new RegExp(String(pattern), 'i').test(String(value || ''));
        } catch (_) {
          return false;
        }
      });
    };

    const targetDomainMatches = (targetHost, domains) => {
      return Array.isArray(domains) && domains.some((domain) => {
        const value = String(domain || '').toLowerCase();
        return targetHost === value || targetHost.endsWith(`.${value}`);
      });
    };

    const siteDetailLink = (href) => {
      for (const rules of semanticRules) {
        const detail = rules.detail_link;
        if (!detail || typeof detail !== 'object') continue;
        try {
          const parsed = new URL(String(href || ''), window.location.href);
          const targetHost = String(parsed.hostname || '').toLowerCase();
          if (!targetDomainMatches(targetHost, detail.domains)) continue;
          const identifier = (detail.query_id_params || [])
            .map((key) => parsed.searchParams.get(String(key)) || '')
            .find(Boolean) || '';
          const pathMatches = matchesProfilePattern(parsed.pathname, detail.path_patterns);
          if (!identifier && !pathMatches) continue;
          const prefix = String(detail.key_prefix || 'detail').toLowerCase();
          return {
            key: identifier ? `${prefix}:${identifier}` : `${targetHost}${parsed.pathname}`.toLowerCase(),
            kind: String(detail.kind || 'result').toLowerCase(),
            deduplicate: rules.deduplicate_detail_links === true
          };
        } catch (_) {
          continue;
        }
      }
      return null;
    };

    const sitePrimaryLinkRules = () => {
      return semanticRules
        .map((rules) => rules.primary_link)
        .filter((rules) => rules && typeof rules === 'object');
    };

    const isProfilePreferredPrimaryHref = (href) => {
      const value = String(href || '');
      return sitePrimaryLinkRules().some((rules) => {
        if (!matchesProfilePattern(value, rules.preferred_patterns)) return false;
        if (!Array.isArray(rules.required_query_params) || !rules.required_query_params.length) return true;
        try {
          const parsed = new URL(value, window.location.href);
          return rules.required_query_params.some((key) => parsed.searchParams.has(String(key)));
        } catch (_) {
          return false;
        }
      });
    };

    const isProfileExcludedPrimaryHref = (href) => {
      const rules = sitePrimaryLinkRules();
      if (!rules.length) return false;
      const value = String(href || '');
      if (!value) return true;
      return rules.some((rule) => matchesProfilePattern(value, rule.excluded_patterns));
    };

    const linkResult = (link) => {
      if (!link) return { text: '', href: '', selector_hint: '' };
      return {
        text: textOf(link, 180),
        href: normalize(link.href || link.getAttribute('href') || '', 260),
        selector_hint: buildSelectorHint(link)
      };
    };

    const linkCandidates = (root) => {
      const candidates = [];
      const seen = new Set();
      const add = (candidate) => {
        if (!candidate || seen.has(candidate)) return;
        seen.add(candidate);
        candidates.push(candidate);
      };
      if (root.matches && root.matches('a[href]')) add(root);
      if (root.closest) add(root.closest('a[href]'));
      for (const candidate of Array.from(root.querySelectorAll('a[href]'))) add(candidate);
      return candidates;
    };

    const extractPrimaryLink = (root) => {
      const candidates = linkCandidates(root);
      const detailLink = candidates.find((candidate) => {
        return Boolean(siteDetailLink(candidate.href || candidate.getAttribute('href') || ''));
      });
      if (detailLink) return linkResult(detailLink);

      if (sitePrimaryLinkRules().length) {
        const preferredLink = candidates.find((candidate) => {
          return isProfilePreferredPrimaryHref(candidate.href || candidate.getAttribute('href') || '');
        });
        if (preferredLink) return linkResult(preferredLink);

        const allowedLink = candidates.find((candidate) => {
          const href = candidate.href || candidate.getAttribute('href') || '';
          return !isProfileExcludedPrimaryHref(href) && !isAuthorProfileElement(candidate);
        });
        return linkResult(allowedLink);
      }

      const articleLink = findFirst(root, articleTitleSelectors, isArticleLinkElement);
      const link = articleLink || findFirst(root, mergeSelectors(
        profileSelectors('primary_link_selectors'),
        [
          'a[href][title]',
          'h1 a[href]', 'h2 a[href]', 'h3 a[href]', 'h4 a[href]',
          'a[href*="/article/details/"]',
          'a[href*="/article/"]',
          'a[href]'
        ]
      ), (candidate) => !isAuthorProfileElement(candidate));

      return linkResult(link);
    };

    const cardSemanticContext = (el) => {
      const parts = [];
      let node = el;
      let depth = 0;
      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 5) {
        parts.push(
          node.tagName || '',
          node.getAttribute('id') || '',
          node.getAttribute('class') || '',
          node.getAttribute('role') || '',
          node.getAttribute('aria-label') || '',
          node.getAttribute('data-testid') || '',
          node.getAttribute('data-ad') || '',
          node.getAttribute('data-is-ad') || ''
        );
        node = node.parentElement;
        depth += 1;
      }
      return normalize(parts.join(' '), 900).toLowerCase();
    };

    const semanticBadges = (el) => {
      const selectors = [
        '[data-ad]', '[data-is-ad="true"]', '[class*="sponsored" i]',
        '[class*="promoted" i]', '[class*="promotion" i]', '[class*="badge" i]',
        '[class*="label" i]', '[aria-label*="sponsored" i]', '[aria-label*="promoted" i]'
      ];
      const values = [];
      for (const badge of Array.from(el.querySelectorAll(selectors.join(','))).slice(0, 12)) {
        const value = textOf(badge, 80);
        if (value && !values.includes(value)) values.push(value);
      }
      return values;
    };

    const classifyCardSemantics = (el, primaryLink, rootText) => {
      const href = String(primaryLink?.href || '').toLowerCase();
      const own = elementDescriptor(el);
      const context = `${cardSemanticContext(el)} ${own} ${href}`;
      const badges = semanticBadges(el);
      const badgeText = badges.join(' ').toLowerCase();
      const normalizedRootText = String(rootText || '').toLowerCase();
      const isHotSearch = /(hot[-_ ]?(search|list|rank|topic)|hotlist|hotrank|toplist|trending)/.test(own) || /热搜|热榜/.test(badgeText);
      const isAccount = /(account|profile|user-card|creator-card|author-card)/.test(own) ||
        isAuthorProfileHref(href);
      const isChat = /(chat|wangwang|aliim|contact-seller)/.test(`${own} ${href}`) ||
        /旺旺|联系卖家/.test(badgeText) ||
        isProfileExcludedPrimaryHref(href) && /(wangwang|aliim|amos)/.test(href);
      const isShop = /(shop|store|seller-card)/.test(`${own} ${href}`) ||
        /店铺|卖家/.test(badgeText) ||
        isProfileExcludedPrimaryHref(href) && /(shop|store|seller)/.test(href);
      const detailLink = siteDetailLink(href);
      const isProduct = detailLink?.kind === 'product' ||
        /\/item(?:\.|\/)|\/product(?:\.|\/)|\/goods(?:\.|\/)/.test(href);
      const isHotel = detailLink?.kind === 'hotel';
      const isSidebar = Boolean(el.closest && el.closest('aside,[role="complementary"]')) ||
        /(sidebar|side-bar|right-rail|right-panel|container-right|main-right)/.test(own);
      const genericPaidLink = /\/(?:paid|premium|sponsored|promotion)(?:[_\-/]|$)/.test(href);
      const genericActivity = /精选活动/.test(badgeText) ||
        /(^|[\s_-])(activity|campaign|event)([\s_-]|$)/.test(own);
      let isAd = Boolean(
        el.matches?.('[data-ad],[data-adid],[data-is-ad="true"],[class*="sponsored" i]') ||
        el.querySelector?.('[data-ad],[data-adid],[data-is-ad="true"],[class*="sponsored" i]') ||
        /(^|[\s_-])(ad|ads|sponsored|promoted|promotion)([\s_-]|$)|广告|推广/.test(`${own} ${badgeText}`) ||
        genericPaidLink
      );

      let kind = 'result';
      if (genericActivity) kind = 'activity';
      else if (genericPaidLink) kind = 'paid_result';
      else if (isHotel) kind = 'hotel';
      else if (isProduct) kind = 'product';
      else if (isChat) kind = 'chat';
      else if (isShop) kind = 'shop';
      else if (isAccount) kind = 'account';
      else if (isHotSearch) kind = 'hot_search';
      else if (isAd) kind = 'promotion';

      let region = 'main_result';
      if (kind === 'activity') region = 'activity';
      else if (isAd) region = 'sponsored_result';
      else if (isHotSearch) region = 'hot_search';
      else if (isSidebar) region = 'sidebar';
      else if (kind === 'chat') region = 'chat';

      // Profile rules are intentionally last and only resolve known ambiguity.
      for (const rules of semanticRules) {
        if (matchesProfilePattern(href, rules.paid_link_patterns)) {
          region = 'sponsored_result';
          kind = 'paid_column';
          isAd = true;
        } else if (matchesProfilePattern(
          `${badgeText} ${normalizedRootText.slice(0, 80)}`,
          rules.activity_text_patterns
        )) {
          region = 'activity';
          kind = 'activity';
          isAd = true;
        } else if (matchesProfilePattern(badgeText, rules.promotion_text_patterns)) {
          region = 'sponsored_result';
          kind = 'promotion';
          isAd = true;
        }
      }

      if (isHotel && !isAd) region = 'main_result';
      return { region, kind, is_ad: isAd, semantic_badges: badges };
    };

    const labeledText = (rootText, labels, limit = 120) => {
      const escaped = labels
        .map((label) => String(label || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .filter(Boolean)
        .join('|');
      if (!escaped) return '';

      const match = String(rootText || '').match(
        new RegExp(`(?:${escaped})\\s*[:：-]?\\s*([^\\n|·•,，;；]{2,${Math.min(limit, 80)}})`, 'i')
      );
      return match ? normalize(match[1], limit) : '';
    };

    const extractAuthor = (rootText, root) => {
      const authorEl = findFirst(root, mergeSelectors(
        profileSelectors('author_selectors'),
        [
          '[rel="author"]',
          '[itemprop="author"]',
          '[class*="author" i]',
          '[class*="byline" i]',
          '[class*="writer" i]',
          '[class*="user" i]',
          '[class*="nick" i]',
          '[class*="avatar" i] + *',
          '[data-testid*="author" i]',
          '[data-testid*="user" i]',
          '[data-test*="author" i]',
          '[data-test*="user" i]',
          'a[href*="/user"]',
          'a[href*="/profile"]',
          'a[href*="/people"]',
          'a[href*="/u/"]'
        ]
      ));

      const fromElement = textOf(authorEl, 120);
      const fromLabel = labeledText(rootText, ['author', 'by', 'writer', 'posted by', '作者', '博主', '发布者', '發布者'], 120);

      return {
        value: fromElement || fromLabel,
        selector_hint: authorEl ? buildSelectorHint(authorEl) : ''
      };
    };

    const extractMetricField = (
      rootText,
      root,
      selectors,
      labels,
      pattern,
      limit = 120,
      requirePattern = false
    ) => {
      const fieldEl = findFirst(root, selectors);
      const elementText = textOf(fieldEl, limit);
      const labeled = labeledText(rootText, labels, limit);
      const searchable = `${elementText} ${labeled} ${rootText}`;
      const match = searchable.match(pattern);
      const value = requirePattern
        ? (match ? normalize(match[0], limit) : '')
        : (elementText || labeled || (match ? normalize(match[0], limit) : ''));
      return {
        value,
        raw_text: value
          ? normalize(requirePattern ? (match?.[0] || '') : (elementText || labeled || match?.[0] || ''), 240)
          : '',
        selector_hint: fieldEl && (!requirePattern || pattern.test(elementText))
          ? buildSelectorHint(fieldEl)
          : ''
      };
    };

    const extractLikes = (rootText, root) => extractMetricField(
      rootText,
      root,
      [
        '[class*="like" i]:not([class*="dislike" i])',
        '[class*="upvote" i]',
        '[class*="vote" i]',
        '[class*="digg" i]',
        '[data-testid*="like" i]',
        '[aria-label*="like" i]',
        '[aria-label*="赞同" i]',
        '[aria-label*="点赞" i]'
      ],
      ['likes', 'like count', 'upvotes', '点赞', '赞同', '获赞'],
      /(?:likes?|upvotes?|点赞|赞同|获赞)\s*[:：]?\s*[\d,.万亿kKmM]+|[\d,.万亿kKmM]+\s*(?:likes?|upvotes?|点赞|赞同)/i,
      120,
      true
    );

    const extractFavorites = (rootText, root) => extractMetricField(
      rootText,
      root,
      [
        '[class*="favorite" i]',
        '[class*="favourite" i]',
        '[class*="bookmark" i]',
        '[class*="collect" i]',
        '[data-testid*="favorite" i]',
        '[data-testid*="bookmark" i]',
        '[aria-label*="收藏" i]'
      ],
      ['favorites', 'favourites', 'bookmarks', '收藏', '收藏数'],
      /(?:favorites?|favourites?|bookmarks?|收藏(?:数)?)\s*[:：]?\s*[\d,.万亿kKmM]+|[\d,.万亿kKmM]+\s*(?:favorites?|favourites?|bookmarks?|收藏)/i,
      120,
      true
    );

    const extractComments = (rootText, root) => extractMetricField(
      rootText,
      root,
      [
        '[class*="comment-count" i]',
        '[class*="comment-num" i]',
        '[class*="reply-count" i]',
        '[data-testid*="comment" i]',
        '[aria-label*="评论" i]',
        '[aria-label*="回复" i]'
      ],
      ['comments', 'comment count', 'replies', '评论', '评论数', '回复数'],
      /(?:comments?|replies|评论(?:数)?|回复数)\s*[:：]?\s*[\d,.万亿kKmM]+|[\d,.万亿kKmM]+\s*(?:comments?|replies|评论|回复)/i,
      120,
      true
    );

    const extractShop = (rootText, root) => extractMetricField(
      rootText,
      root,
      mergeSelectors(
        profileSelectors('shop_selectors'),
        [
          '[class*="shop-name" i]',
          '[class*="store-name" i]',
          '[class*="seller-name" i]',
          '[class*="merchant-name" i]',
          '[data-testid*="shop" i]',
          '[data-testid*="seller" i]'
        ]
      ),
      ['shop', 'store', 'merchant', 'seller', '店铺', '商家', '卖家'],
      /(?:shop|store|merchant|seller|店铺|商家|卖家)\s*[:：-]?\s*[^\n|·•,，;；]{2,80}/i
    );

    const extractDuration = (rootText, root) => extractMetricField(
      rootText,
      root,
      [
        'time[datetime]',
        '[class*="duration" i]',
        '[class*="video-length" i]',
        '[data-testid*="duration" i]'
      ],
      ['duration', 'video length', '时长', '视频长度'],
      /(?:\d{1,2}:)?\d{1,2}:\d{2}|(?:duration|时长)\s*[:：]?\s*\d+\s*(?:h|m|s|小时|分钟|秒)/i,
      120,
      true
    );

    const extractTemperature = (rootText, root, kind) => {
      const high = kind === 'high';
      return extractMetricField(
        rootText,
        root,
        high
          ? ['[class*="temp-high" i]', '[class*="high-temp" i]', '[data-testid*="high-temp" i]']
          : ['[class*="temp-low" i]', '[class*="low-temp" i]', '[data-testid*="low-temp" i]'],
        high
          ? ['high temperature', 'maximum temperature', '最高温', '最高气温', '高温']
          : ['low temperature', 'minimum temperature', '最低温', '最低气温', '低温'],
        high
          ? /(?:high|maximum|最高|高温)[^\d-]{0,12}-?\d+(?:\.\d+)?\s*°?\s*[CF℃]?/i
          : /(?:low|minimum|最低|低温)[^\d-]{0,12}-?\d+(?:\.\d+)?\s*°?\s*[CF℃]?/i,
        120,
        true
      );
    };

    const extractSortState = (rootText, root) => extractMetricField(
      rootText,
      root,
      [
        '[role="tab"][aria-selected="true"]',
        '[aria-current="true"][class*="sort" i]',
        '[class*="sort" i][class*="active" i]',
        '[class*="sort" i][class*="selected" i]'
      ],
      ['sort', 'sort state', 'sort order', '排序', '排序状态', '排序方式'],
      /(?:sort(?:ed)?(?: by)?|排序(?:状态|方式)?)\s*[:：]?\s*[^\n|·•,，;；]{2,80}/i
    );

    const extractSource = (rootText, root, primaryLink) => {
      const sourceEl = findFirst(root, mergeSelectors(
        profileSelectors('source_selectors'),
        [
          '[class*="source" i]',
          '[class*="origin" i]',
          '[class*="from" i]',
          '[class*="site" i]',
          '[class*="platform" i]',
          '[class*="channel" i]',
          '[data-testid*="source" i]',
          '[data-testid*="origin" i]',
          '[data-test*="source" i]',
          '[data-test*="origin" i]'
        ]
      ));

      const fromElement = textOf(sourceEl, 120);
      const fromLabel = labeledText(rootText, ['source', 'from', 'origin', 'site', '来源', '來自', '出处', '出處'], 120);

      let fromLink = '';
      try {
        if (primaryLink && primaryLink.href) {
          const parsed = new URL(primaryLink.href, window.location.href);
          fromLink = parsed.hostname || '';
        }
      } catch (_) {
        fromLink = '';
      }

      return {
        value: fromElement || fromLabel || fromLink,
        selector_hint: sourceEl ? buildSelectorHint(sourceEl) : ''
      };
    };

    const compactSummary = (value, titleValue) => {
      let summary = normalize(value, 320);
      const title = normalize(titleValue, 220);
      if (title && summary.toLowerCase().startsWith(title.toLowerCase())) {
        summary = normalize(summary.slice(title.length), 320);
      }
      summary = summary.replace(/^[-–—:：|·•\s]+/, '').trim();
      if (summary && title && summary.toLowerCase() === title.toLowerCase()) return '';
      return summary;
    };

    const extractSummary = (rootText, root, titleValue) => {
      const summaryEl = findFirst(root, mergeSelectors(
        profileSelectors('summary_selectors'),
        [
          '[class*="summary" i]',
          '[class*="desc" i]',
          '[class*="description" i]',
          '[class*="abstract" i]',
          '[class*="snippet" i]',
          '[class*="intro" i]',
          '[class*="excerpt" i]',
          '[class*="content" i]',
          '[data-testid*="summary" i]',
          '[data-testid*="desc" i]',
          '[data-testid*="snippet" i]',
          '[data-test*="summary" i]',
          '[data-test*="desc" i]',
          '[data-test*="snippet" i]',
          'p'
        ]
      ));

      const fromElement = compactSummary(textOf(summaryEl, 360), titleValue);
      const fromText = compactSummary(rootText, titleValue);

      return {
        value: fromElement || fromText,
        selector_hint: summaryEl ? buildSelectorHint(summaryEl) : ''
      };
    };

    const PRICE_RE =
      /(?:S\$|US\$|A\$|HK\$|\$|£|€|¥|￥|Rp|RM|SGD|USD|IDR|MYR)\s?\d[\d,.]*(?:\.\d+)?|\d[\d,.]*(?:\.\d+)?\s?(?:SGD|USD|IDR|MYR|円)/i;

    const normalizePriceValue = (value) => {
      const cleaned = normalize(value, 120);
      const match = cleaned.match(PRICE_RE);
      return match ? normalize(match[0], 80) : '';
    };

    const extractPrice = (rootText, root) => {
      const priceEl = findFirst(root, mergeSelectors(
        profileSelectors('price_selectors'),
        [
          '[class*="price" i]',
          '[data-testid*="price" i]',
          '[data-test*="price" i]',
          '[aria-label*="price" i]'
        ]
      ));

      const fromElement = normalizePriceValue(textOf(priceEl, 120));
      if (fromElement) {
        return {
          value: fromElement,
          raw_text: textOf(priceEl, 240),
          selector_hint: buildSelectorHint(priceEl)
        };
      }

      const fromText = normalizePriceValue(rootText);

      return {
        value: fromText,
        raw_text: fromText ? normalize(rootText, 240) : '',
        selector_hint: priceEl ? buildSelectorHint(priceEl) : ''
      };
    };

    const ratingClassValue = (el) => {
      if (!el) return '';

      const raw = `${el.getAttribute('class') || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;

      const wordMap = [
        ['Five', 'Five stars'],
        ['Four', 'Four stars'],
        ['Three', 'Three stars'],
        ['Two', 'Two stars'],
        ['One', 'One star']
      ];

      for (const [needle, value] of wordMap) {
        if (new RegExp(`\\b${needle}\\b`, 'i').test(raw)) {
          return value;
        }
      }

      const numeric = raw.match(/(?:rating|star)[^0-9]*(\d(?:\.\d)?)/i);
      if (numeric) {
        return `${numeric[1]} stars`;
      }

      return '';
    };

    const ratingScopeKind = (ratingEl, root, primaryLink) => {
      const parts = [];
      let node = ratingEl;
      let depth = 0;
      while (node && node !== root && depth < 4) {
        parts.push(elementDescriptor(node));
        node = node.parentElement;
        depth += 1;
      }
      const context = parts.join(' ');
      const ratingRules = semanticRules
        .map((rules) => rules.rating)
        .filter((rules) => rules && typeof rules === 'object');
      const shopRating = ratingRules.some((rules) => {
        return matchesProfilePattern(context, rules.shop_patterns);
      });
      const productRating = ratingRules.some((rules) => {
        return matchesProfilePattern(context, rules.product_patterns);
      });
      if (shopRating && !productRating) return 'shop_rating';
      if (productRating || siteDetailLink(primaryLink?.href || '')?.kind === 'product') {
        return 'product_rating';
      }
      return ratingRules.some((rules) => rules.unknown_without_match === true)
        ? 'unknown'
        : 'rating';
    };

    const extractRating = (rootText, root, primaryLink) => {
      const ratingEl = findFirst(root, mergeSelectors(
        profileSelectors('rating_selectors'),
        [
          '[class*="rating" i]',
          '[aria-label*="rating" i]',
          '[title*="rating" i]',
          '[class*="star" i]',
          '[aria-label*="star" i]'
        ]
      ));

      const fromClass = ratingClassValue(ratingEl);
      if (fromClass) {
        return {
          value: fromClass,
          raw_text: textOf(ratingEl, 240),
          kind: ratingScopeKind(ratingEl, root, primaryLink),
          status: 'present',
          selector_hint: buildSelectorHint(ratingEl)
        };
      }

      const ratingText = `${textOf(ratingEl, 120)} ${rootText}`;
      const match = ratingText.match(
        /(?:\d(?:\.\d)?\s*(?:out of|\/)\s*5)|(?:\d(?:\.\d)?\s*★)|(?:rating\s*[:\-]?\s*\d(?:\.\d)?)/i
      );

      return {
        value: match ? normalize(match[0], 80) : '',
        raw_text: match ? normalize(match[0], 240) : '',
        kind: ratingScopeKind(ratingEl, root, primaryLink),
        status: match ? 'present' : 'missing',
        selector_hint: ratingEl ? buildSelectorHint(ratingEl) : ''
      };
    };

    const extractReviewCount = (rootText) => {
      const match = rootText.match(
        /(?:\(?\d[\d,.]*\)?\s*(?:reviews?|ratings?|sold|bought|orders?))|(?:(?:reviews?|ratings?)\s*[:\-]?\s*\d[\d,.]*)/i
      );
      return match ? normalize(match[0], 80) : '';
    };

    const extractAvailability = (rootText) => {
      const match = rootText.match(
        /\b(?:in stock|out of stock|available|unavailable|sold out|limited stock|only \d+ left)\b/i
      );
      return match ? normalize(match[0], 80) : '';
    };

    const extractButtons = (root) => {
      if (!includeButtons) return [];

      const buttonSelectors = mergeSelectors(
        profileSelectors('button_selectors'),
        [
          'button',
          '[role="button"]',
          'input[type="button"]',
          'input[type="submit"]',
          'a[href]'
        ]
      );

      const buttons = Array.from(root.querySelectorAll(buttonSelectors.join(',')));

      return buttons
        .map((el) => {
          const rect = el.getBoundingClientRect();
          if (!el || rect.width < 2 || rect.height < 2) return null;

          const style = window.getComputedStyle(el);
          if (!style) return null;
          if (style.display === 'none') return null;
          if (style.visibility === 'hidden') return null;
          if (Number(style.opacity) === 0) return null;

          const text = normalize(
            el.getAttribute('aria-label') ||
            el.getAttribute('value') ||
            el.innerText ||
            el.textContent ||
            '',
            120
          );

          if (!text) return null;

          const selectorHint = buildSelectorHint(el);
          const selectorMeta = selectorMetadata(selectorHint);
          const actionable = isActionable(el, rect);
          const href = normalize(el.href || el.getAttribute('href') || '', 260);
          const semanticText = `${text} ${href}`.toLowerCase();
          let kind = 'control';
          if (siteDetailLink(href)?.kind === 'product') kind = 'product';
          else if (/(wangwang|aliim|amos|contact-seller|旺旺|联系卖家)/.test(semanticText)) kind = 'chat';
          else if (/(shop|store|seller|店铺|卖家)/.test(semanticText)) kind = 'shop';
          const clickable = Boolean(
            actionable &&
            selectorHint &&
            selectorMeta.match_count === 1 &&
            selectorMeta.visible &&
            selectorMeta.enabled
          );
          return {
            text,
            region: 'card_control',
            kind,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            href,
            selector_hint: clickable ? selectorHint : '',
            selector_hint_validated: clickable,
            match_count: selectorMeta.match_count,
            visible: true,
            enabled: isEnabled(el),
            actionable,
            clickable,
            generation_id: generationId,
            bbox: [
              Math.round(rect.x),
              Math.round(rect.y),
              Math.round(rect.width),
              Math.round(rect.height)
            ]
          };
        })
        .filter(Boolean)
        .slice(0, 8);
    };

    const PAGE_CHROME_FRAGMENTS = [
      '#nav',
      'nav-',
      'navbar',
      'breadcrumb',
      'header',
      'footer',
      'menu',
      'sidebar',
      'toolbar',
      'container-right',
      'main-rt',
      'main-right',
      'right-sidebar',
      'right-side',
      'side-bar',
      'csdn-toolbar',
      'csdn-profile',
      'onlyuser',
      'passport',
      'login',
      'vip',
      'write',
      'remind',
      'message'
    ];

    const hasChromeFragment = (value) => {
      const text = String(value || '').toLowerCase();
      return PAGE_CHROME_FRAGMENTS.some((fragment) => text.includes(fragment));
    };

    const elementChromeText = (el) => {
      if (!el) return '';
      return normalize([
        el.tagName || '',
        el.getAttribute('id') || '',
        el.getAttribute('class') || '',
        el.getAttribute('role') || '',
        el.getAttribute('aria-label') || '',
        el.getAttribute('data-testid') || '',
        el.getAttribute('data-test') || ''
      ].join(' '), 360).toLowerCase();
    };

    const elementLooksLikeChrome = (el) => {
      if (!el || !el.tagName) return false;
      const tag = el.tagName.toLowerCase();
      if (['nav', 'header', 'footer'].includes(tag)) return true;
      return hasChromeFragment(elementChromeText(el));
    };

    const promoteCandidateRoot = (el) => {
      if (!el || !el.tagName) return null;
      const initialSemantics = classifyCardSemantics(el, { href: elementHref(el) }, directText(el));
      const preserveSemanticRegion = ['account', 'hot_search', 'shop', 'chat', 'product'].includes(
        initialSemantics.kind
      );
      if (elementLooksLikeChrome(el) && !preserveSemanticRegion) return null;

      const startTag = el.tagName.toLowerCase();
      const startRect = el.getBoundingClientRect();
      const shouldPromote = (
        ['a', 'span', 'h1', 'h2', 'h3', 'h4'].includes(startTag) ||
        startRect.height < 80 ||
        startRect.width < Math.max(160, window.innerWidth * 0.35)
      );

      if (!shouldPromote) return el;

      let best = el;
      let node = el.parentElement;
      let depth = 0;

      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 5) {
        const tag = node.tagName.toLowerCase();
        if (['html', 'body', 'main', 'nav', 'header', 'footer'].includes(tag)) break;
        if (elementLooksLikeChrome(node) && !preserveSemanticRegion) return null;

        const rect = node.getBoundingClientRect();
        const area = rect.width * rect.height;
        const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
        if (area > viewportArea * 0.70) break;

        const nodeText = directText(node);
        const hasLink = Boolean(node.querySelector('a[href]'));
        const hasHeading = Boolean(node.querySelector('h1,h2,h3,h4,[role="heading"]'));
        const hasSummaryLike = Boolean(node.querySelector(
          'p,[class*="summary" i],[class*="desc" i],[class*="abstract" i],' +
          '[class*="snippet" i],[class*="content" i],[class*="intro" i]'
        ));

        if (hasLink && (hasHeading || startTag === 'a') && nodeText.length >= 40) {
          best = node;
          if (nodeText.length >= 90 || hasSummaryLike) break;
        }

        node = node.parentElement;
        depth += 1;
      }

      return best;
    };

    const looksLikePageChrome = (data) => {
      if (['account', 'hot_search', 'shop', 'chat', 'product'].includes(String(data.kind || ''))) {
        return false;
      }
      const selector = String(data.selector_hint || '').toLowerCase();
      const title = String(data.title || '').trim().toLowerCase();
      const preview = String(data.text_preview || '').trim().toLowerCase();
      const link = String(data.primary_link || '').trim().toLowerCase();

      if (hasChromeFragment(selector) || hasChromeFragment(link)) {
        return true;
      }

      if (link.includes('mp.csdn.net') || link.includes('passport.csdn.net')) {
        return true;
      }

      const chromeTitles = new Set([
        'fresh & fast',
        'sell',
        'best sellers',
        'customer service',
        "today's deals",
        'new releases',
        'help',
        'login',
        'sign in',
        '会员中心',
        '消息',
        '创作中心',
        '个人中心'
      ]);

      if (chromeTitles.has(title) || chromeTitles.has(preview)) {
        return true;
      }

      if (
        preview.length < 4 &&
        !data.price &&
        !data.rating &&
        !data.author &&
        !data.likes &&
        !data.favorites &&
        !data.comments &&
        !data.shop &&
        !data.duration &&
        !data.source &&
        !data.summary &&
        !data.has_image
      ) {
        return true;
      }

      return false;
    };

    const cardQualityScore = (data) => {
      if (looksLikePageChrome(data)) return 0;

      let score = 0;

      const title = String(data.title || '').trim();
      const preview = String(data.text_preview || '').trim();
      const buttons = Array.isArray(data.buttons) ? data.buttons : [];

      if (title.length >= 8) score += 20;
      if (preview.length >= 60) score += 15;
      if (data.primary_link) score += 12;
      if (data.price) score += 18;
      if (data.rating) score += 14;
      if (data.review_count) score += 10;
      if (data.availability) score += 8;
      if (data.author) score += 10;
      if (data.likes || data.favorites || data.comments) score += 8;
      if (data.shop || data.duration) score += 6;
      if (data.source) score += 6;
      if (data.summary && String(data.summary).length >= 40) score += 14;
      if (data.has_image) score += 12;
      if (buttons.length > 0) score += 8;
      if (data.kind === 'product') score += 20;
      if (data.region === 'main_result') score += 35;
      if (data.region === 'sidebar' || data.region === 'hot_search') score -= 25;
      if (data.is_ad) score -= 10;

      return score;
    };

    const isHighQualityCard = (item) => {
      const score = item.quality_score || cardQualityScore(item.data || {});
      if (score >= 42) return true;

      const data = item.data || {};
      const preview = String(data.text_preview || '').trim();
      const buttons = Array.isArray(data.buttons) ? data.buttons : [];

      // Allow quote/article-style cards that do not have price/rating/image.
      return (
        score >= 30 &&
        preview.length >= 80 &&
        (data.primary_link || buttons.length > 0)
      );
    };

    const hasEnoughGoodCards = (items) => {
      if (!Array.isArray(items) || items.length === 0) return false;

      const good = items.filter(isHighQualityCard);
      if (good.length >= Math.min(maxCards, 3)) return true;

      const signatureCounts = new Map();
      for (const item of good) {
        const count = signatureCounts.get(item.signature) || 0;
        signatureCounts.set(item.signature, count + 1);
      }

      return Array.from(signatureCounts.values()).some((count) => count >= 2);
    };

    const hasImage = (root) => {
      return Boolean(root.querySelector('img, picture, source[srcset]'));
    };

    const structuralSignature = (el, fields) => {
      const tag = el.tagName.toLowerCase();
      const classTokens = normalize(el.getAttribute('class') || '', 160)
        .split(' ')
        .filter(Boolean)
        .slice(0, 4)
        .join('.');
      const children = Array.from(el.children)
        .slice(0, 8)
        .map((child) => child.tagName.toLowerCase())
        .join('>');
      const fieldBits = [
        fields.title ? 'title' : '',
        fields.price ? 'price' : '',
        fields.rating ? 'rating' : '',
        fields.author ? 'author' : '',
        fields.likes ? 'likes' : '',
        fields.favorites ? 'favorites' : '',
        fields.comments ? 'comments' : '',
        fields.shop ? 'shop' : '',
        fields.duration ? 'duration' : '',
        fields.high_temperature ? 'high_temperature' : '',
        fields.low_temperature ? 'low_temperature' : '',
        fields.sort_state ? 'sort_state' : '',
        fields.source ? 'source' : '',
        fields.summary ? 'summary' : '',
        fields.buttons && fields.buttons.length ? 'button' : '',
        fields.has_image ? 'image' : ''
      ].filter(Boolean).join('|');

      return `${tag}|${classTokens}|${children}|${fieldBits}`;
    };

    const queryAllSafe = (selectors) => {
      const result = [];
      const seen = new Set();

      for (const selector of selectors || []) {
        try {
          const nodes = Array.from(document.querySelectorAll(selector));
          for (const node of nodes) {
            if (!node || seen.has(node)) continue;
            seen.add(node);
            result.push(node);
          }
        } catch (_) {
          // Ignore invalid selectors.
        }
      }

      return result;
    };

    const buildCandidatesFromContainers = (containers, selectorSource) => {
      const seen = new Set();
      const localCandidates = [];

      for (const rawEl of containers || []) {
        if (!rawEl) continue;

        const el = promoteCandidateRoot(rawEl);
        if (!el || seen.has(el)) continue;
        seen.add(el);

        const initialSemantics = classifyCardSemantics(el, { href: elementHref(el) }, directText(el));
        if (
          elementLooksLikeChrome(el) &&
          !['account', 'hot_search', 'shop', 'chat', 'product'].includes(initialSemantics.kind)
        ) continue;

        const tag = el.tagName.toLowerCase();
        if (
          tag === 'html' ||
          tag === 'body' ||
          tag === 'main' ||
          tag === 'nav' ||
          tag === 'header' ||
          tag === 'footer'
        ) {
          continue;
        }

        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect)) continue;

        const area = rect.width * rect.height;
        const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
        if (area > viewportArea * 0.85) continue;

        const rootText = directText(el);
        if (!rootText || rootText.length < 4) continue;
        if (query && !rootText.toLowerCase().includes(query)) continue;

        const title = extractTitle(el);
        const price = extractPrice(rootText, el);
        let primaryLink = extractPrimaryLink(el);
        const rating = extractRating(rootText, el, primaryLink);
        const buttons = extractButtons(el);
        if (!primaryLink.href || isAuthorProfileHref(primaryLink.href)) {
          const buttonArticleLink = buttons.find((button) => {
            return button && button.href && isArticleHref(button.href);
          });
          if (buttonArticleLink) {
            primaryLink = {
              text: buttonArticleLink.text || '',
              href: buttonArticleLink.href,
              selector_hint: buttonArticleLink.selector_hint || ''
            };
          }
        }
        const author = extractAuthor(rootText, el);
        const likes = extractLikes(rootText, el);
        const favorites = extractFavorites(rootText, el);
        const comments = extractComments(rootText, el);
        const shop = extractShop(rootText, el);
        const duration = extractDuration(rootText, el);
        const highTemperature = extractTemperature(rootText, el, 'high');
        const lowTemperature = extractTemperature(rootText, el, 'low');
        const sortState = extractSortState(rootText, el);
        const source = extractSource(rootText, el, primaryLink);
        const summary = extractSummary(rootText, el, title.value);
        const reviewCount = extractReviewCount(rootText);
        const availability = extractAvailability(rootText);
        const imagePresent = hasImage(el);
        const semantics = classifyCardSemantics(el, primaryLink, rootText);

        const fields = {
          title: title.value,
          price: price.value,
          rating: rating.kind === 'shop_rating' || rating.kind === 'unknown' ? '' : rating.value,
          product_rating: rating.kind === 'product_rating' ? rating.value : '',
          shop_rating: rating.kind === 'shop_rating' ? rating.value : '',
          review_count: reviewCount,
          availability,
          author: author.value,
          likes: likes.value,
          favorites: favorites.value,
          comments: comments.value,
          shop: shop.value,
          duration: duration.value,
          high_temperature: highTemperature.value,
          low_temperature: lowTemperature.value,
          sort_state: sortState.value,
          source: source.value,
          summary: summary.value,
          primary_link: primaryLink.href,
          buttons,
          has_image: imagePresent
        };

        const fieldCount = [
          fields.title,
          fields.price,
          fields.rating,
          fields.review_count,
          fields.availability,
          fields.author,
          fields.likes,
          fields.favorites,
          fields.comments,
          fields.shop,
          fields.duration,
          fields.high_temperature,
          fields.low_temperature,
          fields.sort_state,
          fields.source,
          fields.summary,
          fields.primary_link,
          fields.has_image,
          fields.buttons && fields.buttons.length
        ].filter(Boolean).length;

        if (fieldCount < 2) continue;

        const cardSelectorHint = buildSelectorHint(el);
        const primaryLinkSelectorHint = primaryLink.selector_hint || '';
        const cardSelectorMeta = selectorMetadata(cardSelectorHint);
        const primaryLinkSelectorMeta = selectorMetadata(primaryLinkSelectorHint);
        const cardActionable = isActionable(el, rect);
        const cardSelectorValidated = Boolean(
          cardSelectorHint &&
          cardSelectorMeta.match_count === 1 &&
          cardSelectorMeta.visible &&
          cardSelectorMeta.enabled
        );
        const primaryLinkSelectorValidated = Boolean(
          primaryLinkSelectorHint &&
          primaryLinkSelectorMeta.match_count === 1 &&
          primaryLinkSelectorMeta.visible &&
          primaryLinkSelectorMeta.enabled
        );
        const cardClickable = Boolean(
          cardActionable &&
          cardSelectorValidated
        );
        const data = {
          selector_source: selectorSource,
          region: semantics.region,
          kind: semantics.kind,
          is_ad: semantics.is_ad,
          semantic_badges: semantics.semantic_badges,
          selector_hint: cardSelectorHint,
          selector_hint_validated: cardSelectorValidated,
          match_count: cardSelectorMeta.match_count,
          visible: true,
          enabled: isEnabled(el),
          actionable: cardActionable,
          clickable: cardClickable,
          generation_id: generationId,
          title: title.value,
          title_selector_hint: title.selector_hint,
          price: price.value,
          price_selector_hint: price.selector_hint,
          price_raw_text: price.raw_text,
          rating: rating.kind === 'shop_rating' || rating.kind === 'unknown' ? null : (rating.value || null),
          product_rating: rating.kind === 'product_rating' ? (rating.value || null) : null,
          shop_rating: rating.kind === 'shop_rating' ? (rating.value || null) : null,
          rating_candidate: rating.value || null,
          rating_kind: rating.kind,
          rating_raw_text: rating.raw_text,
          rating_selector_hint: rating.selector_hint,
          review_count: reviewCount,
          availability,
          author: author.value,
          author_selector_hint: author.selector_hint,
          likes: likes.value,
          likes_selector_hint: likes.selector_hint,
          likes_raw_text: likes.raw_text,
          favorites: favorites.value,
          favorites_selector_hint: favorites.selector_hint,
          favorites_raw_text: favorites.raw_text,
          comments: comments.value,
          comments_selector_hint: comments.selector_hint,
          comments_raw_text: comments.raw_text,
          shop: shop.value,
          shop_selector_hint: shop.selector_hint,
          shop_raw_text: shop.raw_text,
          duration: duration.value,
          duration_selector_hint: duration.selector_hint,
          duration_raw_text: duration.raw_text,
          high_temperature: highTemperature.value,
          high_temperature_selector_hint: highTemperature.selector_hint,
          high_temperature_raw_text: highTemperature.raw_text,
          low_temperature: lowTemperature.value,
          low_temperature_selector_hint: lowTemperature.selector_hint,
          low_temperature_raw_text: lowTemperature.raw_text,
          sort_state: sortState.value,
          sort_state_selector_hint: sortState.selector_hint,
          sort_state_raw_text: sortState.raw_text,
          source: source.value,
          source_selector_hint: source.selector_hint,
          summary: summary.value,
          summary_selector_hint: summary.selector_hint,
          primary_link: primaryLink.href,
          href: primaryLink.href,
          primary_link_text: primaryLink.text,
          primary_link_selector_hint: primaryLinkSelectorHint,
          primary_link_selector_hint_validated: primaryLinkSelectorValidated,
          primary_link_match_count: primaryLinkSelectorMeta.match_count,
          primary_link_visible: primaryLinkSelectorMeta.visible,
          primary_link_enabled: primaryLinkSelectorMeta.enabled,
          selector_metadata: {
            root: selectorDescriptor(cardSelectorHint),
            title: selectorDescriptor(title.selector_hint),
            price: selectorDescriptor(price.selector_hint),
            rating: selectorDescriptor(rating.selector_hint),
            author: selectorDescriptor(author.selector_hint),
            likes: selectorDescriptor(likes.selector_hint),
            favorites: selectorDescriptor(favorites.selector_hint),
            comments: selectorDescriptor(comments.selector_hint),
            shop: selectorDescriptor(shop.selector_hint),
            duration: selectorDescriptor(duration.selector_hint),
            high_temperature: selectorDescriptor(highTemperature.selector_hint),
            low_temperature: selectorDescriptor(lowTemperature.selector_hint),
            sort_state: selectorDescriptor(sortState.selector_hint),
            source: selectorDescriptor(source.selector_hint),
            summary: selectorDescriptor(summary.selector_hint),
            primary_link: selectorDescriptor(primaryLinkSelectorHint)
          },
          recommended_action: primaryLink.href
            ? 'navigate_primary_link'
            : (cardClickable ? 'use_validated_selector' : 'use_actionable_child_control'),
          has_image: imagePresent,
          buttons,
          text_preview: normalize(rootText, 280),
          bbox: [
            Math.round(rect.x),
            Math.round(rect.y),
            Math.round(rect.width),
            Math.round(rect.height)
          ]
        };

        const qualityScore = cardQualityScore(data);
        const signature = structuralSignature(el, fields);

        localCandidates.push({
          el,
          signature,
          fieldCount,
          area,
          top: rect.top,
          left: rect.left,
          quality_score: qualityScore,
          data: {
            ...data,
            quality_score: qualityScore
          }
        });
      }

      return localCandidates;
    };

    const cachedContainerSelectors = cacheSelectors('card_container_selectors');
    const profileContainerSelectors = siteProfileSelectors('card_container_selectors');

    const genericContainerSelectors = [
      'article',
      'li',
      'tr',
      'tbody > tr',
      '[role="article"]',
      '[role="row"]',
      'h1:has(a[href])',
      'h2:has(a[href])',
      'h3:has(a[href])',
      'h4:has(a[href])',
      'a[href*="blog.csdn.net"]',
      'a[href*="/article/details/"]',
      '[class*="search-list" i] > *',
      '[class*="result-list" i] > *',
      '[class*="list-container" i] > *',
      'section',
      '[data-testid*="card" i]',
      '[data-testid*="item" i]',
      '[data-testid*="product" i]',
      '[data-testid*="article" i]',
      '[data-testid*="post" i]',
      '[data-testid*="result" i]',
      '[data-testid*="row" i]',
      '[data-test*="card" i]',
      '[data-test*="item" i]',
      '[data-test*="product" i]',
      '[data-test*="article" i]',
      '[data-test*="post" i]',
      '[data-test*="result" i]',
      '[data-test*="row" i]',
      '[class*="card" i]',
      '[class*="item" i]',
      '[class*="product" i]',
      '[class*="article" i]',
      '[class*="post" i]',
      '[class*="entry" i]',
      '[class*="blog" i]',
      '[class*="search-result" i]',
      '[class*="search-item" i]',
      '[class*="search-list" i]',
      '[class*="so-item" i]',
      '[class*="result-item" i]',
      '[class*="result-list" i]',
      '[class*="result" i]',
      '[class*="list" i]',
      '[class*="row" i]',
      'div'
    ];

    const cachedContainers = queryAllSafe(cachedContainerSelectors);
    const cachedCandidates = buildCandidatesFromContainers(cachedContainers, 'cache');
    const cacheCandidateCount = cachedCandidates.length;
    const cacheGoodCandidateCount = cachedCandidates.filter(isHighQualityCard).length;
    let profileCandidateCount = 0;
    let genericCandidateCount = 0;

    let selectorSource = 'generic';
    let candidates = [];

    if (hasEnoughGoodCards(cachedCandidates)) {
      selectorSource = 'cache';
      candidates = cachedCandidates;
    } else {
      const profileContainers = queryAllSafe(profileContainerSelectors);
      const profileCandidates = buildCandidatesFromContainers(profileContainers, 'profile');
      profileCandidateCount = profileCandidates.length;

      if (hasEnoughGoodCards(profileCandidates)) {
        selectorSource = 'profile';
        candidates = profileCandidates;
      } else {
        const genericContainers = queryAllSafe(genericContainerSelectors);
        selectorSource = 'generic';
        candidates = buildCandidatesFromContainers(genericContainers, 'generic');
        genericCandidateCount = candidates.length;
      }
    }

    const groups = new Map();
    for (const item of candidates) {
      const group = groups.get(item.signature) || [];
      group.push(item);
      groups.set(item.signature, group);
    }

    const recurringSignatures = Array.from(groups.entries())
      .map(([signature, group]) => ({
        signature,
        count: group.length,
        sample_selector_hint: group[0]?.data?.selector_hint || '',
        fields_detected: [
          group.some((x) => x.data.title) ? 'title' : '',
          group.some((x) => x.data.price) ? 'price' : '',
          group.some((x) => x.data.rating) ? 'rating' : '',
          group.some((x) => x.data.review_count) ? 'review_count' : '',
          group.some((x) => x.data.availability) ? 'availability' : '',
          group.some((x) => x.data.author) ? 'author' : '',
          group.some((x) => x.data.likes) ? 'likes' : '',
          group.some((x) => x.data.favorites) ? 'favorites' : '',
          group.some((x) => x.data.comments) ? 'comments' : '',
          group.some((x) => x.data.shop) ? 'shop' : '',
          group.some((x) => x.data.duration) ? 'duration' : '',
          group.some((x) => x.data.high_temperature) ? 'high_temperature' : '',
          group.some((x) => x.data.low_temperature) ? 'low_temperature' : '',
          group.some((x) => x.data.sort_state) ? 'sort_state' : '',
          group.some((x) => x.data.source) ? 'source' : '',
          group.some((x) => x.data.summary) ? 'summary' : '',
          group.some((x) => x.data.buttons && x.data.buttons.length) ? 'buttons' : '',
          group.some((x) => x.data.has_image) ? 'image' : ''
        ].filter(Boolean)
      }))
      .filter((item) => item.count >= 2)
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);

    const scored = candidates.map((item) => {
      const groupCount = groups.get(item.signature)?.length || 1;
      let score = 0;

      score += item.fieldCount * 20;
      score += item.quality_score || 0;
      if (groupCount >= 2) score += 50 + Math.min(groupCount, 20) * 4;
      if (item.data.price) score += 20;
      if (item.data.title) score += 15;
      if (item.data.author) score += 8;
      if (item.data.likes || item.data.favorites || item.data.comments) score += 6;
      if (item.data.shop || item.data.duration) score += 4;
      if (item.data.source) score += 4;
      if (item.data.summary) score += 10;
      if (item.data.buttons && item.data.buttons.length) score += 12;
      if (item.data.has_image) score += 8;
      if (item.data.region === 'main_result') score += 60;
      if (item.data.kind === 'product') score += 20;
      if (item.data.region === 'sidebar' || item.data.region === 'hot_search') score -= 35;
      if (item.data.is_ad) score -= 10;
      if (item.top >= 0 && item.top <= window.innerHeight) score += 8;

      // Penalize very large containers because they are often grids/sections, not cards.
      const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
      if (item.area > viewportArea * 0.45) score -= 40;

      return {
        ...item,
        groupCount,
        score
      };
    });

    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      if (b.groupCount !== a.groupCount) return b.groupCount - a.groupCount;
      if (a.top !== b.top) return a.top - b.top;
      return a.left - b.left;
    });

    const hasRichCandidates = scored.some((candidate) => {
      return Boolean(
        candidate.data.price ||
        candidate.data.rating ||
        candidate.data.review_count ||
        candidate.data.availability ||
        candidate.data.author ||
        candidate.data.likes ||
        candidate.data.favorites ||
        candidate.data.comments ||
        candidate.data.shop ||
        candidate.data.duration ||
        candidate.data.high_temperature ||
        candidate.data.low_temperature ||
        candidate.data.sort_state ||
        candidate.data.source ||
        candidate.data.summary ||
        candidate.data.has_image
      );
    });

    const selectable = scored.filter((item) => {
      if (looksLikePageChrome(item.data)) return false;

      // If we already have rich listing-like candidates, remove weak nav-like entries.
      if (!hasRichCandidates) return true;

      return Boolean(
        item.data.price ||
        item.data.rating ||
        item.data.review_count ||
        item.data.availability ||
        item.data.author ||
        item.data.likes ||
        item.data.favorites ||
        item.data.comments ||
        item.data.shop ||
        item.data.duration ||
        item.data.high_temperature ||
        item.data.low_temperature ||
        item.data.sort_state ||
        item.data.source ||
        item.data.summary ||
        item.data.has_image ||
        item.quality_score >= 45
      );
    });

    const selected = [];
    for (const item of selectable) {
      const conflictsWithExisting = selected.find((chosen) => {
        return item.el.contains(chosen.el) || chosen.el.contains(item.el);
      });

      if (conflictsWithExisting) {
        // Prefer the candidate with more extracted fields. If tied, prefer the smaller
        // repeated card-like container over a large section/grid wrapper.
        const itemBetter =
          item.fieldCount > conflictsWithExisting.fieldCount ||
          (
            item.fieldCount === conflictsWithExisting.fieldCount &&
            item.groupCount >= conflictsWithExisting.groupCount &&
            item.area < conflictsWithExisting.area
          );

        if (itemBetter) {
          const idx = selected.indexOf(conflictsWithExisting);
          selected.splice(idx, 1, item);
        }

        continue;
      }

      selected.push(item);
      if (selected.length >= maxCards) break;
    }

    const deduplicated = [];
    const profileDetailLinks = new Set();
    for (const item of selected) {
      const detailLink = siteDetailLink(item.data.primary_link);
      if (detailLink?.deduplicate && detailLink.key) {
        if (profileDetailLinks.has(detailLink.key)) continue;
        profileDetailLinks.add(detailLink.key);
      }
      deduplicated.push(item);
    }

    let resultIndex = 0;
    const cards = deduplicated.map((item, index) => {
      const isMainResult = item.data.region === 'main_result' &&
        ['result', 'product', 'hotel'].includes(item.data.kind) && !item.data.is_ad;
      if (isMainResult) resultIndex += 1;
      return {
        id: `card_${index + 1}`,
        group_count: item.groupCount,
        ...item.data,
        result_index: isMainResult ? resultIndex : null
      };
    });

    return {
      ok: true,
      url: window.location.href,
      title: document.title,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        scroll_x: window.scrollX,
        scroll_y: window.scrollY
      },
      query,
      viewport_only: viewportOnly,
      generation_id: generationId,
      profile_ids: activeProfiles.map((profile) => profile.id || '').filter(Boolean),
      cache_records_used: activeCacheRecords.length,
      selector_source: selectorSource,
      cache_accepted: selectorSource === 'cache',
      cache_rejection_reason:
        activeCacheRecords.length > 0 && selectorSource !== 'cache'
          ? 'cache_validation_failed'
          : null,
      cache_candidate_count: cacheCandidateCount,
      cache_good_candidate_count: cacheGoodCandidateCount,
      profile_candidate_count: profileCandidateCount,
      generic_candidate_count: genericCandidateCount,
      cached_container_selectors: cachedContainerSelectors.length,
      profile_container_selectors: profileContainerSelectors.length,
      total_candidates: candidates.length,
      returned: cards.length,
      recurring_signatures: recurringSignatures,
      cards,
      error: null
    };
  }, params);
}
""".strip()

    return template.replace("__PARAMS__", params_json)
