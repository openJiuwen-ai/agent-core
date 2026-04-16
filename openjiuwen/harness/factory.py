# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Factory function for creating DeepAgent instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional
from os import PathLike

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails import (
    SecurityRail,
    SkillUseRail,
    SessionRail,
    SubagentRail,
    TaskPlanningRail,
)
from openjiuwen.harness.schema.agent_mode import AgentMode
from openjiuwen.harness.schema.config import (
    AudioModelConfig,
    DeepAgentConfig,
    SubAgentConfig,
    VisionModelConfig,
)
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.prompts.sections.tools.task_tool import GENERAL_PURPOSE_AGENT_DESC
from openjiuwen.harness.tools.web_tools import is_free_search_enabled


def _is_disabled_free_search_tool(tool: Tool | ToolCard) -> bool:
    card = tool.card if isinstance(tool, Tool) else tool
    return card.name == "free_search" and not is_free_search_enabled()


def _normalize_tools(
    tools: Optional[List[Tool | ToolCard]],
) -> tuple[List[ToolCard], List[Tool]]:
    """Split mixed tool inputs into cards and concrete tool instances."""
    normalized_cards: List[ToolCard] = []
    tool_instances: List[Tool] = []

    for tool in tools or []:
        if _is_disabled_free_search_tool(tool):
            continue
        if isinstance(tool, Tool):
            tool_instances.append(tool)
            normalized_cards.append(tool.card)
            continue
        if isinstance(tool, ToolCard):
            normalized_cards.append(tool)
            continue
        raise TypeError(
            "tools must contain Tool or ToolCard instances, "
            f"got {type(tool).__name__}"
        )

    return normalized_cards, tool_instances


def _register_tool_instances(
    tool_instances: List[Tool],
    *,
    tag: str,
) -> None:
    """Register concrete tool instances so ToolCards become executable."""
    for tool in tool_instances:
        existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
        if existing_tool is not None:
            if existing_tool is not tool:
                raise ValueError(
                    "Tool id is already registered with a different tool instance: "
                    f"tool_id='{tool.card.id}', tool_name='{tool.card.name}'"
                )

            tag_result = Runner.resource_mgr.add_resource_tag(tool.card.id, tag)
            if tag_result.is_err():
                raise tag_result.msg()
            continue

        result = Runner.resource_mgr.add_tool(tool, tag=tag)
        if result.is_err():
            raise result.msg()


def _inject_general_purpose_subagent(
    subagents: Optional[List[SubAgentConfig | DeepAgent]],
    *,
    add_general_purpose_agent: bool,
    resolved_language: str,
    rails: Optional[List[AgentRail]],
    system_prompt: Optional[str],
    tools: Optional[List[Tool | ToolCard]],
    mcps: Optional[List[McpServerConfig]],
    model: Model,
    skills: Optional[List[str]],
) -> list[SubAgentConfig | DeepAgent]:
    """Inject general-purpose subagent if requested and not already present."""
    effective_subagents = list(subagents or [])
    if not add_general_purpose_agent:
        return effective_subagents

    has_gp = any(
        (isinstance(s, SubAgentConfig) and s.agent_card.name == "general-purpose")
        or (isinstance(s, DeepAgent) and getattr(getattr(s, "card", None), "name", None) == "general-purpose")
        for s in effective_subagents
    )
    if has_gp:
        return effective_subagents

    desc = GENERAL_PURPOSE_AGENT_DESC.get(resolved_language, GENERAL_PURPOSE_AGENT_DESC["cn"])
    gp_rails = [
        r for r in (rails or [])
        if not isinstance(r, (SubagentRail, SessionRail))
    ] or None
    effective_subagents.insert(0, SubAgentConfig(
        agent_card=AgentCard(name="general-purpose", description=desc),
        system_prompt=system_prompt or "",
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        skills=skills,
        rails=gp_rails,
    ))
    return effective_subagents


