# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-process routing of completed OpenTelemetry spans to trajectories.

The processor deliberately has no exporter or persistence responsibilities.  A
subscription owns a small pending buffer; ``drain`` transfers that buffer to a
canonical :class:`~openjiuwen.agent_evolving.trajectory.model.Trajectory`.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
import threading
import uuid
from collections import deque
from collections.abc import Collection
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, attributes_to_map

__all__ = ["TrajectorySpanProcessor"]

_LOGGER = logging.getLogger(__name__)

_CATEGORIES = frozenset({"llm", "tool", "agent", "task", "message", "member", "team"})
_MAX_ISSUES = 128

_ACTIVE_SUBSCRIPTIONS: ContextVar[tuple["_SubscriptionHandle", ...]] = ContextVar(
    "trajectory_active_subscriptions", default=()
)
_SUPPRESSION_DEPTH: ContextVar[int] = ContextVar("trajectory_suppression_depth", default=0)


class _SubscriptionHandle:
    """Opaque identity returned by ``subscribe``.

    The class intentionally exposes no routing state.  Keeping the state in a
    processor-owned table makes handles safe to pass between tasks while
    preventing a rail from depending on implementation details.
    """

    __slots__ = ()


@dataclass
class _PendingSpan:
    identity: tuple[str, str]
    payload: dict[str, Any]
    end_time: int


@dataclass
class _SubscriptionState:
    handle: _SubscriptionHandle
    categories: frozenset[str]
    trace_id: str | None
    pending: deque[_PendingSpan]
    pending_ids: set[tuple[str, str]]
    issues: list[Mapping[str, object]]
    active: bool = True


def _issue(
    code: str,
    *,
    message: str | None = None,
    span_name: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> Mapping[str, object]:
    """Build the private immutable shape used for capture-quality issues."""

    data: dict[str, object] = {"code": code}
    if message:
        data["message"] = str(message)[:512]
    if span_name:
        data["span_name"] = str(span_name)
    if trace_id:
        data["trace_id"] = str(trace_id)
    if span_id:
        data["span_id"] = str(span_id)
    return MappingProxyType(data)


def _normalise_trace_id(value: Any) -> str | None:
    """Normalize OTel integer/hex/UUID IDs while tolerating test doubles."""

    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            return str(value)
        return f"{value:032x}"
    if isinstance(value, bytes):
        return value.hex().lower()
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("0x"):
        text = text[2:]
    try:
        text = uuid.UUID(text).hex
    except (ValueError, AttributeError):
        pass
    if all(char in "0123456789abcdefABCDEF" for char in text) and len(text) <= 32:
        text = text.zfill(32)
    return text.lower()


def _normalise_span_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            return str(value)
        return f"{value:016x}"
    if isinstance(value, bytes):
        return value.hex().lower()
    text = str(value).strip()
    if text.startswith("0x"):
        text = text[2:]
    if all(char in "0123456789abcdefABCDEF" for char in text) and len(text) <= 16:
        text = text.zfill(16)
    return text.lower() or None


def _context_value(span: Any) -> Any:
    context = getattr(span, "context", None)
    if context is None:
        getter = getattr(span, "get_span_context", None)
        if callable(getter):
            context = getter()
    return context


def _span_identity(span: Any) -> tuple[str, str]:
    context = _context_value(span)
    trace_id = _normalise_trace_id(getattr(context, "trace_id", None))
    span_id = _normalise_span_id(getattr(context, "span_id", None))
    if not trace_id or not span_id:
        raise ValueError("span is missing a valid trace/span identity")
    return trace_id, span_id


def _span_status(span: Any) -> dict[str, Any]:
    status = getattr(span, "status", None)
    if status is None:
        return {}
    status_code = getattr(status, "status_code", None)
    status_name = getattr(status_code, "name", str(status_code or "UNSET"))
    if not str(status_name).startswith("STATUS_CODE_"):
        status_name = f"STATUS_CODE_{status_name}"
    result: dict[str, Any] = {"code": status_name}
    description = getattr(status, "description", None)
    if description:
        result["message"] = str(description)
    return result


def _span_kind(span: Any) -> str:
    kind = getattr(span, "kind", None)
    name = getattr(kind, "name", str(kind or "INTERNAL"))
    if str(name).startswith("SPAN_KIND_"):
        return str(name)
    return f"SPAN_KIND_{name}"


def _span_to_otlp(span: ReadableSpan) -> tuple[dict[str, Any], tuple[str, str]]:
    """Copy one ended span into the canonical OTLP JSON representation."""

    trace_id, span_id = _span_identity(span)
    parent_context = getattr(span, "parent", None)
    parent_span_id = _normalise_span_id(getattr(parent_context, "span_id", None))

    encoded: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": str(getattr(span, "name", "")),
        "kind": _span_kind(span),
        "attributes": attributes_from_map(getattr(span, "attributes", None) or {}),
        "status": _span_status(span),
    }
    if parent_span_id:
        encoded["parentSpanId"] = parent_span_id
    start_time = getattr(span, "start_time", None)
    end_time = getattr(span, "end_time", None)
    if start_time is not None:
        encoded["startTimeUnixNano"] = str(int(start_time))
    if end_time is not None:
        encoded["endTimeUnixNano"] = str(int(end_time))

    events: list[dict[str, Any]] = []
    for event in getattr(span, "events", ()) or ():
        event_data: dict[str, Any] = {"name": str(getattr(event, "name", ""))}
        timestamp = getattr(event, "timestamp", None)
        if timestamp is not None:
            event_data["timeUnixNano"] = str(int(timestamp))
        event_data["attributes"] = attributes_from_map(getattr(event, "attributes", None) or {})
        events.append(event_data)
    if events:
        encoded["events"] = events

    links: list[dict[str, Any]] = []
    for link in getattr(span, "links", ()) or ():
        link_context = getattr(link, "context", None)
        link_trace_id = _normalise_trace_id(getattr(link_context, "trace_id", None))
        link_span_id = _normalise_span_id(getattr(link_context, "span_id", None))
        if not link_trace_id or not link_span_id:
            continue
        links.append(
            {
                "traceId": link_trace_id,
                "spanId": link_span_id,
                "attributes": attributes_from_map(getattr(link, "attributes", None) or {}),
            }
        )
    if links:
        encoded["links"] = links

    resource = getattr(span, "resource", None)
    resource_attributes = getattr(resource, "attributes", resource if isinstance(resource, Mapping) else None)
    scope = getattr(span, "instrumentation_scope", None)
    scope_data: dict[str, Any] = {}
    if scope is not None:
        scope_name = getattr(scope, "name", None)
        scope_version = getattr(scope, "version", None)
        if scope_name:
            scope_data["name"] = str(scope_name)
        if scope_version:
            scope_data["version"] = str(scope_version)

    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": attributes_from_map(resource_attributes or {})},
                "scopeSpans": [{"scope": scope_data, "spans": [encoded]}],
            }
        ]
    }
    return payload, (trace_id, span_id)


