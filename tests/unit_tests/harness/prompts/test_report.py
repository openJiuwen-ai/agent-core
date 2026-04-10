# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for PromptReport and build_report()."""
from __future__ import annotations

from openjiuwen.harness.prompts import (
    PromptMode,
    PromptSection,
    SystemPromptBuilder,
)
from openjiuwen.harness.prompts.report import PromptReport


class TestPromptReport:
    @staticmethod
    def test_from_builder_basic():
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection("identity", {"cn": "你好世界", "en": "Hello"}, priority=10))
        report = PromptReport.from_builder(builder)
        assert report.section_count == 1
        assert report.total_chars == 4  # len("你好世界")
        assert report.mode == "full"
        assert report.language == "cn"
        assert report.estimated_tokens > 0

    @staticmethod
    def test_from_builder_empty():
        builder = SystemPromptBuilder(language="en")
        report = PromptReport.from_builder(builder)
        assert report.section_count == 0
        assert report.total_chars == 0
        assert report.estimated_tokens == 0

    @staticmethod
    def test_from_builder_multiple_sections():
        builder = SystemPromptBuilder(language="en")
        builder.add_section(PromptSection("a", {"cn": "中文A", "en": "EnglishA"}, priority=10))
        builder.add_section(PromptSection("b", {"cn": "中文B", "en": "EnglishBB"}, priority=20))
        report = PromptReport.from_builder(builder)
        assert report.section_count == 2
        assert report.total_chars == len("EnglishA") + len("EnglishBB")

    @staticmethod
    def test_to_dict():
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection("id", {"cn": "身份", "en": "identity"}, priority=10))
        report = PromptReport.from_builder(builder)
        d = report.to_dict()
        assert d["section_count"] == 1
        assert d["mode"] == "full"
        assert d["language"] == "cn"
        assert len(d["sections"]) == 1
        assert d["sections"][0]["name"] == "id"

    @staticmethod
    def test_summary():
        builder = SystemPromptBuilder(language="cn", mode=PromptMode.MINIMAL)
        builder.add_section(PromptSection("identity", {"cn": "身份", "en": "id"}, priority=10))
        report = PromptReport.from_builder(builder)
        s = report.summary()
        assert "mode=minimal" in s
        assert "lang=cn" in s
        assert "sections=1" in s

    @staticmethod
    def test_sections_sorted_by_priority():
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection("b", {"cn": "B", "en": "B"}, priority=20))
        builder.add_section(PromptSection("a", {"cn": "A", "en": "A"}, priority=10))
        report = PromptReport.from_builder(builder)
        assert report.sections[0].name == "a"
        assert report.sections[1].name == "b"

    @staticmethod
    def test_minimal_mode_keeps_tools_section():
        builder = SystemPromptBuilder(language="cn", mode=PromptMode.MINIMAL)
        builder.add_section(PromptSection("identity", {"cn": "身份", "en": "id"}, priority=10))
        builder.add_section(PromptSection("tools", {"cn": "工具\n## task_tool 使用原则", "en": "tools"}, priority=20))
        rendered = builder.build()
        assert "身份" in rendered
        assert "## task_tool 使用原则" in rendered


class TestBuildReport:
    @staticmethod
    def test_build_report_returns_prompt_report():
        builder = SystemPromptBuilder(language="cn")
        builder.add_section(PromptSection("identity", {"cn": "身份", "en": "id"}, priority=10))
        report = builder.build_report()
        assert isinstance(report, PromptReport)
        assert report.section_count == 1

    @staticmethod
    def test_build_report_reflects_dynamic_changes():
        builder = SystemPromptBuilder(language="cn")
        r1 = builder.build_report()
        assert r1.section_count == 0

        builder.add_section(PromptSection("x", {"cn": "X", "en": "X"}, priority=10))
        r2 = builder.build_report()
        assert r2.section_count == 1
