# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Goal mode for team members and the leader."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.goal_coordinator import TeamGoalCoordinator
from openjiuwen.agent_teams.agent.member_activity import MemberActivityRegistry
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.goal import (
    bind_request_goal,
    bind_task_goal,
    bound_task_id,
    release_task_goal,
    resolve_goal_spec,
    to_stop_config,
)
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.rails.team_goal_rail import TeamGoalRail
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import (
    ExternalCliAgentSpec,
    TeamCompletionSnapshot,
    TeamGoalSpec,
    TeamRole,
    TeamRuntimeContext,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.member_options import get_member_goal
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import SpawnTeammateTool
from openjiuwen.core.runner import Runner
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalStatus,
    GoalStopConfig,
    GoalStopStrategy,
)
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)
from tests.unit_tests.agent_teams.test_team_agent_coordination import (
    _make_leader,
    _wire_completion_handler,
)

_MEMBER = "dev-1"


@dataclass
class _Task:
    """The fields of a task row that goal mode reads."""

    task_id: str
    title: str
    content: str
    status: str
    assignee: str | None


class _TaskBoard:
    """In-memory stand-in for the member's task manager."""

    def __init__(self, *tasks: _Task) -> None:
        self.member_name = _MEMBER
        self.tasks = {task.task_id: task for task in tasks}

    async def get(self, task_id: str) -> _Task | None:
        """Return one task by id."""
        return self.tasks.get(task_id)

    async def get_tasks_by_assignee(self, member_name: str, status: str | None = None) -> list[_Task]:
        """Return the member's tasks, optionally filtered by status."""
        owned = [task for task in self.tasks.values() if task.assignee == member_name]
        if status is None:
            return owned
        return [task for task in owned if task.status == status]


def _in_progress(task_id: str = "t1", assignee: str | None = _MEMBER) -> _Task:
    return _Task(task_id, "Build the parser", "Parse every token kind.", TaskStatus.IN_PROGRESS.value, assignee)


def _tool_ctx(tool_name: str, args: dict[str, Any], result: Any = None) -> Any:
    """Build the callback context a tool hook receives."""
    inputs = SimpleNamespace(
        tool_name=tool_name,
        tool_args=args,
        tool_result=result,
        tool_msg=None,
        tool_call=SimpleNamespace(id="call-1"),
    )
    return SimpleNamespace(inputs=inputs, extra={}, context=None, session=None)


class _GoalHarness:
    """A started NativeHarness with goal mode on, plus its output drain."""

    def __init__(self, strategy: GoalStopStrategy = GoalStopStrategy.AGENT_REPORT) -> None:
        self.harness = NativeHarness(make_spec())
        self.harness.enable_goal(GoalStopConfig(strategy=strategy), ready=self._never_ready)
        self._collected: list = []
        self._consumer: asyncio.Task | None = None

    @staticmethod
    async def _never_ready() -> bool:
        """Hold goal attempts back so a test only sees the goal record move."""
        return False

    async def __aenter__(self) -> NativeHarness:
        await Runner.start()
        await start_harness(self.harness)
        self._consumer = asyncio.create_task(drain_outputs(self.harness, self._collected))
        return self.harness

    async def __aexit__(self, *exc: Any) -> None:
        await self.harness.stop()
        await self._consumer
        await Runner.stop()


def _member_ctx(**overrides: Any) -> TeamRuntimeContext:
    return TeamRuntimeContext(role=TeamRole.TEAMMATE, member_name=_MEMBER, **overrides)


def _spec(**overrides: Any) -> TeamAgentSpec:
    return TeamAgentSpec(agents={"leader": DeepAgentSpec()}, **overrides)


# ── configuration ─────────────────────────────────────────────────────────


@pytest.mark.level0
def test_goal_mode_is_off_by_default() -> None:
    """Without goal settings neither the leader nor a teammate has a goal."""
    spec = _spec()
    leader_ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader")
    assert resolve_goal_spec(spec, leader_ctx) is None
    assert resolve_goal_spec(spec, _member_ctx()) is None


