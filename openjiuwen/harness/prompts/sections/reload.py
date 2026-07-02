# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reload prompt section for DeepAgent - context offload hint."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


RELOAD_HINT_CN = (
    "# 上下文压缩\n\n"
    "你的上下文在过长时会被自动压缩，并以如下 marker 标记：\n\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    "当你看到这类 marker，并且回答问题需要被隐藏的原始内容时，"
    "优先调用 reload_original_context_messages 工具恢复原始消息。\n\n"
    "调用规则：\n"
    "- offload_handle 必须使用 marker 中 handle= 后面的精确值。\n"
    "- offload_type 必须使用 marker 中 type= 后面的精确值。\n"
    "- path 只是文件存储位置提示，不是 offload_handle；不要把 path 传给 offload_handle。\n"
    "- 如果 marker 没有 handle 字段，不要猜测 handle，也不要从 path 中自行推断；应说明无法通过 reload 精确恢复。\n\n"
    "示例：看到 [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.json]] 时，"
    "应调用 reload_original_context_messages(offload_handle=\"abc123\", offload_type=\"filesystem\")。\n\n"
    "请勿猜测或编造缺失的内容。\n\n"
    '存储类型："in_memory"（会话缓存）、"filesystem"（文件系统持久化内容）。'
)

RELOAD_HINT_EN = (
    "# Context Compression\n\n"
    "Your context may be automatically compressed when it becomes too long "
    "and marked with one of these markers:\n\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    "When you see one of these markers and the hidden original content would help, "
    "prefer calling reload_original_context_messages to restore the original messages.\n\n"
    "Call rules:\n"
    "- offload_handle must be the exact value after handle= in the marker.\n"
    "- offload_type must be the exact value after type= in the marker.\n"
    "- path is only a filesystem location hint, not the offload_handle; do not pass path as offload_handle.\n"
    "- If the marker has no handle field, do not guess or infer it from path; "
    "- Explain that reload cannot precisely restore it.\n\n"
    'Example: for [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.json]], '
    'call reload_original_context_messages(offload_handle="abc123", offload_type="filesystem").\n\n'
    "Do not guess or fabricate missing content.\n\n"
    'Storage types: "in_memory" (session cache), "filesystem" (persistent filesystem content).'
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