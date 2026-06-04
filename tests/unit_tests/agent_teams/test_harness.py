# coding: utf-8
"""Unit tests for ``openjiuwen.agent_teams.harness.TeamHarness``.

The harness is the sole adapter between TeamAgent and the underlying
DeepAgent runtime. These tests cover its three contracts:

1. ``build`` mounts rails in a load-bearing order and eagerly initializes
   the team-tool rail before the policy rail.
2. Runtime methods (``steer`` / ``follow_up`` / ``run_streaming`` /
   ``register_rail`` / ``unregister_rail``) forward to the inner agent.
3. State queries (``has_pending_interrupt`` /
   ``is_pending_interrupt_resume_valid``) tolerate missing session and
   missing interruption state without raising.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness, _MountedRails
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY


def _stub_deep_agent(
    *,
    workspace: Any = None,
    sys_operation: Any = None,
    model: Any = None,
    loop_session: Any = None,
) -> MagicMock:
    """Return a MagicMock shaped like a DeepAgent with controllable internals."""
    deep_agent = MagicMock(name="DeepAgent")
    deep_agent.add_rail = MagicMock()
    deep_agent.register_rail = AsyncMock()
    deep_agent.unregister_rail = AsyncMock()
    deep_agent.steer = AsyncMock()
    deep_agent.follow_up = AsyncMock()
    deep_agent.deep_config = SimpleNamespace(
        workspace=workspace,
        sys_operation=sys_operation,
        model=model,
    )
    deep_agent.loop_session = loop_session
    return deep_agent


def _make_harness(deep_agent: MagicMock) -> TeamHarness:
    rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    return TeamHarness(deep_agent, rails, role=TeamRole.LEADER, member_name="leader")


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


def test_build_mounts_rails_in_load_bearing_order() -> None:
    """All rails are added in the documented order so the LLM-visible
    ability snapshot matches the test-visible one."""
    deep_agent = _stub_deep_agent(workspace="WS", sys_operation="SYS")
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = deep_agent

    team_tool = MagicMock(name="TeamToolRail")
    team_policy = MagicMock(name="TeamPolicyRail")
    first_iter = MagicMock(name="FirstIterationGate")
    workspace_rail = MagicMock(name="TeamWorkspaceRail")
    approval = MagicMock(name="TeamToolApprovalRail")

    harness = TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
        first_iter_gate=first_iter,
        team_workspace_rail=workspace_rail,
        tool_approval_rail=approval,
    )

    assert harness.inner_agent is deep_agent
    spec.build.assert_called_once()

    mount_order = [call.args[0] for call in deep_agent.add_rail.call_args_list]
    assert mount_order == [team_tool, team_policy, first_iter, workspace_rail, approval]


def test_build_eagerly_initializes_team_tool_rail() -> None:
    """team_tool_rail.set_sys_operation / set_workspace / init must run
    AFTER add_rail(team_tool) but BEFORE add_rail(team_policy) so tool
    snapshots between configure() and the first invoke see team tools.
    """
    deep_agent = _stub_deep_agent(workspace="WS", sys_operation="SYS")
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = deep_agent

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

    deep_agent.add_rail.side_effect = _track_add_rail

    TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
    )

    assert call_log == [
        "add_rail:team_tool",
        "set_sys_operation",
        "set_workspace",
        "init",
        "add_rail:team_policy",
    ]
    team_tool.set_sys_operation.assert_called_once_with("SYS")
    team_tool.set_workspace.assert_called_once_with("WS")
    team_tool.init.assert_called_once_with(deep_agent)


def test_build_skips_optional_rails_when_none() -> None:
    deep_agent = _stub_deep_agent()
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = deep_agent

    team_tool = MagicMock(name="TeamToolRail")
    team_policy = MagicMock(name="TeamPolicyRail")

    TeamHarness.build(
        agent_spec=spec,
        role=TeamRole.LEADER,
        member_name="leader",
        team_tool_rail=team_tool,
        team_policy_rail=team_policy,
    )

    mounted = [call.args[0] for call in deep_agent.add_rail.call_args_list]
    assert mounted == [team_tool, team_policy]


def test_build_mounts_team_plan_mode_rail_when_provided() -> None:
    deep_agent = _stub_deep_agent()
    spec = MagicMock(name="DeepAgentSpec")
    spec.build.return_value = deep_agent

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

    mounted = [call.args[0] for call in deep_agent.add_rail.call_args_list]
    assert mounted == [team_tool, team_policy, team_plan_mode]
    assert harness.rails.team_plan_mode is team_plan_mode


def test_run_agent_customizer_invokes_with_inner_agent() -> None:
    """Customizer must receive (deep_agent, member_name, role.value)."""
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)
    captured: list[tuple[Any, ...]] = []

    def _customizer(agent: Any, member_name: str, role: str) -> None:
        captured.append((agent, member_name, role))

    harness.run_agent_customizer(_customizer)

    assert captured == [(deep_agent, "leader", "leader")]


def test_run_agent_customizer_swallows_exceptions() -> None:
    """A broken customizer must not abort team setup."""
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)

    def _broken(*_: Any) -> None:
        raise RuntimeError("boom")

    harness.run_agent_customizer(_broken)


# ---------------------------------------------------------------------------
# Runtime call surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_forwards_to_inner_agent() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)

    await harness.steer("hello")

    deep_agent.steer.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_follow_up_forwards_to_inner_agent() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)

    await harness.follow_up("update")

    deep_agent.follow_up.assert_awaited_once_with("update")


@pytest.mark.asyncio
async def test_abort_forwards_to_inner_agent() -> None:
    """``abort`` is the cooperative shutdown seam — must reach the agent."""
    deep_agent = _stub_deep_agent()
    deep_agent.abort = AsyncMock()
    harness = _make_harness(deep_agent)

    await harness.abort()

    deep_agent.abort.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_register_and_unregister_rail_forward() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)
    rail = MagicMock(name="DynamicRail")

    await harness.register_rail(rail)
    await harness.unregister_rail(rail)

    deep_agent.register_rail.assert_awaited_once_with(rail)
    deep_agent.unregister_rail.assert_awaited_once_with(rail)


def test_find_rails_delegates_to_deep_agent() -> None:
    """find_rails forwards the type query to the underlying agent."""
    from openjiuwen.harness.rails import TeamSkillRail

    deep_agent = _stub_deep_agent()
    skill_rail = MagicMock(name="TeamSkillRail")
    deep_agent.find_rails_by_type = MagicMock(return_value=[skill_rail])
    harness = _make_harness(deep_agent)

    found = harness.find_rails(TeamSkillRail)

    deep_agent.find_rails_by_type.assert_called_once_with((TeamSkillRail,))
    assert found == [skill_rail]


def test_find_rails_returns_empty_when_no_match() -> None:
    """find_rails returns an empty list when no rail of the type is mounted."""
    from openjiuwen.harness.rails import TeamSkillRail

    deep_agent = _stub_deep_agent()
    deep_agent.find_rails_by_type = MagicMock(return_value=[])
    harness = _make_harness(deep_agent)

    assert harness.find_rails(TeamSkillRail) == []


@pytest.mark.asyncio
async def test_run_streaming_delegates_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_streaming`` is the seam where a remote runtime would replace
    ``Runner.run_agent_streaming``. Tests confirm the call site is here.
    """
    from openjiuwen.agent_teams import harness as harness_module

    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)
    captured: list[tuple[Any, dict, Any]] = []

    async def _fake(agent: Any, inputs: dict, *, session: Any = None) -> Any:
        captured.append((agent, inputs, session))
        if False:  # pragma: no cover — generator marker
            yield None

    monkeypatch.setattr(harness_module.Runner, "run_agent_streaming", _fake)

    iterator = harness.run_streaming({"query": "q"}, session_id="sess-1")
    async for _ in iterator:
        pass

    assert captured == [(deep_agent, {"query": "q"}, "sess-1")]


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


