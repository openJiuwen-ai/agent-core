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


def build_interactive_probe_js(
    *,
    max_items: int = 50,
    viewport_only: bool = True,
    query: str = "",
) -> str:
    """Build browser_run_code JavaScript for compact interactive-element probing."""

    params: Dict[str, Any] = {
        "max_items": _clamp_int(max_items, default=50, minimum=1, maximum=100),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip().lower(),
    }
    params_json = json.dumps(params, ensure_ascii=False)

    return f"""
async (page) => {{
  const params = {params_json};

  return await page.evaluate((params) => {{
    const maxItems = Math.max(1, Math.min(Number(params.max_items || 50), 100));
    const viewportOnly = params.viewport_only !== false;
    const query = String(params.query || '').trim().toLowerCase();

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

    const buildSelectorHint = (el) => {{
      const tag = el.tagName.toLowerCase();

      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy');
      if (testid) {{
        if (el.getAttribute('data-testid')) return `[data-testid="${{attrEscape(testid)}}"]`;
        if (el.getAttribute('data-test')) return `[data-test="${{attrEscape(testid)}}"]`;
        return `[data-cy="${{attrEscape(testid)}}"]`;
      }}

      const id = el.getAttribute('id');
      if (id) return `#${{cssEscape(id)}}`;

      const aria = el.getAttribute('aria-label');
      if (aria) return `${{tag}}[aria-label="${{attrEscape(aria)}}"]`;

      const name = el.getAttribute('name');
      if (name) return `${{tag}}[name="${{attrEscape(name)}}"]`;

      const placeholder = el.getAttribute('placeholder');
      if (placeholder) return `${{tag}}[placeholder="${{attrEscape(placeholder)}}"]`;

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

      return path.join(' > ');
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

    const queryAliases = (value) => {{
      const raw = String(value || '').trim().toLowerCase();
      if (!raw) return [];

      const aliases = new Set([raw]);
      const add = (items) => items.forEach((item) => aliases.add(item));

      if (['search', 'find', 'query', 'keyword', 'keywords'].includes(raw)) {{
        add(['search', 'find', 'query', 'keyword', 'keywords', 'searchbox', 'search-box',
          'search_input', 'search-input',
          '搜索', '搜', '搜尋', '查询', '查找', '关键词', '关键字', '搜寻', '検索']);
      }}

      if (['input', 'textbox', 'text box', 'field'].includes(raw)) {{
        add(['input', 'textbox', 'text box', 'field', 'textarea', 'keyword',
          'search', 'query', '输入', '輸入', '搜索', '搜尋', '关键词', '关键字']);
      }}

      if (['next', 'pagination', 'page'].includes(raw)) {{
        add(['next', 'pagination', 'page', '下一页', '下一頁', '下页', '下頁',
          '更多', '加载更多', '載入更多', 'load more']);
      }}

      if (['login', 'sign in', 'signin'].includes(raw)) {{
        add(['login', 'sign in', 'signin', 'log in', '登录', '登入', '登陆']);
      }}

      return Array.from(aliases).filter(Boolean);
    }};

    const queryTerms = queryAliases(query);

    const classifyActionLikelihood = (el, searchable) => {{
      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const role = roleFromTag(el);
      const text = String(searchable || '').toLowerCase();

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
      if (!queryMatches(searchable)) continue;

      const actionLikelihood = classifyActionLikelihood(el, searchable);

      candidates.push({{
        tag,
        role,
        action_likelihood: actionLikelihood,
        text,
        accessible_name: name,
        aria_label: normalize(el.getAttribute('aria-label') || ''),
        testid: normalize(testid),
        input_type: normalize(type),
        name: normalize(nameAttr),
        placeholder: normalize(placeholder),
        href: normalize(el.getAttribute('href') || '', 180),
        disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
        bbox: [
          Math.round(rect.x),
          Math.round(rect.y),
          Math.round(rect.width),
          Math.round(rect.height)
        ],
        selector_hint: buildSelectorHint(el),
        score: scoreElement(el, rect, text, name, actionLikelihood)
      }});
    }}

    candidates.sort((a, b) => {{
      if (b.score !== a.score) return b.score - a.score;
      if (a.bbox[1] !== b.bbox[1]) return a.bbox[1] - b.bbox[1];
      return a.bbox[0] - b.bbox[0];
    }});

    const elements = candidates.slice(0, maxItems).map((item, index) => {{
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
      total_candidates: candidates.length,
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
) -> str:
    """Build browser_run_code_unsafe JavaScript for compact repeated-card probing."""

    params: Dict[str, Any] = {
        "max_cards": _clamp_int(max_cards, default=20, minimum=1, maximum=50),
        "viewport_only": bool(viewport_only),
        "include_buttons": bool(include_buttons),
        "query": str(query or "").strip().lower(),
        "site_profiles": site_profiles or [],
        "selector_cache_records": selector_cache_records or [],
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

    const buildSelectorHint = (el) => {
      if (!el || !el.tagName) return '';

      const tag = el.tagName.toLowerCase();

      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy');
      if (testid) {
        if (el.getAttribute('data-testid')) return `[data-testid="${attrEscape(testid)}"]`;
        if (el.getAttribute('data-test')) return `[data-test="${attrEscape(testid)}"]`;
        return `[data-cy="${attrEscape(testid)}"]`;
      }

      const id = el.getAttribute('id');
      if (id) return `#${cssEscape(id)}`;

      const stableClasses = normalize(el.getAttribute('class') || '', 160)
        .split(' ')
        .filter(Boolean)
        .filter((item) => !/^(active|selected|disabled|hover|focus|show|hide|open|closed)$/i.test(item))
        .slice(0, 3);

      const simple = stableClasses.length
        ? `${tag}${stableClasses.map((item) => `.${cssEscape(item)}`).join('')}`
        : tag;

      try {
        if (document.querySelectorAll(simple).length === 1) {
          return simple;
        }
      } catch (_) {
        // Fall through to a full nth-of-type path when a selector cannot be tested.
      }

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
          if (node.getAttribute('data-testid')) {
            path.unshift(`[data-testid="${attrEscape(nodeTestid)}"]`);
          } else if (node.getAttribute('data-test')) {
            path.unshift(`[data-test="${attrEscape(nodeTestid)}"]`);
          } else {
            path.unshift(`[data-cy="${attrEscape(nodeTestid)}"]`);
          }
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

      return path.join(' > ');
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

    const extractPrimaryLink = (root) => {
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

      if (!link) {
        return {
          text: '',
          href: '',
          selector_hint: ''
        };
      }

      return {
        text: textOf(link, 180),
        href: normalize(link.href || link.getAttribute('href') || '', 260),
        selector_hint: buildSelectorHint(link)
      };
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
          selector_hint: buildSelectorHint(priceEl)
        };
      }

      const fromText = normalizePriceValue(rootText);

      return {
        value: fromText,
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

    const extractRating = (rootText, root) => {
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
          selector_hint: buildSelectorHint(ratingEl)
        };
      }

      const ratingText = `${textOf(ratingEl, 120)} ${rootText}`;
      const match = ratingText.match(
        /(?:\d(?:\.\d)?\s*(?:out of|\/)\s*5)|(?:\d(?:\.\d)?\s*★)|(?:rating\s*[:\-]?\s*\d(?:\.\d)?)/i
      );

      return {
        value: match ? normalize(match[0], 80) : '',
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

          return {
            text,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            href: normalize(el.href || el.getAttribute('href') || '', 260),
            selector_hint: buildSelectorHint(el),
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
      if (elementLooksLikeChrome(el)) return null;

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
        if (elementLooksLikeChrome(node)) return null;

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
      if (data.source) score += 6;
      if (data.summary && String(data.summary).length >= 40) score += 14;
      if (data.has_image) score += 12;
      if (buttons.length > 0) score += 8;

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

        if (elementLooksLikeChrome(el)) continue;

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
        const rating = extractRating(rootText, el);
        let primaryLink = extractPrimaryLink(el);
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
        const source = extractSource(rootText, el, primaryLink);
        const summary = extractSummary(rootText, el, title.value);
        const reviewCount = extractReviewCount(rootText);
        const availability = extractAvailability(rootText);
        const imagePresent = hasImage(el);

        const fields = {
          title: title.value,
          price: price.value,
          rating: rating.value,
          review_count: reviewCount,
          availability,
          author: author.value,
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
          fields.source,
          fields.summary,
          fields.primary_link,
          fields.has_image,
          fields.buttons && fields.buttons.length
        ].filter(Boolean).length;

        if (fieldCount < 2) continue;

        const data = {
          selector_source: selectorSource,
          selector_hint: buildSelectorHint(el),
          title: title.value,
          title_selector_hint: title.selector_hint,
          price: price.value,
          price_selector_hint: price.selector_hint,
          rating: rating.value,
          rating_selector_hint: rating.selector_hint,
          review_count: reviewCount,
          availability,
          author: author.value,
          author_selector_hint: author.selector_hint,
          source: source.value,
          source_selector_hint: source.selector_hint,
          summary: summary.value,
          summary_selector_hint: summary.selector_hint,
          primary_link: primaryLink.href,
          primary_link_text: primaryLink.text,
          primary_link_selector_hint: primaryLink.selector_hint,
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
      if (item.data.source) score += 4;
      if (item.data.summary) score += 10;
      if (item.data.buttons && item.data.buttons.length) score += 12;
      if (item.data.has_image) score += 8;
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

    const cards = selected.map((item, index) => ({
      id: `card_${index + 1}`,
      group_count: item.groupCount,
      ...item.data
    }));

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