# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission narrowing: merge a tool-level override into the base config.

The override can only *tighten* (restrict) permissions — never *loosen* them.
For each tool listed in the override, the effective permission level is the
``strictest()`` of the base level and the override level.
"""

from __future__ import annotations

import copy
from typing import Any

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.tiered_policy import _parse_level, strictest


def narrow_permissions(
    base_config: dict[str, Any],
    tools_override: dict[str, str],
) -> dict[str, Any]:
    """Narrow base permissions by applying tool-level overrides.

    For each tool in ``tools_override``, the result level is
    ``strictest(base_level, override_level)``.  For tools not listed in the
    base ``tools`` dict, the effective base level is resolved from ``defaults``
    (per-key → wildcard ``"*"`` → fallback ``ASK``) before applying
    ``strictest()``.

    This guarantees the result is never less restrictive than either input.
    All other fields (``defaults``, ``rules``, ``approval_overrides`,
    ``permission_mode``, etc.) are preserved unchanged.

    Args:
        base_config: Full permission config dict (as consumed by PermissionEngine).
        tools_override: Flat ``{tool_name: level_string}`` dict.
            Only ``"allow"`` / ``"ask"`` / ``"deny"`` are valid values.

    Returns:
        Deep-copied config dict with the ``tools`` field narrowed.
    """
    narrowed = copy.deepcopy(base_config)
    base_tools = narrowed.get("tools", {})
    defaults = narrowed.get("defaults", {})

    for tool_name, override_level_str in tools_override.items():
        override_level = PermissionLevel(override_level_str)
        base_level_str = base_tools.get(tool_name)

        if base_level_str is not None:
            base_level = _parse_level(base_level_str)
            narrowed_level = strictest(base_level, override_level)
        else:
            # Resolve effective base level from defaults.
            default_str = defaults.get(tool_name) or defaults.get("*")
            default_level = _parse_level(default_str) if default_str else PermissionLevel.ASK
            narrowed_level = strictest(default_level, override_level)

        base_tools[tool_name] = narrowed_level.value

    narrowed["tools"] = base_tools
    return narrowed


def format_base_permissions_for_desc(
    config: dict[str, Any],
    *,
    lang: str = "en",
) -> str:
    """Format base permission rules as readable text.

    Generates a multi-line description of the current base permission
    configuration (explicit tool rules + defaults fallback) and the
    narrowing constraints, suitable for injection into prompts or
    tool descriptions where the caller needs to understand what
    permissions are in effect and what narrowing is allowed.

    Args:
        config: Full permission config dict (as consumed by PermissionEngine).
        lang: ``"en"`` or ``"cn"`` for bilingual output.

    Returns:
        Multi-line string describing current base permissions and narrowing
        rules.  Returns empty string if ``tools`` is empty or missing.
    """
    tools = config.get("tools", {})
    defaults = config.get("defaults", {})
    if not tools and not defaults:
        return ""

    lines: list[str] = []

    if lang == "cn":
        lines.append("当前 teammate 的基础权限规则：")
    else:
        lines.append("Current teammate base permissions:")

    # List explicit tool rules first.
    for tool, level in sorted(tools.items()):
        lines.append(f"- {tool}: {level}")

    # Show the default wildcard if present (tools not listed above follow this).
    wildcard = defaults.get("*")
    if wildcard:
        if lang == "cn":
            lines.append(f"- 其他未列出的工具: {wildcard} (defaults 兜底)")
        else:
            lines.append(f"- Other tools not listed above: {wildcard} (defaults fallback)")

    # Narrowing rules explanation.
    if lang == "cn":
        lines.append("")
        lines.append("收窄规则：只能收紧，不能放宽。")
        lines.append("ask → deny ✓  |  allow → ask/deny ✓  |  deny → allow/ask ✗ (自动修正)")
    else:
        lines.append("")
        lines.append("Narrowing rules: only tightening, never loosening.")
        lines.append("ask → deny ✓  |  allow → ask/deny ✓  |  deny → allow/ask ✗ (auto-corrected)")

    return "\n".join(lines)