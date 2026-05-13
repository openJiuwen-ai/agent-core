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
# General-purpose agent description (bilingual) - used in available_agents list
# ---------------------------------------------------------------------------
GENERAL_PURPOSE_AGENT_DESC: Dict[str, str] = {
    "cn": "通用型子代理，适合执行独立的复杂子任务。"
          "子代理运行在独立的上下文窗口中，其中间工具调用结果不会污染主代理的上下文，"
          "从而大幅节省主代理的 Token 消耗，保持主上下文窗口的清晰与高效。",
    "en": "General-purpose subagent for executing independent complex subtasks. "
    "The subagent runs in an isolated context window — intermediate tool call results "
    "never enter the main agent's context, significantly reducing token consumption "
    "and keeping the main context window clean and focused.",
}

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = """\
Launch an ephemeral subagent to handle complex, multi-step independent tasks \
with isolated context windows.

## WHEN TO USE

Proactively delegate to a subagent in these scenarios:
- **Reasoning-heavy subtasks** — research, analysis, or synthesis tasks that require \
many tool calls (e.g., searching the web, reading multiple files, summarising results)
- **Context-polluting work** — any task whose intermediate tool results would flood \
your own context window; the subagent's results never enter YOUR context, only its \
final answer is returned to you
- **Independent parallel workstreams** — subtasks that do not depend on each other \
can be issued concurrently, dramatically reducing wall-clock time
- **Large-scale data gathering** — when a task involves fetching, parsing, or \
transforming large volumes of data that would otherwise exhaust your context

## WHEN NOT TO USE

- Simple, single-tool calls that finish in one step
- Tasks that need the full conversation history as context (the subagent starts fresh)
- Tasks where you must stream a live response directly back to the user

## Available agent types

{available_agents}

Important: specify only agent types listed above. The subagent runs autonomously \
and returns only its final summary to you.
"""

TASK_TOOL_DESCRIPTION_CN = """\
启动临时子代理，处理复杂、多步骤、独立的隔离上下文任务。

## 何时使用（应主动委派）

在以下场景应主动调用 task_tool 委派子代理：
- **推理密集型子任务** — 需要大量工具调用的研究、分析或汇总任务（如网络搜索、读取多个文件、整合结果）
- **会污染上下文的工作** — 子任务的中间工具结果会淹没主代理上下文窗口时；\
子代理的中间结果不会进入你的上下文，仅最终答案会返回给你，从而保持主上下文清洁高效
- **可并行的独立子任务** — 互不依赖的子任务可并发发出，大幅缩短整体执行时间
- **大规模数据采集** — 需要抓取、解析或转换大量数据，否则将耗尽你的上下文空间

## 何时不使用

- 单步、单工具调用即可完成的简单操作
- 任务需要完整对话历史作为上下文（子代理以全新会话启动）
- 需要实时向用户流式输出的任务

## 可用代理类型及说明

{available_agents}

重要：只能指定上方列出的代理类型。子代理自主执行并仅将最终摘要返回给你。
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
