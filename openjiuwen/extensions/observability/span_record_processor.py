# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Non-blocking fan-out of complete ended-span OTLP records."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.otlp_codec import (
    encode_recording_span_snapshot_to_otlp_json,
    encode_span_to_otlp_json,
)
from openjiuwen.extensions.observability.semconv import (
    AT_SESSION_ID,
    AT_TEAM_ID,
    AT_TEAM_NAME,
    GEN_AI_CONVERSATION_ID,
    LANGFUSE_SESSION_ID,
    OJ_AGENT_MODE,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_TEAM_ID,
    OJ_TEAM_NAME,
    OJ_TRACE_SCHEMA_VERSION,
)


@dataclass(frozen=True, slots=True)
class OtlpSpanRecord:
    """One complete single-span OTLP request plus immutable routing hints."""

    raw_json: bytes
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_time_unix_nano: int
    end_time_unix_nano: int
    session_id: str | None
    request_id: str | None
    run_id: str | None
    agent_mode: str | None
    schema_version: str = "1"
    record_revision: int = 1
    observed_time_unix_nano: int = 0
    lifecycle: str = "final"
    execution_subject_id: str | None = None
    execution_subject_display_name: str | None = None
    execution_subject_kind: str | None = None
    execution_subject_parent_id: str | None = None
    execution_subject_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class OtlpSpanSnapshotRecord:
    """One independently recoverable current snapshot of a recording span."""

    raw_json: bytes
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time_unix_nano: int
    observed_time_unix_nano: int
    record_revision: int
    update_kind: str
    session_id: str | None
    request_id: str | None
    run_id: str | None
    agent_mode: str | None
    schema_version: str = "1"
    lifecycle: str = "running"
    execution_subject_id: str | None = None
    execution_subject_display_name: str | None = None
    execution_subject_kind: str | None = None
    execution_subject_parent_id: str | None = None
    execution_subject_session_id: str | None = None


class OtlpSpanRecordConsumer(Protocol):
    """Fast synchronous sink used from ``SpanProcessor.on_end``."""

    def consume(self, record: OtlpSpanRecord) -> None:
        """Accept *record* without performing blocking persistence."""


class OtlpSpanSnapshotConsumer(Protocol):
    """Optional live-snapshot capability of an ended-span consumer."""

    def consume_snapshot(self, record: OtlpSpanSnapshotRecord) -> None:
        """Accept one recording-span snapshot without blocking."""


@dataclass(slots=True)
class _ConsumerRegistration:
    """Identity registration plus the callbacks already leased to it."""

    consumer: OtlpSpanRecordConsumer
    accepting: bool = True
    in_flight: int = 0
    unregister_waiters: int = 0
    lease_threads: dict[int, int] = field(default_factory=dict)


def _accepts_snapshots(registration: _ConsumerRegistration) -> bool:
    """Report whether a live registration can take in-flight span snapshots."""
    if not registration.accepting:
        return False
    return callable(getattr(registration.consumer, "consume_snapshot", None))


def _attribute_text(attributes: Any, *keys: str) -> str | None:
    for key in keys:
        try:
            value = attributes.get(key)
        except (AttributeError, TypeError):
            return None
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _agent_mode(attributes: Any) -> str | None:
    """Return the explicit request mode or infer Team from Team identity."""
    explicit = _attribute_text(attributes, OJ_AGENT_MODE)
    if explicit is not None:
        return explicit
    if _attribute_text(attributes, OJ_TEAM_ID, OJ_TEAM_NAME, AT_TEAM_ID, AT_TEAM_NAME) is not None:
        return "team"
    return None


def _hex_id(value: Any, width: int) -> str:
    try:
        return f"{int(value):0{width}x}"
    except (TypeError, ValueError):
        return ""


