# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SubagentRail — registers task, session, or runtime tools on DeepAgent."""

from __future__ import annotations

import asyncio
from typing import Callable, Collection, List, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.tools import get_tool_description
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools import SessionToolkit, build_session_tools, create_task_tool
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    DEFAULT_BROWSER_CAPABILITIES,
)
from openjiuwen.harness.tools.subagent._control_registry import release_all_subagent_controls
from openjiuwen.harness.tools.subagent.subagent_tools import build_subagent_tools

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


TaskPromptExtension = Callable[[AgentCallbackContext, str], Optional[str]]


class SubagentRail(DeepAgentRail):
    """Rail that registers subagent delegation tools on DeepAgent.

    Three modes are supported (``enable_subagent_runtime`` wins):

    - runtime: ``subagent_spawn`` / ``subagent_wait`` / ``subagent_list``
      with optional synchronous ``task_tool`` overrides for selected types
    - async session: ``sessions_spawn`` / ``sessions_list`` / ``sessions_cancel``
    - sync task: ``task_tool``
    """

    priority = 95

    _REFRESHABLE_TOOL_NAMES = {"task_tool", "sessions_spawn", "subagent_spawn"}

    def __init__(
        self,
        enable_async_subagent: bool = False,
        enable_subagent_runtime: bool = False,
        task_prompt_extension: TaskPromptExtension | None = None,
        synchronous_subagent_types: Collection[str] | None = None,
    ) -> None:
        """Initialize the subagent rail.

        Args:
            enable_async_subagent: Whether to register async session tools
                instead of the synchronous ``task_tool``.
            enable_subagent_runtime: Whether to register persistent subagent
                runtime tools instead of task/session tools.
            task_prompt_extension: Optional callback that supplies additional
                guidance for the synchronous ``task_tool`` prompt. It receives
                the current callback context and prompt language, and its
                result is appended to the same ``task_tool`` section.
            synchronous_subagent_types: Subagent names that remain on the
                synchronous ``task_tool`` while other configured subagents use
                the persistent runtime.
        """
        super().__init__()
        self.enable_async_subagent = enable_async_subagent
        self.enable_subagent_runtime = enable_subagent_runtime
        self.task_prompt_extension = task_prompt_extension
        self.synchronous_subagent_types = frozenset(
            str(name).strip()
            for name in (synchronous_subagent_types or ())
            if str(name).strip()
        )
        self.tools = None
        self._toolkit = None
        self.system_prompt_builder = None

    def _runtime_mode(self) -> bool:
        return self.enable_subagent_runtime

    def _async_mode(self) -> bool:
        return not self.enable_subagent_runtime and self.enable_async_subagent

    def init(self, agent) -> None:
        """Register subagent tools on the agent."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if not agent.deep_config.subagents:
            logger.info("[SubagentRail] No subagents configured, skipping")
            return

        configured_subagents = list(agent.deep_config.subagents)
        available_agents = self._build_available_agents_description(configured_subagents)
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        if self._runtime_mode():
            runtime_subagents, sync_subagents = self._partition_runtime_subagents(
                configured_subagents
            )
            self.tools = []
            if runtime_subagents:
                runtime_names = self._subagent_names(runtime_subagents)
                self.tools.extend(
                    build_subagent_tools(
                        parent_agent=agent,
                        language=self.system_prompt_builder.language,
                        available_agents=self._build_available_agents_description(
                            runtime_subagents
                        ),
                        agent_id=agent_id,
                        allowed_subagent_types=runtime_names,
                    )
                )
            if sync_subagents:
                sync_names = self._subagent_names(sync_subagents)
                self.tools.extend(
                    create_task_tool(
                        parent_agent=agent,
                        available_agents=self._build_available_agents_description(
                            sync_subagents
                        ),
                        language=self.system_prompt_builder.language,
                        agent_id=agent_id,
                        allowed_subagent_types=sync_names,
                    )
                )
            mode = "runtime with sync overrides" if sync_subagents else "runtime"
        elif self._async_mode():
            self._toolkit = SessionToolkit()
            agent.set_session_toolkit(self._toolkit)
            self.tools = build_session_tools(
                parent_agent=agent,
                toolkit=self._toolkit,
                language=self.system_prompt_builder.language,
                available_agents=available_agents,
                agent_id=agent_id,
            )
            mode = "async session"
        else:
            self.tools = create_task_tool(
                parent_agent=agent,
                available_agents=available_agents,
                language=self.system_prompt_builder.language,
                agent_id=agent_id,
                allowed_subagent_types=self._subagent_names(configured_subagents),
            )
            mode = "sync task"

        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

        logger.info(
            "[SubagentRail] Registered %s tool(s) with %s subagent(s)",
            mode,
            len(agent.deep_config.subagents),
        )

    def refresh_available_agents(self, agent) -> None:
        """Refresh the available-agents text in registered subagent tool cards."""
        if not self.tools:
            return
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", self.system_prompt_builder)
        language = getattr(self.system_prompt_builder, "language", "cn")
        configured_subagents = list(agent.deep_config.subagents or [])
        runtime_subagents, sync_subagents = self._partition_runtime_subagents(
            configured_subagents
        )
        runtime_agents = self._build_available_agents_description(runtime_subagents)
        sync_agents = self._build_available_agents_description(sync_subagents)
        all_agents = self._build_available_agents_description(configured_subagents)
        refreshed = []
        for tool in self.tools:
            card = getattr(tool, "card", None)
            name = getattr(card, "name", None)
            if name not in self._REFRESHABLE_TOOL_NAMES:
                continue
            if name == "subagent_spawn":
                available_agents = runtime_agents
                allowed_types = self._subagent_names(runtime_subagents)
            elif self._runtime_mode():
                available_agents = sync_agents
                allowed_types = self._subagent_names(sync_subagents)
            else:
                available_agents = all_agents
                allowed_types = self._subagent_names(configured_subagents)
            card.description = get_tool_description(name, language).format(
                available_agents=available_agents,
            )
            set_allowed_types = getattr(tool, "set_allowed_subagent_types", None)
            if callable(set_allowed_types):
                set_allowed_types(allowed_types)
            refreshed.append(name)
        if refreshed:
            logger.info("[SubagentRail] Refreshed available_agents for %s", ", ".join(refreshed))

    def uninit(self, agent) -> None:
        """Remove tools from the agent."""
        if self._runtime_mode():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning(
                    "[SubagentRail] skip subagent cancel_all: no running event loop",
                )
            else:
                loop.create_task(release_all_subagent_controls(agent, reason="rail_uninit"))

        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool.card, "name", None)
                if name:
                    agent.ability_manager.remove_ability(name)

        if self._async_mode():
            agent.set_session_toolkit(None)

        if self._runtime_mode():
            mode = "runtime"
        elif self._async_mode():
            mode = "async session"
        else:
            mode = "sync task"
        logger.info("[SubagentRail] Unregistered %s tools", mode)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the tool system prompt section before model call."""
        if not self.tools or self.system_prompt_builder is None:
            return

        if self._runtime_mode():
            tool_names = {
                getattr(getattr(tool, "card", None), "name", None)
                for tool in self.tools
            }
            try:
                from openjiuwen.harness.prompts.sections.subagent_tools import (
                    build_subagent_tools_section,
                )

                language = self.system_prompt_builder.language
                extension_content = None
                if (
                    "task_tool" not in tool_names
                    and self.task_prompt_extension is not None
                ):
                    extension_content = self.task_prompt_extension(ctx, language)
                if "subagent_spawn" in tool_names:
                    section = build_subagent_tools_section(
                        language=language,
                        extension_content=extension_content,
                    )
                    if section is not None:
                        self.system_prompt_builder.add_section(section)
                else:
                    self.system_prompt_builder.remove_section(SectionName.SUBAGENT_TOOLS)
            except ImportError:
                logger.warning("[SubagentRail] subagent_tools prompt section not available, skipping")

            if "task_tool" in tool_names:
                self._inject_task_tool_section(ctx)
            else:
                self.system_prompt_builder.remove_section(SectionName.TASK_TOOL)
            return

        if not self.enable_async_subagent:
            self._inject_task_tool_section(ctx)
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
            logger.warning("[SubagentRail] session_tools prompt section not available, skipping")

    def _inject_task_tool_section(self, ctx: AgentCallbackContext) -> None:
        try:
            from openjiuwen.harness.prompts.sections.task_tool import build_task_section

            language = self.system_prompt_builder.language
            extension_content = None
            if self.task_prompt_extension is not None:
                extension_content = self.task_prompt_extension(ctx, language)
            section = build_task_section(
                language=language,
                extension_content=extension_content,
            )
            if section is not None:
                self.system_prompt_builder.add_section(section)
            else:
                self.system_prompt_builder.remove_section(SectionName.TASK_TOOL)
        except ImportError:
            logger.warning("[SubagentRail] task_tool prompt section not available, skipping")

    def _partition_runtime_subagents(
        self,
        subagents: List[SubAgentConfig | "DeepAgent"],
    ) -> tuple[List[SubAgentConfig | "DeepAgent"], List[SubAgentConfig | "DeepAgent"]]:
        if not self._runtime_mode() or not self.synchronous_subagent_types:
            return list(subagents), []
        runtime_subagents = []
        sync_subagents = []
        for spec in subagents:
            name = self._extract_agent_meta(spec)[0]
            target = (
                sync_subagents
                if name in self.synchronous_subagent_types
                else runtime_subagents
            )
            target.append(spec)
        return runtime_subagents, sync_subagents

    def _subagent_names(
        self,
        subagents: List[SubAgentConfig | "DeepAgent"],
    ) -> frozenset[str]:
        return frozenset(self._extract_agent_meta(spec)[0] for spec in subagents)

    _KNOWN_AGENT_TOOLS: dict[str, str] = {
        "explore_agent": "bash, glob, grep, list_files, read_file",
        "plan_agent": "bash, glob, grep, list_files, read_file",
        "browser_agent": (
            "browser_probe_cards, browser_probe_interactives, browser_custom_action, "
            "browser_list_custom_actions, browser_runtime_health"
        ),
    }

    def _build_available_agents_description(self, subagents: List[SubAgentConfig | "DeepAgent"]) -> str:
        if not subagents:
            return ""

        lines = []
        for spec in subagents:
            agent_name, agent_desc = self._extract_agent_meta(spec)
            tools_str = self._extract_agent_tools(spec, agent_name)
            lines.append(f"- {agent_name}: {agent_desc} (Tools: {tools_str})")
            if agent_name == "browser_agent":
                lines.append("  Available Playwright capabilities:")
                for capability in DEFAULT_BROWSER_CAPABILITIES:
                    tool_names = ", ".join(capability.tool_names)
                    lines.append(f"    - {capability.name}: {capability.description} (Tools: {tool_names})")

        return "\n".join(lines)

    def _extract_agent_meta(self, spec: SubAgentConfig | "DeepAgent") -> tuple[str, str]:
        if isinstance(spec, SubAgentConfig):
            return spec.agent_card.name, spec.agent_card.description

        card = getattr(spec, "card", None)
        name = getattr(card, "name", None) or "general-purpose"
        description = getattr(card, "description", None) or "DeepAgent instance"
        return name, description

    def _extract_agent_tools(self, spec: SubAgentConfig | "DeepAgent", agent_name: str) -> str:
        if isinstance(spec, SubAgentConfig) and spec.tools:
            names = []
            for tool in spec.tools:
                name = getattr(tool, "name", None) or getattr(getattr(tool, "card", None), "name", None)
                if name:
                    names.append(name)
            if names:
                return ", ".join(names)

        if not isinstance(spec, SubAgentConfig):
            ability_mgr = getattr(spec, "ability_manager", None)
            if ability_mgr is not None:
                try:
                    tool_names: list[str] = []
                    list_abilities = getattr(ability_mgr, "list", None)
                    cards = list_abilities() if callable(list_abilities) else []
                    if not isinstance(cards, list):
                        cards = []
                    for card in cards:
                        if not isinstance(card, ToolCard):
                            continue
                        name = getattr(card, "name", None)
                        if isinstance(name, str):
                            tool_names.append(name)
                    if tool_names:
                        return ", ".join(tool_names)
                except (AttributeError, TypeError) as exc:
                    logger.debug(
                        "[SubagentRail] Failed to extract tool names from agent %s: %s",
                        agent_name,
                        exc,
                    )

        if agent_name in self._KNOWN_AGENT_TOOLS:
            return self._KNOWN_AGENT_TOOLS[agent_name]

        return "All tools"


__all__ = [
    "SubagentRail",
    "TaskPromptExtension",
]
