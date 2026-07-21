# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recovery engine: kind→ops mapping, rate limiting, and effect dispatch.

``RecoveryPolicy`` maps anomalies to atomic ``RecoveryAction`` sets.
``RecoveryExecutor`` turns those ops into stream suppress / notices / steering
/ terminate via ``operations``. User-facing copy comes from
``robustness_prompt`` (steer/notice/critical text).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.agent_ras.models import Anomaly, AnomalyKind, Severity
from openjiuwen.harness.agent_ras.recovery.robustness_prompt import (
    critical_text_for,
    generic_steer_text_for,
    recovery_user_notice_for_anomaly,
    steer_text_for,
    user_notice_text_for,
)
from openjiuwen.harness.agent_ras.recovery.state import SuppressFlushState
from openjiuwen.harness.agent_ras.recovery.operations import (
    emit_user_notice,
    inject_steering,
    pending_from_anomaly,
    terminate,
    truncate_chunk_on_hit,
)

# Per-invoke cap on rate-limited auto-recovery actions (not host-configurable).
_MAX_AUTO_ACTIONS_PER_INVOKE = 5


class RecoveryAction(str, Enum):
    """Atomic recovery operations applied by ``RecoveryExecutor`` / Monitor.

    - ``SUPPRESS_STREAM``: truncate/suppress thinking-loop stream chunks
    - ``INJECT_STEERING`` / ``REPORT_TO_USER`` / ``TERMINATE``: immediate apply
    """

    OBSERVE_ONLY = "observe_only"
    REPORT_TO_USER = "report_to_user"
    INJECT_STEERING = "inject_steering"
    ESCALATE_USER = "escalate_user"
    SUPPRESS_STREAM = "suppress_stream"
    TERMINATE = "terminate"


# Severity fall-back when kind has no explicit override.
DEFAULT_SEVERITY_ACTIONS: dict[Severity, list[RecoveryAction]] = {
    Severity.LOW: [RecoveryAction.OBSERVE_ONLY, RecoveryAction.INJECT_STEERING],
    Severity.MEDIUM: [RecoveryAction.REPORT_TO_USER],
    Severity.HIGH: [RecoveryAction.REPORT_TO_USER, RecoveryAction.INJECT_STEERING],
    Severity.CRITICAL: [RecoveryAction.INJECT_STEERING, RecoveryAction.ESCALATE_USER],
}

# Kind → set of atomic ops (primary mapping source).
DEFAULT_KIND_OVERRIDES: dict[AnomalyKind, list[RecoveryAction]] = {
    AnomalyKind.LLM_THINKING_LOOP: [
        RecoveryAction.OBSERVE_ONLY,
        RecoveryAction.SUPPRESS_STREAM,
    ],
    AnomalyKind.LLM_THINKING_DEAD_LOOP: [
        RecoveryAction.OBSERVE_ONLY,
        RecoveryAction.SUPPRESS_STREAM,
    ],
}

_IMMEDIATE_OPS = frozenset({
    RecoveryAction.INJECT_STEERING,
    RecoveryAction.REPORT_TO_USER,
    RecoveryAction.ESCALATE_USER,
    RecoveryAction.TERMINATE,
})


def needs_immediate_apply(actions: set[RecoveryAction]) -> bool:
    """True when anomaly should run non-stream side effects now."""
    return bool(actions & _IMMEDIATE_OPS)


