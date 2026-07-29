# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-scoped worktree cleanup helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.paths import team_session_dir, team_session_worktrees_dir
from openjiuwen.core.common.logging import team_logger


def _is_under_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


async def remove_session_worktrees(
    team_name: str,
    session_id: str,
    *,
    manager: Any = None,
) -> bool:
    """Remove every team-managed worktree under one team session."""
    if not team_name or not session_id:
        return True

    worktrees_root = team_session_worktrees_dir(team_name, session_id)
    if not worktrees_root.exists():
        return True
    if worktrees_root.is_symlink() or not worktrees_root.is_dir():
        team_logger.warning(
            "Keeping unexpected session worktrees path for team {} session {}: {}",
            team_name,
            session_id,
            worktrees_root,
        )
        return False

    session_root_real = os.path.realpath(str(team_session_dir(team_name, session_id)))
    worktrees_root_real = os.path.realpath(str(worktrees_root))
    if not _is_under_root(worktrees_root_real, session_root_real):
        team_logger.warning(
            "Keeping session worktrees outside team session root for team {} session {}: {}",
            team_name,
            session_id,
            worktrees_root,
        )
        return False

    from openjiuwen.harness.tools.worktree import WorktreeConfig, WorktreeManager
    from openjiuwen.harness.tools.worktree.git import find_canonical_git_root

    worktree_manager = manager or WorktreeManager(WorktreeConfig(enabled=True))
    success = True
    for entry in list(worktrees_root.iterdir()):
        entry_path = str(entry)
        entry_real = os.path.realpath(entry_path)
        if not _is_under_root(entry_real, worktrees_root_real):
            team_logger.warning(
                "Keeping session worktree path outside worktrees root for team {} session {}: {}",
                team_name,
                session_id,
                entry_path,
            )
            success = False
            continue

        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
                continue
            if not entry.is_dir():
                continue

            repo_root = await find_canonical_git_root(entry_path)
            removed = False
            if repo_root is not None:
                removed = await worktree_manager.remove_worktree(entry_path, repo_root, force=True)
            if not removed and Path(entry_path).exists():
                await _rmtree(entry_path)
        except Exception as exc:
            team_logger.warning(
                "Failed to remove session worktree for team {} session {}: {} ({})",
                team_name,
                session_id,
                entry_path,
                exc,
            )
            success = False

    try:
        if worktrees_root.exists():
            await _rmtree(str(worktrees_root))
    except Exception as exc:
        team_logger.warning(
            "Failed to remove session worktrees root for team {} session {}: {} ({})",
            team_name,
            session_id,
            worktrees_root,
            exc,
        )
        success = False

    return success


async def _rmtree(path: str) -> None:
    import asyncio

    await asyncio.to_thread(shutil.rmtree, path)


__all__ = ["remove_session_worktrees"]
