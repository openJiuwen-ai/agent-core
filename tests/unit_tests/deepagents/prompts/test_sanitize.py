# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for prompt sanitization utilities."""
from __future__ import annotations

from openjiuwen.deepagents.prompts.sanitize import (
    sanitize_path,
    sanitize_user_content,
)


class TestSanitizePath:
    @staticmethod
    def test_removes_angle_brackets():
        assert sanitize_path("/home/<user>/file") == "/home/user/file"

    @staticmethod
    def test_removes_braces_and_brackets():
        assert sanitize_path("path/{id}/[0]") == "path/id/0"

    @staticmethod
    def test_removes_backtick_and_dollar():
        assert sanitize_path("path/`cmd`/$VAR") == "path/cmd/VAR"

    @staticmethod
    def test_removes_triple_dots():
        assert sanitize_path("path/.../secret") == "path//secret"

    @staticmethod
    def test_preserves_normal_path():
        assert sanitize_path("/home/user/project/file.py") == "/home/user/project/file.py"

    @staticmethod
    def test_removes_escaped_newlines():
        assert sanitize_path("path\\nto\\rfile") == "pathtofile"


class TestSanitizeUserContent:
    @staticmethod
    def test_removes_injection_chars():
        result = sanitize_user_content("Hello <script>alert(1)</script>")
        assert "<" not in result
        assert ">" not in result

    @staticmethod
    def test_truncates_to_max_len():
        long_content = "a" * 5000
        result = sanitize_user_content(long_content, max_len=100)
        assert len(result) == 100

    @staticmethod
    def test_default_max_len():
        long_content = "a" * 3000
        result = sanitize_user_content(long_content)
        assert len(result) == 2000

    @staticmethod
    def test_short_content_unchanged():
        assert sanitize_user_content("hello world") == "hello world"

    @staticmethod
    def test_sanitize_then_truncate():
        # Injection chars are removed before truncation
        content = "<" * 10 + "a" * 100
        result = sanitize_user_content(content, max_len=50)
        assert len(result) <= 50
        assert "<" not in result
