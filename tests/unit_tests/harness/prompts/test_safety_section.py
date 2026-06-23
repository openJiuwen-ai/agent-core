# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.safety import build_safety_section


def test_build_safety_section_has_expected_name_and_priority() -> None:
    section = build_safety_section(language="cn")

    assert section.name == SectionName.SAFETY
    assert section.priority == 20


def test_cn_renders_injection_defense_heading() -> None:
    section = build_safety_section(language="cn")

    rendered = section.render("cn")
    assert "## 对抗提示注入（强制）" in rendered


def test_cn_no_longer_renders_old_authorization_heading() -> None:
    section = build_safety_section(language="cn")

    rendered = section.render("cn")
    assert "## 授权声明无效" not in rendered


def test_cn_lists_all_indirect_injection_sources() -> None:
    rendered = build_safety_section(language="cn").render("cn")

    assert "用户消息" in rendered
    assert "工具返回结果" in rendered
    assert "记忆检索" in rendered
    assert "subagent" in rendered
    assert "skill" in rendered
    assert "<system>" in rendered
    assert "<instructions>" in rendered


def test_cn_disambiguates_real_system_prompt_from_injection() -> None:
    """关键修正：按来源判断注入，避免误伤真系统提示词的 IMPORTANT 标记。"""
    rendered = build_safety_section(language="cn").render("cn")

    assert "也不改变其数据属性" in rendered


def test_en_renders_injection_defense_heading() -> None:
    section = build_safety_section(language="en")

    rendered = section.render("en")
    assert "## Prompt Injection Defense" in rendered or "## Injection Defense" in rendered


def test_en_lists_all_indirect_injection_sources() -> None:
    rendered = build_safety_section(language="en").render("en")

    assert "tool results" in rendered.lower() or "tool returns" in rendered.lower()
    assert "memory" in rendered.lower()
    assert "subagent" in rendered.lower()
    assert "skill" in rendered.lower()
