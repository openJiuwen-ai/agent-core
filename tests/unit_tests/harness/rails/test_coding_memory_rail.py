# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodingMemoryRail 与个人 MemoryRail 的 prompt section 隔离测试。

两个 rail 都可能在 code 模式同时挂载，且都在每次 model call 前先 remove 再
add 自己的段落。历史上二者共用 ``SectionName.MEMORY``，后运行的个人
MemoryRail 会删掉 CodingMemoryRail 注入的编码记忆策略与 frontmatter 模板。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.rails.memory.coding_memory_rail import (
    CODING_MEMORY_SECTION_NAME,
    CodingMemoryRail,
)
from openjiuwen.harness.rails.memory.memory_rail import MemoryRail


class _RecordingBuilder:
    """记录 add/remove 调用的最小 builder 替身。"""

    def __init__(self) -> None:
        self.language = "en"
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section) -> None:
        self.added_sections.append(section)

    def remove_section(self, section_name) -> None:
        self.removed_sections.append(section_name)


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(inputs=None, agent=None)


def _make_coding_rail() -> CodingMemoryRail:
    return CodingMemoryRail(
        coding_memory_dir="coding_memory",
        embedding_config=Mock(),
        language="en",
    )


def _make_memory_rail() -> MemoryRail:
    return MemoryRail(embedding_config=Mock())


def _bind(rails, builder) -> None:
    for rail in rails:
        rail.system_prompt_builder = builder


@pytest.mark.asyncio
@pytest.mark.parametrize("coding_first", [True, False])
async def test_coding_and_personal_memory_sections_coexist(coding_first: bool) -> None:
    """无论哪个 rail 后运行，两段策略都必须同时出现在系统提示里。"""
    builder = SystemPromptBuilder(language="en")
    coding = _make_coding_rail()
    memory = _make_memory_rail()
    _bind([coding, memory], builder)

    first, second = (coding, memory) if coding_first else (memory, coding)
    await first.before_model_call(_make_ctx())
    await second.before_model_call(_make_ctx())

    prompt = builder.build()
    assert "# Coding Memory Usage Policy" in prompt
    assert "# Memory Usage Policy" in prompt
    # CodingMemoryRail 的 frontmatter 契约模板不能被个人记忆策略覆盖。
    assert "type: user | feedback | project | reference" in prompt


@pytest.mark.asyncio
async def test_coding_memory_rail_removes_only_its_own_section() -> None:
    builder = _RecordingBuilder()
    coding = _make_coding_rail()
    coding.system_prompt_builder = builder

    await coding.before_model_call(_make_ctx())

    assert builder.removed_sections == [CODING_MEMORY_SECTION_NAME]
    assert [section.name for section in builder.added_sections] == [
        CODING_MEMORY_SECTION_NAME
    ]
    assert CODING_MEMORY_SECTION_NAME != "memory"


@pytest.mark.asyncio
async def test_uninit_keeps_personal_memory_section() -> None:
    builder = SystemPromptBuilder(language="en")
    memory = _make_memory_rail()
    coding = _make_coding_rail()
    _bind([memory, coding], builder)

    await memory.before_model_call(_make_ctx())
    await coding.before_model_call(_make_ctx())
    assert builder.has_section("memory")
    assert builder.has_section(CODING_MEMORY_SECTION_NAME)

    coding.uninit(SimpleNamespace())

    assert builder.has_section("memory"), "uninit 不得删除个人 MemoryRail 的段落"
    assert not builder.has_section(CODING_MEMORY_SECTION_NAME)
