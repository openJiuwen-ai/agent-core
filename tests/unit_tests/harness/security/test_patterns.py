# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission pattern matching regression tests."""

from __future__ import annotations

from openjiuwen.harness.security.patterns import match_wildcard


def test_match_wildcard_rejects_trailing_newline() -> None:
    assert match_wildcard("git status", "git status") is True
    assert match_wildcard("git status\n", "git status") is False

    assert match_wildcard("git status", "git status *") is True
    assert match_wildcard("git status -sb", "git status *") is True
    assert match_wildcard("git status\n", "git status *") is False
    assert match_wildcard("git status -sb\n", "git status *") is False


def test_match_wildcard_still_rejects_command_injection() -> None:
    assert match_wildcard("git status; rm -rf /", "git status *") is False
    assert match_wildcard("git status\nrm -rf /", "git status *") is False
    assert match_wildcard("dir foo; rm -rf /", "dir *") is False
    assert match_wildcard("dir foo && rm", "dir *") is False
    assert match_wildcard("dir foo | bash", "dir *") is False


def test_match_wildcard_allows_filename_globs() -> None:
    assert match_wildcard("dir /b *.docx", "dir *") is True
    assert match_wildcard("dir /b*.docx", "dir *") is True
    assert match_wildcard("ls *.txt", "ls *") is True


def test_match_wildcard_allows_non_ascii_and_punctuation() -> None:
    """Issue #4814: glob must match non-ASCII (Chinese) and harmless punctuation."""
    assert match_wildcard("echo 权限测试", "echo *") is True
    assert match_wildcard("echo hello,everyone", "echo *") is True
    assert match_wildcard("echo 你好，世界", "echo *") is True


def test_match_wildcard_rejects_chinese_command_injection() -> None:
    """Issue #4814: widening char class must not break injection rejection."""
    assert match_wildcard("echo 你好; rm -rf /", "echo *") is False
    assert match_wildcard("echo 你好\nrm -rf /", "echo *") is False
    assert match_wildcard("echo 你好 && rm /", "echo *") is False


def test_match_wildcard_rejects_null_byte() -> None:
    """Issue #4814: null byte must also be blocked (C-string truncation defense)."""
    assert match_wildcard("echo \x00 rm -rf /", "echo *") is False
    assert match_wildcard("echo 你好\x00rm", "echo *") is False