def _end_time(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0].get("endTimeUnixNano", 0))
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def _stable_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _merge_pending(pending: list[_PendingSpan]) -> dict[str, Any]:
    """Merge spans while retaining distinct OTLP resource/scope groups."""

    trajectory_id = pending[0].identity[0]
    resource_groups: dict[str, dict[str, Any]] = {}
    scope_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in sorted(pending, key=lambda value: (value.end_time, value.identity)):
        for source_resource_span in item.payload.get("resourceSpans", ()):
            resource = deepcopy(source_resource_span.get("resource") or {})
            resource_key = _stable_key(resource)
            target_resource = resource_groups.get(resource_key)
            if target_resource is None:
                target_resource = {"resource": resource, "scopeSpans": []}
                resource_groups[resource_key] = target_resource
            for source_scope_span in source_resource_span.get("scopeSpans", ()):
                scope = deepcopy(source_scope_span.get("scope") or {})
                scope_key = (resource_key, _stable_key(scope))
                target_scope = scope_groups.get(scope_key)
                if target_scope is None:
                    target_scope = {"scope": scope, "spans": []}
                    scope_groups[scope_key] = target_scope
                    target_resource["scopeSpans"].append(target_scope)
                target_scope["spans"].extend(deepcopy(source_scope_span.get("spans") or ()))
    for resource_span in resource_groups.values():
        resource = resource_span["resource"]
        attributes = attributes_to_map(resource.get("attributes"))
        attributes.setdefault(TRAJECTORY_ID, trajectory_id)
        resource["attributes"] = attributes_from_map(attributes)
    return {"resourceSpans": list(resource_groups.values())}


