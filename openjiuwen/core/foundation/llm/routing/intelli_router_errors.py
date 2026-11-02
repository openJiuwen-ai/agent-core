# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Translate intelli_router failures into framework errors.

intelli_router raises its own exception hierarchy and only remembers *when* a
failed deployment may be retried (its cooldown), not *why* it failed. This
module records the reason per deployment, decides whether the router can still
serve the call from another candidate deployment, and builds a ``BaseError``
whose message a user can act on and whose ``details`` tell downstream consumers
what to do:

- ``switchable`` (181001): another candidate is usable. Before output,
  ``should_retry`` asks the model-call retry rail to call the router again; the
  router skips the failed deployment because it already put it in cooldown
  before raising. After output, the team layer re-drives the round.
- ``exhausted_retryable`` (181010): no candidate is usable but a transient
  failure may recover; retry once after the earliest cooldown.
- ``exhausted_request_rejected`` (181010): every candidate rejected the request
  itself (e.g. context too long). Not retried here; the ReAct context recovery
  may shrink the request and call again.
- ``exhausted_user_action`` (181010): retrying will not help; a human decides.

The router is read through its public attributes. The only write is releasing
the cooldown of deployments that rejected the *request* (not the deployment),
so a recovery retry with a smaller request can use them at once.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error

try:
    from intelli_router.utils.exceptions import IntelliRouterError
except ImportError:  # pragma: no cover - intelli_router is an optional dependency
    IntelliRouterError = None

# Exception types the client translates; empty when intelli_router is absent.
ROUTER_ERROR_TYPES: tuple[type[BaseException], ...] = (
    (IntelliRouterError,) if IntelliRouterError is not None else ()
)

ROUTE_CASE_SWITCHABLE = "switchable"
ROUTE_CASE_EXHAUSTED_RETRYABLE = "exhausted_retryable"
ROUTE_CASE_EXHAUSTED_REQUEST_REJECTED = "exhausted_request_rejected"
ROUTE_CASE_EXHAUSTED_USER_ACTION = "exhausted_user_action"

CATEGORY_DETERMINISTIC = "deterministic"
CATEGORY_TRANSIENT = "transient"
CATEGORY_REQUEST_REJECTED = "request_rejected"
CATEGORY_STREAM_INTERRUPTED = "stream_interrupted"

REASON_HAS_TRANSIENT = "has_transient"
REASON_ALL_DETERMINISTIC = "all_deterministic"
REASON_STREAM_INTERRUPTED = "stream_interrupted"
REASON_TAG_NOT_FOUND = "tag_not_found"
REASON_TRANSIENT_REPEATED = "transient_repeated"

# Delay before re-calling the router when another candidate is usable. The
# failed deployment is already in cooldown, so no real wait is needed.
SWITCH_RETRY_DELAY_SECONDS = 1.0
# The retry rail refuses delays above 60s (model_anomaly_detection_rail).
_MAX_RETRY_AFTER_SECONDS = 60.0
# Margin so the retry lands just after the cooldown expires.
_COOLDOWN_MARGIN_SECONDS = 0.5
# A deployment failed this many times in a row (no success between) is not
# worth waiting for again.
_REPEATED_FAILURE_THRESHOLD = 2
# Longest provider error excerpt quoted to the user.
_DETAIL_MAX_CHARS = 200

_TRANSIENT_ERROR_TYPES = {
    "DeploymentServerError",
    "DeploymentRateLimitError",
    "DeploymentTimeoutError",
    "DeploymentNetworkError",
}
# Rejections of the deployment (credentials / endpoint): another deployment
# may work, this one will not until someone fixes its configuration.
_DETERMINISTIC_STATUS_CODES = {401, 403, 404}

_HTTP_STATUS_PATTERN = re.compile(r"returned HTTP (\d{3})")
_TIMEOUT_PATTERN = re.compile(r"timed out after ([\d.]+)s")

