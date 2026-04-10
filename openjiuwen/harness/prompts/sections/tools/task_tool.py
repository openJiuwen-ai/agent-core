# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool metadata for tool registration.

This module provides ONLY the tool registration metadata:
- Tool name
- Tool description template (with {available_agents} placeholder)
- Tool input parameters schema

It does NOT contain system prompt sections.
For system prompt sections, see sections/task_tool.py
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