class RecoveryPolicy:
    """Map anomaly kind (preferred) or severity to atomic recovery ops."""

    def __init__(
        self,
        severity_actions: dict[Severity, list[RecoveryAction]] | None = None,
        kind_overrides: dict[AnomalyKind, list[RecoveryAction]] | None = None,
    ) -> None:
        self._map = dict(severity_actions) if severity_actions else dict(DEFAULT_SEVERITY_ACTIONS)
        self._kind_overrides = dict(kind_overrides) if kind_overrides else {}

    def actions_for(
        self,
        severity: Severity,
        kind: AnomalyKind | None = None,
    ) -> list[RecoveryAction]:
        """Return configured ops for ``kind`` override, else severity defaults."""
        if kind is not None and kind in self._kind_overrides:
            return list(self._kind_overrides[kind])
        return list(self._map.get(severity, []))

    def ops_for(self, anomaly: Anomaly) -> set[RecoveryAction]:
        """Resolve the atomic op set for one anomaly (kind preferred over severity)."""
        return set(self.actions_for(anomaly.severity, kind=anomaly.kind))

    @classmethod
    def from_config(cls, config: Any) -> "RecoveryPolicy":
        """Build policy with default kind overrides merged over config overrides."""
        kind_overrides = dict(DEFAULT_KIND_OVERRIDES)
        kind_overrides.update(getattr(config, "kind_overrides", {}) or {})
        # Drop legacy DEFER_HITL if present in host overrides.
        for kind, actions in list(kind_overrides.items()):
            kind_overrides[kind] = [
                action for action in actions
                if getattr(action, "value", action) != "defer_hitl"
            ]
        return cls(
            getattr(config, "severity_actions", None),
            kind_overrides=kind_overrides,
        )


class LocalAutoRecovery:
    """Per-invoke rate limit for steering (and related) auto-recovery actions."""

    def __init__(
        self,
        policy: RecoveryPolicy,
        *,
        locale: str = "cn",
    ) -> None:
        self._policy = policy
        self._counts: dict[RecoveryAction, int] = {}
        self._locale = (locale or "cn").strip().lower()
        if self._locale == "zh":
            self._locale = "cn"

    def set_locale(self, locale: str) -> None:
        """Update message locale from DeepAgent language (cn/en)."""
        loc = (locale or "cn").strip().lower()
        self._locale = "cn" if loc == "zh" else loc

    def steer_message(self, anomaly: Anomaly) -> str | None:
        """Return a rate-limited steering text from ``robustness_prompt``, or None."""
        if RecoveryAction.INJECT_STEERING not in self._policy.actions_for(
            anomaly.severity, kind=anomaly.kind
        ):
            return None
        if not self.allow(RecoveryAction.INJECT_STEERING):
            return None
        specific = steer_text_for(anomaly, locale=self._locale)
        if specific:
            return specific
        return generic_steer_text_for(anomaly, locale=self._locale)

    def allow(self, action: RecoveryAction) -> bool:
        """Consume one per-invoke budget slot for ``action``; False when exhausted."""
        used = self._counts.get(action, 0)
        if used >= _MAX_AUTO_ACTIONS_PER_INVOKE:
            return False
        self._counts[action] = used + 1
        return True

    def reset(self) -> None:
        """Clear per-invoke counters (call at invoke start)."""
        self._counts.clear()


@dataclass(slots=True)
class StreamRecoveryArgs:
    """Stream chunk view passed to ``RecoveryExecutor.run_stream_recovery``."""

    chunk_result: Any
    chunk_type: str
    chunk_text: str


@dataclass(slots=True)
class RecoveryPlan:
    """Resolved remediation effects for one anomaly."""

    actions: set[RecoveryAction] = field(default_factory=set)
    primary_action: RecoveryAction = RecoveryAction.OBSERVE_ONLY
    steering_text: str | None = None
    user_notice: str | None = None
    recovery_notice: str | None = None
    terminate_message: str | None = None
    terminate_critical: bool = False

    @property
    def has_effect(self) -> bool:
        return bool(
            self.steering_text
            or self.user_notice
            or self.recovery_notice
            or self.terminate_message
            or self.primary_action != RecoveryAction.OBSERVE_ONLY
        )


def should_emit_user_notice(
    anomaly: Anomaly,
    policy_actions: set[RecoveryAction],
    *,
    notify_user_on_warning: bool,
) -> bool:
    """True when policy asks to surface a WARNING-level notice to the user."""
    if not notify_user_on_warning:
        return False
    if RecoveryAction.REPORT_TO_USER in policy_actions:
        return True
    return (
        anomaly.severity == Severity.LOW
        and RecoveryAction.INJECT_STEERING in policy_actions
    )


