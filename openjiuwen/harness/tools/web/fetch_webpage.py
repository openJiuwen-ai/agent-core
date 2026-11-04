# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async webpage fetch tool.

Fetches a URL with a capped streaming read (so an oversized body cannot
exhaust memory), decodes it with charset detection, and extracts the main
text. HTML parsing runs directly in the event loop.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlsplit

import aiohttp

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import (
    _FETCH_WEBPAGE_DEFAULT_MAX_BYTES,
    _FETCH_WEBPAGE_DEFAULT_MAX_CHARS,
    _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS,
    _FETCH_WEBPAGE_MAX_BYTES_ENV,
    _FETCH_WEBPAGE_MAX_CHARS_ENV,
    _FETCH_WEBPAGE_MAX_TIMEOUT_ENV,
    _REQUEST_HEADERS,
    _domain_allowed,
    _decode_ddg_redirect,
    _parse_html,
    _safe_int,
)
from openjiuwen.harness.tools.web._decode import _decode_response_text
from openjiuwen.harness.tools.web._budget import WebResearchBudget, get_web_budget
from openjiuwen.harness.tools.web._quality import WebContentError, challenge_reason


def _raise_fetch_http_error(url: str, status: int, body: bytes) -> None:
    """Raise a webpage-fetch error that includes the response body.

    Scoped to webpage fetch (distinct from the web-search engine error) so the
    surfaced message reads coherently instead of borrowing search semantics.

    Args:
        url: The page URL being fetched (surfaced in the error message).
        status: HTTP status code.
        body: Raw response body bytes.

    Raises:
        BaseError: ``TOOL_WEB_FETCH_EXECUTION_ERROR`` when status is >= 400.
    """
    reason = _http.format_http_error_reason(status, body)
    if not reason:
        return
    raise build_error(StatusCode.TOOL_WEB_FETCH_EXECUTION_ERROR, url=url, reason=reason)


@dataclass(frozen=True)
class _FetchRequest:
    """Inputs shared by the direct webpage fetch path."""

    session: aiohttp.ClientSession
    url: str
    timeout_seconds: int
    byte_cap: int
    proxy_url: str | None = None
    allowed_domains: tuple[str, ...] | None = None
    budget: WebResearchBudget | None = None


def _content_issue(title: str, content: str, raw: str = "") -> str | None:
    stripped = content.strip()
    if not stripped or stripped == title.strip():
        return "dynamic_shell" if "<script" in raw.casefold() else "empty"
    loading = re.search(r"enable javascript|javascript is required|please wait|正在加载|数据加载中|请开启.*javascript", stripped, re.IGNORECASE)
    placeholders = re.findall(r"(?m)^\s*(?:-|--|[\u2014\u2013]|\[missing\])\s*$", stripped)
    if loading and (len(stripped) < 200 or len(placeholders) >= 4):
        return "dynamic_shell"
    return None


def _require_text_response(body: bytes, content_type: str) -> None:
    """Reject documents/binary payloads before decoding them as webpage text."""
    media_type = content_type.partition(";")[0].strip().lower()
    sample = body[:4096].lstrip().removeprefix(b"\xef\xbb\xbf").lstrip()
    if media_type == "application/pdf" or sample.startswith(b"%PDF-"):
        raise WebContentError(
            "unsupported_content",
            "PDF documents require a document/PDF reader; no PDF text was extracted. "
            "Use a supported document tool or another source, not raw PDF bytes as evidence.",
        )
    text_type = (
        not media_type or media_type.startswith("text/")
        or media_type in {"application/json", "application/xml", "application/javascript"}
        or media_type.endswith(("+json", "+xml"))
    )
    binary_signature = sample.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"PK\x03\x04"))
    if not text_type or binary_signature:
        raise WebContentError(
            "unsupported_content",
            "The response is a non-text document or binary payload, not extracted webpage content. "
            "Use the appropriate document/media tool or another source.",
        )


