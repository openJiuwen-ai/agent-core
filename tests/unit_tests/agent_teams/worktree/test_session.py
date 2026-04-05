# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.session."""

import asyncio

import pytest

from openjiuwen.agent_teams.worktree.models import WorktreeSession
from openjiuwen.agent_teams.worktree.session import (
    get_current_session,
    require_current_session,
    set_current_session,
)
from tests.test_logger import logger


def _make_session(name: str = "test") -> WorktreeSession:
    return WorktreeSession(
        original_cwd="/repo",
        worktree_path=f"/repo/.agent_teams/worktrees/{name}",
        worktree_name=name,
    )


class TestGetCurrentSession:
    def test_default_none(self):
        # Reset to clean state
        set_current_session(None)
        assert get_current_session() is None
        logger.info("get_current_session default None verified")


class TestSetCurrentSession:
    def test_set_and_get(self):
        session = _make_session()
        set_current_session(session)
        assert get_current_session() is session
        logger.info("set_current_session + get verified")
        # Cleanup
        set_current_session(None)

    def test_clear(self):
        session = _make_session()
        set_current_session(session)
        set_current_session(None)
        assert get_current_session() is None


class TestRequireCurrentSession:
    def test_raises_when_none(self):
        set_current_session(None)
        with pytest.raises(RuntimeError, match="Not in a worktree session"):
            require_current_session()
        logger.info("require_current_session raises RuntimeError verified")

    def test_returns_session(self):
        session = _make_session()
        set_current_session(session)
        result = require_current_session()
        assert result is session
        # Cleanup
        set_current_session(None)


@pytest.mark.asyncio
class TestContextVarIsolation:
    async def test_isolation_across_tasks(self):
        """ContextVar should be isolated between tasks spawned independently."""
        set_current_session(None)
        barrier = asyncio.Event()
        results: dict[str, WorktreeSession | None] = {}

        async def task_a():
            session_a = _make_session("task-a")
            set_current_session(session_a)
            barrier.set()
            # Let task_b run
            await asyncio.sleep(0.01)
            results["a"] = get_current_session()

        async def task_b():
            await barrier.wait()
            # task_b never sets a session — should see None
            results["b"] = get_current_session()

        await asyncio.gather(
            asyncio.create_task(task_a()),
            asyncio.create_task(task_b()),
        )

        assert results["a"] is not None
        assert results["a"].worktree_name == "task-a"
        assert results["b"] is None
        logger.info("ContextVar isolation across tasks verified")

        # Cleanup
        set_current_session(None)
