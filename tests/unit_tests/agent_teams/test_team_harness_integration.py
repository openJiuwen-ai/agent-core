# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end integration over the adopted TeamHarness + StreamController chain.

Unlike ``test_stream_controller`` (which drives a fake MemberRuntime), these
tests wire the **real** TeamHarness — composing a **real** NativeHarness over the
real task-loop kernel — to a **real** StreamController, and drive the full chain:

    harness.start(team_session) -> stream_controller.start()
        -> harness.send(query) -> native supervisor runs a round
        -> native.outputs() chunks -> StreamController._forward_outputs
        -> TeamOutputSchema tagged into stream_queue
        -> harness phase/round events -> _map_state / _map_round

The only fake is the inner ``react_agent`` (the LLM), injected after start via
the shared NativeHarness fixtures — the same seam the stage-A native tests use.
This exercises the collection logic the unit tests stub out: that the team layer
really forwards a live native's stream and maps its real phase/round events.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness, TeamHarness
from openjiuwen.agent_teams.harness.team_harness import _MountedRails
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.runner import Runner
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    make_provider,
    wait_for_state,
    wait_invoke_running,
)


def _make_team_harness(*, member_name: str, role: TeamRole = TeamRole.LEADER) -> TeamHarness:
    """Build a TeamHarness over a real NativeHarness (rails stubbed).

    Construct directly (not via ``build``) so the test wires the interaction
    chain without the team rail-mounting machinery; the rails are not exercised
    by the forward/mapping path under test.
    """
    provider = make_provider()
    native = NativeHarness(provider)
    native.prepare_config()
    rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    return TeamHarness(provider, native, rails, role=role, member_name=member_name)


class _Recorder:
    """Wire a StreamController over a TeamHarness and record status transitions."""

    def __init__(self, harness: TeamHarness, *, member_name: str, role: TeamRole) -> None:
        self.statuses: list[MemberStatus] = []
        self.executions: list[ExecutionStatus] = []
        blueprint = SimpleNamespace(member_name=member_name, role=role)

        async def _status(status: MemberStatus) -> None:
            self.statuses.append(status)

        async def _exec(status: ExecutionStatus) -> None:
            self.executions.append(status)

        self.controller = StreamController(
            blueprint_getter=lambda: blueprint,
            state=TeamAgentState(),
            resources=PrivateAgentResources(harness=harness),
            status_updater=_status,
            execution_updater=_exec,
        )
        self.controller.stream_queue = asyncio.Queue()


async def _inject_fake(harness: TeamHarness, *, sleep_seconds: float = 0.0, answer_output: str = "") -> FakeReactAgent:
    """Swap the real inner react_agent for a scripted fake after start."""
    native = harness.inner_agent
    fake = FakeReactAgent(native.card)
    fake.sleep_seconds = sleep_seconds
    fake.answer_output = answer_output
    native.set_react_agent(fake, initialized=True)
    return fake


async def _collect_until(queue: asyncio.Queue, predicate, *, timeout: float = 3.0) -> list[Any]:
    """Drain ``queue`` (skipping the None sentinel) until ``predicate(item)`` holds."""
    collected: list[Any] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if item is None:
            continue
        collected.append(item)
        if predicate(item):
            break
    return collected


@pytest.mark.asyncio
@pytest.mark.level1
async def test_real_native_round_forwards_tagged_stream_and_maps_status() -> None:
    """A real native round flows tagged chunks into the team stream + maps status."""
    await Runner.start()
    harness = _make_team_harness(member_name="leader")
    rec = _Recorder(harness, member_name="leader", role=TeamRole.LEADER)
    try:
        await harness.start(team_session=None)
        await _inject_fake(harness, answer_output="done")
        await rec.controller.start()

        await harness.send("hello")
        assert await wait_for_state(harness, HarnessState.IDLE)

        # The round produced a final answer chunk; collect until it arrives.
        chunks = await _collect_until(
            rec.controller.stream_queue,
            lambda c: getattr(c, "type", None) == "answer",
        )
    finally:
        await rec.controller.stop()
        await harness.stop()
        await Runner.stop()

    # Every forwarded chunk is upgraded to TeamOutputSchema tagged with the member.
    assert chunks, "expected the live native round to produce stream chunks"
    assert all(isinstance(c, TeamOutputSchema) for c in chunks)
    assert {c.source_member for c in chunks} == {"leader"}
    assert {c.role for c in chunks} == {TeamRole.LEADER}
    answer = next(c for c in chunks if c.type == "answer")
    assert answer.payload["output"] == "done"

    # Phase events mapped onto member status: BUSY while running, READY at idle.
    assert MemberStatus.BUSY in rec.statuses
    assert rec.statuses[-1] == MemberStatus.READY
    # Round events mapped onto the execution state machine (started -> finished).
    assert ExecutionStatus.RUNNING in rec.executions
    assert ExecutionStatus.COMPLETED in rec.executions
    assert rec.executions[-1] == ExecutionStatus.IDLE


