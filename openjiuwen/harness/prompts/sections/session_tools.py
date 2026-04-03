# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session tools system prompt section for DeepAgent.

This module provides the system prompt section that tells the AI
how to use async session tools (sessions_list, sessions_spawn).
"""

from __future__ import annotations

from typing import Dict, Optional
from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# Session tools system prompt (bilingual)
# ---------------------------------------------------------------------------
SESSION_SYSTEM_PROMPT_CN = """## 会话工具sessions_spawn 用于创建临时子代理，独立完成复杂任务
说明:
    - 部分会话或代码类工具返回中若含 status 为 pending（或等价字段），表示请求已受理，任务正在后台执行，并非失败。
    - 此时不得为「催促结果」或「以为未执行」而连续、重复发起相同或等价的 function_call（相同工具、相同意图、相同关键参数）。
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
    - 若工具返回中含 status 为 pending：用简短自然语言说明任务已在后台执行，请用户稍候或等待系统后续推送/下一轮输入；不要堆叠多余工具调用
    - 仅当用户明确要求重试、变更参数或取消时，再发起新的 function_call
"""

SESSION_SYSTEM_PROMPT_EN = """## Session tools sessions_spawn is used to create temporary subagents
that handle isolated tasks.

When to use:
- Tasks that are complex, multi-step, and can be executed independently
- Scenarios requiring parallel processing, focused reasoning, or large context/token usage
- Tasks that require sandboxed execution (e.g., code execution, search, formatting)
- When only the final output is needed and intermediate steps are not required

When NOT to use:
- Tasks are simple
- Intermediate steps need to be observed
- Task decomposition provides no benefit and only adds latency

Usage Guidelines:
- Execute independent tasks in parallel whenever possible
- Use sub-agents to isolate complex tasks and improve efficiency
- If the tool response contains a status of pending: use brief, natural language to inform the user 
that the task is being executed in the background 
and ask them to wait for subsequent system notifications or the next round of input; do not stack redundant tool calls.
- Only initiate a new function call when the user explicitly requests a retry, changes parameters, or cancels the task.
"""

SESSION_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": SESSION_SYSTEM_PROMPT_CN,
    "en": SESSION_SYSTEM_PROMPT_EN,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def build_session_tools_system_prompt(language: str = "cn") -> str:
    """Get the session tools system prompt for the given language.

    Args:
        language: 'cn' or 'en'.

    Returns:
        Session tools system prompt text.
    """
    return SESSION_SYSTEM_PROMPT.get(language, SESSION_SYSTEM_PROMPT["cn"])


def build_session_tools_section(language: str = "cn") -> Optional["PromptSection"]:
    """Build a PromptSection for session tools system prompt.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance for session tools.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    content = build_session_tools_system_prompt(language)

    return PromptSection(
        name=SectionName.SESSION_TOOLS,
        content={language: content},
        priority=85,
    )


__all__ = [
    "SESSION_SYSTEM_PROMPT",
    "build_session_tools_system_prompt",
    "build_session_tools_section",
]
