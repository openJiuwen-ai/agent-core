# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Factory function for creating DeepAgent instances."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Dict
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
    LLMRetryRail,
    SecurityRail,
    SkillUseRail,
    SubagentRail,
    TaskPlanningRail,
)
from openjiuwen.harness.rails import SysOperationRail
from openjiuwen.harness.schema.agent_mode import AgentMode
from openjiuwen.harness.schema.config import (
    AudioModelConfig,
    DeepAgentConfig,
    SubAgentConfig,
    VisionModelConfig,
    is_vision_model_config_complete,
)
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.prompts.tools.task_tool import GENERAL_PURPOSE_AGENT_DESC
from openjiuwen.harness.tools import create_vision_tools, is_free_search_enabled

import logging
browser_agent_logger = logging.getLogger("openjiuwen.browser_agent")

def _collect_disabled_skills_from_state(skills_dirs: list[str]) -> list[str]:
    """Read skills_state.json from each skills_dir and collect disabled skill names."""
    disabled: set[str] = set()
    for skills_dir in skills_dirs:
        state_path = Path(skills_dir) / "skills_state.json"
        if not state_path.is_file():
            continue
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read skills_state.json at %s", state_path)
            continue
        skill_configs = data.get("skill_configs", {})
        for name, cfg in skill_configs.items():
            if isinstance(cfg, dict) and cfg.get("enabled") is False:
                disabled.add(name)
    return sorted(disabled)


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
    gp_rails = [r for r in (rails or []) if not isinstance(r, SubagentRail)]
    if not any(isinstance(r, SysOperationRail) for r in gp_rails):
        gp_rails = [SysOperationRail(), *gp_rails]
    gp_rails = gp_rails or None
    effective_subagents.insert(0, SubAgentConfig(
        agent_card=AgentCard(name="general-purpose", description=desc),
        system_prompt=system_prompt or "",
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        skills=skills,
        rails=gp_rails,
        restrict_to_work_dir=False,
    ))
    return effective_subagents


@dataclass
class DeepAgentParts:
    """Assembled DeepAgent construction inputs, decoupled from any instance.

    ``resolve_deep_agent_parts`` produces these from raw inputs (pure assembly,
    no DeepAgent created); ``apply_deep_agent_parts`` materializes them onto a
    target DeepAgent. This lets a ``NativeHarness`` configure itself directly
    from a spec instead of building a throwaway template DeepAgent and copying
    its config + rails across.

    Attributes:
        config: The assembled ``DeepAgentConfig`` (rails not embedded here;
            they travel in ``rails`` and are applied via ``add_rail``).
        rails: User-supplied rails followed by the auto-added default rails,
            in mount order.
        tool_cards: Tool cards to register on the agent's ability manager.
        tool_instances: Concrete tool instances to register on the shared
            resource manager so their cards become executable.
    """

    config: DeepAgentConfig
    rails: List[AgentRail]
    tool_cards: List[ToolCard]
    tool_instances: List[Tool]