# Router events reporting that a deployment served a call.
_SUCCESS_EVENTS = {"request_succeeded", "stream_succeeded"}
# Router events carrying a deployment failure.
_FAILURE_EVENTS = {"request_retried", "all_deployments_exhausted"}
ROUTING_RECORD_EVENTS = _FAILURE_EVENTS | _SUCCESS_EVENTS


@dataclass(frozen=True)
class RouteFailure:
    """The most recent failure of one deployment.

    ``message`` is a user-facing phrase completing "model 'X' (provider,
    host) ...", e.g. "rejected the API key (HTTP 401)".
    """

    category: str
    error_type: str
    status_code: int | None
    message: str
    timestamp: float


# (model_group_id, deployment_id) -> last failure. Shared per process because
# members with different strategies use different router instances, and the
# reason must be readable from any of them.
_last_failures: dict[tuple[str | None, str], RouteFailure] = {}
_last_failures_lock = Lock()


def reset_failure_records() -> None:
    """Clear recorded failures (tests)."""
    with _last_failures_lock:
        _last_failures.clear()


def _store_failure(model_group_id: str | None, deployment_id: str, failure: RouteFailure) -> None:
    with _last_failures_lock:
        _last_failures[(model_group_id, deployment_id)] = failure


def _load_failure(model_group_id: str | None, deployment_id: str) -> RouteFailure | None:
    with _last_failures_lock:
        return _last_failures.get((model_group_id, deployment_id))


def _clear_failure(model_group_id: str | None, deployment_id: str) -> None:
    with _last_failures_lock:
        _last_failures.pop((model_group_id, deployment_id), None)


