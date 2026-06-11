# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async HTTP transport for the web tools package (aiohttp based).

A single ``request`` coroutine replaces the original synchronous
``_http_request``. It handles proxy resolution, TLS verification, a capped
streaming read (so an oversized body cannot exhaust memory), and a
trust_env=False retry that mirrors the original ProxyError fallback.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import unquote, urlsplit, urlunsplit

import aiohttp

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.tools.web._common import _free_search_ssl_verify, _resolve_proxy

# Chunk size for the streaming reader.
_READ_CHUNK_SIZE = 64 * 1024
# Connect-phase timeout cap (seconds); read phase uses the caller's full budget.
_CONNECT_TIMEOUT_CAP = 10


def _make_connector() -> aiohttp.TCPConnector:
    """Build a TCP connector honoring the SSL-verify configuration.

    ``ssl=True`` uses aiohttp's default verification (equivalent to requests
    ``verify=True``); ``ssl=False`` disables it (default, for intranet usage).
    """
    if _free_search_ssl_verify():
        return aiohttp.TCPConnector(ssl=True)
    return aiohttp.TCPConnector(ssl=False)


@asynccontextmanager
async def new_session() -> AsyncIterator[aiohttp.ClientSession]:
    """Yield a per-invoke aiohttp session.

    ``trust_env=True`` makes aiohttp honor ``HTTP(S)_PROXY``/``NO_PROXY`` when
    no explicit ``FREE_SEARCH_PROXY_URL`` is set, matching requests' default.
    """
    async with aiohttp.ClientSession(trust_env=True, connector=_make_connector()) as session:
        yield session


async def _read_capped(resp: aiohttp.ClientResponse, max_bytes: int | None) -> tuple[bytes, bool]:
    """Read a response body, stopping once ``max_bytes`` is reached.

    Args:
        resp: The aiohttp response to read.
        max_bytes: Byte ceiling; ``None`` reads the whole body.

    Returns:
        A tuple of (body bytes, truncated flag).
    """
    buf = bytearray()
    truncated = False
    async for chunk in resp.content.iter_chunked(_READ_CHUNK_SIZE):
        buf.extend(chunk)
        if max_bytes is not None and len(buf) >= max_bytes:
            truncated = True
            break
    return bytes(buf), truncated


def _split_proxy_credentials(proxy: str) -> tuple[str, aiohttp.BasicAuth | None]:
    """Split inline credentials out of a proxy URL into an explicit BasicAuth.

    aiohttp ignores ``user:pass@`` embedded in a proxy URL, so authenticated
    proxies need the credentials passed separately. Returns the proxy URL with
    its userinfo removed plus a BasicAuth (or None when no credentials present).

    Args:
        proxy: The proxy URL, optionally carrying ``user:pass@`` userinfo.

    Returns:
        A tuple of (proxy URL without userinfo, BasicAuth or None).
    """
    parts = urlsplit(proxy)
    if not parts.username:
        return proxy, None
    auth = aiohttp.BasicAuth(unquote(parts.username), unquote(parts.password or ""))
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    clean = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return clean, auth


async def _do_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    json_body: dict[str, Any] | None,
    proxy: str | None,
    proxy_auth: aiohttp.BasicAuth | None,
    timeout: aiohttp.ClientTimeout,
    max_bytes: int | None,
) -> tuple[int, dict[str, str], bytes, str, bool]:
    """Issue a single request and return (status, headers, body, final_url, truncated)."""
    async with session.request(
        method,
        url,
        headers=headers,
        json=json_body,
        proxy=proxy,
        proxy_auth=proxy_auth,
        timeout=timeout,
    ) as resp:
        body, truncated = await _read_capped(resp, max_bytes)
        return resp.status, dict(resp.headers), body, str(resp.url), truncated


async def request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float,
    max_bytes: int | None = None,
) -> tuple[int, dict[str, str], bytes, str, bool]:
    """Perform an HTTP request, retrying without env proxies on a proxy error.

    Args:
        session: The aiohttp session to use for the primary attempt.
        method: HTTP method (case-insensitive).
        url: Target URL.
        headers: Optional request headers.
        json_body: Optional JSON request body (POST).
        timeout_seconds: Total timeout budget; also used for the read phase.
        max_bytes: Optional byte ceiling for the response body.

    Returns:
        A tuple of (status, headers, body bytes, final URL, truncated flag).
    """
    method_up = method.upper()
    proxy = _resolve_proxy(url)
    explicit_proxy = proxy is not None
    proxy_auth: aiohttp.BasicAuth | None = None
    if proxy is not None:
        # aiohttp ignores inline credentials in the proxy URL; carry them in an
        # explicit BasicAuth so authenticated proxies (corporate gateways) work.
        proxy, proxy_auth = _split_proxy_credentials(proxy)
    timeout = aiohttp.ClientTimeout(
        total=timeout_seconds,
        sock_connect=min(timeout_seconds, _CONNECT_TIMEOUT_CAP),
        sock_read=timeout_seconds,
    )
    try:
        return await _do_request(
            session,
            method_up,
            url,
            headers=headers,
            json_body=json_body,
            proxy=proxy,
            proxy_auth=proxy_auth,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    except (aiohttp.ClientProxyConnectionError, aiohttp.ClientHttpProxyError):
        if explicit_proxy:
            raise
        async with aiohttp.ClientSession(trust_env=False, connector=_make_connector()) as fallback:
            return await _do_request(
                fallback,
                method_up,
                url,
                headers=headers,
                json_body=json_body,
                proxy=None,
                proxy_auth=None,
                timeout=timeout,
                max_bytes=max_bytes,
            )


def format_http_error_reason(status: int, body: bytes) -> str:
    """Format an HTTP error reason that includes the response body.

    Args:
        status: HTTP status code.
        body: Raw response body bytes.

    Returns:
        A reason string like ``"HTTP 404; response body: ..."``, or ``""`` when
        ``status`` is below 400 (i.e. not an error). Centralizing this lets the
        search path and the fetch path share one body-rendering rule while
        raising their own scope-appropriate status codes.
    """
    if status < 400:
        return ""
    text = ""
    try:
        text = json.dumps(json.loads(body), ensure_ascii=False)
    except (ValueError, TypeError):
        text = (body.decode("utf-8", errors="replace") or "").strip()
    reason = f"HTTP {status}"
    if text:
        reason = f"{reason}; response body: {text[:1000]}"
    return reason


def raise_for_status_with_body(status: int, body: bytes, *, engine: str) -> None:
    """Raise a web-search engine error that includes the response body.

    Args:
        status: HTTP status code.
        body: Raw response body bytes.
        engine: Engine/provider name for the error message.

    Raises:
        BaseError: ``TOOL_WEB_SEARCH_ENGINE_ERROR`` when status is >= 400.
    """
    reason = format_http_error_reason(status, body)
    if not reason:
        return
    raise build_error(StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR, engine=engine, reason=reason)