def resolve_deep_agent_parts(
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
    enable_read_image_multimodal: Optional[bool] = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    default_mode: AgentMode = AgentMode.NORMAL,
    model_selection: Optional[Dict[Model, str]] = None,
    parallel_tool_calls: bool = True,
    enable_llm_retry_rail: bool = True,
    **config_kwargs: Any,
) -> DeepAgentParts:
    """Assemble DeepAgent config + rails + tools without creating an instance.

    The pure-assembly half of :func:`create_deep_agent`: normalizes tools,
    injects the optional general-purpose sub-agent, resolves workspace /
    sys_operation, builds the ``DeepAgentConfig``, and computes the auto-added
    default rails (Security / TaskPlanning / SkillUse / Subagent) that the
    caller did not already provide. Returns a :class:`DeepAgentParts` that
    :func:`apply_deep_agent_parts` materializes onto a target agent.
    """
    if card is None:
        card = AgentCard(
            name="deep_agent",
            description="DeepAgent instance",
        )

    normalized_tools, tool_instances = _normalize_tools(tools)

    resolved_language = resolve_language(language)
    vision_tools_enabled = is_vision_model_config_complete(vision_model_config)
    if vision_tools_enabled:
        existing_tool_names = {card.name for card in normalized_tools}
        for tool in create_vision_tools(
            language=resolved_language,
            vision_model_config=vision_model_config,
            agent_id=card.id,
        ):
            if tool.card.name in existing_tool_names:
                continue
            tool_instances.append(tool)
            normalized_tools.append(tool.card)
            existing_tool_names.add(tool.card.name)

    effective_enable_read_image_multimodal = (
        False if vision_tools_enabled else enable_read_image_multimodal
    )

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
        sysop_id = f"{card.name}_{card.id}"
        # Get-or-create: the id is stable across rebuilds (a member harness is
        # reconstructed on every team resume), and add_sys_operation is a strict
        # add that errors on a duplicate id. Resolve the existing instance first
        # so a rebuild does not log a spurious "resource already exist" error.
        sys_operation_obj = Runner.resource_mgr.get_sys_operation(sysop_id)
        if sys_operation_obj is None:
            sysop_card = SysOperationCard(
                id=sysop_id,
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(
                    shell_allowlist=None,
                    restrict_to_sandbox=restrict_to_work_dir,
                ),
            )
            add_result = Runner.resource_mgr.add_sys_operation(sysop_card)
            if add_result.is_err():
                logger.error(f"add_sys_operation failed: {add_result.msg()}")
            sys_operation_obj = Runner.resource_mgr.get_sys_operation(sysop_id)
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
        enable_read_image_multimodal=effective_enable_read_image_multimodal,
        enable_async_subagent=enable_async_subagent,
        add_general_purpose_agent=add_general_purpose_agent,
        default_mode=default_mode,
        parallel_tool_calls=parallel_tool_calls,
        restrict_to_work_dir=restrict_to_work_dir,
    )

    # Forward extra kwargs to config fields
    for key, value in config_kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            logger.warning(f"Unknown DeepAgentConfig field '{key}', ignored")

    all_rails: List[AgentRail] = list(rails or [])

    # Auto-add default rails that the caller did not explicitly provide.
    # Each entry: (RailClass, should_add, make_rail)
    user_provided_rail_types = {type(r) for r in rails} if rails else set()

    def _already_provided(rail_cls: type) -> bool:
        return any(issubclass(t, rail_cls) for t in user_provided_rail_types)

    def _make_skill_rail() -> SkillUseRail:
        skills_dirs: list[str] = []
        skills_base = workspace_obj.get_node_path("skills")
        if skills_base:
            skills_dirs.append(str(skills_base))
        # Aggregate skills from each team workspace mounted under
        # ``.team/{team_id}``; the team mount is a symlink to the shared
        # workspace root, so the team-shared skills live at
        # ``{target}/skills``. Paths are added even when they do not yet
        # exist — SkillUseRail skips missing directories at refresh time.
        for _team_id, target_path in workspace_obj.list_team_links():
            skills_dirs.append(str(Path(target_path) / "skills"))
        disabled_skills = _collect_disabled_skills_from_state(skills_dirs)
        # ``include_tools`` registers read_file / code / bash so skills can do
        # file/shell ops. When a SysOperationRail is already mounted it owns
        # those tools (and refresh-binds them to the live sys_operation), so
        # re-registering here only double-registers over its ids — every build
        # then logs a refresh + duplicate-ability warning per overlapping tool.
        # Defer to SysOperationRail when present; only include the fallback set
        # when no fs rail provides them.
        include_tools = not _already_provided(SysOperationRail)
        return SkillUseRail(
            skills_dir=skills_dirs,
            skill_mode="all",
            disabled_skills=disabled_skills or None,
            include_tools=include_tools,
        )

    def _make_task_planning_rail() -> TaskPlanningRail:
        return TaskPlanningRail(model_selection=model_selection)

    default_rails = [
        (SecurityRail, True, lambda: SecurityRail()),
        (LLMRetryRail, enable_llm_retry_rail, lambda: LLMRetryRail()),
        (TaskPlanningRail, enable_task_planning, _make_task_planning_rail),
        (SkillUseRail, bool(skills) or config.enable_skill_discovery, _make_skill_rail),
        (SubagentRail, bool(effective_subagents),
         lambda: SubagentRail(enable_async_subagent=enable_async_subagent)),
    ]
    for rail_cls, should_add, make_rail in default_rails:
        if should_add and not _already_provided(rail_cls):
            all_rails.append(make_rail())

    return DeepAgentParts(
        config=config,
        rails=all_rails,
        tool_cards=normalized_tools,
        tool_instances=tool_instances,
    )


