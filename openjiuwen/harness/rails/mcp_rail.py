# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""McpRail: registers MCP resource tools onto the agent.

MCP server registration is handled separately via DeepAgentConfig.mcps.
This rail mounts ListMcpResourcesTool and ReadMcpResourceTool so the LLM
can discover and read resources from already-registered MCP servers.
"""
from __future__ import annotations

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.mcp_tools import ListMcpResourcesTool, ReadMcpResourceTool


class McpRail(DeepAgentRail):
    """Rail that exposes MCP resource listing and reading as agent tools."""

    priority = 95

    def __init__(self) -> None:
        super().__init__()
        self.tools = None

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        list_tool = ListMcpResourcesTool(lang, agent_id)
        read_tool = ReadMcpResourceTool(lang, agent_id)
        self.tools = [list_tool, read_tool]

        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if not self.tools:
            return
        for tool in self.tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove_ability(name)


__all__ = ["McpRail"]