def _coerce_status(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _classify(error_type: str, status_code: int | None) -> str:
    if error_type in _TRANSIENT_ERROR_TYPES:
        return CATEGORY_TRANSIENT
    if status_code in _DETERMINISTIC_STATUS_CODES:
        return CATEGORY_DETERMINISTIC
    if status_code is not None and 400 <= status_code < 500 and status_code != 429:
        return CATEGORY_REQUEST_REJECTED
    return CATEGORY_TRANSIENT


def _provider_detail(body: Any) -> str | None:
    """Pull the provider's own error message out of a response body."""
    if not body:
        return None
    text = str(body)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        candidate = error or payload.get("message")
        if isinstance(candidate, str) and candidate.strip():
            text = candidate
    text = " ".join(text.split())
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[:_DETAIL_MAX_CHARS].rstrip() + "..."
    return text or None


def _describe(error_type: str, status_code: int | None, timeout: Any = None, detail: str | None = None) -> str:
    """User-facing phrase for one failure (see ``RouteFailure.message``)."""
    if error_type == "DeploymentTimeoutError":
        return f"did not respond within {timeout}s" if timeout is not None else "did not respond in time"
    if error_type == "DeploymentNetworkError":
        return "could not be reached"
    if error_type == "DeploymentRateLimitError" or status_code == 429:
        return "is rate limited (HTTP 429)"
    if status_code in (401, 403):
        return f"rejected the API key (HTTP {status_code})"
    if status_code == 404:
        return "was not found at its endpoint (HTTP 404)"
    if status_code is not None and status_code >= 500:
        return f"returned a server error (HTTP {status_code})"
    if status_code is not None and 400 <= status_code < 500:
        phrase = f"rejected the request (HTTP {status_code})"
        return f"{phrase}: {detail}" if detail else phrase
    return "failed"


def _parse_error_text(text: str) -> tuple[str, int | None, Any]:
    """Infer ``(error_type, status_code, timeout)`` from an intelli_router message."""
    match = _HTTP_STATUS_PATTERN.search(text)
    status_code = int(match.group(1)) if match else None
    if "(auth failed)" in text:
        return "DeploymentAuthError", status_code, None
    if "(server error)" in text:
        return "DeploymentServerError", status_code, None
    if "rate limited" in text:
        return "DeploymentRateLimitError", 429, None
    timeout_match = _TIMEOUT_PATTERN.search(text)
    if timeout_match:
        return "DeploymentTimeoutError", None, timeout_match.group(1)
    if "network error" in text:
        return "DeploymentNetworkError", None, None
    return "DeploymentError", status_code, None


def _make_failure(
    error_type: str,
    status_code: int | None,
    timeout: Any = None,
    *,
    detail: str | None = None,
    output_emitted: bool = False,
) -> RouteFailure:
    message = _describe(error_type, status_code, timeout, detail)
    category = _classify(error_type, status_code)
    if output_emitted:
        category = CATEGORY_STREAM_INTERRUPTED
        message = f"stopped in the middle of its answer ({message})"
    return RouteFailure(
        category=category,
        error_type=error_type,
        status_code=status_code,
        message=message,
        timestamp=time.time(),
    )


def record_routing_event(event: Any) -> None:
    """Keep the per-deployment failure records in step with router events.

    ``REQUEST_RETRIED`` carries the failure the router switched away from;
    ``ALL_DEPLOYMENTS_EXHAUSTED`` carries the last failure of a call; a
    success (``REQUEST_SUCCEEDED`` / ``STREAM_SUCCEEDED``) clears the record so
    a fixed deployment is not reported with its old reason.
    """
    extra = getattr(event, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
    deployment_id = getattr(event, "deployment_id", None) or extra.get("route_id")
    if not deployment_id:
        return
    event_type = getattr(getattr(event, "event_type", None), "value", None) or str(getattr(event, "event_type", ""))
    if event_type in _SUCCESS_EVENTS:
        _clear_failure(extra.get("model_group_id"), str(deployment_id))
        return
    error_message = getattr(event, "error_message", None)
    if not error_message:
        return
    parsed_type, status_code, timeout = _parse_error_text(str(error_message))
    error_type = getattr(event, "error_type", None) or extra.get("fallback_reason") or parsed_type
    _store_failure(extra.get("model_group_id"), str(deployment_id), _make_failure(error_type, status_code, timeout))


def _record_exception(
    model_group_id: str | None,
    exc: BaseException,
    output_emitted: bool,
) -> tuple[str | None, RouteFailure | None]:
    """Record the failure behind ``exc``; return ``(deployment_id, failure)`` that triggered it."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        details = {}

    deployment_id = details.get("deployment_id")
    if deployment_id:
        error_type = type(exc).__name__
        status_code = _coerce_status(details.get("status_code"))
        if status_code is None and error_type == "DeploymentRateLimitError":
            status_code = 429
        failure = _make_failure(
            error_type,
            status_code,
            details.get("timeout"),
            detail=_provider_detail(details.get("response_body")),
            output_emitted=output_emitted,
        )
        _store_failure(model_group_id, str(deployment_id), failure)
        return str(deployment_id), failure

    # AllDeploymentsFailed: errors = [(deployment_id, message), ...]
    errors = details.get("errors")
    last: tuple[str | None, RouteFailure | None] = (None, None)
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            error_type, status_code, timeout = _parse_error_text(str(item[1]))
            failure = _make_failure(error_type, status_code, timeout)
            _store_failure(model_group_id, str(item[0]), failure)
            last = (str(item[0]), failure)
    return last


def _cooldown_remaining(router: Any, deployment_id: str, now: float) -> float | None:
    state = getattr(router, "state", None)
    if state is None:
        return None
    status = getattr(state, "deployment_status", {}).get(deployment_id)
    if getattr(status, "value", status) != "cooldown":
        return None
    until = getattr(state, "cooldown_until", {}).get(deployment_id) or 0
    remaining = until - now
    return remaining if remaining > 0 else None


def _consecutive_failures(router: Any, deployment_id: str) -> int:
    state = getattr(router, "state", None)
    if state is None:
        return 0
    return int(getattr(state, "consecutive_failures", {}).get(deployment_id, 0) or 0)


def _release_cooldown(router: Any, deployment_id: str) -> None:
    """Make a deployment selectable again (``LocalRouterState.reset_deployment``)."""
    reset = getattr(getattr(router, "state", None), "reset_deployment", None)
    if callable(reset):
        reset(deployment_id)


def _pinned_tag(router: Any) -> str | None:
    tag = getattr(getattr(router, "strategy", None), "fallback_tag", None)
    return str(tag) if tag else None


def _candidates(router: Any, tag: str | None, model_name: str | None) -> list[Any]:
    deployments = list(getattr(router, "deployments", None) or [])
    if model_name and model_name != "*":
        deployments = [d for d in deployments if getattr(d, "model_name", None) == model_name]
    if tag:
        deployments = [d for d in deployments if getattr(d, "fallback_tag", None) == tag]
    return deployments


def _endpoint(deployment: Any) -> str:
    api_base = str(getattr(deployment, "api_base", "") or "")
    host = urlparse(api_base).hostname if "://" in api_base else None
    return host or api_base


def _position(router: Any, deployment: Any) -> int | None:
    """1-based place of ``deployment`` in the group order (the order the router tries)."""
    for index, candidate in enumerate(getattr(router, "deployments", None) or [], start=1):
        if candidate is deployment:
            return index
    return None


def _label(deployment: Any) -> str:
    return (
        f"'{getattr(deployment, 'model_name', '')}' "
        f"({getattr(deployment, 'provider', '')}, {_endpoint(deployment)})"
    )


@dataclass(frozen=True)
class _Candidate:
    deployment: Any
    failure: RouteFailure | None
    cooldown_remaining: float | None
    position: int | None = None

    @property
    def deployment_id(self) -> str:
        return str(getattr(self.deployment, "id", ""))

    @property
    def category(self) -> str:
        # No record: assume transient so missing data never escalates to a user.
        return self.failure.category if self.failure is not None else CATEGORY_TRANSIENT

    def describe(self) -> str:
        reason = self.failure.message if self.failure is not None else "is temporarily unavailable"
        return f"model {_label(self.deployment)} {reason}"

    def to_dict(self) -> dict[str, Any]:
        failure = self.failure
        return {
            "deployment_id": self.deployment_id,
            "model_name": getattr(self.deployment, "model_name", None),
            "provider": getattr(self.deployment, "provider", None),
            "endpoint": _endpoint(self.deployment),
            "position": self.position,
            "category": self.category,
            "status_code": failure.status_code if failure else None,
            "error_type": failure.error_type if failure else None,
            "message": failure.message if failure else "is temporarily unavailable",
            "cooldown_remaining_seconds": self.cooldown_remaining,
        }


def _fix_hint(failures: list[RouteFailure | None]) -> str:
    """What the user should check, from the deterministic failures involved."""
    statuses = {f.status_code for f in failures if f is not None}
    hints = []
    if statuses & {401, 403}:
        hints.append("check the API key configured for this model")
    if 404 in statuses:
        hints.append("check the model name and endpoint")
    return f" Please {' and '.join(hints)}." if hints else ""


def _sentences(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def to_model_error(
    exc: BaseException,
    *,
    router: Any,
    model_name: str | None,
    output_emitted: bool,
) -> BaseError:
    """Translate an intelli_router exception into a framework ``BaseError``."""
    now = time.time()
    group_id = getattr(router, "model_group_id", None)
    trigger_id, trigger = _record_exception(group_id, exc, output_emitted)
    tag = _pinned_tag(router)
    candidates = [
        _Candidate(
            deployment=d,
            failure=_load_failure(group_id, str(getattr(d, "id", ""))),
            cooldown_remaining=_cooldown_remaining(router, str(getattr(d, "id", "")), now),
            position=_position(router, d),
        )
        for d in _candidates(router, tag, model_name)
    ]
    # The deployment that raised this error comes first, so consumers that
    # report one reason (the reliability notice) report the right one.
    failed = sorted(
        (c for c in candidates if c.cooldown_remaining is not None),
        key=lambda c: c.deployment_id != trigger_id,
    )
    usable = [c for c in candidates if c.cooldown_remaining is None]
    trigger_deployment = next(
        (d for d in getattr(router, "deployments", None) or [] if str(getattr(d, "id", "")) == trigger_id),
        None,
    )
    if trigger_deployment is not None and trigger is not None:
        what_happened = f"The model {_label(trigger_deployment)} {trigger.message}."
    else:
        what_happened = f"The model '{tag or model_name or '*'}' failed."
    pin_note = f"This member is set to use '{tag}' only, so no other model was tried." if tag else ""

    def build(status: StatusCode, route_case: str, reason: str, message: str,
              retry_after: float | None = None) -> BaseError:
        details = {
            "route_case": route_case,
            "reason": reason,
            "should_retry": retry_after is not None,
            "retry_after_seconds": retry_after,
            "user_action_required": route_case == ROUTE_CASE_EXHAUSTED_USER_ACTION,
            "output_emitted": output_emitted,
            "model_group_id": group_id,
            "pinned_model": tag,
            "failures": [c.to_dict() for c in failed],
            # Where the router can still go; lets a notice say what it switched to.
            "usable": [
                {
                    "model_name": getattr(c.deployment, "model_name", None),
                    "provider": getattr(c.deployment, "provider", None),
                    "endpoint": _endpoint(c.deployment),
                    "position": c.position,
                }
                for c in usable
            ],
            "status_code": trigger.status_code if trigger is not None else None,
        }
        # ``msg`` replaces the technical template: the text is shown to users.
        return build_error(status, msg=message, details=details, cause=exc)

    if not candidates:
        missing = tag or model_name or "*"
        return build(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_USER_ACTION, REASON_TAG_NOT_FOUND,
            f"The model '{missing}' is not in the selected model group. "
            "Please choose another model or add it back to the model group.",
        )

    if usable and output_emitted:
        return build(
            StatusCode.MODEL_CALL_FAILED, ROUTE_CASE_SWITCHABLE, REASON_STREAM_INTERRUPTED,
            _sentences(what_happened, "Retrying with another model in the group."),
        )
    if usable:
        category = trigger.category if trigger is not None else CATEGORY_TRANSIENT
        return build(
            StatusCode.MODEL_CALL_FAILED, ROUTE_CASE_SWITCHABLE, category,
            _sentences(what_happened, "Switching to another model in the group."),
            retry_after=SWITCH_RETRY_DELAY_SECONDS,
        )

    listed = "; ".join(c.describe() for c in candidates)
    if len(candidates) == 1:
        overview = f"The {listed}."
    else:
        scope = f"model '{tag}'" if tag else "model in the group"
        overview = f"No {scope} is available: {listed}."

    if output_emitted:
        return build(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_USER_ACTION, REASON_STREAM_INTERRUPTED,
            _sentences(what_happened, pin_note or "No other model in the group is available."),
        )

    transient = [c for c in candidates if c.category == CATEGORY_TRANSIENT]
    rejected = [c for c in candidates if c.category == CATEGORY_REQUEST_REJECTED]
    if not transient and rejected:
        # The request itself was refused (e.g. too long). Free those
        # deployments so a retry with a smaller request can use them at once.
        for candidate in rejected:
            _release_cooldown(router, candidate.deployment_id)
        return build(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_REQUEST_REJECTED, CATEGORY_REQUEST_REJECTED,
            overview,
        )
    if not transient:
        return build(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_USER_ACTION, REASON_ALL_DETERMINISTIC,
            _sentences(overview, _fix_hint([c.failure for c in candidates]), pin_note),
        )
    if all(_consecutive_failures(router, c.deployment_id) >= _REPEATED_FAILURE_THRESHOLD for c in transient):
        return build(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_USER_ACTION, REASON_TRANSIENT_REPEATED,
            _sentences(overview, "Still unavailable after retrying.", pin_note),
        )

    waits = [c.cooldown_remaining for c in transient if c.cooldown_remaining is not None]
    wait = min(waits) + _COOLDOWN_MARGIN_SECONDS if waits else SWITCH_RETRY_DELAY_SECONDS
    retry_after = max(SWITCH_RETRY_DELAY_SECONDS, min(wait, _MAX_RETRY_AFTER_SECONDS))
    return build(
        StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, ROUTE_CASE_EXHAUSTED_RETRYABLE, REASON_HAS_TRANSIENT,
        _sentences(overview, f"Retrying automatically in {retry_after:.0f} seconds."),
        retry_after=retry_after,
    )
