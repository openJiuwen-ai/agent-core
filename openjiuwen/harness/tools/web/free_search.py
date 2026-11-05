# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async free web search tool (DuckDuckGo / Bing).

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
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote_plus, unquote, urlencode, urljoin, urlparse
from uuid import uuid4

import aiohttp
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.foundation.llm import Model
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.web._common import (
    _FREE_SEARCH_BING_ENABLED_ENV,
    _FREE_SEARCH_BING_MARKET_ENV,
    _FREE_SEARCH_BING_FALLBACK_MARKET_ENV,
    _FREE_SEARCH_DDG_ENABLED_ENV,
    _FREE_SEARCH_DEBUG_DIR_ENV,
    _FREE_SEARCH_DEBUG_ENV,
    _decode_ddg_redirect,
    _decode_bing_redirect,
    _duckduckgo_search_url,
    _domain_allowed,
    _env_flag,
    _normalized_domain,
    _parse_html,
    _parse_http_url,
    _safe_int,
    _search_request_headers,
    _strip_tags,
)
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._decode import _decode_response_text
from openjiuwen.harness.tools.web._budget import WebResearchBudget, get_web_budget
from openjiuwen.harness.tools.web._assessment import SearchAssessment, assess_candidates, resolve_search_model
from openjiuwen.harness.tools.web._quality import WebContentError, challenge_reason

# These endpoints are used only when the caller has explicitly constrained
# search to domestic academic domains.  They deliberately stay in this
# module, instead of turning the generic web-search tool into a domestic-only
# tool for every caller.
_DOMESTIC_SEARCH_DOMAINS = (
    "scholar.baidu.com",
    "xueshu.baidu.com",
    "cnki.net",
    "wanfangdata.com.cn",
)
_DOMESTIC_ENGINE_NAMES = frozenset({"baidu-scholar", "baidu-web", "cnki", "wanfang"})
_BING_MARKETS = {"en-US": ("en-US", "US"), "zh-CN": ("zh-Hans", "CN")}


@dataclass(frozen=True)
class _FreeSearchRequest:
    """Inputs for one free-search fallback pass."""

    session: aiohttp.ClientSession
    query: str
    max_results: int
    timeout_seconds: int
    proxy_url: str | None = None
    allowed_domains: tuple[str, ...] | None = None
    enabled_engines: frozenset[str] | tuple[str, ...] | None = None
    provider: str = "auto"
    budget: WebResearchBudget | None = None
    model: Model | None = None
    original_task: str = ""


