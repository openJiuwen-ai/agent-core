# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``openjiuwen.agent_teams.harness.TeamHarness``.

TeamHarness is the sole adapter between TeamAgent and a NativeHarness-backed
DeepAgent brain. These tests cover its three contracts:

1. ``build`` constructs a NativeHarness over a provider that mounts the team
   rails in a load-bearing order, then eagerly initializes the team-tool rail
   on the configured native so the LLM-visible ability snapshot is populated.
2. The interaction surface (``send`` / ``abort`` / ``outputs`` /
   ``on_state_changed`` / ``on_round`` / ``register_rail`` / ``find_rails``)
   forwards to the native.
3. State queries (``has_pending_interrupt`` /
   ``is_pending_interrupt_resume_valid``) tolerate a missing session and missing
   interruption state without raising, and read the agent session that survives
   a native ``loop_session`` cleanup.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness
from openjiuwen.agent_teams.harness import team_harness as harness_module
from openjiuwen.agent_teams.harness.team_harness import _MountedRails
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY


class _FakeNative:
    """Minimal stand-in for NativeHarness used by ``build`` tests.

    ``prepare_config`` runs the provider (so the template's rails get mounted)
    and adopts the template's ``deep_config`` — enough for ``build`` to read
    ``deep_config.sys_operation`` / ``workspace`` when eager-initializing the
    team-tool rail. The real native's async init / supervisor are out of scope.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self._template: Any = None
        self.deep_config: Any = None

    def prepare_config(self) -> None:
        self._template = self._provider()
        self.deep_config = self._template.deep_config


def _stub_native(
    *,
    workspace: Any = None,
    sys_operation: Any = None,
    model: Any = None,
    loop_session: Any = None,
) -> MagicMock:
    """Return a MagicMock shaped like the NativeHarness the harness forwards to."""
    native = MagicMock(name="NativeHarness")
    native.add_rail = MagicMock()
    native.register_rail = AsyncMock()
    native.unregister_rail = AsyncMock()
    native.send = AsyncMock()
    native.abort = AsyncMock()
    native.pause = AsyncMock()
    native.on_state_changed = AsyncMock()
    native.on_round = AsyncMock()
    native.deep_config = SimpleNamespace(
        workspace=workspace,
        sys_operation=sys_operation,
        model=model,
    )
    native.loop_session = loop_session
    return native


def _stub_template(*, workspace: Any = None, sys_operation: Any = None) -> MagicMock:
    """Return a MagicMock DeepAgent template produced by ``agent_spec.build``."""
    template = MagicMock(name="DeepAgentTemplate")
    template.add_rail = MagicMock()
    template.deep_config = SimpleNamespace(workspace=workspace, sys_operation=sys_operation, model=None)
    return template


def _make_harness(native: MagicMock) -> TeamHarness:
    """Wire a TeamHarness whose runtime/native target is ``native``."""
    rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    return TeamHarness(lambda: native, native, rails, role=TeamRole.LEADER, member_name="leader")


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


def test_build_mounts_rails_in_load_bearing_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider mounts all team rails in the documented order so the
    LLM-visible ability snapshot matches the test-visible one."""
    monkeypatch.setattr(harness_module, "NativeHarness", _FakeNative)
    template = _stub_template(workspace="WS", sys_operation="SYS")
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = template

    team_tool = MagicMock(name="TeamToolRail")
    team_policy = MagicMock(name="TeamPolicyRail")
    workspace_rail = MagicMock(name="TeamWorkspaceRail")
    approval = MagicMock(name="TeamToolApprovalRail")

    harness = TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
        team_workspace_rail=workspace_rail,
        tool_approval_rail=approval,
    )

    assert isinstance(harness.inner_agent, _FakeNative)
    spec.build.assert_called_once()

    mount_order = [call.args[0] for call in template.add_rail.call_args_list]
    assert mount_order == [team_tool, team_policy, workspace_rail, approval]


def test_build_eagerly_initializes_team_tool_rail(monkeypatch: pytest.MonkeyPatch) -> None:
    """team_tool_rail.set_sys_operation / set_workspace / init run AFTER the
    provider mounted the rails and the native adopted its config, so the tool
    snapshot the LLM sees between configure() and the first run includes team
    tools. init binds to the configured native (not the discarded template).
    """
    monkeypatch.setattr(harness_module, "NativeHarness", _FakeNative)
    template = _stub_template(workspace="WS", sys_operation="SYS")
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = template

    call_log: list[str] = []
    team_tool = MagicMock(name="TeamToolRail")
    team_tool.set_sys_operation.side_effect = lambda *_: call_log.append("set_sys_operation")
    team_tool.set_workspace.side_effect = lambda *_: call_log.append("set_workspace")
    team_tool.init.side_effect = lambda *_: call_log.append("init")
    team_policy = MagicMock(name="TeamPolicyRail")

    def _track_add_rail(rail: Any) -> None:
        if rail is team_tool:
            call_log.append("add_rail:team_tool")
        elif rail is team_policy:
            call_log.append("add_rail:team_policy")
        else:
            call_log.append("add_rail:other")

    template.add_rail.side_effect = _track_add_rail

    harness = TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
    )

    assert call_log == [
        "add_rail:team_tool",
        "add_rail:team_policy",
        "set_sys_operation",
        "set_workspace",
        "init",
    ]
    team_tool.set_sys_operation.assert_called_once_with("SYS")
    team_tool.set_workspace.assert_called_once_with("WS")
    team_tool.init.assert_called_once_with(harness.inner_agent)


