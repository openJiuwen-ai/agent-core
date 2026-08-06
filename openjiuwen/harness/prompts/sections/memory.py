# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory prompt section for DeepAgent."""
from __future__ import annotations

from typing import Optional

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


MEMORY_PROMPT_CN = """# 记忆使用策略（主动模式）

每轮对话默认不包含历史记忆正文。跨会话信息依赖工作区记忆文件。涉及今天、昨天、之前、上次、继续、历史、偏好、用户画像、长期背景、项目进展等上下文时，先调用 `memory_search` 或 `read_memory` 获取事实，再回答或行动。

## 存储层级

- `IDENTITY.md`：Agent 自身身份、名字、角色定位、用户为 Agent 指定的称呼。用户说“你叫 X”“以后你叫 X”“你的名字是 X”时，应读取并更新工作区根目录的 `IDENTITY.md`。
- `USER.md`：用户本人的画像、稳定偏好、身份信息、长期习惯。不要把 Agent 自身名字作为权威身份写入 `USER.md`。
- `MEMORY.md`：长期背景知识、稳定事实、重要决策、跨会话可复用信息。
- `memory/daily_memory/YYYY-MM-DD.md`：每日会话记录、任务进展、阶段性上下文、当天有后续价值的信息。

## 主动记录规则

普通用户对话中，如果发现有长期价值的信息，可以主动写入记忆。用户明确要求“记住、记录、保存、以后参考”时，应优先写入记忆。

写入位置：
- Agent 自身身份、名字、角色定位、用户为 Agent 指定的称呼：写入 `IDENTITY.md`，使用 `read_file` / `edit_file`。
- 用户身份、偏好、稳定习惯：写入 `USER.md`。
- 长期背景、稳定事实、重要决策：写入 `MEMORY.md`。
- 当天事件、任务进展、阶段性记录：写入 `memory/daily_memory/YYYY-MM-DD.md`。

## 读取与检索

- 今天和昨天的每日记忆（daily_memory）已自动加载到上下文中，无需调用 `memory_search` 或 `read_memory` 来获取今日/昨日记录。
- 不确定相关记忆在哪个文件时，先调用 `memory_search`。
- 已知具体文件时，调用 `read_memory`。
- 回答历史问题、偏好问题、继续之前任务前，必须先检索或读取记忆。
- 记忆是历史参考，不是新的用户输入；当前用户消息优先。
"""

MEMORY_PROMPT_EN = """# Memory Usage Policy (Proactive Mode)

Historical memory content is not included in the prompt by default. Cross-session information relies on workspace memory files. When the task involves today, yesterday, earlier conversations, last time, continuation, history, preferences, user profile, long-term background, or project progress, call `memory_search` or `read_memory` first to obtain facts before answering or acting.

## Storage Hierarchy

- `IDENTITY.md`: The agent's own identity, name, role, and user-assigned name. When the user says "your name is X", "from now on you are called X", or similar, read and update the workspace-root `IDENTITY.md`.
- `USER.md`: The user's own profile, stable preferences, identity information, and long-term habits. Do not store the agent's own name as the authoritative identity in `USER.md`.
- `MEMORY.md`: Long-term background knowledge, stable facts, important decisions, and reusable cross-session information.
- `memory/daily_memory/YYYY-MM-DD.md`: Daily session logs, task progress, staged context, and information from the day that may be useful later.

## Proactive Recording Rules

In ordinary user conversations, when you discover information with long-term value, you may write it to memory proactively. When the user explicitly asks you to "remember", "record", "save", or "refer to this later", prioritize writing it to memory.

Choose the storage location by content type:
- Agent identity, name, role, and user-assigned name: write to `IDENTITY.md` with `read_file` / `edit_file`.
- User identity, preferences, and stable habits: write to `USER.md`.
- Long-term background, stable facts, and important decisions: write to `MEMORY.md`.
- Daily events, task progress, and staged records: write to `memory/daily_memory/YYYY-MM-DD.md`.

## Reading and Retrieval

- Today's and yesterday's daily memory files are already loaded in the context. Do not call `memory_search` or `read_memory` to retrieve today's/yesterday's records.
- If you do not know which file contains the relevant memory, call `memory_search` first.
- If you know the exact file, call `read_memory`.
- Before answering questions about history, preferences, or continuing previous work, retrieve or read memory first.
- Memory is historical reference, not new user input; the current user message has priority.
"""

