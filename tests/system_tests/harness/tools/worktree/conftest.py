# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fixtures for DeepAgent + worktree integration tests.

The fixtures here intentionally use a real git repository on disk so the
``GitBackend`` exercises the actual ``git worktree add/remove`` command
path rather than a mock. Each test gets its own ``tmp_path`` so there is
no cross-test contention on the worktrees directory or the git refs.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest

from openjiuwen.core.sys_operation.cwd import CwdState, _cwd_state
from openjiuwen.harness.tools.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorktreeConfig,
    WorktreeManager,
    set_current_session,
)


def _git(cwd: str, *args: str) -> None:
    """Run a git command inside ``cwd``, surfacing stderr on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_git_repo(repo_path: str) -> None:
    """Initialize a minimal git repo with one commit and an origin/main ref.

    ``GitBackend._resolve_base`` looks up ``origin/<default-branch>`` to pick
    the base for the new worktree branch, so we need to populate that ref
    locally even though there is no real remote.
    """
    os.makedirs(repo_path, exist_ok=True)
    _git(repo_path, "init", "--quiet")
    # Repoint HEAD before the first commit so ``main`` is the initial branch
    # regardless of git version (``--initial-branch`` requires git >= 2.28).
    _git(repo_path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Test User")
    readme = Path(repo_path) / "README.md"
    readme.write_text("# integration test repo\n", encoding="utf-8")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "--quiet", "-m", "init")
    _git(repo_path, "update-ref", "refs/remotes/origin/main", "HEAD")


@dataclass
class WorktreeBed:
    """Bundled fixture handles passed into each test.

    Keeping these in one object avoids a fixture explosion when tests need
    the repo, the workspace, the manager and the captured events together.
    """

    repo_root: Path
    workspace_root: Path
    manager: WorktreeManager
    enter_tool: EnterWorktreeTool
    exit_tool: ExitWorktreeTool
    events: list[Any]


@pytest.fixture(autouse=True)
def _isolate_context_state() -> Iterator[None]:
    """Reset worktree session and cwd ContextVar before/after each test."""
    set_current_session(None)
    _cwd_state.set(CwdState())
    yield
    set_current_session(None)
    _cwd_state.set(CwdState())


@pytest.fixture
def worktree_bed(tmp_path: Path) -> WorktreeBed:
    """Real git repo + workspace + WorktreeManager bound to a captured event log.

    The workspace is placed *inside* the repo so DeepAgent's
    ``init_cwd(workspace, workspace=workspace)`` lands the cwd in a directory
    that ``find_canonical_git_root`` can walk up from to discover the repo.
    """
    repo_root = tmp_path / "repo"
    _init_git_repo(str(repo_root))
    workspace_root = repo_root / "wkspc"
    workspace_root.mkdir(parents=True, exist_ok=True)

    captured: list[Any] = []

    async def event_handler(event: Any) -> None:
        captured.append(event)

    manager = WorktreeManager(
        WorktreeConfig(enabled=True),
        workspace_root=str(workspace_root),
        event_handler=event_handler,
    )
    return WorktreeBed(
        repo_root=repo_root,
        workspace_root=workspace_root,
        manager=manager,
        enter_tool=EnterWorktreeTool(manager),
        exit_tool=ExitWorktreeTool(manager),
        events=captured,
    )
