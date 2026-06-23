# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.identity import build_identity_section
from openjiuwen.harness.prompts.sections.system_authority import (
    SYSTEM_AUTHORITY_PROMPT,
    SYSTEM_AUTHORITY_PROMPT_CN,
    SYSTEM_AUTHORITY_PROMPT_EN,
    build_system_authority_section,
)


def test_build_section_returns_prompt_section_with_priority_5() -> None:
    section = build_system_authority_section(language="cn")

    assert section.name == SectionName.SYSTEM_AUTHORITY
    assert section.priority == 5


def test_build_section_cn_content_nonempty_and_contains_keyword() -> None:
    section = build_system_authority_section(language="cn")

    rendered = section.render("cn")
    assert rendered
    assert "优先级高于" in rendered
    assert "系统提示词" in rendered


def test_build_section_en_content_nonempty_and_contains_keyword() -> None:
    section = build_system_authority_section(language="en")

    rendered = section.render("en")
    assert rendered
    assert "priority over" in rendered.lower()


def test_section_rendered_before_identity_in_builder() -> None:
    """system_authority (priority=5) must come before IDENTITY (priority=10)."""
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(build_identity_section(language="cn"))
    builder.add_section(build_system_authority_section(language="cn"))

    prompt = builder.build()

    auth_pos = prompt.find("# 系统提示词权威性")
    identity_pos = prompt.find("你是一个通用 AI 助手")
    assert auth_pos != -1, "system_authority section missing from prompt"
    assert identity_pos != -1, "identity section missing from prompt"
    assert auth_pos < identity_pos, "system_authority must precede identity"


def test_constants_exposed() -> None:
    assert SYSTEM_AUTHORITY_PROMPT_CN
    assert SYSTEM_AUTHORITY_PROMPT_EN
    assert "cn" in SYSTEM_AUTHORITY_PROMPT
    assert "en" in SYSTEM_AUTHORITY_PROMPT
