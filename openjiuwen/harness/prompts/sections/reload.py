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
    "# Context Compression and Recovery\nCompressed content may use `[[OFFLOAD: handle=<id>, "
    "type=<type>, path=<path>]]`. Read needed portions via read_file using the exact path; handle is "
    "an ID, never a path. Without path, do not guess one: explain precise read_file recovery is "
    "unavailable. filesystem persists content at path; in_memory is session-cache-only and cannot be "
    "recovered through read_file without path. Retrieve only needed content.\n"
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