@pytest.mark.asyncio
@pytest.mark.level1
async def test_real_native_immediate_abort_through_team_chain() -> None:
    """An immediate abort through the team chain cancels the live round + maps CANCELLED."""
    await Runner.start()
    harness = _make_team_harness(member_name="leader")
    rec = _Recorder(harness, member_name="leader", role=TeamRole.LEADER)
    try:
        await harness.start(team_session=None)
        fake = await _inject_fake(harness, sleep_seconds=30.0)  # long round → abortable
        await rec.controller.start()

        await harness.send("work for a while")
        await wait_invoke_running(fake)  # the inner work is genuinely in-flight

        # Cancel through the StreamController seam (cooperative cancel forwards
        # an immediate abort to the runtime).
        await rec.controller.cancel_agent()
        assert await wait_for_state(harness, HarnessState.IDLE)

        markers = await _collect_until(
            rec.controller.stream_queue,
            lambda c: getattr(c, "type", None) == "round_aborted",
        )
    finally:
        await rec.controller.stop()
        await harness.stop()
        await Runner.stop()

    # The native emitted a round_aborted marker; the forwarder tagged + relayed it.
    assert any(getattr(c, "type", None) == "round_aborted" for c in markers)
    assert fake.cancelled_count >= 1, "the inner invoke must observe the cancellation"
    # The round event mapped to a CANCELLED execution transition, settling to IDLE.
    assert ExecutionStatus.CANCELLED in rec.executions
    assert rec.executions[-1] == ExecutionStatus.IDLE
    assert rec.statuses[-1] == MemberStatus.READY


@pytest.mark.asyncio
@pytest.mark.level1
async def test_teammate_round_fans_out_to_leader_queue_over_real_natives() -> None:
    """A teammate's live native round fans out into the leader's stream_queue.

    Mirrors SpawnManager's in-process chunk forward: a teammate StreamController
    observer pushes the teammate's (real, tagged) chunks into the leader's queue.
    """
    await Runner.start()
    leader = _make_team_harness(member_name="leader", role=TeamRole.LEADER)
    teammate = _make_team_harness(member_name="dev-1", role=TeamRole.TEAMMATE)
    leader_rec = _Recorder(leader, member_name="leader", role=TeamRole.LEADER)
    teammate_rec = _Recorder(teammate, member_name="dev-1", role=TeamRole.TEAMMATE)

    async def _forward(chunk: Any) -> None:
        await leader_rec.controller.stream_queue.put(chunk)

    try:
        await leader.start(team_session=None)
        await teammate.start(team_session=None)
        await _inject_fake(leader, answer_output="leader-done")
        await _inject_fake(teammate, answer_output="teammate-done")
        await leader_rec.controller.start()
        await teammate_rec.controller.start()
        teammate_rec.controller.add_chunk_observer(_forward)

        await teammate.send("do the work")
        assert await wait_for_state(teammate, HarnessState.IDLE)

        leader_seen = await _collect_until(
            leader_rec.controller.stream_queue,
            lambda c: getattr(c, "type", None) == "answer",
        )
    finally:
        await leader_rec.controller.stop()
        await teammate_rec.controller.stop()
        await teammate.stop()
        await leader.stop()
        await Runner.stop()

    # The teammate's chunks reached the leader's queue, tagged as the teammate's.
    assert leader_seen
    assert all(isinstance(c, TeamOutputSchema) for c in leader_seen)
    assert {c.source_member for c in leader_seen} == {"dev-1"}
    assert {c.role for c in leader_seen} == {TeamRole.TEAMMATE}
    answer = next(c for c in leader_seen if c.type == "answer")
    assert answer.payload["output"] == "teammate-done"
