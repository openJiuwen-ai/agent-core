# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the forward-layer ``StreamController`` over a ``MemberRuntime``.

The runtime (NativeHarness/TeamHarness, or a CLI runtime) owns round driving
and input delivery; the StreamController only forwards + tags the runtime's
output chunks, maps its phase/round events onto MemberStatus / ExecutionStatus,
and forwards cancel/abort. These tests drive a lightweight fake runtime so the
mapping/forwarding contract is exercised without a real DeepAgent.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable

import pytest

from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import _RETRY_QUERY, StreamController
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.stream.base import OutputSchema


class _FakeRuntime:
    """Duck-typed ``MemberRuntime`` exposing only what the controller drives.

    Holds a fixed list of output chunks for ``outputs``, records abort/send
    calls, and lets a test fire phase/round callbacks the way a real runtime's
    supervisor would (kwargs narrowed to the callback's declared parameters,
    so the controller's ``_map_state(new)`` / ``_map_round(kind, result)`` are
    invoked positionally).
    """

    def __init__(self, chunks: list[Any] | None = None) -> None:
        self._chunks = chunks or []
        self.state = HarnessState.IDLE
        self.abort_calls: list[bool] = []
        self.sent: list[tuple[Any, bool]] = []
        self._state_cbs: list[Callable[..., Any]] = []
        self._round_cbs: list[Callable[..., Any]] = []
        self._pending_interrupt = False

    def outputs(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        if on_state is not None:
            self._state_cbs.append(on_state)
        if on_round is not None:
            self._round_cbs.append(on_round)

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        self.sent.append((content, immediate))
        return None

    async def abort(self, *, immediate: bool = False) -> None:
        self.abort_calls.append(immediate)

    def has_pending_interrupt(self) -> bool:
        return self._pending_interrupt

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        return False

    async def fire_state(self, new: HarnessState) -> None:
        for cb in self._state_cbs:
            await cb(new)

    async def fire_round(self, kind: str, result: dict | None = None) -> None:
        for cb in self._round_cbs:
            await cb(kind, result)


def _make_controller(
    runtime: _FakeRuntime,
    *,
    member_name: str = "m",
    role: TeamRole = TeamRole.LEADER,
    state: TeamAgentState | None = None,
    status_updater: Callable[[MemberStatus], Any] | None = None,
    execution_updater: Callable[[ExecutionStatus], Any] | None = None,
    wake_mailbox_callback: Callable[[], Any] | None = None,
    request_completion_poll_callback: Callable[[], Any] | None = None,
) -> StreamController:
    """Wire a StreamController against a fake runtime with overridable hooks."""
    blueprint = SimpleNamespace(member_name=member_name, role=role)

    async def _noop(_: Any) -> None:
        return None

    return StreamController(
        blueprint_getter=lambda: blueprint,
        state=state or TeamAgentState(),
        resources=PrivateAgentResources(harness=runtime),
        status_updater=status_updater or _noop,
        execution_updater=execution_updater or _noop,
        wake_mailbox_callback=wake_mailbox_callback,
        request_completion_poll_callback=request_completion_poll_callback,
    )


def _task_failed_chunk(text: str) -> Any:
    """Build a chunk shaped like a DeepAgent ``task_failed`` frame."""
    payload = SimpleNamespace(type="task_failed", data=[SimpleNamespace(text=text)])
    return SimpleNamespace(payload=payload)


# ----------------------------------------------------------------------
# Chunk tagging
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_tag_chunk_upgrades_plain_outputschema() -> None:
    """Plain OutputSchema gets upgraded to TeamOutputSchema with source_member + role."""
    sc = _make_controller(_FakeRuntime())

    raw = OutputSchema(type="message", index=0, payload={"text": "hi"})
    tagged = sc._tag_chunk(raw)

    assert isinstance(tagged, TeamOutputSchema)
    assert tagged.source_member == "m"
    assert tagged.role == TeamRole.LEADER
    assert tagged.type == "message"
    assert tagged.payload == {"text": "hi"}
    # Original chunk must not be mutated — runtime internals may keep a ref.
    assert not isinstance(raw, TeamOutputSchema)


@pytest.mark.level1
def test_tag_chunk_passes_through_team_output_with_matching_identity() -> None:
    """An already-tagged chunk with matching member + role is returned unchanged."""
    sc = _make_controller(_FakeRuntime())

    pre_tagged = TeamOutputSchema(type="message", index=0, payload={}, source_member="m", role=TeamRole.LEADER)
    out = sc._tag_chunk(pre_tagged)

    assert out is pre_tagged


@pytest.mark.level1
def test_tag_chunk_rewrites_team_output_with_mismatched_member() -> None:
    """An already-tagged chunk with a different member is re-tagged."""
    sc = _make_controller(_FakeRuntime())

    pre_tagged = TeamOutputSchema(type="message", index=0, payload={}, source_member="other", role=TeamRole.TEAMMATE)
    out = sc._tag_chunk(pre_tagged)

    assert isinstance(out, TeamOutputSchema)
    assert out.source_member == "m"
    assert out.role == TeamRole.LEADER
    assert pre_tagged.source_member == "other"  # original untouched
    assert pre_tagged.role == TeamRole.TEAMMATE


@pytest.mark.level1
def test_tag_chunk_passes_through_non_outputschema() -> None:
    """Non-OutputSchema chunks (custom payloads) survive tagging untouched."""
    sc = _make_controller(_FakeRuntime())

    custom = SimpleNamespace(type="custom", payload={"x": 1})
    out = sc._tag_chunk(custom)

    assert out is custom


# ----------------------------------------------------------------------
# Output forwarding + observers
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_forward_outputs_tags_and_fans_out_to_observers() -> None:
    """_forward_outputs must tag every chunk and broadcast to observers."""
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]
    sc = _make_controller(_FakeRuntime(raw_chunks))
    sc.stream_queue = asyncio.Queue()

    received: list[Any] = []

    async def _observer(chunk: Any) -> None:
        received.append(chunk)

    sc.add_chunk_observer(_observer)

    await sc._forward_outputs()

    queued: list[Any] = []
    while not sc.stream_queue.empty():
        queued.append(sc.stream_queue.get_nowait())
    assert len(queued) == 2
    assert all(isinstance(c, TeamOutputSchema) and c.source_member == "m" and c.role == TeamRole.LEADER for c in queued)
    # Observer saw the same tagged objects (no copies between queue and fan-out).
    assert received == queued