class TrajectorySpanProcessor(SpanProcessor):
    """Fan out ended observability spans to isolated subscriptions."""

    def __init__(self, *, max_pending_spans: int | None = None) -> None:
        if max_pending_spans is not None and max_pending_spans <= 0:
            raise ValueError("max_pending_spans must be positive or None")
        self._max_pending_spans = max_pending_spans
        self._lock = threading.RLock()
        self._subscriptions: dict[_SubscriptionHandle, _SubscriptionState] = {}

    def subscribe(
        self,
        *,
        include_span_categories: Collection[str],
        trace_id: str | None = None,
    ) -> object:
        categories = frozenset(
            {include_span_categories} if isinstance(include_span_categories, str) else include_span_categories
        )
        unknown = categories - _CATEGORIES
        if unknown:
            raise ValueError(f"unsupported span categories: {sorted(unknown)!r}")
        if not categories:
            raise ValueError("include_span_categories must not be empty")
        if "team" in categories and trace_id is None:
            raise ValueError("team subscriptions require a trace_id")
        normalized_trace_id = _normalise_trace_id(trace_id)
        if trace_id is not None and normalized_trace_id is None:
            raise ValueError("trace_id must not be empty")
        with self._lock:
            handle = _SubscriptionHandle()
            self._subscriptions[handle] = _SubscriptionState(
                handle=handle,
                categories=categories,
                trace_id=normalized_trace_id,
                pending=deque(),
                pending_ids=set(),
                issues=[],
            )
        if normalized_trace_id is None:
            active = _ACTIVE_SUBSCRIPTIONS.get()
            _ACTIVE_SUBSCRIPTIONS.set(active + (handle,))
        return handle

    def unsubscribe(self, subscription: object) -> None:
        if not isinstance(subscription, _SubscriptionHandle):
            return
        with self._lock:
            state = self._subscriptions.pop(subscription, None)
            if state is not None:
                state.active = False
                state.pending.clear()
                state.pending_ids.clear()
                state.issues.clear()
        active = _ACTIVE_SUBSCRIPTIONS.get()
        if subscription in active:
            _ACTIVE_SUBSCRIPTIONS.set(tuple(item for item in active if item is not subscription))

    def drain(self, subscription: object) -> tuple[Trajectory | None, tuple[Mapping[str, object], ...]]:
        if not isinstance(subscription, _SubscriptionHandle):
            return None, ()
        with self._lock:
            state = self._subscriptions.get(subscription)
            if state is None or not state.active:
                return None, ()
            pending = list(state.pending)
            state.pending.clear()
            state.pending_ids.clear()
            issues = tuple(state.issues)
            state.issues.clear()
        if not pending:
            return None, issues
        try:
            trajectory = Trajectory.from_otlp(_merge_pending(pending))
        except Exception as exc:
            capture_issue = _issue("trajectory_conversion_error", message=str(exc))
            _LOGGER.warning("trajectory span processor capture issue: %s", dict(capture_issue))
            return None, issues + (capture_issue,)
        return trajectory, issues

    @contextmanager
    def suppress(self) -> Iterator[None]:
        token = _SUPPRESSION_DEPTH.set(_SUPPRESSION_DEPTH.get() + 1)
        try:
            yield
        finally:
            _SUPPRESSION_DEPTH.reset(token)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return

    def on_end(self, span: ReadableSpan) -> None:
        """Capture one ended span without ever affecting the caller thread."""
        try:
            self._on_end(span)
        except Exception as exc:
            # OTel invokes processors synchronously from ``span.end``.  A
            # malformed span or a routing-side failure must not turn capture
            # into a business-path exception.  Best-effort issue recording is
            # itself guarded because test doubles and custom log handlers may
            # fail too.
            try:
                self._record_processing_issue(span, exc)
            except Exception as issue_exc:
                self._safe_log_issue("span processing issue recording failed", issue_exc)

    def _on_end(self, span: ReadableSpan) -> None:
        if _SUPPRESSION_DEPTH.get() > 0:
            return
        span_name = str(getattr(span, "name", ""))
        category = self._category_for_name(span_name)
        if category is None:
            return
        try:
            payload, identity = _span_to_otlp(span)
        except Exception as exc:
            try:
                self._record_capture_issue(
                    span_name=span_name,
                    span=span,
                    exc=exc,
                    category=category,
                    code="span_conversion_error",
                )
            except Exception as issue_exc:
                self._safe_log_issue("span conversion issue recording failed", issue_exc)
            return
        current_handles = set(_ACTIVE_SUBSCRIPTIONS.get())
        trace_id = identity[0]
        with self._lock:
            matched: list[_SubscriptionState] = []
            for state in self._subscriptions.values():
                if not state.active or category not in state.categories:
                    continue
                if state.trace_id is None:
                    if state.handle not in current_handles:
                        continue
                elif state.trace_id != trace_id:
                    continue
                matched.append(state)
            for state in matched:
                if identity in state.pending_ids:
                    continue
                if self._max_pending_spans is not None and len(state.pending) >= self._max_pending_spans:
                    self._append_issue(
                        state,
                        _issue(
                            "pending_buffer_full",
                            message="pending span buffer is full",
                            span_name=span_name,
                            trace_id=identity[0],
                            span_id=identity[1],
                        ),
                    )
                    continue
                state.pending.append(
                    _PendingSpan(
                        identity=identity,
                        payload=deepcopy(payload),
                        end_time=_end_time(payload),
                    )
                )
                state.pending_ids.add(identity)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        return True

    def shutdown(self) -> None:
        with self._lock:
            for state in self._subscriptions.values():
                state.active = False
                state.pending.clear()
                state.pending_ids.clear()
                state.issues.clear()
            self._subscriptions.clear()
        active = _ACTIVE_SUBSCRIPTIONS.get()
        if active:
            _ACTIVE_SUBSCRIPTIONS.set(tuple())

    @staticmethod
    def _category_for_name(name: str) -> str | None:
        if name == "llm.call":
            return "llm"
        if name.startswith("tool."):
            return "tool"
        if name.startswith("agent."):
            return "agent"
        if name.startswith("task."):
            return "task"
        if name.startswith(("message.", "msg.")):
            return "message"
        if name.startswith("member."):
            return "member"
        if name.startswith(("team.", "plan.", "event.")):
            return "team"
        return None

    def _append_issue(self, state: _SubscriptionState, issue: Mapping[str, object]) -> None:
        if len(state.issues) < _MAX_ISSUES:
            state.issues.append(issue)
        try:
            _LOGGER.warning("trajectory span processor capture issue: %s", dict(issue))
        except Exception as exc:
            self._safe_log_issue("capture issue logging failed", exc)

    def _record_processing_issue(self, span: Any, exc: Exception) -> None:
        """Best-effort route of an unexpected ``on_end`` failure."""
        try:
            span_name = self._safe_span_name(span)
            category = self._category_for_name(span_name)
            if category is None:
                return
            self._record_capture_issue(
                span_name=span_name,
                span=span,
                exc=exc,
                category=category,
                code="span_processing_error",
            )
        except Exception as issue_exc:
            self._safe_log_issue("span processing issue recording failed", issue_exc)

    def _record_capture_issue(
        self,
        *,
        span_name: str,
        span: Any,
        exc: Exception,
        category: str,
        code: str,
    ) -> None:
        """Route a stable capture issue while isolating issue-side failures."""
        try:
            identity = _span_identity(span)
        except Exception:
            identity = (None, None)
        issue = _issue(
            code,
            message=self._safe_text(exc),
            span_name=span_name,
            trace_id=identity[0],
            span_id=identity[1],
        )
        current_handles = set(_ACTIVE_SUBSCRIPTIONS.get())
        trace_id = identity[0]
        with self._lock:
            for state in self._subscriptions.values():
                if not state.active:
                    continue
                if category not in state.categories:
                    continue
                if state.trace_id is None:
                    if state.handle not in current_handles:
                        continue
                elif trace_id is None or state.trace_id != trace_id:
                    continue
                self._append_issue(state, issue)

    @staticmethod
    def _safe_span_name(span: Any) -> str:
        try:
            return str(getattr(span, "name", ""))
        except Exception:
            return "<unavailable>"

    @staticmethod
    def _safe_text(value: Any) -> str:
        try:
            return str(value)[:512]
        except Exception:
            return "<unavailable>"

    @staticmethod
    def _safe_log_issue(message: str, exc: Exception) -> None:
        # Logging is secondary to capture and must not escape ``on_end``.
        with suppress(Exception):
            _LOGGER.warning("trajectory span processor %s: %s", message, exc)
