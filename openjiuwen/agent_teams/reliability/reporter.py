# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Anomaly reporter: bridges detection to the remediation pipeline.

``AnomalyReporter`` is the seam between the (per-member) monitor and the
(leader-process) remediation handler. ``EventAnomalyReporter`` publishes each
anomaly as an ``AnomalyDetectedEvent`` on the team's TEAM topic, so the
leader's coordination loop consumes it like any other team event — the same
cross-process path member/task events already use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.reliability.anomaly import Anomaly
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage, TeamTopic
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager.messager import Messager


class AnomalyReporter(Protocol):
    """Forwards a detected anomaly into the remediation pipeline."""

    async def report(self, anomaly: Anomaly) -> None:
        """Report a detected anomaly."""
        ...


class EventAnomalyReporter:
    """Publish anomalies as ``AnomalyDetectedEvent`` on the team TEAM topic.

    ``sender_id`` is this member's messager node id; it lets the leader's
    self-message filter ignore the leader's own publications. Member anomalies
    carry the member's node id, so they reach the leader normally.
    """

    def __init__(self, *, messager: "Messager", team_name: str, sender_id: str) -> None:
        self._messager = messager
        self._team_name = team_name
        self._sender_id = sender_id

    async def report(self, anomaly: Anomaly) -> None:
        """Wrap the anomaly into an event and publish it to the team topic."""
        event = AnomalyDetectedEvent(
            team_name=self._team_name,
            member_name=anomaly.member_name,
            detector=anomaly.detector,
            kind=anomaly.kind.value,
            severity=anomaly.severity.value,
            summary=anomaly.summary,
            evidence=anomaly.evidence,
            peer_member=anomaly.peer_member,
        )
        message = EventMessage.from_event(event)
        message.sender_id = self._sender_id
        try:
            await self._messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self._team_name),
                message=message,
            )
        except Exception:
            team_logger.error("failed to publish anomaly from member %s", anomaly.member_name, exc_info=True)
