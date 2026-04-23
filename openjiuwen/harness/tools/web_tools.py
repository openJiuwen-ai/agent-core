# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts.sections.tools import build_tool_card

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}
_CHARSET_HEADER_RE = re.compile(r"charset=([^\s;]+)", flags=re.IGNORECASE)
_CHARSET_META_RE = re.compile(
    br"""<meta[^>]+charset=["']?\s*([A-Za-z0-9._-]+)""",
    flags=re.IGNORECASE,
)
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
_FREE_SEARCH_DEBUG_ENV = "FREE_SEARCH_DEBUG"
_FREE_SEARCH_DEBUG_DIR_ENV = "FREE_SEARCH_DEBUG_DIR"
_FREE_SEARCH_DDG_ENABLED_ENV = "FREE_SEARCH_DDG_ENABLED"
_FREE_SEARCH_BING_ENABLED_ENV = "FREE_SEARCH_BING_ENABLED"
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_FREE_SEARCH_DDG_URL_ENV = "FREE_SEARCH_DDG_URL"
_PAID_SEARCH_API_KEY_ENVS = (
    "BOCHA_API_KEY",
    "PERPLEXITY_API_KEY",
    "SERPER_API_KEY",
    "JINA_API_KEY",
)
_FREE_SEARCH_DEFAULT_NO_PROXY = (
    "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com, api.openai.rnd.huawei.com"
)
_FETCH_WEBPAGE_DEFAULT_MAX_CHARS = 20000
_FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS = 45
_FETCH_WEBPAGE_MAX_TIMEOUT_ENV = "MCP_FETCH_WEBPAGE_MAX_TIMEOUT_SECONDS"
_FETCH_WEBPAGE_MAX_CHARS_ENV = "MCP_FETCH_WEBPAGE_MAX_CHARS"
_MOJIBAKE_MARKERS = ("mojibake", "Ã", "Â", "â", "ï¿½")


def _get_free_search_proxy_url() -> str:
    """Return the configured proxy URL used by web search/fetch tools."""
    return str(os.environ.get(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _free_search_ssl_verify() -> bool:
    return _env_bool(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _disable_insecure_request_warning() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _duckduckgo_search_url(query: str) -> str:
    base_url = (
        os.environ.get(_FREE_SEARCH_DDG_URL_ENV, "https://html.duckduckgo.com/html/")
        or "https://html.duckduckgo.com/html/"
    ).strip()
    separator = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('?&')}{separator}q={quote_plus(query)}"


def _no_proxy_entries() -> list[str]:
    configured = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or _FREE_SEARCH_DEFAULT_NO_PROXY
    return [entry.strip().lower() for entry in configured.split(",") if entry.strip()]


def _should_bypass_free_search_proxy(url: str) -> bool:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url:
        return True
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not hostname:
        return False
    for entry in _no_proxy_entries():
        if entry == "*":
            return True
        if entry.startswith(".") and (hostname == entry[1:] or hostname.endswith(entry)):
            return True
        if hostname == entry or hostname.endswith(f".{entry}"):
            return True
    return False


def _apply_free_search_proxy(url: str, kwargs: dict[str, Any]) -> bool:
    """Apply the configured search proxy to a requests kwargs dict."""
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url or _should_bypass_free_search_proxy(url):
        return False
    kwargs.setdefault("proxies", {"http": proxy_url, "https": proxy_url})
    return True


def _http_request(method: str, url: str, **kwargs) -> requests.Response:
    """Try normal request first; retry without env proxies on ProxyError."""
    method_up = method.upper()
    explicit_proxy = _apply_free_search_proxy(url, kwargs)
    if "verify" not in kwargs:
        kwargs["verify"] = _free_search_ssl_verify()
        if kwargs["verify"] is False:
            _disable_insecure_request_warning()
    try:
        if method_up == "GET":
            return requests.get(url, **kwargs)
        if method_up == "POST":
            return requests.post(url, **kwargs)
        return requests.request(method_up, url, **kwargs)
    except requests.exceptions.ProxyError:
        if explicit_proxy:
            raise
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method_up, url, **kwargs)


def _strip_tags(value: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _decode_ddg_redirect(url: str) -> str:
    """Decode DuckDuckGo redirect URLs."""
    parsed = urlparse(url)
    if parsed.path != "/l/":
        return url
    query = parse_qs(parsed.query)
    target = query.get("uddg")
    if not target:
        return url
    return unquote(target[0])


def _decode_bing_redirect(url: str) -> str:
    """Decode Bing redirect URLs to extract the actual target URL."""
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc.lower() or parsed.path != "/ck/a":
        return url

    query = parse_qs(parsed.query)
    values = query.get("u")
    if not values:
        return url
    encoded = values[0]
    if not encoded:
        return url

    if encoded.startswith("a1"):
        payload = encoded[2:]
        padding = "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8")).decode(
                "utf-8",
                errors="ignore",
            )
            if decoded.startswith(("http://", "https://")):
                return decoded
        except ValueError:
            return url
    elif encoded.startswith(("http://", "https://")):
        return encoded

    return url


