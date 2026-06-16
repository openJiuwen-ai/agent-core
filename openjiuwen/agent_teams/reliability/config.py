# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-team reliability configuration.

Hung off ``TeamAgentSpec.reliability`` and opt-in via ``enabled`` (default
False, so existing teams are unaffected). One config per team; per-member
threshold overrides are a future extension.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from openjiuwen.agent_teams.reliability.anomaly import Severity
from openjiuwen.agent_teams.reliability.remediation.action import RemediationAction
from openjiuwen.agent_teams.reliability.remediation.policy import DEFAULT_SEVERITY_ACTIONS


class ToolErrorConfig(BaseModel):
    """Thresholds for tool-call error-rate detection."""

    enabled: bool = True
    window_seconds: float = 60.0
    rate_threshold: int = 5
    consecutive_threshold: int = 3


class RepeatToolConfig(BaseModel):
    """Thresholds for repeat / loop tool-call detection."""

    enabled: bool = True
    history_size: int = 30
    repeat_warn: int = 10
    pingpong_warn: int = 10
    loop_block: int = 20
    global_stop: int = 30


class ModelErrorConfig(BaseModel):
    """Thresholds for model-call error detection."""

    enabled: bool = True
    window_seconds: float = 120.0
    rate_threshold: int = 3
    consecutive_threshold: int = 2


class OutputLengthConfig(BaseModel):
    """Thresholds for over-long output / thinking detection."""

    enabled: bool = True
    text_threshold: int = 32000
    thinking_threshold: int = 16000


class CompactionConfig(BaseModel):
    """Thresholds for frequent-compaction inference."""

    enabled: bool = True
    window_seconds: float = 300.0
    frequency_threshold: int = 3
    drop_ratio: float = 0.3


class PingPongConfig(BaseModel):
    """Threshold for team-level ping-pong detection."""

    enabled: bool = True
    min_volleys: int = 6


class DetectorsConfig(BaseModel):
    """Toggle + thresholds for every detector."""

    tool_error: ToolErrorConfig = Field(default_factory=ToolErrorConfig)
    repeat_tool: RepeatToolConfig = Field(default_factory=RepeatToolConfig)
    model_error: ModelErrorConfig = Field(default_factory=ModelErrorConfig)
    output_length: OutputLengthConfig = Field(default_factory=OutputLengthConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    pingpong: PingPongConfig = Field(default_factory=PingPongConfig)


class RestartIntensityConfig(BaseModel):
    """Budget for automated local remediation (actions per period)."""

    intensity: int = 5
    period_seconds: float = 60.0


class RemediationPolicyConfig(BaseModel):
    """Severity-to-action mapping; defaults to the tiered leader-first policy."""

    severity_actions: dict[Severity, list[RemediationAction]] = Field(
        default_factory=lambda: dict(DEFAULT_SEVERITY_ACTIONS),
    )


class ReliabilityConfig(BaseModel):
    """Per-team reliability framework configuration.

    Opt-in: ``enabled`` defaults to False. ``monitor_roles`` selects which
    roles get a ReliabilityRail attached (default: leader + teammates). The
    leader self-monitors via an in-process local sink that bypasses the
    messager self-filter (see ``LocalAnomalyReporter`` and
    ``TeamAgent._register_reliability_local_sink``).
    """

    enabled: bool = False
    monitor_roles: list[str] = Field(default_factory=lambda: ["leader", "teammate"])
    detectors: DetectorsConfig = Field(default_factory=DetectorsConfig)
    policy: RemediationPolicyConfig = Field(default_factory=RemediationPolicyConfig)
    restart_intensity: RestartIntensityConfig = Field(default_factory=RestartIntensityConfig)
