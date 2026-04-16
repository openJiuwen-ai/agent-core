# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Git 操作 — branch / push / PR (GitCode API)。

orchestrator 基础设施，不继承 Tool。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List
from urllib.parse import quote
from urllib.request import Request, urlopen

from openjiuwen.auto_harness.infra.git_auth import (
    build_git_auth_env,
)

logger = logging.getLogger(__name__)


class GitOperations:
    """Git 操作（orchestrator 基础设施）。

    Args:
        workspace: git 仓库工作目录。
        remote: fork 远程名称。
        base_branch: 目标分支。
        fork_owner: fork 所有者。
        upstream_owner: 上游仓库所有者。
        upstream_repo: 上游仓库名称。
        gitcode_token: GitCode API token。
        user_name: git commit 用户名。
        user_email: git commit 邮箱。
    """

    def __init__(
        self,
        workspace: str,
        remote: str = "",
        base_branch: str = "develop",
        fork_owner: str = "",
        upstream_owner: str = "openJiuwen",
        upstream_repo: str = "agent-core",
        gitcode_username: str = "",
        gitcode_token: str = "",
        user_name: str = "",
        user_email: str = "",
    ) -> None:
        self._workspace = workspace
        self._remote = remote
        self._base_branch = base_branch
        self._fork_owner = fork_owner
        self._upstream_owner = upstream_owner
        self._upstream_repo = upstream_repo
        self._user_name = user_name
        self._user_email = user_email
        self._gitcode_username = gitcode_username
        self._token = (
            gitcode_token
            or os.getenv("GITCODE_ACCESS_TOKEN", "")
        )
        self._git_env = build_git_auth_env(
            username=self._gitcode_username,
            token=self._token,
        )

    async def _git(
        self, *args: str
    ) -> tuple[int, str]:
        """执行 git 命令并返回 (returncode, output)。"""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=self._workspace,
            env=self._git_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode or 0, output.strip()

    async def create_branch(
        self, branch_name: str
    ) -> Dict[str, Any]:
        """创建并切换到新分支。"""
        code, out = await self._git(
            "checkout", "-b", branch_name
        )
        return {
            "success": code == 0,
            "branch": branch_name,
            "output": out,
        }

    async def collect_status(self) -> Dict[str, List[str]]:
        """Collect current git status in a structured form."""
        code, out = await self._git(
            "status", "--porcelain", "--untracked-files=all"
        )
        status: Dict[str, List[str]] = {
            "dirty_files": [],
            "tracked_modified_files": [],
            "untracked_files": [],
            "renamed_files": [],
        }
        if code != 0 or not out:
            return status

        for line in out.splitlines():
            if len(line) < 4:
                continue
            marker = line[:2]
            path = line[3:].strip()
            if not path:
                continue
            if " -> " in path:
                new_path = path.split(" -> ", 1)[1].strip()
                status["renamed_files"].append(new_path)
                path = new_path
            normalized = path.replace("\\", "/")
            status["dirty_files"].append(normalized)
            if marker == "??":
                status["untracked_files"].append(normalized)
            else:
                status["tracked_modified_files"].append(normalized)

        for key, value in status.items():
            status[key] = list(dict.fromkeys(value))
        return status

    async def list_dirty_files(self) -> List[str]:
        """Return current dirty files."""
        status = await self.collect_status()
        return status["dirty_files"]

    async def current_branch(self) -> str:
        """Return current branch name."""
        _, out = await self._git(
            "rev-parse", "--abbrev-ref", "HEAD"
        )
        return out.strip()

    async def current_head(self) -> str:
        """Return current HEAD sha."""
        _, out = await self._git(
            "rev-parse", "HEAD"
        )
        return out.strip()

    async def diff_stat(
        self,
        paths: List[str] | None = None,
    ) -> str:
        """Return git diff --stat summary."""
        args = ["diff", "--stat"]
        if paths:
            args.append("--")
            args.extend(paths)
        _, out = await self._git(*args)
        return out.strip()

    async def status_porcelain(self) -> str:
        """Return raw git status --porcelain output."""
        _, out = await self._git(
            "status", "--porcelain", "--untracked-files=all"
        )
        return out.strip()

    async def show_last_commit_stat(self) -> str:
        """Return a compact summary of the latest commit."""
        _, out = await self._git(
            "show",
            "--stat",
            "--format=fuller",
            "-1",
        )
        return out.strip()

    async def push(
        self, branch_name: str
    ) -> Dict[str, Any]:
        """推送到 fork 远程。"""
        code, out = await self._git(
            "push", "-u", self._remote, branch_name
        )
        return {
            "success": code == 0,
            "output": out,
        }

    def _create_pr_sync(
        self,
        title: str,
        body: str,
        head_branch: str,
    ) -> Dict[str, Any]:
        """通过 GitCode API 创建 MR（同步）。"""
        owner = quote(self._upstream_owner, safe="")
        repo = quote(self._upstream_repo, safe="")
        url = (
            f"https://api.gitcode.com/api/v5/repos/"
            f"{owner}/{repo}/pulls"
            f"?access_token={self._token}"
        )
        payload = json.dumps({
            "title": title,
            "head": f"{self._fork_owner}:{head_branch}",
            "base": self._base_branch,
            "body": body,
        }).encode("utf-8")
        req = Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pr_url = data.get("html_url", "")
            return {"success": True, "pr_url": pr_url}
        except Exception as exc:
            logger.error("GitCode PR creation failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def create_pr(
        self,
        title: str,
        body: str,
        head_branch: str,
    ) -> Dict[str, Any]:
        """异步创建 PR。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._create_pr_sync, title, body, head_branch
        )
