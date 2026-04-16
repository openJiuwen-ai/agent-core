# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool metadata for tool registration.

This module provides ONLY the tool registration metadata:
- Tool name
- Tool description template (with {available_agents} placeholder)
- Tool input parameters schema

For other system prompt sections, see ``sections/task_tool.py`` (non-tool prompts).
"""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# General-purpose agent description (bilingual) - used in available_agents list
# ---------------------------------------------------------------------------
GENERAL_PURPOSE_AGENT_DESC: Dict[str, str] = {
    "cn": "用于研究复杂问题、搜索文件与内容、执行多步骤任务。该智能体拥有与主代理完全相同的全部工具权限。"
          "适合用于隔离上下文与 Token 消耗，并完成特定的复杂任务，因为它拥有与主代理完全相同的全部能力。",
    "en": "General-purpose agent for researching complex questions, searching for files and content, "
    "and executing multi-step tasks. This agent has access to all tools as the main agent. "
    "The general-purpose agent is suitable for isolating context and token consumption, "
    "and completing specific complex tasks",
}

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
    "\n\n输出要求：在回答末尾必须给出“Critical Files for Implementation”，列出 3-5 个最关键文件路径。"
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

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = (
    "Launch an ephemeral subagent to handle complex, multi-step independent tasks "
    "with isolated context windows.\n\n"
    "Available agent types and the tools they have access to:\n"
    "{available_agents}\n\n"
    "Important: When using the Task tool, you must specify the subagent_type and "
    "task_description parameters to select the agent type and describe the task. "
    "Do not specify agents you do not have access to!\n"
)

TASK_TOOL_DESCRIPTION_CN = """启动临时子代理，处理复杂、多步骤、独立的隔离上下文任务。

可用代理类型及对应工具：
{available_agents}

重要：使用 Task 工具时，必须指定 subagent_type, task_description 参数选择代理类型和描述任务。请勿指定你无权访问的其他代理！！！
"""

DESCRIPTION: Dict[str, str] = {
    "cn": TASK_TOOL_DESCRIPTION_CN,
    "en": TASK_TOOL_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Tool parameters (bilingual)
# ---------------------------------------------------------------------------
TASK_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "subagent_type": {
        "cn": "子代理类型",
        "en": "Type of subagent to use",
    },
    "task_description": {
        "cn": "任务描述",
        "en": "Task description",
    },
}


def get_task_tool_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for task tool input_params.

    Args:
        language: 'cn' or 'en'.

    Returns:
        JSON Schema dict for tool parameters.
    """
    p = TASK_TOOL_PARAMS
    return {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": p["subagent_type"].get(language, p["subagent_type"]["cn"]),
            },
            "task_description": {
                "type": "string",
                "description": p["task_description"].get(language, p["task_description"]["cn"]),
            },
        },
        "required": ["subagent_type", "task_description"],
    }


class TaskMetadataProvider(ToolMetadataProvider):
    """Task tool metadata provider for tool registration.

    Provides tool name, description template, and parameter schema.
    Does NOT provide system prompt content.
    """

    def get_name(self) -> str:
        """Return tool name."""
        return "task_tool"

    def get_description(self, language: str = "cn") -> str:
        """Return tool description template with {available_agents} placeholder.

        Args:
            language: 'cn' or 'en'.

        Returns:
            Tool description template string.
        """
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        """Return JSON Schema for tool input parameters.

        Args:
            language: 'cn' or 'en'.

        Returns:
            JSON Schema dict for tool parameters.
        """
        return get_task_tool_input_params(language)
