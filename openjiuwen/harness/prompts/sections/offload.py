# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context offload prompt section for DeepAgent."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


OFFLOAD_HINT_CN = (
    "# 上下文卸载\n\n"
    "当上下文中的部分内容被卸载到文件系统时，会标记为：\n"
    "[[OFFLOAD: handle=<id>, type=filesystem, path=<path>]]\n\n"
    "这表示完整原始内容已保存到 marker 中的 path。"
    "如需使用完整内容，请根据该 path 获取原始文件内容。"
    "请勿猜测或编造缺失的内容。"
)

OFFLOAD_HINT_EN = (
    "# Context Offload\n\n"
    "When part of the context is offloaded to the filesystem, it is marked as:\n"
    "[[OFFLOAD: handle=<id>, type=filesystem, path=<path>]]\n\n"
    "This means the complete original content was saved at the path in the marker. "
    "If the complete content is needed, use that path to obtain the original file content. "
    "Do not guess or fabricate missing content."
)


def build_offload_section(
        language: str = "cn",
) -> "PromptSection":
    """Build a PromptSection for context offload hints."""
    from openjiuwen.harness.prompts.builder import PromptSection

    hint = OFFLOAD_HINT_CN if language == "cn" else OFFLOAD_HINT_EN

    return PromptSection(
        name="offload",
        content={language: hint},
        priority=90,
    )
