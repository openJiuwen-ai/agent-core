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
        worktree_path=f"/workspace/.worktrees/{name}",
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
class TestSharedContainerAcrossGather:
    async def test_mutation_propagates_within_gather(self):
        """Tasks spawned via asyncio.gather share the same mutable session holder.

        This is the documented contract (see session.py module docstring):
        asyncio.gather copies the ContextVar *binding* (reference to the
        holder), not the holder itself, so sibling tasks see each other's
        mutations. This property is relied upon so that tool calls within
        a single agent's turn observe a consistent session.
        """
        set_current_session(None)
        results: dict[str, WorktreeSession | None] = {}
        a_done = asyncio.Event()

        async def task_a():
            set_current_session(_make_session("shared"))
            a_done.set()
            results["a"] = get_current_session()

        async def task_b():
            # Wait until task_a mutated the shared holder
            await a_done.wait()
            results["b"] = get_current_session()

        await asyncio.gather(task_a(), task_b())

        assert results["a"] is not None
        assert results["a"].worktree_name == "shared"
        # task_b never called set_current_session itself, but because the
        # holder is shared by reference it observes task_a's mutation.
        assert results["b"] is results["a"]
        logger.info("Shared session holder across gather tasks verified")

        # Cleanup
        set_current_session(None)
