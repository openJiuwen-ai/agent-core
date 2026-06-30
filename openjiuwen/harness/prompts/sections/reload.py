# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reload prompt section for DeepAgent - context offload hint."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


RELOAD_HINT_CN = (
    "# 上下文压缩\n\n"
    "你的上下文在过长时会被自动压缩，"
    "并标记为：\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    "当你看到这类标记，并认为读取它有助于回答时，"
    "可以调用reload_original_context_messages工具，"
    "并使用 marker 中的精确值作为参数。"
    "当 marker 包含 path 时，调用时将 path 作为 offload_handle；"
    "否则将 handle 作为 offload_handle。\n\n"
    "请勿猜测或编造缺失的内容。\n\n"
    '存储类型："in_memory"（会话缓存）、"filesystem"（文件系统持久化内容）'
)

RELOAD_HINT_EN = (
    "# Context Compression\n\n"
    "Your context will be automatically compressed when it becomes too long "
    "and marked with:\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    'If the marker has a path value, call reload_original_context_messages(offload_handle="<path>", '
    'offload_type="<type>"). Otherwise, call reload_original_context_messages(offload_handle="<id>", '
    'offload_type="<type>"). Use the exact values from the marker.\n\n'
    "Do not guess or fabricate missing content.\n\n"
    'Storage types: "in_memory" (session cache), "filesystem" (filesystem-backed persisted content)'
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
