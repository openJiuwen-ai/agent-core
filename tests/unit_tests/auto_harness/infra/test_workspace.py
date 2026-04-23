# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_workspace — WorktreeManager 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
)
from openjiuwen.auto_harness.infra.worktree_manager import (
    WorktreeManager,
    _slugify,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("fix timeout bug") == (
            "fix-timeout-bug"
        )

    def test_special_chars(self):
        slug = _slugify("add: feature/new!")
        assert "/" not in slug
        assert ":" not in slug
        assert "!" not in slug

    def test_chinese(self):
        slug = _slugify("修复超时问题")
        assert len(slug) > 0

    def test_truncation(self):
        long_topic = "a" * 100
        slug = _slugify(long_topic)
        assert len(slug) <= 40

    def test_empty(self):
        assert _slugify("") == "task"

    def test_only_special(self):
        assert _slugify("!!!") == "task"


class TestWorktreeManager:
    def _make_config(
        self, tmp_path: Path, local_repo: str = "",
    ) -> AutoHarnessConfig:
        data_dir = str(tmp_path / "data")
        return AutoHarnessConfig(
            data_dir=data_dir,
            local_repo=local_repo,
            git_base_branch="develop",
            git_user_name="test-user",
            git_user_email="test@example.com",
            git_remote="myfork",
            fork_owner="TestOwner",
            upstream_repo="agent-core",
        )

    @pytest.mark.asyncio
    async def test_prepare_with_local_repo(
        self, tmp_path,
    ):
        """有 local_repo 时，fetch + worktree add。"""
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = self._make_config(
            tmp_path, local_repo=str(local),
        )
        mgr = WorktreeManager(cfg)

        wt_path = str(
            tmp_path / "data" / "worktrees" / "wt"
        )

        calls = []

        async def fake_run_git(*args, cwd, env=None):
            calls.append((args, cwd, env))
            # worktree add 需要创建目录
            if args[0] == "worktree" and args[1] == "add":
                Path(args[3]).mkdir(
                    parents=True, exist_ok=True,
                )
            # remote get-url 返回失败表示需要添加
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            result = await mgr.prepare("fix timeout")

        assert "worktrees" in result
        # 验证 fetch 被调用
        fetch_calls = [
            c for c in calls if c[0][0] == "fetch"
        ]
        assert len(fetch_calls) == 1
        assert fetch_calls[0][1] == str(local)

        # 验证 worktree add 被调用
        wt_calls = [
            c for c in calls
            if c[0][0] == "worktree" and c[0][1] == "add"
        ]
        assert len(wt_calls) == 1

        # 验证 git config user.name 被设置
        config_calls = [
            c for c in calls
            if c[0][0] == "config"
            and "user.name" in c[0]
        ]
        assert len(config_calls) == 1

    @pytest.mark.asyncio
    async def test_prepare_deletes_existing_branch_before_add(
        self, tmp_path,
    ):
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = self._make_config(
            tmp_path, local_repo=str(local),
        )
        mgr = WorktreeManager(cfg)

        calls = []
        branch_ref = (
            "refs/heads/auto-harness/fix-timeout"
        )

        async def fake_run_git(*args, cwd, env=None):
            del cwd, env
            calls.append(args)
            if args[:4] == (
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
            ):
                return 0, ""
            if args[:3] == (
                "worktree",
                "list",
                "--porcelain",
            ):
                return 0, (
                    f"worktree {local}\n"
                    "HEAD deadbeef\n"
                    "branch refs/heads/develop\n"
                )
            if args[:2] == ("worktree", "add"):
                Path(args[4]).mkdir(
                    parents=True, exist_ok=True,
                )
            if args[:2] == (
                "remote",
                "get-url",
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            await mgr.prepare("fix timeout")

        prune_idx = calls.index(
            ("worktree", "prune")
        )
        show_ref_idx = calls.index(
            (
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
            )
        )
        delete_idx = calls.index(
            (
                "branch",
                "-D",
                "auto-harness/fix-timeout",
            )
        )
        add_idx = next(
            idx
            for idx, call in enumerate(calls)
            if call[:2] == ("worktree", "add")
        )
        assert prune_idx < show_ref_idx < delete_idx < add_idx

    @pytest.mark.asyncio
    async def test_prepare_removes_managed_worktree_for_existing_branch(
        self, tmp_path,
    ):
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = self._make_config(
            tmp_path, local_repo=str(local),
        )
        mgr = WorktreeManager(cfg)

        calls = []
        branch_ref = (
            "refs/heads/auto-harness/fix-timeout"
        )
        stale_wt = (
            Path(cfg.worktrees_dir)
            / "old-fix-timeout"
        )

        async def fake_run_git(*args, cwd, env=None):
            del cwd, env
            calls.append(args)
            if args[:4] == (
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
            ):
                return 0, ""
            if args[:3] == (
                "worktree",
                "list",
                "--porcelain",
            ):
                return 0, (
                    f"worktree {stale_wt}\n"
                    "HEAD deadbeef\n"
                    f"branch {branch_ref}\n"
                )
            if args[:2] == ("worktree", "add"):
                Path(args[4]).mkdir(
                    parents=True, exist_ok=True,
                )
            if args[:2] == (
                "remote",
                "get-url",
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            await mgr.prepare("fix timeout")

        remove_idx = calls.index(
            (
                "worktree",
                "remove",
                "--force",
                str(stale_wt),
            )
        )
        delete_idx = calls.index(
            (
                "branch",
                "-D",
                "auto-harness/fix-timeout",
            )
        )
        add_idx = next(
            idx
            for idx, call in enumerate(calls)
            if call[:2] == ("worktree", "add")
        )
        assert remove_idx < delete_idx < add_idx

    @pytest.mark.asyncio
    async def test_prepare_rejects_unmanaged_worktree_for_existing_branch(
        self, tmp_path,
    ):
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = self._make_config(
            tmp_path, local_repo=str(local),
        )
        mgr = WorktreeManager(cfg)

        calls = []
        branch_ref = (
            "refs/heads/auto-harness/fix-timeout"
        )
        unmanaged_wt = (
            tmp_path / "foreign" / "fix-timeout"
        )

        async def fake_run_git(*args, cwd, env=None):
            del cwd, env
            calls.append(args)
            if args[:4] == (
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
            ):
                return 0, ""
            if args[:3] == (
                "worktree",
                "list",
                "--porcelain",
            ):
                return 0, (
                    f"worktree {unmanaged_wt}\n"
                    "HEAD deadbeef\n"
                    f"branch {branch_ref}\n"
                )
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            with pytest.raises(
                RuntimeError,
                match="unmanaged worktree",
            ):
                await mgr.prepare("fix timeout")

        assert (
            "branch",
            "-D",
            "auto-harness/fix-timeout",
        ) not in calls
        assert not any(
            call[:2] == ("worktree", "add")
            for call in calls
        )

    @pytest.mark.asyncio
    async def test_prepare_without_local_repo_clones(
        self, tmp_path,
    ):
        """无 local_repo 时，先 clone 再 worktree add。"""
        cfg = self._make_config(tmp_path)
        mgr = WorktreeManager(cfg)

        calls = []

        async def fake_run_git(*args, cwd, env=None):
            calls.append((args, cwd, env))
            if args[0] == "clone":
                # 模拟 clone 创建目录
                Path(args[-1]).mkdir(
                    parents=True, exist_ok=True,
                )
            if args[0] == "worktree" and args[1] == "add":
                Path(args[3]).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            result = await mgr.prepare("add feature")

        # 验证 clone 被调用
        clone_calls = [
            c for c in calls if c[0][0] == "clone"
        ]
        assert len(clone_calls) == 1

    @pytest.mark.asyncio
    async def test_cleanup(self, tmp_path):
        """cleanup 调用 git worktree remove。"""
        cfg = self._make_config(tmp_path)
        mgr = WorktreeManager(cfg)

        wt_dir = tmp_path / "data" / "worktrees" / "wt1"
        wt_dir.mkdir(parents=True)

        calls = []

        async def fake_run_git(*args, cwd, env=None):
            calls.append((args, cwd, env))
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            await mgr.cleanup(str(wt_dir))

        remove_calls = [
            c for c in calls
            if c[0][0] == "worktree"
            and c[0][1] == "remove"
        ]
        assert len(remove_calls) == 1

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_path(
        self, tmp_path,
    ):
        """清理不存在的路径不报错。"""
        cfg = self._make_config(tmp_path)
        mgr = WorktreeManager(cfg)

        # 不应抛异常
        await mgr.cleanup(
            str(tmp_path / "nonexistent")
        )

    @pytest.mark.asyncio
    async def test_prepare_adds_fork_remote(
        self, tmp_path,
    ):
        """配置了 git_remote 时添加 fork remote。"""
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = self._make_config(
            tmp_path, local_repo=str(local),
        )
        mgr = WorktreeManager(cfg)

        calls = []

        async def fake_run_git(*args, cwd, env=None):
            calls.append((args, cwd, env))
            if args[0] == "worktree" and args[1] == "add":
                Path(args[3]).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            await mgr.prepare("test remote")

        # 验证 remote add 被调用
        remote_add = [
            c for c in calls
            if c[0][0] == "remote" and c[0][1] == "add"
        ]
        assert len(remote_add) == 1
        assert "myfork" in remote_add[0][0]

    @pytest.mark.asyncio
    async def test_prepare_uses_task_scoped_git_auth_env(
        self, tmp_path,
    ):
        local = tmp_path / "local_repo"
        local.mkdir()
        cfg = AutoHarnessConfig(
            data_dir=str(tmp_path / "data"),
            local_repo=str(local),
            git_base_branch="develop",
            git_user_name="test-user",
            git_user_email="test@example.com",
            git_remote="myfork",
            fork_owner="ForkOwner",
            gitcode_username="bot-user",
            gitcode_token="secret-token",
        )
        mgr = WorktreeManager(cfg)

        seen_envs = []

        async def fake_run_git(*args, cwd, env=None):
            seen_envs.append(env or {})
            if args[0] == "worktree" and args[1] == "add":
                Path(args[3]).mkdir(
                    parents=True, exist_ok=True,
                )
            if (
                args[0] == "remote"
                and args[1] == "get-url"
            ):
                return 1, "not found"
            return 0, "ok"

        with patch(
            "openjiuwen.auto_harness.infra.worktree_manager._run_git",
            side_effect=fake_run_git,
        ):
            await mgr.prepare("auth test")

        assert seen_envs
        assert all(
            env.get("GIT_TERMINAL_PROMPT") == "0"
            for env in seen_envs
        )
        assert all(
            env.get("GIT_CONFIG_KEY_2")
            == "http.https://gitcode.com/.extraheader"
            for env in seen_envs
        )
