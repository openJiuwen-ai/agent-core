# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for openjiuwen.harness.tools.worktree.rails.

Covers the injection rail (``WorktreeRail``) lifecycle: ``init`` builds
the manager + registers enter/exit tools on the agent, ``uninit``
unwinds both registrations cleanly. Lifecycle hook subclasses
(``AutoSetupRail`` / ``DiffSummaryRail``) are exercised by the manager
tests, so we only assert their inheritance contract here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation.cwd import (
    get_cwd,
    get_original_cwd,
    init_cwd,
)
from openjiuwen.harness.tools.worktree import (
    AutoSetupRail,
    DiffSummaryRail,
    WorktreeCreatedEvent,
    WorktreeLifecycleRail,
    WorktreeManager,
    WorktreeRail,
    WorktreeSession,
    get_current_session,
    set_current_session,
)
from openjiuwen.harness.tools.worktree.rails import _SESSION_STATE_KEY


# --- Stubs --------------------------------------------------------------------


@dataclass
class _StubCard:
    id: str = "test_agent"
    name: str = "test_agent"


@dataclass
class _StubAbilityManager:
    added: list[Any] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def add(self, card: Any) -> None:
        self.added.append(card)

    def remove(self, name: str) -> None:
        self.removed.append(name)


@dataclass
class _StubPromptBuilder:
    language: str = "cn"


@dataclass
class _StubAgent:
    card: _StubCard = field(default_factory=_StubCard)
    ability_manager: _StubAbilityManager = field(default_factory=_StubAbilityManager)
    system_prompt_builder: _StubPromptBuilder = field(default_factory=_StubPromptBuilder)


# --- Tests --------------------------------------------------------------------


def test_worktree_rail_defaults_before_init():
    """Manager is None and priority matches SysOperationRail before init."""
    rail = WorktreeRail()
    assert rail.priority == 100
    assert rail.manager is None


def test_worktree_rail_init_registers_two_tools_on_agent():
    """``init`` should mount exactly enter_worktree + exit_worktree."""
    rail = WorktreeRail()
    agent = _StubAgent()

    with (
        patch.object(WorktreeManager, "__init__", return_value=None) as mock_mgr,
        patch("openjiuwen.harness.tools.worktree.rails.Runner") as mock_runner,
    ):
        mock_runner.resource_mgr.add_tool = MagicMock()
        rail.init(agent)

    assert mock_mgr.call_count == 1, "WorktreeManager should be built exactly once"
    assert isinstance(rail.manager, WorktreeManager)
    tool_names = sorted(card.name for card in agent.ability_manager.added)
    assert tool_names == ["enter_worktree", "exit_worktree"]


def test_worktree_rail_init_forwards_event_handler():
    """User-supplied event handler should reach the manager untouched."""

    async def handler(event: Any) -> None:
        _ = event

    rail = WorktreeRail(event_handler=handler)
    agent = _StubAgent()

    captured: dict[str, Any] = {}

    def fake_init(self, config, *, backend=None, event_handler=None, rails=None):
        _ = self, backend
        captured["config"] = config
        captured["event_handler"] = event_handler
        captured["rails"] = list(rails or [])

    with (
        patch.object(WorktreeManager, "__init__", fake_init),
        patch("openjiuwen.harness.tools.worktree.rails.Runner") as mock_runner,
    ):
        mock_runner.resource_mgr.add_tool = MagicMock()
        rail.init(agent)

    assert captured["event_handler"] is handler


def test_worktree_rail_uninit_removes_tools_from_both_managers():
    """``uninit`` must unwind ability manager AND resource manager."""
    rail = WorktreeRail()
    agent = _StubAgent()

    with (
        patch.object(WorktreeManager, "__init__", return_value=None),
        patch("openjiuwen.harness.tools.worktree.rails.Runner") as mock_runner,
    ):
        mock_runner.resource_mgr.add_tool = MagicMock()
        mock_runner.resource_mgr.remove_tool = MagicMock()
        rail.init(agent)
        tool_ids = [getattr(t.card, "id", None) for t in rail._tools]
        rail.uninit(agent)

    assert sorted(agent.ability_manager.removed) == ["enter_worktree", "exit_worktree"]
    removed_ids = [call.args[0] for call in mock_runner.resource_mgr.remove_tool.call_args_list]
    assert sorted(removed_ids) == sorted(filter(None, tool_ids))
    assert rail.manager is None
    assert rail._tools == []


