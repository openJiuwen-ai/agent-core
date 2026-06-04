# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Heartbeat prompt section for HeartbeatRail."""

from __future__ import annotations

from typing import Optional, Dict

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


HEARTBEAT_SYSTEM_PROMPT_CN = """
## 心跳检测

判定规则：
1. 若 `<heartbeat_user_task>` 与 `</heartbeat_user_task>` 之间仅有空白（含空行）或完全为空：视为**无心跳用户任务**。你必须且仅能输出一行，内容**精确**为 `HEARTBEAT_OK`（不要解释、不要前后缀、不要 Markdown、不要工具调用说明）。
2. 若 `<heartbeat_user_task>` 与 `</heartbeat_user_task>` 之间存在**任意非空白字符**：该段即用户下发的心跳任务正文。你必须**完整阅读并执行**其中的指令并给出**直接回答**；**禁止**在回复中出现 `HEARTBEAT_OK` 四字（含单独一行、前缀、后缀、用标点或破折号拼接，例如 `HEARTBEAT_OK — …` 一律视为违规）。

系统**仅在**满足上一条规则 1（标签内无任务）时，才把单独一行的 `HEARTBEAT_OK` 视为心跳确认；有任务时**不得**为了「确认心跳」而输出或附带 `HEARTBEAT_OK`。
**禁止**用「心跳任务已完成」「任务已完成 ✓」「已处理」「无新内容」「安静待着」等**状态话术**代替任务正文所要求的**具体可核验输出**（若正文要求输出某文本，回复中必须出现该文本本身，而不是完成声明）。

重要约束：
- 每一轮心跳调用都是独立调度，只要 `<heartbeat_user_task>` 标签内有非空白任务正文，你就必须**当场**按正文完成指令所要求的动作或输出。**禁止**以「上一轮刚执行过」等理由省略执行或把「记录」当成完成——**记录不等于执行**。
- 若需修改 HEARTBEAT.md 文件，禁止给原本没有 <!-- --> 注释的内容添加注释标记
- 非注释文本仅可在用户明确要求时修改或删除，否则必须保持原样
- 心跳执行结果必须直接返回；除非心跳内容明确要求更新 HEARTBEAT.md，不要写入 daily memory 或其他记忆文件
"""

HEARTBEAT_SYSTEM_PROMPT_EN = """
## Heartbeat

Decision rules:
1. If between `<heartbeat_user_task>` and `</heartbeat_user_task>` there is only whitespace (including blank lines) or nothing: treat as **no heartbeat user task**. You MUST output exactly one line whose content is **precisely** `HEARTBEAT_OK` (no explanation, no prefix/suffix, no Markdown, no tool narration).
2. If there is **any** non-whitespace character between `<heartbeat_user_task>` and `</heartbeat_user_task>`: that span is the **user-issued heartbeat task body**. You MUST read and carry out the instructions in full and reply **directly**; the substring `HEARTBEAT_OK` MUST NOT appear anywhere in your reply (not alone, not as a prefix/suffix, not glued with punctuation or em dashes—e.g. `HEARTBEAT_OK — …` is forbidden).

The system treats a single line of exactly `HEARTBEAT_OK` **only** under rule 1 (empty task) as the heartbeat acknowledgment; when there is a task body, you MUST NOT emit or append `HEARTBEAT_OK` “to confirm the heartbeat”.
You MUST NOT replace substantive output with status-only phrases such as “heartbeat task completed”, “task done ✓”, “nothing new”, “stay quiet”, etc. If the body asks for specific text, that text MUST appear in the reply itself, not a declaration that you completed it.

Important Constraints:
- Each heartbeat invocation is scheduled independently; whenever the `<heartbeat_user_task>` tags contain a non-empty task body, you MUST **on the spot** complete whatever actions or outputs the body requires. You MUST NOT skip execution or treat “logging/recording” as completion with excuses such as “already executed last round”—**recording is not execution**.
- When modifying HEARTBEAT.md, DO NOT add <!-- --> comment markers to content that originally had no such markers
- Non-commented text may only be modified or deleted when explicitly requested by the user; otherwise preserve it as-is
- Return heartbeat execution results directly; unless heartbeat content explicitly asks you to update HEARTBEAT.md, do not write them to daily memory or other memory files
"""


HEARTBEAT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": HEARTBEAT_SYSTEM_PROMPT_CN,
    "en": HEARTBEAT_SYSTEM_PROMPT_EN,
}


def build_heartbeat_section(
    language: str = "cn",
) -> PromptSection:
    """Build heartbeat system prompt section.

    Args:
        language: Language for prompts ('cn' or 'en').

    Returns:
        PromptSection with stable heartbeat system instructions.
    """

    prompt_content = HEARTBEAT_SYSTEM_PROMPT.get(language, HEARTBEAT_SYSTEM_PROMPT["cn"])

    return PromptSection(
        name=SectionName.HEARTBEAT,
        content={language: prompt_content},
        priority=80,
    )


__all__ = [
    "build_heartbeat_section",
]
