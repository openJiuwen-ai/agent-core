# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the remediation policy and the local auto-remediator."""

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy


def _anomaly(severity: Severity) -> Anomaly:
    return Anomaly(detector="d", kind=AnomalyKind.TOOL_CALL_LOOP, severity=severity, member_name="m", summary="s")


def test_policy_default_tiers():
    policy = RemediationPolicy()
    assert policy.actions_for(Severity.LOW) == [RemediationAction.OBSERVE_ONLY]
    assert RemediationAction.REPORT_LEADER in policy.actions_for(Severity.MEDIUM)
    assert RemediationAction.LOCAL_STEER in policy.actions_for(Severity.HIGH)
    assert RemediationAction.ESCALATE_USER in policy.actions_for(Severity.CRITICAL)


def test_policy_custom_override():
    policy = RemediationPolicy({Severity.LOW: [RemediationAction.REPORT_LEADER]})
    assert policy.actions_for(Severity.LOW) == [RemediationAction.REPORT_LEADER]
    assert policy.actions_for(Severity.HIGH) == []


def test_local_remediator_steers_high():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=5, period_seconds=60.0, now=lambda: 0.0)
    assert auto.steer_message(_anomaly(Severity.HIGH)) is not None


def test_local_remediator_skips_medium():
    auto = LocalAutoRemediator(RemediationPolicy(), now=lambda: 0.0)
    assert auto.steer_message(_anomaly(Severity.MEDIUM)) is None


def test_local_remediator_restart_intensity_budget():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=3, period_seconds=60.0, now=lambda: 0.0)
    results = [auto.steer_message(_anomaly(Severity.HIGH)) for _ in range(5)]
    fired = [r for r in results if r is not None]
    assert len(fired) == 3
