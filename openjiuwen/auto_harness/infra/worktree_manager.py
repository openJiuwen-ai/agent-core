# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Worktree 管理器 — 为每个 task 创建隔离的 git worktree。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.auto_harness.infra.git_auth import (
    build_git_auth_env,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.schema import (
        AutoHarnessConfig,
    )

logger = logging.getLogger(__name__)


def _slugify(topic: str) -> str:
    """将 topic 转为文件系统安全的 slug。

    Args:
        topic: 任务主题。

    Returns:
        仅含字母、数字、连字符的 slug。
    """
    slug = re.sub(
        r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", topic
    )
    return slug.strip("-")[:40] or "task"


async def _run_git(
    *args: str, cwd: str, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """执行 git 命令。

    Args:
        *args: git 子命令及参数。
        cwd: 工作目录。

    Returns:
        (returncode, stdout+stderr) 元组。
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env=env or environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    return proc.returncode or 0, output.strip()


class WorktreeManager:
    """为每个 task 创建和清理 git worktree。

    工作流：
    - 有 ``local_repo`` → fetch + worktree add
    - 无 ``local_repo`` → 确保 clone 缓存 → fetch + worktree add

    Args:
        config: Auto Harness 配置。
    """

    def __init__(
        self, config: "AutoHarnessConfig"
    ) -> None:
        self._config = config
        self._git_env = build_git_auth_env(
            username=config.resolve_gitcode_username(),
            token=config.resolve_gitcode_token(),
        )

    def _base_repo(self) -> str:
        """返回基础仓库路径。

        优先使用 ``local_repo``，否则使用
        ``{data_dir}/repo/agent-core`` 缓存。

        Returns:
            基础仓库的绝对路径。
        """
        if self._config.local_repo:
            return str(
                Path(self._config.local_repo).resolve()
            )
        return self._config.cache_repo_dir

    async def _ensure_base_repo(self) -> str:
        """确保基础仓库存在并已 fetch。

        Returns:
            基础仓库路径。

        Raises:
            RuntimeError: clone 或 fetch 失败。
        """
        base = self._base_repo()
        base_path = Path(base)

        if self._config.local_repo:
            # 本地仓库：只 fetch
            if not base_path.exists():
                raise RuntimeError(
                    f"local_repo not found: {base}"
                )
            code, out = await _run_git(
                "fetch", "origin", cwd=base, env=self._git_env
            )
            if code != 0:
                logger.warning(
                    "fetch failed in local_repo "
                    "(continuing): %s",
                    out,
                )
            return base

        # 无本地仓库：确保 clone 缓存
        git_dir = base_path / ".git"
        if git_dir.is_dir() or (
            base_path.is_dir()
            and (base_path / "HEAD").is_file()
        ):
            # 已有缓存，fetch
            code, out = await _run_git(
                "fetch", "origin", cwd=base, env=self._git_env
            )
            if code != 0:
                logger.warning(
                    "fetch failed in cache repo "
                    "(continuing): %s",
                    out,
                )
            return base

        # 首次 clone
        base_path.parent.mkdir(
            parents=True, exist_ok=True
        )
        code, out = await _run_git(
            "clone",
            "-b",
            self._config.git_base_branch or "develop",
            self._config.repo_url,
            str(base_path),
            cwd=str(base_path.parent),
            env=self._git_env,
        )
        if code != 0:
            raise RuntimeError(
                f"git clone failed: {out}"
            )
        logger.info("Cloned repo to %s", base)
        return base

    async def prepare(self, topic: str) -> str:
        """为 task 创建隔离的 worktree。

        Args:
            topic: 任务主题，用于生成目录名和分支名。

        Returns:
            Worktree 绝对路径。

        Raises:
            RuntimeError: worktree 创建失败。
        """
        base = await self._ensure_base_repo()

        slug = _slugify(topic)
        ts = int(time.time())
        wt_name = f"{ts}-{slug}"
        branch_name = f"auto-harness/{slug}"

        wt_root = Path(
            self._config.worktrees_dir
        )
        wt_root.mkdir(parents=True, exist_ok=True)
        wt_path = str(wt_root / wt_name)

        base_branch = (
            self._config.git_base_branch or "develop"
        )

        code, out = await _run_git(
            "worktree",
            "add",
            "-b",
            branch_name,
            wt_path,
            f"origin/{base_branch}",
            cwd=base,
            env=self._git_env,
        )
        if code != 0:
            raise RuntimeError(
                f"worktree add failed: {out}"
            )

        logger.info(
            "Created worktree: %s (branch: %s)",
            wt_path,
            branch_name,
        )

        # 配置 git user（worktree 级别）
        if self._config.git_user_name:
            await _run_git(
                "config",
                "user.name",
                self._config.git_user_name,
                cwd=wt_path,
                env=self._git_env,
            )
        if self._config.git_user_email:
            await _run_git(
                "config",
                "user.email",
                self._config.git_user_email,
                cwd=wt_path,
                env=self._git_env,
            )

        # 配置 fork remote（如果指定）
        if self._config.git_remote:
            # 检查 remote 是否已存在
            rc, _ = await _run_git(
                "remote",
                "get-url",
                self._config.git_remote,
                cwd=wt_path,
                env=self._git_env,
            )
            if rc != 0:
                # worktree 共享 remote，在 base 上添加
                fork_url = (
                    f"https://gitcode.com/"
                    f"{self._config.fork_owner}/"
                    f"{self._config.upstream_repo}.git"
                )
                await _run_git(
                    "remote",
                    "add",
                    self._config.git_remote,
                    fork_url,
                    cwd=base,
                    env=self._git_env,
                )

        return wt_path

    async def prepare_readonly_snapshot(
        self,
        *,
        label: str = "assess",
    ) -> str:
        """Create a detached read-only snapshot from origin/base.

        Used by assess/plan phases so analysis always targets the
        latest fetched remote base branch instead of the user's
        mutable local checkout.
        """
        base = await self._ensure_base_repo()
        ts = int(time.time())
        wt_root = Path(self._config.worktrees_dir)
        wt_root.mkdir(parents=True, exist_ok=True)
        wt_path = str(wt_root / f"{ts}-{label}")
        base_branch = (
            self._config.git_base_branch or "develop"
        )

        code, out = await _run_git(
            "worktree",
            "add",
            "--detach",
            wt_path,
            f"origin/{base_branch}",
            cwd=base,
            env=self._git_env,
        )
        if code != 0:
            raise RuntimeError(
                f"readonly worktree add failed: {out}"
            )

        logger.info(
            "Created readonly worktree: %s",
            wt_path,
        )
        return wt_path

    async def cleanup(
        self, worktree_path: str
    ) -> None:
        """清理 worktree。

        Args:
            worktree_path: 要清理的 worktree 路径。
        """
        base = self._base_repo()
        wt = Path(worktree_path)

        if not wt.exists():
            return

        code, out = await _run_git(
            "worktree",
            "remove",
            "--force",
            str(wt),
            cwd=base,
            env=self._git_env,
        )
        if code != 0:
            logger.warning(
                "worktree remove failed "
                "(manual cleanup needed): %s",
                out,
            )
        else:
            logger.info(
                "Cleaned up worktree: %s",
                worktree_path,
            )