def _normalized_domain(url: str) -> str:
    """Return a normalized domain for ranking/filtering."""
    netloc = urlparse(url).netloc.lower().strip()
    if netloc.startswith("www."):
        return netloc[4:]
    return netloc


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


def _parse_html(html: str) -> BeautifulSoup:
    """Parse HTML with lxml when available, otherwise html.parser."""
    try:
        import lxml  # noqa: F401

        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


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


def _contains_cjk(value: str) -> bool:
    """Whether the string contains CJK Unified Ideographs."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in (value or ""))


def _env_flag(name: str, default: bool = True) -> bool:
    """Parse bool-like env values, preserving a default for empty values."""
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def is_free_search_enabled() -> bool:
    """Whether at least one free-search backend is enabled."""
    return (
        _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False)
        or _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False)
    )


def is_paid_search_enabled() -> bool:
    """Whether at least one paid-search provider API key is configured."""
    return any(str(os.environ.get(key, "") or "").strip() for key in _PAID_SEARCH_API_KEY_ENVS)


def _search_request_headers(query: str) -> dict[str, str]:
    """Return browser-like headers aligned to the query language."""
    headers = dict(_REQUEST_HEADERS)
    if _contains_cjk(query):
        headers["Accept-Language"] = "zh-CN,zh-Hans;q=0.9,zh;q=0.8,en;q=0.6"
    else:
        headers["Accept-Language"] = "en-US,en;q=0.9"
    return headers


def _query_terms(query: str) -> list[str]:
    """Extract lightweight query terms for scoring and diagnostics."""
    raw_terms = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", (query or "").lower())
    terms: list[str] = []
    for term in raw_terms:
        expanded_terms: list[str] = []
        if _contains_cjk(term):
            numeric_terms = re.findall(r"\d{4}|\d+\u6708|\d+\u65e5|\d+", term)
            expanded_terms.extend(numeric_terms)
            cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", term)
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
        if re.fullmatch(r"\d+\u6708|\d+\u65e5", term):
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


class WebFreeSearchTool(Tool):
    """Free search via DuckDuckGo. Input query and return ranked URLs with snippets."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
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
    def _search_duckduckgo_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
        """Search DuckDuckGo HTML endpoint and parse results synchronously."""
        url = _duckduckgo_search_url(query)
        response = _http_request("GET", url, headers=_search_request_headers(query), timeout=timeout_seconds)
        if WebFreeSearchTool._is_ddg_challenge_page(response.status_code, response.text):
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR,
                engine="duckduckgo",
                reason="anti-bot challenge page returned",
            )
        if response.status_code != 200:
            raise build_error(
                StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR,
                engine="duckduckgo",
                reason=f"non-200 status: {response.status_code}",
            )
        response.raise_for_status()
        html = response.text

        links = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        rows: list[dict[str, str]] = []
        for index, (href, title_raw) in enumerate(links[:max_results]):
            snippet_raw = ""
            if index < len(snippets):
                snippet_raw = snippets[index][0] or snippets[index][1] or ""
            rows.append(
                {
                    "title": _strip_tags(title_raw) or f"Result {index + 1}",
                    "url": _decode_ddg_redirect(href),
                    "snippet": _strip_tags(snippet_raw),
                    "source": "duckduckgo",
                }
            )
        return rows

    @staticmethod
    def _search_duckduckgo_via_jina_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
        """Search DuckDuckGo via jina.ai proxy and parse markdown results."""
        url = f"https://r.jina.ai/http://duckduckgo.com/html/?q={quote_plus(query)}"
        response = _http_request("GET", url, headers=_search_request_headers(query), timeout=timeout_seconds)
        response.raise_for_status()
        text = response.text or ""

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
    def _search_bing_sync(
        query: str,
        max_results: int,
        timeout_seconds: int,
        *,
        debug_run_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Search Bing and parse standard SERP result blocks synchronously."""
        if _contains_cjk(query):
            url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=zh-Hans&mkt=zh-CN&cc=CN"
        else:
            url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US&mkt=en-US&cc=US"

        response = _http_request("GET", url, headers=_search_request_headers(query), timeout=timeout_seconds)
        response.raise_for_status()
        html = response.text
        soup = _parse_html(html)
        results_area = _extract_bing_results_area(soup)

        _write_debug_payload(
            debug_run_id or "",
            "bing",
            "raw",
            {
                "query": query,
                "request_url": url,
                "status_code": response.status_code,
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
    def _search_free_sync(query: str, max_results: int, timeout_seconds: int) -> tuple[str, list[dict[str, str]]]:
        """Search using multiple free search engines with best-effort fallback."""
        errors: list[str] = []
        debug_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        best_engine = ""
        best_rows: list[dict[str, str]] = []

        engines: list[tuple[str, Any]] = []
        if _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False):
            engines.extend(
                [
                    ("duckduckgo", WebFreeSearchTool._search_duckduckgo_sync),
                    ("duckduckgo-jina", WebFreeSearchTool._search_duckduckgo_via_jina_sync),
                ]
            )
        if _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False):
            engines.append(("bing", WebFreeSearchTool._search_bing_sync))

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
                        rows = runner(effective_query, max_results, timeout_seconds, debug_run_id=debug_run_id)
                    else:
                        rows = runner(effective_query, max_results, timeout_seconds)
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

                if filtered_rows and not best_rows:
                    best_engine = engine_name
                    best_rows = filtered_rows
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

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the free web search tool."""
        query = str(inputs.get("query", "") or "").strip()
        max_results = int(inputs.get("max_results", 8) or 8)
        timeout_seconds = int(inputs.get("timeout_seconds", 20) or 20)

        if not query:
            return "[ERROR]: query cannot be empty."

        max_results = max(1, min(max_results, 20))
        timeout_seconds = max(5, min(timeout_seconds, 60))
        try:
            engine_used, rows = await asyncio.to_thread(
                WebFreeSearchTool._search_free_sync, query, max_results, timeout_seconds
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

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class WebPaidSearchTool(Tool):
    """Paid search via Bocha/Perplexity/SERPER/JINA. Support provider=auto|bocha|perplexity|serper|jina."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ):
        super().__init__(card or build_tool_card("paid_search", "WebPaidSearchTool", language, agent_id=agent_id))

    @staticmethod
    def _jina_search_sync(query: str, timeout_seconds: int) -> dict[str, Any]:
        """Search using Jina AI DeepSearch API synchronously."""
        jina_key = os.environ.get("JINA_API_KEY", "")
        if not jina_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="JINA_API_KEY")

        payload = {
            "model": "jina-deepsearch-v1",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "reasoning_effort": "low",
        }
        response = _http_request(
            "POST",
            "https://deepsearch.jina.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {jina_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        answer = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            answer = choices[0].get("message", {}).get("content", "")
        urls = re.findall(r"https?://[^\s)\]>\"']+", answer or "")
        return {"provider": "jina", "answer": (answer or "").strip(), "urls": urls}

    @staticmethod
    def _extract_bocha_urls(data: dict[str, Any], max_results: int) -> list[str]:
        """Extract result URLs from Bocha payloads."""
        candidates: list[Any] = []
        for container in (
            data.get("data", {}).get("webPages", {}).get("value"),
            data.get("webPages", {}).get("value"),
            data.get("data", {}).get("webPages"),
            data.get("webPages"),
            data.get("data", {}).get("results"),
            data.get("results"),
        ):
            if isinstance(container, list):
                candidates = container
                break

        urls: list[str] = []
        for item in candidates[:max_results]:
            if not isinstance(item, dict):
                continue
            maybe_url = item.get("url") or item.get("link")
            if maybe_url:
                urls.append(str(maybe_url))
        return urls

    @staticmethod
    def _extract_bocha_answer(data: dict[str, Any]) -> str:
        """Extract summary/answer text from Bocha payloads."""
        candidates = [
            data.get("summary"),
            data.get("answer"),
            data.get("data", {}).get("summary"),
            data.get("data", {}).get("answer"),
            data.get("data", {}).get("message"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()

        web_pages = data.get("data", {}).get("webPages", {})
        if isinstance(web_pages, dict):
            value = web_pages.get("value")
            if isinstance(value, list):
                snippets: list[str] = []
                for item in value[:3]:
                    if not isinstance(item, dict):
                        continue
                    snippet = item.get("summary") or item.get("snippet")
                    if isinstance(snippet, str) and snippet.strip():
                        snippets.append(snippet.strip())
                if snippets:
                    return "\n\n".join(snippets[:3])
        return ""

    @staticmethod
    def _bocha_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
        """Search using Bocha Web Search API synchronously."""
        bocha_key = os.environ.get("BOCHA_API_KEY", "")
        if not bocha_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="BOCHA_API_KEY")

        response = _http_request(
            "POST",
            os.environ.get("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search"),
            headers={"Authorization": f"Bearer {bocha_key}", "Content-Type": "application/json"},
            json={"query": query, "summary": True, "count": max_results},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "provider": "bocha",
            "answer": WebPaidSearchTool._extract_bocha_answer(data),
            "urls": WebPaidSearchTool._extract_bocha_urls(data, max_results),
        }

    @staticmethod
    def _serper_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
        """Search using Serper (Google Search API) synchronously."""
        serper_key = os.environ.get("SERPER_API_KEY", "")
        if not serper_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="SERPER_API_KEY")

        response = _http_request(
            "POST",
            "https://google.serper.dev/search",
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        urls: list[str] = []
        organic = data.get("organic", [])
        if isinstance(organic, list):
            for item in organic[:max_results]:
                if isinstance(item, dict) and item.get("link"):
                    urls.append(str(item["link"]))
        return {"provider": "serper", "answer": "", "urls": urls}

    @staticmethod
    def _parse_perplexity_citations(data: dict[str, Any]) -> list[str]:
        """Extract citation URLs from Perplexity API response."""
        for key in ("citations", "search_results", "web_search_results", "sources"):
            entries = data.get(key)
            if not isinstance(entries, list):
                continue
            urls: list[str] = []
            for item in entries:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    maybe_url = item.get("url") or item.get("link") or item.get("source_url")
                    if maybe_url:
                        urls.append(str(maybe_url))
            if urls:
                return urls
        return []

    @staticmethod
    def _perplexity_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
        """Search using Perplexity AI synchronously."""
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not perplexity_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="PERPLEXITY_API_KEY")

        payload = {
            "model": os.environ.get("PPLX_MODEL", "sonar-pro"),
            "messages": [
                {"role": "system", "content": "Provide concise answer and include citations."},
                {"role": "user", "content": query},
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "stream": False,
        }
        response = _http_request(
            "POST",
            os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/chat/completions"),
            headers={"Authorization": f"Bearer {perplexity_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        answer = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            answer = choices[0].get("message", {}).get("content", "")

        return {
            "provider": "perplexity",
            "answer": (answer or "").strip(),
            "urls": WebPaidSearchTool._parse_perplexity_citations(data)[:max_results],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the paid web search tool."""
        query = str(inputs.get("query", "") or "").strip()
        provider = str(inputs.get("provider", "auto") or "auto").strip().lower()
        max_results = int(inputs.get("max_results", 8) or 8)
        timeout_seconds = int(inputs.get("timeout_seconds", 45) or 45)

        if not query:
            return "[ERROR]: query cannot be empty."

        if provider not in {"auto", "bocha", "jina", "serper", "perplexity"}:
            return "[ERROR]: provider must be one of auto|bocha|jina|serper|perplexity."

        timeout_seconds = max(10, min(timeout_seconds, 120))
        max_results = max(1, min(max_results, 20))

        runners = {
            "bocha": lambda: WebPaidSearchTool._bocha_search_sync(
                query=query, max_results=max_results, timeout_seconds=timeout_seconds
            ),
            "jina": lambda: WebPaidSearchTool._jina_search_sync(query=query, timeout_seconds=timeout_seconds),
            "serper": lambda: WebPaidSearchTool._serper_search_sync(
                query=query, max_results=max_results, timeout_seconds=timeout_seconds
            ),
            "perplexity": lambda: WebPaidSearchTool._perplexity_search_sync(
                query=query, max_results=max_results, timeout_seconds=timeout_seconds
            ),
        }
        order = [provider] if provider != "auto" else ["bocha", "perplexity", "serper", "jina"]

        errors: list[str] = []
        for name in order:
            try:
                runner_process = runners.get(name)
                if runner_process is None:
                    errors.append(f"{name}: runner not found")
                    continue
                result = await asyncio.to_thread(runner_process)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue

            answer = str(result.get("answer", "") or "").strip()
            urls = [str(u) for u in (result.get("urls", []) or []) if u][:max_results]
            lines = [f"Paid search provider: {name}"]
            if answer:
                lines.append("Answer:")
                lines.append(answer)
            if urls:
                lines.append("URLs:")
                for idx, url in enumerate(urls, 1):
                    lines.append(f"{idx}. {url}")
            if not answer and not urls:
                lines.append("No usable result payload.")
            return "\n".join(lines)

        return "[ERROR]: paid search failed. " + " | ".join(errors)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class WebFetchWebpageTool(Tool):
    """Fetch webpage text content from URL. Returns status/title/plain text content."""

    def __init__(self, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("fetch_webpage", "WebFetchWebpageTool", language, agent_id=agent_id))

    @staticmethod
    def _extract_declared_charset(response: requests.Response) -> str:
        """Extract charset from response headers or HTML meta tags."""
        content_type = response.headers.get("Content-Type", "") or ""
        header_match = _CHARSET_HEADER_RE.search(content_type)
        if header_match:
            return header_match.group(1).strip().strip("\"'")

        head_bytes = (response.content or b"")[:4096]
        meta_match = _CHARSET_META_RE.search(head_bytes)
        if meta_match:
            try:
                return meta_match.group(1).decode("ascii", errors="ignore").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _score_decoded_text(value: str) -> float:
        """Score decoded text quality to avoid mojibake."""
        if not value:
            return float("-inf")
        score = 0.0
        score -= value.count("\ufffd") * 8
        for marker in _MOJIBAKE_MARKERS:
            score -= value.count(marker) * 3
        score += len(re.findall(r"[\u4e00-\u9fff]", value)) * 0.15
        score += len(re.findall(r"[A-Za-z0-9]", value)) * 0.02
        score -= len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)) * 5
        return score

    @staticmethod
    def _decode_response_text(response: requests.Response) -> str:
        """Decode response content with proper charset detection."""
        raw = response.content or b""
        if not raw:
            return ""

        declared = (WebFetchWebpageTool._extract_declared_charset(response) or "").lower()
        response_encoding = (response.encoding or "").strip().lower()
        apparent = (response.apparent_encoding or "").strip().lower()

        candidates: list[str] = []
        if declared and declared not in {"iso-8859-1", "latin-1", "latin1"}:
            candidates.append(declared)
        candidates.extend(
            [
                "utf-8-sig",
                "utf-8",
                apparent,
                response_encoding,
                "gbk",
                "gb18030",
                "big5",
                "shift_jis",
                "cp1252",
                "iso-8859-1",
            ]
        )

        decoded_candidates: list[tuple[float, str]] = []
        seen: set[str] = set()
        for enc in candidates:
            enc = (enc or "").strip().lower()
            if not enc or enc in seen:
                continue
            seen.add(enc)
            try:
                text = raw.decode(enc, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
            decoded_candidates.append((WebFetchWebpageTool._score_decoded_text(text), text))

        if decoded_candidates:
            decoded_candidates.sort(key=lambda item: item[0], reverse=True)
            return decoded_candidates[0][1]

        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _fetch_via_jina_reader_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
        """Fetch webpage content via jina.ai reader proxy."""
        reader_url = f"https://r.jina.ai/{url}"
        response = _http_request("GET", reader_url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        response.raise_for_status()
        return {
            "url": url,
            "status_code": response.status_code,
            "title": "",
            "content": WebFetchWebpageTool._decode_response_text(response).strip(),
        }

    @staticmethod
    def _extract_main_text_from_html(text: str) -> tuple[str, str]:
        """Extract main content and title from HTML."""
        soup = _parse_html(text)
        title_tag = soup.select_one("title")
        title = title_tag.get_text(" ", strip=True) if title_tag is not None else ""

        for selector in ("script", "style", "noscript", "svg", "canvas", "iframe"):
            for node in soup.select(selector):
                node.decompose()

        for selector in (
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "button",
            "[role='navigation']",
            ".nav",
            ".navbar",
            ".header",
            ".footer",
            ".sidebar",
            ".aside",
            ".recommend",
            ".related",
            ".share",
            ".breadcrumb",
            ".menu",
            ".toolbar",
        ):
            for node in soup.select(selector):
                node.decompose()

        candidate_selectors = [
            "main",
            "[role='main']",
            "article",
            ".article",
            ".article-content",
            ".article-body",
            ".post",
            ".post-content",
            ".entry-content",
            ".content",
            ".detail",
            ".news",
            "#content",
            "#main",
        ]
        best_text = ""
        for selector in candidate_selectors:
            node = soup.select_one(selector)
            if node is None:
                continue
            node_text = node.get_text("\n", strip=True)
            if len(node_text) > len(best_text):
                best_text = node_text

        if not best_text:
            body = soup.body or soup
            blocks: list[str] = []
            for node in body.select("p, li, h1, h2, h3"):
                piece = node.get_text(" ", strip=True)
                if len(piece) >= 20:
                    blocks.append(piece)
            best_text = "\n".join(blocks) if blocks else body.get_text("\n", strip=True)

        best_text = re.sub(r"\n{3,}", "\n\n", best_text).strip()
        return title, best_text

    @staticmethod
    def _fetch_webpage_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
        """Fetch webpage content synchronously with fallback to jina.ai reader."""
        response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        if response.status_code in {401, 403, 429}:
            return WebFetchWebpageTool._fetch_via_jina_reader_sync(url, timeout_seconds)
        response.raise_for_status()

        text = WebFetchWebpageTool._decode_response_text(response)
        content_type = response.headers.get("Content-Type", "")
        title = ""

        if "html" in content_type.lower():
            title, text = WebFetchWebpageTool._extract_main_text_from_html(text)
        else:
            text = re.sub(r"\s+", " ", text).strip()

        return {
            "url": response.url,
            "status_code": response.status_code,
            "title": title,
            "content": text,
        }

    @staticmethod
    def _clip_text(value: str, max_chars: int) -> str:
        """Clip text to maximum length with truncation indicator."""
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}\n...[truncated]"

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL by decoding redirects and ensuring protocol."""
        raw = (url or "").strip()
        if not raw:
            return raw
        decoded = _decode_ddg_redirect(raw)
        if decoded.startswith(("http://", "https://")):
            return decoded
        return f"https://{decoded}"

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the webpage fetch tool."""
        url = WebFetchWebpageTool._normalize_url(str(inputs.get("url", "") or ""))
        max_chars = int(inputs.get("max_chars", _FETCH_WEBPAGE_DEFAULT_MAX_CHARS) or _FETCH_WEBPAGE_DEFAULT_MAX_CHARS)
        timeout_seconds = int(
            inputs.get("timeout_seconds", _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS)
            or _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS
        )

        if not url:
            return "[ERROR]: url cannot be empty."

        max_chars_cap = int(os.environ.get(_FETCH_WEBPAGE_MAX_CHARS_ENV, "200000") or "200000")
        timeout_cap = int(os.environ.get(_FETCH_WEBPAGE_MAX_TIMEOUT_ENV, "600") or "600")
        if max_chars != 0:
            max_chars = max(500, min(max_chars, max_chars_cap))
        timeout_seconds = max(5, min(timeout_seconds, timeout_cap))

        try:
            data = await asyncio.to_thread(WebFetchWebpageTool._fetch_webpage_sync, url, timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return f"[ERROR]: failed to fetch webpage: {exc}"

        lines = [
            f"URL: {data.get('url', url)}",
            f"Status: {data.get('status_code', '')}",
        ]
        if data.get("title"):
            lines.append(f"Title: {data['title']}")
        lines.append("Content:")
        lines.append(WebFetchWebpageTool._clip_text(str(data.get("content", "") or ""), max_chars) or "[empty]")
        return "\n".join(lines)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


def create_web_tools(
    *,
    language: str = "cn",
    agent_id: Optional[str] = None,
    include_free_search: bool = True,
    include_paid_search: bool = True,
    include_fetch_webpage: bool = True,
) -> list[Tool]:
    """Create web tools, preferring configured paid search over free search."""
    tools: list[Tool] = []
    if include_paid_search and is_paid_search_enabled():
        tools.append(WebPaidSearchTool(language=language, agent_id=agent_id))
    if include_free_search and is_free_search_enabled():
        tools.append(WebFreeSearchTool(language=language, agent_id=agent_id))
    if include_fetch_webpage:
        tools.append(WebFetchWebpageTool(language=language, agent_id=agent_id))
    return tools
