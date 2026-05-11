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
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.stream.base import OutputSchema


def _make_controller(harness: TeamHarness) -> StreamController:
    """Wire a StreamController against a fake harness, no team_member needed."""
    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name="m", role=TeamRole.LEADER)

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
    blueprint = SimpleNamespace(member_name="m", role=TeamRole.LEADER)
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
    blueprint = SimpleNamespace(member_name="m", role=TeamRole.LEADER)
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
    blueprint = SimpleNamespace(member_name="m", role=TeamRole.LEADER)
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


# ----------------------------------------------------------------------
# Chunk observer + source_member tagging
# ----------------------------------------------------------------------


def _harness_yielding(chunks: list[Any]) -> TeamHarness:
    """Build a minimal TeamHarness whose run_streaming yields *chunks*."""
    deep_agent = MagicMock(name="DeepAgent")
    deep_agent.deep_config = SimpleNamespace(workspace=None, sys_operation=None, model=None)
    deep_agent.loop_session = None

    async def _abort() -> None:
        return None

    deep_agent.abort = _abort
    rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    harness = TeamHarness(deep_agent, rails, role=TeamRole.LEADER, member_name="m")

    async def _run_streaming(_inputs: Any, *, session_id: Any = None) -> Any:
        for ch in chunks:
            yield ch

    harness.run_streaming = _run_streaming  # type: ignore[assignment]
    return harness


def test_tag_chunk_upgrades_plain_outputschema() -> None:
    """Plain OutputSchema gets upgraded to TeamOutputSchema with source_member + role."""
    harness = _make_harness_with_abort([])
    sc = _make_controller(harness)

    raw = OutputSchema(type="message", index=0, payload={"text": "hi"})
    tagged = sc._tag_chunk(raw)

    assert isinstance(tagged, TeamOutputSchema)
    assert tagged.source_member == "m"
    assert tagged.role == TeamRole.LEADER
    assert tagged.type == "message"
    assert tagged.payload == {"text": "hi"}
    # Original chunk must not be mutated — DeepAgent internals may keep a ref.
    assert not isinstance(raw, TeamOutputSchema)


def test_tag_chunk_passes_through_team_output_with_matching_identity() -> None:
    """An already-tagged chunk with matching member + role is returned unchanged."""
    harness = _make_harness_with_abort([])
    sc = _make_controller(harness)

    pre_tagged = TeamOutputSchema(type="message", index=0, payload={}, source_member="m", role=TeamRole.LEADER)
    out = sc._tag_chunk(pre_tagged)

    assert out is pre_tagged


def test_tag_chunk_rewrites_team_output_with_mismatched_member() -> None:
    """An already-tagged chunk with a different member is re-tagged."""
    harness = _make_harness_with_abort([])
    sc = _make_controller(harness)

    pre_tagged = TeamOutputSchema(type="message", index=0, payload={}, source_member="other", role=TeamRole.TEAMMATE)
    out = sc._tag_chunk(pre_tagged)

    assert isinstance(out, TeamOutputSchema)
    assert out.source_member == "m"
    assert out.role == TeamRole.LEADER
    assert pre_tagged.source_member == "other"  # original untouched
    assert pre_tagged.role == TeamRole.TEAMMATE


def test_tag_chunk_passes_through_non_outputschema() -> None:
    """Non-OutputSchema chunks (custom payloads) survive tagging untouched."""
    harness = _make_harness_with_abort([])
    sc = _make_controller(harness)

    custom = SimpleNamespace(type="custom", payload={"x": 1})
    out = sc._tag_chunk(custom)

    assert out is custom


@pytest.mark.asyncio
async def test_stream_one_round_tags_and_fans_out_to_observers() -> None:
    """_stream_one_round must tag every chunk and broadcast to observers."""
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]
    harness = _harness_yielding(raw_chunks)
    sc = _make_controller(harness)
    sc.stream_queue = asyncio.Queue()

    received: list[Any] = []

    async def _observer(chunk: Any) -> None:
        received.append(chunk)

    sc.add_chunk_observer(_observer)

    await sc._stream_one_round("hi")

    # Local queue saw tagged chunks.
    queued: list[Any] = []
    while not sc.stream_queue.empty():
        queued.append(sc.stream_queue.get_nowait())
    assert len(queued) == 2
    assert all(isinstance(c, TeamOutputSchema) and c.source_member == "m" and c.role == TeamRole.LEADER for c in queued)
    # Observer saw the same tagged objects (no copies between queue and fan-out).
    assert received == queued