def plan_recovery(
    anomaly: Anomaly,
    policy: RecoveryPolicy | None,
    auto: LocalAutoRecovery | None,
    *,
    locale: str = "cn",
    notify_user_on_warning: bool = True,
    policy_actions: set[RecoveryAction] | None = None,
) -> RecoveryPlan:
    """Map policy actions + rate-limited steering into a ``RecoveryPlan``.

    Called by ``Monitor.recovery`` / executor before applying side effects.
    Text comes from ``robustness_prompt``.
    """
    loc = (locale or "cn").strip().lower()
    if loc == "zh":
        loc = "cn"
    actions = (
        set(policy_actions)
        if policy_actions is not None
        else set(policy.actions_for(anomaly.severity, kind=anomaly.kind))
        if policy is not None
        else set()
    )
    plan = RecoveryPlan(actions=actions, primary_action=RecoveryAction.OBSERVE_ONLY)

    if should_emit_user_notice(anomaly, actions, notify_user_on_warning=notify_user_on_warning):
        plan.user_notice = user_notice_text_for(anomaly, locale=loc)

    if RecoveryAction.INJECT_STEERING in actions and auto is not None:
        plan.steering_text = auto.steer_message(anomaly)
        if plan.steering_text:
            plan.primary_action = RecoveryAction.INJECT_STEERING

    if RecoveryAction.REPORT_TO_USER in actions or RecoveryAction.ESCALATE_USER in actions:
        if plan.primary_action == RecoveryAction.OBSERVE_ONLY:
            plan.primary_action = RecoveryAction.REPORT_TO_USER

    if (
        not plan.user_notice
        and plan.steering_text
        and notify_user_on_warning
    ):
        plan.recovery_notice = recovery_user_notice_for_anomaly(anomaly, locale=loc)

    if RecoveryAction.SUPPRESS_STREAM in actions:
        if plan.primary_action == RecoveryAction.OBSERVE_ONLY:
            plan.primary_action = RecoveryAction.SUPPRESS_STREAM

    if RecoveryAction.TERMINATE in actions:
        if (
            anomaly.severity == Severity.CRITICAL
            and anomaly.kind in (AnomalyKind.REPEAT_TOOL_CALL, AnomalyKind.TOOL_CALL_LOOP)
        ):
            plan.terminate_message = critical_text_for(anomaly, locale=loc) or (
                f"[agent_ras] {anomaly.kind.value} detected: {anomaly.summary}"
            )
            plan.terminate_critical = True
        else:
            plan.terminate_message = (
                f"[agent_ras] {anomaly.kind.value} detected: {anomaly.summary}"
            )

    return plan