def test_worktree_rail_init_tool_cards_carry_agent_id():
    """Tool ids should be deterministic per-agent (uses agent.card.id)."""
    rail = WorktreeRail()
    agent = _StubAgent(card=_StubCard(id="agent-abc"))

    with (
        patch.object(WorktreeManager, "__init__", return_value=None),
        patch("openjiuwen.harness.tools.worktree.rails.Runner") as mock_runner,
    ):
        mock_runner.resource_mgr.add_tool = MagicMock()
        rail.init(agent)

    assert all(isinstance(t, Tool) for t in rail._tools)
    assert all(t.card.id.endswith("agent-abc") for t in rail._tools)


def test_lifecycle_rail_subclasses_inherit_from_lifecycle_base():
    """Built-in hook rails must subclass the renamed lifecycle base."""
    assert issubclass(AutoSetupRail, WorktreeLifecycleRail)
    assert issubclass(DiffSummaryRail, WorktreeLifecycleRail)
    assert not issubclass(WorktreeRail, WorktreeLifecycleRail)


def test_lifecycle_rail_hooks_are_noop_by_default():
    """The hook base class should be safe to subclass without overrides."""

    class _NoOverrides(WorktreeLifecycleRail):
        pass

    rail = _NoOverrides()
    # All hooks expose async signatures; default impls return None/True/[] as
    # documented. We only spot-check that calling them does not raise.
    import asyncio

    ctx = MagicMock()
    session = MagicMock()
    asyncio.run(rail.after_worktree_create(ctx, session))
    assert asyncio.run(rail.before_worktree_exit(ctx, session, "keep")) is None
    assert asyncio.run(rail.on_worktree_file_write(ctx, session, "/foo")) is True


def test_create_event_dataclass_still_usable():
    """Sanity: dataclass import still works through the renamed module."""
    event = WorktreeCreatedEvent(
        worktree_name="demo",
        worktree_path="/tmp/demo",
        owner_id=None,
        tag=None,
        existed=False,
    )
    assert event.worktree_name == "demo"


# --- before_invoke / after_invoke bridging with agent Session -----------------


