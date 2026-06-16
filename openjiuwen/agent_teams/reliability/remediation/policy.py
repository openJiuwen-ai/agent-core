# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Severity-to-action remediation policy.

The default is tiered and leader-first: LOW is observed only, MEDIUM is
reported to the leader (LLM decides), HIGH adds reversible local self-steering
before reporting, and CRITICAL self-steers then escalates to the user.
"""

from __future__ import annotations

from openjiuwen.agent_teams.reliability.anomaly import Severity
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction

DEFAULT_SEVERITY_ACTIONS: dict[Severity, list[RemediationAction]] = {
    Severity.LOW: [RemediationAction.OBSERVE_ONLY],
    Severity.MEDIUM: [RemediationAction.REPORT_LEADER],
    Severity.HIGH: [RemediationAction.LOCAL_STEER, RemediationAction.REPORT_LEADER],
    Severity.CRITICAL: [RemediationAction.LOCAL_STEER, RemediationAction.ESCALATE_USER],
}


class RemediationPolicy:
    """Map anomaly severity to the ordered remediation actions to apply."""

    def __init__(self, severity_actions: dict[Severity, list[RemediationAction]] | None = None) -> None:
        self._map = dict(severity_actions) if severity_actions else dict(DEFAULT_SEVERITY_ACTIONS)

    def actions_for(self, severity: Severity) -> list[RemediationAction]:
        """Return the remediation actions configured for ``severity``."""
        return self._map.get(severity, [])
