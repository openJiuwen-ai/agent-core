# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fetch-tool network policy (Pipeline C). ASK interrupts before tool execution."""

from __future__ import annotations

import logging
import ipaddress
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.match_trace import record_match, record_target
from openjiuwen.harness.security.permission_engine.toolguard.pattern_matchers import match_wildcard

logger = logging.getLogger(__name__)

_FETCH_TOOLS = frozenset({"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"})
_URL_ARG_KEYS = ("url", "uri", "href", "webpage", "page_url", "target_url")
_VALID_ACTIONS = frozenset({"allow", "ask", "deny"})
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


def url_hostname(url: str) -> str | None:
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
    endpoint = re.fullmatch(r"(\[[^\]]+\]|[^:/#@\[\]]+):([0-9]+)", pat)
    if endpoint:
        try:
            port = int(endpoint[2])
            target = urlparse(normalized if "://" in normalized else f"//{normalized}")
            target_port = target.port
            if target_port is None:
                target_port = {"http": 80, "https": 443}.get(target.scheme.lower())
            host = target.hostname
        except ValueError:
            return False
        if not host or not 0 < port <= 65535 or target_port != port:
            return False
        pattern_host = endpoint[1].removeprefix("[").removesuffix("]")
        try:
            return ipaddress.ip_address(host) == ipaddress.ip_address(pattern_host)
        except ValueError:
            return match_wildcard(host.lower().rstrip("."), pattern_host.lower().rstrip("."))
    host = url_hostname(normalized)
    if not host:
        return False
    return match_wildcard(host, pat.lower())


class NetGuardChecker:
    """Network allow/ask/deny for fetch tools. ALLOW does not lift Pipeline A."""

    def __init__(self, section: Mapping[str, Any], composition: Mapping[str, Any] | None = None):
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
        self._source_order = tuple(composition.get("source_order", ())) if composition else ()
        self._url_layers = composition.get("net_url_layers", {}) if composition else {}
        self._composed = composition is not None
        raw_enforce = section.get("enforce_host_exit")
        self._enforce_host_exit = True if raw_enforce is None else bool(raw_enforce)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def enforce_host_exit(self) -> bool:
        return self._enforce_host_exit

    def evaluate(
        self,
        tool_name: str,
        tool_args: Mapping[str, Any],
    ) -> PermissionResult | None:
        if not self._enabled:
            return None
        if str(tool_name or "").strip() not in _FETCH_TOOLS:
            return None
        return self.check_url(extract_fetch_url(tool_args))

    def check_url(self, url: str | None) -> PermissionResult | None:
        """Return ASK/DENY or ``None`` (allowed); callers handle ASK before tools."""
        result = self._check_url(url)
        if url:
            record_target("domain", url, result.permission if result is not None else PermissionLevel.ALLOW)
        return result

    def _check_url(self, url: str | None) -> PermissionResult | None:
        if not self._enabled:
            return None
        hits: list[tuple[str, str]] = []
        if url:
            for pattern, action in self._urls.items():
                if match_net_pattern(pattern, url):
                    hits.append((pattern, action))
                    record_match("domain", url, PermissionLevel(action), pattern,
                                 self._url_layers.get(pattern) if self._composed else None)
        if hits:
            if any(action == "deny" for _pattern, action in hits):
                pattern = next(p for p, action in hits if action == "deny")
                source = self._url_layers.get(pattern) if self._composed else None
                return PermissionResult(
                    permission=PermissionLevel.DENY,
                    matched_rule=f"net_guard:source:{source}:url:{pattern}" if source else f"net_guard:url:{pattern}",
                    reason=f"net_guard denied: {pattern}",
                )
            if self._composed:
                for source in self._source_order:
                    selected = [
                        (pattern, action) for pattern, action in hits
                        if self._url_layers.get(pattern) == source
                    ]
                    if not selected:
                        continue
                    if any(action == "ask" for _pattern, action in selected):
                        pattern = next(pattern for pattern, action in selected if action == "ask")
                        return PermissionResult(
                            PermissionLevel.ASK, f"net_guard:source:{source}:url:{pattern}",
                            f"net_guard requires approval: {pattern}",
                        )
                    return None
            if any(action == "ask" for _pattern, action in hits):
                pattern = next(p for p, action in hits if action == "ask")
                return PermissionResult(PermissionLevel.ASK, f"net_guard:url:{pattern}",
                                        f"net_guard requires approval: {pattern}")
            return None
        record_match("domain", url or "", PermissionLevel(self._defaults), "net_guard:defaults", is_default=True)
        if self._defaults in {"deny", "ask"}:
            return PermissionResult(
                permission=PermissionLevel(self._defaults),
                matched_rule="net_guard:defaults",
                reason=f"net_guard {self._defaults}: defaults",
            )
        return None


def build_net_guard_checker(
    permissions: Mapping[str, Any] | None,
) -> NetGuardChecker | None:
    perms = permissions if isinstance(permissions, Mapping) else {}
    section = perms.get("net_guard")
    if not isinstance(section, Mapping) or not section.get("enabled"):
        return None
    composition = perms.get("composition")
    return NetGuardChecker(section, composition if isinstance(composition, Mapping) else None)


__all__ = [
    "NetGuardChecker",
    "build_net_guard_checker",
    "extract_fetch_url",
    "match_net_pattern",
    "url_hostname",
]
