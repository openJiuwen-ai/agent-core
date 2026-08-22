# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-agent Agent RAS configuration (single-Agent)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from openjiuwen.harness.agent_ras.models import AnomalyKind, Severity
from openjiuwen.harness.agent_ras.recovery.engine import (
    DEFAULT_SEVERITY_ACTIONS,
    RecoveryAction,
)


class RepeatToolConfig(BaseModel):
    """Thresholds for repeat / loop tool-call detection."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    warning_threshold: int = Field(default=5, ge=2)
    critical_threshold: int = Field(default=10, ge=2)
    global_breaker_threshold: int = Field(default=10, ge=2)
    unknown_tool_threshold: int = Field(default=10, ge=2)

    @property
    def history_size(self) -> int:
        return max(
            4 * self.critical_threshold,
            2 * self.global_breaker_threshold,
            2 * self.unknown_tool_threshold,
        )


class LlmThinkingLoopConfig(BaseModel):
    """Thresholds for LLM thinking-loop detection.

    Character-based gates (``len(text)``), not word counts:

    - ``detection_start_chars``: no L1/L2/L3 until cumulative stream length
      reaches this threshold
    - ``text_repetition`` (L1/L2): after start, scan every ``window_max_chars``
      on a near-window of the same size (FIFO trim + scan gate + min length)
    - ``plan_execution`` (L3): after start, semantic skill every
      ``semantic_eval_chars`` incremental chars since last eval
    - Detection / recovery skill names are bound by fault-domain registry
      (``agents.base``), not host-configurable.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    detection_start_chars: int = Field(
        default=30000, ge=1,
        description="Cumulative chars before any L1/L2/L3 detection runs",
    )
    window_max_chars: int = Field(
        default=2000, ge=100,
        description="L1/L2 near-window size, scan interval, and min detect length",
    )
    loop_repeat_threshold: int = Field(default=5, ge=2)
    similar_clause_sim_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    semantic_eval_chars: int = Field(
        default=10000, ge=1,
        description="Incremental chars since last L3 eval before next detection",
    )
    semantic_content_enabled: bool = True


class DetectorsConfig(BaseModel):
    """Toggle + thresholds for every detector."""

    model_config = ConfigDict(extra="forbid")

    repeat_tool: RepeatToolConfig = Field(default_factory=RepeatToolConfig)
    llm_thinking_loop: LlmThinkingLoopConfig = Field(
        default_factory=LlmThinkingLoopConfig,
    )


class RecoveryConfig(BaseModel):
    """Recovery-side knobs (parallel to ``detectors``)."""

    model_config = ConfigDict(extra="forbid")

    # LOW 警告时是否向用户推送可见 notice（steering 始终按 policy 执行）
    notify_user_on_warning: bool = True


class RecoveryPolicyConfig(BaseModel):
    """Severity-to-action mapping; optional per-kind overrides."""

    model_config = ConfigDict(extra="forbid")

    severity_actions: dict[Severity, list[RecoveryAction]] = Field(
        default_factory=lambda: dict(DEFAULT_SEVERITY_ACTIONS),
    )
    kind_overrides: dict[AnomalyKind, list[RecoveryAction]] = Field(
        default_factory=dict,
        description="Optional per-AnomalyKind action overrides (empty = use severity only)",
    )


def coerce_message_locale(language: str | None) -> str:
    """Map DeepAgent / prompt language to Agent RAS message locale (cn/en).

    Not a config field — mirrors how SecurityRail / SkillUseRail read
    ``system_prompt_builder.language`` at runtime.
    """
    loc = (language or "cn").strip().lower()
    if loc in ("en", "english"):
        return "en"
    if loc in ("zh", "zh-cn", "zhcn", "chinese", "cn"):
        return "cn"
    return "cn"


class AgentRASConfig(BaseModel):
    """Per-agent Agent RAS framework configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    detectors: DetectorsConfig = Field(default_factory=DetectorsConfig)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    policy: RecoveryPolicyConfig = Field(default_factory=RecoveryPolicyConfig)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "AgentRASConfig":
        repeat = self.detectors.repeat_tool
        if repeat.critical_threshold < repeat.warning_threshold:
            raise ValueError("critical_threshold must be >= warning_threshold")
        return self
