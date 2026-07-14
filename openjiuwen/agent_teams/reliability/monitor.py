# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-member detector aggregator."""

from __future__ import annotations

from openjiuwen.agent_teams.reliability.anomaly import Anomaly
from openjiuwen.agent_teams.reliability.detectors.base import Detector
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.reporter import AnomalyReporter
from openjiuwen.agent_teams.reliability.signals import Signal
from openjiuwen.core.common.logging import team_logger


class ReliabilityMonitor:
    """Aggregate one member's detectors and route their anomalies by policy.

    Lives in the member's process (attached via ``ReliabilityRail``), so
    detection runs where the signals originate. Each ``feed`` fans the signal
    out to every detector and routes each anomaly: report to the leader (for
    REPORT_LEADER / ESCALATE_USER) or log (for OBSERVE_ONLY). The produced
    anomalies are returned so the rail can apply reversible local steering. A
    misbehaving detector is logged and skipped so one bad detector never
    blocks the others.
    """

    def __init__(self, detectors: list[Detector], reporter: AnomalyReporter, policy: RemediationPolicy) -> None:
        self._detectors = detectors
        self._reporter = reporter
        self._policy = policy

    async def feed(self, signal: Signal) -> list[Anomaly]:
        """Run every detector against the signal; route and return anomalies."""
        produced: list[Anomaly] = []
        for detector in self._detectors:
            try:
                anomaly = detector.observe(signal)
            except Exception:
                team_logger.error("reliability detector %s raised on observe", detector.name, exc_info=True)
                continue
            if anomaly is None:
                continue
            produced.append(anomaly)
            await self._route(anomaly)
        return produced

    async def _route(self, anomaly: Anomaly) -> None:
        """Report to the leader, or log when the policy says observe-only."""
        actions = self._policy.actions_for(anomaly.severity)
        if RemediationAction.REPORT_LEADER in actions or RemediationAction.ESCALATE_USER in actions:
            await self._reporter.report(anomaly)
        elif RemediationAction.OBSERVE_ONLY in actions:
            team_logger.info(
                "reliability observe-only: member=%s kind=%s %s",
                anomaly.member_name,
                anomaly.kind.value,
                anomaly.summary,
            )

    def reset(self) -> None:
        """Reset all detectors for a new round."""
        for detector in self._detectors:
            detector.reset()
