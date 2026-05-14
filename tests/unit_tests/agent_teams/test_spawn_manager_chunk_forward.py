# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Chunk fan-out wiring inside SpawnManager.

Exercises ``_wire_inprocess_chunk_forward`` and the matching cleanup
path, ensuring that an in-process teammate's stream chunks reach the
leader's stream_queue and that the forwarder is detached on teardown.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.session.stream.base import OutputSchema


def _make_stream_controller(member_name: str, *, role: TeamRole = TeamRole.TEAMMATE) -> StreamController:
    """Build a StreamController with the minimum wiring for observer tests."""
    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    state = TeamAgentState()
    blueprint = SimpleNamespace(member_name=member_name, role=role)

    async def _noop(_: Any) -> None:
        return None

    return StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=None),
        status_updater=_noop,
        execution_updater=_noop,
    )


def _make_spawn_manager_with(leader_sc: StreamController) -> SpawnManager:
    """Construct SpawnManager with a fake leader exposing stream_controller."""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    leader_agent = SimpleNamespace(stream_controller=leader_sc)
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: leader_agent,
    )
    return sm


def _make_handle_with(teammate_sc: StreamController) -> InProcessSpawnHandle:
    teammate_agent = SimpleNamespace(stream_controller=teammate_sc)
    return InProcessSpawnHandle(
        process_id="inproc-test",
        agent_ref=teammate_agent,
    )


@pytest.mark.asyncio
async def test_wire_forward_routes_teammate_chunk_to_leader_queue() -> None:
    """After wiring, a chunk on the teammate stream_controller observer
    chain must land in the leader's stream_queue.
    """
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_stream_controller("teammate_m")

    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_handle_with(teammate_sc)

    sm._wire_inprocess_chunk_forward(handle)

    assert handle.chunk_forward is not None
    assert teammate_sc._chunk_observers == [handle.chunk_forward]

    raw = OutputSchema(type="message", index=0, payload={"x": 1})
    tagged = teammate_sc._tag_chunk(raw)
    await handle.chunk_forward(tagged)

    received = await asyncio.wait_for(leader_sc.stream_queue.get(), timeout=1.0)
    assert received is tagged
    assert received.source_member == "teammate_m"
    assert received.role == TeamRole.TEAMMATE


@pytest.mark.asyncio
async def test_cleanup_detaches_forward_observer() -> None:
    """cleanup_teammate must remove the forwarder so post-cleanup chunks
    cannot leak into the leader queue.
    """
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_stream_controller("teammate_m")

    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_handle_with(teammate_sc)
    sm._wire_inprocess_chunk_forward(handle)
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    assert handle.chunk_forward is None
    assert teammate_sc._chunk_observers == []
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_wire_skips_when_leader_or_agent_ref_missing() -> None:
    """Wire must no-op when leader or teammate agent ref is unavailable."""
    sm_no_leader = SpawnManager(
        state=SimpleNamespace(session_id="sess"),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: None,
    )
    handle = InProcessSpawnHandle(process_id="inproc-test", agent_ref=SimpleNamespace())
    sm_no_leader._wire_inprocess_chunk_forward(handle)
    assert handle.chunk_forward is None

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    handle_no_ref = InProcessSpawnHandle(process_id="inproc-test", agent_ref=None)
    sm._wire_inprocess_chunk_forward(handle_no_ref)
    assert handle_no_ref.chunk_forward is None
