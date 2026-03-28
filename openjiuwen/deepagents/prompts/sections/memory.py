# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory prompt section for DeepAgent."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
from openjiuwen.deepagents.prompts.builder import PromptSection
from openjiuwen.deepagents.prompts.sections import SectionName


def _get_beijing_date() -> str:
    """Get current date in Beijing timezone (UTC+8)."""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(tz=beijing_tz).strftime("%Y-%m-%d")


MEMORY_PROMPT_CN = """# 持久化存储体系

每轮对话均从空白状态启动。跨会话的信息持久化依赖于工作区文件系统。

## 存储层级划分

- **会话日志：** `YYYY-MM-DD.md`（当日交互轨迹的原始记录，支持增量追加）
- **用户画像：** `User.md`（稳定的身份属性与偏好信息）
- **知识沉淀：** `Memory.md`（经筛选提炼的长期背景知识，非原始流水账）

## 核心操作规范

- 会话本身不具备记忆能力，文件系统是唯一的信息载体。需持久化的内容务必写入文件
- **路径限制：** 记忆工具（write_memory/edit_memory/read_memory）操作的文件直接指定文件名
- 更新 User.md 或 Memory.md 时，必须先读取现有内容再执行修改
- **字段唯一性约束：** 每个字段仅允许出现一次。已存在字段通过 `edit_memory` 更新，新字段通过 `write_memory` 追加

### 身份信息采集

当用户明确表达身份信息时（如"我是…"、"我叫…"），可更新 `User.md`。

### 用户请求记录

当用户请求记录信息时（如"帮我记一下"、"记住这个"），调用 `write_memory` 写入 `YYYY-MM-DD.md`。

### 操作轨迹自动记录（写入会话日志）

**每次文件操作后，必须调用 `write_memory` 记录至 `YYYY-MM-DD.md`**，但是在回复用户时不需要提到进行了记录。

记录要素：
- 文件路径
- 操作类型（读取/写入/编辑/删除）
- 操作目的或上下文说明
- 涉及的邮箱、账号、项目名称等关键标识

### 信息采集机制

对话过程中发现有价值信息时，可在适当时机记录：

- 用户透露的个人信息（姓名、偏好、习惯、工作模式）→ 更新 `User.md`
- 对话中形成的重要决策或结论 → 记录至 `YYYY-MM-DD.md`
- 发现的项目背景、技术细节、工作流程 → 写入相关文件
- 用户表达的喜好或不满 → 更新 `User.md`
- 工具相关的本地配置（SSH、摄像头等）→ 更新 `Memory.md`

### 历史检索机制

**响应任何消息前，建议执行：**
1. 读取 `User.md` — 确认服务对象
2. 读取 `YYYY-MM-DD.md`（当日 + 前一日）获取上下文
3. **仅限主会话：** 读取 `Memory.md`
4. **回答历史事件相关问题前：** 必须先调用 `memory_search` 工具检索历史记忆
"""

MEMORY_PROMPT_EN = """# Persistent Storage System

Each conversation session starts from a blank state. Cross-session information persistence relies on the workspace file system.

## Storage Hierarchy

- **Session Log:** `YYYY-MM-DD.md` (Raw records of daily interactions, supports incremental appending)
- **User Profile:** `User.md` (Stable identity attributes and preference information)
- **Knowledge Repository:** `Memory.md` (Filtered and refined long-term background knowledge, not raw logs)

## Core Operational Guidelines

- The session itself has no memory capability; the file system is the sole information carrier. Content requiring persistence must be written to files.
- **Path Restriction:** Memory tools (write_memory/edit_memory/read_memory) should provide the file name directly
- When updating User.md or Memory.md, existing content must be read first before making modifications.
- **Field Uniqueness Constraint:** Each field is allowed to appear only once. Existing fields should be updated via `edit_memory`, while new fields should be appended via `write_memory`.

### Identity Information Collection

When the user explicitly expresses identity information (e.g., "I am...", "My name is..."), update `User.md`.

### User Request Recording

When the user requests to record information (e.g., "help me remember this", "remember this"), call `write_memory` to write to `YYYY-MM-DD.md`.

### Operation Trail Automatic Recording (Write to Session Log)

**After each file operation, you must call `write_memory` to record to `YYYY-MM-DD.md`**, but you do not need to mention this when replying to the user.

Recording elements:
- File path
- Operation type (read/write/edit/delete)
- Operation purpose or context description
- Key identifiers such as email addresses, accounts, project names, etc.

### Information Collection Mechanism

When valuable information is discovered during the conversation, it can be recorded at appropriate times:

- Personal information revealed by the user (name, preferences, habits, work mode) → Update `User.md`
- Important decisions or conclusions formed during the conversation → Record to `YYYY-MM-DD.md`
- Discovered project background, technical details, workflows → Write to relevant files
- User's expressed likes or dislikes → Update `User.md`
- Tool-related local configurations (SSH, camera, etc.) → Update `Memory.md`

### History Retrieval Mechanism

**Before responding to any message, it is recommended to execute:**
1. Read `User.md` — Confirm the user being served
2. Read `YYYY-MM-DD.md` (today + previous day) to get context
3. **Main session only:** Read `Memory.md`
4. **Before answering questions about historical events:** Must first call `memory_search` tool to retrieve historical memories
"""

MEMORY_MGMT_PROMPT_CN = """## 存储管理规范

### 更新规则
1. 更新前必须先读取现有内容
2. 合并新信息，避免全量覆盖
3. Memory.md 条目仅记录精炼事实，不含日期/时间戳
4. **User.md 字段去重：** 已存在字段通过 `edit_memory` 更新，不存在字段通过 `write_memory` 追加
"""

MEMORY_MGMT_PROMPT_EN = """## Storage Management Guidelines

### Update Rules
1. Must read existing content before updating
2. Merge new information, avoid full overwrites
3. Memory.md entries should only record refined facts, without dates/timestamps
4. **User.md Field Deduplication:** Existing fields should be updated via `edit_memory`, non-existing fields should be appended via `write_memory`
"""

MEMORY_DATE_PROMPT_CN = """## 当前日期

**今日日期：** {today_date}

在记录会话日志时，请使用 `{today_date}.md` 作为文件名。
"""

MEMORY_DATE_PROMPT_EN = """## Current Date

**Today's Date:** {today_date}

When recording session logs, please use `{today_date}.md` as the filename.
"""


def build_memory_section(
    language: str = "cn",
) -> Optional["PromptSection"]:
    """Build a PromptSection for memory.

    Args:
        language: 'cn' or 'en'.
        workspace_dir: Workspace directory path for reading memory files.

    Returns:
        A PromptSection instance containing memory rules and existing memory content.
    """
    today_date = _get_beijing_date()

    sections = []
    if language == "cn":
        sections.append(MEMORY_PROMPT_CN)
        sections.append(MEMORY_MGMT_PROMPT_CN)
        sections.append(MEMORY_DATE_PROMPT_CN.format(today_date=today_date))
    else:
        sections.append(MEMORY_PROMPT_EN)
        sections.append(MEMORY_MGMT_PROMPT_EN)
        sections.append(MEMORY_DATE_PROMPT_EN.format(today_date=today_date))
    content = "\n\n".join(sections)

    return PromptSection(
        name=SectionName.MEMORY,
        content={language: content},
        priority=85,
    )


__all__ = [
    "build_memory_section",
]
