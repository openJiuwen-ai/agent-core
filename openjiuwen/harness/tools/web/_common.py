# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared constants and pure helpers for the async web tools package.

This module holds everything that is independent of the HTTP transport and the
individual tool implementations: environment-name constants, request headers,
proxy/SSL configuration readers, and small string/URL utilities. Keeping these
here lets the transport layer (``_http``), the decoder (``_decode``) and each
tool module stay focused on their own concern.
"""

from __future__ import annotations

import base64
import os
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}

# Environment variable names (centralized so every module reads the same key).
_FREE_SEARCH_DEBUG_ENV = "FREE_SEARCH_DEBUG"
_FREE_SEARCH_DEBUG_DIR_ENV = "FREE_SEARCH_DEBUG_DIR"
_FREE_SEARCH_DDG_ENABLED_ENV = "FREE_SEARCH_DDG_ENABLED"
_FREE_SEARCH_BING_ENABLED_ENV = "FREE_SEARCH_BING_ENABLED"
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_FREE_SEARCH_DDG_URL_ENV = "FREE_SEARCH_DDG_URL"
_PAID_SEARCH_PROVIDER_ENV = "PAID_SEARCH_PROVIDER"
_PAID_SEARCH_PROVIDER_ALT_ENV = "WEB_PAID_SEARCH_PROVIDER"
_PAID_SEARCH_PROVIDER_KEY_ENVS = {
    "perplexity": "PERPLEXITY_API_KEY",
    "bocha": "BOCHA_API_KEY",
    "jina": "JINA_API_KEY",
    "serper": "SERPER_API_KEY",
}
_PAID_SEARCH_PROVIDER_ORDER = ("perplexity", "bocha", "jina", "serper")
_PAID_SEARCH_API_KEY_ENVS = tuple(_PAID_SEARCH_PROVIDER_KEY_ENVS.values())
_PAID_SEARCH_DEFAULT_TIMEOUT_SECONDS = 180
_PAID_SEARCH_MIN_TIMEOUT_SECONDS = 30
_PAID_SEARCH_MAX_TIMEOUT_SECONDS = 300
_PPLX_ALLOWED_MODELS = {"sonar", "sonar-pro"}
_PPLX_DEFAULT_MODEL = "sonar-pro"
_JINA_ALLOWED_MODELS = {"jina-deepsearch-v1"}
_JINA_DEFAULT_MODEL = "jina-deepsearch-v1"
_FREE_SEARCH_DEFAULT_NO_PROXY = (
    "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com, api.openai.rnd.huawei.com"
)
_FETCH_WEBPAGE_DEFAULT_MAX_CHARS = 20000
_FETCH_WEBPAGE_DEFAULT_TIMEOUT_SECONDS = 45
_FETCH_WEBPAGE_MAX_TIMEOUT_ENV = "MCP_FETCH_WEBPAGE_MAX_TIMEOUT_SECONDS"
_FETCH_WEBPAGE_MAX_CHARS_ENV = "MCP_FETCH_WEBPAGE_MAX_CHARS"
# Hard byte ceiling for fetched response bodies (stream reader stops past this),
# derived from the char cap so an unbounded page cannot exhaust memory.
_FETCH_WEBPAGE_MAX_BYTES_ENV = "MCP_FETCH_WEBPAGE_MAX_BYTES"
_FETCH_WEBPAGE_DEFAULT_MAX_BYTES = 8_000_000


def _safe_int(value: Any, default: int) -> int:
    """Parse an int from arbitrary tool input without ever raising.

    Tolerates ``"8"`` (int-like), ``"8.0"`` (float-like) and falls back to the
    default for ``None``/``"abc"``/other junk. LLM tool calls routinely pass
    such values, so parsing must never crash the tool.

    Args:
        value: Raw value from the tool inputs dict.
        default: Value to return when parsing fails.

    Returns:
        The parsed integer, or ``default`` when the value is not numeric.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _env_flag(name: str, default: bool = True) -> bool:
    """Parse a bool-like env value, preserving a default for empty values."""
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _contains_cjk(value: str) -> bool:
    """Whether the string contains CJK Unified Ideographs."""
    return any("一" <= ch <= "鿿" for ch in (value or ""))


def _strip_tags(value: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _parse_html(html: str) -> BeautifulSoup:
    """Parse HTML with lxml when available, otherwise html.parser."""
    try:
        import lxml  # noqa: F401

        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _normalized_domain(url: str) -> str:
    """Return a normalized domain for ranking/filtering."""
    netloc = urlparse(url).netloc.lower().strip()
    if netloc.startswith("www."):
        return netloc[4:]
    return netloc


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


def _duckduckgo_search_url(query: str) -> str:
    """Build the DuckDuckGo HTML endpoint URL for a query."""
    base_url = (
        os.environ.get(_FREE_SEARCH_DDG_URL_ENV, "https://html.duckduckgo.com/html/")
        or "https://html.duckduckgo.com/html/"
    ).strip()
    separator = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('?&')}{separator}q={quote_plus(query)}"


def _search_request_headers(query: str) -> dict[str, str]:
    """Return browser-like headers aligned to the query language."""
    headers = dict(_REQUEST_HEADERS)
    if _contains_cjk(query):
        headers["Accept-Language"] = "zh-CN,zh-Hans;q=0.9,zh;q=0.8,en;q=0.6"
    else:
        headers["Accept-Language"] = "en-US,en;q=0.9"
    return headers


def _safe_env_choice(name: str, default: str, allowed: set[str]) -> str:
    """Read an env string only when it is in a conservative allowlist."""
    from openjiuwen.core.common.logging import tool_logger

    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    normalized = raw.lower()
    if normalized in allowed:
        return normalized
    tool_logger.warning("Ignoring unsupported paid-search model %s=%r; using %s", name, raw, default)
    return default


def _get_free_search_proxy_url() -> str:
    """Return the configured proxy URL used by web search/fetch tools."""
    return str(os.environ.get(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _free_search_ssl_verify() -> bool:
    """Whether to verify TLS certificates (default off for intranet usage)."""
    return _env_flag(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _no_proxy_entries() -> list[str]:
    """Return the NO_PROXY host entries, lower-cased."""
    configured = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or _FREE_SEARCH_DEFAULT_NO_PROXY
    return [entry.strip().lower() for entry in configured.split(",") if entry.strip()]


def _should_bypass_free_search_proxy(url: str) -> bool:
    """Whether the configured proxy should be bypassed for this URL."""
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


def _resolve_proxy(url: str) -> str | None:
    """Resolve the proxy URL to use for a request, or None to go direct."""
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url or _should_bypass_free_search_proxy(url):
        return None
    return proxy_url


def is_free_search_enabled() -> bool:
    """Whether at least one free-search backend is enabled."""
    return (
        _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False)
        or _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False)
    )


def is_paid_search_enabled() -> bool:
    """Whether at least one paid-search provider API key is configured."""
    return any(str(os.environ.get(key, "") or "").strip() for key in _PAID_SEARCH_API_KEY_ENVS)


def _configured_paid_search_providers() -> list[str]:
    """Return paid-search providers whose API keys are configured, in fallback order."""
    return [
        provider
        for provider in _PAID_SEARCH_PROVIDER_ORDER
        if str(os.environ.get(_PAID_SEARCH_PROVIDER_KEY_ENVS[provider], "") or "").strip()
    ]
