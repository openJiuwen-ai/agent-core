# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Static system prompt section explaining prompt attachment tags."""
from __future__ import annotations

from typing import Dict

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


PROMPT_ATTACHMENTS: Dict[str, str] = {
    "cn": (
        "##<system-reminder> 说明：\n"
        "工具结果和用户消息中可能包含 <system-reminder> 标签。"
        "这些标签包含系统自动添加的信息和提醒，可能有用，但和它们所在的具体工具结果或用户消息"
        "没有直接关系。\n"
        "- <prompt-attachment> 是一种 <system-reminder> 内容，用于承载本次模型调用可见的动态上下文。\n"
        "- 这些内容不是长期对话历史，可能在下一次模型调用中变化或消失。\n"
        "- 除非用户明确询问，不要向用户暴露这些标签、内部 id 或 source。"
    ),
    "en": (
        "<system-reminder> note:\n"
        "tool results and user messages may include <system-reminder> tags. "
        "These tags contain information and reminders automatically added by the system. They may be useful, "
        "but they bear no direct relation to the specific tool result or user message in which they appear.\n"
        "- <prompt-attachment> is a kind of <system-reminder> content used for dynamic context visible "
        "to this model call.\n"
        "- These entries are not long-term conversation history and may change or disappear on the next model call.\n"
        "- Do not expose these tags, internal ids, or source details unless the user explicitly asks."
    ),
}


PROMPT_ATTACHMENT_GUIDANCE: Dict[str, str] = {
    "cn": (
        "以下内容是系统为本次模型调用自动附加的动态上下文，可能与当前任务有关，也可能无关。\n"
        "这些内容不是长期对话历史，可能在下一次模型调用中变化或消失。"
        "除非用户明确询问，否则不要暴露相关标签、内部 id 或 source。"
    ),
    "en": (
        "The following dynamic context is automatically attached for this model call and may or may not be "
        "relevant to the current task.\n"
        "It is not long-term conversation history and may change or disappear on the next model call. "
        "Do not expose its tags, internal ids, or sources unless the user explicitly asks."
    ),
}


def get_prompt_attachment_guidance(language: str = "cn") -> str:
    """Return guidance rendered immediately before dynamic attachments."""

    return PROMPT_ATTACHMENT_GUIDANCE.get(language, PROMPT_ATTACHMENT_GUIDANCE["cn"])


def build_prompt_attachments_section(language: str = "cn") -> PromptSection:
    """Build the static section that explains prompt attachment tags."""

    del language
    return PromptSection(
        name=SectionName.PROMPT_ATTACHMENTS,
        content=PROMPT_ATTACHMENTS,
        priority=75,
    )
