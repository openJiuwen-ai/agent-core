# coding: utf-8
"""Shared browser-side page index used by compact semantic probes."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


PAGE_INDEX_STATE_KEY = "__openjiuwenPageStructureIndexV3"
PAGE_INDEX_SCHEMA_VERSION = 3
PAGE_INDEX_RUNTIME_KEY = "__openjiuwenPageStructureRuntimeV3"
PAGE_INDEX_RUNTIME_MISSING_ERROR = "page_index_runtime_missing"


_PAGE_INDEX_INSTALL_TEMPLATE = r"""
async (page) => {
  return await page.evaluate(() => {
    const RUNTIME_KEY = '__openjiuwenPageStructureRuntimeV3';
    const STATE_KEY = '__openjiuwenPageStructureIndexV3';
    const INITIAL_CONFIGURATION = __OPENJIUWEN_PAGE_INDEX_INITIAL_CONFIGURATION__;
    const existingRuntime = window[RUNTIME_KEY];
    if (existingRuntime && existingRuntime.schemaVersion === 3) {
      const configurationResult = INITIAL_CONFIGURATION
        ? existingRuntime.configure(INITIAL_CONFIGURATION)
        : {configuration_revision: existingRuntime.configurationRevision || ''};
      return {
        ok: true,
        error: null,
        already_installed: true,
        schema_version: 3,
        configuration_revision: configurationResult.configuration_revision || '',
      };
    }
    const SCHEMA_VERSION = 3;
    const MAX_INDEX_NODES = 12000;
    const REPRESENTATIVE_LIMIT = 3;
    const MAX_GROUP_ATTEMPTS = 3;
    const MAX_GROUPS = 40;
    const MAX_TEXT = 600;
    const EXCLUDED_TAGS = new Set([
      'script', 'style', 'noscript', 'template', 'meta', 'link', 'head',
    ]);
    const INTERACTIVE_ROLES = new Set([
      'button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox',
      'radio', 'switch', 'slider', 'spinbutton', 'option', 'menuitem',
      'tab', 'treeitem',
    ]);
    const CHROME_ROLES = new Set([
      'banner', 'contentinfo', 'navigation', 'complementary',
    ]);
    const CHROME_WORDS = [
      'navbar', 'breadcrumb', 'toolbar', 'sidebar', 'footer', 'header',
      'passport', 'login-panel', 'site-menu', 'navigation',
    ];
    const PRICE_RE = new RegExp(
      '(?:S\\$|US\\$|A\\$|HK\\$|C\\$|NZ\\$|\\$|£|€|¥|￥|₹|₩|₽|Rp|RM|'
      + 'SGD|USD|IDR|MYR|CNY|RMB|JPY|AUD|CAD)\\s?\\d[\\d,.]*(?:\\.\\d+)?'
      + '|\\d[\\d,.]*(?:\\.\\d+)?\\s?(?:SGD|USD|IDR|MYR|CNY|RMB|JPY|AUD|CAD|円)',
      'i',
    );
    const RATING_RE = /(?:\b[0-5](?:\.\d)?\s*(?:\/\s*5|stars?)\b|\b(?:one|two|three|four|five)\s+stars?\b)/i;
    const AVAILABILITY_RE = /\b(?:in stock|out of stock|available|sold out|limited availability)\b/i;
    const REVIEW_COUNT_RE = /\b\d[\d,.]*\s*(?:reviews?|ratings?)\b/i;

    const now = () => performance.now();
    const normalize = (value, limit = 180) => String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, limit);
    const lower = (value, limit = 180) => normalize(value, limit).toLowerCase();
    const clamp = (value, minimum, maximum, fallback) => {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.max(minimum, Math.min(maximum, Math.trunc(parsed)));
    };
    const unique = (items, limit = 50) => {
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
    const hashText = (value) => {
      const text = String(value || '');
      let hash = 2166136261;
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(36);
    };
    const attrEscape = (value) => String(value || '')
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"');
    const cssEscape = (value) => {
      const raw = String(value || '');
      if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(raw);
      }
      return raw.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    };
    const routeSignature = () => {
      let path = String(location.pathname || '/').replace(/\d+/g, '*');
      path = path.replace(/\/+/g, '/');
      if (path !== '/' && path.endsWith('/')) path = path.slice(0, -1);
      return path || '/';
    };
    const directText = (element) => {
      const parts = [];
      for (const child of Array.from(element.childNodes || [])) {
        if (child.nodeType === Node.TEXT_NODE) parts.push(child.nodeValue || '');
      }
      return normalize(parts.join(' '), 220);
    };
    const roleFromElement = (element) => {
      const explicit = lower(element.getAttribute('role'), 60);
      if (explicit) return explicit;
      const tag = element.tagName.toLowerCase();
      const type = lower(element.getAttribute('type'), 40);
      if (tag === 'button') return 'button';
      if (tag === 'a' && element.hasAttribute('href')) return 'link';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (element.isContentEditable) return 'textbox';
      if (tag !== 'input') return '';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'search') return 'searchbox';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      return 'textbox';
    };
    const buildSelectorHint = (element) => {
      const tag = element.tagName.toLowerCase();
      const testId = element.getAttribute('data-testid');
      const dataTest = element.getAttribute('data-test');
      const dataCy = element.getAttribute('data-cy');
      if (testId) return `[data-testid="${attrEscape(testId)}"]`;
      if (dataTest) return `[data-test="${attrEscape(dataTest)}"]`;
      if (dataCy) return `[data-cy="${attrEscape(dataCy)}"]`;
      const id = element.getAttribute('id');
      if (id) return `#${cssEscape(id)}`;
      const aria = element.getAttribute('aria-label');
      if (aria) return `${tag}[aria-label="${attrEscape(aria)}"]`;
      const name = element.getAttribute('name');
      if (name) return `${tag}[name="${attrEscape(name)}"]`;
      const placeholder = element.getAttribute('placeholder');
      if (placeholder) return `${tag}[placeholder="${attrEscape(placeholder)}"]`;

      const path = [];
      let node = element;
      let depth = 0;
      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 8) {
        const nodeTag = node.tagName.toLowerCase();
        const nodeId = node.getAttribute('id');
        if (nodeId) {
          path.unshift(`#${cssEscape(nodeId)}`);
          break;
        }
        let nth = 1;
        let previous = node.previousElementSibling;
        while (previous) {
          if (previous.tagName.toLowerCase() === nodeTag) nth += 1;
          previous = previous.previousElementSibling;
        }
        const classes = unique(
          String(node.getAttribute('class') || '')
            .split(/\s+/)
            .filter((item) => item && !/^(active|selected|disabled|open|closed)$/i.test(item)),
          2,
        ).map((item) => `.${cssEscape(item)}`).join('');
        path.unshift(`${nodeTag}${classes}:nth-of-type(${nth})`);
        node = node.parentElement;
        depth += 1;
      }
      return path.join(' > ');
    };
    const mutationIsMeaningful = (mutation) => {
      if (mutation.type === 'childList' || mutation.type === 'characterData') return true;
      if (mutation.type !== 'attributes') return false;
      return [
        'aria-expanded', 'aria-hidden', 'aria-disabled', 'disabled', 'hidden',
        'style', 'class', 'value', 'href', 'role',
      ].includes(String(mutation.attributeName || ''));
    };
    const createDocumentId = () => `doc_${hashText([
      location.href,
      performance.timeOrigin || 0,
      Date.now(),
      Math.random(),
    ].join('|'))}`;
    const ensureState = () => {
      let state = window[STATE_KEY];
      if (!state || state.schemaVersion !== SCHEMA_VERSION) {
        state = {
          schemaVersion: SCHEMA_VERSION,
          documentId: createDocumentId(),
          domVersion: 1,
          index: null,
          observer: null,
          schemaCache: Object.create(null),
          configuration: {
            revision: '',
            site_profiles: [],
            selector_cache_records: [],
          },
          lifecycle: {
            restoredFromBfcache: false,
            pageshowCount: 0,
          },
          url: location.href,
        };
        window.addEventListener('pageshow', (event) => {
          state.lifecycle.pageshowCount += 1;
          state.lifecycle.restoredFromBfcache = Boolean(event.persisted);
        });
        if (document.documentElement && typeof MutationObserver === 'function') {
          state.observer = new MutationObserver((mutations) => {
            if (!mutations.some(mutationIsMeaningful)) return;
            state.domVersion += 1;
            state.index = null;
          });
          state.observer.observe(document.documentElement, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeFilter: [
              'aria-expanded', 'aria-hidden', 'aria-disabled', 'disabled',
              'hidden', 'style', 'class', 'value', 'href', 'role',
            ],
          });
        }
        window[STATE_KEY] = state;
      }
      if (state.url !== location.href) {
        state.url = location.href;
        state.domVersion += 1;
        state.index = null;
        state.lifecycle.restoredFromBfcache = false;
      }
      return state;
    };
    const configure = (configuration) => {
      const state = ensureState();
      const candidate = configuration && typeof configuration === 'object'
        ? configuration
        : {};
      const revision = String(candidate.revision || '');
      if (!revision) {
        return {
          ok: false,
          error: 'page_index_configuration_revision_missing',
          configuration_revision: state.configuration.revision || '',
        };
      }
      if (state.configuration.revision !== revision) {
        state.configuration = {
          revision,
          site_profiles: Array.isArray(candidate.site_profiles)
            ? candidate.site_profiles
            : [],
          selector_cache_records: Array.isArray(candidate.selector_cache_records)
            ? candidate.selector_cache_records
            : [],
        };
        state.schemaCache = Object.create(null);
      }
      return {
        ok: true,
        error: null,
        configuration_revision: state.configuration.revision,
      };
    };
    const elementDescriptor = (element) => lower([
      element.tagName || '',
      element.getAttribute('id') || '',
      element.getAttribute('class') || '',
      element.getAttribute('role') || '',
      element.getAttribute('aria-label') || '',
      element.getAttribute('title') || '',
      element.getAttribute('data-testid') || '',
      element.getAttribute('data-test') || '',
    ].join(' '), 360);
    const looksLikePageChrome = (node) => {
      if (!node) return true;
      if (CHROME_ROLES.has(node.role)) return true;
      const descriptor = `${node.descriptor} ${node.selectorHint}`.toLowerCase();
      return CHROME_WORDS.some((word) => descriptor.includes(word));
    };
    const dimensionsSimilar = (nodes) => {
      if (!nodes.length) return 0;
      const widths = nodes.map((node) => Math.max(1, node.rect.width));
      const heights = nodes.map((node) => Math.max(1, node.rect.height));
      const average = (items) => items.reduce((sum, item) => sum + item, 0) / items.length;
      const meanWidth = average(widths);
      const meanHeight = average(heights);
      const deviation = average(nodes.map((node, index) => {
        const widthDelta = Math.abs(widths[index] - meanWidth) / meanWidth;
        const heightDelta = Math.abs(heights[index] - meanHeight) / meanHeight;
        return (widthDelta + heightDelta) / 2;
      }));
      return Math.max(0, 1 - deviation);
    };
    const canonicalGroupId = (index, memberIds) => {
      const ids = memberIds.slice().sort((left, right) => left - right);
      const members = ids.map((id) => index.nodes[id]).filter(Boolean);
      const parentIds = Array.from(new Set(members.map((node) => node.parentId)))
        .sort((left, right) => Number(left || -1) - Number(right || -1));
      const signatures = Array.from(new Set(members.map((node) => node.structuralSignature)))
        .sort();
      return `group_${hashText([
        index.documentId,
        parentIds.join(','),
        ids.join(','),
        signatures.join(','),
      ].join('|'))}`;
    };
    const scoreGroup = (index, memberIds, signatureKind, source = 'repeated_group') => {
      const members = memberIds.map((id) => index.nodes[id]).filter(Boolean);
      if (members.length < 2) return null;
      const parentIds = new Set(members.map((node) => node.parentId));
      const uniqueText = new Set(members.map((node) => lower(node.aggregateText, 220)).filter(Boolean));
      const averageDescendants = members.reduce((sum, node) => sum + node.descendantCount, 0) / members.length;
      const averageArea = members.reduce((sum, node) => sum + node.rect.width * node.rect.height, 0) / members.length;
      const featureCounts = {
        heading: members.filter((node) => node.firstHeadingText).length,
        link: members.filter((node) => node.firstLinkHref).length,
        price: members.filter((node) => node.firstPriceText).length,
        interactive: members.filter((node) => node.interactiveCount > 0).length,
        image: members.filter((node) => node.imageCount > 0).length,
      };
      let score = Math.min(42, members.length * 7);
      score += dimensionsSimilar(members) * 14;
      score += uniqueText.size >= Math.min(3, members.length) ? 10 : -8;
      score += featureCounts.heading >= Math.ceil(members.length / 2) ? 12 : 0;
      score += featureCounts.link >= Math.ceil(members.length / 2) ? 10 : 0;
      score += featureCounts.price >= Math.ceil(members.length / 2) ? 16 : 0;
      score += featureCounts.interactive >= Math.ceil(members.length / 2) ? 8 : 0;
      score += featureCounts.image >= Math.ceil(members.length / 2) ? 6 : 0;
      score += averageDescendants >= 3 && averageDescendants <= 300 ? 10 : -10;
      score += averageArea >= 800 ? 6 : -12;
      score += parentIds.size === 1 ? 8 : 0;
      score += source === 'cache' ? 18 : 0;
      score += source === 'site_profile' ? 14 : 0;
      score += signatureKind === 'exact' ? 5 : 0;
      if (members.every(looksLikePageChrome)) score -= 60;
      if (members.every((node) => node.aggregateText.length < 3)) score -= 30;
      return {
        id: canonicalGroupId(index, memberIds),
        source,
        signatureKind,
        parentId: parentIds.size === 1 ? members[0].parentId : null,
        memberIds,
        memberCount: members.length,
        dominantSignature: members[0].structuralSignature,
        shapeSignature: members[0].shapeSignature,
        score,
        confidence: Math.max(0, Math.min(1, score / 100)),
        averageDescendants,
        averageArea,
        uniqueTextCount: uniqueText.size,
        featureCounts,
      };
    };
    const groupsOverlap = (index, left, right) => {
      const rightRoots = new Set(right.memberIds);
      let overlap = 0;
      for (const memberId of left.memberIds) {
        let current = memberId;
        let depth = 0;
        while (current != null && depth < 12) {
          if (rightRoots.has(current)) {
            overlap += 1;
            break;
          }
          const node = index.nodes[current];
          current = node ? node.parentId : null;
          depth += 1;
        }
      }
      return overlap / Math.max(1, Math.min(left.memberIds.length, right.memberIds.length));
    };
    const discoverRepeatedGroups = (index) => {
      const candidates = [];
      for (const parentId of Object.keys(index.childrenByParent)) {
        const childIds = index.childrenByParent[parentId]
          .map((id) => Number(id))
          .filter((id) => index.nodes[id] && index.nodes[id].visible);
        if (childIds.length < 2) continue;
        const exactBuckets = new Map();
        const shapeBuckets = new Map();
        for (const childId of childIds) {
          const node = index.nodes[childId];
          const exact = node.structuralSignature;
          const shape = node.shapeSignature;
          if (!exactBuckets.has(exact)) exactBuckets.set(exact, []);
          if (!shapeBuckets.has(shape)) shapeBuckets.set(shape, []);
          exactBuckets.get(exact).push(childId);
          shapeBuckets.get(shape).push(childId);
        }
        const exactMembers = new Set();
        for (const ids of exactBuckets.values()) {
          if (ids.length < 2) continue;
          ids.forEach((id) => exactMembers.add(id));
          const group = scoreGroup(index, ids, 'exact');
          if (group) candidates.push(group);
        }
        for (const ids of shapeBuckets.values()) {
          if (ids.length < 2) continue;
          const novel = ids.filter((id) => !exactMembers.has(id));
          const chosen = novel.length >= 2 ? novel : ids;
          const group = scoreGroup(index, chosen, 'approximate');
          if (group) candidates.push(group);
        }
      }
      candidates.sort((left, right) => right.score - left.score);
      const selected = [];
      for (const candidate of candidates) {
        if (candidate.score < 18) continue;
        const conflicting = selected.some((existing) => {
          const ratio = Math.max(
            groupsOverlap(index, candidate, existing),
            groupsOverlap(index, existing, candidate),
          );
          return ratio >= 0.65 && Math.abs(candidate.memberCount - existing.memberCount) <= 2;
        });
        if (conflicting) continue;
        selected.push(candidate);
        if (selected.length >= MAX_GROUPS) break;
      }
      return selected;
    };
    const registerGroupContext = (index, group, overwrite = true) => {
      if (!group) return;
      group.memberIds.forEach((memberId, itemIndex) => {
        if (!overwrite && index.memberRootContext[memberId]) return;
        index.memberRootContext[memberId] = {
          groupId: group.id,
          itemIndex,
          score: group.score,
          source: group.source,
        };
      });
    };
    const buildIndex = (state) => {
      const startedAt = now();
      const root = document.body || document.documentElement;
      const nodes = [];
      const childrenByParent = Object.create(null);
      const interactiveIds = [];
      const elementToId = new WeakMap();
      let truncated = false;
      const walker = document.createTreeWalker(
        root,
        NodeFilter.SHOW_ELEMENT,
        {
          acceptNode: (element) => {
            const tag = element.tagName.toLowerCase();
            if (EXCLUDED_TAGS.has(tag)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
          },
        },
      );
      const elements = [root];
      let current = walker.nextNode();
      while (current) {
        if (elements.length >= MAX_INDEX_NODES) {
          truncated = true;
          break;
        }
        elements.push(current);
        current = walker.nextNode();
      }
      for (const element of elements) {
        const id = nodes.length;
        elementToId.set(element, id);
        let parent = element.parentElement;
        while (parent && !elementToId.has(parent)) parent = parent.parentElement;
        const parentId = parent ? elementToId.get(parent) : null;
        const rectRaw = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const visible = Boolean(
          rectRaw.width >= 2
          && rectRaw.height >= 2
          && style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity || 1) !== 0
          && element.getAttribute('aria-hidden') !== 'true'
          && !element.hidden
        );
        const inViewport = Boolean(
          visible
          && rectRaw.bottom >= 0
          && rectRaw.right >= 0
          && rectRaw.top <= window.innerHeight
          && rectRaw.left <= window.innerWidth
        );
        const tag = element.tagName.toLowerCase();
        const role = roleFromElement(element);
        const type = lower(element.getAttribute('type'), 40);
        const editable = Boolean(
          element.isContentEditable
          || tag === 'textarea'
          || tag === 'select'
          || (tag === 'input' && !['button', 'submit', 'reset', 'image'].includes(type))
        );
        const nativeInteractive = Boolean(
          tag === 'button'
          || tag === 'select'
          || tag === 'textarea'
          || tag === 'input'
          || (tag === 'a' && element.hasAttribute('href'))
        );
        const focusable = element.tabIndex >= 0;
        const clickable = Boolean(
          nativeInteractive
          || INTERACTIVE_ROLES.has(role)
          || focusable
          || typeof element.onclick === 'function'
          || style.cursor === 'pointer'
        );
        const disabled = Boolean(
          element.disabled
          || element.getAttribute('aria-disabled') === 'true'
        );
        const interactive = visible && !disabled && (clickable || editable);
        const ownText = normalize([
          directText(element),
          element.getAttribute('aria-label') || '',
          element.getAttribute('title') || '',
          element.getAttribute('placeholder') || '',
          element.getAttribute('alt') || '',
          ['input', 'textarea', 'select'].includes(tag) ? element.value || '' : '',
        ].join(' '), 240);
        const classTokens = unique(
          String(element.getAttribute('class') || '').split(/\s+/),
          8,
        );
        const node = {
          id,
          element,
          parentId,
          childIds: [],
          tag,
          role,
          type,
          ownText,
          aggregateText: ownText,
          descriptor: elementDescriptor(element),
          classTokens,
          selectorHint: buildSelectorHint(element),
          href: normalize(element.href || element.getAttribute('href') || '', 300),
          visible,
          inViewport,
          disabled,
          editable,
          focusable,
          clickable,
          interactive,
          isHeading: /^h[1-6]$/.test(tag) || role === 'heading',
          rect: {
            x: Math.round(rectRaw.x),
            y: Math.round(rectRaw.y),
            width: Math.round(rectRaw.width),
            height: Math.round(rectRaw.height),
            top: Math.round(rectRaw.top),
            left: Math.round(rectRaw.left),
            right: Math.round(rectRaw.right),
            bottom: Math.round(rectRaw.bottom),
          },
          descendantCount: 1,
          interactiveCount: interactive ? 1 : 0,
          imageCount: tag === 'img' ? 1 : 0,
          firstHeadingText: '',
          firstLinkText: role === 'link' ? ownText : '',
          firstLinkHref: role === 'link' ? normalize(element.href || element.getAttribute('href') || '', 300) : '',
          firstPriceText: PRICE_RE.test(ownText) ? normalize((ownText.match(PRICE_RE) || [''])[0], 80) : '',
          structuralSignature: '',
          shapeSignature: '',
        };
        nodes.push(node);
        if (parentId != null) {
          nodes[parentId].childIds.push(id);
          if (!childrenByParent[parentId]) childrenByParent[parentId] = [];
          childrenByParent[parentId].push(id);
        }
        if (interactive) interactiveIds.push(id);
      }
      for (let index = nodes.length - 1; index >= 0; index -= 1) {
        const node = nodes[index];
        const children = node.childIds.map((id) => nodes[id]);
        const textParts = [node.ownText];
        const childSignatures = [];
        const childShapeCounts = new Map();
        for (const child of children) {
          if (child.aggregateText) textParts.push(child.aggregateText);
          node.descendantCount += child.descendantCount;
          node.interactiveCount += child.interactiveCount;
          node.imageCount += child.imageCount;
          if (!node.firstHeadingText && child.firstHeadingText) {
            node.firstHeadingText = child.firstHeadingText;
          }
          if (!node.firstLinkText && child.firstLinkText) {
            node.firstLinkText = child.firstLinkText;
            node.firstLinkHref = child.firstLinkHref;
          }
          if (!node.firstPriceText && child.firstPriceText) {
            node.firstPriceText = child.firstPriceText;
          }
          childSignatures.push(child.structuralSignature);
          const shape = `${child.tag}:${child.role || '-'}:${child.interactive ? 'i' : '-'}`;
          childShapeCounts.set(shape, (childShapeCounts.get(shape) || 0) + 1);
        }
        node.aggregateText = normalize(textParts.join(' '), MAX_TEXT);
        if (node.isHeading) node.firstHeadingText = node.ownText || node.aggregateText;
        const flags = [
          node.interactive ? 'i' : '-',
          node.editable ? 'e' : '-',
          node.isHeading ? 'h' : '-',
          node.href ? 'l' : '-',
          node.imageCount ? 'm' : '-',
        ].join('');
        const orderedChildren = childSignatures.slice(0, 24).join(',');
        node.structuralSignature = hashText(
          `${node.tag}|${node.role}|${node.type}|${flags}|${orderedChildren}`,
        );
        const shapeParts = Array.from(childShapeCounts.keys())
          .sort((left, right) => left.localeCompare(right))
          .slice(0, 16)
          .join(',');
        const descendantBucket = Math.min(8, Math.ceil(node.descendantCount / 10));
        node.shapeSignature = hashText(
          `${node.tag}|${node.role}|${flags}|${descendantBucket}|${shapeParts}`,
        );
      }
      const index = {
        schemaVersion: SCHEMA_VERSION,
        documentId: state.documentId,
        url: location.href,
        title: document.title,
        domVersion: state.domVersion,
        scrollX: Math.round(window.scrollX),
        scrollY: Math.round(window.scrollY),
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        nodes,
        childrenByParent,
        interactiveIds,
        elementToId,
        truncated,
        groups: [],
        memberRootContext: Object.create(null),
        buildMs: 0,
      };
      index.groups = discoverRepeatedGroups(index);
      for (const group of index.groups) registerGroupContext(index, group, false);
      index.buildMs = Math.round((now() - startedAt) * 100) / 100;
      return index;
    };
    const ensurePageIndex = () => {
      const state = ensureState();
      const cacheMatches = Boolean(
        state.index
        && state.index.url === location.href
        && state.index.domVersion === state.domVersion
        && state.index.scrollX === Math.round(window.scrollX)
        && state.index.scrollY === Math.round(window.scrollY)
        && state.index.viewportWidth === window.innerWidth
        && state.index.viewportHeight === window.innerHeight
      );
      if (cacheMatches) return {state, index: state.index, cacheHit: true};
      state.index = buildIndex(state);
      return {state, index: state.index, cacheHit: false};
    };
    const queryAliases = (value) => {
      const raw = lower(value, 120);
      if (!raw) return [];
      const aliases = new Set([raw]);
      const add = (items) => items.forEach((item) => aliases.add(item));
      if (['search', 'find', 'query', 'keyword', 'keywords'].includes(raw)) {
        add([
          'search', 'find', 'query', 'keyword', 'keywords', 'searchbox',
          '搜索', '搜尋', '查询', '查找', '关键词', '关键字', '検索',
        ]);
      }
      if (['input', 'textbox', 'text box', 'field'].includes(raw)) {
        add([
          'input', 'textbox', 'text box', 'field', 'textarea', 'search',
          '输入', '輸入', '搜索', '搜尋', '关键词', '关键字',
        ]);
      }
      if (['next', 'pagination', 'page'].includes(raw)) {
        add([
          'next', 'pagination', 'page', 'load more', '下一页', '下一頁',
          '更多', '加载更多', '載入更多',
        ]);
      }
      if (['login', 'sign in', 'signin'].includes(raw)) {
        add(['login', 'sign in', 'signin', 'log in', '登录', '登入', '登陆']);
      }
      return Array.from(aliases).filter(Boolean);
    };
    const actionLikelihood = (node, searchable) => {
      const text = lower(searchable, 500);
      if (
        node.type === 'search'
        || node.role === 'searchbox'
        || /\b(search|query|keyword)\b/i.test(text)
        || /(搜索|搜尋|查询|查找|关键词|关键字|検索)/.test(text)
      ) return 'search';
      if (node.editable || ['textbox', 'combobox'].includes(node.role)) return 'input';
      if (/\b(next|pagination|page|load more)\b/i.test(text)) return 'pagination';
      if (/\b(login|sign in|signin|log in)\b/i.test(text)) return 'login';
      if (/\b(filter|sort|category)\b/i.test(text)) return 'filter';
      if (/\b(cart|basket|buy|checkout)\b/i.test(text)) return 'commerce';
      return node.role || node.tag;
    };
    const findContainingGroup = (index, nodeId) => {
      let current = nodeId;
      let depth = 0;
      while (current != null && depth < 16) {
        const context = index.memberRootContext[current];
        if (context) return {context, root: index.nodes[current]};
        const node = index.nodes[current];
        current = node ? node.parentId : null;
        depth += 1;
      }
      return null;
    };
    const queryInteractives = (index, params) => {
      const startedAt = now();
      const maxItems = clamp(params.max_items, 1, 100, 50);
      const viewportOnly = params.viewport_only !== false;
      const query = lower(params.query, 160);
      const terms = queryAliases(query);
      const scopeGroupId = String(params.scope_group_id || '').trim();
      const scopeItemIndexRaw = Number(params.scope_item_index);
      const scopeItemIndex = Number.isInteger(scopeItemIndexRaw) && scopeItemIndexRaw >= 0
        ? scopeItemIndexRaw
        : null;
      const candidates = [];
      for (const nodeId of index.interactiveIds) {
        const node = index.nodes[nodeId];
        if (!node || !node.visible || node.disabled) continue;
        if (viewportOnly && !node.inViewport) continue;
        const groupMatch = findContainingGroup(index, nodeId);
        if (scopeGroupId && (!groupMatch || groupMatch.context.groupId !== scopeGroupId)) continue;
        if (scopeItemIndex !== null && (
          !groupMatch || groupMatch.context.itemIndex !== scopeItemIndex
        )) continue;
        const liveValue = ['input', 'textarea', 'select'].includes(node.tag)
          ? normalize(node.element.value || '', 180)
          : '';
        const text = liveValue || node.ownText || node.aggregateText;
        const groupText = groupMatch ? groupMatch.root.aggregateText : '';
        const searchable = lower([
          text,
          node.role,
          node.tag,
          node.type,
          node.descriptor,
          node.selectorHint,
          groupText,
        ].join(' '), 1400);
        if (terms.length && !terms.some((term) => searchable.includes(term))) continue;
        const likelihood = actionLikelihood(node, searchable);
        let score = 0;
        if (node.element.getAttribute('data-testid')) score += 40;
        if (node.element.getAttribute('data-test') || node.element.getAttribute('data-cy')) score += 30;
        if (node.element.getAttribute('aria-label')) score += 25;
        if (node.tag === 'button') score += 25;
        if (['input', 'select', 'textarea'].includes(node.tag)) score += 22;
        if (node.tag === 'a') score += 18;
        if (node.role) score += 15;
        if (likelihood === 'search') score += 35;
        if (query) score += 20;
        if (node.inViewport) score += 15;
        const groupContext = groupMatch ? {
          group_id: groupMatch.context.groupId,
          item_index: groupMatch.context.itemIndex,
          title: groupMatch.root.firstHeadingText || groupMatch.root.firstLinkText || '',
          price: groupMatch.root.firstPriceText || '',
          text_preview: normalize(groupMatch.root.aggregateText, 180),
        } : null;
        candidates.push({
          id: `interactive_${node.id}`,
          node_id: node.id,
          tag: node.tag,
          role: node.role,
          input_type: node.type,
          text: normalize(text, 180),
          name: normalize(
            node.element.getAttribute('aria-label')
            || node.element.getAttribute('title')
            || node.element.getAttribute('placeholder')
            || node.element.getAttribute('name')
            || '',
            180,
          ),
          action_likelihood: likelihood,
          selector_hint: node.selectorHint,
          disabled: node.disabled,
          editable: node.editable,
          bbox: node.rect,
          group_context: groupContext,
          score,
        });
      }
      candidates.sort((left, right) => right.score - left.score);
      return {
        elements: candidates.slice(0, maxItems),
        totalCandidates: candidates.length,
        queryMs: Math.round((now() - startedAt) * 100) / 100,
      };
    };
    const selectorList = (records, name) => unique(
      records.flatMap((record) => {
        const selectors = record && record.selectors ? record.selectors : record;
        return Array.isArray(selectors && selectors[name]) ? selectors[name] : [];
      }),
      30,
    );
    const matchingProfiles = (profiles) => {
      const hostname = lower(location.hostname, 200);
      const path = String(location.pathname || '/');
      return (profiles || []).filter((profile) => {
        const domains = Array.isArray(profile.domains) ? profile.domains : [];
        const domainMatch = domains.some((domain) => {
          const normalized = lower(domain, 200);
          return hostname === normalized || hostname.endsWith(`.${normalized}`);
        });
        if (!domainMatch) return false;
        const patterns = Array.isArray(profile.route_patterns) ? profile.route_patterns : [];
        if (!patterns.length) return true;
        return patterns.some((pattern) => {
          try {
            return new RegExp(String(pattern || '')).test(path);
          } catch (_) {
            return false;
          }
        });
      });
    };
    const matchingCacheRecords = (records) => {
      const hostname = lower(location.hostname, 200);
      const route = routeSignature();
      return (records || []).filter((record) => {
        if (!record || (record.kind || 'card_probe') !== 'card_probe') return false;
        return lower(record.domain, 200) === hostname
          && String(record.route_signature || '/') === route;
      });
    };
    const idsForSelectors = (index, selectors) => {
      const ids = [];
      for (const selector of unique(selectors, 20)) {
        try {
          for (const element of Array.from(document.querySelectorAll(selector)).slice(0, 200)) {
            const id = index.elementToId.get(element);
            if (id != null) ids.push(id);
          }
        } catch (_) {
          // Ignore stale or browser-specific selectors.
        }
      }
      return Array.from(new Set(ids));
    };
    const makeSelectorGroup = (index, ids, source) => {
      if (ids.length < 2) return null;
      const group = scoreGroup(index, ids, source === 'cache' ? 'exact' : 'approximate', source);
      if (!group || group.score < 30) return null;
      const memberSet = new Set(group.memberIds);
      let canonical = null;
      let bestOverlap = 0;
      for (const candidate of index.groups) {
        const overlap = candidate.memberIds.filter((id) => memberSet.has(id)).length;
        const ratio = overlap / Math.max(1, Math.max(group.memberCount, candidate.memberCount));
        if (ratio > bestOverlap) {
          bestOverlap = ratio;
          canonical = candidate;
        }
      }
      if (canonical && bestOverlap >= 0.7) {
        group.id = canonical.id;
        group.canonicalSource = canonical.source;
      }
      return group;
    };
    const descendantIds = (index, rootId, limit = 1200) => {
      const result = [];
      const stack = [rootId];
      while (stack.length && result.length < limit) {
        const id = stack.pop();
        if (id == null) continue;
        result.push(id);
        const node = index.nodes[id];
        if (!node) continue;
        for (let childIndex = node.childIds.length - 1; childIndex >= 0; childIndex -= 1) {
          stack.push(node.childIds[childIndex]);
        }
      }
      return result;
    };
    const stableClassToken = (node) => {
      return (node.classTokens || []).find((token) => {
        return token
          && token.length <= 80
          && !/^(active|selected|disabled|open|closed|hover|focus)$/i.test(token)
          && !/^(css|sc|jsx)-[a-z0-9]+$/i.test(token);
      }) || '';
    };
    const pathStepMatches = (node, step) => {
      if (!node || !step) return false;
      if (step.tag && node.tag !== step.tag) return false;
      if (step.role && node.role !== step.role) return false;
      if (step.type && node.type !== step.type) return false;
      if (step.classToken && !(node.classTokens || []).includes(step.classToken)) return false;
      return true;
    };
    const relativePath = (index, rootId, targetId) => {
      const reversed = [];
      let current = targetId;
      let depth = 0;
      while (current !== rootId && current != null && depth < 16) {
        const node = index.nodes[current];
        if (!node || node.parentId == null) return null;
        const parent = index.nodes[node.parentId];
        if (!parent) return null;
        const childIndex = parent.childIds.indexOf(current);
        if (childIndex < 0) return null;
        const classToken = stableClassToken(node);
        const siblingMatches = parent.childIds
          .map((id) => index.nodes[id])
          .filter((candidate) => pathStepMatches(candidate, {
            tag: node.tag,
            role: node.role,
            type: node.type,
            classToken,
          }));
        reversed.push({
          index: childIndex,
          tag: node.tag,
          role: node.role,
          type: node.type,
          classToken,
          ordinal: Math.max(0, siblingMatches.findIndex((candidate) => candidate.id === node.id)),
        });
        current = node.parentId;
        depth += 1;
      }
      if (current !== rootId) return null;
      return reversed.reverse();
    };
    const resolveRelativePath = (index, rootId, path) => {
      let current = rootId;
      for (const step of path || []) {
        const parent = index.nodes[current];
        if (!parent) return null;
        const indexedChild = parent.childIds[step.index];
        const indexedNode = indexedChild == null ? null : index.nodes[indexedChild];
        if (pathStepMatches(indexedNode, step)) {
          current = indexedNode.id;
          continue;
        }
        const matches = parent.childIds
          .map((id) => index.nodes[id])
          .filter((candidate) => pathStepMatches(candidate, step));
        const fallback = matches[Math.max(0, Number(step.ordinal || 0))];
        if (!fallback) return null;
        current = fallback.id;
      }
      return index.nodes[current] || null;
    };
    const isArticleHref = (href) => {
      const value = lower(href, 400);
      return Boolean(
        value.includes('/article/details/')
        || value.includes('/articles/')
        || value.includes('/post/')
        || value.includes('/posts/')
        || value.includes('/blog/')
        || (value.includes('blog.csdn.net') && value.includes('/article/'))
      );
    };
    const isAuthorProfile = (node) => {
      if (!node) return false;
      const value = `${node.descriptor} ${node.href}`.toLowerCase();
      if (isArticleHref(node.href)) return false;
      return /\b(author|byline|profile|avatar|nickname|user|btm-rt)\b/i.test(value)
        || /\/(?:user|users|profile|people|u)\//i.test(node.href);
    };
    const fieldScore = (field, node) => {
      const text = normalize(node.ownText || node.aggregateText, 420);
      const descriptor = node.descriptor;
      let score = 0;
      if (field === 'title') {
        if (node.isHeading) score += 55;
        if (node.role === 'link') score += 28;
        if (/\b(title|headline|subject|product-name|item-name)\b/i.test(descriptor)) score += 42;
        if (isArticleHref(node.href)) score += 35;
        if (isAuthorProfile(node)) score -= 80;
        if (text.length >= 3 && text.length <= 220) score += 20;
      } else if (field === 'primary_link') {
        if (!node.href) return -1000;
        score += 35;
        if (node.isHeading || isArticleHref(node.href)) score += 45;
        if (/\b(title|headline|subject)\b/i.test(descriptor)) score += 30;
        if (isAuthorProfile(node)) score -= 100;
      } else if (field === 'price') {
        if (PRICE_RE.test(text)) score += 60;
        if (/\b(price|fare|amount|cost|total)\b/i.test(descriptor)) score += 45;
        if (text.length <= 100) score += 15;
      } else if (field === 'rating') {
        if (RATING_RE.test(text)) score += 55;
        if (/\b(rating|star|score|review)\b/i.test(descriptor)) score += 45;
      } else if (field === 'review_count') {
        if (REVIEW_COUNT_RE.test(text)) score += 60;
        if (/\b(review-count|rating-count|reviews|ratings)\b/i.test(descriptor)) score += 40;
      } else if (field === 'availability') {
        if (AVAILABILITY_RE.test(text)) score += 55;
        if (/\b(availability|stock|inventory)\b/i.test(descriptor)) score += 45;
      } else if (field === 'author') {
        if (isAuthorProfile(node)) score += 65;
        if (/\b(author|byline|writer|nickname)\b/i.test(descriptor)) score += 45;
      } else if (field === 'source') {
        if (/\b(source|origin|platform|channel|publisher|site)\b/i.test(descriptor)) score += 55;
      } else if (field === 'summary') {
        if (/\b(summary|snippet|description|abstract|excerpt|content)\b/i.test(descriptor)) score += 55;
        if (text.length >= 40 && text.length <= 500) score += 25;
      }
      if (!node.visible) score -= 80;
      return score;
    };
    const bestFieldNode = (index, rootId, field, selectors) => {
      const root = index.nodes[rootId];
      if (!root) return null;
      for (const selector of selectors || []) {
        try {
          const element = root.element.matches(selector)
            ? root.element
            : root.element.querySelector(selector);
          const id = element ? index.elementToId.get(element) : null;
          if (id != null) return index.nodes[id];
        } catch (_) {
          // Ignore invalid selector and continue with indexed scoring.
        }
      }
      let best = null;
      let bestScore = -1000;
      for (const id of descendantIds(index, rootId)) {
        const node = index.nodes[id];
        const score = fieldScore(field, node);
        if (score > bestScore) {
          best = node;
          bestScore = score;
        }
      }
      return bestScore >= 30 ? best : null;
    };
    const chooseRepresentativeIds = (memberIds) => {
      if (memberIds.length <= REPRESENTATIVE_LIMIT) return memberIds.slice();
      return [
        memberIds[0],
        memberIds[Math.floor(memberIds.length / 2)],
        memberIds[memberIds.length - 1],
      ];
    };
    const inferGroupSchema = (index, group, fieldSelectors) => {
      const representativeIds = chooseRepresentativeIds(group.memberIds);
      const fields = [
        'title', 'primary_link', 'price', 'rating', 'review_count', 'availability',
        'author', 'source', 'summary',
      ];
      const schema = {
        fields: Object.create(null),
        buttonPaths: [],
        representativeIds,
        representativesParsed: representativeIds.length,
      };
      for (const field of fields) {
        const pathCounts = new Map();
        for (const rootId of representativeIds) {
          const node = bestFieldNode(index, rootId, field, fieldSelectors[field] || []);
          if (!node) continue;
          const path = relativePath(index, rootId, node.id);
          if (!path) continue;
          const key = JSON.stringify(path);
          const item = pathCounts.get(key) || {path, count: 0};
          item.count += 1;
          pathCounts.set(key, item);
        }
        const best = Array.from(pathCounts.values())
          .sort((left, right) => right.count - left.count)[0];
        if (best) {
          schema.fields[field] = {
            path: best.path,
            support: best.count,
            confidence: best.count / representativeIds.length,
          };
        }
      }
      const buttonCounts = new Map();
      for (const rootId of representativeIds) {
        for (const id of descendantIds(index, rootId)) {
          const node = index.nodes[id];
          if (!node || !node.interactive) continue;
          const buttonLike = node.role === 'button'
            || node.tag === 'button'
            || (node.tag === 'input' && ['button', 'submit', 'reset'].includes(node.type));
          if (!buttonLike) continue;
          const path = relativePath(index, rootId, id);
          if (!path) continue;
          const key = JSON.stringify(path);
          const item = buttonCounts.get(key) || {path, count: 0};
          item.count += 1;
          buttonCounts.set(key, item);
        }
      }
      schema.buttonPaths = Array.from(buttonCounts.values())
        .filter((item) => item.count >= Math.min(2, representativeIds.length))
        .sort((left, right) => right.count - left.count)
        .slice(0, 6)
        .map((item) => item.path);
      return schema;
    };
    const normalizePriceValue = (value) => {
      const match = normalize(value, 180).match(PRICE_RE);
      return match ? normalize(match[0], 100) : '';
    };
    const ratingClassValue = (node) => {
      if (!node) return '';
      const descriptor = `${node.descriptor} ${node.classTokens.join(' ')}`.toLowerCase();
      const values = [
        ['five', 'Five stars'],
        ['four', 'Four stars'],
        ['three', 'Three stars'],
        ['two', 'Two stars'],
        ['one', 'One star'],
      ];
      for (const [token, label] of values) {
        if (descriptor.includes(token) && descriptor.includes('star')) return label;
      }
      return '';
    };
    const fieldNode = (index, rootId, schema, field) => {
      const config = schema.fields[field];
      if (!config) return null;
      const node = resolveRelativePath(index, rootId, config.path);
      return node && fieldScore(field, node) >= 20 ? node : null;
    };
    const cardFromSchema = (index, group, schema, rootId, itemIndex, includeButtons) => {
      const root = index.nodes[rootId];
      const titleNode = fieldNode(index, rootId, schema, 'title');
      const linkNode = fieldNode(index, rootId, schema, 'primary_link');
      const priceNode = fieldNode(index, rootId, schema, 'price');
      const ratingNode = fieldNode(index, rootId, schema, 'rating');
      const reviewCountNode = fieldNode(index, rootId, schema, 'review_count');
      const availabilityNode = fieldNode(index, rootId, schema, 'availability');
      const authorNode = fieldNode(index, rootId, schema, 'author');
      const sourceNode = fieldNode(index, rootId, schema, 'source');
      const summaryNode = fieldNode(index, rootId, schema, 'summary');
      const title = normalize(
        (titleNode && (titleNode.ownText || titleNode.aggregateText))
        || root.firstHeadingText
        || root.firstLinkText
        || '',
        220,
      );
      const primaryLink = normalize(
        (linkNode && linkNode.href) || root.firstLinkHref || '',
        300,
      );
      const price = normalizePriceValue(
        priceNode && (priceNode.ownText || priceNode.aggregateText),
      ) || root.firstPriceText || normalizePriceValue(root.aggregateText);
      const rating = normalize(
        ratingClassValue(ratingNode)
        || (ratingNode && (ratingNode.ownText || ratingNode.aggregateText))
        || '',
        120,
      );
      const reviewCountText = normalize(
        reviewCountNode && (reviewCountNode.ownText || reviewCountNode.aggregateText),
        120,
      );
      const reviewCountMatch = reviewCountText.match(REVIEW_COUNT_RE);
      const reviewCount = reviewCountMatch ? normalize(reviewCountMatch[0], 100) : '';
      const availability = normalize(
        availabilityNode && (availabilityNode.ownText || availabilityNode.aggregateText),
        140,
      );
      const author = normalize(authorNode && (authorNode.ownText || authorNode.aggregateText), 140);
      const source = normalize(sourceNode && (sourceNode.ownText || sourceNode.aggregateText), 140);
      const summary = normalize(summaryNode && (summaryNode.ownText || summaryNode.aggregateText), 360);
      const buttons = [];
      if (includeButtons) {
        for (const path of schema.buttonPaths) {
          const node = resolveRelativePath(index, rootId, path);
          if (!node || !node.interactive) continue;
          buttons.push({
            text: normalize(node.ownText || node.aggregateText, 140),
            role: node.role,
            href: node.href,
            selector_hint: node.selectorHint,
          });
        }
      }
      let score = 0;
      if (title) score += 30;
      if (primaryLink) score += 18;
      if (price) score += 20;
      if (rating) score += 12;
      if (summary) score += 10;
      if (root.imageCount) score += 8;
      if (buttons.length) score += 8;
      return {
        id: `card_${group.id}_${itemIndex + 1}`,
        node_id: rootId,
        group_id: group.id,
        group_item_index: itemIndex,
        title,
        price,
        rating,
        review_count: reviewCount,
        availability,
        author,
        source,
        summary,
        has_image: root.imageCount > 0,
        primary_link: primaryLink,
        primary_link_text: normalize(linkNode && (linkNode.ownText || linkNode.aggregateText), 180),
        text_preview: normalize(root.aggregateText, 600),
        selector_hint: root.selectorHint,
        bbox: root.rect,
        title_selector_hint: titleNode ? titleNode.selectorHint : '',
        price_selector_hint: priceNode ? priceNode.selectorHint : '',
        rating_selector_hint: ratingNode ? ratingNode.selectorHint : '',
        review_count_selector_hint: reviewCountNode ? reviewCountNode.selectorHint : '',
        availability_selector_hint: availabilityNode ? availabilityNode.selectorHint : '',
        author_selector_hint: authorNode ? authorNode.selectorHint : '',
        source_selector_hint: sourceNode ? sourceNode.selectorHint : '',
        summary_selector_hint: summaryNode ? summaryNode.selectorHint : '',
        primary_link_selector_hint: linkNode ? linkNode.selectorHint : '',
        buttons,
        score,
      };
    };
    const groupQueryMatchCount = (index, group, query) => {
      if (!query) return group.memberCount;
      return group.memberIds.reduce((count, memberId) => {
        const node = index.nodes[memberId];
        return count + (node && lower(node.aggregateText, 1200).includes(query) ? 1 : 0);
      }, 0);
    };
    const groupDiagnostic = (group, queryMatches, cardsReturned, selected) => ({
      group_id: group.id,
      source: group.source,
      signature_kind: group.signatureKind,
      confidence: group.confidence,
      member_count: group.memberCount,
      score: group.score,
      query_match_count: queryMatches,
      cards_returned: cardsReturned,
      selected,
      evidence: {
        unique_text_count: group.uniqueTextCount,
        feature_counts: group.featureCounts,
        average_descendants: group.averageDescendants,
      },
    });
    const queryCards = (state, index, params) => {
      const startedAt = now();
      const maxCards = clamp(params.max_cards, 1, 50, 20);
      const viewportOnly = params.viewport_only !== false;
      const includeButtons = params.include_buttons !== false;
      const query = lower(params.query, 160);
      const diagnosticsLevelRaw = lower(params.diagnostics_level || 'compact', 20);
      const diagnosticsLevel = ['compact', 'standard', 'debug'].includes(diagnosticsLevelRaw)
        ? diagnosticsLevelRaw
        : 'compact';
      const configuration = state.configuration || {};
      const requestedRevision = String(params.configuration_revision || '');
      if (requestedRevision && requestedRevision !== String(configuration.revision || '')) {
        return {
          error: 'page_index_configuration_mismatch',
          expectedConfigurationRevision: requestedRevision,
          actualConfigurationRevision: String(configuration.revision || ''),
          cards: [],
          groups: [],
          queryMs: Math.round((now() - startedAt) * 100) / 100,
        };
      }
      const profiles = matchingProfiles(configuration.site_profiles || []);
      const cacheRecords = matchingCacheRecords(configuration.selector_cache_records || []);
      const cacheSelectors = selectorList(cacheRecords, 'card_container_selectors');
      const siteProfileSelectors = unique(
        profiles.flatMap((profile) => profile.card_container_selectors || []),
        30,
      );
      const cachedCandidates = idsForSelectors(index, cacheSelectors);
      const cachedGroup = makeSelectorGroup(index, cachedCandidates, 'cache');
      const hasEnoughGoodCards = (group) => Boolean(
        group && group.memberCount >= 2 && group.score >= 45,
      );
      const cacheAccepted = hasEnoughGoodCards(cachedGroup);
      const cacheRejectionReason = cacheRecords.length && !cacheAccepted
        ? (cachedCandidates.length < 2
          ? 'cache_candidate_count_too_small'
          : 'cache_validation_failed')
        : '';
      const profiledCandidates = idsForSelectors(index, siteProfileSelectors);
      const profileGroup = makeSelectorGroup(index, profiledCandidates, 'site_profile');
      const candidateGroups = [];
      const seenGroupKeys = new Set();
      const addCandidateGroup = (group) => {
        if (!group) return;
        const key = group.memberIds.slice().sort((left, right) => left - right).join(',');
        if (!key || seenGroupKeys.has(key)) return;
        seenGroupKeys.add(key);
        candidateGroups.push(group);
      };
      if (cacheAccepted) addCandidateGroup(cachedGroup);
      addCandidateGroup(profileGroup);
      const repeatedGroups = index.groups
        .filter((group) => group.score >= 18)
        .map((group) => ({
          group,
          queryMatches: groupQueryMatchCount(index, group, query),
        }))
        .sort((left, right) => {
          if (query && left.queryMatches !== right.queryMatches) {
            return right.queryMatches - left.queryMatches;
          }
          return right.group.score - left.group.score;
        });
      for (const item of repeatedGroups) addCandidateGroup(item.group);
      if (!candidateGroups.length) {
        return {
          error: null,
          diagnosticsLevel,
          cards: [],
          groups: [],
          selectorSource: 'repeated_group',
          cacheAccepted,
          cacheRejectionReason,
          cacheRecordsUsed: cacheRecords.length,
          cacheCandidateCount: cachedCandidates.length,
          cacheGoodCandidateCount: cachedGroup ? cachedGroup.memberCount : 0,
          profileIds: profiles.map((profile) => profile.id).filter(Boolean),
          representativesParsed: 0,
          schemaReused: false,
          groupsExamined: 0,
          expectedConfigurationRevision: requestedRevision,
          actualConfigurationRevision: String(configuration.revision || ''),
          queryMs: Math.round((now() - startedAt) * 100) / 100,
        };
      }
      const fieldNames = {
        title: 'title_selectors',
        primary_link: 'primary_link_selectors',
        price: 'price_selectors',
        rating: 'rating_selectors',
        review_count: 'review_count_selectors',
        availability: 'availability_selectors',
        author: 'author_selectors',
        source: 'source_selectors',
        summary: 'summary_selectors',
      };
      const fieldSelectors = Object.create(null);
      const selectorSources = cacheRecords.concat(profiles);
      for (const [field, name] of Object.entries(fieldNames)) {
        fieldSelectors[field] = selectorList(selectorSources, name);
      }
      const attempts = [];
      let selected = null;
      let representativesParsed = 0;
      for (const group of candidateGroups.slice(0, MAX_GROUP_ATTEMPTS)) {
        const schemaKey = hashText([
          location.hostname,
          routeSignature(),
          group.dominantSignature,
          group.shapeSignature,
          group.id,
          JSON.stringify(fieldSelectors),
        ].join('|'));
        let schema = state.schemaCache[schemaKey];
        const schemaReused = Boolean(schema);
        if (!schema) {
          schema = inferGroupSchema(index, group, fieldSelectors);
          state.schemaCache[schemaKey] = schema;
          representativesParsed += schema.representativesParsed;
        }
        const cards = [];
        for (let itemIndex = 0; itemIndex < group.memberIds.length; itemIndex += 1) {
          const rootId = group.memberIds[itemIndex];
          const root = index.nodes[rootId];
          if (!root || !root.visible) continue;
          if (viewportOnly && !root.inViewport) continue;
          const card = cardFromSchema(
            index,
            group,
            schema,
            rootId,
            itemIndex,
            includeButtons,
          );
          const searchable = lower([
            card.title,
            card.price,
            card.rating,
            card.author,
            card.source,
            card.summary,
            card.text_preview,
          ].join(' '), 1200);
          if (query && !searchable.includes(query)) continue;
          cards.push(card);
          if (cards.length >= maxCards) break;
        }
        const queryMatches = groupQueryMatchCount(index, group, query);
        const attempt = {
          group,
          cards,
          schemaReused,
          diagnostic: groupDiagnostic(group, queryMatches, cards.length, false),
        };
        attempts.push(attempt);
        if (cards.length) {
          selected = attempt;
          break;
        }
      }
      if (selected) {
        selected.diagnostic.selected = true;
        registerGroupContext(index, selected.group, true);
      }
      const orderedDiagnostics = selected
        ? [selected.diagnostic].concat(
          attempts.filter((attempt) => attempt !== selected).map((attempt) => attempt.diagnostic),
        )
        : attempts.map((attempt) => attempt.diagnostic);
      return {
        error: null,
        diagnosticsLevel,
        cards: selected ? selected.cards : [],
        groups: orderedDiagnostics,
        selectorSource: selected ? selected.group.source : candidateGroups[0].source,
        cacheAccepted,
        cacheRejectionReason,
        cacheRecordsUsed: cacheRecords.length,
        cacheCandidateCount: cachedCandidates.length,
        cacheGoodCandidateCount: cachedGroup ? cachedGroup.memberCount : 0,
        profileIds: profiles.map((profile) => profile.id).filter(Boolean),
        representativesParsed,
        schemaReused: selected ? selected.schemaReused : attempts.every((attempt) => attempt.schemaReused),
        groupsExamined: attempts.length,
        expectedConfigurationRevision: requestedRevision,
        actualConfigurationRevision: String(configuration.revision || ''),
        queryMs: Math.round((now() - startedAt) * 100) / 100,
      };
    };

    const withoutEmptyValues = (value) => Object.fromEntries(
      Object.entries(value).filter(([, item]) => {
        if (item == null || item === '') return false;
        if (Array.isArray(item) && item.length === 0) return false;
        return true;
      }),
    );
    const compactButton = (button) => withoutEmptyValues({
      text: button.text,
      role: button.role,
      href: button.href,
    });
    const compactCard = (card) => withoutEmptyValues({
      id: card.id,
      node_id: card.node_id,
      group_id: card.group_id,
      group_item_index: card.group_item_index,
      title: card.title,
      price: card.price,
      rating: card.rating,
      review_count: card.review_count,
      availability: card.availability,
      author: card.author,
      source: card.source,
      summary: card.summary,
      has_image: card.has_image || undefined,
      primary_link: card.primary_link,
      primary_link_text: card.primary_link_text,
      buttons: (card.buttons || []).slice(0, 4).map(compactButton),
    });
    const compactGroupDiagnostic = (group) => {
      if (!group) return null;
      return {
        group_id: group.group_id,
        source: group.source,
        signature_kind: group.signature_kind,
        confidence: group.confidence,
        member_count: group.member_count,
        score: group.score,
        query_match_count: group.query_match_count,
        cards_returned: group.cards_returned,
        selected: group.selected,
      };
    };
    const pageIndexDiagnostics = (state, index, cacheHit, result) => ({
      schema_version: index.schemaVersion,
      document_id: index.documentId,
      page_version: `${index.documentId}:${index.domVersion}:${hashText(index.url)}`,
      cache_hit: cacheHit,
      index_rebuilt: !cacheHit,
      restored_from_bfcache: Boolean(state.lifecycle.restoredFromBfcache),
      build_ms: cacheHit ? 0 : index.buildMs,
      query_ms: result.queryMs,
      nodes_indexed: index.nodes.length,
      repeated_groups: index.groups.length,
      representatives_parsed: result.representativesParsed || 0,
      schema_reused: Boolean(result.schemaReused),
      groups_examined: result.groupsExamined || 0,
      configuration_revision: String((state.configuration || {}).revision || ''),
      truncated: index.truncated,
    });

    const query = (request) => {
      const {state, index, cacheHit} = ensurePageIndex();
      const mode = String(request.mode || '');
      if (mode === 'interactives') {
        const result = queryInteractives(index, request.params || {});
        return {
          ok: true,
          error: null,
          url: location.href,
          title: document.title,
          query: String((request.params || {}).query || ''),
          scope_group_id: String((request.params || {}).scope_group_id || ''),
          scope_item_index: (request.params || {}).scope_item_index,
          viewport: {
            width: window.innerWidth,
            height: window.innerHeight,
            scroll_x: Math.round(window.scrollX),
            scroll_y: Math.round(window.scrollY),
          },
          viewport_only: (request.params || {}).viewport_only !== false,
          elements: result.elements,
          returned: result.elements.length,
          total_candidates: result.totalCandidates,
          page_index: {
            schema_version: index.schemaVersion,
            document_id: index.documentId,
            page_version: `${index.documentId}:${index.domVersion}:${hashText(index.url)}`,
            cache_hit: cacheHit,
            index_rebuilt: !cacheHit,
            restored_from_bfcache: Boolean(state.lifecycle.restoredFromBfcache),
            build_ms: cacheHit ? 0 : index.buildMs,
            query_ms: result.queryMs,
            nodes_indexed: index.nodes.length,
            interactive_nodes: index.interactiveIds.length,
            repeated_groups: index.groups.length,
            configuration_revision: String((state.configuration || {}).revision || ''),
            truncated: index.truncated,
          },
        };
      }
      if (mode === 'cards') {
        const result = queryCards(state, index, request.params || {});
        if (result.error) {
          return {
            ok: false,
            error: result.error,
            expected_configuration_revision: result.expectedConfigurationRevision || '',
            actual_configuration_revision: result.actualConfigurationRevision || '',
            cards: [],
          };
        }
        const diagnosticsLevel = result.diagnosticsLevel || 'compact';
        const cards = diagnosticsLevel === 'compact'
          ? result.cards.map(compactCard)
          : result.cards;
        const response = {
          ok: true,
          error: null,
          url: location.href,
          title: document.title,
          query: String((request.params || {}).query || ''),
          diagnostics_level: diagnosticsLevel,
          viewport: {
            width: window.innerWidth,
            height: window.innerHeight,
            scroll_x: Math.round(window.scrollX),
            scroll_y: Math.round(window.scrollY),
          },
          viewport_only: (request.params || {}).viewport_only !== false,
          cards,
          _cache_cards: result.cards.slice(0, 3),
          returned: cards.length,
          total_candidates: result.groups.length ? result.groups[0].member_count : 0,
          selected_group: compactGroupDiagnostic(result.groups.find((group) => group.selected)),
          selector_source: result.selectorSource,
          profile_ids: result.profileIds,
          cache_records_used: result.cacheRecordsUsed,
          cache_accepted: result.cacheAccepted,
          cache_rejection_reason: result.cacheRejectionReason,
          cache_candidate_count: result.cacheCandidateCount,
          cache_good_candidate_count: result.cacheGoodCandidateCount,
          page_index: pageIndexDiagnostics(state, index, cacheHit, result),
        };
        if (diagnosticsLevel !== 'compact') response.groups = result.groups;
        if (diagnosticsLevel === 'debug') {
          response.recurring_signatures = index.groups.map((group) => ({
            group_id: group.id,
            signature: group.dominantSignature,
            count: group.memberCount,
            score: group.score,
          })).slice(0, 12);
          response.cached_container_selectors = selectorList(
            matchingCacheRecords((state.configuration || {}).selector_cache_records || []),
            'card_container_selectors',
          );
        }
        return response;
      }
      return {
        ok: false,
        error: `unsupported_page_index_probe_mode:${mode}`,
      };
    };
    window[RUNTIME_KEY] = {
      schemaVersion: SCHEMA_VERSION,
      get configurationRevision() {
        return String((ensureState().configuration || {}).revision || '');
      },
      configure,
      query,
    };
    const configurationResult = INITIAL_CONFIGURATION
      ? configure(INITIAL_CONFIGURATION)
      : {configuration_revision: ''};
    return {
      ok: true,
      error: null,
      already_installed: false,
      schema_version: SCHEMA_VERSION,
      configuration_revision: configurationResult.configuration_revision || '',
    };
  });
}
"""


_PAGE_INDEX_QUERY_TEMPLATE = r"""
async (page) => {
  const request = __OPENJIUWEN_PAGE_INDEX_REQUEST__;
  return await page.evaluate((request) => {
    const runtime = window.__openjiuwenPageStructureRuntimeV3;
    if (!runtime || runtime.schemaVersion !== 3 || typeof runtime.query !== 'function') {
      return {
        ok: false,
        error: 'page_index_runtime_missing',
      };
    }
    return runtime.query(request);
  }, request);
}
"""


_PAGE_INDEX_CONFIGURE_TEMPLATE = r"""
async (page) => {
  const configuration = __OPENJIUWEN_PAGE_INDEX_CONFIGURATION__;
  return await page.evaluate((configuration) => {
    const runtime = window.__openjiuwenPageStructureRuntimeV3;
    if (!runtime || runtime.schemaVersion !== 3 || typeof runtime.configure !== 'function') {
      return {
        ok: false,
        error: 'page_index_runtime_missing',
      };
    }
    return runtime.configure(configuration);
  }, configuration);
}
"""


def _compact_selector_cache_record(record: Dict[str, Any]) -> Dict[str, Any]:
    selectors = record.get("selectors")
    return {
        "domain": str(record.get("domain") or ""),
        "route_signature": str(record.get("route_signature") or "/"),
        "kind": str(record.get("kind") or "card_probe"),
        "selectors": selectors if isinstance(selectors, dict) else {},
    }


def build_page_index_configuration(
    *,
    site_profiles: Optional[List[Dict[str, Any]]] = None,
    selector_cache_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build stable browser-side configuration for indexed card probes."""
    compact_cache_records = sorted(
        (
            _compact_selector_cache_record(record)
            for record in selector_cache_records or []
            if isinstance(record, dict)
        ),
        key=lambda item: (
            item["domain"],
            item["route_signature"],
            item["kind"],
            json.dumps(item["selectors"], ensure_ascii=False, sort_keys=True),
        ),
    )
    payload = {
        "site_profiles": list(site_profiles or []),
        "selector_cache_records": compact_cache_records,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "revision": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
        **payload,
    }


def build_page_index_install_js(
    initial_configuration: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the one-time browser-side page-index runtime installer."""
    initial_json = json.dumps(
        initial_configuration,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _PAGE_INDEX_INSTALL_TEMPLATE.replace(
        "__OPENJIUWEN_PAGE_INDEX_INITIAL_CONFIGURATION__",
        initial_json,
    )


def build_page_index_configure_js(configuration: Dict[str, Any]) -> str:
    """Build a configuration update for an installed page-index runtime."""
    configuration_json = json.dumps(
        configuration,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _PAGE_INDEX_CONFIGURE_TEMPLATE.replace(
        "__OPENJIUWEN_PAGE_INDEX_CONFIGURATION__",
        configuration_json,
    )


def build_page_index_probe_js(request: Dict[str, Any]) -> str:
    """Build a small query against the installed browser-side page index."""
    request_json = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    return _PAGE_INDEX_QUERY_TEMPLATE.replace(
        "__OPENJIUWEN_PAGE_INDEX_REQUEST__",
        request_json,
    )