@pytest.mark.level0
def test_member_goal_applies_to_teammates_unless_they_opt_out() -> None:
    """member_goal reaches a teammate by default and is dropped by its opt-out."""
    goal = TeamGoalSpec(max_attempts=3)
    spec = _spec(member_goal=goal)
    assert resolve_goal_spec(spec, _member_ctx()) == goal
    assert resolve_goal_spec(spec, _member_ctx(goal_enabled=False)) is None
    assert resolve_goal_spec(spec, TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader")) is None
    assert resolve_goal_spec(spec, _member_ctx(cli_agent="claude")) is None
    human_ctx = TeamRuntimeContext(role=TeamRole.HUMAN_AGENT, member_name="human")
    assert resolve_goal_spec(spec, human_ctx) is None


@pytest.mark.level0
def test_leader_goal_applies_to_the_leader_only() -> None:
    """leader.goal reaches the leader and no one else."""
    goal = TeamGoalSpec(strategy="agent_report")
    spec = _spec(leader=LeaderSpec(goal=goal))
    assert resolve_goal_spec(spec, TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader")) == goal
    assert resolve_goal_spec(spec, _member_ctx()) is None
    stop = to_stop_config(goal)
    assert stop.strategy is GoalStopStrategy.AGENT_REPORT
    assert stop.max_attempts == 10


@pytest.mark.level1
def test_leader_goal_is_rejected_on_a_third_party_leader_harness() -> None:
    """A leader on a third-party harness cannot be given a goal."""
    with pytest.raises(ValueError, match="leader.goal"):
        _spec(
            leader=LeaderSpec(cli_agent="claude", goal=TeamGoalSpec()),
            external_cli_agents=[ExternalCliAgentSpec(cli_agent="claude")],
        )


# ── a member's goal follows its task ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_started_task_becomes_the_members_goal() -> None:
    """Binding an owned in-progress task opens a goal that states the task."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        assert await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        record = await harness.goal_manager.get()
        assert record.status is GoalStatus.ACTIVE
        assert "Build the parser" in record.objective
        assert "Parse every token kind." in record.objective
        assert bound_task_id(harness.session) == "t1"

        # Binding the same open task again keeps the goal it already has.
        assert await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        assert (await harness.goal_manager.get()).goal_id == record.goal_id


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize(
    "task",
    [
        _in_progress(assignee="someone-else"),
        _Task("t1", "x", "y", TaskStatus.PENDING.value, _MEMBER),
        None,
    ],
)
async def test_task_not_held_in_progress_opens_no_goal(task: _Task | None) -> None:
    """A task the member does not hold in progress never becomes its goal."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(*([task] if task is not None else []))
        assert not await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        assert await harness.goal_manager.get() is None
        assert bound_task_id(harness.session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_releasing_the_task_drops_its_goal_only() -> None:
    """Release drops the goal of the named task and leaves any other alone."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        assert not await release_task_goal(harness.goal_manager, harness.session, "other")
        assert await harness.goal_manager.get() is not None
        assert await release_task_goal(harness.goal_manager, harness.session, "t1")
        assert await harness.goal_manager.get() is None
        assert bound_task_id(harness.session) is None


# ── claim opens the goal, hand-in is assessed ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claiming_a_task_opens_its_goal() -> None:
    """A successful claim makes the claimed task the member's goal."""
    async with _GoalHarness() as harness:
        rail = TeamGoalRail(task_manager=_TaskBoard(_in_progress()))
        rail._harness = harness
        result = SimpleNamespace(success=True)
        await rail.after_tool_call(_tool_ctx("claim_task", {"task_id": "t1", "status": "claimed"}, result))
        assert bound_task_id(harness.session) == "t1"
        assert (await harness.goal_manager.get()).status is GoalStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_claim_opens_no_goal() -> None:
    """A claim the task board refused leaves the member without a goal."""
    async with _GoalHarness() as harness:
        rail = TeamGoalRail(task_manager=_TaskBoard(_in_progress()))
        rail._harness = harness
        result = SimpleNamespace(success=False)
        await rail.after_tool_call(_tool_ctx("claim_task", {"task_id": "t1", "status": "claimed"}, result))
        assert await harness.goal_manager.get() is None


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("claim_task", {"task_id": "t1", "status": "completed"}),
        ("member_complete_task", {"task_id": "t1"}),
    ],
)
async def test_accepted_hand_in_closes_the_goal(tool_name: str, args: dict[str, Any]) -> None:
    """A hand-in the assessment accepts runs, and its success closes the goal."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        rail = TeamGoalRail(task_manager=board)
        rail._harness = harness
        await bind_task_goal(harness.goal_manager, harness.session, board, "t1")

        before = _tool_ctx(tool_name, args)
        await rail.before_tool_call(before)
        assert "_skip_tool_calls" not in before.extra

        await rail.after_tool_call(_tool_ctx(tool_name, args, SimpleNamespace(success=True)))
        assert (await harness.goal_manager.get()).status is GoalStatus.COMPLETED
        assert bound_task_id(harness.session) is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rejected_hand_in_never_reaches_the_task_board() -> None:
    """A hand-in the assessment rejects is skipped and names the missing work."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        rail = TeamGoalRail(task_manager=board)
        rail._harness = harness
        await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        verdict = GoalAssessment(
            status=GoalAssessmentStatus.CONTINUE,
            evidence="tests were never run",
            remaining_work="run the parser tests",
        )
        harness._task_completion_rail.assess_completion_claim = AsyncMock(return_value=verdict)

        ctx = _tool_ctx("member_complete_task", {"task_id": "t1"})
        await rail.before_tool_call(ctx)

        assert ctx.extra["_skip_tool_calls"] == {"call-1": True}
        assert "run the parser tests" in ctx.inputs.tool_msg.content
        record = await harness.goal_manager.get()
        assert record.status is GoalStatus.ACTIVE
        assert record.last_assessment.remaining_work == "run the parser tests"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_hand_in_passes_when_no_verdict_is_available() -> None:
    """An assessor that cannot produce a verdict does not block the hand-in."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        rail = TeamGoalRail(task_manager=board)
        rail._harness = harness
        await bind_task_goal(harness.goal_manager, harness.session, board, "t1")
        harness._task_completion_rail.assess_completion_claim = AsyncMock(return_value=None)

        ctx = _tool_ctx("member_complete_task", {"task_id": "t1"})
        await rail.before_tool_call(ctx)
        assert "_skip_tool_calls" not in ctx.extra


@pytest.mark.asyncio
@pytest.mark.level1
async def test_hand_in_of_a_task_started_without_a_claim_is_still_assessed() -> None:
    """A task started for the member and finished in one round is assessed at hand-in."""
    async with _GoalHarness() as harness:
        rail = TeamGoalRail(task_manager=_TaskBoard(_in_progress()))
        rail._harness = harness
        verdict = GoalAssessment(status=GoalAssessmentStatus.CONTINUE, evidence="nothing done", remaining_work="do it")
        harness._task_completion_rail.assess_completion_claim = AsyncMock(return_value=verdict)

        ctx = _tool_ctx("member_complete_task", {"task_id": "t1"})
        await rail.before_tool_call(ctx)

        assert ctx.extra["_skip_tool_calls"] == {"call-1": True}
        assert bound_task_id(harness.session) == "t1"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_hand_in_of_a_task_the_member_does_not_hold_is_not_assessed() -> None:
    """Completing a task that is not the member's in-progress task is left untouched."""
    async with _GoalHarness() as harness:
        rail = TeamGoalRail(task_manager=_TaskBoard(_in_progress(assignee="someone-else")))
        rail._harness = harness
        harness._task_completion_rail.assess_completion_claim = AsyncMock()

        ctx = _tool_ctx("member_complete_task", {"task_id": "t1"})
        await rail.before_tool_call(ctx)

        assert "_skip_tool_calls" not in ctx.extra
        harness._task_completion_rail.assess_completion_claim.assert_not_awaited()


# ── keeping the goal in step with the board ───────────────────────────────


def _coordinator(harness: NativeHarness, board: _TaskBoard, **overrides: Any) -> tuple[TeamGoalCoordinator, Any]:
    """Build a teammate coordinator over a fake team harness wrapping ``harness``."""
    team_harness = MagicMock()
    team_harness.goal_manager = harness.goal_manager
    team_harness.goal_session = harness.session
    team_harness.send = AsyncMock()
    params: dict[str, Any] = {
        "role": TeamRole.TEAMMATE,
        "member_name": _MEMBER,
        "harness": team_harness,
        "task_manager_getter": lambda: board,
        "message_manager_getter": lambda: None,
        "registry_getter": lambda: None,
        "leader_name_getter": lambda: "leader",
        "emit_blocked": MagicMock(),
    }
    params.update(overrides)
    return TeamGoalCoordinator(**params), team_harness


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_started_for_the_member_becomes_its_goal_at_round_end() -> None:
    """A task put in progress for the member (scheduler start, plan approval) gets a goal."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        coordinator, _ = _coordinator(harness, board)
        assert await coordinator._sync_task_goal()
        assert bound_task_id(harness.session) == "t1"
        assert (await harness.goal_manager.get()).status is GoalStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("new_status", [TaskStatus.CANCELLED.value, TaskStatus.PENDING.value])
async def test_goal_is_dropped_when_the_task_leaves_the_member(new_status: str) -> None:
    """A task cancelled or taken away no longer holds a goal for the member."""
    async with _GoalHarness() as harness:
        task = _in_progress()
        board = _TaskBoard(task)
        coordinator, _ = _coordinator(harness, board)
        await coordinator._sync_task_goal()

        task.status = new_status
        task.assignee = None if new_status == TaskStatus.PENDING.value else _MEMBER
        await coordinator._sync_task_goal()

        assert await harness.goal_manager.get() is None
        assert bound_task_id(harness.session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_sent_back_after_hand_in_gets_a_fresh_goal() -> None:
    """A task a reviewer returns to in-progress starts over as a new goal."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        rail = TeamGoalRail(task_manager=board)
        rail._harness = harness
        coordinator, _ = _coordinator(harness, board)
        await coordinator._sync_task_goal()
        first = await harness.goal_manager.get()

        args = {"task_id": "t1"}
        await rail.before_tool_call(_tool_ctx("member_complete_task", args))
        await rail.after_tool_call(_tool_ctx("member_complete_task", args, SimpleNamespace(success=True)))
        await coordinator._sync_task_goal()

        second = await harness.goal_manager.get()
        assert second.status is GoalStatus.ACTIVE
        assert second.goal_id != first.goal_id


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_is_told_once_to_hand_in_work_assessed_as_done() -> None:
    """A goal assessed as met with the task still open prompts one hand-in reminder."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        coordinator, team_harness = _coordinator(harness, board)
        await coordinator._sync_task_goal()
        record = await harness.goal_manager.get()
        await harness.goal_manager.apply_assessment(
            goal_id=record.goal_id,
            revision=record.revision,
            assessment=GoalAssessment(status=GoalAssessmentStatus.COMPLETE, evidence="looks done"),
        )

        await coordinator._sync_task_goal()
        await coordinator._sync_task_goal()
        await asyncio.sleep(0)

        team_harness.send.assert_awaited_once()
        assert "t1" in team_harness.send.await_args.args[0]
        assert (await harness.goal_manager.get()).status is GoalStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.level1
async def test_blocked_member_goal_is_reported_to_the_leader() -> None:
    """A member goal that blocks sends the leader a message naming the task."""
    async with _GoalHarness() as harness:
        board = _TaskBoard(_in_progress())
        messages = MagicMock()
        messages.send_message = AsyncMock()
        coordinator, _ = _coordinator(harness, board, message_manager_getter=lambda: messages)
        await coordinator._sync_task_goal()

        await coordinator._on_goal_changed(
            {"status": "blocked", "last_assessment": {"evidence": "no database access"}},
        )

        messages.send_message.assert_awaited_once()
        content = messages.send_message.await_args.args[0]
        assert "t1" in content and "no database access" in content
        assert messages.send_message.await_args.kwargs["to_member_name"] == "leader"


# ── the leader's goal ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_user_request_becomes_the_leaders_goal_and_is_not_replaced() -> None:
    """The first request opens the goal; a later one leaves that goal in place."""
    async with _GoalHarness() as harness:
        assert await bind_request_goal(harness.goal_manager, "build a compiler")
        first = await harness.goal_manager.get()
        assert "build a compiler" in first.objective

        assert await bind_request_goal(harness.goal_manager, "also add docs")
        assert (await harness.goal_manager.get()).goal_id == first.goal_id


@pytest.mark.asyncio
@pytest.mark.level1
async def test_empty_request_opens_no_leader_goal() -> None:
    """An empty or non-text input never opens a goal."""
    async with _GoalHarness() as harness:
        assert not await bind_request_goal(harness.goal_manager, "   ")
        assert not await bind_request_goal(harness.goal_manager, {"answer": 1})
        assert await harness.goal_manager.get() is None


@pytest.mark.level0
def test_leader_goal_attempt_waits_for_every_other_member() -> None:
    """The leader may act on its goal only once no other member is moving."""
    registry = MemberActivityRegistry("leader")
    registry.record("leader", MemberStatus.BUSY)
    registry.record(_MEMBER, MemberStatus.BUSY)
    assert not registry.others_idle()
    registry.record(_MEMBER, MemberStatus.READY)
    assert registry.others_idle()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_open_leader_goal_holds_back_team_completion() -> None:
    """A complete board does not conclude the team while the leader's goal is open."""
    agent = _make_leader()
    messager = _wire_completion_handler(agent, TeamCompletionSnapshot(member_count=2, task_count=3))
    agent._goal = MagicMock()
    agent._goal.holds_completion.return_value = True
    event = InnerEventMessage(event_type=InnerEventType.POLL_TASK, payload={})

    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    messager.publish.assert_not_awaited()

    agent._goal.holds_completion.return_value = False
    await agent._coordination.dispatcher.team_completion.on_poll_task(event)
    messager.publish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_coordinator_holds_completion_only_for_an_active_goal() -> None:
    """Only an active goal holds completion, and holding it nudges the leader."""
    async with _GoalHarness() as harness:
        coordinator, team_harness = _coordinator(harness, _TaskBoard(), role=TeamRole.LEADER, member_name="leader")
        assert not coordinator.holds_completion()

        await coordinator.open_user_goal("build a compiler")
        assert coordinator.holds_completion()
        team_harness.nudge_goal.assert_called_once()

        record = await harness.goal_manager.get()
        await harness.goal_manager.apply_assessment(
            goal_id=record.goal_id,
            revision=record.revision,
            assessment=GoalAssessment(status=GoalAssessmentStatus.COMPLETE, evidence="all met"),
        )
        assert not coordinator.holds_completion()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_blocked_leader_goal_surfaces_a_marker() -> None:
    """A leader goal that blocks is surfaced through the blocked callback."""
    async with _GoalHarness() as harness:
        emit_blocked = MagicMock()
        coordinator, _ = _coordinator(
            harness,
            _TaskBoard(),
            role=TeamRole.LEADER,
            member_name="leader",
            emit_blocked=emit_blocked,
        )
        await coordinator._on_goal_changed({"status": "blocked", "last_stop_reason": "blocked"})
        emit_blocked.assert_called_once_with("blocked")


# ── input reopens a blocked goal ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.level1
async def test_outside_input_reopens_a_blocked_goal_for_one_more_attempt() -> None:
    """A goal blocked at its limit gets one further attempt after new input."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        harness.enable_goal(GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT, max_attempts=1))
        fake = await start_harness(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.goal_manager.set("ship the parser")
            for _ in range(300):
                record = harness.goal_manager.peek()
                if record.status is GoalStatus.BLOCKED and harness.state is HarnessState.IDLE:
                    break
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 1

            await harness.send("the database is back")
            for _ in range(300):
                if len(fake.invocations) == 3 and harness.state is HarnessState.IDLE:
                    break
                await asyncio.sleep(0.01)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [str(invocation["query"]) for invocation in fake.invocations]
        assert queries[1] == "the database is back"
        assert "ship the parser" in queries[2]
        assert len(queries) == 3
    finally:
        await Runner.stop()


# ── the per-member switch on spawn_teammate ───────────────────────────────


@pytest_asyncio.fixture
async def backend_factory():
    """Yield a factory building a leader backend with member goal mode on or off."""
    token = set_session_id("team-goal-test-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    await database.team.create_team(team_name="test-team", display_name="Test Team", leader_member_name="leader-1")

    def _build(enable_member_goal: bool) -> TeamBackend:
        return TeamBackend(
            team_name="test-team",
            member_name="leader-1",
            is_leader=True,
            db=database,
            messager=AsyncMock(spec=Messager),
            enable_member_goal=enable_member_goal,
        )

    try:
        yield _build
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_teammate_records_the_members_goal_switch(backend_factory) -> None:
    """With member goal mode on, spawn_teammate exposes ``goal`` and stores it."""
    backend = backend_factory(True)
    tool = SpawnTeammateTool(backend, make_translator("cn"))
    assert "goal" in tool.card.input_params["properties"]

    result = await tool.invoke({"member_name": "dev-2", "display_name": "Dev", "desc": "dev", "goal": False})
    assert result.success, result.error
    member = await backend.db.member.get_member("dev-2", "test-team")
    assert get_member_goal(member) is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_teammate_hides_and_refuses_goal_when_member_goal_is_off(backend_factory) -> None:
    """With member goal mode off, ``goal`` is absent from the schema and refused."""
    backend = backend_factory(False)
    tool = SpawnTeammateTool(backend, make_translator("en"))
    assert "goal" not in tool.card.input_params["properties"]

    result = await tool.invoke({"member_name": "dev-3", "display_name": "Dev", "desc": "dev", "goal": True})
    assert not result.success
    assert "goal" in result.error
    assert await backend.db.member.get_member("dev-3", "test-team") is None


# ── assembly ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.level1
async def test_goal_rail_mounts_on_the_member_harness_and_sees_its_goal() -> None:
    """Mounted through the harness, the rail hooks tool calls and reaches the goal."""
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

    await Runner.start()
    try:
        board = _TaskBoard(_in_progress())
        rail = TeamGoalRail(task_manager=board)
        harness = NativeHarness(make_spec(), extra_rails=[rail])
        harness.enable_goal(GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT), ready=_GoalHarness._never_ready)
        await start_harness(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert rail._harness is harness
            callbacks = rail.get_callbacks()
            assert AgentCallbackEvent.BEFORE_TOOL_CALL in callbacks
            assert AgentCallbackEvent.AFTER_TOOL_CALL in callbacks

            claim = _tool_ctx("claim_task", {"task_id": "t1", "status": "claimed"}, SimpleNamespace(success=True))
            await callbacks[AgentCallbackEvent.AFTER_TOOL_CALL](claim)
            assert bound_task_id(harness.session) == "t1"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
