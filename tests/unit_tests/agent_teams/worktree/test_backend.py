# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.backend."""

import pytest

from openjiuwen.agent_teams.worktree.backend import (
    GitBackend,
    WorktreeBackend,
    _BACKEND_REGISTRY,
    create_backend,
    register_worktree_backend,
)
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from tests.test_logger import logger


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
