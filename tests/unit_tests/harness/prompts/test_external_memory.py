# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for external_memory prompt section"""

from __future__ import annotations

import pytest

from openjiuwen.harness.prompts.sections.external_memory import (
    build_external_memory_section,
    EXTERNAL_MEMORY_SECTION_NAME,
)


class TestBuildExternalMemorySection:
    """Test build_external_memory_section function."""

    def test_build_with_valid_prompt_block(self):
        """Test building PromptSection with valid prompt block."""
        prompt_block = "Use memory tools to store and retrieve information."
        section = build_external_memory_section(prompt_block, language="en")

        assert section is not None
        assert section.name == EXTERNAL_MEMORY_SECTION_NAME
        assert section.content.get("en") == prompt_block
        assert section.priority == 55

    def test_build_with_cn_language(self):
        """Scenario 2: Build section with Chinese language."""
        prompt_block = "使用记忆工具存储和检索信息"
        section = build_external_memory_section(prompt_block, language="cn")

        assert section is not None
        assert section.content.get("cn") == prompt_block

    def test_build_with_empty_prompt_block(self):
        """Scenario 2: Build section with empty prompt block returns None."""
        section = build_external_memory_section("", language="en")
        assert section is None

    def test_build_with_none_prompt_block(self):
        """Scenario 2: Build section with None prompt block returns None."""
        section = build_external_memory_section(None, language="en")
        assert section is None