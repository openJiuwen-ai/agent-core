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

from typing import Any, ClassVar

from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage, MessageEvent, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


def _route_notice_key(evidence: dict[str, Any] | None) -> tuple[Any, Any, Any, Any] | None:
    """Identify what a route notice reports: which router, the failed deployment and why.

    Members sharing a model-group router see the same failure, so the key
    leaves the member out. Members pinned to one model run their own router
    (``pinned_model``), which switches on its own, so the pin is part of the
    key. ``failures[0]`` is the deployment that raised.
    """
    verdict = _route_verdict(evidence)
    if verdict is None:
        return None
    failures = [f for f in verdict.get("failures") or [] if isinstance(f, dict)]
    if not failures:
        return None
    trigger = failures[0]
    return verdict.get("pinned_model"), trigger.get("deployment_id"), verdict.get("reason"), trigger.get("status_code")


def _route_verdict(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    verdict = (evidence or {}).get("model_failure")
    return verdict if isinstance(verdict, dict) else None


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
        # Route notices already delivered, keyed by ``_route_notice_key``.
        self._noticed: set[tuple[Any, Any, Any, Any]] = set()

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
        if payload.kind == AnomalyKind.MODEL_ROUTE_NOTICE.value:
            await self._deliver_route_notice(payload.summary, payload.evidence)
            return
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

    async def _deliver_route_notice(self, summary: str, evidence: dict[str, Any] | None) -> None:
        """Tell the leader about a deployment switch once per router, deployment and reason.

        The switch is a property of the model-group router, not of the member
        whose call happened to hit it, so the notice names no member and a
        second member reporting the same failure is dropped. The notice says
        who the router serves: every member not pinned to a model, or only the
        members pinned to ``pinned_model``.
        """
        key = _route_notice_key(evidence)
        if key is not None:
            if key in self._noticed:
                return
            self._noticed.add(key)
        pinned = (_route_verdict(evidence) or {}).get("pinned_model")
        scope = (
            t("reliability.route_notice_scope_pinned", model=pinned)
            if pinned
            else t("reliability.route_notice_scope_group")
        )
        await self._round.deliver_input(t("reliability.route_notice", summary=summary, scope=scope))

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

        A route notice about the leader's own call is delivered like a
        member's: the router has already switched to a working model, so the
        leader round it starts does not fail again. An unavailable verdict is
        only logged: delivering it would start a leader round on the same
        failing model, which fails and reports again. The failed round already
        shows the user a readable error.
        """
        if self._blueprint.role != TeamRole.LEADER:
            return
        if anomaly.kind == AnomalyKind.MODEL_ROUTE_NOTICE:
            await self._deliver_route_notice(anomaly.summary, anomaly.evidence)
            return
        if anomaly.kind == AnomalyKind.MODEL_UNAVAILABLE:
            team_logger.warning("leader model anomaly (not delivered to leader): %s", anomaly.summary)
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
