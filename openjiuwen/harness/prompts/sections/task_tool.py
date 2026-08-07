# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool system prompt section for DeepAgent.

This module provides ONLY the system prompt section that tells the AI
how to use the task_tool. It does NOT contain tool registration metadata.

For tool registration metadata, see sections/tools/task_tool.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection

# ---------------------------------------------------------------------------
# Task system prompt (bilingual) - for system message injection
# ---------------------------------------------------------------------------
TASK_SYSTEM_PROMPT_EN = """\
# Subagent Usage Rules

Subagents run in isolated contexts and return only the final result to the main agent, making them suitable for isolating large amounts of intermediate information.

## When to Use

- Deeply read two or more independent documents or files.
- Search, fetch, or analyze three or more independent sources.
- The subtask is reasoning-intensive, requires many tool calls, or its intermediate results would noticeably consume the main context.
- Multiple subtasks are independent and suitable for parallel execution.

## When Not to Use

- A simple operation that can be completed with a single tool call.
- The subtask must depend on the full conversation history.
- The task requires streaming intermediate results to the user in real time.

## Usage Principles

- Delegate independent subtasks in parallel; do not wait for them sequentially.
- Clearly state the objective, scope, constraints, and expected output in `task_description`.
- The main agent is responsible for synthesis, verification, and final delivery.
"""

TASK_SYSTEM_PROMPT_CN = """\
# 子智能体使用规则

子智能体在独立上下文中运行，只把最终结果返回主智能体，适合隔离大量中间信息。

## 应当使用

- 深入阅读两篇及以上相互独立的文档或文件。
- 搜索、抓取或分析三个及以上独立来源。
- 子任务推理密集、工具调用很多或中间结果会明显占用主上下文。
- 多个子任务彼此独立、适合并行执行。

## 不应使用

- 单步工具调用即可完成的简单操作。
- 子任务必须依赖完整对话历史。
- 任务需要实时向用户流式返回中间结果。

## 使用原则

- 多个互不依赖的子任务应并行委派，不要串行等待。
- 在 `task_description` 中写清目标、范围、限制和预期输出。
- 主智能体负责汇总、核对和最终交付。
"""

TASK_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TASK_SYSTEM_PROMPT_CN,
    "en": TASK_SYSTEM_PROMPT_EN,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def build_task_system_prompt(language: str = "cn") -> str:
    """Get the task tool system prompt for the given language.

    This is used ONLY for system prompt injection, NOT for tool registration.

    Args:
        language: 'cn' or 'en'.

    Returns:
        Task system prompt text.
    """
    return TASK_SYSTEM_PROMPT.get(language, TASK_SYSTEM_PROMPT["cn"])


def build_task_section(
    language: str = "cn",
    extension_content: str | None = None,
) -> Optional["PromptSection"]:
    """Build a PromptSection for task tool system prompt.

    This creates a system prompt section that tells the AI how to use task_tool.
    It does NOT include available_agents list - that's in the tool description.

    Args:
        language: 'cn' or 'en'.
        extension_content: Optional product-specific guidance appended to the
            built-in task tool prompt. The extension remains part of the same
            ``task_tool`` section.

    Returns:
        A PromptSection instance for task tool.
    """
    from openjiuwen.harness.prompts.builder import PromptSection
    from openjiuwen.harness.prompts.sections import SectionName

    content = build_task_system_prompt(language)
    if extension_content and extension_content.strip():
        content = f"{content.rstrip()}\n\n{extension_content.strip()}\n"

    return PromptSection(
        name=SectionName.TASK_TOOL,
        content={language: content},
        priority=85,
        category="system_prompt",
    )
