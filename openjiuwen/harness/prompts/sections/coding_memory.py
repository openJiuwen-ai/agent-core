# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Coding Memory prompt section for DeepAgent."""
from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

CODING_MEMORY_READ_ONLY_CN = """# coding memory（只读）
位于 `{memory_dir}`。用 coding_memory_read 读取。不允许写入。
"""

CODING_MEMORY_READ_ONLY_EN = """# coding memory (read-only)
At `{memory_dir}`. Use coding_memory_read to read. No writing allowed.
"""

CODING_MEMORY_PROMPT_CN = """# coding memory

你有一个基于文件的持久化记忆系统，位于 `{memory_dir}`。该目录已存在，直接用 coding_memory_write 写入。
用户要求记住则立即保存；要求忘记则找到并删除。

## 记忆类型

| 类型 | 存什么 | 何时保存 |
|------|--------|---------|
| user | 用户的角色、目标、技术背景、偏好 | 了解到用户身份或偏好时 |
| feedback | 用户对工作方式的纠正或认可 | 用户说"不要这样做"或确认某做法有效时。包含原因 |
| project | 项目的截止日期、决策背景等不能从代码推导的信息 | 了解到谁在做什么、为什么。相对日期转绝对日期 |
| reference | 外部系统的指针（Jira、Grafana、Slack 等） | 了解到外部资源位置时 |

feedback 和 project 类型的内容结构：规则/事实 → **原因：** → **如何应用：**

**示例：**
> user: 测试不要 mock 数据库，上次被坑了
> → [保存 feedback：集成测试必须连真实数据库。原因：mock/prod 差异掩盖了迁移问题]
>
> user: 周四后冻结非关键合并，移动端要切 release
> → [保存 project：合并冻结从 2026-04-10 开始。原因：移动端 release 切分支]

## 不应保存的内容

- 代码模式、架构、文件路径、项目结构（从代码可推导）
- Git 历史、最近改动（git log/blame 是权威来源）
- 调试方案（修复在代码中，上下文在 commit message 中）
- 已在项目文档中记录的内容
- 临时任务细节、当前会话上下文

## 如何保存和更新记忆

- **新建记忆**：用 `coding_memory_write` 写入独立 .md 文件，必须包含 frontmatter：

      ---
      name: 记忆名称
      description: 一行描述，要具体
      type: user | feedback | project | reference
      ---

      记忆内容

- **编辑已有记忆**：用 `coding_memory_edit` 精确替换记忆文件中的指定文本（old_text → new_text）
- 写入前先查看上方"已加载的相关记忆"中是否已有可更新的条目，避免重复
- 系统自动索引，无需手动维护

## 访问记忆

- 记忆可能相关时，或用户提及之前的工作时，主动检索
- 用户要求回忆时**必须**访问
- 记忆可能过时——引用记忆中的文件/函数/标志前，先验证它们是否仍然存在
- 用户要求忽略记忆时，当作无记忆处理
"""

CODING_MEMORY_PROMPT_EN = """# coding memory

You have a persistent, file-based memory system at `{memory_dir}`. Write directly with coding_memory_write.
User asks to remember → save immediately. User asks to forget → find and remove.

## Types of memory

| Type | What to store | When to save |
|------|--------------|--------------|
| user | Role, goals, technical background, preferences | When you learn about user identity or preferences |
| feedback | Corrections or confirmations of your approach | "don't do X" or confirms approach worked. Include why |
| project | Deadlines, decisions not derivable from code | Who does what, why. Relative dates → absolute |
| reference | Pointers to external systems (Jira, Grafana, Slack) | When you learn about external resource locations |

feedback/project content structure: rule/fact → **Why:** → **How to apply:**

**Examples:**
> user: don't mock DB in tests, got burned last time
> → [save feedback: must hit real DB. Why: mock/prod divergence masked broken migration]
>
> user: freeze merges after Thursday, mobile cutting release
> → [save project: merge freeze 2026-04-10. Why: mobile release branch cut]

## What NOT to save
- Code patterns, architecture, file paths (derivable from code)
- Git history (git log/blame is authoritative)
- Debug solutions (fix in code, context in commit msg)
- Already documented content; ephemeral task details

## How to save and update memories
- **Create**: Write .md file via `coding_memory_write` with frontmatter:

      ---
      name: memory name
      description: one-line, be specific
      type: user | feedback | project | reference
      ---
      memory content

- **Edit existing**: Use `coding_memory_edit` to replace specific text (old_text → new_text)
- Before writing, check the "Loaded relevant memories" section above for existing entries to update
- Auto-indexed, no manual maintenance needed

## Accessing memories
- Proactively search when relevant or user references prior work
- **Must** access when user asks to recall
- Verify stale memories (files/functions may no longer exist)
- If user says ignore memories, proceed as if none exist
"""


def build_coding_memory_section(language="cn", read_only=False, memory_dir="coding_memory/"):
    if read_only:
        tpl = CODING_MEMORY_READ_ONLY_CN if language == "cn" else CODING_MEMORY_READ_ONLY_EN
    else:
        tpl = CODING_MEMORY_PROMPT_CN if language == "cn" else CODING_MEMORY_PROMPT_EN
    return PromptSection(name=SectionName.MEMORY, content={language: tpl.format(memory_dir=memory_dir)}, priority=85)

__all__ = [
    "build_coding_memory_section",
]