@pytest.mark.asyncio
@pytest.mark.level1
async def test_observer_exception_auto_detaches_and_does_not_block_stream() -> None:
    """A misbehaving observer is detached and never blocks the producer."""
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]
    sc = _make_controller(_FakeRuntime(raw_chunks))
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

    await sc._forward_outputs()

    # Bad observer ran exactly once before being detached.
    assert bad_calls == [0]
    # Good observer kept receiving all chunks.
    assert len(good_calls) == 2
    # Both chunks made it into the local queue regardless.
    assert sc.stream_queue.qsize() == 2


@pytest.mark.level1
def test_remove_chunk_observer_is_idempotent() -> None:
    """Removing an observer that was never registered must not raise."""
    sc = _make_controller(_FakeRuntime())

    async def _observer(_: Any) -> None:
        return None

    sc.remove_chunk_observer(_observer)  # not registered → silent
    sc.add_chunk_observer(_observer)
    sc.remove_chunk_observer(_observer)
    sc.remove_chunk_observer(_observer)  # second remove → silent
    assert sc._chunk_observers == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_teammate_chunks_reach_leader_queue_via_forward_observer() -> None:
    """End-to-end: teammate chunks flow into leader's stream_queue tagged
    with the teammate's member name — same data path SpawnManager wires
    when an in-process teammate is spawned.
    """
    raw_chunks = [
        OutputSchema(type="message", index=0, payload={"step": 1}),
        OutputSchema(type="message", index=1, payload={"step": 2}),
    ]
    leader_sc = _make_controller(_FakeRuntime(), member_name="leader_m", role=TeamRole.LEADER)
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_controller(_FakeRuntime(raw_chunks), member_name="teammate_m", role=TeamRole.TEAMMATE)
    teammate_sc.stream_queue = asyncio.Queue()

    # Mimic SpawnManager._wire_inprocess_chunk_forward.
    async def _forward(chunk: Any) -> None:
        queue = leader_sc.stream_queue
        if queue is None:
            return
        await queue.put(chunk)

    teammate_sc.add_chunk_observer(_forward)

    await teammate_sc._forward_outputs()

    leader_seen: list[Any] = []
    while not leader_sc.stream_queue.empty():
        leader_seen.append(leader_sc.stream_queue.get_nowait())

    assert len(leader_seen) == 2
    assert all(isinstance(ch, TeamOutputSchema) for ch in leader_seen)
    assert {ch.source_member for ch in leader_seen} == {"teammate_m"}
    assert {ch.role for ch in leader_seen} == {TeamRole.TEAMMATE}
    assert [ch.payload for ch in leader_seen] == [{"step": 1}, {"step": 2}]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_forward_observer_drops_when_leader_queue_unset() -> None:
    """If leader's stream_queue is None (leader not streaming yet / already
    closed), the forward observer must drop chunks rather than buffer.
    """
    raw_chunks = [OutputSchema(type="message", index=0, payload={"step": 1})]
    leader_sc = _make_controller(_FakeRuntime(), member_name="leader_m", role=TeamRole.LEADER)
    # Intentionally leave leader_sc.stream_queue as None.

    teammate_sc = _make_controller(_FakeRuntime(raw_chunks), member_name="teammate_m", role=TeamRole.TEAMMATE)
    teammate_sc.stream_queue = asyncio.Queue()

    forwarded: list[Any] = []

    async def _forward(chunk: Any) -> None:
        queue = leader_sc.stream_queue
        if queue is None:
            return
        forwarded.append(chunk)
        await queue.put(chunk)

    teammate_sc.add_chunk_observer(_forward)

    await teammate_sc._forward_outputs()

    assert forwarded == []
    assert teammate_sc.stream_queue.qsize() == 1  # teammate's own queue unaffected


