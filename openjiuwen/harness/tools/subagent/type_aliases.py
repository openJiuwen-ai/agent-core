# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Map dest-stable YAML keys onto SDK AgentCard names.

``jiuwenswarm`` config uses ``general_agent``; factory injects
``AgentCard(name="general-purpose")``. Allowlists and spec lookup must treat
those as the same type or ``task_tool`` / ``subagent_spawn`` raise 182505.
"""

from __future__ import annotations

from collections.abc import Collection

_CANONICAL_SUBAGENT_TYPES = {
    "general_agent": "general-purpose",
}


def canonicalize_subagent_type(subagent_type: str) -> str:
    """Return the SDK card name for a configured or advertised type."""
    name = str(subagent_type or "").strip()
    return _CANONICAL_SUBAGENT_TYPES.get(name, name)


def subagent_type_allowed(
    subagent_type: str,
    allowed_subagent_types: Collection[str] | None,
) -> bool:
    """Return True when the requested type matches an allowlist entry."""
    if allowed_subagent_types is None:
        return True
    wanted = canonicalize_subagent_type(subagent_type)
    return any(
        canonicalize_subagent_type(item) == wanted
        for item in allowed_subagent_types
    )