class _StubSession:
    """Minimal Session stand-in exposing get_state / update_state."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._state: dict[str, Any] = dict(initial or {})

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, data: dict[str, Any]) -> None:
        self._state.update(data)


def _make_session(name: str = "demo") -> WorktreeSession:
    return WorktreeSession(
        original_cwd="/repo",
        worktree_path=f"/workspace/.worktrees/{name}",
        worktree_name=name,
    )


def _make_ctx(session: _StubSession | None) -> Any:
    ctx = MagicMock()
    ctx.session = session
    return ctx


@pytest.fixture(autouse=True)
def _reset_contextvar_session():
    """Ensure each test starts and ends with a clean ContextVar."""
    set_current_session(None)
    yield
    set_current_session(None)


def test_before_invoke_restores_session_from_dict_state():
    """``before_invoke`` should hydrate ContextVar from the persisted dict."""
    rail = WorktreeRail()
    persisted = _make_session("alpha").model_dump()
    session = _StubSession({_SESSION_STATE_KEY: persisted})

    asyncio.run(rail.before_invoke(_make_ctx(session)))

    current = get_current_session()
    assert isinstance(current, WorktreeSession)
    assert current.worktree_name == "alpha"


def test_before_invoke_tolerates_legacy_object_payload():
    """Backwards compat: a stashed BaseModel (not dict) is still accepted."""
    rail = WorktreeRail()
    persisted = _make_session("legacy")
    session = _StubSession({_SESSION_STATE_KEY: persisted})

    asyncio.run(rail.before_invoke(_make_ctx(session)))

    assert get_current_session() is persisted


def test_before_invoke_resets_contextvar_when_state_missing():
    """No stashed state -> ContextVar is cleared, even if stale."""
    rail = WorktreeRail()
    set_current_session(_make_session("stale"))
    session = _StubSession({})

    asyncio.run(rail.before_invoke(_make_ctx(session)))

    assert get_current_session() is None


def test_before_invoke_noop_when_session_absent():
    """``ctx.session=None`` must not crash; ContextVar untouched."""
    rail = WorktreeRail()
    sentinel = _make_session("kept")
    set_current_session(sentinel)

    asyncio.run(rail.before_invoke(_make_ctx(None)))

    assert get_current_session() is sentinel


def test_after_invoke_persists_current_session_as_dict():
    """``after_invoke`` should dump the ContextVar session into Session.state."""
    rail = WorktreeRail()
    active = _make_session("beta")
    set_current_session(active)
    session = _StubSession()

    asyncio.run(rail.after_invoke(_make_ctx(session)))

    stored = session.get_state(_SESSION_STATE_KEY)
    assert isinstance(stored, dict)
    assert stored["worktree_name"] == "beta"


def test_after_invoke_persists_none_to_clear_state():
    """Exiting the worktree must propagate the clear to Session.state."""
    rail = WorktreeRail()
    session = _StubSession({_SESSION_STATE_KEY: _make_session("old").model_dump()})

    asyncio.run(rail.after_invoke(_make_ctx(session)))

    assert session.get_state(_SESSION_STATE_KEY) is None


def test_after_invoke_noop_when_session_absent():
    """``ctx.session=None`` must not crash."""
    rail = WorktreeRail()
    set_current_session(_make_session("any"))

    # Should not raise.
    asyncio.run(rail.after_invoke(_make_ctx(None)))


def test_invoke_roundtrip_survives_a_simulated_resume():
    """End-to-end: enter in invoke 1, resume in invoke 2 sees same worktree."""
    rail = WorktreeRail()
    session = _StubSession()

    # Invoke 1: agent enters a worktree via tool, after_invoke persists.
    set_current_session(_make_session("resumable"))
    asyncio.run(rail.after_invoke(_make_ctx(session)))

    # Simulate a new task/contextvar binding for the next invoke.
    set_current_session(None)
    assert get_current_session() is None

    # Invoke 2: before_invoke restores from the same Session.
    asyncio.run(rail.before_invoke(_make_ctx(session)))

    restored = get_current_session()
    assert isinstance(restored, WorktreeSession)
    assert restored.worktree_name == "resumable"


# --- cwd sync on resume -------------------------------------------------------


def test_before_invoke_redirects_cwd_to_worktree_path(tmp_path):
    """Restoring a worktree session must also redirect cwd + original_cwd.

    ``EnterWorktreeTool`` flips both ContextVars on entry. A partial
    restore that only repoints the worktree-session ContextVar would
    leave resumed bash / write_file calls running in the previous cwd
    (typically the repo root), defeating the interrupt/resume promise.
    """
    rail = WorktreeRail()
    # Pretend the parent task was sitting in the repo root before
    # the resume — mirrors the demo, which calls set_cwd(repo) just
    # before the second Runner.run_agent.
    init_cwd(str(tmp_path))
    assert get_cwd() == str(tmp_path.resolve())

    worktree_dir = tmp_path / ".worktrees" / "redirect"
    worktree_dir.mkdir(parents=True)
    stored = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(worktree_dir),
        worktree_name="redirect",
    )
    session = _StubSession({_SESSION_STATE_KEY: stored.model_dump()})

    asyncio.run(rail.before_invoke(_make_ctx(session)))

    expected = str(worktree_dir.resolve())
    assert get_cwd() == expected
    assert get_original_cwd() == expected


def test_before_invoke_leaves_cwd_alone_when_no_stored_session(tmp_path):
    """No stored worktree => cwd belongs to whoever set it last, untouched."""
    rail = WorktreeRail()
    init_cwd(str(tmp_path))
    untouched = get_cwd()
    session = _StubSession({})

    asyncio.run(rail.before_invoke(_make_ctx(session)))

    assert get_cwd() == untouched
