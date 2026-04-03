# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for CLI default skill directory configuration."""

from __future__ import annotations

from openjiuwen.harness.cli.agent.factory import (
    _DEFAULT_SKILL_DIRS,
    _default_skill_dirs,
)


def test_default_skill_dirs_returns_expected_paths():
    """_default_skill_dirs should return the four default paths in priority order."""
    dirs = _default_skill_dirs()
    assert len(dirs) == 4
    assert dirs[0] == "~/.openjiuwen/workspace/skills"
    assert dirs[1] == "~/.claude/skills"
    assert dirs[2] == "~/.codex/skills"
    assert dirs[3] == "~/.jiuwenclaw/workspace/skills"


def test_default_skill_dirs_returns_copy():
    """_default_skill_dirs should return a copy, not the original list."""
    dirs = _default_skill_dirs()
    dirs.append("extra")
    assert len(_DEFAULT_SKILL_DIRS) == 4