def test_has_pending_interrupt_returns_false_without_session() -> None:
    deep_agent = _stub_deep_agent(loop_session=None)
    harness = _make_harness(deep_agent)

    assert harness.has_pending_interrupt() is False


def test_has_pending_interrupt_returns_false_without_state() -> None:
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = None
    deep_agent = _stub_deep_agent(loop_session=session)
    harness = _make_harness(deep_agent)

    assert harness.has_pending_interrupt() is False


def test_has_pending_interrupt_returns_true_when_state_present() -> None:
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = SimpleNamespace(interrupted_tools={})
    deep_agent = _stub_deep_agent(loop_session=session)
    harness = _make_harness(deep_agent)

    assert harness.has_pending_interrupt() is True


@pytest.mark.asyncio
async def test_pending_interrupt_survives_loop_session_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """ask_user interrupts can outlive DeepAgent.loop_session cleanup."""
    from openjiuwen.agent_teams import harness as harness_module

    class FakeAgentSession:
        def __init__(self, state: Any) -> None:
            self.state = state

        async def pre_run(self, inputs: dict[str, Any]) -> None:
            self.inputs = inputs

        def get_state(self, key: str) -> Any:
            return self.state if key == INTERRUPTION_KEY else None

    async def _fake_runner(*_: Any, **__: Any) -> Any:
        if False:  # pragma: no cover - async generator marker
            yield None

    pending_state = SimpleNamespace(
        interrupted_tools={
            "ask-user": SimpleNamespace(interrupt_requests={"tool-ask-1": object()}),
        }
    )
    agent_session = FakeAgentSession(pending_state)
    team_session = SimpleNamespace(
        create_agent_session=MagicMock(return_value=agent_session),
    )
    deep_agent = _stub_deep_agent(loop_session=None)
    harness = _make_harness(deep_agent)
    monkeypatch.setattr(harness_module.Runner, "run_agent_streaming", _fake_runner)

    async for _ in harness.run_streaming(
        {"query": "q"},
        session_id="team-session",
        team_session=team_session,
    ):
        pass

    interactive = InteractiveInput()
    interactive.update("tool-ask-1", {"answers": {"framework": "React"}})

    assert harness.has_pending_interrupt() is True
    assert harness.is_pending_interrupt_resume_valid(interactive) is True


