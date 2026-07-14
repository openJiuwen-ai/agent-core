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
# Task Management
Use the todo tools (todo_create, todo_modify, todo_list) to break down and manage your work. These tools help track progress, organize complex tasks, and ensure all requirements are completed.

**When to create a task list — call todo_create immediately when:**
- User explicitly requests a todo list or provides multiple items to complete
- Task requires 3 or more distinct steps
- Task has planning nature (multi-step implementation, feature development, etc.)

Identify the planning need and call todo_create BEFORE starting execution.

**When NOT to use todo tools — do not call todo_create, todo_modify, or todo_list when:**
- User only wants a task list displayed and explicitly says no execution is needed now (e.g. list only, do not execute, not yet) → present the list in your reply as text or a table; do not call any todo tools

**Task management rules:**
- Update status in real-time: call todo_modify the moment a task status changes
- Only one task can be in_progress at a time; complete it before starting the next
- **Follow list order**: do not skip pending earlier tasks; the tool rejects out-of-order status changes and task additions
- Batch updates: consolidate status changes in one todo_modify when order is valid (e.g. complete current, then in_progress next)
- When marking the current step completed, include the next pending item as in_progress in the same todo_modify batch whenever more work remains
- Mark unneeded tasks as cancelled via todo_modify (explicit skip)
- Call todo_list before cross-stage or batch status changes when IDs/order are uncertain
- Can understand the current task planning progress by calling todo_list.

**Before marking a task completed:**
- Verify the work is fully done (e.g., run tests to confirm)
- Never mark completed if: partially implemented, tests failing, unresolved errors
- After completing, check whether new follow-up tasks were discovered. Before append or insert, call todo_list and follow the list-order rules.
- skill_complete does not update todos — use todo_modify to modify todos

**Todo tool choice (same for main and sub agents: each checks its own session's todo.json)**
| User intent / todo status | Action |
| Continue or retry while pending / in_progress items exist | todo_list → todo_modify; **do not** todo_create |
| Independent new request and no active items remain | todo_create a complete new plan |
| User explicitly asks to restart or replan while active items exist | todo_create(force=true) |

- When the user says "continue" / "retry" / "try again", resume from the in_progress item;
  do not restart from Stage 1
- Do not rename or reuse an old todo as the first step of an independent new plan
- Sub-agents have separate sessions; the main agent must **not** block a sub-agent from
  todo_create in its own session because the main session already has todos
"""

TODO_SYSTEM_PROMPT_CN = """
# 任务管理
使用 todo 工具（todo_create、todo_modify、todo_list）拆解和管理工作。这些工具用于跟踪进度、组织复杂任务，确保所有需求都被完成。

**何时创建任务列表 — 以下情况立即调用 todo_create：**
- 用户明确要求使用待办清单，或提供了多个待完成事项
- 任务需要 3 个或更多步骤
- 任务具有规划性质（多步骤实现、功能开发等）

**识别到规划需求后，在开始执行前立即调用 todo_create。**

**何时不使用 todo 工具 — 以下情况不要调用 todo_create、todo_modify、todo_list：**
- 用户仅要求列出/展示任务清单，且明确表示不需要执行、暂不执行、先不要做等 → 直接在回复中以文本或表格呈现清单，勿调用 todo 工具

**任务管理规则：**
- 实时更新状态：任务状态变化时立即调用 todo_modify
- 同一时间只能有一个任务处于 in_progress，完成后再开始下一个
- **按列表顺序推进**：不得跳过 pending 的前序任务；工具会拒绝跨档状态更新和新增任务
- 批量更新：将多个状态变更合并为一次 todo_modify 调用（须满足顺序：例如先 completed 当前项，再 in_progress 下一项）
- 将当前步骤标为 completed 时，若仍有后续待办，须在同一批 todo_modify 中把下一项 pending 标为 in_progress
- 不再需要的任务用 todo_modify 标记为 cancelled（明确跳过）
- 跨 Stage 或批量改状态前，先 todo_list 核对 ID 与顺序
- 可通过调用 todo_list 了解当前任务规划进展

**将任务标记为已完成前：**
- 必须仔细验证工作已全部完成（如运行测试用例）
- 以下情况绝对不能标记为已完成：部分实现、测试失败、存在未解决的错误等
- 标记完成后，检查实现过程中是否发现新的后续任务；append 或 insert 前先 todo_list，并遵守列表顺序规则
- skill_complete 不更新 todo，修改 todo 请用 todo_modify

**todo 工具选择（主 Agent、子 Agent 相同：各看本 session 的 todo.json）**
| 用户意图 / 本 session todo 状态 | 做法 |
| 用户要求继续/重试，且有 pending / in_progress | todo_list → todo_modify 续跑；**不要** todo_create |
| 独立新请求，且已无活跃项 | todo_create 建立完整新计划 |
| 用户明确要求重来/重规划，且有活跃项 | todo_create（force=true） |

- 用户说「继续/重试/再试一次」时，从 in_progress 项续跑，勿从 Stage 1 重来
- 独立新计划不得改名或复用旧 todo 作为首项
- 子 Agent 拥有独立 session；主 Agent **不得**因主会话已有 todo 而阻止子 Agent 在其自身 session 内正常 todo_create
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

TODO_ADVANCE_REMINDER_USER_PROMPT_CN = """
以下是当前任务规划中所有任务的内容和状态：

{tasks}

没有 in_progress 任务，但仍有 pending，请调用 todo_modify 推进：
若当前步骤未完成，先将对应 pending 标 in_progress；
若当前步骤完成，同一批将已完成项标 completed、下一项 pending 标 in_progress；
不确定 id 时先 todo_list。
"""

TODO_ADVANCE_REMINDER_USER_PROMPT_EN = """
Current task plan:

{tasks}

No task is in_progress, but pending items remain. Call todo_modify to advance:
If the current step is not finished, set the corresponding pending item to in_progress first;
If the current step is finished, mark the completed item completed and the next pending item in_progress in the same batch;
Call todo_list if IDs are uncertain.
"""

TODO_ADVANCE_REMINDER_USER_PROMPT: Dict[str, str] = {
    "cn": TODO_ADVANCE_REMINDER_USER_PROMPT_CN,
    "en": TODO_ADVANCE_REMINDER_USER_PROMPT_EN,
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


def build_todo_advance_reminder_user_prompt(
    language: str = "cn",
    *,
    tasks: str = "",
) -> str:
    """Reminder when pending todos exist but none are in_progress."""
    prompt_template = TODO_ADVANCE_REMINDER_USER_PROMPT.get(
        language, TODO_ADVANCE_REMINDER_USER_PROMPT["cn"]
    )
    return prompt_template.format(tasks=tasks)


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
        priority=31,
    )
