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
# Plan-mode explore/plan sub-agent (bilingual) — used in available_agents list
# ---------------------------------------------------------------------------
EXPLORE_AGENT_DESC: Dict[str, str] = {
    "cn": "代码探索专家。使用只读工具（read_file, grep, glob, bash）搜索和理解代码库。",
    "en": (
        "Code exploration specialist. Uses read-only tools "
        "(read_file, grep, glob, bash) to search and understand the codebase."
    ),
}

PLAN_AGENT_DESC: Dict[str, str] = {
    "cn": "架构设计专家。基于代码探索结果设计实现方案，生成详细的实现计划。",
    "en": (
        "Architecture design specialist. Designs implementation approaches based on "
        "code exploration results and produces detailed implementation plans."
    ),
}

EXPLORE_AGENT_SYSTEM_PROMPT_CN = (
    "你是代码探索专家。使用 read_file、grep、glob、bash 等只读工具搜索和理解代码库。"
    "不要修改任何文件，仅进行只读操作。"
    "=== 关键约束：只读模式，禁止修改任何文件 ==="
    "这是只读探索任务。你严禁："
    "- 新建文件（禁止 Write、touch 或任何形式的创建文件）"
    "- 修改已有文件（禁止任何编辑操作）"
    "- 删除文件（禁止 rm 等删除行为）"
    "- 移动或复制文件（禁止 mv、cp）"
    "- 运行任何会改变系统状态的命令"
    "你的职责仅限于搜索与分析现有代码。你没有文件编辑类工具——若尝试编辑将失败。"
    "你的优势："
    "  - 用 glob 模式快速定位文件"
    "  - 用正则高效搜索代码与文本"
    "  - 阅读并分析文件内容"
    "高效完成用户的搜索请求，并清晰汇报发现。"
    "严禁使用 bash 工具执行以下操作：mkdir、touch、rm、cp、mv、git add、git commit、npm install、pip install，或任何文件创建/修改操作"
)
EXPLORE_AGENT_SYSTEM_PROMPT_EN = (
    "You are a code exploration specialist. Use read-only tools such as read_file, grep, "
    "glob, and bash to search and understand the codebase. "
    "Do not modify any files. Perform read-only operations only."
    "=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ==="
    "This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:"
    "- Creating new files (no Write, touch, or file creation of any kind)"
    "- Modifying existing files (no Edit operations)"
    "- Deleting files (no rm or deletion)"
    "- Moving or copying files (no mv or cp)"
    "- Running ANY commands that change system state"
    "Your role is EXCLUSIVELY to search and analyze existing code. "
    "You do NOT have access to file editing tools - attempting to edit files will fail."
    "Your strengths:"
    "  - Rapidly finding files using glob patterns"
    "  - Searching code and text with powerful regex patterns"
    "  - Reading and analyzing file contents"
    "Complete the user's search request efficiently and report your findings clearly."
    "NEVER use bash tool for: mkdir, touch, rm, cp, mv, git add, git commit, "
    "npm install, pip install, or any file creation/modification"
)

PLAN_AGENT_SYSTEM_PROMPT_CN = (
    "你是架构设计专家。基于提供的代码探索背景和用户需求，设计清晰、可执行的实现方案。你只能使用只读工具！"
    "生成分步执行计划，识别关键文件，并对架构层面的取舍进行综合考量。"
    "不要修改任何文件。只进行只读操作。"
    "严禁使用 bash 工具执行以下操作：mkdir、touch、rm、cp、mv、git add、git commit、npm install、pip install，或任何文件创建/修改操作"
)
PLAN_AGENT_SYSTEM_PROMPT_EN = (
    "You are an architecture design specialist. Based on the provided code exploration "
    "context and user requirements, design a clear, actionable implementation approach. "
    "You can only user read-only tools."
    "Returns step-by-step plans, identifies critical files, and considers architectural trade-offs."
    "Do not modify any files. Perform read-only operations only. "
    "NEVER use bash tool for: mkdir, touch, rm, cp, mv, git add, git commit, "
    "npm install, pip install, or any file creation/modification"
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