def test_is_pending_interrupt_resume_valid_rejects_non_interactive_input() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)

    assert harness.is_pending_interrupt_resume_valid("not an interactive input") is False


def test_is_pending_interrupt_resume_valid_returns_false_without_pending_ids() -> None:
    """No interrupted tools means no resume is valid."""
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = SimpleNamespace(interrupted_tools={})
    deep_agent = _stub_deep_agent(loop_session=session)
    harness = _make_harness(deep_agent)

    interactive = InteractiveInput()
    interactive.update("call-1", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is False


def test_is_pending_interrupt_resume_valid_accepts_matching_ids() -> None:
    entry = SimpleNamespace(interrupt_requests={"call-1": object()})
    state = SimpleNamespace(interrupted_tools={"call-1": entry})
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = state
    deep_agent = _stub_deep_agent(loop_session=session)
    harness = _make_harness(deep_agent)

    interactive = InteractiveInput()
    interactive.update("call-1", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is True


def test_is_pending_interrupt_resume_valid_rejects_mismatched_ids() -> None:
    entry = SimpleNamespace(interrupt_requests={"call-1": object()})
    state = SimpleNamespace(interrupted_tools={"call-1": entry})
    session = MagicMock(name="LoopSession")
    session.get_state.return_value = state
    deep_agent = _stub_deep_agent(loop_session=session)
    harness = _make_harness(deep_agent)

    interactive = InteractiveInput()
    interactive.update("call-2", {"approved": True})

    assert harness.is_pending_interrupt_resume_valid(interactive) is False


def test_init_cwd_for_round_no_op_without_workspace() -> None:
    """Workspace is None should short-circuit init_cwd_for_round so the
    harness doesn't reach into a non-existent cwd setup.
    """
    deep_agent = _stub_deep_agent(workspace=None)
    harness = _make_harness(deep_agent)

    harness.init_cwd_for_round()


# ---------------------------------------------------------------------------
# Memory wrapper methods
# ---------------------------------------------------------------------------


def test_register_member_tools_forwards_inner_agent() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)
    memory_manager = MagicMock(name="TeamMemoryManager")

    harness.register_member_tools(memory_manager)

    memory_manager.register_tools.assert_called_once_with(deep_agent)


@pytest.mark.asyncio
async def test_inject_member_memory_forwards_inner_agent() -> None:
    deep_agent = _stub_deep_agent()
    harness = _make_harness(deep_agent)
    memory_manager = MagicMock(name="TeamMemoryManager")
    memory_manager.load_and_inject = AsyncMock()

    await harness.inject_member_memory(memory_manager, query="hello")

    memory_manager.load_and_inject.assert_awaited_once_with(deep_agent, query="hello")
