# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionRail — registers async session tools on DeepAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.sections import SectionName
from openjiuwen.deepagents.prompts.sections.tools.task_tool import (
    GENERAL_PURPOSE_AGENT_DESC,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.tools.session_tools import (
    SessionToolkit,
    build_session_tools,
)

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent


class SessionRail(DeepAgentRail):
    """Rail that registers async session tools (sessions_list, sessions_spawn).

    This rail enables the main agent to spawn async subagent tasks that run
    in the background without blocking the current conversation.
    """

    priority = 95

    def __init__(self, language: str = "cn") -> None:
        """Initialize SessionRail.

        Args:
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__()
        self.language = language
        self.tools = None
        self._toolkit = None
        self.system_prompt_builder = None

    def init(self, agent: "DeepAgent") -> None:
        """Register session tools on the agent.

        Args:
            agent: DeepAgent instance to register tools on.
        """
        self.system_prompt_builder = agent.system_prompt_builder

        # Create SessionToolkit and attach to agent
        self._toolkit = SessionToolkit()
        agent.set_session_toolkit(self._toolkit)

        # Build available_agents description
        available_agents = self._build_available_agents_description(
            agent.deep_config.subagents if agent.deep_config else []
        )

        # Build session tools
        self.tools = build_session_tools(
            parent_agent=agent,
            toolkit=self._toolkit,
            language=self.language,
            available_agents=available_agents,
        )

        # Register tools in resource manager
        Runner.resource_mgr.add_tool(list(self.tools))

        # Register tools in ability manager
        for tool in self.tools:
            agent.ability_manager.add(tool.card)

        logger.info(f"[SessionRail] Registered with {len(agent.deep_config.subagents)} subagent(s)")

    def uninit(self, agent: "DeepAgent") -> None:
        """Remove session tools from the agent.

        Args:
            agent: DeepAgent instance to remove tools from.
        """
        if self.tools:
            for tool in self.tools:
                name = getattr(tool, "name", None)
                if name:
                    agent.ability_manager.remove(name)
                Runner.resource_mgr.remove_tool(tool.card.id)
        agent.set_session_toolkit(None)
        logger.info("[SessionRail] Unregistered session tools and session toolkit")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject session tools prompt section before model call.

        Args:
            ctx: Agent callback context.
        """
        if not self.tools or self.system_prompt_builder is None:
            return

        try:
            from openjiuwen.deepagents.prompts.sections.session_tools import (
                build_session_tools_section,
            )

            section = build_session_tools_section(language=self.language)
            if section is not None:
                self.system_prompt_builder.add_section(section)
            else:
                self.system_prompt_builder.remove_section(SectionName.SESSION_TOOLS)
        except ImportError:
            logger.warning(
                "[SessionRail] session_tools prompt section not available, skipping"
            )

    def _build_available_agents_description(
        self, subagents: list[SubAgentConfig | "DeepAgent"]
    ) -> str:
        """Build description of available subagents for tool registration.

        Returns:
            Formatted string describing available subagent types.
        """
        default_desc = GENERAL_PURPOSE_AGENT_DESC.get(
            self.language, GENERAL_PURPOSE_AGENT_DESC["cn"]
        )

        if not subagents:
            return f'"general-purpose": {default_desc}'

        lines = []
        has_general_purpose = False

        for spec in subagents:
            agent_name, agent_desc = self._extract_agent_meta(spec)
            if agent_name == "general-purpose":
                has_general_purpose = True
            lines.append(f'"{agent_name}": {agent_desc}')

        if not has_general_purpose:
            lines.insert(0, f'"general-purpose": {default_desc}')

        return "\n".join(lines)

    def _extract_agent_meta(
        self, spec: SubAgentConfig | "DeepAgent"
    ) -> tuple[str, str]:
        """Extract agent name and description from config or instance."""
        if isinstance(spec, SubAgentConfig):
            return spec.agent_card.name, spec.agent_card.description

        card = getattr(spec, "card", None)
        name = getattr(card, "name", None) or "general-purpose"
        description = getattr(card, "description", None) or "DeepAgent instance"
        return name, description


__all__ = ["SessionRail"]
