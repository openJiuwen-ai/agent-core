# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory prompt section for DeepAgent."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


def _get_beijing_date() -> str:
    """Get current date in Beijing timezone (UTC+8)."""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(tz=beijing_tz).strftime("%Y-%m-%d")

MEMORY_PROMPT_CN_READ_ONLY = """# 持久化存储体系（只读模式）

### 存储层级划分

- **会话日志：** `YYYY-MM-DD.md`（存储当日有参考价值的交互记录，包括情景记忆和任务指令。支持增量追加，确保每次操作、用户指令和情景变化都被记录。）
- **用户画像：** `USER.md`（稳定的身份属性与偏好信息）
- **知识沉淀：** `MEMORY.md`（经筛选提炼的长期背景知识，非原始流水账）

#### 历史检索机制

 1.**历史信息检索（强制）** — **回答任何关于历史事件、日期、人物、过去对话的问题前，必须先调用 `memory_search` 工具检索相关记忆**
   - 搜索查询应包含问题中的关键信息（人名、日期、事件关键词）
   - 如果搜索结果不足，尝试用不同的关键词再次搜索
   - 基于检索到的记忆信息回答问题，不要依赖预训练知识
"""

MEMORY_PROMPT_EN_READ_ONLY = """# Persistent Storage System (Read-Only Mode)

### Storage Hierarchy

- **Session Log:** `YYYY-MM-DD.md` (Valuable interaction records for the day, including episodic memory, and task instructions. Supports incremental appending to ensure every operation, user instruction, and contextual change is recorded.)
- **User Profile:** `USER.md` (Stable identity attributes and preference information.)
- **Knowledge Repository:** `MEMORY.md` (Filtered and refined long-term background knowledge, not raw logs.)

#### History Retrieval Mechanism

1. **Historical information retrieval (mandatory):** Before answering any question about historical events, dates, people, or past conversations, you must call `memory_search` first
   - Search query should include key information from the question (names, dates, event keywords)
   - If results are insufficient, retry with different keywords
   - Answer based on retrieved memory results, not pretraining knowledge

**Note:** In cron job mode, only reading and searching memories is supported. Writing or modifying memory files is not allowed.
"""

MEMORY_PROMPT_CN = """# 持久化存储体系

每轮对话均从空白状态启动。跨会话的信息持久化依赖于工作区文件系统。记录悄悄进行就好，不需要让用户感知到。

### 存储层级划分

- **会话日志：** `YYYY-MM-DD.md`（存储当日有参考价值的交互记录，包括情景记忆和任务指令。支持增量追加，确保每次操作、用户指令和情景变化都被记录。）
- **用户画像：** `USER.md`（稳定的身份属性与偏好信息）
- **知识沉淀：** `MEMORY.md`（经筛选提炼的长期背景知识，非原始流水账）

### 核心操作规范

- 会话本身不具备记忆能力，文件系统是唯一的信息载体。需持久化的内容务必写入文件
- **路径限制：** 记忆工具（write_memory/edit_memory/read_memory）操作文件时，直接给出文件名
- 更新 USER.md 或 MEMORY.md 时，必须先读取现有内容再执行修改
- **字段唯一性约束：** 每个字段仅允许出现一次。已存在字段通过 `edit_memory` 更新，新字段通过 `write_memory` 追加

### 信息采集、存储操作与记录

对话过程中，发现有价值的信息时，应该立即进行分类、存储，并及时记录，确保不拖延记录过程：

1. **用户画像信息（user_profile）**：记录用户的身份信息、偏好、习惯等稳定属性，比如用户的职业、兴趣、工作模式、喜好、不满等。
   - **存储**：写入 `USER.md`。

2. **情景记忆信息（episodic_memory）**：记录用户经历的具体事件或重要决策，比如用户要求完成的任务、描述的项目进展、某次事件等。
   - **存储**：写入 `YYYY-MM-DD.md`。

3. **语义记忆信息（semantic_memory）**：存储背景知识、技术细节、工具相关的本地配置（SSH、摄像头等）等长期有效信息，比如项目技术栈、工具的配置等。
   - **存储**：写入 `MEMORY.md`。

4. **摘要记忆（summary_memory）**：提炼对话中的关键信息，帮助后续快速回顾，比如对话中形成的重要决策、核心结论、讨论的要点等。
   - **存储**：写入 `YYYY-MM-DD.md`。

5. **用户请求记录（request_memory）**：记录用户明确请求的信息，帮助后续服务，比如用户要求记住某个信息、用户要求某个动作等。
   - **存储**：写入 `YYYY-MM-DD.md`。

6. **其他信息（others）**：当用户提到任何有价值的细节或信息时，或每次文件操作后，自动调用 write_memory 使用 append=true 参数追加记录至 YYYY-MM-DD.md。
    有价值的信息包括但不限于：用户提供的联系人信息、项目细节、任务指令、偏好、文件路径、存储位置、任何可提高效率的信息等。发现的项目背景、技术细节、工作流程等也要写入相关文件。

#### 历史检索机制

 1.**历史信息检索（强制）** — **回答任何关于历史事件、日期、人物、过去对话的问题前，必须先调用 `memory_search` 工具检索相关记忆**
   - 搜索查询应包含问题中的关键信息（人名、日期、事件关键词）
   - 如果搜索结果不足，尝试用不同的关键词再次搜索
   - 基于检索到的记忆信息回答问题，不要依赖预训练知识
"""

