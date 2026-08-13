# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""NetworkGuard：对 fetch/search 等工具的 host/URL 做 allow/ask/deny。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.harness.security.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.patterns import URLMatcher
from openjiuwen.harness.security.tiered_policy import _NETWORK_TOOLS, _parse_level, strictest

logger = logging.getLogger(__name__)

_URL_ARG_KEYS = ("url", "uri", "href", "link", "query", "q", "search_query")


def network_url_text(tool_args: dict[str, Any]) -> str:
    for key in _URL_ARG_KEYS:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate_network_guard(
    permission_config: dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> PermissionResult | None:
    """若工具属网络类则返回网络轴决策；否则 ``None``（不参与合并）。"""
    if tool_name not in _NETWORK_TOOLS:
        return None

    network = permission_config.get("network")
    if not isinstance(network, dict) or network.get("enabled", True) is False:
        return None

    mode = str(permission_config.get("mode") or "auto").strip().lower()
    ignore_user = bool(network.get("ignore_user_host_rules")) or mode == "full_access"

    default_raw = network.get("defaults", "allow")
    try:
        default_level = _parse_level(str(default_raw))
    except ValueError:
        default_level = PermissionLevel.ALLOW if mode != "strict" else PermissionLevel.ASK

    if ignore_user:
        return PermissionResult(
            permission=PermissionLevel.ALLOW,
            matched_rule="network_guard:full_access",
            reason="Full Access ignores user host/URL ask rules",
        )

    url = network_url_text(tool_args)
    matcher = URLMatcher()
    hosts = network.get("hosts") if isinstance(network.get("hosts"), list) else []
    matched_level: PermissionLevel | None = None
    matched_rule: str | None = None
    for item in hosts:
        if not isinstance(item, dict):
            continue
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if url and not matcher.match_url(pattern.strip(), url):
            continue
        if not url:
            continue
        action = str(item.get("action") or "ask").strip().lower()
        try:
            level = _parse_level(action)
        except ValueError:
            continue
        if matched_level is None:
            matched_level = level
            matched_rule = f"network_guard:host[{pattern.strip()}]"
        else:
            matched_level = strictest(matched_level, level)
            matched_rule = f"{matched_rule}|network_guard:host[{pattern.strip()}]"

    if matched_level is not None:
        return PermissionResult(
            permission=matched_level,
            matched_rule=matched_rule,
            reason=f"Network host rule: {matched_rule}",
        )

    return PermissionResult(
        permission=default_level,
        matched_rule="network_guard:defaults",
        reason=f"Network default: {default_level.value}",
    )


__all__ = ["evaluate_network_guard", "network_url_text"]
