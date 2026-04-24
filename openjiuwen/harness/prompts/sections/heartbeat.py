# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Heartbeat prompt section for HeartbeatRail."""

from __future__ import annotations

from typing import Optional, Dict

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


HEARTBEAT_SYSTEM_PROMPT_CN = """
## 心跳检测
{heartbeat_section}

当收到心跳检测消息时：
- 若上方无心跳内容，请精确回复：HEARTBEAT_OK
- 若上方有心跳内容，请根据内容判断是否有需要处理的事项：
  - 无事项需要处理，回复：HEARTBEAT_OK
  - 有事项需要处理，直接回复提醒内容（不要包含 HEARTBEAT_OK）

系统会识别 HEARTBEAT_OK 作为心跳确认。

重要约束：
- 若需修改 HEARTBEAT.md 文件，禁止给原本没有 <!-- --> 注释的内容添加注释标记
- 非注释文本仅可在用户明确要求时修改或删除，否则必须保持原样
"""

HEARTBEAT_SYSTEM_PROMPT_EN = """
## Heartbeat
{heartbeat_section}

When you receive a heartbeat message:
- If there is no heartbeat content above, reply exactly: HEARTBEAT_OK
- If there is heartbeat content above, check if anything needs attention:
  - Nothing to handle, reply: HEARTBEAT_OK
  - Something needs attention, reply with the alert content directly (do NOT include HEARTBEAT_OK)

The system recognizes HEARTBEAT_OK as a heartbeat acknowledgment.

Important Constraints:
- When modifying HEARTBEAT.md, DO NOT add <!-- --> comment markers to content that originally had no such markers
- Non-commented text may only be modified or deleted when explicitly requested by the user; otherwise preserve it as-is
"""


HEARTBEAT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": HEARTBEAT_SYSTEM_PROMPT_CN,
    "en": HEARTBEAT_SYSTEM_PROMPT_EN,
}


def _clean_heartbeat_content(content: str) -> str:
    """Clean HEARTBEAT.md content.

    Args:
        content: Raw content from HEARTBEAT.md file.

    Returns:
        Cleaned content with HTML comments and empty lines removed.
    """
    lines = content.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped_line = line.strip()

        if stripped_line.startswith("<!--") and stripped_line.endswith("-->"):
            continue

        if stripped_line:
            cleaned_lines.append(line.strip())

    return "\n".join(cleaned_lines)


def build_heartbeat_section(
    language: str = "cn",
    heartbeat_content: Optional[str] = None,
) -> Optional[PromptSection]:
    """Build heartbeat system prompt section.

    Args:
        language: Language for prompts ('cn' or 'en').
        heartbeat_content: Content from HEARTBEAT.md file.

    Returns:
        PromptSection if heartbeat_content is not None, else None.
    """

    prompt_content = HEARTBEAT_SYSTEM_PROMPT.get(language, HEARTBEAT_SYSTEM_PROMPT["cn"])
    cleaned_content = _clean_heartbeat_content(heartbeat_content) if heartbeat_content else ""

    if cleaned_content:
        heartbeat_section = cleaned_content
    else:
        heartbeat_section = "（无心跳内容）" if language == "cn" else "(No heartbeat content)"

    return PromptSection(
        name=SectionName.HEARTBEAT,
        content={language: prompt_content.format(heartbeat_section=heartbeat_section)},
        priority=80,
    )


__all__ = [
    "build_heartbeat_section",
]