def create_deep_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    enable_async_subagent: bool = False,
    add_general_purpose_agent: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    vision_model_config: Optional[VisionModelConfig] = None,
    audio_model_config: Optional[AudioModelConfig] = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    default_mode: AgentMode = AgentMode.AUTO,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a DeepAgent instance.

    This is the primary entry point for constructing a
    DeepAgent. The function is synchronous; rails are
    queued and async-registered on the first invoke().

    Args:
        model: Pre-constructed Model instance.
        card: Agent identity card. If None, a default
            card is created.
        system_prompt: System prompt for the inner
            ReActAgent.
        tools: Tool instances or tool cards to register on the agent.
        mcps: MCP server configs to register on the agent.
        subagents: Sub-agent specification or sub-agent instance,
            supports subagent using different model, tools and prompt.
        rails: AgentRail instances to register.
        enable_task_loop: Enable outer task loop (P1).
        enable_async_subagent: Enable async subagent via SessionRail (default False).
            When True and subagents are configured, SessionRail is mounted instead of SubagentRail.
        add_general_purpose_agent: Add general-purpose agent.
             When True, a general-purpose agent is added as sub-agents.
        max_iterations: Max ReAct iterations per
            invoke.
        workspace: Workspace path for file operations.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
        sys_operation: System operation.
        vision_model_config: Shared vision-model
            configuration injected into all vision
            tools registered by DeepAgent rails.
        audio_model_config: Shared audio-model
            configuration injected into all audio
            tools registered by DeepAgent rails.
        enable_task_planning: Enable task_planning_rail.
        restrict_to_work_dir: If True, restrict file access to workspace directory.
            If False, allow access to any path including system root.
        default_mode: Initial agent mode (``AgentMode.AUTO`` or ``AgentMode.PLAN``).
        **config_kwargs: Extra fields forwarded to
            DeepAgentConfig.

    Returns:
        Configured DeepAgent instance ready for
        invoke()/stream().
    """
    if card is None:
        card = AgentCard(
            name="deep_agent",
            description="DeepAgent instance",
        )

    normalized_tools, tool_instances = _normalize_tools(tools)

    resolved_language = resolve_language(language)

    effective_subagents = _inject_general_purpose_subagent(
        subagents,
        add_general_purpose_agent=add_general_purpose_agent,
        resolved_language=resolved_language,
        rails=rails,
        system_prompt=system_prompt,
        tools=tools,
        mcps=mcps,
        model=model,
        skills=skills,
    )

    if not workspace:
        workspace_obj = Workspace(root_path="./", language=resolved_language)
    elif isinstance(workspace, (str, PathLike)):
        workspace_obj = Workspace(root_path=str(workspace), language=resolved_language)
    else:
        workspace_obj = workspace

    if not isinstance(sys_operation, SysOperation):
        sysop_card = SysOperationCard(
                id=f"{card.name}_{card.id}",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(
                    restrict_to_sandbox=restrict_to_work_dir,
                ),
            )
        add_result = Runner.resource_mgr.add_sys_operation(sysop_card)
        if add_result.is_err():
            logger.error(f"add_sys_operation failed: {add_result.msg()}")
        sys_operation_obj = Runner.resource_mgr.get_sys_operation(f"{card.name}_{card.id}")
    else:
        sys_operation_obj = sys_operation

    config = DeepAgentConfig(
        model=model,
        card=card,
        system_prompt=system_prompt,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        subagents=effective_subagents or None,
        tools=normalized_tools or None,
        mcps=mcps,
        workspace=workspace_obj,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation_obj,
        language=resolved_language,
        prompt_mode=prompt_mode,
        vision_model_config=vision_model_config,
        audio_model_config=audio_model_config,
        enable_async_subagent=enable_async_subagent,
        add_general_purpose_agent=add_general_purpose_agent,
        default_mode=default_mode,
    )

    # Forward extra kwargs to config fields
    for key, value in config_kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            logger.warning(f"Unknown DeepAgentConfig field '{key}', ignored")

    agent = DeepAgent(card)
    agent.configure(config)

    if tool_instances:
        _register_tool_instances(tool_instances, tag=card.id)

    # Register tools on the shared ability manager
    if normalized_tools:
        for tool in normalized_tools:
            agent.ability_manager.add(tool)

    # Queue rails for lazy async registration
    if rails:
        for rail_inst in rails:
            agent.add_rail(rail_inst)

    # Auto-add default rails that the caller did not explicitly provide.
    # Each entry: (RailClass, should_add, make_rail)
    user_provided_rail_types = {type(r) for r in rails} if rails else set()

    def _already_provided(rail_cls: type) -> bool:
        return any(issubclass(t, rail_cls) for t in user_provided_rail_types)

    def _make_skill_rail() -> SkillUseRail:
        skills_base = workspace_obj.get_node_path("skills")
        skills_dir = [str(skills_base / s) for s in (skills or [])] if skills_base else []
        return SkillUseRail(skills_dir=skills_dir, skill_mode="all")

    default_rails = [
        (SecurityRail, True, lambda: SecurityRail()),
        (TaskPlanningRail, enable_task_planning, lambda: TaskPlanningRail()),
        (SkillUseRail, bool(skills), _make_skill_rail),
        (SessionRail, bool(subagents) and enable_async_subagent, lambda: SessionRail()),
        (SubagentRail, bool(subagents) and not enable_async_subagent, lambda: SubagentRail()),
    ]
    for rail_cls, should_add, make_rail in default_rails:
        if should_add and not _already_provided(rail_cls):
            agent.add_rail(make_rail())
    return agent
