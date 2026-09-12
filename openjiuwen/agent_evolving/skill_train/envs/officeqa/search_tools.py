# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Controller-facing HTTP search helper for OfficeQA custom evidence gathers."""

from __future__ import annotations

import enum
import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _env_or(name: str, fallback: str) -> str:
    return str(os.environ.get(name, "") or "").strip() or fallback


# Internal knobs (public DEFAULT_* names are exposed lazily via __getattr__).
_CTRL: dict[str, object] = {
    "ua": (
        "Mozilla/5.0 (compatible; OpenJiuwenOfficeQA/1.0; "
        "+https://gitcode.com/openJiuwen)"
    ),
    "endpoint": _env_or(
        "OFFICEQA_SEARCH_API_URL",
        "http://localhost:8080/search_tool/search",
    ),
    "token_env": "OFFICEQA_CUSTOM_SEARCH_AUTH",
    "engine": "duckduckgo",
    "hits": 4,
    "seconds": 20,
    "retries": 4,
    "backoff": 1.0,
}

_PUBLIC_DEFAULTS = {
    "DEFAULT_USER_AGENT": "ua",
    "DEFAULT_CUSTOM_SEARCH_URL": "endpoint",
    "DEFAULT_CUSTOM_SEARCH_AUTH_ENV": "token_env",
    "DEFAULT_CUSTOM_SEARCH_PROVIDER": "engine",
    "DEFAULT_CUSTOM_SEARCH_MAX_RESULTS": "hits",
    "DEFAULT_CUSTOM_SEARCH_TIMEOUT": "seconds",
    "DEFAULT_CUSTOM_SEARCH_MAX_RETRIES": "retries",
    "DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS": "backoff",
}


def __getattr__(name: str) -> object:
    key = _PUBLIC_DEFAULTS.get(name)
    if key is not None:
        return _CTRL[key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC_DEFAULTS))


class _Phase(enum.Enum):
    READY = "ready"
    TRANSMIT = "transmit"
    BACKOFF = "backoff"
    PARSE = "parse"
    DONE = "done"
    ABORT = "abort"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "".join(list(text)[:limit])


def _looks_like_hit(row: dict) -> bool:
    keys = set(row)
    titleish = keys & {"title", "name", "headline", "source"}
    linkish = keys & {"url", "link", "href", "display_url"}
    bodyish = keys & {"snippet", "description", "body", "text", "content"}
    return bool(titleish or (linkish and bodyish) or (linkish and titleish))


_HIT_CONTAINER_KEYS = (
    "results",
    "items",
    "data",
    "organic",
    "organic_results",
    "search_results",
    "webPages",
    "value",
)


def _scan_hit_nodes(node: object, *, depth: int = 0) -> Iterator[dict]:
    """Depth-first visitor that yields dicts resembling search hits."""
    if depth > 8:
        return
    if isinstance(node, dict):
        if _looks_like_hit(node):
            yield node
            return
        for key in _HIT_CONTAINER_KEYS:
            if key in node:
                yield from _scan_hit_nodes(node[key], depth=depth + 1)
                return
        for child in node.values():
            yield from _scan_hit_nodes(child, depth=depth + 1)
        return
    if isinstance(node, list):
        for child in node:
            if isinstance(child, dict) and _looks_like_hit(child):
                yield child
            else:
                yield from _scan_hit_nodes(child, depth=depth + 1)


