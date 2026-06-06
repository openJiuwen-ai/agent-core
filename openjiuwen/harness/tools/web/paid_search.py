# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async paid web search tool (Bocha / Perplexity / Serper / Jina).

Each provider runner is an async coroutine sharing one per-invoke aiohttp
session; ``provider=auto`` tries configured providers in a fixed fallback order.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

import aiohttp

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import (
    _JINA_ALLOWED_MODELS,
    _JINA_DEFAULT_MODEL,
    _PAID_SEARCH_DEFAULT_TIMEOUT_SECONDS,
    _PAID_SEARCH_MAX_TIMEOUT_SECONDS,
    _PAID_SEARCH_MIN_TIMEOUT_SECONDS,
    _PAID_SEARCH_PROVIDER_ALT_ENV,
    _PAID_SEARCH_PROVIDER_ENV,
    _PPLX_ALLOWED_MODELS,
    _PPLX_DEFAULT_MODEL,
    _configured_paid_search_providers,
    _safe_env_choice,
    _safe_int,
)


class WebPaidSearchTool(Tool):
    """Paid search via Bocha/Perplexity/SERPER/JINA. Support provider=auto|bocha|perplexity|serper|jina."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: str | None = None,
        card: ToolCard | None = None,
    ):
        super().__init__(card or build_tool_card("paid_search", "WebPaidSearchTool", language, agent_id=agent_id))

    @staticmethod
    async def _jina_search(session: aiohttp.ClientSession, query: str, timeout_seconds: int) -> dict[str, Any]:
        """Search using Jina AI DeepSearch API."""
        jina_key = os.environ.get("JINA_API_KEY", "")
        if not jina_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="JINA_API_KEY")

        payload = {
            "model": _safe_env_choice("JINA_MODEL", _JINA_DEFAULT_MODEL, _JINA_ALLOWED_MODELS),
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "reasoning_effort": "low",
        }
        status, _headers, body, _final_url, _truncated = await _http._request(
            session,
            "POST",
            "https://deepsearch.jina.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {jina_key}", "Content-Type": "application/json"},
            json_body=payload,
            timeout_seconds=timeout_seconds,
        )
        _http._raise_for_status_with_body(status, body, engine="jina")
        data = json.loads(body)

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
    async def _bocha_search(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Search using Bocha Web Search API."""
        bocha_key = os.environ.get("BOCHA_API_KEY", "")
        if not bocha_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="BOCHA_API_KEY")

        status, _headers, body, _final_url, _truncated = await _http._request(
            session,
            "POST",
            os.environ.get("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search"),
            headers={"Authorization": f"Bearer {bocha_key}", "Content-Type": "application/json"},
            json_body={"query": query, "summary": True, "count": max_results},
            timeout_seconds=timeout_seconds,
        )
        _http._raise_for_status_with_body(status, body, engine="bocha")
        data = json.loads(body)
        return {
            "provider": "bocha",
            "answer": WebPaidSearchTool._extract_bocha_answer(data),
            "urls": WebPaidSearchTool._extract_bocha_urls(data, max_results),
        }

    @staticmethod
    async def _serper_search(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Search using Serper (Google Search API)."""
        serper_key = os.environ.get("SERPER_API_KEY", "")
        if not serper_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="SERPER_API_KEY")

        headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
        status, _headers, body, _final_url, _truncated = await _http._request(
            session,
            "POST",
            "https://google.serper.dev/search",
            headers=headers,
            json_body={"q": query, "num": max_results},
            timeout_seconds=timeout_seconds,
        )
        if status == 400:
            status, _headers, body, _final_url, _truncated = await _http._request(
                session,
                "POST",
                "https://google.serper.dev/search",
                headers=headers,
                json_body={"q": query},
                timeout_seconds=timeout_seconds,
            )
        _http._raise_for_status_with_body(status, body, engine="serper")
        data = json.loads(body)
        urls: list[str] = []
        organic = data.get("organic", [])
        if isinstance(organic, list):
            for item in organic[:max_results]:
                if isinstance(item, dict) and item.get("link"):
                    urls.append(str(item["link"]))
        return {"provider": "serper", "answer": "", "urls": urls}

    @staticmethod
    def _parse_perplexity_citations(data: dict[str, Any]) -> list[str]:
        """Extract citation URLs from a Perplexity API response."""
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
    async def _perplexity_search(
        session: aiohttp.ClientSession,
        query: str,
        max_results: int,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Search using Perplexity AI."""
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not perplexity_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="PERPLEXITY_API_KEY")

        payload = {
            "model": _safe_env_choice("PPLX_MODEL", _PPLX_DEFAULT_MODEL, _PPLX_ALLOWED_MODELS),
            "messages": [
                {"role": "system", "content": "Provide concise answer and include citations."},
                {"role": "user", "content": query},
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "stream": False,
        }
        status, _headers, body, _final_url, _truncated = await _http._request(
            session,
            "POST",
            os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/chat/completions"),
            headers={"Authorization": f"Bearer {perplexity_key}", "Content-Type": "application/json"},
            json_body=payload,
            timeout_seconds=timeout_seconds,
        )
        _http._raise_for_status_with_body(status, body, engine="perplexity")
        data = json.loads(body)

        answer = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            answer = choices[0].get("message", {}).get("content", "")

        return {
            "provider": "perplexity",
            "answer": (answer or "").strip(),
            "urls": WebPaidSearchTool._parse_perplexity_citations(data)[:max_results],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> str:
        """Invoke the paid web search tool."""
        query = str(inputs.get("query", "") or "").strip()
        provider = str(inputs.get("provider", "auto") or "auto").strip().lower()
        env_provider = str(
            os.environ.get(_PAID_SEARCH_PROVIDER_ENV)
            or os.environ.get(_PAID_SEARCH_PROVIDER_ALT_ENV)
            or ""
        ).strip().lower()
        if provider == "auto" and env_provider:
            provider = env_provider
        max_results = _safe_int(inputs.get("max_results", 8) or 8, 8)
        timeout_seconds = _safe_int(
            inputs.get("timeout_seconds", _PAID_SEARCH_DEFAULT_TIMEOUT_SECONDS)
            or _PAID_SEARCH_DEFAULT_TIMEOUT_SECONDS,
            _PAID_SEARCH_DEFAULT_TIMEOUT_SECONDS,
        )

        if not query:
            return "[ERROR]: query cannot be empty."

        if provider not in {"auto", "bocha", "jina", "serper", "perplexity"}:
            return "[ERROR]: provider must be one of auto|bocha|jina|serper|perplexity."

        timeout_seconds = max(
            _PAID_SEARCH_MIN_TIMEOUT_SECONDS,
            min(timeout_seconds, _PAID_SEARCH_MAX_TIMEOUT_SECONDS),
        )
        max_results = max(1, min(max_results, 20))

        if provider == "auto":
            order = _configured_paid_search_providers()
            if not order:
                return (
                    "[ERROR]: no paid search provider API key configured. "
                    "Set one of BOCHA_API_KEY, PERPLEXITY_API_KEY, SERPER_API_KEY, JINA_API_KEY."
                )
        else:
            order = [provider]

        errors: list[str] = []
        async with _http._new_session() as session:
            runners = {
                "bocha": lambda: WebPaidSearchTool._bocha_search(session, query, max_results, timeout_seconds),
                "jina": lambda: WebPaidSearchTool._jina_search(session, query, timeout_seconds),
                "serper": lambda: WebPaidSearchTool._serper_search(session, query, max_results, timeout_seconds),
                "perplexity": lambda: WebPaidSearchTool._perplexity_search(
                    session, query, max_results, timeout_seconds
                ),
            }
            for name in order:
                try:
                    runner_process = runners.get(name)
                    if runner_process is None:
                        errors.append(f"{name}: runner not found")
                        continue
                    result = await runner_process()
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
                    errors.append(f"{name}: no usable result payload")
                    continue
                return "\n".join(lines)

        return "[ERROR]: paid search failed. " + " | ".join(errors)

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
