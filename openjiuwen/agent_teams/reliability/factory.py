# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assembly helpers: build detectors, the member rail, and handler deps from config."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from openjiuwen.agent_teams.reliability.config import DetectorsConfig, ReliabilityConfig
from openjiuwen.agent_teams.reliability.detectors.base import Detector
from openjiuwen.agent_teams.reliability.detectors.compaction import FrequentCompactionDetector
from openjiuwen.agent_teams.reliability.detectors.model_error import ModelStreamErrorDetector
from openjiuwen.agent_teams.reliability.detectors.output_length import OutputLengthDetector
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.detectors.repeat_tool import RepeatToolCallDetector
from openjiuwen.agent_teams.reliability.detectors.tool_error import ToolErrorRateDetector
from openjiuwen.agent_teams.reliability.monitor import ReliabilityMonitor
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.reporter import EventAnomalyReporter, LocalAnomalyReporter

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager.messager import Messager


def build_member_detectors(config: DetectorsConfig, now: Callable[[], float] = time.time) -> list[Detector]:
    """Build the member-internal detectors (team-level ping-pong is excluded)."""
    detectors: list[Detector] = []
    if config.tool_error.enabled:
        detectors.append(
            ToolErrorRateDetector(
                window_seconds=config.tool_error.window_seconds,
                rate_threshold=config.tool_error.rate_threshold,
                consecutive_threshold=config.tool_error.consecutive_threshold,
                now=now,
            )
        )
    if config.repeat_tool.enabled:
        detectors.append(
            RepeatToolCallDetector(
                history_size=config.repeat_tool.history_size,
                repeat_warn=config.repeat_tool.repeat_warn,
                pingpong_warn=config.repeat_tool.pingpong_warn,
                loop_block=config.repeat_tool.loop_block,
                global_stop=config.repeat_tool.global_stop,
            )
        )
    if config.model_error.enabled:
        detectors.append(
            ModelStreamErrorDetector(
                window_seconds=config.model_error.window_seconds,
                rate_threshold=config.model_error.rate_threshold,
                consecutive_threshold=config.model_error.consecutive_threshold,
                now=now,
            )
        )
    if config.output_length.enabled:
        detectors.append(
            OutputLengthDetector(
                text_threshold=config.output_length.text_threshold,
                thinking_threshold=config.output_length.thinking_threshold,
            )
        )
    if config.compaction.enabled:
        detectors.append(
            FrequentCompactionDetector(
                window_seconds=config.compaction.window_seconds,
                frequency_threshold=config.compaction.frequency_threshold,
                drop_ratio=config.compaction.drop_ratio,
                now=now,
            )
        )
    return detectors


def build_remediation_policy(config: ReliabilityConfig) -> RemediationPolicy:
    """Build the remediation policy from config."""
    return RemediationPolicy(config.policy.severity_actions)


def build_reliability_rail(
    config: ReliabilityConfig,
    *,
    member_name: str,
    messager: "Messager | None",
    team_name: str,
    sender_id: str,
    is_leader: bool = False,
) -> ReliabilityRail:
    """Assemble the per-member reliability rail (detectors + reporter + local remediator).

    The leader gets a ``LocalAnomalyReporter`` — its own anomalies route
    in-process, bypassing the messager self-filter — while other roles get an
    ``EventAnomalyReporter`` that publishes across processes to the leader.
    """
    policy = build_remediation_policy(config)
    detectors = build_member_detectors(config.detectors)
    local_reporter = LocalAnomalyReporter() if is_leader else None
    reporter = local_reporter or EventAnomalyReporter(messager=messager, team_name=team_name, sender_id=sender_id)
    monitor = ReliabilityMonitor(detectors, reporter, policy)
    auto = LocalAutoRemediator(
        policy,
        intensity=config.restart_intensity.intensity,
        period_seconds=config.restart_intensity.period_seconds,
    )
    return ReliabilityRail(
        monitor=monitor,
        member_name=member_name,
        auto_remediator=auto,
        local_reporter=local_reporter,
    )


def build_pingpong_detector(config: ReliabilityConfig) -> PingPongDetector:
    """Build the team-level ping-pong detector for the leader handler."""
    return PingPongDetector(min_volleys=config.detectors.pingpong.min_volleys)