class SpanRecordProcessor(SpanProcessor):
    """Serialize each ended span once and fan it out to registered consumers.

    Consumers are identity-deduplicated. Their ``consume`` implementation must
    remain fast (normally a bounded ``put_nowait``); persistence, retries and
    queue ownership belong to the embedding application rather than Agent Core.
    """

    def __init__(self) -> None:
        self._registrations: list[_ConsumerRegistration] = []
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._callback_local = threading.local()
        self._span_revisions: dict[tuple[str, str], int] = {}

    def add_consumer(self, consumer: OtlpSpanRecordConsumer) -> None:
        """Register *consumer* once by object identity."""
        consume = getattr(consumer, "consume", None)
        if not callable(consume):
            raise TypeError("span record consumer must define consume(record)")
        with self._lock:
            for registration in self._registrations:
                if registration.consumer is not consumer:
                    continue
                if registration.accepting:
                    return
                raise RuntimeError("span record consumer unregister is still in progress")
            self._registrations.append(_ConsumerRegistration(consumer=consumer))

    def remove_consumer(
        self,
        consumer: OtlpSpanRecordConsumer,
        timeout_millis: int = 30000,
    ) -> None:
        """Disable *consumer* and wait for callbacks that already hold a lease.

        The wait is a control-plane barrier. ``on_end`` never waits for another
        thread or for consumer persistence. A consumer unregistering itself
        waits for other threads but excludes its currently executing callback,
        which is released when ``consume`` returns.

        Args:
            consumer: Consumer identity to remove; a missing identity is a no-op.
            timeout_millis: Maximum barrier wait before raising ``TimeoutError``.

        Raises:
            ValueError: If ``timeout_millis`` is negative.
            RuntimeError: If one consumer callback tries to unregister another
                consumer already leased by the same ``on_end`` call.
            TimeoutError: If another callback does not exit before the deadline.
        """
        if timeout_millis < 0:
            raise ValueError("timeout_millis must be non-negative")
        deadline = time.monotonic() + timeout_millis / 1000.0
        thread_id = threading.get_ident()
        with self._condition:
            registration = self._find_registration(consumer)
            if registration is None:
                return
            current_registration = getattr(
                self._callback_local,
                "current_registration",
                None,
            )
            owns_target_callback = current_registration is registration
            same_thread_leases = registration.lease_threads.get(thread_id, 0)
            if same_thread_leases and not owns_target_callback:
                raise RuntimeError(
                    "cannot unregister another consumer leased by the current on_end call"
                )
            registration.accepting = False
            excluded_leases = same_thread_leases if owns_target_callback else 0
            registration.unregister_waiters += 1
            try:
                while registration.in_flight > excluded_leases:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for span record consumer callbacks"
                        )
                    self._condition.wait(timeout=remaining)
            finally:
                registration.unregister_waiters -= 1
                if registration.in_flight == 0 and registration.unregister_waiters == 0:
                    self._discard_registration(registration)

    # Explicit registration aliases make the lifecycle self-describing for
    # embedders while preserving the concise add/remove surface.
    register_consumer = add_consumer
    unregister_consumer = remove_consumer

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        del parent_context
        self.publish_snapshot(span, "started")

    def publish_snapshot(self, span: Any, update_kind: str) -> None:
        """Publish one full current snapshot of a still-recording span."""
        registrations = self._acquire_snapshot_leases()
        if not registrations:
            return

        try:
            identity = self._span_identity(span)
            with self._lock:
                if not span.is_recording():
                    for registration in registrations:
                        self._release_lease(registration)
                    return
                revision = self._span_revisions.get(identity, 0) + 1
                self._span_revisions[identity] = revision
            record = self._build_snapshot_record(
                span,
                update_kind=update_kind,
                record_revision=revision,
            )
        except BaseException as exc:
            for registration in registrations:
                self._release_lease(registration)
            if isinstance(exc, Exception):
                logger.warning("span_record_processor: failed to encode recording span - {}", exc)
                return
            raise

        self._deliver_snapshot(registrations, record)

    def on_end(self, span: ReadableSpan) -> None:
        """Deliver one immutable record without affecting the business path."""
        registrations = self._acquire_leases()
        if not registrations:
            try:
                identity = self._span_identity(span)
            except Exception as exc:
                logger.warning("span_record_processor: failed to identify ended span - {}", exc)
                return
            with self._lock:
                self._span_revisions.pop(identity, None)
            return

        try:
            identity = self._span_identity(span)
            revision = self._next_revision(identity)
            record = self._build_record(span, record_revision=revision)
        except BaseException as exc:
            for registration in registrations:
                self._release_lease(registration)
            if isinstance(exc, Exception):
                logger.warning("span_record_processor: failed to encode ended span - {}", exc)
                return
            raise

        for index, registration in enumerate(registrations):
            previous_registration = getattr(
                self._callback_local,
                "current_registration",
                None,
            )
            self._callback_local.current_registration = registration
            try:
                registration.consumer.consume(record)
            except Exception as exc:
                logger.warning(
                    "span_record_processor: consumer {} failed - {}",
                    type(registration.consumer).__name__,
                    exc,
                )
            except BaseException:
                for pending_registration in registrations[index + 1:]:
                    self._release_lease(pending_registration)
                raise
            finally:
                self._callback_local.current_registration = previous_registration
                self._release_lease(registration)
        with self._lock:
            self._span_revisions.pop(identity, None)

    def _deliver_snapshot(
        self,
        registrations: tuple[_ConsumerRegistration, ...],
        record: OtlpSpanSnapshotRecord,
    ) -> None:
        for index, registration in enumerate(registrations):
            previous_registration = getattr(
                self._callback_local,
                "current_registration",
                None,
            )
            self._callback_local.current_registration = registration
            try:
                registration.consumer.consume_snapshot(record)
            except Exception as exc:
                logger.warning(
                    "span_record_processor: snapshot consumer {} failed - {}",
                    type(registration.consumer).__name__,
                    exc,
                )
            except BaseException:
                for pending_registration in registrations[index + 1:]:
                    self._release_lease(pending_registration)
                raise
            finally:
                self._callback_local.current_registration = previous_registration
                self._release_lease(registration)

    def _find_registration(
        self,
        consumer: OtlpSpanRecordConsumer,
    ) -> _ConsumerRegistration | None:
        for registration in self._registrations:
            if registration.consumer is consumer:
                return registration
        return None

    def _discard_registration(self, registration: _ConsumerRegistration) -> None:
        self._registrations = [
            existing
            for existing in self._registrations
            if existing is not registration
        ]

    def _acquire_leases(self) -> tuple[_ConsumerRegistration, ...]:
        thread_id = threading.get_ident()
        with self._lock:
            registrations = tuple(
                registration
                for registration in self._registrations
                if registration.accepting
            )
            for registration in registrations:
                registration.in_flight += 1
                registration.lease_threads[thread_id] = (
                    registration.lease_threads.get(thread_id, 0) + 1
                )
            return registrations

    def _acquire_snapshot_leases(self) -> tuple[_ConsumerRegistration, ...]:
        thread_id = threading.get_ident()
        with self._lock:
            registrations = tuple(
                registration
                for registration in self._registrations
                if _accepts_snapshots(registration)
            )
            for registration in registrations:
                registration.in_flight += 1
                registration.lease_threads[thread_id] = (
                    registration.lease_threads.get(thread_id, 0) + 1
                )
            return registrations

    def _next_revision(self, identity: tuple[str, str]) -> int:
        with self._lock:
            revision = self._span_revisions.get(identity, 0) + 1
            self._span_revisions[identity] = revision
            return revision

    def _release_lease(self, registration: _ConsumerRegistration) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            registration.in_flight -= 1
            thread_leases = registration.lease_threads.get(thread_id, 0) - 1
            if thread_leases > 0:
                registration.lease_threads[thread_id] = thread_leases
            else:
                registration.lease_threads.pop(thread_id, None)
            if (
                registration.in_flight == 0
                and not registration.accepting
                and registration.unregister_waiters == 0
            ):
                self._discard_registration(registration)
            self._condition.notify_all()

    @staticmethod
    def _span_identity(span: Any) -> tuple[str, str]:
        context = getattr(span, "context", None)
        trace_id = _hex_id(getattr(context, "trace_id", None), 32)
        span_id = _hex_id(getattr(context, "span_id", None), 16)
        if not trace_id or not span_id:
            raise ValueError("span is missing a valid trace/span identity")
        return trace_id, span_id

    @staticmethod
    def _build_record(
        span: ReadableSpan,
        *,
        record_revision: int,
    ) -> OtlpSpanRecord:
        trace_id, span_id = SpanRecordProcessor._span_identity(span)

        parent = getattr(span, "parent", None)
        parent_span_id = _hex_id(getattr(parent, "span_id", None), 16) or None
        start_time = getattr(span, "start_time", None)
        end_time = getattr(span, "end_time", None)
        if start_time is None or end_time is None:
            raise ValueError("span record requires start and end timestamps")

        attributes = getattr(span, "attributes", None) or {}
        return OtlpSpanRecord(
            raw_json=encode_span_to_otlp_json(span),
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            start_time_unix_nano=int(start_time),
            end_time_unix_nano=int(end_time),
            session_id=_attribute_text(
                attributes,
                GEN_AI_CONVERSATION_ID,
                OJ_SESSION_ID,
                LANGFUSE_SESSION_ID,
                AT_SESSION_ID,
            ),
            request_id=_attribute_text(attributes, OJ_REQUEST_ID),
            run_id=_attribute_text(attributes, OJ_RUN_ID),
            agent_mode=_agent_mode(attributes),
            schema_version=_attribute_text(attributes, OJ_TRACE_SCHEMA_VERSION) or "1",
            record_revision=record_revision,
            observed_time_unix_nano=time.time_ns(),
            execution_subject_id=_attribute_text(attributes, OJ_EXECUTION_SUBJECT_ID),
            execution_subject_display_name=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
            ),
            execution_subject_kind=_attribute_text(attributes, OJ_EXECUTION_SUBJECT_KIND),
            execution_subject_parent_id=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_PARENT_ID,
            ),
            execution_subject_session_id=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_SESSION_ID,
            ),
        )

    @staticmethod
    def _build_snapshot_record(
        span: Any,
        *,
        update_kind: str,
        record_revision: int,
    ) -> OtlpSpanSnapshotRecord:
        trace_id, span_id = SpanRecordProcessor._span_identity(span)
        parent = getattr(span, "parent", None)
        parent_span_id = _hex_id(getattr(parent, "span_id", None), 16) or None
        start_time = getattr(span, "start_time", None)
        if start_time is None:
            raise ValueError("span snapshot requires a start timestamp")

        attributes = getattr(span, "attributes", None) or {}
        return OtlpSpanSnapshotRecord(
            raw_json=encode_recording_span_snapshot_to_otlp_json(span),
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=str(getattr(span, "name", "")),
            start_time_unix_nano=int(start_time),
            observed_time_unix_nano=time.time_ns(),
            record_revision=record_revision,
            update_kind=str(update_kind),
            session_id=_attribute_text(
                attributes,
                GEN_AI_CONVERSATION_ID,
                OJ_SESSION_ID,
                LANGFUSE_SESSION_ID,
                AT_SESSION_ID,
            ),
            request_id=_attribute_text(attributes, OJ_REQUEST_ID),
            run_id=_attribute_text(attributes, OJ_RUN_ID),
            agent_mode=_agent_mode(attributes),
            schema_version=_attribute_text(attributes, OJ_TRACE_SCHEMA_VERSION) or "1",
            execution_subject_id=_attribute_text(attributes, OJ_EXECUTION_SUBJECT_ID),
            execution_subject_display_name=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
            ),
            execution_subject_kind=_attribute_text(attributes, OJ_EXECUTION_SUBJECT_KIND),
            execution_subject_parent_id=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_PARENT_ID,
            ),
            execution_subject_session_id=_attribute_text(
                attributes,
                OJ_EXECUTION_SUBJECT_SESSION_ID,
            ),
        )

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Return immediately; consumers own their queues and drain lifecycle."""
        return True

    def shutdown(self) -> None:
        """Leave consumer lifecycle to the embedding application.

        The process-wide processor can be attached to a freshly initialized
        provider after all current demands release. Clearing registrations
        here would silently disconnect a still-live Swarm queue on that next
        provider; embedders explicitly unregister when their queue is gone.
        """
        return


__all__ = [
    "OtlpSpanRecord",
    "OtlpSpanRecordConsumer",
    "OtlpSpanSnapshotConsumer",
    "OtlpSpanSnapshotRecord",
    "SpanRecordProcessor",
]
