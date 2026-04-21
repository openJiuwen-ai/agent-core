# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for auto-harness git auth helpers and operations."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.auto_harness.infra.git_auth import (
    build_git_auth_env,
)
from openjiuwen.auto_harness.infra.git_operations import (
    GitOperations,
)


class TestBuildGitAuthEnv:
    def test_without_credentials_only_disables_prompts(
        self,
    ):
        env = build_git_auth_env()
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GCM_INTERACTIVE"] == "never"
        assert "GIT_CONFIG_COUNT" not in env

    def test_with_credentials_injects_gitcode_header(
        self,
    ):
        env = build_git_auth_env(
            username="bot-user",
            token="secret-token",
        )
        expected = base64.b64encode(
            b"bot-user:secret-token"
        ).decode("ascii")
        assert env["GIT_CONFIG_COUNT"] == "3"
        assert env["GIT_CONFIG_KEY_2"] == (
            "http.https://gitcode.com/.extraheader"
        )
        assert env["GIT_CONFIG_VALUE_2"] == (
            f"AUTHORIZATION: basic {expected}"
        )


class TestGitOperations:
    @pytest.mark.asyncio
    async def test_git_helper_preserves_leading_space_in_stdout(self):
        git = GitOperations(workspace="/tmp/worktree")

        proc = AsyncMock()
        proc.communicate.return_value = (
            b" M openjiuwen/auto_harness/schema.py\n",
            b"",
        )
        proc.returncode = 0

        with patch(
            "openjiuwen.auto_harness.infra.git_operations.asyncio.create_subprocess_exec",
            return_value=proc,
        ):
            code, out = await git._git(
                "status", "--porcelain"
            )

        assert code == 0
        assert out == " M openjiuwen/auto_harness/schema.py"

    @pytest.mark.asyncio
    async def test_push_uses_task_scoped_auth_env(self):
        git = GitOperations(
            workspace="/tmp/worktree",
            remote="fork",
            gitcode_username="bot-user",
            gitcode_token="secret-token",
        )

        proc = AsyncMock()
        proc.communicate.return_value = (b"ok", b"")
        proc.returncode = 0

        with patch(
            "openjiuwen.auto_harness.infra.git_operations.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as create_proc:
            result = await git.push("feature-branch")

        assert result["success"] is True
        _, kwargs = create_proc.call_args
        env = kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_KEY_2"] == (
            "http.https://gitcode.com/.extraheader"
        )

    @pytest.mark.asyncio
    async def test_collect_status_splits_tracked_and_untracked_files(self):
        git = GitOperations(workspace="/tmp/worktree")

        git._git = AsyncMock(
            side_effect=[
                (
                    0,
                    " M openjiuwen/auto_harness/schema.py\n"
                    "?? tests/unit_tests/auto_harness/test_schema.py\n"
                    "R  old.py -> new.py",
                ),
            ]
        )

        result = await git.collect_status()

        assert result["dirty_files"] == [
            "openjiuwen/auto_harness/schema.py"
            ,
            "tests/unit_tests/auto_harness/test_schema.py",
            "new.py",
        ]
        assert result["tracked_modified_files"] == [
            "openjiuwen/auto_harness/schema.py",
            "new.py",
        ]
        assert result["untracked_files"] == [
            "tests/unit_tests/auto_harness/test_schema.py"
        ]
        assert result["renamed_files"] == ["new.py"]

    @pytest.mark.asyncio
    async def test_status_porcelain_returns_raw_output(self):
        git = GitOperations(workspace="/tmp/worktree")

        git._git = AsyncMock(
            return_value=(
                0,
                " M openjiuwen/auto_harness/schema.py\n"
                "?? tests/unit_tests/auto_harness/test_schema.py",
            )
        )

        result = await git.status_porcelain()

        assert result == (
            " M openjiuwen/auto_harness/schema.py\n"
            "?? tests/unit_tests/auto_harness/test_schema.py"
        )
        git._git.assert_awaited_once_with(
            "status", "--porcelain", "--untracked-files=all"
        )

    @pytest.mark.asyncio
    async def test_show_last_commit_stat_returns_compact_summary(self):
        git = GitOperations(workspace="/tmp/worktree")

        git._git = AsyncMock(
            return_value=(
                0,
                "commit abc123\n"
                "Author: auto-harness\n\n"
                " 1 file changed, 2 insertions(+)",
            )
        )

        result = await git.show_last_commit_stat()

        assert result == (
            "commit abc123\n"
            "Author: auto-harness\n\n"
            " 1 file changed, 2 insertions(+)"
        )
        git._git.assert_awaited_once_with(
            "show",
            "--stat",
            "--format=fuller",
            "-1",
        )

    @pytest.mark.asyncio
    async def test_diff_name_only_returns_normalized_unique_paths(self):
        git = GitOperations(workspace="/tmp/worktree")

        git._git = AsyncMock(
            return_value=(
                0,
                "openjiuwen\\core\\foo.py\n"
                "tests/unit_tests/test_foo.py\n"
                "openjiuwen\\core\\foo.py",
            )
        )

        result = await git.diff_name_only("HEAD")

        assert result == [
            "openjiuwen/core/foo.py",
            "tests/unit_tests/test_foo.py",
        ]
        git._git.assert_awaited_once_with(
            "diff",
            "--name-only",
            "HEAD",
        )