# ----------------------------------------------------------------------
# Transient-retry on the forward layer
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_handle_retry_swallows_and_redrives_retryable_failure() -> None:
    """A retryable task_failed within the budget swallows the round and re-drives."""
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    consumed = await sc._handle_retry(_task_failed_chunk("[181001] transient blip"))

    assert consumed is True
    assert sc._swallow_failed_round is True
    assert sc._retry_attempt == 1
    assert runtime.sent == [(_RETRY_QUERY, False)]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_handle_retry_forwards_non_retryable_failure() -> None:
    """A non-retryable task_failed falls through so the consumer sees the error."""
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    consumed = await sc._handle_retry(_task_failed_chunk("[999999] fatal"))

    assert consumed is False
    assert sc._swallow_failed_round is False
    assert runtime.sent == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_handle_retry_ignores_normal_chunk() -> None:
    """A normal (non task_failed) chunk is never consumed by retry handling."""
    sc = _make_controller(_FakeRuntime())

    consumed = await sc._handle_retry(OutputSchema(type="message", index=0, payload={"text": "hi"}))

    assert consumed is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_handle_retry_stops_after_attempt_budget_exhausted() -> None:
    """Past the attempt budget a retryable failure stops being swallowed."""
    from openjiuwen.agent_teams.agent.stream_controller import _MAX_RETRY_ATTEMPTS

    runtime = _FakeRuntime()
    sc = _make_controller(runtime)
    sc._retry_attempt = _MAX_RETRY_ATTEMPTS

    consumed = await sc._handle_retry(_task_failed_chunk("[181001] transient"))

    assert consumed is False
    assert runtime.sent == []


