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
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.reliability import ReliabilityConfig
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

pytestmark = pytest.mark.level1


def _make_leader_with_reliability(enabled: bool = True) -> TeamAgent:
    team_spec = TeamSpec(team_name="rel-team", display_name="rel-team", leader_member_name="leader-1")
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="rel-team",
        lifecycle="temporary",
        leader=LeaderSpec(member_name="leader-1", display_name="Leader", persona="PM"),
        reliability=ReliabilityConfig(enabled=enabled),
    )
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        persona="PM",
        team_spec=team_spec,
        db_config=DatabaseConfig(db_type="memory"),
    )
    agent = TeamAgent(AgentCard(id="leader-1", name="leader", description="test"))
    agent.configure(spec, context)
    return agent


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