def test_build_skips_optional_rails_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness_module, "NativeHarness", _FakeNative)
    template = _stub_template()
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = template

    team_tool = MagicMock(name="TeamToolRail")
    team_policy = MagicMock(name="TeamPolicyRail")

    TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
    )

    mounted = [call.args[0] for call in template.add_rail.call_args_list]
    assert mounted == [team_tool, team_policy]


def test_build_mounts_team_plan_mode_rail_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness_module, "NativeHarness", _FakeNative)
    template = _stub_template()
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = template

    team_tool = MagicMock(name="TeamToolRail")
    team_policy = MagicMock(name="TeamPolicyRail")
    team_plan_mode = MagicMock(name="TeamPlanModeRail")

    harness = TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
        team_plan_mode_rail=team_plan_mode,
    )

    mounted = [call.args[0] for call in template.add_rail.call_args_list]
    assert mounted == [team_tool, team_policy, team_plan_mode]
    assert harness.rails.team_plan_mode is team_plan_mode


def test_run_agent_customizer_invokes_with_native() -> None:
    """Customizer must receive (native, member_name, role.value)."""
    native = _stub_native()
    harness = _make_harness(native)
    captured: list[tuple[Any, ...]] = []

    def _customizer(agent: Any, member_name: str, role: str) -> None:
        captured.append((agent, member_name, role))

    harness.run_agent_customizer(_customizer)

    assert captured == [(native, "leader", "leader")]


def test_run_agent_customizer_swallows_exceptions() -> None:
    """A broken customizer must not abort team setup."""
    native = _stub_native()
    harness = _make_harness(native)

    def _broken(*_: Any) -> None:
        raise RuntimeError("boom")

    harness.run_agent_customizer(_broken)


# ---------------------------------------------------------------------------
# Interaction surface (forwarded to the native)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_forwards_to_native() -> None:
    native = _stub_native()
    harness = _make_harness(native)

    await harness.send("hello", immediate=True)

    native.send.assert_awaited_once_with("hello", immediate=True)


@pytest.mark.asyncio
async def test_abort_forwards_to_native() -> None:
    """``abort`` is the cancel seam — must reach the native with its mode.

    Only forwards while a run cycle is live; ``start`` binds an active agent
    session, so the test simulates that to exercise the forwarding path.
    """
    native = _stub_native()
    harness = _make_harness(native)
    harness._active_agent_session = MagicMock(name="agent_session")

    await harness.abort(immediate=True)

    native.abort.assert_awaited_once_with(immediate=True)


@pytest.mark.asyncio
async def test_abort_is_noop_without_active_cycle() -> None:
    """abort must not reach a never-started / already-stopped native."""
    native = _stub_native()
    harness = _make_harness(native)
    # No active agent session bound (native never started / already stopped).

    await harness.abort(immediate=True)

    native.abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_pause_forwards_to_native() -> None:
    native = _stub_native()
    harness = _make_harness(native)
    harness._active_agent_session = MagicMock(name="agent_session")

    await harness.pause()

    native.pause.assert_awaited_once_with()


def test_outputs_delegates_to_native() -> None:
    native = _stub_native()
    sentinel = object()
    native.outputs = MagicMock(return_value=sentinel)
    harness = _make_harness(native)

    assert harness.outputs() is sentinel
    native.outputs.assert_called_once_with()


@pytest.mark.asyncio
async def test_on_state_changed_and_on_round_forward() -> None:
    native = _stub_native()
    harness = _make_harness(native)
    state_cb = MagicMock(name="state_cb")
    round_cb = MagicMock(name="round_cb")

    await harness.on_state_changed(state_cb)
    await harness.on_round(round_cb)

    native.on_state_changed.assert_awaited_once_with(state_cb)
    native.on_round.assert_awaited_once_with(round_cb)


@pytest.mark.asyncio
async def test_register_and_unregister_rail_forward() -> None:
    native = _stub_native()
    harness = _make_harness(native)
    rail = MagicMock(name="DynamicRail")

    await harness.register_rail(rail)
    await harness.unregister_rail(rail)

    native.register_rail.assert_awaited_once_with(rail)
    native.unregister_rail.assert_awaited_once_with(rail)


