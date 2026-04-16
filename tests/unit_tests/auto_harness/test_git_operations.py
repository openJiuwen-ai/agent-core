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
    async def test_commit_stages_only_declared_changed_files(self):
        git = GitOperations(workspace="/tmp/worktree")

        git._git = AsyncMock(
            side_effect=[
                (0, ""),
                (0, ""),
                (0, "[main abc123] auto-harness: tighten scope"),
                (0, "abc123"),
            ]
        )

        result = await git.commit(
            "auto-harness: tighten scope",
            files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        )

        assert result["success"] is True
        assert result["staged_files"] == [
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        ]
        assert result["commit_sha"] == "abc123"
        assert git._git.await_args_list[0].args == (
            "add",
            "--",
            "openjiuwen/auto_harness/schema.py",
        )
        assert git._git.await_args_list[1].args == (
            "add",
            "--",
            "tests/unit_tests/auto_harness/test_schema.py",
        )
        assert git._git.await_args_list[2].args == (
            "commit",
            "-m",
            "auto-harness: tighten scope",
        )

    @pytest.mark.asyncio
    async def test_commit_requires_files(self):
        git = GitOperations(workspace="/tmp/worktree")

        result = await git.commit(
            "auto-harness: tighten scope",
            [],
        )

        assert result["success"] is False
        assert result["error_code"] == "empty_files"
