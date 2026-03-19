# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for prompts manager (ThreadSafePromptManager)"""

import tempfile
from pathlib import Path

import pytest

from openjiuwen.core.memory.graph.extraction.prompts.manager import (
    ThreadSafePromptManager,
)


class TestLoadPrContent:
    """Tests for load_pr_content (static)"""

    @staticmethod
    def test_single_role_and_content():
        """Single role block parses to one message"""
        content = "`#user#`\nHello world."
        messages = ThreadSafePromptManager.load_pr_content(content)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "\nHello world."

    @staticmethod
    def test_system_and_user():
        """System and user blocks parse to two messages"""
        content = "`#system#`\nYou are helpful.\n`#user#`\nHi."
        messages = ThreadSafePromptManager.load_pr_content(content)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "helpful" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "Hi." in messages[1]["content"]

    @staticmethod
    def test_empty_content_returns_empty_list():
        """Empty or whitespace-only content returns empty list"""
        assert ThreadSafePromptManager.load_pr_content("") == []
        assert ThreadSafePromptManager.load_pr_content("   \n  ") == []

    @staticmethod
    def test_assistant_role():
        """Assistant role is parsed"""
        content = "`#assistant#`\nResponse here."
        messages = ThreadSafePromptManager.load_pr_content(content)
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"

    @staticmethod
    def test_tool_role():
        """Tool role is parsed"""
        content = "`#tool#`\nTool result."
        messages = ThreadSafePromptManager.load_pr_content(content)
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"


class TestRegisterInBulk:
    """Tests for register_in_bulk"""

    @staticmethod
    def test_empty_directory_raises():
        """Directory with no .pr.md files raises error"""
        from openjiuwen.core.common.exception.errors import BaseError

        with tempfile.TemporaryDirectory() as tmpdir:
            inst = ThreadSafePromptManager()
            with pytest.raises(BaseError, match="prompt files not found"):
                inst.register_in_bulk(tmpdir, name="test")

    @staticmethod
    def test_directory_with_pr_md_registers():
        """Directory with one .pr.md file registers and adds name to _all_prompt_names"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pr_path = Path(tmpdir) / "test_prompt.pr.md"
            pr_path.write_text("`#user#`\nHello.", encoding="utf-8")
            inst = ThreadSafePromptManager()
            inst.register_in_bulk(tmpdir, name="test_bulk")
            assert "test_prompt" in getattr(inst, "_all_prompt_names")


class TestContainsAndGet:
    """Tests for __contains__ and get"""

    @staticmethod
    def test_contains_returns_true_for_registered_name():
        """__contains__ returns True for name in _all_prompt_names"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pr_path = Path(tmpdir) / "cov_test.pr.md"
            pr_path.write_text("`#user#`\nHi.", encoding="utf-8")
            inst = ThreadSafePromptManager()
            inst.register_in_bulk(tmpdir, name="cov")
            assert "cov_test" in inst

    @staticmethod
    def test_contains_returns_false_for_unknown_name():
        """__contains__ returns False for name not registered"""
        inst = ThreadSafePromptManager()
        assert "nonexistent_prompt_xyz_123" not in inst

    @staticmethod
    def test_get_returns_template_when_registered():
        """get(name) returns PromptTemplate when name was registered"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pr_path = Path(tmpdir) / "get_test.pr.md"
            pr_path.write_text("`#user#`\nContent.", encoding="utf-8")
            inst = ThreadSafePromptManager()
            inst.register_in_bulk(tmpdir, name="g")
            template = inst.get("get_test")
            assert template is not None
            assert template.name == "get_test"

    @staticmethod
    def test_get_returns_none_for_unknown_name():
        """get(name) returns None when name not registered"""
        inst = ThreadSafePromptManager()
        assert inst.get("unknown_name_xyz_456") is None