def test_find_rails_delegates_to_native() -> None:
    """find_rails forwards the type query to the underlying native."""
    from openjiuwen.harness.rails import TeamSkillRail

    native = _stub_native()
    skill_rail = MagicMock(name="TeamSkillRail")
    native.find_rails_by_type = MagicMock(return_value=[skill_rail])
    harness = _make_harness(native)

    found = harness.find_rails(TeamSkillRail)

    native.find_rails_by_type.assert_called_once_with((TeamSkillRail,))
    assert found == [skill_rail]


def test_find_rails_returns_empty_when_no_match() -> None:
    """find_rails returns an empty list when no rail of the type is mounted."""
    from openjiuwen.harness.rails import TeamSkillRail

    native = _stub_native()
    native.find_rails_by_type = MagicMock(return_value=[])
    harness = _make_harness(native)

    assert harness.find_rails(TeamSkillRail) == []


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


def test_has_pending_interrupt_returns_false_without_session() -> None:
    native = _stub_native(loop_session=None)
    harness = _make_harness(native)

    assert harness.has_pending_interrupt() is False


def test_has_pending_interrupt_returns_false_without_state() -> None:
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = None
    native = _stub_native(loop_session=session)
    harness = _make_harness(native)

    assert harness.has_pending_interrupt() is False


def test_has_pending_interrupt_returns_true_when_state_present() -> None:
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = SimpleNamespace(interrupted_tools={})
    native = _stub_native(loop_session=session)
    harness = _make_harness(native)

    assert harness.has_pending_interrupt() is True


def test_pending_interrupt_read_from_active_agent_session_after_loop_cleanup() -> None:
    """ask_user interrupts can outlive native ``loop_session`` cleanup.

    When ``loop_session`` is None, the harness falls back to the active agent
    session bound at ``start`` — so a pending interrupt is still visible and a
    matching resume is still valid.
    """
    pending_state = SimpleNamespace(
        interrupted_tools={
            "ask-user": SimpleNamespace(interrupt_requests={"tool-ask-1": object()}),
        }
    )

    class _AgentSession:
        def get_state(self, key: str) -> Any:
            return pending_state if key == INTERRUPTION_KEY else None

    native = _stub_native(loop_session=None)
    harness = _make_harness(native)
    # Mimic the session bound during start(team_session=...).
    harness._active_agent_session = _AgentSession()

    interactive = InteractiveInput()
    interactive.update("tool-ask-1", {"answers": {"framework": "React"}})

    assert harness.has_pending_interrupt() is True
    assert harness.is_pending_interrupt_resume_valid(interactive) is True


def test_is_pending_interrupt_resume_valid_rejects_non_interactive_input() -> None:
    native = _stub_native()
    harness = _make_harness(native)

    assert harness.is_pending_interrupt_resume_valid("not an interactive input") is False


def test_is_pending_interrupt_resume_valid_returns_false_without_pending_ids() -> None:
    """No interrupted tools means no resume is valid."""
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = SimpleNamespace(interrupted_tools={})
    native = _stub_native(loop_session=session)
    harness = _make_harness(native)

    interactive = InteractiveInput()
    interactive.update("call-1", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is False


def test_is_pending_interrupt_resume_valid_accepts_matching_ids() -> None:
    entry = SimpleNamespace(interrupt_requests={"call-1": object()})
    state = SimpleNamespace(interrupted_tools={"call-1": entry})
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = state
    native = _stub_native(loop_session=session)
    harness = _make_harness(native)

    interactive = InteractiveInput()
    interactive.update("call-1", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is True


def test_is_pending_interrupt_resume_valid_rejects_mismatched_ids() -> None:
    entry = SimpleNamespace(interrupt_requests={"call-1": object()})
    state = SimpleNamespace(interrupted_tools={"call-1": entry})
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = state
    native = _stub_native(loop_session=session)
    harness = _make_harness(native)

    interactive = InteractiveInput()
    interactive.update("call-2", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is False


def test_init_cwd_for_round_no_op_without_workspace() -> None:
    """Workspace is None should short-circuit init_cwd_for_round so the
    harness doesn't reach into a non-existent cwd setup.
    """
    native = _stub_native(workspace=None)
    harness = _make_harness(native)

    harness.init_cwd_for_round()


# ---------------------------------------------------------------------------
# Memory wrapper methods
# ---------------------------------------------------------------------------


def test_register_member_tools_forwards_native() -> None:
    native = _stub_native()
    harness = _make_harness(native)
    memory_manager = MagicMock(name="TeamMemoryManager")

    harness.register_member_tools(memory_manager)

    memory_manager.register_tools.assert_called_once_with(native)


@pytest.mark.asyncio
async def test_inject_member_memory_forwards_native() -> None:
    native = _stub_native()
    harness = _make_harness(native)
    memory_manager = MagicMock(name="TeamMemoryManager")
    memory_manager.load_and_inject = AsyncMock()

    await harness.inject_member_memory(memory_manager, query="hello")

    memory_manager.load_and_inject.assert_awaited_once_with(native, query="hello")