@pytest.mark.asyncio
async def test_observer_exception_auto_detaches_and_does_not_block_stream() -> None:
    """A misbehaving observer is detached and never blocks the producer."""
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]
    harness = _harness_yielding(raw_chunks)
    sc = _make_controller(harness)
    sc.stream_queue = asyncio.Queue()

    bad_calls: list[int] = []
    good_calls: list[Any] = []

    async def _bad(chunk: Any) -> None:
        bad_calls.append(chunk.index)
        raise RuntimeError("boom")

    async def _good(chunk: Any) -> None:
        good_calls.append(chunk)

    sc.add_chunk_observer(_bad)
    sc.add_chunk_observer(_good)

    await sc._stream_one_round("hi")

    # Bad observer ran exactly once before being detached.
    assert bad_calls == [0]
    # Good observer kept receiving all chunks.
    assert len(good_calls) == 2
    # Both chunks made it into the local queue regardless.
    assert sc.stream_queue.qsize() == 2


def test_remove_chunk_observer_is_idempotent() -> None:
    """Removing an observer that was never registered must not raise."""
    harness = _make_harness_with_abort([])
    sc = _make_controller(harness)

    async def _observer(_: Any) -> None:
        return None

    sc.remove_chunk_observer(_observer)  # not registered → silent
    sc.add_chunk_observer(_observer)
    sc.remove_chunk_observer(_observer)
    sc.remove_chunk_observer(_observer)  # second remove → silent
    assert sc._chunk_observers == []


def _make_controller_with(
    *, member_name: str, harness: TeamHarness, role: TeamRole = TeamRole.TEAMMATE
) -> StreamController:
    """Variant of _make_controller that lets the test fix member identity."""
    state = TeamAgentState(session_id="sess")
    blueprint = SimpleNamespace(member_name=member_name, role=role)

    async def _noop(_: Any) -> None:
        return None

    return StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=harness),
        status_updater=_noop,
        execution_updater=_noop,
    )


@pytest.mark.asyncio
async def test_teammate_chunks_reach_leader_queue_via_forward_observer() -> None:
    """End-to-end: teammate chunks flow into leader's stream_queue tagged
    with the teammate's member name — same data path SpawnManager wires
    when an in-process teammate is spawned.
    """
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]

    leader_sc = _make_controller_with(
        member_name="leader_m", harness=_make_harness_with_abort([]), role=TeamRole.LEADER
    )
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_controller_with(
        member_name="teammate_m", harness=_harness_yielding(raw_chunks), role=TeamRole.TEAMMATE
    )
    teammate_sc.stream_queue = asyncio.Queue()

    # Mimic SpawnManager._wire_inprocess_chunk_forward.
    async def _forward(chunk: Any) -> None:
        queue = leader_sc.stream_queue
        if queue is None:
            return
        await queue.put(chunk)

    teammate_sc.add_chunk_observer(_forward)

    await teammate_sc._stream_one_round("any-query")

    leader_seen: list[Any] = []
    while not leader_sc.stream_queue.empty():
        leader_seen.append(leader_sc.stream_queue.get_nowait())

    assert len(leader_seen) == 2
    assert all(isinstance(ch, TeamOutputSchema) for ch in leader_seen)
    assert {ch.source_member for ch in leader_seen} == {"teammate_m"}
    assert {ch.role for ch in leader_seen} == {TeamRole.TEAMMATE}
    assert [ch.payload for ch in leader_seen] == [{"step": 1}, {"step": 2}]


@pytest.mark.asyncio
async def test_forward_observer_drops_when_leader_queue_unset() -> None:
    """If leader's stream_queue is None (leader not streaming yet / already
    closed), the forward observer must drop chunks rather than buffer.
    Buffering would invert ownership of the data flow.
    """
    raw_chunks = [OutputSchema(type="message", index=0, payload={"step": 1})]

    leader_sc = _make_controller_with(
        member_name="leader_m", harness=_make_harness_with_abort([]), role=TeamRole.LEADER
    )
    # Intentionally leave leader_sc.stream_queue as None.

    teammate_sc = _make_controller_with(
        member_name="teammate_m", harness=_harness_yielding(raw_chunks), role=TeamRole.TEAMMATE
    )
    teammate_sc.stream_queue = asyncio.Queue()

    forwarded: list[Any] = []

    async def _forward(chunk: Any) -> None:
        queue = leader_sc.stream_queue
        if queue is None:
            return
        forwarded.append(chunk)
        await queue.put(chunk)

    teammate_sc.add_chunk_observer(_forward)

    # Must not raise even though leader queue is None.
    await teammate_sc._stream_one_round("any-query")

    assert forwarded == []
    assert teammate_sc.stream_queue.qsize() == 1  # teammate's own queue unaffected