# ----------------------------------------------------------------------
# Phase → MemberStatus mapping
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_state_running_sets_busy() -> None:
    """A RUNNING phase transition marks the member BUSY."""
    statuses: list[MemberStatus] = []

    async def _record(status: MemberStatus) -> None:
        statuses.append(status)

    sc = _make_controller(_FakeRuntime(), status_updater=_record)

    await sc._map_state(HarnessState.RUNNING)

    assert statuses == [MemberStatus.BUSY]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_state_idle_sets_ready_and_settles() -> None:
    """An IDLE phase transition marks READY and runs the idle-settled hook."""
    statuses: list[MemberStatus] = []
    polls: list[None] = []

    async def _record(status: MemberStatus) -> None:
        statuses.append(status)

    async def _poll() -> None:
        polls.append(None)

    sc = _make_controller(_FakeRuntime(), status_updater=_record, request_completion_poll_callback=_poll)
    sc.stream_queue = asyncio.Queue()

    await sc._map_state(HarnessState.IDLE)

    assert statuses == [MemberStatus.READY]
    assert polls == [None]  # idle-settled fired the completion poll


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_state_idle_stamps_the_idle_clock() -> None:
    """Settling into IDLE starts the member's process-local idle clock.

    The stall sweeps measure staleness from this stamp rather than from the
    task row's ``updated_at``, so a paused team cannot fabricate a stall
    (F_65).
    """
    state = TeamAgentState()
    sc = _make_controller(_FakeRuntime(), state=state)
    sc.stream_queue = asyncio.Queue()

    before = time.monotonic()
    await sc._map_state(HarnessState.IDLE)

    assert state.idle_since is not None
    assert state.idle_since >= before


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_state_running_clears_the_idle_clock() -> None:
    """A member driving a round is progressing, so it carries no idle clock."""
    state = TeamAgentState(idle_since=time.monotonic() - 100)
    sc = _make_controller(_FakeRuntime(), state=state)

    await sc._map_state(HarnessState.RUNNING)

    assert state.idle_since is None


# ----------------------------------------------------------------------
# Round → ExecutionStatus mapping
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_round_started_walks_starting_running() -> None:
    transitions: list[ExecutionStatus] = []

    async def _record(status: ExecutionStatus) -> None:
        transitions.append(status)

    sc = _make_controller(_FakeRuntime(), execution_updater=_record)
    sc._swallow_failed_round = True  # started must reset the swallow latch

    await sc._map_round("started")

    assert transitions == [ExecutionStatus.STARTING, ExecutionStatus.RUNNING]
    assert sc._swallow_failed_round is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_map_round_finished_walks_completing_completed_idle() -> None:
    transitions: list[ExecutionStatus] = []

    async def _record(status: ExecutionStatus) -> None:
        transitions.append(status)

    sc = _make_controller(_FakeRuntime(), execution_updater=_record)

    await sc._map_round("finished")

    assert transitions == [ExecutionStatus.COMPLETING, ExecutionStatus.COMPLETED, ExecutionStatus.IDLE]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_map_round_aborted_walks_cancel_path() -> None:
    transitions: list[ExecutionStatus] = []

    async def _record(status: ExecutionStatus) -> None:
        transitions.append(status)

    sc = _make_controller(_FakeRuntime(), execution_updater=_record)

    await sc._map_round("aborted")

    assert transitions == [
        ExecutionStatus.CANCEL_REQUESTED,
        ExecutionStatus.CANCELLING,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.IDLE,
    ]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_map_round_failed_walks_failed_idle() -> None:
    transitions: list[ExecutionStatus] = []

    async def _record(status: ExecutionStatus) -> None:
        transitions.append(status)

    sc = _make_controller(_FakeRuntime(), execution_updater=_record)

    await sc._map_round("failed")

    assert transitions == [ExecutionStatus.FAILED, ExecutionStatus.IDLE]


# ----------------------------------------------------------------------
# Cancel / abort forwarding
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cancel_agent_forwards_immediate_abort() -> None:
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    await sc.cancel_agent()

    assert runtime.abort_calls == [True]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cooperative_cancel_forwards_graceful_abort() -> None:
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    await sc.cooperative_cancel()

    assert runtime.abort_calls == [False]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_drain_agent_task_forwards_immediate_abort() -> None:
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    await sc.drain_agent_task()

    assert runtime.abort_calls == [True]


@pytest.mark.level1
def test_is_agent_running_reflects_runtime_phase() -> None:
    runtime = _FakeRuntime()
    sc = _make_controller(runtime)

    assert sc.is_agent_running() is False
    runtime.state = HarnessState.RUNNING
    assert sc.is_agent_running() is True
    assert sc.has_in_flight_round() is True


