# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.agent_teams.worktree.manager import WorktreeManager
from openjiuwen.agent_teams.worktree.models import (
    WorktreeChangeSummary,
    WorktreeConfig,
    WorktreeCreateResult,
    WorktreeLifecyclePolicy,
)
from openjiuwen.agent_teams.worktree.session import get_current_session, set_current_session
from openjiuwen.core.sys_operation.cwd import CwdState, _cwd_state, init_cwd
from tests.test_logger import logger


MOCK_WORKSPACE = "/mock/workspace"


@pytest.fixture
def mock_backend():
    backend = AsyncMock()
    backend.create = AsyncMock(return_value=WorktreeCreateResult(
        worktree_path="/mock/workspace/.worktrees/test-wt",
        worktree_branch="worktree-test",
        head_commit="abc123",
        existed=False,
    ))
    backend.remove = AsyncMock(return_value=True)
    backend.exists = AsyncMock(return_value=True)
    return backend


@pytest.fixture(autouse=True)
def _clean_session():
    """Ensure ContextVar state is clean before and after each test.

    Also seeds CwdState with a workspace so ``_resolve_target_path``
    can derive a worktree target without raising.  On teardown the
    CwdState is reset to a fresh empty instance so subsequent test
    files in the same process don't see leaked mock paths.
    """
    set_current_session(None)
    init_cwd("/mock/cwd", workspace=MOCK_WORKSPACE)
    yield
    set_current_session(None)
    _cwd_state.set(CwdState())


def _make_manager(
    backend: AsyncMock,
    config: WorktreeConfig | None = None,
    publish_event: AsyncMock | None = None,
    rails: list | None = None,
) -> WorktreeManager:
    return WorktreeManager(
        config=config or WorktreeConfig(enabled=True),
        backend=backend,
        publish_event=publish_event,
        rails=rails,
    )


class TestEnter:
    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    async def test_enter_creates_worktree_and_sets_session(
        self, mock_branch, mock_git_root, mock_backend
    ):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"

        mgr = _make_manager(mock_backend)
        session = await mgr.enter("my-slug", member_name="m1", team_name="t1")

        assert session.worktree_path == "/mock/workspace/.worktrees/test-wt"
        assert session.worktree_name == "my-slug"
        assert session.member_name == "m1"
        assert session.team_name == "t1"
        assert session.original_branch == "main"
        mock_backend.create.assert_awaited_once_with(
            "my-slug",
            "/repo",
            f"{MOCK_WORKSPACE}/.worktrees/my-slug",
        )

        # ContextVar should be set
        assert get_current_session() is session
        logger.info("enter sets ContextVar and returns session")

    @pytest.mark.asyncio
    async def test_enter_invalid_slug_raises(self, mock_backend):
        mgr = _make_manager(mock_backend)
        with pytest.raises(ValueError, match="Invalid worktree name"):
            await mgr.enter("../escape")
        logger.info("enter rejects invalid slug")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    async def test_enter_not_in_git_repo_raises(self, mock_git_root, mock_backend):
        mock_git_root.return_value = None

        mgr = _make_manager(mock_backend)
        with pytest.raises(RuntimeError, match="not in a git repository"):
            await mgr.enter("valid-slug")
        logger.info("enter raises RuntimeError outside git repo")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    async def test_enter_publishes_event(self, mock_branch, mock_git_root, mock_backend):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"

        publish = AsyncMock()
        mgr = _make_manager(mock_backend, publish_event=publish)
        await mgr.enter("ev-slug", member_name="m1", team_name="t1")

        publish.assert_awaited_once()
        logger.info("enter publishes WorktreeCreatedEvent")


