# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Identity section for DeepAgent system prompt."""
from __future__ import annotations

from typing import Dict

from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName

IDENTITY: Dict[str, str] = {
    "cn": (
        "你是一个通用 AI 助手。请根据用户的需求，合理使用可用工具完成任务。\n"
        "在执行过程中保持目标聚焦，遇到问题时尝试不同策略。\n"
        "重要提示：如果某个工具返回无结果、空输出或错误，\n"
        "切勿使用相同参数重复调用同一工具。\n"
        "请根据对话上下文中已有的工具调用记录，尝试不同的工具或方法。"
    ),
    "en": (
        "You are a general-purpose AI assistant. Use available tools to complete tasks based on user needs.\n"
        "Stay focused on the goal during execution and try different strategies when encountering problems.\n"
        "Important: if a tool returns no results, empty output, or an error,\n"
        "do NOT call the same tool with identical arguments again.\n"
        "Refer to the tool-call history already visible in the conversation context and try a different tool or "
        "approach."
    ),
}


def build_identity_section(language: str = "cn") -> PromptSection:
    """Build the identity prompt section."""
    _ = language
    return PromptSection(
        name=SectionName.IDENTITY,
        content=IDENTITY,
        priority=10,
    )