class RecoveryExecutor:
    """Owns suppress/pending state and dispatches ops through ``operations``.

    Stream-phase thinking-loop resolution (L1/L2 immediate abort, L3 recovery)
    is orchestrated by ``AgentRASMonitor``; this class records deferred pending,
    truncates stream chunks, and runs ``apply`` for steering / notices / terminate.
    """

    def __init__(
        self,
        auto_recovery: Optional[LocalAutoRecovery],
        *,
        locale: str = "cn",
        notify_user_on_warning: bool = True,
    ) -> None:
        self._auto = auto_recovery
        self._locale = (locale or "cn").strip().lower()
        if self._locale == "zh":
            self._locale = "cn"
        self._notify_user_on_warning = notify_user_on_warning
        self._suppress_flush = SuppressFlushState()
        self._pending_recovery_notice: str | None = None

    def set_locale(self, locale: str) -> None:
        """Update message locale and push it to LocalAutoRecovery."""
        loc = (locale or "cn").strip().lower()
        self._locale = "cn" if loc == "zh" else loc
        if self._auto is not None:
            self._auto.set_locale(self._locale)

    def schedule_recovery_notice(self, message: str) -> None:
        """Queue a notice to emit before the next model call."""
        self._pending_recovery_notice = message

    def take_pending_recovery_notice(self) -> str | None:
        """Pop and return any queued recovery notice, or None."""
        notice = self._pending_recovery_notice
        self._pending_recovery_notice = None
        return notice

    @property
    def suppress_state(self) -> SuppressFlushState:
        """Session-local suppress buffer and deferred recovery pending."""
        return self._suppress_flush

    def reset(self) -> None:
        """Clear suppress/pending state and auto-recovery counters at invoke start."""
        self._suppress_flush.clear()
        self._pending_recovery_notice = None
        if self._auto is not None:
            self._auto.reset()

    def should_suppress_stream(self, chunk_type: str) -> bool:
        """True while deferred recovery is active for ``chunk_type``."""
        return self._suppress_flush.should_suppress(chunk_type)

    def record_suppressed_chunk(self, chunk_type: str, text: str) -> None:
        """Append truncated stream text for a later flush."""
        self._suppress_flush.record_suppressed(chunk_type, text)

    def record_deferred_recovery(
        self,
        anomaly: Anomaly,
        policy_actions: set[RecoveryAction] | None = None,
    ) -> None:
        """Arm suppress pending from anomaly evidence when stream suppress is allowed."""
        actions = policy_actions or set()
        if RecoveryAction.SUPPRESS_STREAM not in actions:
            return
        self._suppress_flush.record_pending(pending_from_anomaly(anomaly))

    async def run_stream_recovery(
        self,
        ctx: AgentCallbackContext | None,
        anomaly: Anomaly,
        stream: StreamRecoveryArgs,
        policy_actions: set[RecoveryAction],
    ) -> None:
        """Apply stream truncate/suppress and optional immediate ``apply``."""
        if RecoveryAction.SUPPRESS_STREAM in policy_actions:
            evidence = anomaly.evidence or {}
            raw_keep = evidence.get("stream_chunk_keep_len")
            keep_len = int(raw_keep) if raw_keep is not None else 0
            truncate_chunk_on_hit(
                stream.chunk_result,
                stream.chunk_text,
                keep_len=keep_len if keep_len > 0 else None,
            )
            suppressed_part = (
                stream.chunk_text[keep_len:] if keep_len > 0 else stream.chunk_text
            )
            self.record_suppressed_chunk(stream.chunk_type, suppressed_part)
            self.record_deferred_recovery(anomaly, policy_actions)

        if ctx is not None and needs_immediate_apply(policy_actions):
            await self.apply(ctx, anomaly, policy_actions)

    async def apply(
        self,
        ctx: AgentCallbackContext,
        anomaly: Anomaly,
        policy_actions: set[RecoveryAction],
    ) -> None:
        """Emit notices / steering / terminate from ``plan_recovery`` (rate-limited)."""
        plan = plan_recovery(
            anomaly,
            None,
            self._auto,
            locale=self._locale,
            notify_user_on_warning=self._notify_user_on_warning,
            policy_actions=policy_actions,
        )

        notice_emitted = False
        if plan.user_notice and self._allowed(RecoveryAction.REPORT_TO_USER):
            logger.warning(
                "[RecoveryExecutor] user notice (%s/%s): %s",
                anomaly.kind.value,
                anomaly.severity.value,
                plan.user_notice,
            )
            await emit_user_notice(ctx, plan.user_notice)
            notice_emitted = True

        if (
            not notice_emitted
            and plan.recovery_notice
            and self._allowed(RecoveryAction.REPORT_TO_USER)
        ):
            logger.warning(
                "[RecoveryExecutor] recovery notice (%s): %s",
                anomaly.kind.value,
                plan.recovery_notice,
            )
            await emit_user_notice(ctx, plan.recovery_notice)

        if plan.steering_text:
            logger.info(
                "[RecoveryExecutor] steering pushed for %s (%s chars)",
                anomaly.kind.value,
                len(plan.steering_text),
            )
            await inject_steering(ctx, plan.steering_text)

        if plan.terminate_message and self._allowed(RecoveryAction.TERMINATE):
            await terminate(
                ctx,
                plan.terminate_message,
                write_error_stream=plan.terminate_critical,
            )

    def _allowed(self, action: RecoveryAction) -> bool:
        return self._auto is None or self._auto.allow(action)
