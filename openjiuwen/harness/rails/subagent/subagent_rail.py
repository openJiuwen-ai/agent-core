# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SubagentRail — registers task or session tools on DeepAgent for subagent delegation."""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools import SessionToolkit, build_session_tools, create_task_tool

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


class SubagentRail(DeepAgentRail):
    """Rail that registers task or session tools for subagent delegation.

    When ``enable_async_subagent`` is False (default), registers synchronous
    task tools for ephemeral subagent delegation.

    When ``enable_async_subagent`` is True, registers async session tools
    that allow spawning background subagent tasks, and injects the session
    tools prompt section before each model call.
    """

    priority = 95

    def __init__(self, enable_async_subagent: bool = False) -> None:
        super().__init__()
        self.enable_async_subagent = enable_async_subagent
        self.tools = None
        self._toolkit = None  # only used in async branch
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        """Register task or session tools on the agent.

        Args:
            agent: DeepAgent instance to register tools on.
        """
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        # Skip registration if no subagents are configured
        if not agent.deep_config.subagents:
            logger.info("[SubagentRail] No subagents configured, skipping")
            return

        # Build available_agents description for tool registration
        available_agents = self._build_available_agents_description(agent.deep_config.subagents)
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        if self.enable_async_subagent:
            self._toolkit = SessionToolkit()
            agent.set_session_toolkit(self._toolkit)
            self.tools = build_session_tools(
                parent_agent=agent,
                toolkit=self._toolkit,
                language=self.system_prompt_builder.language,
                available_agents=available_agents,
                agent_id=agent_id,
            )
        else:
            self.tools = create_task_tool(
                parent_agent=agent,
                available_agents=available_agents,
                language=self.system_prompt_builder.language,
                agent_id=agent_id,
            )

        Runner.resource_mgr.add_tool(list(self.tools))
        for tool in self.tools:
            agent.ability_manager.add(tool.card)

        mode = "async session" if self.enable_async_subagent else "sync task"
        logger.info(f"[SubagentRail] Registered {mode} tool with {len(agent.deep_config.subagents)} subagent(s)")

    def uninit(self, agent) -> None:
        """Remove tools from the agent.

        Args:
            agent: DeepAgent instance to remove tools from.
        """
        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool.card, "name", None)
                if name:
                    agent.ability_manager.remove(name)
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)

        if self.enable_async_subagent:
            agent.set_session_toolkit(None)
        mode = "async session" if self.enable_async_subagent else "sync task"

        logger.info(f"[SubagentRail] Unregistered {mode} tools")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject tool system prompt section before model call.

        In sync mode (enable_async_subagent=False), injects the task_tool
        prompt section so the model sees delegation guidance.
        In async mode, injects the session tools section so the model can
        see available session tools.

        Args:
            ctx: Agent callback context.
        """
        if not self.tools or self.system_prompt_builder is None:
            return

        if not self.enable_async_subagent:
            try:
                from openjiuwen.harness.prompts.sections.task_tool import (
                    build_task_section,
                )

                section = build_task_section(language=self.system_prompt_builder.language)
                if section is not None:
                    self.system_prompt_builder.add_section(section)
                else:
                    self.system_prompt_builder.remove_section(SectionName.TASK_TOOL)
            except ImportError:
                logger.warning(
                    "[SubagentRail] task_tool prompt section not available, skipping"
                )
            return

        try:
            from openjiuwen.harness.prompts.sections.session_tools import (
                build_session_tools_section,
            )

            section = build_session_tools_section(language=self.system_prompt_builder.language)
            if section is not None:
                self.system_prompt_builder.add_section(section)
            else:
                self.system_prompt_builder.remove_section(SectionName.SESSION_TOOLS)
        except ImportError:
            logger.warning(
                "[SubagentRail] session_tools prompt section not available, skipping"
            )

    # Well-known tool sets for built-in agent types whose tools are resolved
    # at runtime (i.e. ``SubAgentConfig.tools`` is empty).
    _KNOWN_AGENT_TOOLS: dict[str, str] = {
        "explore_agent": "bash, glob, grep, list_files, read_file",
        "plan_agent": "bash, glob, grep, list_files, read_file",
    }

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
            tools_str = self._extract_agent_tools(spec, agent_name)
            lines.append(f"- {agent_name}: {agent_desc} (Tools: {tools_str})")

        return "\n".join(lines)

    def _extract_agent_meta(self, spec: SubAgentConfig | "DeepAgent") -> tuple[str, str]:
        if isinstance(spec, SubAgentConfig):
            return spec.agent_card.name, spec.agent_card.description

        card = getattr(spec, "card", None)
        name = getattr(card, "name", None) or "general-purpose"
        description = getattr(card, "description", None) or "DeepAgent instance"
        return name, description

    def _extract_agent_tools(self, spec: SubAgentConfig | "DeepAgent", agent_name: str) -> str:
        """Extract a human-readable tool list for a subagent spec.

        Resolution order:
        1. Explicit ``tools`` on SubAgentConfig — extract card names.
        2. DeepAgent instance — read registered tool names from ability_manager.
        3. Well-known defaults for built-in agent types.
        4. Fallback to ``"All tools"``.
        """
        # 1. SubAgentConfig with explicit tools
        if isinstance(spec, SubAgentConfig) and spec.tools:
            names = []
            for t in spec.tools:
                name = getattr(t, "name", None) or getattr(getattr(t, "card", None), "name", None)
                if name:
                    names.append(name)
            if names:
                return ", ".join(names)

        # 2. DeepAgent instance with registered tools
        if not isinstance(spec, SubAgentConfig):
            ability_mgr = getattr(spec, "ability_manager", None)
            if ability_mgr is not None:
                try:
                    tool_names: list[str] = []
                    for c in getattr(ability_mgr, "cards", []):
                        n = getattr(c, "name", None)
                        if isinstance(n, str):
                            tool_names.append(n)
                    if tool_names:
                        return ", ".join(tool_names)
                except (AttributeError, TypeError) as e:
                    logger.debug(
                        "[SubagentRail] Failed to extract tool names from agent %s: %s",
                        agent_name,
                        e,
                    )

        # 3. Well-known defaults
        if agent_name in self._KNOWN_AGENT_TOOLS:
            return self._KNOWN_AGENT_TOOLS[agent_name]

        # 4. Fallback
        return "All tools"


__all__ = [
    "SubagentRail",
]
