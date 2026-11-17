# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Wire the existing runtime detectors into standalone evaluation agents."""

from openjiuwen.agent_teams.reliability.config import ReliabilityConfig
from openjiuwen.agent_teams.reliability.anomaly import AnomalyKind
from openjiuwen.agent_teams.reliability.factory import (
    build_reliability_components,
    build_remediation_policy,
    reliability_rail_from_components,
)
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator


class _StandaloneRemediator(LocalAutoRemediator):
    def steer_message(self, anomaly):
        message = super().steer_message(anomaly)
        if message and anomaly.kind == AnomalyKind.OUTPUT_TOO_LONG and anomaly.evidence.get("truncated_without_action"):
            return (
                "[reliability] Your last output was truncated, not a completed answer. "
                "Do not repeat it. Use one available tool for the next bounded action, "
                "or return a concise final answer if the task is already verified complete."
            )
        return message


def build_single_agent_reliability_rail() -> ReliabilityRail:
    """Fresh state per case, identical for source and candidate evaluations."""
    config = ReliabilityConfig.model_validate(
        {
            "enabled": True,
            "detectors": {
                "tool_error": {"enabled": False},
                "repeat_tool": {
                    "history_size": 36,
                    "repeat_warn": 3,
                    "pingpong_warn": 6,
                    "loop_block": 6,
                    "global_stop": 12,
                },
                "model_error": {"enabled": False},
                "output_length": {"enabled": True},
                "compaction": {"enabled": False},
                "pingpong": {"enabled": False},
            },
            "policy": {
                "severity_actions": {
                    level: ["local_steer", "observe_only"] for level in ("low", "medium", "high", "critical")
                }
            },
        }
    )
    components = build_reliability_components(
        config,
        member_name="task_agent",
        messager=None,
        team_name="",
        sender_id="task_agent",
        is_leader=True,
    )
    components.auto_remediator = _StandaloneRemediator(
        build_remediation_policy(config),
        intensity=config.restart_intensity.intensity,
        period_seconds=config.restart_intensity.period_seconds,
    )
    return reliability_rail_from_components(components)
