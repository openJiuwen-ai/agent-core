# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Offline regression for develop-only clones with no origin/HEAD.

Run with pytest; all repositories and worktrees are below tmp_path. No model,
network, running team, or user repository is used.
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.worktree import GitBackend, WorktreeConfig


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _develop_only_clone(tmp_path: Path) -> Path:
    """Clone only develop, drop origin/HEAD, and add a local unpushed commit on a work branch."""
    remote = tmp_path / "local-remote"
    remote.mkdir()
    _git(remote, "init", "--initial-branch=main")
    _git(remote, "config", "user.email", "test@example.invalid")
    _git(remote, "config", "user.name", "Test")
    (remote / "README.md").write_text("main baseline\n", encoding="utf-8")
    _git(remote, "add", "README.md")
    _git(remote, "commit", "-m", "main")
    _git(remote, "checkout", "-b", "develop")
    (remote / "source.txt").write_text("frozen develop source\n", encoding="utf-8")
    _git(remote, "add", "source.txt")
    _git(remote, "commit", "-m", "develop")

    repo = tmp_path / "experiment"
    _git(tmp_path, "clone", "--single-branch", "--branch", "develop", "--no-hardlinks", str(remote), str(repo))
    if (repo / ".git" / "refs" / "remotes" / "origin" / "HEAD").exists():
        _git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "checkout", "-b", "experiment/migration")
    (repo / "control.txt").write_text("local intake commit\n", encoding="utf-8")
    _git(repo, "add", "control.txt")
    _git(repo, "commit", "-m", "intake")
    return repo


@pytest.mark.asyncio
async def test_local_worktree_branches_from_current_local_branch(tmp_path):
    """Local-first: the member sees the leader's unpushed commit, not origin/develop."""
    repo = _develop_only_clone(tmp_path)
    expected_sha = _git(repo, "rev-parse", "HEAD")

    target = tmp_path / "member-worktree"
    result = await GitBackend(WorktreeConfig(enabled=True)).create("researcher", str(repo), str(target))

    assert result.base_branch == "experiment/migration"
    assert result.head_commit == expected_sha == _git(target, "rev-parse", "HEAD")
    assert (target / "source.txt").read_text(encoding="utf-8") == "frozen develop source\n"
    assert (target / "control.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_tracking_ref", [False, True])
async def test_remote_node_creates_worktree_without_origin_main(tmp_path, monkeypatch, missing_tracking_ref):
    """Remote-first (remote nodes): a fetch may update only FETCH_HEAD under a narrow refspec."""
    repo = _develop_only_clone(tmp_path)
    expected_ref = "HEAD" if missing_tracking_ref else "origin/develop"
    expected_sha = _git(repo, "rev-parse", expected_ref)
    if missing_tracking_ref:
        monkeypatch.setattr(
            "openjiuwen.harness.tools.worktree.backend.get_default_branch", AsyncMock(return_value="main"),
        )

    target = tmp_path / "member-worktree"
    backend = GitBackend(WorktreeConfig(enabled=True), remote_base=True)
    result = await backend.create("researcher", str(repo), str(target))

    assert result.base_branch == expected_ref
    assert result.head_commit == expected_sha == _git(target, "rev-parse", "HEAD")
    assert (target / "source.txt").read_text(encoding="utf-8") == "frozen develop source\n"
    assert (target / "control.txt").exists() is missing_tracking_ref
    assert _git(repo, "config", "--get-all", "remote.origin.fetch") == (
        "+refs/heads/develop:refs/remotes/origin/develop"
    )
    missing_main = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/remotes/origin/main"], cwd=repo, capture_output=True,
    )
    assert missing_main.returncode != 0