class TestExit:
    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    async def test_exit_keep(self, mock_branch, mock_git_root, mock_backend):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"

        mgr = _make_manager(mock_backend)
        await mgr.enter("keep-slug")

        result = await mgr.exit("keep")
        assert result["action"] == "keep"
        assert get_current_session() is None
        mock_backend.remove.assert_not_awaited()
        logger.info("exit(keep) clears session without removal")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.count_commits_since", new_callable=AsyncMock)
    async def test_exit_remove(
        self, mock_commits, mock_status, mock_branch, mock_git_root, mock_backend
    ):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"
        mock_status.return_value = []
        mock_commits.return_value = 0

        mgr = _make_manager(mock_backend)
        await mgr.enter("rm-slug")

        result = await mgr.exit("remove")
        assert result["action"] == "remove"
        assert get_current_session() is None
        mock_backend.remove.assert_awaited_once()
        logger.info("exit(remove) removes worktree and clears session")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.count_commits_since", new_callable=AsyncMock)
    async def test_exit_remove_with_changes_raises(
        self, mock_commits, mock_status, mock_branch, mock_git_root, mock_backend
    ):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"
        mock_status.return_value = ["M file.py"]
        mock_commits.return_value = 0

        mgr = _make_manager(mock_backend)
        await mgr.enter("dirty-slug")

        with pytest.raises(ValueError, match="uncommitted files"):
            await mgr.exit("remove")
        logger.info("exit(remove) raises when changes exist and discard_changes=False")

    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.get_current_branch", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.status_porcelain", new_callable=AsyncMock)
    @patch("openjiuwen.agent_teams.worktree.manager.count_commits_since", new_callable=AsyncMock)
    async def test_exit_remove_with_changes_discard(
        self, mock_commits, mock_status, mock_branch, mock_git_root, mock_backend
    ):
        mock_git_root.return_value = "/repo"
        mock_branch.return_value = "main"
        mock_status.return_value = ["M file.py"]
        mock_commits.return_value = 0

        mgr = _make_manager(mock_backend)
        await mgr.enter("discard-slug")

        result = await mgr.exit("remove", discard_changes=True)
        assert result["action"] == "remove"
        mock_backend.remove.assert_awaited_once()
        logger.info("exit(remove, discard_changes=True) proceeds despite changes")


class TestCreateAgentWorktree:
    @pytest.mark.asyncio
    @patch("openjiuwen.agent_teams.worktree.manager.find_canonical_git_root", new_callable=AsyncMock)
    async def test_does_not_modify_context_var(self, mock_git_root, mock_backend):
        mock_git_root.return_value = "/repo"

        mgr = _make_manager(mock_backend)
        result = await mgr.create_agent_worktree("agent-slug")

        assert result.worktree_path == "/mock/workspace/.worktrees/test-wt"
        assert get_current_session() is None
        logger.info("create_agent_worktree does not set ContextVar")

    @pytest.mark.asyncio
    async def test_invalid_slug_raises(self, mock_backend):
        mgr = _make_manager(mock_backend)
        with pytest.raises(ValueError, match="Invalid worktree name"):
            await mgr.create_agent_worktree("../../bad")
        logger.info("create_agent_worktree rejects invalid slug")


class TestMemberSlug:
    def test_format(self, mock_backend):
        mgr = _make_manager(mock_backend)
        slug = mgr._member_slug("abcdef1234567890")
        assert slug == "teammate-abcdef12"
        logger.info("_member_slug truncates to 8 chars: %s", slug)

    def test_short_id(self, mock_backend):
        mgr = _make_manager(mock_backend)
        slug = mgr._member_slug("abc")
        assert slug == "teammate-abc"


class TestResolvePolicy:
    def test_auto_resolves_to_ephemeral(self, mock_backend):
        config = WorktreeConfig(enabled=True, lifecycle_policy=WorktreeLifecyclePolicy.AUTO)
        mgr = _make_manager(mock_backend, config=config)
        assert mgr._resolve_policy() == WorktreeLifecyclePolicy.EPHEMERAL

    def test_explicit_durable(self, mock_backend):
        config = WorktreeConfig(enabled=True, lifecycle_policy=WorktreeLifecyclePolicy.DURABLE)
        mgr = _make_manager(mock_backend, config=config)
        assert mgr._resolve_policy() == WorktreeLifecyclePolicy.DURABLE
        logger.info("_resolve_policy respects explicit policy")


class TestFireRail:
    @pytest.mark.asyncio
    async def test_calls_all_rails(self, mock_backend):
        rail_a = MagicMock()
        rail_a.on_enter = AsyncMock(return_value="a")
        rail_b = MagicMock()
        rail_b.on_enter = AsyncMock(return_value="b")

        mgr = _make_manager(mock_backend, rails=[rail_a, rail_b])
        result = await mgr._fire_rail("on_enter", "arg1")

        rail_a.on_enter.assert_awaited_once_with("arg1")
        rail_b.on_enter.assert_awaited_once_with("arg1")
        # Last non-None result wins
        assert result == "b"
        logger.info("_fire_rail invokes all rails, returns last non-None")

    @pytest.mark.asyncio
    async def test_skips_rails_without_method(self, mock_backend):
        rail_no_method = MagicMock(spec=[])  # No attributes

        mgr = _make_manager(mock_backend, rails=[rail_no_method])
        result = await mgr._fire_rail("on_enter")

        assert result is None
        logger.info("_fire_rail skips rails without the target method")
