# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Concurrent shutdown_all_handles teardown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle


def _make_spawn_manager() -> SpawnManager:
    return SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: SimpleNamespace(stream_controller=None),
    )


class _SlowKillHandle(InProcessSpawnHandle):
    """Handle whose force_kill overlaps with peers when shutdown is concurrent."""

    def __init__(self, started: list[str], name: str, gate: asyncio.Event) -> None:
        super().__init__(process_id=f"inproc-{name}")
        self._name = name
        self._started = started
        self._gate = gate
        self._task = asyncio.create_task(asyncio.sleep(3600), name=f"member-{name}")

    async def force_kill(self) -> None:
        self._started.append(f"start:{self._name}")
        self._gate.set()
        # Hold long enough that a serial loop would finish peer cleanups
        # before this returns; concurrent gather must overlap.
        await asyncio.sleep(0.05)
        await super().force_kill()
        self._started.append(f"done:{self._name}")


@pytest.mark.asyncio
async def test_shutdown_all_handles_runs_cleanups_concurrently() -> None:
    sm = _make_spawn_manager()
    started: list[str] = []
    gate = asyncio.Event()

    sm.spawned_handles["alice"] = _SlowKillHandle(started, "alice", gate)
    sm.spawned_handles["bob"] = _SlowKillHandle(started, "bob", gate)

    await sm.shutdown_all_handles()

    assert sm.spawned_handles == {}
    # Both force_kills must have begun before either finished — proves overlap.
    assert started.index("start:alice") < started.index("done:bob")
    assert started.index("start:bob") < started.index("done:alice")
    assert "done:alice" in started and "done:bob" in started


@pytest.mark.asyncio
async def test_shutdown_all_handles_isolates_cleanup_failures() -> None:
    sm = _make_spawn_manager()

    class _BoomHandle(InProcessSpawnHandle):
        def __init__(self) -> None:
            super().__init__(process_id="boom")
            self._task = asyncio.create_task(asyncio.sleep(3600))

        async def force_kill(self) -> None:
            # Cancel first so the test does not leak a sleeping task, then
            # surface the failure cleanup_teammate is expected to swallow.
            await super().force_kill()
            raise RuntimeError("boom")

    ok = InProcessSpawnHandle(process_id="ok")
    ok._task = asyncio.create_task(asyncio.sleep(3600))

    sm.spawned_handles["bad"] = _BoomHandle()
    sm.spawned_handles["good"] = ok

    await sm.shutdown_all_handles()

    assert sm.spawned_handles == {}
    assert ok._task.done()