MEMORY_PROMPT_EN = """# Persistent Storage System

Each conversation session starts from a blank state. Cross-session information persistence relies on the workspace file system. The recording process should occur seamlessly without the user's awareness.

### Storage Hierarchy

- **Session Log:** `YYYY-MM-DD.md` (Valuable interaction records for the day, including episodic memory, and task instructions. Supports incremental appending to ensure every operation, user instruction, and contextual change is recorded.)
- **User Profile:** `USER.md` (Stable identity attributes and preference information.)
- **Knowledge Repository:** `MEMORY.md` (Filtered and refined long-term background knowledge, not raw logs.)

### Core Operation Guidelines

 - The session itself has no memory; the file system is the only carrier. Content requiring persistence must be written to files.	 
 - **Path Restriction:** Memory tools (write_memory/edit_memory/read_memory) should give file name directly when using.
 - When updating USER.md or MEMORY.md, existing content must be read first before making modifications.	 
 - **Field Uniqueness Constraint:** Each field can appear only once. Existing fields should be updated via `edit_memory`, while new fields should be appended via `write_memory`.

### Information Collection, Storage Operations, and Recording

When valuable information appears during the conversation, classify it and store it immediately. Do not delay recording:

1. **User Profile Information (`user_profile`)**: Stable user attributes such as identity, preferences, habits, work style, likes/dislikes.
   - **Storage**: Write to `USER.md`.

2. **Episodic Memory (`episodic_memory`)**: Specific events or important decisions, such as assigned tasks, project progress, or notable incidents.
   - **Storage**: Write to `YYYY-MM-DD.md`.

3. **Semantic Memory (`semantic_memory`)**: Long-term background knowledge, technical details, and tool-related local configs (SSH, camera, etc.).
   - **Storage**: Write to `MEMORY.md`.

4. **Summary Memory (`summary_memory`)**: Distilled key points from the conversation (important decisions, core conclusions, discussion highlights).
   - **Storage**: Write to `YYYY-MM-DD.md`.

5. **User Request Record (`request_memory`)**: Information explicitly requested by the user to be remembered or actions explicitly requested.
   - **Storage**: Write to `YYYY-MM-DD.md`.

6. **Other Information (`others`)**: Whenever the user mentions any valuable detail, or after each file operation, automatically call `write_memory` with `append=true` to append to `YYYY-MM-DD.md` immediately
   Valuable details include but not limited to project details, task instructions, preferences, file paths, storage locations, and any efficiency-improving details. Discovered project background, technical details, and workflows should also be written to relevant files.

#### History Retrieval Mechanism

1. **Historical information retrieval (mandatory):** Before answering any question about historical events, dates, people, or past conversations, you must call `memory_search` first
   - Search query should include key information from the question (names, dates, event keywords)
   - If results are insufficient, retry with different keywords
   - Answer based on retrieved memory results, not pretraining knowledge
"""

MEMORY_MGMT_PROMPT_CN = """### 存储管理规范

#### 更新规则
1. 更新前必须先读取现有内容
2. 合并新信息，避免全量覆盖
3. MEMORY.md 条目仅记录精炼事实，不含日期/时间戳
4. **USER.md 字段去重：** 已存在字段通过 `edit_memory` 更新，不存在字段通过 `write_memory` 追加
"""

