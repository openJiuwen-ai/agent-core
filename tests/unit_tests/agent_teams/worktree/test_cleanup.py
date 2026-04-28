# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.cleanup."""

import os
import time

import pytest
from unittest.mock import AsyncMock, patch

from openjiuwen.agent_teams.worktree.cleanup import cleanup_stale_worktrees, is_ephemeral_slug
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from openjiuwen.core.sys_operation.cwd import CwdState, _cwd_state, init_cwd
from tests.test_logger import logger


@pytest.fixture(autouse=True)
def _reset_cwd_state():
    """Reset CwdState after each test to avoid leaking mock workspace
    paths into other test files in the same process."""
    yield
    _cwd_state.set(CwdState())


class TestIsEphemeralSlug:
    @pytest.mark.level0
    def test_teammate_hex8(self):
        assert is_ephemeral_slug("teammate-a1b2c3d4") is True
        logger.info("teammate-a1b2c3d4 recognized as ephemeral")

    @pytest.mark.level0
    def test_agent_hex7(self):
        assert is_ephemeral_slug("agent-1234567") is True
        logger.info("agent-1234567 recognized as ephemeral")

    @pytest.mark.level0
    def test_feature_branch_not_ephemeral(self):
        assert is_ephemeral_slug("feature-auth") is False

    @pytest.mark.level0
    def test_arbitrary_slug_not_ephemeral(self):
        assert is_ephemeral_slug("my-worktree") is False

    @pytest.mark.level0
    def test_teammate_too_short(self):
        assert is_ephemeral_slug("teammate-abc") is False

    @pytest.mark.level1
    def test_teammate_uppercase_not_matched(self):
        assert is_ephemeral_slug("teammate-A1B2C3D4") is False

    @pytest.mark.level1
    def test_agent_too_long(self):
        assert is_ephemeral_slug("agent-12345678") is False
        logger.info("Non-ephemeral slugs correctly rejected")


class TestCleanupStaleWorktrees:
    @pytest.fixture
    def mock_backend(self):
        backend = AsyncMock()
        backend.remove = AsyncMock(return_value=True)
        return backend

    @pytest.fixture
    def wt_dir(self, tmp_path):
        """Create a fake worktrees directory under a fake agent workspace.

        Also seeds CwdState with the workspace path so
        ``cleanup_stale_worktrees`` can locate ``.worktrees/`` via
        ``get_workspace()``.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wt_base = workspace / ".worktrees"
        wt_base.mkdir(parents=True)
        init_cwd(str(repo), workspace=str(workspace))
        return repo, wt_base

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.has_unpushed_commits", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.worktree_prune", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_expired_no_changes_removed(
        self, mock_prune, mock_unpushed, mock_status, mock_git_root, mock_backend, wt_dir
    ):
        repo, wt_base = wt_dir
        mock_git_root.return_value = str(repo)
        mock_status.return_value = []
        mock_unpushed.return_value = False

        # Create expired ephemeral worktree
        slug = "teammate-a1b2c3d4"
        wt_path = wt_base / slug
        wt_path.mkdir()
        # Set mtime to 60 days ago
        old_time = time.time() - 60 * 86400
        os.utime(str(wt_path), (old_time, old_time))

        config = WorktreeConfig(enabled=True, cleanup_after_days=30)
        removed = await cleanup_stale_worktrees(config, mock_backend)

        assert removed == 1
        mock_backend.remove.assert_awaited_once()
        mock_prune.assert_awaited_once()
        logger.info("Expired worktree with no changes removed")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_not_expired_skipped(self, mock_git_root, mock_backend, wt_dir):
        repo, wt_base = wt_dir
        mock_git_root.return_value = str(repo)

        # Create recent ephemeral worktree (mtime = now)
        slug = "teammate-a1b2c3d4"
        wt_path = wt_base / slug
        wt_path.mkdir()

        config = WorktreeConfig(enabled=True, cleanup_after_days=30)
        removed = await cleanup_stale_worktrees(config, mock_backend)

        assert removed == 0
        mock_backend.remove.assert_not_awaited()
        logger.info("Non-expired worktree skipped")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.has_unpushed_commits", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_has_changes_skipped(
        self, mock_unpushed, mock_status, mock_git_root, mock_backend, wt_dir
    ):
        repo, wt_base = wt_dir
        mock_git_root.return_value = str(repo)
        mock_status.return_value = ["M dirty.py"]
        mock_unpushed.return_value = False

        slug = "teammate-b2c3d4e5"
        wt_path = wt_base / slug
        wt_path.mkdir()
        old_time = time.time() - 60 * 86400
        os.utime(str(wt_path), (old_time, old_time))

        config = WorktreeConfig(enabled=True, cleanup_after_days=30)
        removed = await cleanup_stale_worktrees(config, mock_backend)

        assert removed == 0
        mock_backend.remove.assert_not_awaited()
        logger.info("Worktree with uncommitted changes skipped (fail-closed)")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.has_unpushed_commits", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_has_unpushed_commits_skipped(
        self, mock_unpushed, mock_status, mock_git_root, mock_backend, wt_dir
    ):
        repo, wt_base = wt_dir
        mock_git_root.return_value = str(repo)
        mock_status.return_value = []
        mock_unpushed.return_value = True  # Unpushed commits

        slug = "agent-1234567"
        wt_path = wt_base / slug
        wt_path.mkdir()
        old_time = time.time() - 60 * 86400
        os.utime(str(wt_path), (old_time, old_time))

        config = WorktreeConfig(enabled=True, cleanup_after_days=30)
        removed = await cleanup_stale_worktrees(config, mock_backend)

        assert removed == 0
        mock_backend.remove.assert_not_awaited()
        logger.info("Worktree with unpushed commits skipped (fail-closed)")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.cleanup.has_unpushed_commits", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_current_worktree_skipped(
        self, mock_unpushed, mock_status, mock_git_root, mock_backend, wt_dir
    ):
        repo, wt_base = wt_dir
        mock_git_root.return_value = str(repo)
        mock_status.return_value = []
        mock_unpushed.return_value = False

        slug = "teammate-c3d4e5f6"
        wt_path = wt_base / slug
        wt_path.mkdir()
        old_time = time.time() - 60 * 86400
        os.utime(str(wt_path), (old_time, old_time))

        config = WorktreeConfig(enabled=True, cleanup_after_days=30)
        removed = await cleanup_stale_worktrees(
            config, mock_backend, current_worktree_path=str(wt_path),
        )

        assert removed == 0
        mock_backend.remove.assert_not_awaited()
        logger.info("Current worktree skipped during cleanup")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.cleanup.find_canonical_git_root", new_callable=AsyncMock)
    @pytest.mark.level1
    async def test_no_repo_returns_zero(self, mock_git_root, mock_backend):
        mock_git_root.return_value = None

        config = WorktreeConfig(enabled=True)
        removed = await cleanup_stale_worktrees(config, mock_backend)

        assert removed == 0
        logger.info("cleanup returns 0 when not in git repo")
