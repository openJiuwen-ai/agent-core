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
