# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External memory prompt section constants and helpers."""

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName


def build_external_memory_section(prompt_block: str, language: str = "cn") -> PromptSection | None:
    if not prompt_block:
        return None
    return PromptSection(
        name=SectionName.EXTERNAL_MEMORY,
        content={language: prompt_block},
        priority=55,
    )