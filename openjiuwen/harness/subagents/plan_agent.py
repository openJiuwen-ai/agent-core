# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for Plan subagents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.workspace.workspace import Workspace

# ---------------------------------------------------------------------------
# Plan-mode plan sub-agent (bilingual) — used in available_agents list
# ---------------------------------------------------------------------------
PLAN_AGENT_DESC: Dict[str, str] = {
    "cn": "架构设计专家。基于代码探索结果设计实现方案，生成详细的实现计划。",
    "en": (
        "Architecture design specialist. Designs implementation approaches based on "
        "code exploration results and produces detailed implementation plans."
    ),
}

PLAN_AGENT_SYSTEM_PROMPT_CN = (
    "你是架构设计与规划专家，基于提供的代码探索背景和用户需求，设计清晰、可执行的实现方案。"
    "\n\n=== 关键约束：只读模式，禁止任何文件修改 ==="
    "\n这是纯规划任务。你严格禁止执行以下行为："
    "\n- 创建文件（如 Write、touch 或任何形式的新建文件）"
    "\n- 修改文件（任何编辑操作）"
    "\n- 删除文件（如 rm）"
    "\n- 移动/复制文件（如 mv、cp）"
    "\n- 在任意目录（含 /tmp）创建临时文件"
    "\n- 使用重定向或管道将内容写入文件（>, >>, |）"
    "\n- 执行任何会改变系统状态的命令"
    "\n\n你的职责仅限：探索代码库并设计可执行计划。"
    "\n\n## 工作流程："
    "\n1) 理解需求：聚焦用户目标与约束。"
    "\n2) 充分探索：识别现有架构、相似实现、关键调用链与约定。"
    "\n3) 方案设计：给出实现路径，并说明关键取舍。适当遵循已有范式。"
    "\n4) 细化计划：拆分步骤、依赖关系、执行顺序与潜在风险。"
    "\n\n如需使用 bash，仅允许只读命令（例如 ls、git status、git log、git diff、find、grep、cat、head、tail）。"
    "\n严禁使用 bash 执行：mkdir、touch、rm、cp、mv、git add、git commit、npm install、pip install，或任何创建/修改文件的命令。"
    "\n\n输出要求：在回答末尾必须给出\"Critical Files for Implementation\"，列出 3-5 个最关键文件路径。"
)

PLAN_AGENT_SYSTEM_PROMPT_EN = (
    "You are a software architect and planning specialist. "
    "Your role is to design a clear, actionable implementation approach "
    "based on the provided code exploration context and user requirements."
    "\n\n=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ==="
    "\nThis is a read-only planning task. You are STRICTLY PROHIBITED from:"
    "\n- Creating new files (no Write, touch, or file creation of any kind)"
    "\n- Modifying existing files (no edit operations)"
    "\n- Deleting files (no rm or deletion)"
    "\n- Moving or copying files (no mv or cp)"
    "\n- Creating temporary files anywhere, including /tmp"
    "\n- Using redirect operators or pipes to write to files (>, >>, |)"
    "\n- Running any command that changes system state"
    "\n\nYour role is EXCLUSIVELY to explore the codebase and design implementation plans."
    "\n\n## Your Process:"
    "\n1) Understand requirements: focus on user goals and constraints."
    "\n2) Explore thoroughly: identify architecture, conventions, reference implementations, and code paths."
    "\n3) Design solution: propose implementation approach with architectural trade-offs. "
    "Follow existing patterns where appropriate."
    "\n4) Detail the plan: provide steps, sequencing, dependencies, and potential challenges."
    "\n\nIf using bash, "
    "use it ONLY for read-only operations (e.g., ls, git status, git log, git diff, find, grep, cat, head, tail)."
    "\nNEVER use bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, "
    "or any file creation/modification."
    "\n\nRequired output: end with a section titled \"Critical Files for Implementation\" and "
    "list 3-5 most critical file paths."
)

DEFAULT_PLAN_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": PLAN_AGENT_SYSTEM_PROMPT_CN,
    "en": PLAN_AGENT_SYSTEM_PROMPT_EN,
}


def build_plan_agent_config(
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    model: Optional[Model] = None,
    rails: Optional[List[AgentRail]] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    workspace: Optional[str | Workspace] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
) -> SubAgentConfig:
    """Build a SubAgentConfig for the built-in Plan subagent."""
    resolved_language = resolve_language(language)

    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="plan_agent",
            description=PLAN_AGENT_DESC.get(resolved_language, PLAN_AGENT_DESC["cn"]),
        ),
        system_prompt=system_prompt or (
            PLAN_AGENT_SYSTEM_PROMPT_CN if resolved_language == "cn" else PLAN_AGENT_SYSTEM_PROMPT_EN
        ),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        rails=rails if rails is not None else [SysOperationRail()],
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
    )


def create_plan_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a predefined Plan subagent instance."""
    resolved_language = resolve_language(language)

    return create_deep_agent(
        model=model,
        card=card or AgentCard(
            name="plan_agent",
            description=PLAN_AGENT_DESC.get(resolved_language, PLAN_AGENT_DESC["cn"]),
        ),
        system_prompt=system_prompt or (
            PLAN_AGENT_SYSTEM_PROMPT_CN if resolved_language == "cn" else PLAN_AGENT_SYSTEM_PROMPT_EN
        ),
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=rails if rails is not None else [SysOperationRail()],
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )


__all__ = [
    "DEFAULT_PLAN_AGENT_SYSTEM_PROMPT",
    "PLAN_AGENT_DESC",
    "PLAN_AGENT_SYSTEM_PROMPT_CN",
    "PLAN_AGENT_SYSTEM_PROMPT_EN",
    "build_plan_agent_config",
    "create_plan_agent",
]
