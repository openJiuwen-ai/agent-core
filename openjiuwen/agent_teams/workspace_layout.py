# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace layout helpers shared by team members and swarmflow workers."""

from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

from openjiuwen.agent_teams.paths import independent_member_workspace, team_home
from openjiuwen.core.common.utils.windows_junction import create_windows_junction


def team_member_workspace_path(team_name: str, member_name: str) -> Path:
    """Return the stable workspace path for one team member."""
    return team_home(team_name) / "workspaces" / f"{member_name}_workspace"


def ensure_team_member_workspace_link(team_name: str, member_name: str) -> str:
    """Ensure an existing independent member workspace is visible under the team.

    Standalone DeepAgent workspaces live outside the team tree. When such a
    workspace already exists, expose it at the stable team workspace path via a
    symlink; otherwise return the stable path for the normal workspace setup to
    create/use later.
    """
    workspace_path = team_member_workspace_path(team_name, member_name)
    independent_workspace = independent_member_workspace(member_name)
    if independent_workspace.is_dir() and not workspace_path.exists():
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(
                str(independent_workspace),
                str(workspace_path),
                target_is_directory=True,
            )
        except OSError as exc:
            # Windows 无特权建 symlink 的错误码不止 1314：服务进程/沙箱环境下
            # 同一调用也可能以 WinError 2/5 等形态出现（实测 agentserver 内
            # WinError 2，交互进程 1314）。junction 失败会抛出，统一兜底安全。
            if sys.platform == "win32":
                try:
                    create_windows_junction(
                        str(independent_workspace), str(workspace_path)
                    )
                    return str(workspace_path)
                except OSError:
                    pass
            if getattr(exc, "errno", None) not in (errno.EACCES, errno.EPERM):
                raise
            shutil.copytree(
                str(independent_workspace),
                str(workspace_path),
                symlinks=False,
                copy_function=shutil.copy2,
                dirs_exist_ok=False,
            )
    return str(workspace_path)


__all__ = [
    "ensure_team_member_workspace_link",
    "team_member_workspace_path",
]
