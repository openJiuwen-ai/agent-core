# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Narrow a member spec down to what a one-shot reviewer needs.

A scheduled-dispatch reviewer only inspects a deliverable and casts a vote, so
it should not inherit a teammate's full capability set (todo planning,
sub-agents, write access, scheduling and messaging tools). The framework owns
the capability switches it knows by name; the host that assembled the member
capabilities knows the rest and narrows them through allow-lists.
"""

from __future__ import annotations

from collections.abc import Collection

from openjiuwen.harness.manifest.builtin_elements import SYS_OPERATION
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, DeepAgentSpec, RailSpec


def build_reviewer_agent_spec(
    base: DeepAgentSpec,
    *,
    rail_types: Collection[str] | None = None,
    tool_types: Collection[str] | None = None,
) -> DeepAgentSpec:
    """Derive a reviewer base spec from a member spec.

    Always applied: file tools become read-only, harness todo planning is off,
    and no sub-agent is mounted (declared or general-purpose). MCP servers and
    raw tool cards are inherited untouched.

    Args:
        base: The member spec to derive from (usually the teammate spec).
        rail_types: Rail types to keep; ``None`` keeps every declared rail.
        tool_types: Builtin tool types to keep; ``None`` keeps every declared
            builtin tool.

    Returns:
        A new ``DeepAgentSpec``; ``base`` is left unmodified.
    """
    rails = [
        _read_only_rail(rail)
        for rail in base.rails or []
        if rail_types is None or rail.type in rail_types
    ]
    tools = [
        tool
        for tool in base.tools or []
        if tool_types is None or not isinstance(tool, BuiltinToolSpec) or tool.type in tool_types
    ]
    return base.model_copy(
        update={
            "rails": rails,
            "tools": tools,
            "subagents": None,
            "add_general_purpose_agent": False,
            "enable_async_subagent": False,
            "enable_task_planning": False,
        }
    )


def _read_only_rail(rail: RailSpec) -> RailSpec:
    """Switch the file-system rail to read-only; other rails pass through."""
    if rail.type != SYS_OPERATION:
        return rail
    return rail.model_copy(update={"params": {**(rail.params or {}), "read_only": True}})


__all__ = ["build_reviewer_agent_spec"]
