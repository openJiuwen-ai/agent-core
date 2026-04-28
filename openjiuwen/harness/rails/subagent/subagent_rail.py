# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SubagentRail — registers task tool on DeepAgent for subagent delegation."""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools import create_task_tool

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


class SubagentRail(DeepAgentRail):
    """Rail that registers task tool for subagent delegation.

    This rail enables the main agent to delegate complex, multi-steps
    tasks to ephemeral subagents with isolated context windows.
    """

    priority = 95

    def __init__(self) -> None:
        super().__init__()
        self.tools = None
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        """Register task tool on the agent.

        Args:
            agent: DeepAgent instance to register tools on.
        """
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        # Skip registration if no subagents are configured
        if not agent.deep_config.subagents:
            logger.info("[SubagentRail] No subagents configured, skipping task tool registration")
            return

        # Build available_agents description for tool registration
        available_agents = self._build_available_agents_description(agent.deep_config.subagents)

        agent_id = getattr(getattr(agent, "card", None), "id", None)
        # Create and register task tool (使用统一的 build_tool_card)
        tools = create_task_tool(
            parent_agent=agent,
            available_agents=available_agents,
            language=self.system_prompt_builder.language,
            agent_id=agent_id,
        )
        self.tools = tools

        # Register tools in resource manager and ability manager
        Runner.resource_mgr.add_tool(list(tools))
        for tool in tools:
            agent.ability_manager.add(tool.card)

        logger.info(f"[SubagentRail] Registered task tool with {len(agent.deep_config.subagents)} subagent(s)")

    def uninit(self, agent) -> None:
        """Remove task tool from the agent.

        Args:
            agent: DeepAgent instance to remove tools from.
        """
        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool.card, 'name', None)
                if name:
                    agent.ability_manager.remove(name)
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)

            logger.info("[SubagentRail] Unregistered task tool")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """No standalone task_tool prompt section is managed here anymore."""
        _ = ctx
        return

    def _build_available_agents_description(self, subagents: List[SubAgentConfig | "DeepAgent"]) -> str:
        """Build description of available subagents for tool registration.

        Returns:
            Formatted string describing available subagent types.
        """
        if not subagents:
            return ""

        # Build available subagent types
        lines = []

        for spec in subagents:
            agent_name, agent_desc = self._extract_agent_meta(spec)
            lines.append(f'"{agent_name}": {agent_desc}')

        return "\n".join(lines)

    def _extract_agent_meta(self, spec: SubAgentConfig | "DeepAgent") -> tuple[str, str]:
        if isinstance(spec, SubAgentConfig):
            return spec.agent_card.name, spec.agent_card.description

        card = getattr(spec, "card", None)
        name = getattr(card, "name", None) or "general-purpose"
        description = getattr(card, "description", None) or "DeepAgent instance"
        return name, description

__all__ = [
    "SubagentRail",
]
