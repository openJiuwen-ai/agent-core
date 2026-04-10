# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Todo system prompt section for DeepAgent.

This module provides the system prompt section
how to use the todo tools for progress tracking.
"""

from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

# ---------------------------------------------------------------------------
# TodoTool system prompt - for system message injection
# ---------------------------------------------------------------------------
TODO_SYSTEM_PROMPT_EN = """
Use the todo tools (todo_create, todo_modify, todo_list) to break down and manage your work. These tools help track progress, organize complex tasks, and ensure all requirements are completed.

**When to create a task list — call todo_create immediately when:**
- User explicitly requests a todo list or provides multiple items to complete
- Task requires 3 or more distinct steps
- Task has planning nature (multi-step implementation, feature development, etc.)

Identify the planning need and call todo_create BEFORE starting execution.

**Task management rules:**
- Update status in real-time: call todo_modify the moment a task status changes
- Only one task can be in_progress at a time; complete it before starting the next
- Batch updates: consolidate multiple status changes into a single todo_modify call
- Cancel tasks that are no longer needed
- Can understand the current task planning progress by calling todo_list.

**Before marking a task completed:**
- Verify the work is fully done (e.g., run tests to confirm)
- Never mark completed if: partially implemented, tests failing, unresolved errors
- After completing, check if new follow-up tasks were discovered and append them via todo_modify
"""

TODO_SYSTEM_PROMPT_CN = """
使用 todo 工具（todo_create、todo_modify、todo_list）拆解和管理工作。这些工具用于跟踪进度、组织复杂任务，确保所有需求都被完成。

**何时创建任务列表 — 以下情况立即调用 todo_create：**
- 用户明确要求使用待办清单，或提供了多个待完成事项
- 任务需要 3 个或更多步骤
- 任务具有规划性质（多步骤实现、功能开发等）

**识别到规划需求后，在开始执行前立即调用 todo_create。**

**任务管理规则：**
- 实时更新状态：任务状态变化时立即调用 todo_modify
- 同一时间只能有一个任务处于 in_progress，完成后再开始下一个
- 批量更新：将多个状态变更合并为一次 todo_modify 调用
- 不再需要的任务用 todo_modify 标记为 cancelled
- 可通过调用 todo_list 了解当前任务规划进展

**将任务标记为已完成前：**
- 必须仔细验证工作已全部完成（如运行测试用例）
- 以下情况绝对不能标记为已完成：部分实现、测试失败、存在未解决的错误等
- 标记完成后，检查实现过程中是否发现新的后续任务，及时通过 todo_modify 追加
"""

TODO_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TODO_SYSTEM_PROMPT_CN,
    "en": TODO_SYSTEM_PROMPT_EN,
}

# ---------------------------------------------------------------------------
# Progress reminder user prompt (bilingual) - for user message injection
# ---------------------------------------------------------------------------
PROGRESS_REMINDER_USER_PROMPT_EN = """
The following is the content and status of all tasks in the current task plan:

{tasks}

The task currently being executed is:

{in_progress_task}

Please review the above task progress to ensure the plan is being executed correctly.
If any tasks are stuck or need adjustment, please update them promptly
"""

PROGRESS_REMINDER_USER_PROMPT_CN = """
以下是当前任务规划中所有任务的内容和状态：

{tasks}

正在执行的任务为：

{in_progress_task}

请查看上述任务进度，确保计划正在正确执行。如果有任务卡住或需要调整，请及时更新
"""

PROGRESS_REMINDER_USER_PROMPT: Dict[str, str] = {
    "cn": PROGRESS_REMINDER_USER_PROMPT_CN,
    "en": PROGRESS_REMINDER_USER_PROMPT_EN,
}


def build_todo_system_prompt(language: str = "cn") -> str:
    """Get the todo system prompt for the given language.

    Args:
        language: 'cn' or 'en'.

    Returns:
        Todo system prompt text.
    """
    return TODO_SYSTEM_PROMPT.get(language, TODO_SYSTEM_PROMPT["cn"])


def build_progress_reminder_user_prompt(language: str = "cn",
            tasks: str = "", in_progress_task: str = "") -> str:
    """Get the progress reminder user prompt for the given language.

    Args:
        language: 'cn' or 'en'.
        tasks: All tasks currently planned
        in_progress_task: The task with the status in_progress

    Returns:
        Progress reminder user prompt text.
    """
    prompt_template = PROGRESS_REMINDER_USER_PROMPT.get(
        language, PROGRESS_REMINDER_USER_PROMPT["cn"]
    )
    return prompt_template.format(tasks=tasks, in_progress_task=in_progress_task)


def build_todo_section(language: str = "cn") -> Optional["PromptSection"]:
    """Build a PromptSection for todo system prompt.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance for todo.
    """
    content = build_todo_system_prompt(language)

    return PromptSection(
        name=SectionName.TODO,
        content={language: content},
        priority=90,
    )
