# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for category grouping in PromptReport."""
import pytest

from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.prompts.report import PromptReport
from openjiuwen.harness.prompts.sections.progressive_tool_rail import (
    build_navigation_section,
    build_progressive_tool_rules_section,
)
from openjiuwen.harness.prompts.sections.session_tools import build_session_tools_section
from openjiuwen.harness.prompts.sections.task_tool import build_task_section


def _make_builder(sections) -> SystemPromptBuilder:
    builder = SystemPromptBuilder(language="cn")
    for section in sections:
        builder.add_section(section)
    return builder


def _section(name, priority, content, category=None) -> PromptSection:
    return PromptSection(
        name=name,
        content={"cn": content},
        priority=priority,
        category=category,
    )


@pytest.fixture
def full_builder() -> SystemPromptBuilder:
    return _make_builder([
        _section("identity", 10, "身份" * 10),
        _section("safety", 20, "安全" * 5),
        _section("tools_late", 30, "工具说明" * 5, category="tools"),
        _section("tools_early", 25, "工具清单" * 5, category="tools"),
        _section("skills", 40, "技能" * 5, category="skills"),
        _section("memory", 50, "记忆" * 5, category="memory"),
    ])


def test_categories_grouped_in_fixed_order(full_builder):
    report = PromptReport.from_builder(full_builder)
    assert [c.category for c in report.categories] == [
        "system_prompt", "tools", "skills", "memory",
    ]


def test_sections_without_category_fall_back_to_system_prompt(full_builder):
    report = PromptReport.from_builder(full_builder)
    system_group = report.categories[0]
    assert {s.name for s in system_group.sections} == {"identity", "safety"}


def test_sections_sorted_by_priority_within_category(full_builder):
    report = PromptReport.from_builder(full_builder)
    tools_group = next(c for c in report.categories if c.category == "tools")
    assert [s.name for s in tools_group.sections] == ["tools_early", "tools_late"]
    assert [s.priority for s in tools_group.sections] == [25, 30]


def test_category_char_count_and_estimated_tokens(full_builder):
    report = PromptReport.from_builder(full_builder)
    tools_group = next(c for c in report.categories if c.category == "tools")
    expected_chars = len("工具清单" * 5) + len("工具说明" * 5)
    assert tools_group.char_count == expected_chars
    # cn: 2.5 chars per token
    assert tools_group.estimated_tokens == int(expected_chars / 2.5)
    assert report.total_chars == sum(c.char_count for c in report.categories)


def test_empty_categories_are_omitted():
    builder = _make_builder([
        _section("identity", 10, "身份"),
        _section("tools", 30, "工具", category="tools"),
    ])
    report = PromptReport.from_builder(builder)
    assert [c.category for c in report.categories] == ["system_prompt", "tools"]


def test_display_name_mapping(full_builder):
    report = PromptReport.from_builder(full_builder)
    display_names = {c.category: c.display_name for c in report.categories}
    assert display_names == {
        "system_prompt": "系统提示词",
        "tools": "工具及 MCP",
        "skills": "技能",
        "memory": "记忆",
    }


def test_to_dict_includes_categories(full_builder):
    report = PromptReport.from_builder(full_builder)
    data = report.to_dict()
    assert "categories" in data
    assert [c["category"] for c in data["categories"]] == [
        "system_prompt", "tools", "skills", "memory",
    ]
    tools_entry = next(c for c in data["categories"] if c["category"] == "tools")
    assert tools_entry["display_name"] == "工具及 MCP"
    assert [s["name"] for s in tools_entry["sections"]] == ["tools_early", "tools_late"]
    # Existing fields stay intact.
    for key in ("total_chars", "estimated_tokens", "section_count", "sections", "mode", "language"):
        assert key in data
    assert data["section_count"] == 6


def test_section_info_carries_category(full_builder):
    report = PromptReport.from_builder(full_builder)
    by_name = {s.name: s.category for s in report.sections}
    assert by_name["identity"] == "system_prompt"
    assert by_name["tools_early"] == "tools"
    assert by_name["skills"] == "skills"
    assert by_name["memory"] == "memory"


def test_display_names_follow_report_language():
    builder = SystemPromptBuilder(language="en")
    builder.add_section(_section("tools", 10, "Tools", category="tools"))
    # _section only supplies Chinese content, so use a bilingual section for
    # this language-specific assertion.
    builder.remove_section("tools")
    builder.add_section(PromptSection("tools", {"en": "Tools"}, priority=10, category="tools"))

    report = PromptReport.from_builder(builder)

    assert report.categories[0].display_name == "Tools & MCP"


def test_custom_categories_are_retained_and_serialized():
    builder = _make_builder([_section("custom", 10, "扩展", category="extension")])

    report = PromptReport.from_builder(builder)
    data = report.to_dict()

    assert [category.category for category in report.categories] == ["extension"]
    assert sum(category.char_count for category in report.categories) == report.total_chars
    assert data["sections"][0]["category"] == "extension"
    assert data["categories"][0]["sections"][0]["category"] == "extension"


def test_tool_rules_are_system_prompt_sections():
    sections = [
        build_navigation_section(["read_file"]),
        build_progressive_tool_rules_section(),
        build_session_tools_section(),
        build_task_section(),
    ]

    assert {section.category for section in sections} == {"system_prompt"}