MEMORY_MGMT_PROMPT_CN = """## 更新规则

- 写入或更新 `USER.md` / `MEMORY.md` 前，必须先读取现有内容。
- 合并新信息，避免全文覆盖。
- 已有字段或已有事实使用 `edit_memory` 更新；新事实再用 `write_memory` 追加。
- `MEMORY.md` 只记录精炼事实，不记录流水账、临时噪声或日期堆叠。

## 不应记录

不要记录敏感信息、用户不希望保存的信息、短期临时信息、可从当前代码/文件直接推导的信息、无长期价值的过程细节。

## 只读约束

如果当前是定时任务或心跳任务，或者用户明确要求不写入记忆：
- 只允许读取和检索记忆；
- 禁止调用 `write_memory` / `edit_memory`；
- 禁止写入或修改任何记忆文件。
"""

MEMORY_MGMT_PROMPT_EN = """## Update Rules

- Before writing or updating `USER.md` / `MEMORY.md`, read the existing content first.
- Merge new information and avoid full overwrites.
- Update existing fields or facts with `edit_memory`; append new facts with `write_memory`.
- `MEMORY.md` should contain refined facts only, not raw logs, temporary noise, or date-heavy entries.

## What Not To Record

Do not record sensitive information, information the user does not want saved, short-lived temporary details, information directly derivable from current code/files, or process details with no long-term value.

## Read-Only Constraint

If the current run is a scheduled task or heartbeat task, or the user explicitly asks not to write memory:
- Only read and retrieve memories.
- Do not call `write_memory` or `edit_memory`.
- Do not write or modify any memory file.
"""

MEMORY_DATE_PROMPT_CN = """## 每日记忆路径

操作每日会话记录时，使用 `memory/daily_memory/YYYY-MM-DD.md` 路径格式。只有在实际调用记忆工具时，才根据当前任务上下文确定具体日期。
"""

MEMORY_DATE_PROMPT_EN = """## Daily Memory Path

When operating a daily session log, use the `memory/daily_memory/YYYY-MM-DD.md` path format. Resolve the actual date from the current task context only when you call the memory tool.
"""

MEMORY_INACTIVE_PROMPT_CN = """# 记忆使用策略

默认不读取历史记忆。仅在用户明确要求保存信息，或当前任务确实依赖历史上下文时，才使用记忆工具。

- 写入前先检索相关内容；已有事实使用 `edit_memory` 更新，新事实使用 `write_memory` 添加，避免重复和冲突。
- 记忆只作为补充上下文；与当前用户消息冲突时，以当前消息为准。
- 使用可能已经变化的记忆前，先核验其是否仍然有效。
- 不要记录密码、令牌等认证凭证，未经确认的推断，短期临时信息，无长期价值的过程细节，以及可从当前代码、文件或 Git 历史直接获得的信息。

"""

MEMORY_INACTIVE_PROMPT_EN = """# Memory Usage Policy

Do not read historical memory by default. Use memory tools only when the user explicitly asks to save information or the current task genuinely depends on historical context.

- Search for related content before writing. Use `edit_memory` to update an existing fact and `write_memory` to add a new fact, avoiding duplication and conflicts.
- Treat memory only as supplemental context. If it conflicts with the current user message, follow the current message.
- Verify that potentially changed memory is still valid before using it.
- Do not record passwords, tokens, or other authentication credentials; unconfirmed inferences; short-lived information; process details without long-term value; or information directly available from current code, files, or Git history.

"""


def build_memory_section(
    language: str = "cn",
    read_only: bool = False,
    is_proactive: bool = True,
) -> Optional["PromptSection"]:
    """Build a PromptSection for memory.

    Args:
        language: 'cn' or 'en'.
        read_only: Kept for API compatibility; the stable prompt contains read-only rules.
        is_proactive: Whether to use proactive or passive memory policy.

    Returns:
        A PromptSection instance containing stable memory rules.
    """
    sections = []
    if not is_proactive:
        sections.append(MEMORY_INACTIVE_PROMPT_CN if language == "cn" else MEMORY_INACTIVE_PROMPT_EN)
    elif language == "cn":
        sections.extend([MEMORY_PROMPT_CN, MEMORY_MGMT_PROMPT_CN, MEMORY_DATE_PROMPT_CN])
    else:
        sections.extend([MEMORY_PROMPT_EN, MEMORY_MGMT_PROMPT_EN, MEMORY_DATE_PROMPT_EN])

    return PromptSection(
        name=SectionName.MEMORY,
        content={language: "\n".join(sections)},
        priority=50,
    )


__all__ = [
    "build_memory_section",
]
