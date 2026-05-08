# coding: utf-8
"""Tests for ``openjiuwen.agent_teams.agent.stream_controller`` cancel paths.

Cooperative cancel is the seam where the team-side asks a DeepAgent task
loop to wind down: harness.abort first, fall back to ``Task.cancel`` when
the loop ignores the request. These tests cover that two-phase contract
without spinning up a real DeepAgent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent import stream_controller as stream_controller_module
from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.harness import TeamHarness, _MountedRails
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole


def _make_controller(harness: TeamHarness) -> StreamController:
    """Wire a StreamController against a fake harness, no team_member needed."""
    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name="m")

    async def _noop(_: Any) -> None:
        return None

    return StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=harness),
        status_updater=_noop,
        execution_updater=_noop,
    )


def _make_harness_with_abort(abort_calls: list[None]) -> TeamHarness:
    deep_agent = MagicMock(name="DeepAgent")
    deep_agent.deep_config = SimpleNamespace(workspace=None, sys_operation=None, model=None)
    deep_agent.loop_session = None

    async def _abort() -> None:
        abort_calls.append(None)

    deep_agent.abort = _abort
    rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    return TeamHarness(deep_agent, rails, role=TeamRole.LEADER, member_name="m")


@pytest.mark.asyncio
async def test_cooperative_cancel_no_op_when_no_task() -> None:
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)
    sc = _make_controller(harness)

    await sc.cooperative_cancel()

    assert abort_calls == []
    assert sc._cancel_requested is False


@pytest.mark.asyncio
async def test_cooperative_cancel_finishes_when_task_responds_to_abort() -> None:
    """If the task naturally completes within the timeout (simulating a
    cooperative abort handler), no hard cancel is required.
    """
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)
    sc = _make_controller(harness)

    finished = asyncio.Event()

    async def _round() -> None:
        # Simulate the task loop noticing the abort flag and exiting
        # promptly. The timing is well below the cancel timeout.
        await asyncio.sleep(0.01)
        finished.set()

    sc.agent_task = asyncio.create_task(_round())

    await sc.cooperative_cancel()

    assert abort_calls == [None]
    assert finished.is_set(), "cooperative round must run to completion"
    assert sc.agent_task.done()
    assert not sc.agent_task.cancelled(), "cancel must NOT fire when abort succeeds"
    assert sc._cancel_requested is True


@pytest.mark.asyncio
async def test_cooperative_cancel_falls_back_to_hard_cancel_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task loop that ignores the abort signal must still be terminated."""
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)
    sc = _make_controller(harness)

    async def _stuck() -> None:
        await asyncio.sleep(60)

    sc.agent_task = asyncio.create_task(_stuck())

    monkeypatch.setattr(stream_controller_module, "_COOPERATIVE_ABORT_TIMEOUT_SECONDS", 0.05)

    await sc.cooperative_cancel()

    assert abort_calls == [None]
    assert sc.agent_task.done()
    assert sc.agent_task.cancelled(), "stuck task must be hard-cancelled"


@pytest.mark.asyncio
async def test_cancel_agent_records_execution_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_agent must walk CANCEL_REQUESTED -> CANCELLING regardless of
    whether the cooperative path succeeds or falls back.
    """
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)

    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name="m")
    transitions: list[ExecutionStatus] = []

    async def _record_exec(status: ExecutionStatus) -> None:
        transitions.append(status)

    async def _noop_status(_: MemberStatus) -> None:
        return None

    sc = StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=harness),
        status_updater=_noop_status,
        execution_updater=_record_exec,
    )

    async def _round() -> None:
        await asyncio.sleep(0.01)

    sc.agent_task = asyncio.create_task(_round())
    monkeypatch.setattr(stream_controller_module, "_COOPERATIVE_ABORT_TIMEOUT_SECONDS", 0.5)

    await sc.cancel_agent()

    assert ExecutionStatus.CANCEL_REQUESTED in transitions
    assert ExecutionStatus.CANCELLING in transitions
    assert abort_calls == [None]


@pytest.mark.asyncio
async def test_drain_agent_task_clears_pending_inputs_and_cancels() -> None:
    """drain wipes queued user input AND uses the cooperative cancel path."""
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)
    sc = _make_controller(harness)

    sc.pending_inputs = ["queued"]
    sc.pending_interrupt_resumes = [MagicMock()]

    async def _round() -> None:
        await asyncio.sleep(0.01)

    sc.agent_task = asyncio.create_task(_round())

    await sc.drain_agent_task()

    assert sc.pending_inputs == []
    assert sc.pending_interrupt_resumes == []
    assert abort_calls == [None]


@pytest.mark.asyncio
async def test_execute_round_emits_cancelled_on_cooperative_abort_success() -> None:
    """Even when the inner stream finishes without raising, the
    ExecutionStatus must surface CANCELLED whenever the round was
    cancel-requested. Otherwise the state machine reports COMPLETED for
    a user-cancelled round.
    """
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)

    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name="m")
    transitions: list[ExecutionStatus] = []

    async def _record_exec(status: ExecutionStatus) -> None:
        transitions.append(status)

    async def _noop_status(_: MemberStatus) -> None:
        return None

    sc = StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=harness),
        status_updater=_noop_status,
        execution_updater=_record_exec,
    )

    sc._run_retrying_stream = AsyncMock()
    sc._cancel_requested = True

    await sc._execute_round("query")

    assert ExecutionStatus.CANCELLED in transitions
    assert ExecutionStatus.COMPLETED not in transitions


@pytest.mark.asyncio
async def test_execute_round_emits_completed_on_normal_finish() -> None:
    """The non-cancel happy path still walks COMPLETING -> COMPLETED."""
    abort_calls: list[None] = []
    harness = _make_harness_with_abort(abort_calls)

    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name="m")
    transitions: list[ExecutionStatus] = []

    async def _record_exec(status: ExecutionStatus) -> None:
        transitions.append(status)

    async def _noop_status(_: MemberStatus) -> None:
        return None

    sc = StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=harness),
        status_updater=_noop_status,
        execution_updater=_record_exec,
    )

    sc._run_retrying_stream = AsyncMock()
    sc._cancel_requested = False

    await sc._execute_round("query")

    assert ExecutionStatus.COMPLETING in transitions
    assert ExecutionStatus.COMPLETED in transitions
    assert ExecutionStatus.CANCELLED not in transitions
