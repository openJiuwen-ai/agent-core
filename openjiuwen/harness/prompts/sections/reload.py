# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offload prompt section for DeepAgent."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


RELOAD_HINT_CN = (
    "# 上下文压缩与恢复\n\n"
    "上下文过长时会被自动压缩，并使用以下 marker 标记：\n\n"
    "```text\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n"
    "```\n\n"
    "需要被隐藏的原始内容时：\n\n"
    "- marker 包含 `path` 时，使用其精确值作为 `read_file.file_path`。\n"
    "- `handle` 是内容标识，不是文件路径；不要把它当成 `file_path`。\n"
    "- marker 没有 `path` 时，不要猜测或拼接路径，应说明无法通过 `read_file` 精确恢复。\n"
    "- 只需定位特定内容时，优先搜索相关片段，不要盲目读取整个文件。\n\n"
    "`filesystem` 表示内容已经持久化到 `path`；"
    "`in_memory` 表示内容只在会话缓存中，没有 `path` 时无法通过 `read_file` 恢复。"
)

RELOAD_HINT_EN = (
    "# Context Compression and Recovery\n\n"
    "When the context becomes too long, it may be compressed and marked with:\n\n"
    "```text\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n"
    "```\n\n"
    "When hidden original content is needed:\n\n"
    "- If the marker contains `path`, use its exact value as `read_file.file_path`.\n"
    "- `handle` identifies content and is not a file path; do not use it as `file_path`.\n"
    "- If the marker has no `path`, do not guess or construct one; explain that `read_file` cannot recover it "
    "precisely.\n"
    "- When only specific content is needed, search for relevant portions instead of reading the entire file.\n\n"
    "`filesystem` means the content is persisted at `path`; `in_memory` means it exists only in the session cache "
    "and cannot be recovered with `read_file` when no `path` is present."
)


def build_reload_section(
        language: str = "cn",
) -> "PromptSection":
    """Build a PromptSection for context offload hints."""
    from openjiuwen.harness.prompts.builder import PromptSection

    hint = RELOAD_HINT_CN if language == "cn" else RELOAD_HINT_EN

    return PromptSection(
        name="offload",
        content={language: hint},
        priority=90,
    )