def apply_deep_agent_parts(agent: DeepAgent, parts: DeepAgentParts) -> None:
    """Apply resolved :class:`DeepAgentParts` onto a target DeepAgent.

    Configures the agent from ``parts.config`` (rebuilding its inner
    ReActAgent), registers concrete tool instances on the shared resource
    manager, adds tool cards to the ability manager, and queues all rails for
    lazy async init. The target may be a fresh ``DeepAgent`` (the
    :func:`create_deep_agent` path) or a ``NativeHarness`` configuring itself
    (forward construction, no throwaway template).
    """
    agent.configure(parts.config)

    # Register concrete tool instances through the ability manager so the card
    # id and the resource-manager key stay consistent and get agent-qualified
    # (stateful) or shared by bare id (stateless). The instance cards are the
    # same objects carried in ``parts.tool_cards`` (see ``_normalize_tools``),
    # so track them to avoid double-adding below.
    instance_card_ids: set[int] = set()
    if parts.tool_instances:
        for tool in parts.tool_instances:
            agent.ability_manager.add_ability(tool.card, tool)
            if agent.card.name == "browser_agent":
                browser_agent_logger.info(f"Browser Agent Tool Instance: {tool.card.name}")
            if tool.card is not None:
                instance_card_ids.add(id(tool.card))

    # Register the remaining pure ToolCards (references to globally-registered
    # tools) on the shared ability manager; instance cards are already added.
    if parts.tool_cards:
        for tool_card in parts.tool_cards:
            if id(tool_card) in instance_card_ids:
                continue
            agent.ability_manager.add(tool_card)

    # Queue rails for lazy async registration (user rails + default rails)
    for rail_inst in parts.rails:
        if agent.card.name == "browser_agent":
            browser_agent_logger.info(f"Browser Agent Rail: {type(rail_inst).__name__}")
        agent.add_rail(rail_inst)


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
    enable_read_image_multimodal: Optional[bool] = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    default_mode: AgentMode = AgentMode.NORMAL,
    model_selection: Optional[Dict[Model, str]] = None,
    parallel_tool_calls: bool = True,
    enable_llm_retry_rail: bool = True,
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
        enable_async_subagent: Enable async subagent mode (default False).
            When True and subagents are configured, SubagentRail registers session tools for async subagent spawning;
            When False, it registers synchronous task tools.
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
        enable_read_image_multimodal: Controls read_file native image attachment.
            None (default): probe the agent model once during lazy init.
            True: always attach image bytes as multimodal input.
            False: return image metadata only and suggest vision tools.
        enable_task_planning: Enable task_planning_rail.
        restrict_to_work_dir: If True, restrict file access to workspace directory.
            If False, allow access to any path including system root.
        default_mode: Initial agent mode (``AgentMode.NORMAL`` or ``AgentMode.PLAN``).
        enable_llm_retry_rail: Enable default LLMRetryRail for stream frame timeout and repeated-output retries.
        model_selection: Optional model selection config for TaskPlanningRail.
            Dict mapping Model instance to description string. When provided along with
            enable_task_planning, TaskPlanningRail will be configured with model selection,
            allowing different models to be used for different subtasks.
        **config_kwargs: Extra fields forwarded to
            DeepAgentConfig.

    Returns:
        Configured DeepAgent instance ready for
        invoke()/stream().
    """
    parts = resolve_deep_agent_parts(
        model,
        card=card,
        system_prompt=system_prompt,
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=rails,
        enable_task_loop=enable_task_loop,
        enable_async_subagent=enable_async_subagent,
        add_general_purpose_agent=add_general_purpose_agent,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=language,
        prompt_mode=prompt_mode,
        vision_model_config=vision_model_config,
        audio_model_config=audio_model_config,
        enable_read_image_multimodal=enable_read_image_multimodal,
        enable_task_planning=enable_task_planning,
        restrict_to_work_dir=restrict_to_work_dir,
        default_mode=default_mode,
        model_selection=model_selection,
        parallel_tool_calls=parallel_tool_calls,
        enable_llm_retry_rail=enable_llm_retry_rail,
        **config_kwargs,
    )
    agent = DeepAgent(parts.config.card)
    apply_deep_agent_parts(agent, parts)
    return agent
