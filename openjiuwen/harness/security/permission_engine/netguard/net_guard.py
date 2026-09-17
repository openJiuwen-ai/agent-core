# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fetch-tool network policy (Pipeline C). ``allow`` / ``deny`` only; no ASK."""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import match_wildcard

logger = logging.getLogger(__name__)

_FETCH_TOOLS = frozenset({"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"})
_URL_ARG_KEYS = ("url", "uri", "href", "webpage", "page_url", "target_url")
_VALID_ACTIONS = frozenset({"allow", "deny"})
# URL glob may include query/fragment characters that command wildcards reject.
_URL_WILDCARD_CHARS = r"[-a-zA-Z0-9._/:?#&=%+~@,;\[\]']"


def _parse_action(value: Any) -> str | None:
    raw: Any = value
    if isinstance(value, dict):
        raw = value.get("action") if value.get("action") is not None else value.get("fetch")
    if not isinstance(raw, str) or not raw.strip():
        return None
    action = raw.strip().lower()
    return action if action in _VALID_ACTIONS else None


def extract_fetch_url(tool_args: Mapping[str, Any] | None) -> str | None:
    if not isinstance(tool_args, Mapping):
        return None
    for key in _URL_ARG_KEYS:
        raw = tool_args.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().replace("\\", "/")
    return None


def _hostname(url: str) -> str | None:
    text = url.strip()
    if not text:
        return None
    candidate = text if "://" in text else f"http://{text}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    return host or None


def _match_url_glob(value: str, pattern: str) -> bool:
    if not value or not pattern:
        return False
    val = value.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    to_escape = set(".+^${}()|[]\\")
    escaped = "".join("\\" + c if c in to_escape else c for c in pat)
    escaped = escaped.replace("?", _URL_WILDCARD_CHARS)
    escaped = escaped.replace("*", _URL_WILDCARD_CHARS + "*")
    try:
        return bool(re.fullmatch(escaped, val))
    except re.error:
        return False


def match_net_pattern(pattern: str, url: str) -> bool:
    pat = str(pattern or "").strip().replace("\\", "/")
    normalized = str(url or "").strip().replace("\\", "/")
    if not pat or not normalized:
        return False
    if "://" in pat:
        if "*" in pat or "?" in pat:
            return _match_url_glob(normalized, pat)
        return normalized == pat or normalized.startswith(pat)
    host = _hostname(normalized)
    if not host:
        return False
    return match_wildcard(host, pat.lower())


class NetGuardChecker:
    """Network deny/allow for fetch tools. ALLOW does not lift Pipeline A."""

    def __init__(self, section: Mapping[str, Any]):
        self._enabled = bool(section.get("enabled"))
        defaults = _parse_action(section.get("defaults"))
        self._defaults = defaults or "allow"
        urls = section.get("urls")
        compiled: dict[str, str] = {}
        if isinstance(urls, Mapping):
            for pattern, value in urls.items():
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                action = _parse_action(value)
                if action is None:
                    continue
                compiled[pattern.strip()] = action
        self._urls = compiled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def evaluate(
        self,
        tool_name: str,
        tool_args: Mapping[str, Any],
    ) -> PermissionResult | None:
        if not self._enabled:
            return None
        if str(tool_name or "").strip() not in _FETCH_TOOLS:
            return None
        url = extract_fetch_url(tool_args)
        hits: list[tuple[str, str]] = []
        if url:
            for pattern, action in self._urls.items():
                if match_net_pattern(pattern, url):
                    hits.append((pattern, action))
        if hits:
            if any(action == "deny" for _pattern, action in hits):
                pattern = next(p for p, action in hits if action == "deny")
                return PermissionResult(
                    permission=PermissionLevel.DENY,
                    matched_rule=f"net_guard:url:{pattern}",
                    reason=f"net_guard denied: {pattern}",
                )
            return None
        if self._defaults == "deny":
            return PermissionResult(
                permission=PermissionLevel.DENY,
                matched_rule="net_guard:defaults",
                reason="net_guard denied: defaults",
            )
        return None


def build_net_guard_checker(
    permissions: Mapping[str, Any] | None,
) -> NetGuardChecker | None:
    perms = permissions if isinstance(permissions, Mapping) else {}
    section = perms.get("net_guard")
    if not isinstance(section, Mapping) or not section.get("enabled"):
        return None
    return NetGuardChecker(section)


__all__ = [
    "NetGuardChecker",
    "build_net_guard_checker",
    "extract_fetch_url",
    "match_net_pattern",
]
