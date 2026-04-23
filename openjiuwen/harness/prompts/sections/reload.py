# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reload prompt section for DeepAgent - context offload hint."""
from __future__ import annotations

from typing import Optional


RELOAD_HINT_CN = (
    "# 上下文压缩\n\n"
    "你的上下文在过长时会被自动压缩，"
    "并标记为[OFFLOAD: handle=<id>, type=<type>]。\n\n"
    "如果你认为需要读取隐藏的内容，"
    "可随时调用reload_original_context_messages工具。\n\n"
    "请勿猜测或编造缺失的内容。\n\n"
    '存储类型："in_memory"（会话缓存）'
)

RELOAD_HINT_EN = (
    "# Context Compression\n\n"
    "Your context will be automatically compressed when it becomes too long "
    "and marked with [OFFLOAD: handle=<id>, type=<type>].\n\n"
    'Call reload_original_context_messages(offload_handle="<id>", '
    'offload_type="<type>"), using the exact values from the marker.\n\n'
    "Do not guess or fabricate missing content.\n\n"
    'Storage types: "in_memory" (session cache)'
)


def build_reload_section(
        language: str = "cn",
) -> "PromptSection":
    """Build a PromptSection for context offload hint.

    Args:
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance with offload hint content.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    hint = RELOAD_HINT_CN if language == "cn" else RELOAD_HINT_EN

    return PromptSection(
        name="offload",
        content={language: hint},
        priority=90,
    )