def _select_search_candidates(
    query: str,
    rows: list[dict[str, str]],
    *,
    max_results: int | None = None,
    allowed_domains: tuple[str, ...] | None = None,
    assessment: SearchAssessment | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate targets deterministically; semantic labels come only from the model."""
    judgments = {item.id: item for item in assessment.results} if assessment else {}
    order = {item.id: rank for rank, item in enumerate(assessment.results)} if assessment else {}
    assessment_status = assessment.status if assessment else "unavailable"
    unavailable_reason = (assessment.reason if assessment else "") or "assessment_unavailable"
    decisions: list[dict[str, Any]] = []
    ranked: list[tuple[int, int, int, dict[str, str]]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        url = str(row.get("url", "") or "").strip()
        title = str(row.get("title", "") or "").strip()
        if not title or _parse_http_url(url) is None:
            relevance, reasons = "rejected", ["invalid_target"]
        elif url in seen:
            relevance, reasons = "rejected", ["duplicate_url"]
        elif _is_search_navigation_url(url):
            relevance, reasons = "rejected", ["search_navigation"]
        elif not _domain_allowed(url, allowed_domains):
            relevance, reasons = "rejected", ["outside_allowed_domains"]
        else:
            seen.add(url)
            judgment = judgments.get(index)
            relevance = judgment.relevance if judgment else "uncertain"
            reasons = [judgment.reason] if judgment else [unavailable_reason]
        decisions.append({
            "index": index,
            "row": dict(row),
            "relevance": relevance,
            "reasons": reasons,
            "selected": False,
        })
        if relevance == "rejected":
            continue
        candidate = dict(row, url=url, relevance=relevance, relevance_reason="; ".join(reasons))
        candidate["assessment_status"] = assessment_status
        ranked.append((int(relevance == "uncertain"), order.get(index, index), index, candidate))
    selected = []
    uncertain_count = 0
    for _, _, index, row in sorted(ranked, key=lambda entry: (entry[0], entry[1])):
        decision = decisions[index]
        if max_results is not None and len(selected) >= max_results:
            decision["reasons"] = decision["reasons"] + ["result_limit"]
        elif assessment_status == "ok" and row["relevance"] == "uncertain" and uncertain_count >= 2:
            decision["reasons"] = decision["reasons"] + ["uncertain_limit"]
        else:
            selected.append(row)
            decision["selected"] = True
            uncertain_count += int(row["relevance"] == "uncertain")
    return selected, decisions


def _parse_ddg_reader(text: str, max_results: int) -> list[dict[str, str]]:
    """Bind each DDG Markdown heading to its own summary, not adjacent links."""
    markdown = text.partition("Markdown Content:")[2] if "Markdown Content:" in text else text
    soup = _parse_html(MarkdownIt("commonmark", {"html": False}).render(markdown))
    headings = [heading for heading in soup.select("h1, h2, h3, h4") if heading.find("a", href=True)]
    blocks = headings or (soup.body or soup).find_all("p", recursive=False)
    rows = []
    seen = set()
    for block in blocks:
        anchor = block.find("a", href=True)
        if anchor is None or anchor.find("img") is not None:
            continue
        href = _decode_ddg_redirect(unescape(str(anchor["href"]))).strip()
        parsed = _parse_http_url(href)
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or parsed is None:
            continue
        host = (parsed.hostname or "").casefold()
        if host == "duckduckgo.com" or host.endswith(".duckduckgo.com") or href in seen:
            continue
        snippets = []
        for sibling in block.next_siblings:
            if getattr(sibling, "name", None) in {"h1", "h2", "h3", "h4"}:
                break
            if getattr(sibling, "name", None) != "p":
                continue
            anchors = sibling.find_all("a", href=True)
            targets = {_decode_ddg_redirect(unescape(str(link["href"]))).strip() for link in anchors}
            if targets and targets != {href}:
                if not headings:
                    break
                continue
            snippet = " ".join(sibling.get_text(" ", strip=True).split())
            display_url = re.sub(r"^https?://", "", href).rstrip("/")
            if snippet and snippet != title and snippet.rstrip("/") != display_url:
                snippets.append(snippet)
        rows.append({"title": title, "url": href, "snippet": " ".join(snippets), "source": "duckduckgo-jina"})
        seen.add(href)
        if len(rows) >= max_results:
            break
    return rows


def _is_search_navigation_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    engine_domains = ("bing.com", "duckduckgo.com")
    engine_host = any(host == domain or host.endswith("." + domain) for domain in engine_domains)
    return engine_host and parsed.path.rstrip("/") in {"", "/s", "/ns", "/search", "/html", "/link"}


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


def _record_search_response(
    run_id: str | None,
    engine: str,
    query: str,
    request_url: str,
    final_url: str,
    status: int,
    text: str,
    truncated: bool,
) -> None:
    """Record source-stage evidence without credentials or request headers."""
    if not _is_debug_enabled():
        return
    _write_debug_payload(run_id or uuid4().hex, engine, "response", {
        "query": query, "request_url": request_url, "final_url": final_url,
        "status_code": status, "response_truncated": truncated,
        "source_chars": len(text), "excerpt_truncated": len(text) > 30000,
        "raw_html": text,
    })


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
            parsed = _parse_http_url(href)
            if parsed is None or "bing.com" in parsed.netloc.lower():
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


def _domestic_scope_requested(allowed_domains: tuple[str, ...] | None) -> bool:
    """Return whether the allowlist includes one of the domestic sources."""
    if not allowed_domains:
        return False
    return any(
        _domain_allowed(f"https://{domain}/", allowed_domains)
        for domain in _DOMESTIC_SEARCH_DOMAINS
    )


def _is_domestic_navigation_url(url: str, *, source: str = "") -> bool:
    """Filter search/home/navigation links returned by domestic portals."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower() or "/"
    domain = _normalized_domain(url)
    if path == "/":
        return True
    blocked_paths = {
        "/s",
        "/search",
        "/paper",
        "/periodical",
        "/thesis",
        "/conference",
        "/nstr",
        "/patent",
        "/standard",
        "/cstad",
        "/claw",
        "/book",
        "/video",
        "/localchronicle",
        "/kns8s/defaultresult/index",
        "/defaultresult/index",
    }
    if path in blocked_paths:
        return True
    if domain.endswith("cnki.net") and "defaultresult" in path and "detail" not in path:
        return True
    if domain.endswith("wanfangdata.com.cn") and domain != "d.wanfangdata.com.cn":
        return True
    if domain.endswith("cnki.net") and domain not in {"kns.cnki.net", "www.cnki.net"}:
        return True
    if domain.endswith("baidu.com") and domain not in {"scholar.baidu.com", "xueshu.baidu.com"}:
        return True
    if source == "wanfang" and domain != "d.wanfangdata.com.cn":
        return True
    return False


def _parse_domestic_search_html(
    html: str,
    max_results: int,
    *,
    base_url: str,
    source: str,
    allowed_domains: tuple[str, ...] | None,
) -> list[dict[str, str]]:
    """Parse result links from Baidu Scholar, CNKI, or Wanfang HTML."""
    soup = _parse_html(html)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    navigation_titles = {"首页", "登录", "注册", "帮助", "下一页", "上一页"}

    # The three portals have changed CSS names over time.  Selecting all
    # anchors after the portal-specific selectors keeps this parser useful on
    # both server-rendered and lightly revised pages; URL and title filters
    # remove navigation links and shell-page noise.
    selectors = (
        "a.sc_content_title[href]",
        "a.result-table-list-title[href]",
        "a.fz14[href]",
        "a.title[href]",
        "a[href]",
    )
    anchors: list[Any] = []
    for selector in selectors:
        anchors.extend(soup.select(selector))

    for anchor in anchors:
        href = str(anchor.get("href", "") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        try:
            absolute = urljoin(base_url, unescape(href))
        except ValueError:
            continue
        if _parse_http_url(absolute) is None:
            continue
        if allowed_domains and not _domain_allowed(absolute, allowed_domains):
            continue
        if _is_domestic_navigation_url(absolute, source=source) or absolute in seen:
            continue

        title = anchor.get_text(" ", strip=True) or str(anchor.get("title", "") or "").strip()
        title = re.sub(r"\s+", " ", unescape(title)).strip()
        if len(title) < 4 or title in navigation_titles:
            continue

        snippet = ""
        container = anchor.find_parent(["article", "li", "div"])
        if container is not None:
            for selector in (".abstract", ".summary", ".snippet", ".source", "p"):
                node = container.select_one(selector)
                if node is not None:
                    snippet = node.get_text(" ", strip=True)
                    if snippet and snippet != title:
                        break
        seen.add(absolute)
        rows.append(
            {
                "title": title,
                "url": absolute,
                "snippet": re.sub(r"\s+", " ", snippet).strip()[:1000],
                "origin": source,
                "date": "",
                "source": source,
            }
        )
        if len(rows) >= max_results:
            break
    return rows


class WebFreeSearchTool(Tool):
    """Free search via global engines or constrained domestic academic portals."""

    accepts_tool_callback_context = True

    def __init__(
        self,
        language: str = "cn",
        agent_id: str | None = None,
        card: ToolCard | None = None,
        proxy_url: str | None = None,
        allowed_domains: tuple[str, ...] | None = None,
        enabled_engines: tuple[str, ...] | None = None,
        model: Model | None = None,
    ):
        super().__init__(
            card or build_tool_card("free_search", "WebFreeSearchTool", language, agent_id=agent_id)
        )
        self._model = model
        self._proxy_url = str(proxy_url or "").strip() or None
        self._allowed_domains = allowed_domains
        self._enabled_engines = (
            frozenset(str(engine).strip().lower() for engine in enabled_engines if str(engine).strip())
            if enabled_engines is not None
            else None
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
        proxy_url: str | None = None,
        *,
        debug_run_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Search the DuckDuckGo HTML endpoint and parse results."""
        url = _duckduckgo_search_url(query)
        status, headers, body, final_url, truncated = await _http.request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            max_bytes=2_000_000,
        )
        html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        _record_search_response(debug_run_id, "duckduckgo", query, url, final_url, status, html, truncated)
        if WebFreeSearchTool._is_ddg_challenge_page(status, html):
            raise WebContentError(
                "blocked", "DuckDuckGo requires human verification; do not bypass it via another endpoint.",
            )
        if truncated:
            raise WebContentError("truncated", "DuckDuckGo response exceeded the search response size limit.")
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
        proxy_url: str | None = None,
        *,
        debug_run_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Search DuckDuckGo via the jina.ai proxy and parse markdown results."""
        url = f"https://r.jina.ai/http://duckduckgo.com/html/?q={quote_plus(query)}"
        status, headers, body, final_url, truncated = await _http.request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            max_bytes=2_000_000,
        )
        text = _decode_response_text(body, content_type=headers.get("Content-Type", "")) or ""
        _record_search_response(debug_run_id, "duckduckgo-jina", query, url, final_url, status, text, truncated)
        blocked = challenge_reason(text, status=status)
        if blocked:
            raise WebContentError("blocked", blocked)
        _http.raise_for_status_with_body(status, body, engine="duckduckgo-jina")
        if truncated:
            raise WebContentError("truncated", "Reader response exceeded the search response size limit.")
        if re.search(r"(?im)^Warning:.*returned error [45]\d{2}", text):
            raise WebContentError("source_error", "Reader could not retrieve the requested search page.")
        return _parse_ddg_reader(text, max_results)

    @staticmethod
    async def _search_bing(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
        *,
        debug_run_id: str | None = None,
        proxy_url: str | None = None,
        market: str | None = None,
    ) -> list[dict[str, str]]:
        """Search Bing and parse standard SERP result blocks."""
        market = market or WebFreeSearchTool._bing_market_candidates()[0]
        if market not in _BING_MARKETS:
            raise WebContentError("invalid_input", "Unsupported Bing market; use en-US or zh-CN.")
        language, country = _BING_MARKETS[market]
        url = "https://www.bing.com/search?" + urlencode({
            "q": query, "setlang": language, "mkt": market, "cc": country,
        })

        status, headers, body, final_url, truncated = await _http.request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            max_bytes=2_000_000,
        )
        html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        _record_search_response(debug_run_id, "bing", query, url, final_url, status, html, truncated)
        soup = _parse_html(html)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        blocked = challenge_reason(html, title=title, status=status)
        if blocked:
            raise WebContentError("blocked", blocked)
        _http.raise_for_status_with_body(status, body, engine="bing")
        if truncated:
            raise WebContentError("truncated", "Bing response exceeded the search response size limit.")
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

        for row in _extract_bing_answer_cards(soup, url):
            row_url = str(row.get("url", "") or "")
            if row_url != url and row_url not in seen:
                seen.add(row_url)
                rows.append(row)

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
    async def _search_domestic(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
        *,
        engine: str,
        proxy_url: str | None = None,
        allowed_domains: tuple[str, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Search one of the public domestic academic portals."""
        endpoint_templates = {
            "baidu-scholar": "https://xueshu.baidu.com/s?wd={query}",
            "baidu-web": "https://www.baidu.com/s?wd={query}",
            "cnki": "https://kns.cnki.net/kns8s/defaultresult/index?kw={query}",
            "wanfang": "https://s.wanfangdata.com.cn/paper?q={query}",
        }
        endpoint = endpoint_templates.get(engine)
        if endpoint is None:
            raise ValueError(f"unsupported domestic search engine: {engine}")
        url = endpoint.format(query=quote_plus(query))
        status, headers, body, final_url, _truncated = await _http.request(
            session,
            "GET",
            url,
            headers=_search_request_headers(query),
            timeout_seconds=timeout_seconds,
            max_bytes=2_000_000,
            proxy_url=proxy_url,
        )
        _http.raise_for_status_with_body(status, body, engine=engine)
        endpoint_domain_allowed = _domain_allowed(final_url, allowed_domains)
        if engine == "baidu-web" and (
            _normalized_domain(final_url) == "baidu.com"
            or _normalized_domain(final_url).endswith(".baidu.com")
        ):
            endpoint_domain_allowed = True
        if allowed_domains and not endpoint_domain_allowed:
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR,
                engine=engine,
                reason="search endpoint redirected outside the configured domestic domains",
            )
        html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        return _parse_domestic_search_html(
            html,
            max_results,
            base_url=final_url or url,
            source=engine,
            allowed_domains=allowed_domains,
        )

    @staticmethod
    def _rows_are_usable(query: str, rows: list[dict[str, str]]) -> bool:
        """Decide whether a result page is good enough to stop engine fallback."""
        return bool(rows)

    @staticmethod
    def _bing_market_candidates() -> list[str]:
        """Choose at most two market profiles, independently of query language."""
        primary = (os.environ.get(_FREE_SEARCH_BING_MARKET_ENV) or "en-US").strip() or "en-US"
        fallback = (os.environ.get(_FREE_SEARCH_BING_FALLBACK_MARKET_ENV) or "").strip()
        if not fallback:
            fallback = "zh-CN" if primary.lower() == "en-us" else "en-US"
        supported = {name.lower(): name for name in _BING_MARKETS}
        markets: list[str] = []
        for name, value in (
            (_FREE_SEARCH_BING_MARKET_ENV, primary),
            (_FREE_SEARCH_BING_FALLBACK_MARKET_ENV, fallback),
        ):
            if name == _FREE_SEARCH_BING_FALLBACK_MARKET_ENV and value.lower() == "none":
                break
            market = supported.get(value.lower())
            if market is None:
                raise WebContentError(
                    "invalid_input", f"Unsupported {name}; use en-US or zh-CN (fallback also accepts none).",
                )
            if market not in markets:
                markets.append(market)
        return markets

    @staticmethod
    async def _search_free(request: _FreeSearchRequest) -> tuple[str, list[dict[str, str]]]:
        """Search configured sources, rejecting unrelated results before fallback."""
        session = request.session
        query = request.query
        max_results = request.max_results
        candidate_limit = min(30, max(10, max_results * 3))
        timeout_seconds = request.timeout_seconds
        proxy_url = request.proxy_url
        allowed_domains = request.allowed_domains
        enabled_engines = request.enabled_engines
        errors: list[str] = []
        debug_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:8]
        failures: list[str] = []

        engines: list[tuple[str, Any]] = []
        configured_engines = (
            frozenset(str(engine).strip().lower() for engine in enabled_engines)
            if enabled_engines is not None
            else None
        )
        ddg_enabled = (
            any(name in configured_engines for name in {"duckduckgo", "ddg"})
            if configured_engines is not None
            else _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False)
        )
        bing_enabled = (
            "bing" in configured_engines
            if configured_engines is not None
            else _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False)
        )
        domestic_scope = _domestic_scope_requested(allowed_domains)
        budget = request.budget or WebResearchBudget.from_env()
        if domestic_scope and request.budget is None:
            budget.max_engines = len(_DOMESTIC_ENGINE_NAMES)
        if domestic_scope:
            for engine_name in ("baidu-scholar", "baidu-web", "cnki", "wanfang"):
                engines.append((engine_name, WebFreeSearchTool._search_domestic))
        if not domestic_scope and ddg_enabled:
            engines.extend(
                [
                    ("duckduckgo", WebFreeSearchTool._search_duckduckgo),
                    ("duckduckgo-jina", WebFreeSearchTool._search_duckduckgo_via_jina),
                ]
            )
        if not domestic_scope and bing_enabled:
            engines.append(("bing", WebFreeSearchTool._search_bing))
        provider = request.provider.strip().lower()
        if provider == "ddg":
            provider = "duckduckgo"
        if provider != "auto":
            if provider not in {"bing", "duckduckgo"}:
                raise WebContentError("invalid_input", "Unknown free-search provider; use auto, bing, or duckduckgo.")
            provider_engines = {provider}
            if provider == "duckduckgo":
                provider_engines.add("duckduckgo-jina")
            engines = [(name, runner) for name, runner in engines if name in provider_engines]

        if not engines:
            reason = (
                "all free search engines are disabled"
                if provider == "auto"
                else "The requested free-search provider is disabled or outside the configured source scope."
            )
            raise WebContentError("disabled", reason)

        blocked_providers: set[str] = set()
        for engine_name, runner in engines:
            provider_name = "duckduckgo" if engine_name == "duckduckgo-jina" else engine_name
            if provider_name in blocked_providers:
                continue
            markets = WebFreeSearchTool._bing_market_candidates() if engine_name == "bing" else [None]
            for market_index, market in enumerate(markets):
                effective_query = query
                # Preserve both attempts instead of overwriting primary-market evidence.
                attempt_run_id = f"{debug_run_id}_{market}" if market else debug_run_id
                try:
                    # An alternate market belongs to the same budgeted search.
                    if market_index == 0:
                        budget.reserve_search(engine_name)
                    attempt_timeout = budget.remaining(timeout_seconds)
                    if allowed_domains and engine_name not in _DOMESTIC_ENGINE_NAMES:
                        effective_query = (
                            f"{effective_query} ({' OR '.join(f'site:{domain}' for domain in allowed_domains)})"
                        )
                    if engine_name == "baidu-web" and allowed_domains:
                        effective_query = " OR ".join(
                            f"site:{domain} {query}" for domain in allowed_domains
                        )
                    if engine_name in _DOMESTIC_ENGINE_NAMES:
                        rows = await runner(
                            session,
                            effective_query,
                            candidate_limit,
                            attempt_timeout,
                            engine=engine_name,
                            proxy_url=proxy_url,
                            allowed_domains=allowed_domains,
                        )
                    elif engine_name == "bing":
                        rows = await runner(session, effective_query, candidate_limit, attempt_timeout,
                                            debug_run_id=attempt_run_id, proxy_url=proxy_url, market=market)
                    else:
                        rows = await runner(
                            session,
                            effective_query,
                            candidate_limit,
                            attempt_timeout,
                            proxy_url=proxy_url,
                            debug_run_id=attempt_run_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    status = exc.status if isinstance(exc, WebContentError) else "transport_error"
                    failures.append(status)
                    if status == "blocked":
                        blocked_providers.add(provider_name)
                    errors.append(f"{engine_name}: {exc}")
                    _write_debug_payload(
                        attempt_run_id,
                        engine_name,
                        "error",
                        {"query": query, "effective_query": effective_query, "market": market, "error": str(exc)},
                    )
                    break

                _write_debug_payload(
                    attempt_run_id,
                    engine_name,
                    "candidate",
                    {
                        "query": query,
                        "effective_query": effective_query,
                        "market": market,
                        "row_summary": _summarize_rows(rows),
                        "rows": rows,
                    },
                )
                _, structural_decisions = _select_search_candidates(query, rows, allowed_domains=allowed_domains)
                candidates = []
                for decision in structural_decisions:
                    if decision["relevance"] != "rejected":
                        candidates.append((decision["index"], decision["row"]))
                assessment_timeout = max(
                    1, min(_safe_int(os.environ.get("FREE_SEARCH_ASSESSMENT_TIMEOUT_SECONDS"), 15), 60),
                )
                assessment = await assess_candidates(
                    request.model, query, candidates, original_task=request.original_task,
                    timeout_seconds=budget.remaining(assessment_timeout),
                )
                budget.remaining(assessment_timeout)
                filtered_rows, decisions = _select_search_candidates(
                    query, rows, max_results=max_results, allowed_domains=allowed_domains, assessment=assessment,
                )
                tool_logger.info(
                    "[FreeSearchAssessment] engine=%s market=%s status=%s candidates=%s selected=%s "
                    "elapsed_ms=%s reason=%s error_type=%s",
                    engine_name, market, assessment.status, len(candidates), len(filtered_rows),
                    assessment.elapsed_ms, assessment.reason, assessment.error_type,
                )
                _write_debug_payload(
                    attempt_run_id,
                    engine_name,
                    "filtered",
                    {
                        "query": query,
                        "effective_query": effective_query,
                        "market": market,
                        "candidate_count": len(rows),
                        "selected_count": len(filtered_rows),
                        "decisions": decisions,
                        "assessment": {
                            "status": assessment.status,
                            "reason": assessment.reason,
                            "elapsed_ms": assessment.elapsed_ms,
                            "error_type": assessment.error_type,
                        },
                        "row_summary": _summarize_rows(filtered_rows),
                        "rows": filtered_rows,
                    },
                )

                if WebFreeSearchTool._rows_are_usable(query, filtered_rows):
                    _write_debug_payload(
                        attempt_run_id,
                        engine_name,
                        "decision",
                        {
                            "query": query,
                            "effective_query": effective_query,
                            "market": market,
                            "decision": "accepted",
                            "row_summary": _summarize_rows(filtered_rows),
                            "rows": filtered_rows,
                        },
                    )
                    return engine_name, filtered_rows

                status = "low_relevance" if rows else "empty"
                failures.append(status)
                attempt_name = f"{engine_name} ({market})" if market else engine_name
                errors.append(f"{attempt_name}: {status}")
                _write_debug_payload(attempt_run_id, engine_name, "decision", {
                    "query": query, "effective_query": effective_query, "market": market,
                    "decision": status, "rejected_count": len(rows),
                })
                if status != "low_relevance":
                    break

        _write_debug_payload(
            debug_run_id,
            "final",
            "decision",
            {"query": query, "decision": "error", "errors": errors},
        )
        failure_priority = ("budget_exhausted", "blocked", "truncated", "source_error", "low_relevance", "empty")
        status = next((value for value in failure_priority if value in failures), "transport_error")
        raise WebContentError(
            status,
            " | ".join(errors) + ". No usable evidence returned. Do not fetch rejected results "
            "or repeatedly reformulate the same query; report what is missing.",
        )

    @staticmethod
    def _engine_display_name(engine: str) -> str:
        """Get human-readable engine name."""
        mapping = {
            "duckduckgo": "DuckDuckGo",
            "duckduckgo-jina": "DuckDuckGo (via jina.ai)",
            "bing": "Bing",
            "baidu-scholar": "Baidu Scholar",
            "baidu-web": "Baidu Web (academic-only results)",
            "cnki": "CNKI",
            "wanfang": "Wanfang Data",
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
            callback = kwargs.get("_tool_callback_context")
            budget = get_web_budget(callback)
            model = resolve_search_model(callback, self._model)
            async with _http.new_session() as session:
                engine_used, rows = await WebFreeSearchTool._search_free(
                    _FreeSearchRequest(
                        session=session,
                        query=query,
                        max_results=max_results,
                        timeout_seconds=timeout_seconds,
                        proxy_url=self._proxy_url,
                        allowed_domains=self._allowed_domains,
                        enabled_engines=self._enabled_engines,
                        provider=str(inputs.get("provider", "auto") or "auto"),
                        budget=budget,
                        model=model,
                        original_task=budget.original_task if budget else "",
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            return f"[ERROR]: free search failed: {exc}"

        if not rows:
            return f"Content status: empty. No search results for: {query}"

        self._on_search_results(rows)
        lines = [f"Free search results ({WebFreeSearchTool._engine_display_name(engine_used)}) for: {query}"]
        if any(row.get("relevance") != "uncertain" for row in rows):
            lines.append(
                "Content status: ok. These are discovery candidates, not verified facts. "
                "Publication/market time is unverified until read at the source."
            )
        else:
            lines.append(
                "Content status: uncertain. Only provisional candidates were found. "
                "Verify one promising source before answering; do not treat them as relevant or current facts yet."
            )
        assessment_unavailable = any(row.get("assessment_status") == "unavailable" for row in rows)
        if assessment_unavailable:
            lines.append(
                "Assessment status: unavailable. Relevance was not assessed; these are unverified leads, "
                "not a successful relevance check. Do not retry markets just because assessment failed."
            )
            lines.append(
                "Candidates are retained up to max_results in source order, not relevance order. "
                "Compare all leads for the requested entity, evidence and original source before choosing a page; "
                "do not fetch every candidate."
            )
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {row['title']}")
            lines.append(f"   URL: {row['url']}")
            if row.get("snippet"):
                lines.append(f"   Snippet: {row['snippet']}")
            if row.get("date"):
                lines.append(f"   Search-reported date (unverified): {row['date']}")
            if row.get("relevance") == "uncertain":
                lines.append(f"   Relevance: uncertain ({row['relevance_reason']}); verify at the source.")

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
            "Next step: fetch only a relevant source needed to verify the requested fact. Stop when evidence "
            "is sufficient; a second fetch is not mandatory. Check entity, publication/observation time, "
            "and missing fields. Do not guess article URLs or treat a search snippet as a current quote. "
            "Respect retrieval limits; do not switch to paid search unless authorized."
        )
        if top_fetch_urls and not assessment_unavailable:
            lines.append("Recommended fetch targets:")
            for idx, url in enumerate(top_fetch_urls, 1):
                lines.append(f"{idx}. {url}")
        return "\n".join(lines)

    def _on_search_results(self, rows: list[dict[str, str]]) -> None:
        """Hook for host provenance recording after successful quality filtering."""
        return None

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
