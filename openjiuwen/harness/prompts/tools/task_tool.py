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

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# General-purpose subagent description (bilingual) - used in available_agents list
# ---------------------------------------------------------------------------
GENERAL_PURPOSE_AGENT_DESC: Dict[str, str] = {
    "cn": "通用型子代理，继承主代理的工具与能力（文件读写、bash、MCP、skills 等），"
          "在独立上下文中执行被委派的子任务；中间工具调用结果不进入主代理上下文。",
    "en": "General-purpose subagent that inherits the parent agent's tools and "
          "capabilities (file I/O, bash, MCP, skills, etc.) and runs delegated "
          "subtasks in an isolated context; intermediate tool results do not "
          "enter the parent agent's context.",
}

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = (
    "Synchronously delegate to a listed subagent and wait for its result. A fresh subagent has no "
    "conversation history. Give the objective, context, constraints, findings and expected output. "
    "Verify and synthesize the result. Follow system routing, evidence and recovery rules.\n"
    "Browser interaction uses browser_agent. Supply browser_capabilities ([] for core only); "
    "omit it for other agents. Check the summary, evidence and partial results before resuming. "
    "Reuse resume_task_id for the same incomplete browser task or a targeted correction, within "
    "the original deadline and at most once. For a preserved cua_agent session after timeout "
    "with retryable=true, reuse its ID. Describe only the unfinished work when resuming. "
    "Omit resume_task_id for new work.\n\nAvailable types and capabilities:\n{available_agents}"
)

TASK_TOOL_DESCRIPTION_CN = (
    "同步委派给列表中的子代理并等待结果。新子代理没有对话历史。"
    "写明目标、上下文、约束、已有发现和期望输出。核验并综合结果，遵循系统的选路、证据和恢复规则。\n"
    "实际浏览器操作使用 browser_agent。传入 browser_capabilities（仅用核心能力时传 []），"
    "其他代理省略此参数。续跑前先检查摘要、证据和部分结果。"
    "继续同一个未完成的浏览器任务或定向纠正结果时，使用原 resume_task_id，"
    "在原期限内最多续跑一次。cua_agent 超时且 retryable=true、会话已保留时，使用原 ID。"
    "续跑只描述未完成的部分，新任务省略 resume_task_id。\n\n"
    "可用代理类型及能力：\n{available_agents}"
)

DESCRIPTION: Dict[str, str] = {
    "cn": TASK_TOOL_DESCRIPTION_CN,
    "en": TASK_TOOL_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Tool parameters (bilingual)
# ---------------------------------------------------------------------------
TASK_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "subagent_type": {
        "cn": "当前可用代理列表中的准确类型名。",
        "en": "Exact type name from the current list.",
    },
    "task_description": {
        "cn": "目标、上下文、约束、已有发现和期望输出。",
        "en": "Objective, context, constraints, findings and expected output.",
    },
    "browser_capabilities": {
        "cn": "仅 browser_agent 使用。填能力类别，仅用核心能力时传 []。",
        "en": "Capability categories for browser_agent only. Use [] for core only.",
    },
    "resume_task_id": {
        "cn": "同一浏览器或桌面任务符合续跑条件时传入原 ID。新任务省略。",
        "en": "Original ID for an eligible resume of the same browser or desktop task. Omit for new work.",
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
            "browser_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": p["browser_capabilities"].get(language, p["browser_capabilities"]["cn"]),
            },
            "resume_task_id": {
                "type": "string",
                "description": p["resume_task_id"].get(language, p["resume_task_id"]["cn"]),
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