def _pick_field(row: dict, *names: str, fallback: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return fallback


def _render_hit_block(row: dict, index: int) -> str:
    title = _pick_field(row, "title", "name", "headline", "source", fallback=f"Result #{index}")
    link = _pick_field(row, "url", "link", "href", "display_url")
    blurb = _pick_field(row, "snippet", "description", "body", "text", "content")
    lines = [f"{index}. {title}"]
    if link:
        lines.append(f"   url: {link}")
    if blurb:
        lines.append(f"   excerpt: {blurb}")
    return "\n".join(lines)


def _render_payload(query: str, payload: object) -> str:
    entries = [_render_hit_block(hit, i) for i, hit in enumerate(_scan_hit_nodes(payload), start=1)]
    if not entries:
        dump = json.dumps(payload, ensure_ascii=False) if payload else "(empty)"
        return f"query: {query}\nraw payload: {dump}"
    return f"query: {query}\n\n" + "\n\n".join(entries)


def _retryable_http(code: int) -> bool:
    return code in {408, 429} or code >= 500


def _post_once(request: Request, timeout: int) -> str:
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class _CustomSearchTransport:
    endpoint: str
    bearer: str
    engine: str
    hit_limit: int
    deadline_s: int
    retry_budget: int
    backoff_s: float

    @classmethod
    def from_call_kwargs(cls, **kwargs: object) -> _CustomSearchTransport:
        auth_env = str(kwargs.get("auth_env") or _CTRL["token_env"])
        explicit = kwargs.get("auth_token")
        bearer = str(explicit or os.environ.get(str(auth_env), "")).strip()
        if not bearer:
            raise ValueError(f"custom_search auth token missing; set {auth_env}")
        return cls(
            endpoint=str(kwargs.get("api_url") or _CTRL["endpoint"]),
            bearer=bearer,
            engine=str(kwargs.get("provider") or _CTRL["engine"]),
            hit_limit=int(kwargs.get("max_num_results") or _CTRL["hits"]),
            deadline_s=int(kwargs.get("timeout") or _CTRL["seconds"]),
            retry_budget=int(kwargs.get("max_retries") or _CTRL["retries"]),
            backoff_s=float(kwargs.get("initial_backoff_seconds") or _CTRL["backoff"]),
        )

    def build_request(self, cleaned_query: str) -> Request:
        body = json.dumps(
            {
                "query": cleaned_query,
                "max_num_results": int(self.hit_limit),
                "provider": self.engine,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": self.bearer,
                "Content-Type": "application/json",
                "User-Agent": str(_CTRL["ua"]),
            },
            method="POST",
        )


def _execute_search_request(cleaned: str, transport: _CustomSearchTransport) -> str:
    request = transport.build_request(cleaned)
    attempts_left = max(1, int(transport.retry_budget) + 1)
    attempt_idx = 0
    phase = _Phase.READY
    last_error: RuntimeError | None = None
    raw_text = ""

    while phase not in {_Phase.DONE, _Phase.ABORT}:
        if phase is _Phase.READY:
            phase = _Phase.TRANSMIT
            continue

        if phase is _Phase.TRANSMIT:
            attempt_idx += 1
            attempts_left -= 1
            try:
                raw_text = _post_once(request, transport.deadline_s)
                phase = _Phase.PARSE
            except HTTPError as exc:
                detail = _clip(exc.read().decode("utf-8", errors="ignore"), 1000)
                last_error = RuntimeError(f"custom_search HTTP {exc.code}: {detail}")
                if attempts_left <= 0 or not _retryable_http(exc.code):
                    phase = _Phase.ABORT
                else:
                    phase = _Phase.BACKOFF
            except (URLError, TimeoutError, socket.timeout) as exc:
                last_error = RuntimeError(f"custom_search connection error: {exc}")
                phase = _Phase.ABORT if attempts_left <= 0 else _Phase.BACKOFF
            continue

        if phase is _Phase.BACKOFF:
            delay = max(0.0, float(transport.backoff_s)) * (2 ** (attempt_idx - 1))
            if delay > 0:
                time.sleep(delay)
            phase = _Phase.TRANSMIT
            continue

        if phase is _Phase.PARSE:
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                fallback = raw_text.strip() or "[empty response]"
                return f"query: {cleaned}\n\n{fallback}"
            phase = _Phase.DONE
            return _render_payload(cleaned, parsed)

    raise last_error or RuntimeError("custom_search failed without a captured error")


def custom_search(query: str, **options: object) -> str:
    """POST ``query`` to the custom search endpoint and return a readable dump.

    Keyword options mirror historical callers: ``api_url``, ``auth_token``,
    ``auth_env``, ``provider``, ``max_num_results``, ``timeout``, ``max_retries``,
    ``initial_backoff_seconds``.
    """
    cleaned = str(query or "").strip()
    if not cleaned:
        raise ValueError("custom_search query must be non-empty")
    return _execute_search_request(cleaned, _CustomSearchTransport.from_call_kwargs(**options))
