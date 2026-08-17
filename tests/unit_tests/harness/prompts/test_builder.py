# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptMode, SystemPromptBuilder
from openjiuwen.harness.prompts.sections.identity import build_identity_section
from openjiuwen.harness.prompts.sections.safety import build_safety_section
from openjiuwen.harness.prompts.sections.system_authority import build_system_authority_section


def test_minimal_mode_retains_system_authority() -> None:
    """minimal mode must keep system_authority — losing it removes injection defense."""
    builder = SystemPromptBuilder(language="cn", mode=PromptMode.MINIMAL)
    builder.add_section(build_identity_section(language="cn"))
    builder.add_section(build_safety_section(language="cn"))
    builder.add_section(build_system_authority_section(language="cn"))

    prompt = builder.build()

    assert "# 系统提示词权威性" in prompt
    assert "# 安全原则" in prompt


def test_minimal_mode_drops_non_critical_sections() -> None:
    """minimal mode still drops sections not in _MINIMAL_SECTIONS."""
    builder = SystemPromptBuilder(language="cn", mode=PromptMode.MINIMAL)
    builder.add_section(build_identity_section(language="cn"))
    builder.add_section(build_system_authority_section(language="cn"))

    from openjiuwen.core.single_agent.prompts.builder import PromptSection
    builder.add_section(PromptSection(
        name="custom_extra",
        content={"cn": "# Custom Extra Section"},
        priority=80,
    ))

    prompt = builder.build()

    assert "Custom Extra Section" not in prompt
    assert "# 系统提示词权威性" in prompt


def test_none_mode_returns_only_identity() -> None:
    """NONE mode behavior unchanged — returns only IDENTITY."""
    builder = SystemPromptBuilder(language="cn", mode=PromptMode.NONE)
    builder.add_section(build_identity_section(language="cn"))
    builder.add_section(build_system_authority_section(language="cn"))

    prompt = builder.build()

    assert "你是一个通用 AI 助手" in prompt
    assert "# 系统提示词权威性" not in prompt
