# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for CommitTool."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.auto_harness.schema import (
    CommitFacts,
)
from openjiuwen.auto_harness.tools.commit_tool import (
    CommitTool,
)


class TestCommitTool(IsolatedAsyncioTestCase):
    async def test_guard_failure_does_not_call_git(self):
        git = MagicMock()
        git.commit = AsyncMock()
        facts = CommitFacts(
            current_dirty_files=[
                "openjiuwen/auto_harness/schema.py",
                "docs/tmp.md",
            ],
            edited_files=[
                "openjiuwen/auto_harness/schema.py",
                "docs/tmp.md",
            ],
            allowed_files=[
                "openjiuwen/auto_harness/schema.py"
            ],
        )
        tool = CommitTool(
            git=git,
            facts_provider=lambda: facts,
        )

        result = await tool.invoke({
            "message": "fix(auto-harness): update schema",
            "files": [
                "openjiuwen/auto_harness/schema.py",
                "docs/tmp.md",
            ],
        })

        assert result.success is False
        git.commit.assert_not_awaited()

    async def test_commit_tool_commits_allowed_files(self):
        git = MagicMock()
        git.commit = AsyncMock(return_value={
            "success": True,
            "commit_sha": "abc123",
        })
        facts = CommitFacts(
            current_dirty_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
            edited_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
            allowed_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
            derived_test_files=[
                "tests/unit_tests/auto_harness/test_schema.py"
            ],
        )
        tool = CommitTool(
            git=git,
            facts_provider=lambda: facts,
        )

        result = await tool.invoke({
            "message": "fix(auto-harness): update schema",
            "files": [
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
            "rationale": "Update matching source and test.",
        })

        assert result.success is True
        git.commit.assert_awaited_once_with(
            "fix(auto-harness): update schema",
            [
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        )
