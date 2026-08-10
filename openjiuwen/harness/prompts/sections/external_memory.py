# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External memory prompt section constants and helpers."""

from typing import Mapping

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


def build_external_memory_section(
    prompt_block: str | Mapping[str, str] | None,
    language: str = "cn",
) -> PromptSection | None:
    if not prompt_block:
        return None
    if isinstance(prompt_block, Mapping):
        content = prompt_block.get(language) or prompt_block.get("cn") or prompt_block.get("en")
    else:
        content = prompt_block
    if not content:
        return None
    return PromptSection(
        name=SectionName.EXTERNAL_MEMORY,
        content={language: content},
        priority=55,
    )
