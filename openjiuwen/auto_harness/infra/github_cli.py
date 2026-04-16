# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""GitHub CLI preflight helpers for auto-harness research stages."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class GitHubCliStatus:
    """Result of the GitHub CLI preflight."""

    available: bool
    authenticated: bool
    installed_now: bool = False
    path: str = ""


def ensure_github_cli_ready(
    emit: Callable[[str], None],
) -> GitHubCliStatus:
    """Ensure ``gh`` exists and print login guidance when needed.

    The function is intentionally best-effort:
    - If ``gh`` is missing, it attempts installation via a detected package
      manager and falls back to guidance on failure.
    - If ``gh`` is present but unauthenticated, it does not block the run
      because public repository cloning and some public API calls may still
      work in degraded mode.
    """
    gh_path = shutil.which("gh") or ""
    installed_now = False

    if not gh_path:
        emit(
            "未检测到 `gh`，正在尝试自动安装 GitHub CLI。"
        )
        gh_path = _install_github_cli(emit)
        installed_now = bool(gh_path)
        if not gh_path:
            emit(
                "自动安装 `gh` 失败。"
                "本轮会退回网页补充调研，无法执行 GitHub-first 源码策略。"
            )
            emit(
                "请先安装 GitHub CLI 后重试："
                "https://cli.github.com/"
            )
            return GitHubCliStatus(
                available=False,
                authenticated=False,
            )

        emit(f"已安装 `gh`: {gh_path}")

    authenticated = _is_gh_authenticated()
    if authenticated:
        emit("检测到 `gh` 已登录。")
    else:
        emit(
            "检测到 `gh` 未登录。公开仓库通常仍可 clone，"
            "部分公开 API 也可匿名访问；但匿名请求速率较低，"
            "私有仓库和部分接口会失败。"
        )
        emit(
            "建议先执行 `gh auth login --web` 完成浏览器登录；"
            "若已有 token，也可使用 `gh auth login --with-token`。"
        )

    return GitHubCliStatus(
        available=True,
        authenticated=authenticated,
        installed_now=installed_now,
        path=gh_path,
    )


def _is_gh_authenticated() -> bool:
    """Return whether ``gh`` currently has usable auth configured."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def _install_github_cli(
    emit: Callable[[str], None],
) -> str:
    """Best-effort install of ``gh`` using common package managers."""
    for command, label in _install_commands():
        try:
            emit(f"尝试安装 GitHub CLI: `{label}`")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except Exception as exc:
            emit(f"`{label}` 执行失败: {exc}")
            continue

        if result.returncode == 0:
            gh_path = shutil.which("gh") or ""
            if gh_path:
                return gh_path

        stderr = (result.stderr or "").strip()
        if stderr:
            emit(
                f"`{label}` 安装失败: "
                f"{_truncate(stderr)}"
            )

    return ""


def _install_commands() -> list[tuple[list[str], str]]:
    """Build candidate install commands for the current platform."""
    commands: list[tuple[list[str], str]] = []
    system = platform.system().lower()
    sudo = shutil.which("sudo")
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0

    def _maybe_sudo(cmd: list[str]) -> list[str]:
        if is_root:
            return cmd
        if sudo:
            return [sudo, "-n", *cmd]
        return cmd

    if shutil.which("brew"):
        commands.append((
            ["brew", "install", "gh"],
            "brew install gh",
        ))

    if system == "linux":
        if shutil.which("apt-get"):
            commands.append((
                _maybe_sudo([
                    "apt-get",
                    "install",
                    "-y",
                    "gh",
                ]),
                "apt-get install -y gh",
            ))
        if shutil.which("dnf"):
            commands.append((
                _maybe_sudo([
                    "dnf",
                    "install",
                    "-y",
                    "gh",
                ]),
                "dnf install -y gh",
            ))
        if shutil.which("yum"):
            commands.append((
                _maybe_sudo([
                    "yum",
                    "install",
                    "-y",
                    "gh",
                ]),
                "yum install -y gh",
            ))
        if shutil.which("pacman"):
            commands.append((
                _maybe_sudo([
                    "pacman",
                    "-S",
                    "--noconfirm",
                    "github-cli",
                ]),
                "pacman -S --noconfirm github-cli",
            ))
        if shutil.which("zypper"):
            commands.append((
                _maybe_sudo([
                    "zypper",
                    "--non-interactive",
                    "install",
                    "gh",
                ]),
                "zypper --non-interactive install gh",
            ))

    if system == "windows" and shutil.which("winget"):
        commands.append((
            [
                "winget",
                "install",
                "--id",
                "GitHub.cli",
                "--exact",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            "winget install --id GitHub.cli --exact",
        ))

    return commands


def _truncate(text: str, limit: int = 240) -> str:
    """Keep CLI diagnostics short enough for user-facing status lines."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = [
    "GitHubCliStatus",
    "ensure_github_cli_ready",
]
