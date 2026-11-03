# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-call (including streaming) error detector."""

from __future__ import annotations

import time
from typing import Any, Callable

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.base import ErrorBurstDetector
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind

# ``route_case`` values a model-group client attaches to its errors
# (``core/foundation/llm/routing/intelli_router_errors.py``).
_ROUTE_CASE_SWITCHABLE = "switchable"
_ROUTE_CASE_REQUEST_REJECTED = "exhausted_request_rejected"
_REASON_STREAM_INTERRUPTED = "stream_interrupted"
# Switch reasons worth telling the leader about (a transient switch is not).
_NOTICE_REASONS = {"deterministic", _REASON_STREAM_INTERRUPTED}


def _model_label(entry: dict[str, Any]) -> str:
    """Render a ``failures`` / ``usable`` entry as "#N name (provider, endpoint)"."""
    name = str(entry.get("model_name") or "*")
    where = ", ".join(str(part) for part in (entry.get("provider"), entry.get("endpoint")) if part)
    position = entry.get("position")
    if position:
        return t("reliability.model_label_ranked", position=position, name=name, where=where)
    return t("reliability.model_label", name=name, where=where)


def _reason_text(failure: dict[str, Any]) -> str:
    """Localized reason of one ``failures`` entry.

    Built from the error type and status code rather than the client's
    English ``message``, so the leader relays it in the team language.
    """
    error_type = str(failure.get("error_type") or "")
    status = failure.get("status_code")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    if not error_type and status is None:
        reason = t("reliability.reason_unavailable")
    elif error_type == "DeploymentTimeoutError":
        reason = t("reliability.reason_timeout")
    elif error_type == "DeploymentNetworkError":
        reason = t("reliability.reason_unreachable")
    elif error_type == "DeploymentRateLimitError" or status == 429:
        reason = t("reliability.reason_rate_limited")
    elif status in (401, 403):
        reason = t("reliability.reason_api_key", status=status)
    elif status == 404:
        reason = t("reliability.reason_not_found")
    elif status is not None and status >= 500:
        reason = t("reliability.reason_server_error", status=status)
    elif status is not None and 400 <= status < 500:
        # The client appends the provider's own explanation after ": ".
        message = str(failure.get("message") or "")
        detail = message.split(": ", 1)[1] if ": " in message else ""
        reason = (
            t("reliability.reason_rejected_detail", status=status, detail=detail)
            if detail and failure.get("category") != _REASON_STREAM_INTERRUPTED
            else t("reliability.reason_rejected", status=status)
        )
    else:
        reason = t("reliability.reason_failed")
    if failure.get("category") == _REASON_STREAM_INTERRUPTED:
        return t("reliability.reason_interrupted", reason=reason)
    return reason


class ModelStreamErrorDetector(ErrorBurstDetector):
    """Detect frequent or consecutive model-call errors.

    There is no per-chunk streaming hook, so this operates at model-call
    granularity: a MODEL_EXCEPTION raised by the call, or an error surfaced on
    AFTER_MODEL_CALL that the rail re-emits as a MODEL_EXCEPTION signal.
    Mid-stream single-chunk errors are not observable here. An
    AFTER_MODEL_CALL (success) clears the consecutive streak.

    Errors carrying a model-group routing verdict (``Signal.model_failure``)
    bypass the burst counting: a verdict that needs a human escalates at once
    (CRITICAL, once until a call succeeds), a switch to another deployment is
    reported once per deployment and reason (MEDIUM notice), and a pending
    retry after cooldown stays silent.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 120.0,
        rate_threshold: int = 3,
        consecutive_threshold: int = 2,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            name="model_error",
            error_kind=SignalKind.MODEL_EXCEPTION,
            reset_kind=SignalKind.AFTER_MODEL_CALL,
            anomaly_kind=AnomalyKind.MODEL_ERROR,
            window_seconds=window_seconds,
            rate_threshold=rate_threshold,
            consecutive_threshold=consecutive_threshold,
            now=now,
        )
        # (deployment_id, reason, status_code) already reported as a notice.
        self._noticed: set[tuple[Any, Any, Any]] = set()

    def observe(self, signal: Signal) -> Anomaly | None:
        """Route model-group verdicts; count every other model error as before."""
        verdict = signal.model_failure if signal.kind == SignalKind.MODEL_EXCEPTION else None
        if not verdict:
            return super().observe(signal)
        if verdict.get("user_action_required"):
            return self._unavailable(signal, verdict)
        route_case = verdict.get("route_case")
        if route_case == _ROUTE_CASE_SWITCHABLE:
            return self._notice(signal, verdict)
        if route_case == _ROUTE_CASE_REQUEST_REJECTED:
            # The request itself was refused (e.g. too long): count it like
            # any other model error; context recovery may still fix it.
            return super().observe(signal)
        # exhausted_retryable: the retry rail waits for the cooldown and retries.
        return None

    def _unavailable(self, signal: Signal, verdict: dict[str, Any]) -> Anomaly | None:
        """Escalate once until a model call succeeds (shares the burst level)."""
        if self._fired_severity is not None and self._fired_severity.rank >= Severity.CRITICAL.rank:
            return None
        self._fired_severity = Severity.CRITICAL
        failures = [f for f in verdict.get("failures") or [] if isinstance(f, dict)]
        separator = t("reliability.model_failure_separator")
        reason = separator.join(
            t("reliability.model_failure", model=_model_label(f), reason=_reason_text(f)) for f in failures
        )
        reason = reason or t("reliability.model_not_in_group")
        no_substitute = t(
            "reliability.model_no_substitute_pinned" if verdict.get("pinned_model")
            else "reliability.model_no_substitute_group"
        )
        model = verdict.get("pinned_model") or ", ".join(
            sorted({str(f.get("model_name")) for f in failures if f.get("model_name")})
        )
        return Anomaly(
            detector=self.name,
            kind=AnomalyKind.MODEL_UNAVAILABLE,
            severity=Severity.CRITICAL,
            member_name=signal.member_name,
            summary=t(
                "reliability.model_unavailable", model=model or "*", reason=reason,
                no_substitute=no_substitute,
            ),
            evidence={"model_failure": verdict, "last_error": signal.error},
        )

    def _notice(self, signal: Signal, verdict: dict[str, Any]) -> Anomaly | None:
        """Report a switch away from a failing deployment, once per deployment and reason.

        Only switches caused by a deterministic error or an interrupted stream
        are reported; a transient failure the router is already working around
        needs no attention. ``failures[0]`` is the deployment that raised.
        """
        reason = verdict.get("reason")
        if reason not in _NOTICE_REASONS:
            return None
        failures = [f for f in verdict.get("failures") or [] if isinstance(f, dict)]
        if not failures:
            return None
        trigger = failures[0]
        key = (trigger.get("deployment_id"), reason, trigger.get("status_code"))
        if key in self._noticed:
            return None
        self._noticed.add(key)
        usable = [u for u in verdict.get("usable") or [] if isinstance(u, dict)]
        # Group order: the deployment the router takes next.
        alternatives = _model_label(usable[0]) if usable else t("reliability.model_route_other")
        template = (
            "reliability.model_route_interrupted"
            if reason == _REASON_STREAM_INTERRUPTED
            else "reliability.model_route_switched"
        )
        summary = t(
            template, model=_model_label(trigger), reason=_reason_text(trigger),
            alternatives=alternatives,
        )
        return Anomaly(
            detector=self.name,
            kind=AnomalyKind.MODEL_ROUTE_NOTICE,
            severity=Severity.MEDIUM,
            member_name=signal.member_name,
            summary=summary,
            evidence={"model_failure": verdict},
        )
