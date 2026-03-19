# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for per-tool bilingual descriptions and get_tool_description."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.deepagents.tools.shell import BashTool
from openjiuwen.deepagents.tools.code import CodeTool
from openjiuwen.deepagents.tools.filesystem import ReadFileTool

from openjiuwen.deepagents.prompts.sections.tools import get_tool_description
from openjiuwen.deepagents.prompts.sections.tools.bash import DESCRIPTION as BASH_DESCRIPTION
from openjiuwen.deepagents.prompts.sections.tools.code import DESCRIPTION as CODE_DESCRIPTION
from openjiuwen.deepagents.prompts.sections.tools.list_skill import DESCRIPTION as LIST_SKILL_DESCRIPTION
from openjiuwen.deepagents.prompts.sections.tools.filesystem import (
    READ_FILE_DESCRIPTION,
    WRITE_FILE_DESCRIPTION,
    EDIT_FILE_DESCRIPTION,
    GLOB_DESCRIPTION,
    LIST_DIR_DESCRIPTION,
    GREP_DESCRIPTION,
)
from openjiuwen.deepagents.prompts.sections.tools.todo import (
    TODO_CREATE_DESCRIPTION,
    TODO_LIST_DESCRIPTION,
    TODO_MODIFY_DESCRIPTION,
)


class TestBilingualDescriptions:
    """Each description dict must have both 'cn' and 'en' keys with non-empty values."""

    @staticmethod
    def test_bash_description():
        assert BASH_DESCRIPTION["cn"]
        assert BASH_DESCRIPTION["en"]

    @staticmethod
    def test_code_description():
        assert CODE_DESCRIPTION["cn"]
        assert CODE_DESCRIPTION["en"]

    @staticmethod
    def test_list_skill_description():
        assert LIST_SKILL_DESCRIPTION["cn"]
        assert LIST_SKILL_DESCRIPTION["en"]

    @staticmethod
    def test_filesystem_descriptions():
        for desc in (
            READ_FILE_DESCRIPTION,
            WRITE_FILE_DESCRIPTION,
            EDIT_FILE_DESCRIPTION,
            GLOB_DESCRIPTION,
            LIST_DIR_DESCRIPTION,
            GREP_DESCRIPTION,
        ):
            assert desc["cn"], f"Missing cn for {desc}"
            assert desc["en"], f"Missing en for {desc}"

    @staticmethod
    def test_todo_descriptions():
        for desc in (TODO_CREATE_DESCRIPTION, TODO_LIST_DESCRIPTION, TODO_MODIFY_DESCRIPTION):
            assert desc["cn"].strip()
            assert desc["en"].strip()


class TestGetToolDescription:
    @staticmethod
    def test_known_tool_cn():
        assert get_tool_description("bash", "cn") == BASH_DESCRIPTION["cn"]

    @staticmethod
    def test_known_tool_en():
        assert get_tool_description("bash", "en") == BASH_DESCRIPTION["en"]

    @staticmethod
    def test_unknown_tool_raises():
        with pytest.raises(KeyError):
            get_tool_description("nonexistent", "cn")

    @staticmethod
    def test_all_registered_tools():
        names = [
            "bash", "code", "read_file", "write_file", "edit_file",
            "glob", "list_files", "grep", "list_skill",
            "todo_write", "todo_read", "todo_modify",
        ]
        for name in names:
            assert get_tool_description(name, "cn"), f"Missing cn for {name}"
            assert get_tool_description(name, "en"), f"Missing en for {name}"

    @staticmethod
    def test_default_language_is_cn():
        assert get_tool_description("bash") == BASH_DESCRIPTION["cn"]


class TestToolClassesUseBilingualDescriptions:
    """Verify tool classes pick up descriptions from the centralized registry."""

    @staticmethod
    def test_bash_tool_cn():
        tool = BashTool(MagicMock(), language="cn")
        assert tool.card.description == BASH_DESCRIPTION["cn"]

    @staticmethod
    def test_bash_tool_en():
        tool = BashTool(MagicMock(), language="en")
        assert tool.card.description == BASH_DESCRIPTION["en"]

    @staticmethod
    def test_code_tool_en():
        tool = CodeTool(MagicMock(), language="en")
        assert tool.card.description == CODE_DESCRIPTION["en"]

    @staticmethod
    def test_read_file_tool_en():
        tool = ReadFileTool(MagicMock(), language="en")
        assert tool.card.description == READ_FILE_DESCRIPTION["en"]

    @staticmethod
    def test_default_language_is_cn():
        tool = BashTool(MagicMock())
        assert tool.card.description == BASH_DESCRIPTION["cn"]
