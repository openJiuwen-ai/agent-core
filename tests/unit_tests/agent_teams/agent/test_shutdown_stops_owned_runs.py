# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``shutdown_self`` stops the member's swarmflow runs before the stream unwind.

The unwind path (finalize_round -> harness.stop -> native.stop) bare-cancels
in-flight async tools: the run task dies with no terminal event and no journal
record, and its finally deregisters the ticket — so the dispose-time
``stop_by_owner`` arrives to an empty registry and the embedder's workflow card
stays "running" forever. Shutdown therefore stops the owned runs through the
session-level controller first, while the tickets are still live. The
controller is the team-level registry, so the stop does not depend on the
member's own harness.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []


class _Controller:
    def __init__(self, recorder: _Recorder, *, fail: bool = False) -> None:
        self.recorder = recorder
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []

    async def stop_by_owner(self, member_name: str | None, *, include_paused: bool = False) -> bool:
        self.recorder.events.append("stop_by_owner")
        self.calls.append((member_name, include_paused))
        if self.fail:
            raise RuntimeError("controller down")
        return True


class _StreamController:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    async def cooperative_cancel(self) -> None:
        self.recorder.events.append("cooperative_cancel")


def _agent(recorder: _Recorder, controller: Any) -> TeamAgent:
    agent = TeamAgent(AgentCard(id="worker-1", name="worker-1", description="worker-1"))
    team_spec = TeamSpec(team_name="t", display_name="t", leader_member_name="leader")
    ctx = TeamRuntimeContext(role=TeamRole.TEAMMATE, member_name="worker-1", team_spec=team_spec)
    agent._configurator._blueprint = SimpleNamespace(spec=None, ctx=ctx, language="cn")
    agent._configurator.harness = SimpleNamespace(background_task_controller=controller)
    agent._stream_controller = _StreamController(recorder)
    agent._coordination = SimpleNamespace(
        close_stream=lambda: recorder.events.append("close_stream")
    )
    return agent


@pytest.mark.asyncio
async def test_shutdown_stops_owned_runs_before_the_stream_unwind():
    recorder = _Recorder()
    controller = _Controller(recorder)
    agent = _agent(recorder, controller)

    await agent.shutdown_self(force=True)

    assert recorder.events[:2] == ["stop_by_owner", "cooperative_cancel"]
    assert controller.calls == [("worker-1", True)]


@pytest.mark.asyncio
async def test_shutdown_without_a_controller_still_unwinds():
    recorder = _Recorder()
    agent = _agent(recorder, None)

    await agent.shutdown_self(force=True)

    assert recorder.events[0] == "cooperative_cancel"
    assert "close_stream" in recorder.events


@pytest.mark.asyncio
async def test_controller_failure_does_not_block_the_shutdown():
    recorder = _Recorder()
    agent = _agent(recorder, _Controller(recorder, fail=True))

    await agent.shutdown_self(force=True)

    assert recorder.events[:2] == ["stop_by_owner", "cooperative_cancel"]
    assert "close_stream" in recorder.events
