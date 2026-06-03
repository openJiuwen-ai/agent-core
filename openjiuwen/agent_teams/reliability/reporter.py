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

from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

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


class LocalAnomalyReporter:
    """In-process anomaly sink for leader self-monitoring.

    The leader's own anomalies must not be published — its messager
    self-filter (``kernel`` drops events whose ``sender_id`` equals the local
    member) would discard them. Instead this reporter routes them straight to a
    local sink (the leader's ``ReliabilityHandler``), bound once after the
    dispatcher is built (see ``TeamAgent._register_reliability_local_sink``).
    Until bound it is a no-op so anomalies emitted before wiring are dropped
    with a warning rather than lost silently.
    """

    def __init__(self) -> None:
        self._sink: Callable[[Anomaly], Awaitable[None]] | None = None

    def bind(self, sink: Callable[[Anomaly], Awaitable[None]]) -> None:
        """Bind the local sink that routes anomalies to the handler."""
        self._sink = sink

    async def report(self, anomaly: Anomaly) -> None:
        """Route the anomaly to the bound local sink, if any."""
        if self._sink is None:
            team_logger.warning("local anomaly sink not bound; dropping anomaly from %s", anomaly.member_name)
            return
        await self._sink(anomaly)
