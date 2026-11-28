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
TASK_SYSTEM_PROMPT_EN = (
    "# Subagent Usage Rules\nSubagents isolate intermediate work and return final answers. Delegate "
    "bounded, self-contained independent work, not simple calls, work needing full history/live "
    "streamed updates, vague/coupled work or a duplicate of work already delegated. Give objective, "
    "scope, constraints, known findings and expected output; distinguish research from "
    "implementation. Own the synthesis: give concrete paths/changes after research, not merely \"based"
    " on findings, fix it\". The parent synthesizes, verifies and delivers.\n"
)

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
        priority=80,
        category="system_prompt",
    )
