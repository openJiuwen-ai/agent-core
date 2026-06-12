# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Git 操作 — branch / push / PR (GitCode API)。

orchestrator 基础设施，不继承 Tool。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.error import HTTPError
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

    def set_workspace(self, workspace: str) -> None:
        """Update git command workspace."""
        self._workspace = workspace

    async def _git(
        self, *args: str, extra_env: Dict[str, str] | None = None
    ) -> tuple[int, str]:
        """执行 git 命令并返回 (returncode, output)。"""
        env = dict(self._git_env)
        if extra_env:
            env.update(extra_env)
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=self._workspace,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode or 0, output.rstrip()

    def _sanitize_git_output(self, output: str) -> str:
        """Remove credentials from git command output."""
        sanitized = output
        if self._token:
            sanitized = sanitized.replace(self._token, "***")
            if self._gitcode_username:
                raw = f"{self._gitcode_username}:{self._token}".encode("utf-8")
                sanitized = sanitized.replace(
                    base64.b64encode(raw).decode("ascii"),
                    "***",
                )
        if self._gitcode_username:
            sanitized = re.sub(
                rf"{re.escape(self._gitcode_username)}:[^@\s]+@",
                "***:***@",
                sanitized,
            )
            sanitized = sanitized.replace(
                f"{self._gitcode_username}:***@",
                "***:***@",
            )
        sanitized = re.sub(
            r"(?i)(authorization|private-token|access_token)([=:]\s*)[^\s&]+",
            r"\1\2***",
            sanitized,
        )
        return sanitized

    def _sanitize_git_args(self, args: list[str]) -> str:
        """Return a safe one-line git command for logs."""
        return "git " + " ".join(
            self._sanitize_git_output(str(arg)) for arg in args
        )

    def _authenticated_remote_url(self, remote_url: str) -> str:
        """Return an HTTPS remote URL carrying basic auth credentials."""
        if (
            not remote_url.startswith("https://")
            or not self._gitcode_username
            or not self._token
        ):
            return ""
        parts = urlsplit(remote_url)
        username = quote(self._gitcode_username, safe="")
        token = quote(self._token, safe="")
        return urlunsplit(
            (
                parts.scheme,
                f"{username}:{token}@{parts.netloc}",
                parts.path,
                parts.query,
                parts.fragment,
            )
        )

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

    async def diff_stat_against_base(self) -> str:
        """Return diff stat for commits on the current branch vs base."""
        _, out = await self._git(
            "diff",
            "--stat",
            f"origin/{self._base_branch}...HEAD",
        )
        return out.strip()

    async def add_paths(
        self,
        paths: List[str],
    ) -> Dict[str, Any]:
        """Stage explicit repo-relative paths in the current workspace."""
        if not paths:
            return {
                "success": False,
                "output": "no paths provided",
            }
        code, out = await self._git("add", "--", *paths)
        return {
            "success": code == 0,
            "output": out,
        }

    async def commit(
        self,
        message: str,
    ) -> Dict[str, Any]:
        """Create a commit in the current workspace."""
        code, out = await self._git("commit", "-m", message)
        return {
            "success": code == 0,
            "output": out,
        }

    async def diff_name_only(
        self,
        revision: str = "HEAD",
    ) -> List[str]:
        """Return ``git diff --name-only <revision>`` as normalized paths."""
        _, out = await self._git(
            "diff",
            "--name-only",
            revision,
        )
        files: List[str] = []
        for line in out.splitlines():
            normalized = line.strip().replace("\\", "/")
            if normalized:
                files.append(normalized)
        return list(dict.fromkeys(files))

    async def diff_name_only_against_base(self) -> List[str]:
        """Return files changed by current branch commits vs base."""
        _, out = await self._git(
            "diff",
            "--name-only",
            f"origin/{self._base_branch}...HEAD",
        )
        files: List[str] = []
        for line in out.splitlines():
            normalized = line.strip().replace("\\", "/")
            if normalized:
                files.append(normalized)
        return list(dict.fromkeys(files))

    async def has_commits_against_base(self) -> bool:
        """Return whether HEAD contains commits not in origin/base."""
        code, out = await self._git(
            "rev-list",
            "--count",
            f"origin/{self._base_branch}..HEAD",
        )
        if code != 0:
            return False
        try:
            return int(out.strip() or "0") > 0
        except ValueError:
            return False

    async def _commit_count_against_base(self, ref: str) -> int:
        """Return number of commits in ref not reachable from origin/base."""
        code, out = await self._git(
            "rev-list",
            "--count",
            f"origin/{self._base_branch}..{ref}",
        )
        if code != 0:
            return 0
        try:
            return int(out.strip() or "0")
        except ValueError:
            return 0

    async def _diff_name_only_for_ref_against_base(
        self, ref: str
    ) -> List[str]:
        """Return files changed by a candidate ref vs origin/base."""
        _, out = await self._git(
            "diff",
            "--name-only",
            f"origin/{self._base_branch}...{ref}",
        )
        files: List[str] = []
        for line in out.splitlines():
            normalized = line.strip().replace("\\", "/")
            if normalized:
                files.append(normalized)
        return list(dict.fromkeys(files))

    async def find_existing_issue_fix_ref(
        self,
        issue_number: str,
        *,
        allowed_files: List[str] | None = None,
    ) -> Dict[str, Any]:
        """Find an existing local/remote branch carrying an issue fix."""
        if not issue_number:
            return {"success": False, "reason": "missing issue number"}
        current_branch = await self.current_branch()
        code, out = await self._git(
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
            "refs/remotes/origin",
            "refs/remotes/autoharness",
        )
        if code != 0:
            return {"success": False, "reason": out}

        needle = f"issue-{issue_number}"
        exact_needles = (
            f"fix/issue-{issue_number}",
            f"fix-issue-{issue_number}",
            f"auto-harness/fix-issue-{issue_number}",
        )
        allowed = set(allowed_files or [])
        candidates: list[dict[str, Any]] = []
        for ref in dict.fromkeys(line.strip() for line in out.splitlines()):
            if not ref:
                continue
            normalized = ref.removeprefix("origin/").removeprefix(
                "autoharness/"
            )
            if normalized == current_branch:
                continue
            lower_ref = normalized.lower()
            if needle not in lower_ref and not any(
                value in lower_ref for value in exact_needles
            ):
                continue
            count = await self._commit_count_against_base(ref)
            if count <= 0:
                continue
            files = await self._diff_name_only_for_ref_against_base(ref)
            overlap = len(allowed.intersection(files)) if allowed else 0
            candidates.append(
                {
                    "ref": ref,
                    "commit_count": count,
                    "files": files,
                    "score": (
                        overlap * 100
                        + (50 if lower_ref.startswith("fix/") else 0)
                        + (25 if lower_ref.startswith("fix-issue-") else 0)
                        - count
                    ),
                }
            )
        if not candidates:
            return {
                "success": False,
                "reason": f"no existing branch found for issue {issue_number}",
            }
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return {"success": True, **candidates[0]}

    async def cherry_pick_ref_commits(
        self,
        ref: str,
    ) -> Dict[str, Any]:
        """Cherry-pick commits from origin/base..ref onto current branch."""
        code, out = await self._git(
            "rev-list",
            "--reverse",
            f"origin/{self._base_branch}..{ref}",
        )
        if code != 0:
            return {"success": False, "output": out}
        commits = [line.strip() for line in out.splitlines() if line.strip()]
        if not commits:
            return {"success": False, "output": "no commits to cherry-pick"}
        outputs: List[str] = []
        for commit in commits:
            code, pick_out = await self._git("cherry-pick", commit)
            outputs.append(pick_out)
            if code != 0:
                await self._git("cherry-pick", "--abort")
                return {
                    "success": False,
                    "output": "\n".join(outputs),
                    "commits": commits,
                }
        return {
            "success": True,
            "output": "\n".join(outputs),
            "commits": commits,
        }

    async def status_porcelain(self) -> str:
        """Return raw git status --porcelain output."""
        _, out = await self._git(
            "status", "--porcelain", "--untracked-files=all"
        )
        return out.rstrip()

    async def show_last_commit_stat(self) -> str:
        """Return a compact summary of the latest commit."""
        _, out = await self._git(
            "show",
            "--stat",
            "--format=fuller",
            "-1",
        )
        return out.strip()

    async def discard_worktree_changes(self) -> bool:
        """Discard current worktree changes via ``git checkout .``."""
        code, _ = await self._git("checkout", ".")
        return code == 0

    async def diff_against(self, revision: str) -> str:
        """Return ``git diff <revision>`` output."""
        _, out = await self._git("diff", revision)
        return out

    async def diff_against_base(self) -> str:
        """Return full diff for current branch commits vs base."""
        _, out = await self._git(
            "diff",
            f"origin/{self._base_branch}...HEAD",
        )
        return out

    async def push(
        self, branch_name: str
    ) -> Dict[str, Any]:
        """推送到 fork 远程。"""
        attempts: list[tuple[str, list[str]]] = [
            (
                "branch force-with-lease push",
                ["push", "-u", "--force-with-lease", self._remote, branch_name],
            ),
            (
                "branch push",
                ["push", "-u", self._remote, branch_name],
            ),
            (
                "HEAD refspec push",
                ["push", "-u", self._remote, f"HEAD:refs/heads/{branch_name}"],
            ),
        ]
        _, remote_url = await self._git(
            "remote", "get-url", self._remote
        )
        _, remote_push_url = await self._git(
            "remote", "get-url", "--push", self._remote
        )
        _, origin_push_url = await self._git(
            "remote", "get-url", "--push", "origin"
        )
        context = await self._collect_push_context(
            branch_name=branch_name,
            remote_url=remote_url.strip(),
            remote_push_url=remote_push_url.strip(),
            origin_push_url=origin_push_url.strip(),
        )
        logger.info("[AutoHarnessGitPush] context:\n%s", context)
        outputs: list[str] = []
        outputs.append(f"[push context]\n{context}".strip())
        for label, args in attempts:
            safe_cmd = self._sanitize_git_args(args)
            logger.info(
                "[AutoHarnessGitPush] attempt start: label=%s cmd=%s",
                label,
                safe_cmd,
            )
            started_at = time.perf_counter()
            code, out = await self._git(*args)
            elapsed = time.perf_counter() - started_at
            sanitized = self._sanitize_git_output(out)
            logger.info(
                "[AutoHarnessGitPush] attempt end: label=%s code=%s "
                "elapsed=%.2fs output=%s",
                label,
                code,
                elapsed,
                sanitized,
            )
            outputs.append(
                f"[{label}]\n"
                f"cmd={safe_cmd}\n"
                f"code={code}\n"
                f"elapsed={elapsed:.2f}s\n"
                f"{sanitized}".strip()
            )
            if code == 0:
                return {
                    "success": True,
                    "output": "\n\n".join(outputs),
                }
        diagnostics = await self._collect_push_diagnostics(branch_name)
        logger.warning(
            "[AutoHarnessGitPush] all attempts failed; diagnostics:\n%s",
            diagnostics,
        )
        outputs.append(f"[push diagnostics]\n{diagnostics}".strip())
        return {
            "success": False,
            "output": "\n\n".join(outputs),
        }

    async def _collect_push_context(
        self,
        branch_name: str,
        remote_url: str,
        remote_push_url: str,
        origin_push_url: str,
    ) -> str:
        """Collect safe push context for diagnosing remote failures."""
        checks: list[tuple[str, list[str]]] = [
            ("workspace", ["rev-parse", "--show-toplevel"]),
            ("current_branch", ["branch", "--show-current"]),
            ("head", ["rev-parse", "HEAD"]),
            ("head_subject", ["log", "-1", "--pretty=%s"]),
            ("status", ["status", "--short", "--branch"]),
            ("push_default", ["config", "--get", "push.default"]),
            ("remote_pushdefault", ["config", "--get", "remote.pushDefault"]),
            ("http_version", ["config", "--get", "http.version"]),
            (
                "extraheader_configured",
                ["config", "--get", "http.https://gitcode.com/.extraheader"],
            ),
        ]
        lines = [
            f"workspace_config={self._workspace}",
            f"remote={self._remote}",
            f"branch_name={branch_name}",
            f"remote_url={self._sanitize_git_output(remote_url)}",
            f"remote_push_url={self._sanitize_git_output(remote_push_url)}",
            f"origin_push_url={self._sanitize_git_output(origin_push_url)}",
        ]
        for label, args in checks:
            code, out = await self._git(*args)
            value = self._sanitize_git_output(out)
            if label == "extraheader_configured" and value:
                value = "yes"
            lines.append(f"{label}: code={code} value={value}")
        return "\n".join(lines)

    async def _collect_push_diagnostics(self, branch_name: str) -> str:
        """Run read-only checks and a traced dry-run after push failure."""
        trace_env = {
            "GIT_TRACE": "1",
            "GIT_CURL_VERBOSE": "1",
            "GIT_TRACE_CURL": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        checks: list[tuple[str, list[str], Dict[str, str] | None]] = [
            ("remote -v", ["remote", "-v"], None),
            ("status", ["status", "--short", "--branch"], None),
            ("current branch", ["branch", "--show-current"], None),
            ("head", ["rev-parse", "HEAD"], None),
            ("ls-remote HEAD", ["ls-remote", self._remote, "HEAD"], None),
            (
                "ls-remote target",
                ["ls-remote", self._remote, f"refs/heads/{branch_name}"],
                None,
            ),
            (
                "dry-run push",
                ["push", "--dry-run", self._remote, f"HEAD:refs/heads/{branch_name}"],
                None,
            ),
            (
                "dry-run push trace",
                ["push", "--dry-run", self._remote, f"HEAD:refs/heads/{branch_name}"],
                trace_env,
            ),
        ]
        lines: list[str] = []
        for label, args, env in checks:
            started_at = time.perf_counter()
            code, out = await self._git(*args, extra_env=env)
            elapsed = time.perf_counter() - started_at
            sanitized = self._sanitize_git_output(out)
            lines.append(
                f"[{label}]\n"
                f"cmd={self._sanitize_git_args(args)}\n"
                f"code={code}\n"
                f"elapsed={elapsed:.2f}s\n"
                f"{sanitized}".strip()
            )
        return "\n\n".join(lines)

    def build_pr_web_url(self, head_branch: str) -> str:
        """Return the GitCode web URL for manually creating a PR/MR."""
        if not self._fork_owner or not self._upstream_repo:
            return ""
        source_branch = quote(head_branch, safe="")
        target_branch = quote(self._base_branch, safe="")
        return (
            f"https://gitcode.com/{self._fork_owner}/{self._upstream_repo}"
            f"/merge_requests/new?source_branch={source_branch}"
            f"&target_branch={target_branch}"
        )

    def _extract_pr_url(self, value: Any) -> str:
        """Extract a PR/MR URL from GitCode's varying response shapes."""
        if isinstance(value, dict):
            for key in (
                "html_url",
                "web_url",
                "url",
                "pr_url",
                "pull_request_url",
                "merge_request_url",
            ):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
            for key in ("data", "pull_request", "merge_request", "result"):
                candidate = self._extract_pr_url(value.get(key))
                if candidate:
                    return candidate
            for candidate_value in value.values():
                candidate = self._extract_pr_url(candidate_value)
                if candidate:
                    return candidate
        if isinstance(value, list):
            for item in value:
                candidate = self._extract_pr_url(item)
                if candidate:
                    return candidate
        return ""

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
        request_payload = {
            "title": title,
            "head": f"{self._fork_owner}:{head_branch}",
            "base": self._base_branch,
            "body": body,
        }
        payload = json.dumps(request_payload).encode("utf-8")
        diagnostic_context = {
            "api": f"POST /repos/{self._upstream_owner}/{self._upstream_repo}/pulls",
            "target_repo": f"{self._upstream_owner}/{self._upstream_repo}",
            "source_repo": f"{self._fork_owner}/{self._upstream_repo}",
            "head": request_payload["head"],
            "base": self._base_branch,
            "title": title,
            "body_bytes": len(body.encode("utf-8", errors="replace")),
            "manual_url": self.build_pr_web_url(head_branch),
        }
        req = Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError:
                data = {"raw_body": raw_body}
            pr_url = self._extract_pr_url(data)
            manual_url = self.build_pr_web_url(head_branch)
            if pr_url:
                return {
                    "success": True,
                    "pr_url": pr_url,
                    "manual_url": manual_url,
                    "response": data,
                    "diagnostics": diagnostic_context,
                }
            logger.warning(
                "GitCode PR creation returned no URL: context=%s response=%s manual_url=%s",
                diagnostic_context,
                data,
                manual_url,
            )
            return {
                "success": False,
                "error": "GitCode PR creation returned no URL",
                "manual_url": manual_url,
                "response": data,
                "diagnostics": diagnostic_context,
            }
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            sanitized_body = self._sanitize_git_output(raw_body)
            logger.error(
                "GitCode PR creation HTTP error: status=%s reason=%s context=%s body=%s",
                exc.code,
                exc.reason,
                diagnostic_context,
                sanitized_body,
            )
            return {
                "success": False,
                "error": f"HTTP {exc.code} {exc.reason}: {sanitized_body}",
                "http_status": exc.code,
                "response_body": sanitized_body,
                "manual_url": diagnostic_context["manual_url"],
                "diagnostics": diagnostic_context,
            }
        except Exception as exc:
            logger.error(
                "GitCode PR creation failed: context=%s error=%s",
                diagnostic_context,
                exc,
            )
            return {
                "success": False,
                "error": str(exc),
                "manual_url": diagnostic_context["manual_url"],
                "diagnostics": diagnostic_context,
            }

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