MEMORY_MGMT_PROMPT_EN = """### Storage Management Guidelines

#### Update Rules
1. Must read existing content before updating
2. Merge new information, avoid full overwrites
3. MEMORY.md entries should only record refined facts, without dates/timestamps
4. **USER.md Field Deduplication:** Existing fields should be updated via `edit_memory`, non-existing fields should be appended via `write_memory`
"""

MEMORY_DATE_PROMPT_CN = """
在操作当天的会话日志时，请使用 `{today_date}.md` 作为文件名。
"""

MEMORY_DATE_PROMPT_EN = """
When operating today's session logs file, please use `{today_date}.md` as the filename.
"""

MEMORY_INACTIVE_PROMPT_CN = """## 持久化存储体系（被动模式）

### 存储层级划分

- **会话日志：** `YYYY-MM-DD.md`
- **用户画像：** `USER.md`
- **知识沉淀：** `MEMORY.md`

### 核心操作规范

- 使用记忆工具（write_memory/edit_memory/read_memory）操作文件时，直接给出文件名
- 更新 USER.md 或 MEMORY.md 时，必须先读取现有内容再执行修改
- 已存在字段通过 `edit_memory` 更新，新字段通过 `write_memory` 追加

### 使用原则

- **仅在用户明确要求时记录**：当用户说"记住"、"记录"、"保存"时，调用 write_memory 或 edit_memory 完成存储
- **仅在用户询问历史时搜索**：当用户要求"回忆"、"查找"以前的内容，或明确询问历史信息时，调用 memory_search 检索
- **仅在需要时读取记忆文件**：当回答确实依赖历史上下文时才读取 USER.md、MEMORY.md 等文件
- 记录信息时，根据内容类型选择存储位置：
  - 用户身份/偏好 → `USER.md`
  - 长期知识/配置 → `MEMORY.md`
  - 事件/日常记录 → `YYYY-MM-DD.md`

"""

MEMORY_INACTIVE_PROMPT_EN = """## Persistent Storage System (Passive Mode)

### Storage Hierarchy

- **Session Log:** `memory/YYYY-MM-DD.md`
- **User Profile:** `USER.md`
- **Knowledge Repository:** `MEMORY.md`

### Core Operation Guidelines

- Provide the file name directly when using tools (write_memory/edit_memory/read_memory) to operate memory files
- When updating USER.md or MEMORY.md, existing content must be read first before making modifications
- Existing fields should be updated via `edit_memory`, new fields via `write_memory`

### Usage Principles

- **Record only when the user explicitly asks**: When the user says "remember", "record", or "save", call write_memory or edit_memory to persist the information
- **Search only when the user asks about history**: When the user requests to "recall" or "find" past content, or explicitly asks about historical information, call memory_search to retrieve it
- **Read memory files only when needed**: Read USER.md, MEMORY.md, etc. only when the answer genuinely depends on historical context
- When recording information, choose storage by content type:
  - User identity/preferences → `USER.md`
  - Long-term knowledge/config → `MEMORY.md`
  - Events/daily records → `memory/YYYY-MM-DD.md`

"""


def build_memory_section(
    language: str = "cn",
    read_only: bool = False,
    is_proactive: bool = True,
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
    if read_only:
        if language == "cn":
            sections.append(MEMORY_PROMPT_CN_READ_ONLY)
        else:
            sections.append(MEMORY_PROMPT_EN_READ_ONLY)
    elif not is_proactive:
        if language == "cn":
            sections.append(MEMORY_INACTIVE_PROMPT_CN)
        else:
            sections.append(MEMORY_INACTIVE_PROMPT_EN)
    else:
        if language == "cn":
            sections.append(MEMORY_PROMPT_CN)
            sections.append(MEMORY_MGMT_PROMPT_CN)
            sections.append(MEMORY_DATE_PROMPT_CN.format(today_date=today_date))
        else:
            sections.append(MEMORY_PROMPT_EN)
            sections.append(MEMORY_MGMT_PROMPT_EN)
            sections.append(MEMORY_DATE_PROMPT_EN.format(today_date=today_date))
    content = "\n".join(sections)

    return PromptSection(
        name=SectionName.MEMORY,
        content={language: content},
        priority=50,
    )


__all__ = [
    "build_memory_section",
]
