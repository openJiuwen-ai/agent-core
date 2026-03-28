# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Heartbeat prompt section for HeartbeatRail."""

from __future__ import annotations

from typing import Optional, Dict

from openjiuwen.deepagents.prompts import PromptSection


HEARTBEAT_SYSTEM_PROMPT_CN = """
## Heartbeats
Heartbeats 内容: 
{heartbeat}

如果收到心跳检测消息（用户消息匹配上述心跳提示词），且没有需要处理的事项，请精确回复：
HEARTBEAT_OK

系统会将前导/后缀的 "HEARTBEAT_OK" 视为心跳确认（并可能丢弃它）。

如果有需要处理的事项，请勿包含 "HEARTBEAT_OK"，直接回复提醒文本。
"""


HEARTBEAT_SYSTEM_PROMPT_EN = """
## Heartbeats
Heartbeat content:
{heartbeat}

If you receive a heartbeat poll (a user message matching the heartbeat prompt above), and there is nothing that needs attention, reply exactly:
HEARTBEAT_OK

OpenClaw treats a leading/trailing "HEARTBEAT_OK" as a heartbeat ack (and may discard it).

If something needs attention, do NOT include "HEARTBEAT_OK"; reply with the alert text instead.
"""


HEARTBEAT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": HEARTBEAT_SYSTEM_PROMPT_CN,
    "en": HEARTBEAT_SYSTEM_PROMPT_EN,
}


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

    return PromptSection(
        name="heartbeat",
        content={language: prompt_content.format(heartbeat=heartbeat_content)},
        priority=80,
    )


__all__ = [
    "build_heartbeat_section",
]
