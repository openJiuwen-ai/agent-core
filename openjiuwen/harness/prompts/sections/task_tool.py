# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool system prompt section for DeepAgent.

This module provides ONLY the system prompt section that tells the AI
how to use the task_tool. It does NOT contain tool registration metadata.

For tool registration metadata, see sections/tools/task_tool.py
"""

from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Task system prompt (bilingual) - for system message injection
# ---------------------------------------------------------------------------
TASK_SYSTEM_PROMPT_EN = """## task_tool — launch ephemeral subagents for isolated tasks.
When to use:
    - Complex, multi-step tasks that can run independently
    - Parallel processing, focused reasoning, or large context/token usage
    - Sandboxed execution (code, search, formatting)
    - Only the final output is needed, not intermediate steps
When NOT to use:
    - Simple tasks
    - Intermediate steps need to be observed
    - Splitting provides no benefit, only adds latency
Guidelines:
    - Run independent tasks in parallel whenever possible
    - Use sub-agents to isolate complex tasks and improve efficiency
"""

TASK_SYSTEM_PROMPT_CN = """## task_tool 用于创建临时子代理，独立完成复杂任务并返回最终结果。
使用场景:
    - 任务复杂、多步骤、可独立执行
    - 需要并行处理、专注推理、大量上下文 / Token
    - 需要沙箱安全执行（代码、搜索、格式化）
    - 只需最终输出，不关心中间过程
不使用场景:
    - 任务简单
    - 需要查看中间步骤
    - 拆分无收益、仅增加延迟
使用原则:
    - 独立任务尽量并行执行
    - 用子代理隔离复杂任务，提升效率
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


def build_task_section(language: str = "cn") -> Optional["PromptSection"]:
    """Build a PromptSection for task tool system prompt.

    This creates a system prompt section that tells the AI how to use task_tool.
    It does NOT include available_agents list - that's in the tool description.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance for task tool.
    """
    from openjiuwen.harness.prompts.builder import PromptSection
    from openjiuwen.harness.prompts.sections import SectionName

    content = build_task_system_prompt(language)

    return PromptSection(
        name=SectionName.TASK_TOOL,
        content={language: content},
        priority=85,
    )
