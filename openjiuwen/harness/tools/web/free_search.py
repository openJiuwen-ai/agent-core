# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async free web search tool (DuckDuckGo / Bing / jina proxy).

Network I/O is aiohttp-native via the shared ``_request`` coroutine; HTML
parsing runs directly in the event loop (web search is low-frequency and pages
are small, so offloading to a thread would only add scheduling overhead). The
DuckDuckGo parser binds each link to a snippet within its own result block,
fixing the index-pairing misalignment of the original implementation.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote_plus, urlparse

import aiohttp
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.web._common import (
    _FREE_SEARCH_BING_ENABLED_ENV,
    _FREE_SEARCH_DDG_ENABLED_ENV,
    _FREE_SEARCH_DEBUG_DIR_ENV,
    _FREE_SEARCH_DEBUG_ENV,
    _contains_cjk,
    _decode_ddg_redirect,
    _decode_bing_redirect,
    _duckduckgo_search_url,
    _env_flag,
    _normalized_domain,
    _parse_html,
    _safe_int,
    _search_request_headers,
    _strip_tags,
)
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._decode import _decode_response_text

_QUERY_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "news",
    "latest",
    "today",
    "热点",
    "新闻",
    "最新",
    "今日",
    "今天",
}
_TIMELY_QUERY_HINTS = {
    "news",
    "latest",
    "today",
    "breaking",
    "update",
    "updates",
    "新闻",
    "最新",
    "今日",
    "今天",
    "动态",
    "快讯",
    "头条",
    "热点",
    "天气",
    "weather",
    "forecast",
}
_LOW_CONFIDENCE_RESULT_DOMAINS = {
    "zhihu.com",
    "baike.baidu.com",
    "tieba.baidu.com",
    "zhidao.baidu.com",
    "douban.com",
    "bilibili.com",
    "weibo.com",
}
_LOW_FETCH_VALUE_DOMAINS = {
    "mp.weixin.qq.com",
    "so.html5.qq.com",
}


def _is_low_fetch_value_url(url: str) -> bool:
    """Whether a URL is likely to be a shell page or parameter-gated fetch target."""
    domain = _normalized_domain(url)
    return any(domain == item or domain.endswith(f".{item}") for item in _LOW_FETCH_VALUE_DOMAINS)


def _is_timely_query(query: str) -> bool:
    """Whether the query is asking for current or news-like information."""
    lowered = (query or "").lower()
    return any(hint in lowered for hint in _TIMELY_QUERY_HINTS)


def _is_low_confidence_result_domain(url: str) -> bool:
    """Whether a result domain is a low-confidence generic/UGC source."""
    domain = _normalized_domain(url)
    return any(domain == item or domain.endswith(f".{item}") for item in _LOW_CONFIDENCE_RESULT_DOMAINS)


