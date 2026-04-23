# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory function for creating CodeAgent instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config
from openjiuwen.harness.rails.coding_memory_rail import CodingMemoryRail
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.agent_mode_rail import AgentModeRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config
from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode

CODE_AGENT_FACTORY_NAME = "code_agent"


DEFAULT_CODE_AGENT_SYSTEM_PROMPT_EN = (
    "You are an AI Coding Agent. "
    "Rules: Use tools whenever possible (read/write/edit/grep/list/bash/code), don't guess file contents;"
    "make small, reversible changes; clarify data structures and interfaces before modifying code; "
    "provide testing/verification steps in your output."
)

DEFAULT_CODE_AGENT_SYSTEM_PROMPT_CN = (
    "你是一个 AI 编程助手，规则：能用工具就用工具（读/写/编辑/grep/list/bash/code），不要猜文件内容；变更要小、可回滚；"
    "先澄清数据结构与接口，再动代码；输出给出测试/验证步骤。"
)

DEFAULT_CODE_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": DEFAULT_CODE_AGENT_SYSTEM_PROMPT_CN,
    "en": DEFAULT_CODE_AGENT_SYSTEM_PROMPT_EN,
}

DEFAULT_CODE_AGENT_DESCRIPTION_EN = """You are a senior software engineer and coding agent, 
    excel at translating tasks into runnable code and verifiable results."""

DEFAULT_CODE_AGENT_DESCRIPTION_CN = "资深软件工程师与代码代理。擅长把任务落到可运行的代码与可验证的结果。"

DEFAULT_CODE_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_CODE_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_CODE_AGENT_DESCRIPTION_EN,
}


def _resolve_coding_memory_dir(workspace: Optional[str | Workspace]) -> str:
    """Resolve the coding_memory directory path from the workspace."""
    if isinstance(workspace, Workspace):
        node_path = workspace.get_node_path(WorkspaceNode.CODING_MEMORY)
        if node_path is not None:
            return str(node_path)
        return str(Path(workspace.root_path) / "coding_memory")
    root = workspace if isinstance(workspace, str) else "./"
    return str(Path(root) / "coding_memory")


def _has_agent(subagents: list[SubAgentConfig | DeepAgent], name: str) -> bool:
    """Check whether a named sub-agent already exists."""
    for spec in subagents:
        if isinstance(spec, SubAgentConfig):
            if spec.agent_card.name == name:
                return True
        else:
            card = getattr(spec, "card", None)
            if getattr(card, "name", None) == name:
                return True
    return False


def _inject_builtin_plan_agents(
    subagents: list[SubAgentConfig | DeepAgent],
    *,
    resolved_language: str,
    model: Model,
) -> list[SubAgentConfig | DeepAgent]:
    """Inject explore and plan builtin sub-agents if missing."""
    effective = list(subagents)
    if not _has_agent(effective, "explore_agent"):
        effective.append(
            build_explore_agent_config(
                model=model,
                language=resolved_language,
                max_iterations=25,
            )
        )
    if not _has_agent(effective, "plan_agent"):
        effective.append(
            build_plan_agent_config(
                model=model,
                language=resolved_language,
                max_iterations=25,
            )
        )
    return effective


def _merge_rails_with_required(
    user_rails: Optional[List[AgentRail]],
    required_rails: Sequence[Tuple[type[AgentRail], Callable[[], AgentRail]]],
) -> List[AgentRail]:
    """Merge user rails with required rails, deduplicating by rail class."""
    merged = list(user_rails or [])
    for rail_cls, rail_factory in required_rails:
        if not any(isinstance(rail, rail_cls) for rail in merged):
            merged.append(rail_factory())
    return merged


def build_code_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    embedding_config: Optional[EmbeddingConfig] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as create_code_agent()."""
    resolved_language = resolve_language(language)
    factory_kwargs: Dict[str, Any] = {}
    if embedding_config is not None:
        factory_kwargs["embedding_config"] = embedding_config
    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="code_agent",
            description=DEFAULT_CODE_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_CODE_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt or DEFAULT_CODE_AGENT_SYSTEM_PROMPT.get(
            resolved_language,
            DEFAULT_CODE_AGENT_SYSTEM_PROMPT["cn"],
        ),
        tools=tools,
        mcps=mcps,
        model=model,
        rails=rails,
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        factory_name=CODE_AGENT_FACTORY_NAME,
        factory_kwargs=factory_kwargs,
    )


def create_code_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    embedding_config: Optional[EmbeddingConfig] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a predefined CodeAgent instance.

    predefined CodeAgent is equipped with CodeTool and FileSystemRail. You are free to override the configuration.

    Args:
        model: Pre-constructed Model instance.
        card: Agent identity card. If None, a default
            card is created.
        system_prompt: System prompt for the inner
            ReActAgent.
        tools: Tool instances or tool cards to register on the agent.
        mcps: MCP server configs to register on the agent.
        subagents: Sub-agent specification, supports subagent using different model, tools and prompt.
        rails: AgentRail instances to register.
        enable_task_loop: Enable outer task loop.
        max_iterations: Max ReAct iterations per
            invoke.
        workspace: Workspace path for file operations.
        skills: Skill definitions.
        backend: Backend protocol instance .
        sys_operation: System operation.
        **config_kwargs: Extra fields forwarded to
            DeepAgentConfig.

    Returns:
        Configured CodeAgent instance ready for
        invoke()/stream().
    """
    resolved_language = resolve_language(language)

    final_card = card or AgentCard(
        name="code_agent",
        description=DEFAULT_CODE_AGENT_DESCRIPTION.get(resolved_language, DEFAULT_CODE_AGENT_DESCRIPTION["cn"]),
    )
    final_prompt = system_prompt or DEFAULT_CODE_AGENT_SYSTEM_PROMPT.get(
        resolved_language, DEFAULT_CODE_AGENT_SYSTEM_PROMPT["cn"]
    )

    # Plan-mode composition now belongs to code_agent (not deep_agent).
    effective_subagents = _inject_builtin_plan_agents(
        list(subagents or []),
        resolved_language=resolved_language,
        model=model,
    )

    final_rails = _merge_rails_with_required(
        rails,
        [
            (FileSystemRail, FileSystemRail),
            (AgentModeRail, AgentModeRail),
            (AskUserRail, AskUserRail),
        ],
    )

    # --- CodingMemoryRail ---
    if embedding_config is not None and not any(isinstance(r, CodingMemoryRail) for r in final_rails):
        coding_memory_dir = _resolve_coding_memory_dir(workspace)
        final_rails.append(
            CodingMemoryRail(
                coding_memory_dir=coding_memory_dir,
                embedding_config=embedding_config,
                language=resolved_language,
            )
        )

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=tools,
        mcps=mcps,
        subagents=effective_subagents,
        rails=final_rails,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_planning=True,
        **config_kwargs,
    )


__all__ = [
    "CODE_AGENT_FACTORY_NAME",
    "DEFAULT_CODE_AGENT_DESCRIPTION",
    "DEFAULT_CODE_AGENT_SYSTEM_PROMPT",
    "build_code_agent_config",
    "create_code_agent",
]
