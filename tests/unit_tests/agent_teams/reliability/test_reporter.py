# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for EventAnomalyReporter and AnomalyDetectedEvent serialization."""

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.reporter import EventAnomalyReporter
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage


class _RecordingMessager:
    """Messager stub that records published (topic, message) pairs."""

    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, topic_id: str, message) -> None:
        self.published.append((topic_id, message))


@pytest.mark.asyncio
async def test_reporter_publishes_anomaly_event():
    token = set_session_id("s1")
    try:
        messager = _RecordingMessager()
        reporter = EventAnomalyReporter(messager=messager, team_name="t1", sender_id="node-x")
        anomaly = Anomaly(
            detector="tool_error_rate",
            kind=AnomalyKind.TOOL_ERROR_RATE,
            severity=Severity.HIGH,
            member_name="m1",
            summary="3 failures",
            evidence={"consecutive": 3},
        )
        await reporter.report(anomaly)
    finally:
        reset_session_id(token)
    assert len(messager.published) == 1
    topic, message = messager.published[0]
    assert "t1" in topic
    assert message.sender_id == "node-x"
    payload = message.get_payload()
    assert isinstance(payload, AnomalyDetectedEvent)
    assert payload.kind == "tool_error_rate"
    assert payload.severity == "high"
    assert payload.member_name == "m1"
    assert payload.evidence == {"consecutive": 3}


def test_anomaly_event_round_trip():
    event = AnomalyDetectedEvent(
        team_name="t",
        member_name="m",
        detector="ping_pong",
        kind="ping_pong",
        severity="medium",
        summary="volleys",
        evidence={"volleys": 6},
        peer_member="other",
    )
    wrapped = EventMessage.from_event(event)
    restored = EventMessage.deserialize(wrapped.serialize())
    payload = restored.get_payload()
    assert isinstance(payload, AnomalyDetectedEvent)
    assert payload.kind == "ping_pong"
    assert payload.peer_member == "other"
    assert payload.evidence == {"volleys": 6}
