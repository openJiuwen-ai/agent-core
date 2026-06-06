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
from typing import Any, AsyncIterator

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
    _decode_ddg_redirect,
    _parse_html,
    _safe_int,
)
from openjiuwen.harness.tools.web._decode import _decode_response_text


class WebFetchWebpageTool(Tool):
    """Fetch webpage text content from URL. Returns status/title/plain text content."""

    def __init__(self, language: str = "cn", agent_id: str | None = None):
        super().__init__(build_tool_card("fetch_webpage", "WebFetchWebpageTool", language, agent_id=agent_id))

    @staticmethod
    def _byte_cap(max_chars: int) -> int:
        """Derive a response-body byte ceiling from the char cap.

        The ``* 4`` covers UTF-8 multibyte and HTML markup overhead relative to
        the final extracted characters; both are bounded by a hard ceiling so an
        unbounded page cannot exhaust memory.
        """
        hard = _safe_int(
            os.environ.get(_FETCH_WEBPAGE_MAX_BYTES_ENV, "") or _FETCH_WEBPAGE_DEFAULT_MAX_BYTES,
            _FETCH_WEBPAGE_DEFAULT_MAX_BYTES,
        )
        if max_chars <= 0:
            return hard
        return min(max_chars * 4, hard)

    @staticmethod
    async def _fetch_via_jina_reader(
        session: aiohttp.ClientSession,
        url: str,
        timeout_seconds: int,
        byte_cap: int,
    ) -> dict[str, Any]:
        """Fetch webpage content via the jina.ai reader proxy."""
        reader_url = f"https://r.jina.ai/{url}"
        status, headers, body, _final_url, truncated = await _http._request(
            session,
            "GET",
            reader_url,
            headers=_REQUEST_HEADERS,
            timeout_seconds=timeout_seconds,
            max_bytes=byte_cap,
        )
        _http._raise_for_status_with_body(status, body, engine="fetch_webpage")
        content = _decode_response_text(body, content_type=headers.get("Content-Type", "")).strip()
        return {
            "url": url,
            "status_code": status,
            "title": "",
            "content": content,
            "truncated": truncated,
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
    async def _fetch_webpage(
        session: aiohttp.ClientSession,
        url: str,
        timeout_seconds: int,
        byte_cap: int,
    ) -> dict[str, Any]:
        """Fetch webpage content, falling back to the jina.ai reader on 401/403/429."""
        status, headers, body, final_url, truncated = await _http._request(
            session,
            "GET",
            url,
            headers=_REQUEST_HEADERS,
            timeout_seconds=timeout_seconds,
            max_bytes=byte_cap,
        )
        if status in {401, 403, 429}:
            return await WebFetchWebpageTool._fetch_via_jina_reader(session, url, timeout_seconds, byte_cap)
        _http._raise_for_status_with_body(status, body, engine="fetch_webpage")

        text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
        content_type = headers.get("Content-Type", "")
        title = ""

        if "html" in content_type.lower():
            title, text = WebFetchWebpageTool._extract_main_text_from_html(text)
        else:
            text = re.sub(r"\s+", " ", text).strip()

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
            inputs.get("max_chars", _FETCH_WEBPAGE_DEFAULT_MAX_CHARS) or _FETCH_WEBPAGE_DEFAULT_MAX_CHARS,
            _FETCH_WEBPAGE_DEFAULT_MAX_CHARS,
        )
        timeout_seconds = _safe_int(
            inputs.get("timeout_seconds", _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS)
            or _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS,
            _FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS,
        )

        if not url:
            return "[ERROR]: url cannot be empty."

        max_chars_cap = _safe_int(os.environ.get(_FETCH_WEBPAGE_MAX_CHARS_ENV, "200000") or "200000", 200000)
        timeout_cap = _safe_int(os.environ.get(_FETCH_WEBPAGE_MAX_TIMEOUT_ENV, "600") or "600", 600)
        if max_chars != 0:
            max_chars = max(500, min(max_chars, max_chars_cap))
        timeout_seconds = max(5, min(timeout_seconds, timeout_cap))
        byte_cap = WebFetchWebpageTool._byte_cap(max_chars)

        try:
            async with _http._new_session() as session:
                data = await WebFetchWebpageTool._fetch_webpage(session, url, timeout_seconds, byte_cap)
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
        if data.get("truncated"):
            lines.append("...[truncated: response exceeded byte limit]")
        return "\n".join(lines)

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