class WebFetchWebpageTool(Tool):
    """Fetch webpage text content from URL. Returns status/title/plain text content."""

    accepts_tool_callback_context = True

    def __init__(
        self,
        language: str = "cn",
        agent_id: str | None = None,
        proxy_url: str | None = None,
        allowed_domains: tuple[str, ...] | None = None,
    ):
        super().__init__(build_tool_card("fetch_webpage", "WebFetchWebpageTool", language, agent_id=agent_id))
        self._proxy_url = str(proxy_url or "").strip() or None
        self._allowed_domains = allowed_domains

    @staticmethod
    def _byte_cap() -> int:
        """Return the hard response-body byte ceiling (OOM guard).

        This bounds only the raw bytes read off the wire so an unbounded page
        cannot exhaust memory. It is deliberately independent of ``max_chars``:
        ``max_chars`` caps the *extracted* output text, which is produced after
        main-text extraction strips script/style/markup. Coupling the byte
        ceiling to ``max_chars`` would truncate the input before extraction
        runs, dropping real body content on script/markup-heavy pages where the
        article sits past the early bytes.
        """
        return _safe_int(
            os.environ.get(_FETCH_WEBPAGE_MAX_BYTES_ENV, "") or _FETCH_WEBPAGE_DEFAULT_MAX_BYTES,
            _FETCH_WEBPAGE_DEFAULT_MAX_BYTES,
        )

    @staticmethod
    async def _fetch_via_jina_reader(
        session: aiohttp.ClientSession,
        url: str,
        timeout_seconds: int,
        byte_cap: int,
        proxy_url: str | None = None,
    ) -> dict[str, Any]:
        """Fetch webpage content via the jina.ai reader proxy."""
        reader_url = f"https://r.jina.ai/{url}"
        status, headers, body, _final_url, truncated = await _http.request(
            session,
            "GET",
            reader_url,
            headers=_REQUEST_HEADERS,
            timeout_seconds=timeout_seconds,
            max_bytes=byte_cap,
            proxy_url=proxy_url,
        )
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        if status < 400 and status != 202:
            _require_text_response(body, content_type)
        content = _decode_response_text(body, content_type=content_type).strip()
        blocked = challenge_reason(content, status=status)
        if blocked:
            raise WebContentError("blocked", blocked + " Use another authorized source or ask the user; do not bypass verification.")
        _raise_fetch_http_error(url, status, body)
        source_error = re.search(r"(?im)^Warning:.*returned error\s+([45]\d{2})", content)
        if source_error:
            raise WebContentError("source_error", f"The reader reported HTTP {source_error.group(1)} at the original source.")
        title_match = re.search(r"(?m)^Title:\s*(.*)$", content)
        title = title_match.group(1).strip() if title_match else ""
        if "Markdown Content:" in content:
            content = content.split("Markdown Content:", 1)[1].strip()
        blocked = challenge_reason(content, title=title)
        if blocked or status == 202:
            raise WebContentError("blocked", (blocked or "The reader returned a pending/verification response.") + " Do not bypass verification.")
        _require_text_response(content.encode("utf-8"), "text/plain")
        issue = _content_issue(title, content)
        if issue:
            raise WebContentError(issue, "The reader returned no usable source content.")
        return {
            "url": url,
            "status_code": status,
            "title": title,
            "content": content,
            "truncated": truncated,
        }

    @staticmethod
    def _extract_main_text_from_html(text: str, base_url: str = "") -> tuple[str, str]:
        """Extract main content and title from HTML."""
        soup = _parse_html(text)
        title_tag = soup.select_one("title")
        title = title_tag.get_text(" ", strip=True) if title_tag is not None else ""

        for selector in ("script", "style", "noscript", "svg", "canvas", "iframe"):
            for node in soup.select(selector):
                node.decompose()

        for anchor in soup.select("a[href]"):
            label = anchor.get_text(" ", strip=True)
            href = str(anchor.get("href") or "").strip()
            target = urljoin(base_url, href)
            if label and href and not href.startswith("#") and urlsplit(target).scheme in {"https", "http"}:
                anchor.replace_with(f"[{label}]({target})")
        for table in reversed(soup.select("table, [role='table']")):
            lines: list[str] = []
            for row in table.select("tr, [role='row']"):
                cells = row.select("th, td, [role='cell'], [role='columnheader'], [role='rowheader']")
                if cells:
                    lines.append(" | ".join(cell.get_text(" ", strip=True) or "[missing]" for cell in cells))
            if lines:
                table.replace_with("\n" + "\n".join(lines) + "\n")
        for term in soup.select("dt"):
            value = term.find_next_sibling()
            if value is not None and value.name == "dd":
                term.replace_with(term.get_text(" ", strip=True) + ": " + (value.get_text(" ", strip=True) or "[missing]") + "\n")
                value.decompose()

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
                if selector in {"header", ".header"} and node.find_parent(["article", "main"]) is not None:
                    continue
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
            # Keep short facts and transformed tables, not only long paragraphs.
            best_text = body.get_text("\n", strip=True)

        best_text = re.sub(r"\n{3,}", "\n\n", best_text).strip()
        return title, best_text

    @staticmethod
    async def _fetch_webpage(request: _FetchRequest) -> dict[str, Any]:
        """Read public content; reader fallback is only for unblocked JS shells."""
        started_at = time.monotonic()
        if request.allowed_domains and not _domain_allowed(request.url, request.allowed_domains):
            raise ValueError("URL is outside the configured domestic academic source domains")
        status, headers, body, final_url, truncated = await _http.request(
            request.session,
            "GET",
            request.url,
            headers=_REQUEST_HEADERS,
            timeout_seconds=request.timeout_seconds,
            max_bytes=request.byte_cap,
            proxy_url=request.proxy_url,
        )
        if request.allowed_domains and not _domain_allowed(final_url, request.allowed_domains):
            raise ValueError("redirected URL is outside configured domestic source domains")

        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        if status < 400 and status != 202:
            _require_text_response(body, content_type)
        text = _decode_response_text(body, content_type=content_type)
        raw = text
        title = ""

        if "html" in content_type.lower():
            title, text = WebFetchWebpageTool._extract_main_text_from_html(text, final_url)
        else:
            text = re.sub(r"\s+", " ", text).strip()

        blocked = challenge_reason(raw, title=title, status=status) or challenge_reason(text, title=title)
        if blocked or status == 202:
            raise WebContentError("blocked", (blocked or "The source returned a pending/verification response (202).") + " Do not bypass access controls; use another authorized source or ask the user.")
        _raise_fetch_http_error(request.url, status, body)
        issue = _content_issue(title, text, raw)
        if issue == "dynamic_shell" and not request.allowed_domains:
            remaining = request.timeout_seconds - (time.monotonic() - started_at)
            if request.budget is not None:
                remaining = request.budget.remaining(remaining)
            if remaining <= 0:
                raise WebContentError("budget_exhausted", "No time remains for reader fallback.")
            return await WebFetchWebpageTool._fetch_via_jina_reader(
                request.session, request.url, remaining, request.byte_cap, proxy_url=request.proxy_url,
            )
        if issue:
            raise WebContentError(issue, "No usable source text was extracted. Missing fields are unknown, not zero; do not infer values from adjacent labels.")

        return {
            "url": final_url,
            "status_code": status,
            "title": title,
            "content": text,
            "truncated": truncated,
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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> str:
        """Invoke the webpage fetch tool."""
        url = WebFetchWebpageTool._normalize_url(str(inputs.get("url", "") or ""))
        max_chars = _safe_int(
            inputs.get("max_chars", _FETCH_WEBPAGE_DEFAULT_MAX_CHARS),
            _FETCH_WEBPAGE_DEFAULT_MAX_CHARS,
        )
        timeout_seconds = _safe_int(
            inputs.get("timeout_seconds", _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS)
            or _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS,
            _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS,
        )

        if not url:
            return "[ERROR]: url cannot be empty."
        if not _domain_allowed(url, self._allowed_domains):
            return "[ERROR]: URL is outside the configured domestic academic source domains."

        max_chars_cap = _safe_int(os.environ.get(_FETCH_WEBPAGE_MAX_CHARS_ENV, "200000") or "200000", 200000)
        timeout_cap = _safe_int(os.environ.get(_FETCH_WEBPAGE_MAX_TIMEOUT_ENV, "600") or "600", 600)
        if max_chars != 0:
            max_chars = max(500, min(max_chars, max_chars_cap))
        timeout_seconds = max(5, min(timeout_seconds, timeout_cap))
        byte_cap = WebFetchWebpageTool._byte_cap()

        try:
            budget = get_web_budget(kwargs.get("_tool_callback_context"))
            if budget is not None:
                budget.reserve_fetch()
                timeout_seconds = budget.remaining(timeout_seconds)
            async with _http.new_session() as session:
                data = await WebFetchWebpageTool._fetch_webpage(
                    _FetchRequest(
                        session=session,
                        url=url,
                        timeout_seconds=timeout_seconds,
                        byte_cap=byte_cap,
                        proxy_url=self._proxy_url,
                        allowed_domains=self._allowed_domains,
                        budget=budget,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            return f"[ERROR]: failed to fetch webpage: {exc}"

        lines = [
            f"URL: {data.get('url', url)}",
            f"Status: {data.get('status_code', '')}",
            "Content status: ok. Extracted source text is not independently verified. Check the requested entity and observation/publication time; never fill missing fields with adjacent values.",
        ]
        if data.get("title"):
            lines.append(f"Title: {data['title']}")
        lines.append("Content:")
        lines.append(WebFetchWebpageTool._clip_text(str(data.get("content", "") or ""), max_chars) or "[empty]")
        if data.get("truncated"):
            lines.append("...[truncated: response exceeded byte limit]")
        return "\n".join(lines)

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
