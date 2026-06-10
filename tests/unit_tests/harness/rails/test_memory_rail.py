# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.rails.memory.memory_rail import MemoryRail


class _PromptBuilder:
    def __init__(self) -> None:
        self.language = "en"
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section) -> None:
        self.added_sections.append(section)

    def remove_section(self, section_name) -> None:
        self.removed_sections.append(section_name)


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(session_id="sess1"),
        inputs=None,
        extra={},
    )


def _make_rail(*, read_only: bool) -> MemoryRail:
    rail = MemoryRail(embedding_config=Mock())
    rail.system_prompt_builder = _PromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()
    rail._is_read_only = read_only
    return rail


@pytest.mark.asyncio
async def test_memory_policy_uses_system_section_for_normal_invokes() -> None:
    rail = _make_rail(read_only=False)

    await rail.before_model_call(_make_ctx())

    assert rail.system_prompt_builder.removed_sections == ["memory"]
    assert [section.name for section in rail.system_prompt_builder.added_sections] == ["memory"]
    assert await rail.attachment_manager.collect_for_session("sess1") == []


@pytest.mark.asyncio
async def test_memory_policy_uses_attachment_for_read_only_invokes() -> None:
    rail = _make_rail(read_only=True)

    await rail.before_model_call(_make_ctx())

    assert rail.system_prompt_builder.removed_sections == ["memory"]
    assert rail.system_prompt_builder.added_sections == []
    items = await rail.attachment_manager.collect_for_session("sess1")
    assert [item.id for item in items] == ["session.sess1.memory"]
    assert items[0].source == "agent_core.memory.policy"