def _is_debug_enabled() -> bool:
    """Whether free-search debug dumps are enabled."""
    return str(os.environ.get(_FREE_SEARCH_DEBUG_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _get_debug_dir() -> Path:
    """Resolve the debug output directory."""
    configured = str(os.environ.get(_FREE_SEARCH_DEBUG_DIR_ENV, "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd() / ".tmp" / "free_search_debug"


def _clip_debug_text(value: str, max_chars: int = 30000) -> str:
    """Clip debug text payloads while preserving both head and tail."""
    if len(value) <= max_chars:
        return value
    head_len = max_chars // 2
    tail_len = max_chars - head_len
    return value[:head_len] + f"\n...[truncated {len(value) - max_chars} chars]...\n" + value[-tail_len:]


def _clip_debug_html_fragment(node: Any, max_chars: int = 12000) -> str:
    """Serialize and clip an HTML fragment for debug output."""
    try:
        html = str(node)
    except Exception:
        html = ""
    return _clip_debug_text(html, max_chars=max_chars)


def _extract_debug_html_neighborhood(html: str, marker: str, *, window: int = 4000) -> dict[str, Any]:
    """Capture a neighborhood around a raw HTML marker for debug inspection."""
    position = html.find(marker)
    if position < 0:
        return {"marker": marker, "position": -1, "fragment": ""}
    start = max(0, position - window)
    end = min(len(html), position + len(marker) + window)
    return {
        "marker": marker,
        "position": position,
        "fragment": _clip_debug_text(html[start:end], max_chars=window * 2 + len(marker)),
    }


def _build_bing_debug_sections(html: str, soup: BeautifulSoup) -> dict[str, Any]:
    """Extract targeted Bing HTML fragments and selector stats for diagnosis."""
    sections: dict[str, Any] = {
        "counts": {
            "b_results": 0,
            "b_context": 0,
            "b_topw": 0,
            "b_algo": 0,
            "b_ans": 0,
            "b_card": 0,
        }
    }
    sections["html_length"] = len(html)
    title_tag = soup.select_one("title")
    if title_tag is not None:
        sections["page_title"] = title_tag.get_text(" ", strip=True)

    results_main = soup.select_one('main[aria-label="Search Results"]')
    sections["results_area"] = {
        "selector": 'main[aria-label="Search Results"]',
        "found": results_main is not None,
        "fragment": _clip_debug_html_fragment(results_main) if results_main is not None else "",
    }

    container_selectors = {
        "b_results": "#b_results",
        "b_context": "#b_context",
        "b_topw": "#b_topw",
    }
    for name, selector in container_selectors.items():
        node = soup.select_one(selector)
        sections["counts"][name] = 1 if node is not None else 0
        if node is not None:
            sections[name] = _clip_debug_html_fragment(node)

    algo_nodes = soup.select("li.b_algo")
    sections["counts"]["b_algo"] = len(algo_nodes)
    if algo_nodes:
        sections["b_algo_samples"] = [_clip_debug_html_fragment(node, max_chars=6000) for node in algo_nodes[:3]]
        sections["b_algo_titles"] = [node.get_text(" ", strip=True)[:200] for node in algo_nodes[:10]]

    answer_nodes = soup.select(".b_ans")
    sections["counts"]["b_ans"] = len(answer_nodes)
    if answer_nodes:
        sections["b_ans_samples"] = [_clip_debug_html_fragment(node, max_chars=6000) for node in answer_nodes[:3]]

    card_nodes = soup.select(".b_card")
    sections["counts"]["b_card"] = len(card_nodes)
    if card_nodes:
        sections["b_card_samples"] = [_clip_debug_html_fragment(node, max_chars=6000) for node in card_nodes[:3]]

    raw_markers = [
        'id="b_results"',
        'id="b_context"',
        'id="b_topw"',
        'class="b_algo"',
        'class="b_ans"',
        'aria-label="Search Results"',
    ]
    sections["raw_markers"] = [_extract_debug_html_neighborhood(html, marker) for marker in raw_markers]
    return sections


def _write_debug_payload(run_id: str, engine: str, stage: str, payload: dict[str, Any]) -> None:
    """Write a single debug payload for later diagnosis."""
    if not _is_debug_enabled():
        return
    try:
        debug_dir = _get_debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{run_id}_{engine}_{stage}.json"
        normalized = dict(payload)
        raw_html = normalized.get("raw_html")
        if isinstance(raw_html, str):
            normalized["raw_html"] = _clip_debug_text(raw_html)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        tool_logger.warning("Failed to write free_search debug payload: %s", exc)


def _query_terms(query: str) -> list[str]:
    """Extract lightweight query terms for scoring and diagnostics."""
    raw_terms = re.findall(r"[A-Za-z0-9一-鿿]+", (query or "").lower())
    terms: list[str] = []
    for term in raw_terms:
        expanded_terms: list[str] = []
        if _contains_cjk(term):
            numeric_terms = re.findall(r"\d{4}|\d+月|\d+日|\d+", term)
            expanded_terms.extend(numeric_terms)
            cjk_chunks = re.findall(r"[一-鿿]+", term)
            for chunk in cjk_chunks:
                if 2 <= len(chunk) <= 4:
                    expanded_terms.append(chunk)
                    expanded_terms.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
                elif len(chunk) > 4:
                    expanded_terms.append(chunk[:2])
                    expanded_terms.append(chunk[:4])
                    expanded_terms.append(chunk[-2:])
        else:
            expanded_terms.append(term)
        for expanded in expanded_terms:
            if len(expanded) < 2 or expanded in _QUERY_STOPWORDS:
                continue
            if expanded not in terms:
                terms.append(expanded)
    return terms[:12]


def _query_core_terms(query: str) -> list[str]:
    """Extract query core terms used for simple relevance checks."""
    core_terms: list[str] = []
    for term in _query_terms(query):
        if term.isdigit():
            continue
        if re.fullmatch(r"\d+月|\d+日", term):
            continue
        if len(term) < 2:
            continue
        if term not in core_terms:
            core_terms.append(term)
    return core_terms[:8]


def _match_term_count(query: str, row: dict[str, str]) -> int:
    """Count how many distinct query terms appear in the row."""
    haystack = " ".join(
        [
            str(row.get("title", "") or "").lower(),
            str(row.get("snippet", "") or "").lower(),
            str(row.get("url", "") or "").lower(),
            str(row.get("origin", "") or "").lower(),
        ]
    )
    return sum(1 for term in _query_terms(query) if term in haystack)


def _build_bing_row(
    *,
    href: str,
    title: str,
    snippet: str = "",
    origin: str = "",
    date: str = "",
    source: str = "bing-web",
) -> dict[str, str]:
    """Build a normalized Bing result row."""
    return {
        "title": title.strip(),
        "url": href.strip(),
        "snippet": snippet.strip(),
        "origin": origin.strip(),
        "date": date.strip(),
        "source": source,
    }


def _summarize_rows(rows: list[dict[str, str]], *, limit: int = 10) -> dict[str, Any]:
    """Build a compact row summary for debug payloads."""
    by_source: dict[str, int] = {}
    top_titles: list[dict[str, str]] = []
    for row in rows:
        source = str(row.get("source", "") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
    for row in rows[:limit]:
        top_titles.append(
            {
                "title": str(row.get("title", "") or "")[:200],
                "origin": str(row.get("origin", "") or "")[:120],
                "source": str(row.get("source", "") or ""),
                "url": str(row.get("url", "") or "")[:240],
            }
        )
    return {
        "count": len(rows),
        "by_source": by_source,
        "top_titles": top_titles,
    }


def _extract_bing_results_area(soup: BeautifulSoup) -> BeautifulSoup:
    """Prefer Bing's main SERP container when present."""
    return soup.select_one('main[aria-label="Search Results"]') or soup


def _extract_bing_answer_cards(soup: BeautifulSoup, fallback_url: str) -> list[dict[str, str]]:
    """Extract generic Bing answer/module cards that are not regular organic results."""
    rows: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    selectors = [
        "#b_context .b_ans",
        "#b_context .b_entityTP",
        "#b_context .b_card",
        "#b_context .b_rich",
        "#b_topw .b_ans",
        "#b_topw .b_entityTP",
        "#b_topw .b_card",
        "#b_topw .b_rich",
        ".b_ans",
        ".b_rich",
        ".b_entityTP",
        ".b_vList",
        ".b_tpcn",
        ".b_card",
    ]
    for node in soup.select(",".join(selectors)):
        anchor = node.select_one("a[href]")
        href = ""
        if anchor is not None:
            href_raw = unescape(str(anchor.get("href", "") or ""))
            href = _decode_bing_redirect(href_raw)
            parsed = urlparse(href)
            if not parsed.scheme.startswith("http") or "bing.com" in parsed.netloc.lower():
                href = ""
        title_parts: list[str] = []
        for selector in (".b_focusLabel", ".b_focusTextLarge", ".b_focusTextMedium", ".b_focusTextSmall", "h2", "h3"):
            for tag in node.select(selector):
                text = tag.get_text(" ", strip=True)
                if text and text not in title_parts:
                    title_parts.append(text)
            if title_parts:
                break
        title = " ".join(title_parts).strip()
        if len(title) < 4:
            continue
        snippet = ""
        caption = node.select_one(".b_caption p") or node.select_one("p")
        if caption is not None:
            snippet = caption.get_text(" ", strip=True)
        row_url = href or fallback_url
        dedupe_key = f"{title}|{row_url}"
        if dedupe_key in seen_titles:
            continue
        seen_titles.add(dedupe_key)
        rows.append(_build_bing_row(href=row_url, title=title, snippet=snippet, source="bing-card"))
    return rows


def _ddg_row(anchor: Any, snippet_node: Any, ordinal: int) -> dict[str, str]:
    """Build a DuckDuckGo result row from a link anchor and its snippet node."""
    title = _strip_tags(anchor.decode_contents()) or f"Result {ordinal}"
    snippet = _strip_tags(snippet_node.decode_contents()) if snippet_node is not None else ""
    return {
        "title": title,
        "url": _decode_ddg_redirect(str(anchor.get("href", "") or "")),
        "snippet": snippet,
        "source": "duckduckgo",
    }


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML results, binding each link to its own snippet.

    Prefers per-result containers (real DDG markup). Falls back to a flat scan
    of ``a.result__a`` when no container is present, binding each link only to a
    snippet that precedes the next link so a missing snippet never shifts onto
    the wrong result.
    """
    soup = _parse_html(html)
    rows: list[dict[str, str]] = []

    containers = soup.select(".result, .web-result, .results_links")
    if containers:
        for block in containers:
            anchor = block.select_one("a.result__a[href]")
            if anchor is None:
                continue
            snippet_node = block.select_one(".result__snippet")
            rows.append(_ddg_row(anchor, snippet_node, len(rows) + 1))
            if len(rows) >= max_results:
                break
        return rows

    anchors = soup.select("a.result__a[href]")
    for idx, anchor in enumerate(anchors):
        next_anchor = anchors[idx + 1] if idx + 1 < len(anchors) else None
        snippet_node = anchor.find_next(class_="result__snippet")
        if snippet_node is not None and next_anchor is not None:
            # If this snippet is also the first snippet after the next link,
            # it belongs to that next result, not the current one.
            if snippet_node is next_anchor.find_next(class_="result__snippet"):
                snippet_node = None
        rows.append(_ddg_row(anchor, snippet_node, len(rows) + 1))
        if len(rows) >= max_results:
            break
    return rows


class WebFreeSearchTool(Tool):
    """Free search via DuckDuckGo/Bing. Input query and return ranked URLs with snippets."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: str | None = None,
        card: ToolCard | None = None,
    ):
        super().__init__(
            card or build_tool_card("free_search", "WebFreeSearchTool", language, agent_id=agent_id)
        )

    @staticmethod
    def _is_ddg_challenge_page(status_code: int, html: str) -> bool:
        """Check if the response is a DuckDuckGo anti-bot challenge page."""
        if status_code in {202, 418, 429, 503}:
            return True
        text = (html or "").lower()
        markers = [
            "/anomaly.js",
            "challenge-form",
            "duckduckgo.com/anomaly.js",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    async def _search_duckduckgo(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> list[dict[str, str]]:
        """Search the DuckDuckGo HTML endpoint and parse results."""
        url = _duckduckgo_search_url(query)
        status, headers, body, _final_url, _truncated = await _http._request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
        )
        html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        if WebFreeSearchTool._is_ddg_challenge_page(status, html):
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR,
                engine="duckduckgo",
                reason="anti-bot challenge page returned",
            )
        if status != 200:
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR,
                engine="duckduckgo",
                reason=f"non-200 status: {status}",
            )
        return _parse_ddg_html(html, max_results)

    @staticmethod
    async def _search_duckduckgo_via_jina(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> list[dict[str, str]]:
        """Search DuckDuckGo via the jina.ai proxy and parse markdown results."""
        url = f"https://r.jina.ai/http://duckduckgo.com/html/?q={quote_plus(query)}"
        status, headers, body, _final_url, _truncated = await _http._request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
        )
        _http._raise_for_status_with_body(status, body, engine="duckduckgo-jina")
        text = _decode_response_text(body, content_type=headers.get("Content-Type", "")) or ""

        matches = re.findall(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", text, flags=re.IGNORECASE)

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for title_raw, href in matches:
            title = _strip_tags(title_raw)
            if not title or title.startswith("Image "):
                continue
            decoded = _decode_ddg_redirect(href)
            parsed = urlparse(decoded)
            if not parsed.scheme.startswith("http"):
                continue
            if "duckduckgo.com" in parsed.netloc.lower():
                continue
            if decoded in seen:
                continue
            seen.add(decoded)
            rows.append({"title": title, "url": decoded, "snippet": "", "source": "duckduckgo-jina"})
            if len(rows) >= max_results:
                break
        return rows

    @staticmethod
    async def _search_bing(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
        *,
        debug_run_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Search Bing and parse standard SERP result blocks."""
        if _contains_cjk(query):
            url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=zh-Hans&mkt=zh-CN&cc=CN"
        else:
            url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US&mkt=en-US&cc=US"

        status, headers, body, _final_url, _truncated = await _http._request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
        )
        _http._raise_for_status_with_body(status, body, engine="bing")
        html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        soup = _parse_html(html)
        results_area = _extract_bing_results_area(soup)

        _write_debug_payload(
            debug_run_id or "",
            "bing",
            "raw",
            {
                "query": query,
                "request_url": url,
                "status_code": status,
                "raw_html": html,
                "html_sections": _build_bing_debug_sections(html, soup),
            },
        )

        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for row in _extract_bing_answer_cards(soup, url):
            row_url = str(row.get("url", "") or "")
            if row_url and row_url not in seen:
                seen.add(row_url)
            rows.append(row)

        for item in results_area.select("li.b_algo"):
            anchor = item.select_one("h2 a[href]")
            if anchor is None:
                continue
            href_raw = unescape(str(anchor.get("href", "") or ""))
            href = _decode_bing_redirect(href_raw)
            title = anchor.get_text(" ", strip=True)
            if not href or href in seen:
                continue
            seen.add(href)
            caption_node = item.select_one(".b_caption p") or item.select_one("p")
            origin_node = item.select_one(".tptt") or item.select_one(".source") or item.select_one("cite")
            date_node = item.select_one(".news_dt")
            snippet = caption_node.get_text(" ", strip=True) if caption_node is not None else ""
            origin = origin_node.get_text(" ", strip=True) if origin_node is not None else ""
            date = date_node.get_text(" ", strip=True) if date_node is not None else ""
            rows.append(
                _build_bing_row(
                    href=href,
                    title=title or f"Result {len(rows) + 1}",
                    snippet=snippet,
                    origin=origin,
                    date=date,
                )
            )

        rows = rows[:max_results]
        _write_debug_payload(
            debug_run_id or "",
            "bing",
            "parsed",
            {
                "query": query,
                "request_url": url,
                "rows": rows,
                "row_summary": _summarize_rows(rows),
            },
        )
        return rows

    @staticmethod
    def _score_row(query: str, row: dict[str, str]) -> float:
        """Lightweight scoring used only for page-quality judgement/debug."""
        title = str(row.get("title", "") or "")
        snippet = str(row.get("snippet", "") or "")
        url = str(row.get("url", "") or "")
        origin = str(row.get("origin", "") or "")
        haystack = " ".join([title.lower(), snippet.lower(), url.lower(), origin.lower()])

        score = 0.0
        if title:
            score += 2.0
        if snippet:
            score += 1.0
        if origin:
            score += 0.5
        if str(row.get("source", "") or "") == "bing-card":
            score += 1.0
        if _is_low_confidence_result_domain(url):
            score -= 1.5

        for term in _query_terms(query):
            if term in haystack:
                score += 0.35
        return score

    @staticmethod
    def _filter_ranked_rows(query: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Filter rows lightly while preserving original engine order."""
        preferred: list[dict[str, str]] = []
        deferred: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for row in rows:
            url = str(row.get("url", "") or "")
            title = str(row.get("title", "") or "").strip()
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            if _is_low_fetch_value_url(url):
                deferred.append(row)
            else:
                preferred.append(row)
        # Keep engine order. Only push low-fetch-value shell pages behind real pages.
        return preferred + deferred

    @staticmethod
    def _rows_are_usable(query: str, rows: list[dict[str, str]]) -> bool:
        """Decide whether a result page is good enough to stop engine fallback."""
        if not rows:
            return False
        timely_query = _is_timely_query(query)
        top_rows = rows[:3]
        if not timely_query:
            return True
        for row in top_rows:
            url = str(row.get("url", "") or "")
            if str(row.get("source", "") or "") == "bing-card":
                return True
            if not _is_low_confidence_result_domain(url):
                if _match_term_count(query, row) >= 1:
                    return True
                if row.get("date") or row.get("origin"):
                    return True
        return False

    @staticmethod
    def _bing_query_candidates(query: str) -> list[str]:
        """Return Bing query candidates. Keep only original query."""
        normalized = (query or "").strip()
        return [normalized] if normalized else []

    @staticmethod
    async def _search_free(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> tuple[str, list[dict[str, str]]]:
        """Search using multiple free search engines with best-effort fallback."""
        errors: list[str] = []
        debug_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        best_engine = ""
        best_rows: list[dict[str, str]] = []

        engines: list[tuple[str, Any]] = []
        if _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False):
            engines.extend(
                [
                    ("duckduckgo", WebFreeSearchTool._search_duckduckgo),
                    ("duckduckgo-jina", WebFreeSearchTool._search_duckduckgo_via_jina),
                ]
            )
        if _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False):
            engines.append(("bing", WebFreeSearchTool._search_bing))

        if not engines:
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ALL_ENGINES_FAILED,
                errors="all free search engines are disabled",
            )

        for engine_name, runner in engines:
            candidate_queries = [query]
            if engine_name == "bing":
                candidate_queries = WebFreeSearchTool._bing_query_candidates(query)

            for effective_query in candidate_queries:
                try:
                    if engine_name == "bing":
                        rows = await runner(session, effective_query, max_results, timeout_seconds,
                                            debug_run_id=debug_run_id)
                    else:
                        rows = await runner(session, effective_query, max_results, timeout_seconds)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{engine_name}: {exc}")
                    _write_debug_payload(
                        debug_run_id,
                        engine_name,
                        "error",
                        {"query": query, "effective_query": effective_query, "error": str(exc)},
                    )
                    continue

                filtered_rows = WebFreeSearchTool._filter_ranked_rows(query, rows)[:max_results]
                _write_debug_payload(
                    debug_run_id,
                    engine_name,
                    "candidate",
                    {
                        "query": query,
                        "effective_query": effective_query,
                        "query_core_terms": _query_core_terms(query),
                        "query_terms": _query_terms(query),
                        "row_summary": _summarize_rows(filtered_rows),
                        "rows": filtered_rows,
                    },
                )
                _write_debug_payload(
                    debug_run_id,
                    engine_name,
                    "filtered",
                    {
                        "query": query,
                        "effective_query": effective_query,
                        "query_core_terms": _query_core_terms(query),
                        "query_terms": _query_terms(query),
                        "row_summary": _summarize_rows(filtered_rows),
                        "rows": [
                            {
                                **row,
                                "score": WebFreeSearchTool._score_row(query, row),
                                "match_count": _match_term_count(query, row),
                            }
                            for row in filtered_rows
                        ],
                    },
                )

                if filtered_rows and not best_rows:
                    best_engine = engine_name
                    best_rows = filtered_rows

                if WebFreeSearchTool._rows_are_usable(query, filtered_rows):
                    _write_debug_payload(
                        debug_run_id,
                        engine_name,
                        "decision",
                        {
                            "query": query,
                            "effective_query": effective_query,
                            "decision": "accepted",
                            "row_summary": _summarize_rows(filtered_rows),
                            "rows": filtered_rows,
                        },
                    )
                    return engine_name, filtered_rows

                errors.append(f"{engine_name}: low-quality or empty result")

        if best_rows:
            _write_debug_payload(
                debug_run_id,
                best_engine or "fallback",
                "decision",
                {
                    "query": query,
                    "decision": "best_effort",
                    "row_summary": _summarize_rows(best_rows),
                    "rows": best_rows,
                },
            )
            return best_engine, best_rows

        _write_debug_payload(
            debug_run_id,
            "final",
            "decision",
            {"query": query, "decision": "error", "errors": errors},
        )
        raise build_error(StatusCode.TOOL_WEB_SEARCH_ALL_ENGINES_FAILED, errors=" | ".join(errors))

    @staticmethod
    def _engine_display_name(engine: str) -> str:
        """Get human-readable engine name."""
        mapping = {
            "duckduckgo": "DuckDuckGo",
            "duckduckgo-jina": "DuckDuckGo (via jina.ai)",
            "bing": "Bing",
        }
        return mapping.get(engine, engine)

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> str:
        """Invoke the free web search tool."""
        query = str(inputs.get("query", "") or "").strip()
        max_results = _safe_int(inputs.get("max_results", 8) or 8, 8)
        timeout_seconds = _safe_int(inputs.get("timeout_seconds", 20) or 20, 20)

        if not query:
            return "[ERROR]: query cannot be empty."

        max_results = max(1, min(max_results, 20))
        timeout_seconds = max(5, min(timeout_seconds, 60))
        try:
            async with _http._new_session() as session:
                engine_used, rows = await WebFreeSearchTool._search_free(
                    session, query, max_results, timeout_seconds
                )
        except Exception as exc:  # noqa: BLE001
            return f"[ERROR]: free search failed: {exc}"

        if not rows:
            return f"No search results for: {query}"

        lines = [f"Free search results ({WebFreeSearchTool._engine_display_name(engine_used)}) for: {query}"]
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {row['title']}")
            lines.append(f"   URL: {row['url']}")
            if row.get("snippet"):
                lines.append(f"   Snippet: {row['snippet']}")

        top_fetch_urls: list[str] = []
        for row in rows:
            url = str(row.get("url", "") or "")
            if not url:
                continue
            top_fetch_urls.append(url)
            if len(top_fetch_urls) >= 3:
                break
        lines.append("")
        lines.append(
            "Required next step: before reformulating the query, fetch at least 2 relevant URLs from the top "
            "results. If the first fetch fails, is a dynamic shell page, or is still incomplete, continue with "
            "the next recommended URLs instead of searching again."
        )
        if top_fetch_urls:
            lines.append("Recommended fetch targets:")
            for idx, url in enumerate(top_fetch_urls, 1):
                lines.append(f"{idx}. {url}")
        return "\n".join(lines)

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
