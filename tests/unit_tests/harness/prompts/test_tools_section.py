# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for build_tools_section factory."""
from __future__ import annotations

from openjiuwen.harness.prompts.tools import build_tools_section


class TestBuildToolsSection:
    @staticmethod
    def test_returns_none_when_no_descriptions():
        assert build_tools_section(None) is None
        assert build_tools_section({}) is None

    @staticmethod
    def test_returns_section_with_descriptions():
        descs = {"todo_create": "Create todos", "todo_list": "List todos"}
        s = build_tools_section(descs, language="cn")
        assert s is not None
        assert s.name == "tools"
        assert s.priority == 40
        rendered = s.render("cn")
        assert "todo_create" in rendered
        assert "todo_list" in rendered

    @staticmethod
    def test_en_language():
        descs = {"search": "Search the web"}
        s = build_tools_section(descs, language="en")
        assert s is not None
        rendered = s.render("en")
        assert "Available Tools" in rendered
        assert "search" in rendered

    @staticmethod
    def test_cn_language_header():
        descs = {"tool1": "desc1"}
        s = build_tools_section(descs, language="cn")
        rendered = s.render("cn")
        assert "可用工具" in rendered
