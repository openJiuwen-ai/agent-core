# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The declaration a provider adds so bare tool names resolve to its MCP tools.

A host writes its prompt in terms of the tools it registered, by the names it
gave them. A CLI does not show them under those names: it puts every MCP
server in a namespace of its own, and it may ship a built-in tool whose name
resembles one of the host's. The bare name in the prompt then has two readings,
and a smaller model picks the wrong one.

Only the provider knows how its CLI spells a tool out -- Claude Code's
``mcp__<server>__<tool>`` and Codex's ``mcp__<server>.<tool>`` do not even
agree on the server name -- so the provider states it, next to the servers it
just registered. The host states which server its bare names belong to; the two
meet on the server name and neither has to know the other's rule.
"""

from __future__ import annotations

from collections.abc import Mapping

#: What one server's line stands in for, so the reader knows what to substitute.
TOOL_PLACEHOLDER = "{tool}"


def mcp_tool_naming_preamble(patterns: Mapping[str, str]) -> str:
    """Render the naming declaration for the MCP servers of one session.

    Args:
        patterns: ``server name -> the name pattern the model calls its tools
            by``, each carrying :data:`TOOL_PLACEHOLDER` where the tool's own
            name goes.

    Returns:
        The declaration block, or ``""`` when no MCP server is registered.
    """
    if not patterns:
        return ""
    servers = "\n".join(f'<server name="{name}" tool-name="{pattern}"/>' for name, pattern in patterns.items())
    return (
        f"<mcp-tools>\n{servers}\n</mcp-tools>\n"
        f"Each line above is how you address that server's tools: put the tool's own name where "
        f"{TOOL_PLACEHOLDER} is. Where an instruction names one of those tools without that prefix, "
        f"it means the tool of that server, never a built-in tool of yours whose name resembles it."
    )


__all__ = ["TOOL_PLACEHOLDER", "mcp_tool_naming_preamble"]