# ----------------------------------------------------------------------
# Idle-settled teardown: state.team_cleaned latch + completion poll
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_idle_settled_closes_stream_when_team_cleaned() -> None:
    """An idle-settle on a cleaned team closes the stream (None sentinel)."""
    state = TeamAgentState()
    state.team_cleaned = True
    sc = _make_controller(_FakeRuntime(), state=state)
    sc.stream_queue = asyncio.Queue()

    await sc._map_state(HarnessState.IDLE)

    assert sc.stream_queue.get_nowait() is None
    assert sc.stream_queue.empty()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_settled_no_close_when_not_cleaned() -> None:
    """A normal idle-settle with team_cleaned False must NOT enqueue None."""
    sc = _make_controller(_FakeRuntime())
    sc.stream_queue = asyncio.Queue()

    await sc._map_state(HarnessState.IDLE)

    assert sc.stream_queue.empty()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_settled_wakes_mailbox_and_polls() -> None:
    """Idle-settle on a live team wakes the mailbox then fires completion poll."""
    order: list[str] = []

    def _wake() -> None:
        order.append("wake")

    async def _poll() -> None:
        order.append("poll")

    sc = _make_controller(
        _FakeRuntime(),
        wake_mailbox_callback=_wake,
        request_completion_poll_callback=_poll,
    )
    sc.stream_queue = asyncio.Queue()

    await sc._map_state(HarnessState.IDLE)

    assert order == ["wake", "poll"]


# ----------------------------------------------------------------------
# Completion marker
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_emit_completion_and_close_marker_precedes_sentinel() -> None:
    """The completion marker lands on the queue strictly before the None sentinel."""
    sc = _make_controller(_FakeRuntime())
    sc.stream_queue = asyncio.Queue()

    sc.emit_completion_and_close(member_count=2, task_count=3)

    marker = sc.stream_queue.get_nowait()
    assert isinstance(marker, TeamOutputSchema)
    assert marker.payload["event_type"] == "team.completed"
    assert marker.payload["member_count"] == 2
    assert marker.payload["task_count"] == 3
    assert marker.source_member == "m"
    assert marker.role == TeamRole.LEADER
    assert sc.stream_queue.get_nowait() is None


@pytest.mark.level1
def test_emit_completion_and_close_noop_without_queue() -> None:
    """No queue means the round already tore down — emit is a silent no-op."""
    sc = _make_controller(_FakeRuntime())
    sc.stream_queue = None

    sc.emit_completion_and_close(member_count=1, task_count=1)


# ----------------------------------------------------------------------
# start / stop lifecycle: attach + detach the forwarder
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_start_registers_events_and_pumps_outputs() -> None:
    """start registers the phase/round mappers and pumps outputs into the queue."""
    raw_chunks = [OutputSchema(type="message", index=0, payload={"step": 1})]
    runtime = _FakeRuntime(raw_chunks)
    sc = _make_controller(runtime)
    sc.stream_queue = asyncio.Queue()

    await sc.start()
    # The forwarder drains the fake runtime's finite outputs; await it.
    assert sc._forward_task is not None
    await sc._forward_task
    await sc.stop()

    assert len(runtime._state_cbs) == 1
    assert len(runtime._round_cbs) == 1
    chunk = sc.stream_queue.get_nowait()
    assert isinstance(chunk, TeamOutputSchema)
    assert chunk.payload == {"step": 1}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stop_cancels_a_running_forwarder() -> None:
    """stop cancels a forwarder still blocked on a long-lived output stream."""

    class _BlockingRuntime(_FakeRuntime):
        def outputs(self) -> AsyncIterator[Any]:
            async def _gen() -> AsyncIterator[Any]:
                await asyncio.Event().wait()  # never completes
                yield  # pragma: no cover

            return _gen()

    sc = _make_controller(_BlockingRuntime())
    sc.stream_queue = asyncio.Queue()

    await sc.start()
    forward_task = sc._forward_task
    assert forward_task is not None
    await asyncio.sleep(0.01)  # let the forwarder block on outputs()
    await sc.stop()

    assert forward_task.done()
    assert sc._forward_task is None
