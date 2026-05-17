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
TASK_SYSTEM_PROMPT_EN = """\
## task_tool — proactively delegate to subagents to protect your context window

**Core advantage**: subagents run in isolated context windows — ALL intermediate tool \
call results never enter YOUR context; only the final summary is returned to you, \
dramatically saving tokens and keeping your main context clean and efficient.

**Mandatory use cases (MUST call task_tool — direct tool calls are not allowed):**
    - Deeply reading 2 or more documents/papers/files: each must be delegated to \
an independent subagent; do NOT use read_file to read them yourself one by one
    - Searching, fetching, or analysing 3 or more independent sources

Proactively delegate in these scenarios:
    - Reasoning-heavy tasks: research or analysis requiring many tool calls \
(web search, reading multiple files, synthesising results)
    - Context-flooding risk: the subtask's intermediate results would overwhelm \
your context window — delegate to keep it clean
    - Independent parallel workstreams: dispatch concurrent subtasks to \
dramatically reduce wall-clock time
    - Large-scale data processing: fetching, parsing, or transforming large \
volumes of data that would otherwise exhaust your context

Do NOT delegate:
    - Simple tasks completable in a single tool call
    - Tasks that require full conversation history as context \
(the subagent starts with a fresh session)
    - Tasks where you must stream a live response directly to the user

Principles:
    - When you encounter a delegatable complex task, prefer delegation over \
handling it yourself — this is the most effective way to protect your context
    - Multiple independent subagent tasks MUST be dispatched concurrently, \
never sequentially
"""

TASK_SYSTEM_PROMPT_CN = """\
## task_tool — 主动委派子代理，保护主上下文窗口

**核心优势**：子代理在独立上下文中运行，其所有中间工具调用结果永远不会进入你的上下文，
仅最终摘要返回给你，从而大幅节省 Token、保持主上下文清晰高效。

**强制使用场景（必须调用 task_tool，不得直接操作）：**
    - 需要深入阅读 2 篇及以上文档/论文/文件时，每篇必须委派独立子代理处理，不得用 read_file 自行逐篇读取
    - 需要对 3 个及以上独立来源进行搜索、抓取或分析时

应主动委派的场景：
    - 推理密集型任务：需要大量工具调用的研究与分析（网络搜索、读取多个文件、汇总结果）
    - 上下文污染风险：子任务的中间工具结果会淹没你的上下文窗口——委派出去以保持上下文干净
    - 可并行的独立子任务：互不依赖的任务并发发出，大幅缩短整体执行时间
    - 大规模数据处理：需要抓取、解析或转换大量数据，否则将耗尽你的上下文空间

不应委派的场景：
    - 单步工具调用即可完成的简单操作
    - 任务需要完整对话历史作为背景（子代理以全新会话启动）
    - 需要实时向用户流式返回结果的任务

使用原则：
    - 遇到可委派的复杂任务，优先选择委派而非自行处理——这是保护主上下文最有效的方式
    - 多个互不依赖的子代理任务必须并发发出，不得串行等待
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
