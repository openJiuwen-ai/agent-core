# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HTTP custom-search client used by OfficeQA controller-mediated lookups."""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _env_text(name: str, fallback: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return fallback
    cleaned = str(value).strip()
    return cleaned or fallback


@dataclass(frozen=True)
class _BuiltinSearchSettings:
    user_agent: str
    endpoint: str
    auth_env: str
    provider: str
    hit_limit: int
    timeout_s: int
    retry_count: int
    backoff_s: float


_BUILTINS = _BuiltinSearchSettings(
    user_agent=(
        "Mozilla/5.0 (compatible; OpenJiuwenOfficeQA/1.0; "
        "+https://gitcode.com/openJiuwen)"
    ),
    endpoint=_env_text(
        "OFFICEQA_SEARCH_API_URL",
        "http://localhost:8080/search_tool/search",
    ),
    auth_env="OFFICEQA_CUSTOM_SEARCH_AUTH",
    provider="duckduckgo",
    hit_limit=4,
    timeout_s=20,
    retry_count=4,
    backoff_s=1.0,
)

_TITLE_KEYS = ("title", "name", "headline", "source")
_LINK_KEYS = ("url", "link", "href", "display_url")
_BLURB_KEYS = ("snippet", "description", "body", "text", "content")
_NESTED_LIST_KEYS = (
    "results",
    "items",
    "data",
    "organic",
    "organic_results",
    "search_results",
    "webPages",
    "value",
)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _first_text(row: dict[str, Any], keys: Iterable[str], *, default: str = "") -> str:
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return default


def _row_looks_like_hit(row: dict[str, Any]) -> bool:
    names = set(row)
    has_title = bool(names & set(_TITLE_KEYS))
    has_link = bool(names & set(_LINK_KEYS))
    has_blurb = bool(names & set(_BLURB_KEYS))
    return has_title or (has_link and has_blurb) or (has_link and has_title)


def _collect_hits(node: object, *, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    if isinstance(node, dict):
        if _row_looks_like_hit(node):
            return [node]
        for key in _NESTED_LIST_KEYS:
            if key not in node:
                continue
            found = _collect_hits(node[key], depth=depth + 1)
            if found:
                return found
        hits: list[dict[str, Any]] = []
        for child in node.values():
            hits.extend(_collect_hits(child, depth=depth + 1))
        return hits
    if isinstance(node, list):
        hits = []
        for child in node:
            if isinstance(child, dict) and _row_looks_like_hit(child):
                hits.append(child)
            else:
                hits.extend(_collect_hits(child, depth=depth + 1))
        return hits
    return []


def _format_hit(row: dict[str, Any], index: int) -> str:
    title = _first_text(row, _TITLE_KEYS, default=f"Result #{index}")
    link = _first_text(row, _LINK_KEYS)
    blurb = _first_text(row, _BLURB_KEYS)
    parts = [f"{index}. {title}"]
    if link:
        parts.append(f"   url: {link}")
    if blurb:
        parts.append(f"   excerpt: {blurb}")
    return "\n".join(parts)


def _format_response(query: str, payload: object) -> str:
    hits = _collect_hits(payload)
    if not hits:
        dump = json.dumps(payload, ensure_ascii=False) if payload else "(empty)"
        return f"query: {query}\nraw payload: {dump}"
    blocks = [_format_hit(hit, idx) for idx, hit in enumerate(hits, start=1)]
    return f"query: {query}\n\n" + "\n\n".join(blocks)


def _http_should_retry(status: int) -> bool:
    return status in (408, 429) or status >= 500


@dataclass(frozen=True)
class _SearchCallPlan:
    endpoint: str
    token: str
    provider: str
    hit_limit: int
    timeout_s: int
    retries: int
    backoff_s: float
    user_agent: str

    @classmethod
    def from_options(cls, **options: object) -> _SearchCallPlan:
        auth_env = str(options.get("auth_env") or _BUILTINS.auth_env)
        token = str(options.get("auth_token") or os.environ.get(auth_env, "")).strip()
        if not token:
            raise ValueError(f"custom_search auth token missing; set {auth_env}")
        return cls(
            endpoint=str(options.get("api_url") or _BUILTINS.endpoint),
            token=token,
            provider=str(options.get("provider") or _BUILTINS.provider),
            hit_limit=int(options.get("max_num_results") or _BUILTINS.hit_limit),
            timeout_s=int(options.get("timeout") or _BUILTINS.timeout_s),
            retries=int(options.get("max_retries") or _BUILTINS.retry_count),
            backoff_s=float(options.get("initial_backoff_seconds") or _BUILTINS.backoff_s),
            user_agent=str(_BUILTINS.user_agent),
        )

    def build_request(self, query: str) -> Request:
        body = json.dumps(
            {
                "query": query,
                "max_num_results": int(self.hit_limit),
                "provider": self.provider,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )


def _read_http_body(request: Request, timeout_s: int) -> str:
    with urlopen(request, timeout=timeout_s) as response:
        return response.read().decode("utf-8", errors="ignore")


def _run_search(query: str, plan: _SearchCallPlan) -> str:
    request = plan.build_request(query)
    budget = max(1, int(plan.retries) + 1)
    failure: RuntimeError | None = None
    raw_body = ""

    for round_idx in range(budget):
        try:
            raw_body = _read_http_body(request, plan.timeout_s)
            failure = None
            break
        except HTTPError as err:
            detail = _truncate(err.read().decode("utf-8", errors="ignore"), 1000)
            failure = RuntimeError(f"custom_search HTTP {err.code}: {detail}")
            more_tries = round_idx + 1 < budget and _http_should_retry(err.code)
            if not more_tries:
                raise failure from err
        except (URLError, TimeoutError, socket.timeout) as err:
            failure = RuntimeError(f"custom_search connection error: {err}")
            if round_idx + 1 >= budget:
                raise failure from err
        wait_s = max(0.0, float(plan.backoff_s)) * (2 ** round_idx)
        if wait_s:
            time.sleep(wait_s)
    else:
        raise failure or RuntimeError("custom_search failed without a captured error")

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        leftover = raw_body.strip() or "[empty response]"
        return f"query: {query}\n\n{leftover}"
    return _format_response(query, parsed)


def custom_search(query: str, **options: object) -> str:
    """POST ``query`` to the custom search endpoint and return a readable dump.

    Keyword options mirror historical callers: ``api_url``, ``auth_token``,
    ``auth_env``, ``provider``, ``max_num_results``, ``timeout``, ``max_retries``,
    ``initial_backoff_seconds``.
    """
    cleaned = str(query or "").strip()
    if not cleaned:
        raise ValueError("custom_search query must be non-empty")
    return _run_search(cleaned, _SearchCallPlan.from_options(**options))


# Public aliases kept for historical imports / tool_runtime re-exports.
DEFAULT_USER_AGENT = _BUILTINS.user_agent
DEFAULT_CUSTOM_SEARCH_URL = _BUILTINS.endpoint
DEFAULT_CUSTOM_SEARCH_AUTH_ENV = _BUILTINS.auth_env
DEFAULT_CUSTOM_SEARCH_PROVIDER = _BUILTINS.provider
DEFAULT_CUSTOM_SEARCH_MAX_RESULTS = _BUILTINS.hit_limit
DEFAULT_CUSTOM_SEARCH_TIMEOUT = _BUILTINS.timeout_s
DEFAULT_CUSTOM_SEARCH_MAX_RETRIES = _BUILTINS.retry_count
DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS = _BUILTINS.backoff_s
