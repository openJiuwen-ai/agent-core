# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Coding memory prompt section for DeepAgent."""
from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


CODING_MEMORY_PROMPT_CN = """# Coding Memory 使用策略

Coding memory 位于 `{memory_dir}`。Coding memory 内容不会自动注入系统提示词。涉及用户长期偏好、过往反馈、项目背景、外部系统引用、之前的工程决策或需要继续过去工作时，先调用 coding memory 工具获取事实，再回答或行动。

## 记忆类型

| 类型 | 记录内容 | 何时保存 |
|------|----------|----------|
| user | 用户角色、目标、技术背景、长期偏好 | 了解到用户身份、目标或稳定偏好时 |
| feedback | 用户对工作方式的纠正或认可 | 用户说“不要这样做”、指出问题或确认某种做法有效时 |
| project | 不能从代码直接推导的项目背景、截止日期、决策原因 | 了解到项目为什么这样做、谁负责、时间约束或历史决策时 |
| reference | 外部系统位置或使用线索 | 了解到 Jira、Grafana、Slack、文档、服务入口等外部资源时 |

`feedback` 和 `project` 类型的内容建议包含：规则/事实、原因、如何应用。

## 获取记忆

- 不要假设 coding memory 正文已经在上下文中。
- 当记忆可能相关，或用户提到之前的工作、上次反馈、历史决策、项目背景时，先读取相关记忆。
- 已知具体记忆文件时，调用 `coding_memory_read`。
- 当前用户指令优先于历史记忆；如果历史记忆较旧或与当前指令冲突，先按当前指令执行，并在需要时说明记忆可能已过期。
- 用户要求忽略记忆时，按没有相关记忆处理。

## 保存和更新记忆

- 新建记忆时，使用 `coding_memory_write` 写入独立 `.md` 文件，并包含 frontmatter：

      ---
      name: 记忆名称
      description: 一行具体描述
      type: user | feedback | project | reference
      ---

      记忆内容

- 修改已有记忆时，先用 `coding_memory_read` 读取原文，再用 `coding_memory_edit` 精确替换指定文本。
- 写入前先判断是否已有可更新的记忆，避免重复记录。
- 写入后系统会维护索引，不需要手动维护 `MEMORY.md`。

## 不应保存

- 可从代码、项目文件、README、文档或配置直接推导的信息。
- Git 历史、最近改动、调试过程或临时任务细节。
- 已经在项目文档中明确记录的信息。
- 敏感信息、用户不希望保存的信息、无长期价值的过程细节。

## 写入冲突处理

如果 `coding_memory_write` 返回 `conflict_detected: true` 或 `conflicting_files`：
- 使用 `coding_memory_read` 读取冲突文件。
- 使用 `coding_memory_edit` 更新已有记忆，或删除/替换过时内容。
- 不要在未检查冲突内容的情况下继续追加重复记忆。

## 只读约束

如果当前是定时任务或心跳任务，或者用户明确要求不写入记忆：
- 只允许读取 coding memory；
- 禁止调用 `coding_memory_write` / `coding_memory_edit`；
- 禁止写入或修改任何 coding memory 文件。
"""

CODING_MEMORY_PROMPT_EN = (
    "# Coding Memory Usage Policy\nCoding memory root: {memory_dir}. Content is not preloaded. For "
    "long-term preferences, feedback, background/references, earlier decisions or continuing work, "
    "retrieve relevant memory first (coding_memory_read for a known file). Current instructions "
    "override old memory; mention staleness when useful. Honor requests to ignore memory.\n- Types: "
    "user=identity/goals/stable preferences; feedback=corrections/confirmed working styles; "
    "project=background/owners/deadlines/decision reasons not derivable from code; reference=external"
    " resource locations/usage. Feedback/project should include fact/rule, reason and application.\n- "
    "Create standalone .md with coding_memory_write and frontmatter fields name, description, type "
    "(user|feedback|project|reference). Check for an existing memory to update first; read exact "
    "content before coding_memory_edit. The system maintains MEMORY.md/index; never edit it manually."
    "\nFrontmatter example:\n```yaml\n---\nname: deployment notes\ndescription: deployment constraints\n"
    "type: project\n---\n```\n- Do not save code/docs/config-derived information, Git history, recent "
    "changes/debugging/transient process details, duplicates, unwanted or low-long-term-value "
    "information. Do not record sensitive information unless the host's explicit memory policy allows"
    " it.\n- On conflict_detected/conflicting_files, read the conflicting file and edit/replace "
    "outdated content instead of appending duplicates.\n- Scheduled/heartbeat runs or explicit "
    "no-memory-write requests are read-only: no memory write/edit tool or direct file modification.\n"
)


def build_coding_memory_section(language="cn", read_only=False, memory_dir="coding_memory/"):
    """Build a stable coding memory prompt section.

    ``read_only`` is kept for API compatibility; the prompt itself contains
    read-only rules and does not change by run mode.
    """
    tpl = CODING_MEMORY_PROMPT_CN if language == "cn" else CODING_MEMORY_PROMPT_EN
    return PromptSection(
        name=SectionName.MEMORY,
        content={language: tpl.format(memory_dir=memory_dir)},
        priority=85,
        category="memory",
    )


__all__ = [
    "build_coding_memory_section",
]
