# coding: utf-8

"""Tests for openjiuwen.harness.tools.worktree.backend."""

import os
import subprocess

import pytest
from unittest.mock import AsyncMock

from openjiuwen.harness.tools.worktree.backend import (
    GitBackend,
    WorktreeBackend,
    _BACKEND_REGISTRY,
    create_backend,
    register_worktree_backend,
)
from openjiuwen.harness.tools.worktree.models import WorktreeConfig
from tests.test_logger import logger


@pytest.mark.asyncio
async def test_remote_base_fetch_without_tracking_ref_falls_back_to_head(monkeypatch):
    """Remote node: a single-branch refspec can fetch main without creating origin/main."""
    head = "b" * 40
    resolve = AsyncMock(side_effect=[None, None, head])
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.backend.get_default_branch", AsyncMock(return_value="main"))
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.backend.rev_parse", resolve)
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.backend.fetch_ref", fetch)

    assert await GitBackend(remote_base=True)._resolve_base("repo") == ("HEAD", head)
    fetch.assert_awaited_once_with("repo", "main")


def _git_out(repo: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: str, name: str) -> str:
    with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
        f.write(name)
    _git_out(repo, "add", name)
    _git_out(repo, "commit", "-m", name)
    return _git_out(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_local_base_prefers_current_branch_over_defaults_and_remote(tmp_git_repo):
    """Unpushed commits on the checked-out branch are the base; origin/HEAD is never read."""
    _git_out(tmp_git_repo, "branch", "-M", "main")
    _git_out(tmp_git_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git_out(tmp_git_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git_out(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "unpushed.txt")

    assert await GitBackend()._resolve_base(tmp_git_repo) == ("feature", feature_sha)


@pytest.mark.asyncio
async def test_local_base_detached_head_uses_local_default_before_remote(tmp_git_repo):
    _git_out(tmp_git_repo, "branch", "-M", "develop")
    develop_sha = _git_out(tmp_git_repo, "rev-parse", "HEAD")
    remote_sha = _commit(tmp_git_repo, "remote.txt")
    _git_out(tmp_git_repo, "update-ref", "refs/remotes/origin/main", remote_sha)
    _git_out(tmp_git_repo, "reset", "-q", "--hard", develop_sha)
    _git_out(tmp_git_repo, "checkout", "-q", "--detach", remote_sha)

    assert await GitBackend()._resolve_base(tmp_git_repo) == ("develop", develop_sha)


@pytest.mark.asyncio
async def test_local_base_falls_back_to_remote_then_detached_head(tmp_git_repo):
    """Without any local branch, an existing origin/<default> wins; without that, HEAD."""
    _git_out(tmp_git_repo, "branch", "-M", "main")
    head = _git_out(tmp_git_repo, "rev-parse", "HEAD")
    _git_out(tmp_git_repo, "checkout", "-q", "--detach")
    _git_out(tmp_git_repo, "branch", "-D", "main")
    assert await GitBackend()._resolve_base(tmp_git_repo) == ("HEAD", head)

    _git_out(tmp_git_repo, "update-ref", "refs/remotes/origin/master", head)
    assert await GitBackend()._resolve_base(tmp_git_repo) == ("origin/master", head)


@pytest.mark.asyncio
async def test_create_uses_resolved_commit_not_a_moving_ref(monkeypatch, tmp_path):
    sha = "c" * 40
    backend = GitBackend(WorktreeConfig(longpaths_support=False))
    monkeypatch.setattr(backend, "_resolve_base", AsyncMock(return_value=("origin/develop", sha)))
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.backend.read_worktree_head_sha", AsyncMock(return_value=None))
    add = AsyncMock()
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.backend.worktree_add", add)

    result = await backend.create("fixed-base", "repo", str(tmp_path / "worktree"))

    assert add.await_args.args[3] == sha
    assert result.base_branch == "origin/develop"
    assert result.head_commit == sha


def _wt_target(tmp_path, slug: str) -> str:
    """Compute a deterministic worktree target path under a tmp workspace."""
    import os

    return os.path.join(str(tmp_path), "ws", ".worktrees", slug)


class TestGitBackendCreate:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_new_worktree(self, tmp_git_repo: str, worktree_config: WorktreeConfig, tmp_path):
        backend = GitBackend(worktree_config)
        target = _wt_target(tmp_path, "test-slug")
        result = await backend.create("test-slug", tmp_git_repo, target)

        assert not result.existed
        assert result.worktree_path == target
        assert result.worktree_branch == "worktree-test-slug"
        assert result.head_commit is not None
        logger.info("GitBackend.create new worktree: path=%s", result.worktree_path)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_fast_recovery(self, tmp_git_repo: str, worktree_config: WorktreeConfig, tmp_path):
        backend = GitBackend(worktree_config)
        target = _wt_target(tmp_path, "recover-slug")
        first = await backend.create("recover-slug", tmp_git_repo, target)
        assert not first.existed

        second = await backend.create("recover-slug", tmp_git_repo, target)
        assert second.existed
        assert second.worktree_path == first.worktree_path
        assert second.head_commit is not None
        logger.info("GitBackend.create fast recovery verified")


class TestGitBackendRemove:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_remove_existing(self, tmp_git_repo: str, worktree_config: WorktreeConfig, tmp_path):
        backend = GitBackend(worktree_config)
        target = _wt_target(tmp_path, "remove-me")
        result = await backend.create("remove-me", tmp_git_repo, target)
        assert not result.existed

        ok = await backend.remove(result.worktree_path, tmp_git_repo)
        assert ok is True
        logger.info("GitBackend.remove succeeded")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_remove_nonexistent_raises(self, tmp_git_repo: str, worktree_config: WorktreeConfig):
        """Removing a non-existent worktree raises FileNotFoundError
        because git subprocess cannot chdir into it."""
        backend = GitBackend(worktree_config)
        with pytest.raises(FileNotFoundError):
            await backend.remove("/tmp/nonexistent-worktree-path-xyz", tmp_git_repo)
        logger.info("GitBackend.remove raises FileNotFoundError for missing path")


class TestGitBackendExists:
    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_exists_true(self, tmp_git_repo: str, worktree_config: WorktreeConfig, tmp_path):
        backend = GitBackend(worktree_config)
        target = _wt_target(tmp_path, "exists-check")
        result = await backend.create("exists-check", tmp_git_repo, target)
        assert await backend.exists(result.worktree_path) is True
        logger.info("GitBackend.exists returns True for valid worktree")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_exists_false(self, worktree_config: WorktreeConfig):
        backend = GitBackend(worktree_config)
        assert await backend.exists("/tmp/nonexistent-path") is False
        logger.info("GitBackend.exists returns False for nonexistent path")


class TestCreateBackend:
    @pytest.mark.level1
    def test_git_backend(self):
        backend = create_backend("git")
        assert isinstance(backend, GitBackend)
        logger.info("create_backend('git') returns GitBackend")

    @pytest.mark.level1
    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown worktree backend"):
            create_backend("nonexistent-backend")
        logger.info("create_backend unknown name raises ValueError")


class TestRegisterWorktreeBackend:
    @pytest.mark.level1
    def test_register_custom(self):
        original_keys = set(_BACKEND_REGISTRY.keys())

        class FakeBackend:
            def __init__(self, config):
                pass

        register_worktree_backend("fake", FakeBackend)
        try:
            backend = create_backend("fake")
            assert isinstance(backend, FakeBackend)
            logger.info("register_worktree_backend custom backend works")
        finally:
            # Cleanup registry
            _BACKEND_REGISTRY.pop("fake", None)
            assert set(_BACKEND_REGISTRY.keys()) == original_keys


class TestWorktreeBackendProtocol:
    @pytest.mark.level1
    def test_git_backend_is_worktree_backend(self):
        backend = GitBackend()
        assert isinstance(backend, WorktreeBackend)
        logger.info("GitBackend satisfies WorktreeBackend protocol")
