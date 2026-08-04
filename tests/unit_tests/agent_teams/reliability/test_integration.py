# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end dispatch integration for the reliability framework.

Verifies the wiring beyond the unit level: an AnomalyDetectedEvent enqueued
on a real leader's coordination loop is routed through dispatch() to the
ReliabilityHandler and delivered into the leader's loop — and that when the
framework is disabled, no handler is mounted so the event is a no-op.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.reliability import ReliabilityConfig
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.factory import build_reliability_rail
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage, TeamTopic
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

pytestmark = pytest.mark.level1


def _make_leader_with_reliability(
    enabled: bool = True,
    *,
    reliability: ReliabilityConfig | None = None,
    predefined_members: list[TeamMemberSpec] | None = None,
) -> TeamAgent:
    team_spec = TeamSpec(team_name="rel-team", display_name="rel-team", leader_member_name="leader-1")
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="rel-team",
        lifecycle="temporary",
        leader=LeaderSpec(member_name="leader-1", display_name="Leader", desc="PM"),
        predefined_members=predefined_members or [],
        reliability=reliability or ReliabilityConfig(enabled=enabled),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        desc="PM",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
    )
    agent = TeamAgent(AgentCard(id="leader-1", name="leader", description="test"))
    agent.configure(spec, context)
    return agent


class _CoordinationLoopbackMessager:
    """Forward published member events into the real leader coordination loop."""

    def __init__(self, leader: TeamAgent) -> None:
        self._leader = leader
        self.published: list[tuple[str, EventMessage]] = []

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        self.published.append((topic_id, message))
        await self._leader.coordination_loop.enqueue(message)


def _anomaly_event() -> EventMessage:
    return EventMessage.from_event(
        AnomalyDetectedEvent(
            team_name="rel-team",
            member_name="dev-1",
            detector="tool_error_rate",
            kind="tool_error_rate",
            severity="medium",
            summary="5 consecutive failures",
            evidence={},
        )
    )


@pytest.mark.asyncio
async def test_anomaly_event_routes_to_leader_via_dispatch():
    agent = _make_leader_with_reliability(enabled=True)
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()
    await agent._start_coordination(session=None)

    await agent.coordination_loop.enqueue(_anomaly_event())
    await asyncio.sleep(0.1)

    await agent._stop_coordination()
    agent.deliver_input.assert_called_once()


@pytest.mark.asyncio
async def test_anomaly_ignored_when_reliability_disabled():
    agent = _make_leader_with_reliability(enabled=False)
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()
    await agent._start_coordination(session=None)

    await agent.coordination_loop.enqueue(_anomaly_event())
    await asyncio.sleep(0.1)

    await agent._stop_coordination()
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
async def test_leader_self_monitor_routes_local_anomaly():
    # monitor_roles defaults to ["leader", "teammate"], so the leader mounts
    # its own reliability rail with a LocalAnomalyReporter whose sink is bound
    # to the handler. A leader-local anomaly routes straight to deliver_input
    # without going through the messager (which would self-filter it).
    agent = _make_leader_with_reliability(enabled=True)
    agent._is_agent_running = lambda: False
    agent.deliver_input = AsyncMock()
    await agent._start_coordination(session=None)

    rails = agent._configurator.harness.find_rails(ReliabilityRail)
    assert rails  # the leader's own rail is mounted

    anomaly = Anomaly(
        detector="tool_error_rate",
        kind=AnomalyKind.TOOL_ERROR_RATE,
        severity=Severity.MEDIUM,
        member_name="leader-1",
        summary="leader own anomaly",
    )
    await rails[0]._local_reporter.report(anomaly)
    await asyncio.sleep(0.05)

    await agent._stop_coordination()
    agent.deliver_input.assert_called_once()


@pytest.mark.asyncio
async def test_three_members_one_model_outage_routes_medium_alert_to_leader():
    """One failed model call at a lowered threshold reports a MEDIUM alert."""
    members = [
        TeamMemberSpec(member_name="researcher", display_name="Researcher", desc="Collect requirements"),
        TeamMemberSpec(member_name="developer", display_name="Developer", desc="Implement the feature"),
        TeamMemberSpec(member_name="tester", display_name="Tester", desc="Verify the result"),
    ]
    assignments = {
        "researcher": "task-research",
        "developer": "task-implement",
        "tester": "task-test",
    }
    config = ReliabilityConfig(enabled=True)
    config.detectors.model_error.rate_threshold = 1
    config.detectors.model_error.consecutive_threshold = 100

    leader = _make_leader_with_reliability(reliability=config, predefined_members=members)
    leader._is_agent_running = lambda: False
    leader.deliver_input = AsyncMock()
    await leader._start_coordination(session=None)

    token = set_session_id("rel-medium-demo-session")
    try:
        messager = _CoordinationLoopbackMessager(leader)
        rails = {
            member.member_name: build_reliability_rail(
                config,
                member_name=member.member_name,
                messager=messager,
                team_name="rel-team",
                sender_id=f"node-{member.member_name}",
            )
            for member in members
        }

        healthy_response = SimpleNamespace(content="task completed", reasoning_content="")
        await rails["researcher"].after_model_call(
            AgentCallbackContext(agent=None, inputs=ModelCallInputs(response=healthy_response))
        )

        failed_ctx = AgentCallbackContext(
            agent=None,
            inputs=ModelCallInputs(),
            exception=RuntimeError("mock model endpoint unavailable"),
        )
        failed_ctx.bind_steering_queue(asyncio.Queue())
        await rails["developer"].on_model_exception(failed_ctx)

        await rails["tester"].after_model_call(
            AgentCallbackContext(agent=None, inputs=ModelCallInputs(response=healthy_response))
        )
        await asyncio.sleep(0.05)
    finally:
        reset_session_id(token)
        await leader._stop_coordination()

    assert set(assignments) == set(rails)
    assert len(messager.published) == 1
    topic, event = messager.published[0]
    assert topic == TeamTopic.TEAM.build("rel-medium-demo-session", "rel-team")
    payload = event.get_payload()
    assert isinstance(payload, AnomalyDetectedEvent)
    assert payload.member_name == "developer"
    assert payload.kind == "model_error"
    assert payload.severity == "medium"
    assert payload.evidence == {
        "consecutive": 1,
        "window_count": 1,
        "window_seconds": 120.0,
        "last_error": "mock model endpoint unavailable",
    }
    assert failed_ctx.drain_steering() == []

    leader.deliver_input.assert_awaited_once()
    leader_alert = leader.deliver_input.await_args.args[0]
    assert "[可靠性告警]" in leader_alert
    assert "[medium] developer" in leader_alert
    assert "1 consecutive failures (1 within 120s)" in leader_alert
    assert "detector=model_error" in leader_alert
