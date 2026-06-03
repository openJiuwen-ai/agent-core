# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-side reliability coordination handler.

Consumes the cross-process anomaly stream on the leader and routes each
anomaly per policy into the leader's own loop, so the leader LLM decides the
response with existing team tools (send message / cancel task / stop member /
spawn). Also runs the team-level ping-pong detector over message events, which
no per-member rail can observe. Mirrors ``StaleTaskHandler``: a coordination
handler that reads state and nudges via narrow protocols.
"""

from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, Severity
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage, MessageEvent, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class ReliabilityHandler(BaseCoordinationHandler):
    """Route reported anomalies + detect team-level ping-pong (leader only)."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.ANOMALY_DETECTED: "on_anomaly_detected",
        TeamEvent.MESSAGE: "on_message",
        TeamEvent.BROADCAST: "on_message",
    }

    def __init__(
        self,
        host,
        blueprint,
        infra,
        poll_ctrl,
        *,
        policy: RemediationPolicy,
        pingpong: PingPongDetector,
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        self._policy = policy
        self._pingpong = pingpong

    async def on_anomaly_detected(self, event: EventMessage) -> None:
        """Route a reported anomaly per policy into the leader loop."""
        if self._blueprint.role != TeamRole.LEADER:
            return
        payload = event.get_payload()
        if not isinstance(payload, AnomalyDetectedEvent):
            return
        team_logger.info(
            "reliability anomaly: member=%s kind=%s severity=%s",
            payload.member_name,
            payload.kind,
            payload.severity,
        )
        await self._route(Severity(payload.severity), self._format(payload))

    async def on_message(self, event: EventMessage) -> None:
        """Run team-level ping-pong detection over message events (leader only)."""
        if self._blueprint.role != TeamRole.LEADER:
            return
        payload = event.get_payload()
        if not isinstance(payload, MessageEvent):
            return
        anomaly = self._pingpong.observe(
            Signal(
                kind=SignalKind.MESSAGE,
                member_name=payload.from_member_name,
                peer_member=payload.to_member_name,
            )
        )
        if anomaly is not None:
            await self._route(anomaly.severity, anomaly.summary)

    async def _route(self, severity: Severity, summary: str) -> None:
        """Deliver a report or escalation into the leader's own loop."""
        actions = self._policy.actions_for(severity)
        if RemediationAction.ESCALATE_USER in actions:
            await self._round.deliver_input(t("reliability.escalate_user", summary=summary))
        elif RemediationAction.REPORT_LEADER in actions:
            await self._round.deliver_input(t("reliability.report_leader", summary=summary))

    async def handle_local_anomaly(self, anomaly: Anomaly) -> None:
        """Route a leader-local anomaly straight into the leader loop.

        Used by leader self-monitoring: the leader's own rail feeds anomalies
        here in-process (bypassing the messager self-filter), and they go
        through the same policy routing as cross-process anomaly events.
        """
        if self._blueprint.role != TeamRole.LEADER:
            return
        await self._route(anomaly.severity, self._format_anomaly(anomaly))

    @staticmethod
    def _format(payload: AnomalyDetectedEvent) -> str:
        """Render an anomaly event into a one-line summary for the leader."""
        return f"[{payload.severity}] {payload.member_name}: {payload.summary} (detector={payload.detector})"

    @staticmethod
    def _format_anomaly(anomaly: Anomaly) -> str:
        """Render a local anomaly into a one-line summary for the leader."""
        return f"[{anomaly.severity.value}] {anomaly.member_name}: {anomaly.summary} (detector={anomaly.detector})"
