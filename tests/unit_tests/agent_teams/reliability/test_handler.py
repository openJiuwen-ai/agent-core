# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the leader-side ReliabilityHandler routing."""

import pytest

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.handler import ReliabilityHandler
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage, MessageEvent
from openjiuwen.agent_teams.schema.team import TeamRole


class _Host:
    """Round-controller stub recording delivered inputs."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_input(self, content: str) -> None:
        self.delivered.append(content)


class _Blueprint:
    """Blueprint stub exposing role + member_name."""

    def __init__(self, role: TeamRole) -> None:
        self.role = role
        self.member_name = "team_leader"


def _make_handler(role: TeamRole = TeamRole.LEADER, pingpong: PingPongDetector | None = None) -> tuple:
    host = _Host()
    handler = ReliabilityHandler(
        host,
        _Blueprint(role),
        infra=None,
        poll_ctrl=None,
        policy=RemediationPolicy(),
        pingpong=pingpong or PingPongDetector(min_volleys=6),
    )
    return handler, host


def _anomaly_event(severity: str) -> EventMessage:
    return EventMessage.from_event(
        AnomalyDetectedEvent(
            team_name="t",
            member_name="m",
            detector="tool_error_rate",
            kind="tool_error_rate",
            severity=severity,
            summary="5 failures",
            evidence={},
        )
    )


@pytest.mark.asyncio
async def test_handler_reports_medium_anomaly_to_leader():
    handler, host = _make_handler()
    await handler.on_anomaly_detected(_anomaly_event("medium"))
    assert len(host.delivered) == 1
    assert "m" in host.delivered[0]


@pytest.mark.asyncio
async def test_handler_escalates_critical_to_user():
    handler, host = _make_handler()
    await handler.on_anomaly_detected(_anomaly_event("critical"))
    assert len(host.delivered) == 1


@pytest.mark.asyncio
async def test_handler_ignores_anomaly_when_not_leader():
    handler, host = _make_handler(role=TeamRole.TEAMMATE)
    await handler.on_anomaly_detected(_anomaly_event("high"))
    assert host.delivered == []


@pytest.mark.asyncio
async def test_handler_pingpong_over_messages():
    handler, host = _make_handler(pingpong=PingPongDetector(min_volleys=2))
    first = EventMessage.from_event(MessageEvent(team_name="t", message_id="1", from_member_name="a", to_member_name="b"))
    second = EventMessage.from_event(MessageEvent(team_name="t", message_id="2", from_member_name="b", to_member_name="a"))
    await handler.on_message(first)
    await handler.on_message(second)
    assert len(host.delivered) >= 1


@pytest.mark.asyncio
async def test_handle_local_anomaly_routes_to_leader():
    handler, host = _make_handler()
    anomaly = Anomaly(
        detector="tool_error_rate",
        kind=AnomalyKind.TOOL_ERROR_RATE,
        severity=Severity.MEDIUM,
        member_name="team_leader",
        summary="leader own anomaly",
    )
    await handler.handle_local_anomaly(anomaly)
    assert len(host.delivered) == 1
    assert "leader own anomaly" in host.delivered[0]


@pytest.mark.asyncio
async def test_handle_local_anomaly_ignored_when_not_leader():
    handler, host = _make_handler(role=TeamRole.TEAMMATE)
    anomaly = Anomaly(
        detector="d",
        kind=AnomalyKind.MODEL_ERROR,
        severity=Severity.HIGH,
        member_name="dev-1",
        summary="x",
    )
    await handler.handle_local_anomaly(anomaly)
    assert host.delivered == []
