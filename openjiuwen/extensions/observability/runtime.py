# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry provider lifecycle shared by observability integrations."""

from __future__ import annotations

import base64
import threading
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from openjiuwen.core.common.exception.codes import StatusCode as ErrStatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.callback.events import AgentEvents, LLMCallEvents, ToolCallEvents
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.file_exporter import TraceFileExporter
from openjiuwen.extensions.observability.span_context import (
    ActiveSpanTracker,
    get_active_span_tracker,
    set_active_span_tracker,
)


class SafeSpanProcessor(SpanProcessor):
    """Prevent an optional processor failure from breaking span export."""

    def __init__(self, processor: SpanProcessor) -> None:
        self._processor = processor
        self._shutdown_called = False

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        try:
            self._processor.on_start(span, parent_context=parent_context)
        except Exception as exc:
            self._log_failure("on_start", exc)

    def on_end(self, span: Any) -> None:
        try:
            self._processor.on_end(span)
        except Exception as exc:
            self._log_failure("on_end", exc)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            result = self._processor.force_flush(timeout_millis)
        except Exception as exc:
            self._log_failure("force_flush", exc)
            return True
        if result is False:
            logger.warning(
                "otel: additional span processor {} force_flush returned False",
                type(self._processor).__name__,
            )
        return True

    def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        try:
            self._processor.shutdown()
        except Exception as exc:
            self._log_failure("shutdown", exc)

    def _log_failure(self, method: str, exc: Exception) -> None:
        # Logging is secondary to processor isolation and must not escape.
        with suppress(Exception):
            logger.warning(
                "otel: additional span processor {}.{} failed - {}",
                type(self._processor).__name__,
                method,
                exc,
            )


class ObservabilityRuntime:
    """Own one configured ``TracerProvider`` and its processor lifecycle."""

    def __init__(self) -> None:
        self._provider: TracerProvider | None = None
        self._config: ObservabilityConfig | None = None
        self._additional_processors: list[SpanProcessor] = []
        self._tracker: ActiveSpanTracker | None = None
        self._callback_handler: OtelCallbackHandler | None = None
        self._registered_callbacks: list[tuple[str, Any]] = []
        self._callback_framework: Any | None = None
        self._callback_namespace = "extensions.observability"
        self._lock = threading.RLock()
        self._initializing = False

    def initialize(
        self,
        config: ObservabilityConfig,
        *,
        span_exporter_override: SpanExporter | None = None,
        additional_span_processors: Sequence[SpanProcessor] = (),
    ) -> None:
        """Create the provider and register its exporter and processors."""
        with self._lock:
            if not config.enabled:
                logger.info("observability disabled by config")
                return
            if self._provider is not None:
                self.add_span_processors(additional_span_processors)
                return
            if self._initializing:
                raise RuntimeError("observability initialization is already in progress")

            self._initializing = True
            provider: TracerProvider | None = None
            additional_processors: list[SpanProcessor] = []
            try:
                tracker = ActiveSpanTracker()
                provider = TracerProvider(
                    resource=Resource.create({"service.name": config.service_name}),
                    sampler=ParentBased(root=TraceIdRatioBased(config.sample_rate)),
                    span_limits=SpanLimits(max_attributes=config.max_attributes),
                )
                provider.add_span_processor(tracker)

                exporter = span_exporter_override or build_span_exporter(config)
                if span_exporter_override is not None or isinstance(exporter, ConsoleSpanExporter):
                    provider.add_span_processor(SimpleSpanProcessor(exporter))
                else:
                    provider.add_span_processor(BatchSpanProcessor(exporter))

                self._register_span_processors(
                    provider,
                    additional_span_processors,
                    tracked_processors=additional_processors,
                )
                self._provider = provider
                self._config = config
                self._tracker = tracker
                set_active_span_tracker(tracker)
                self._additional_processors.extend(additional_processors)

                callback_handler = OtelCallbackHandler(
                    config,
                    tracer=provider.get_tracer("openjiuwen.extensions.observability"),
                )
                self._callback_handler = callback_handler
                self._register_callbacks(self._callback_pairs(callback_handler))
                try:
                    trace.set_tracer_provider(provider)
                except Exception as exc:
                    logger.warning("otel: set_tracer_provider failed - {}", exc)
            except Exception:
                self._unregister_callbacks()
                if provider is not None:
                    try:
                        provider.shutdown()
                    except Exception as exc:
                        logger.warning("otel: initialization rollback provider shutdown failed - {}", exc)
                self._provider = None
                self._config = None
                self._tracker = None
                self._callback_handler = None
                set_active_span_tracker(None)
                self._additional_processors.clear()
                raise
            finally:
                self._initializing = False

    def add_span_processors(self, processors: Sequence[SpanProcessor]) -> None:
        """Attach optional processors once, using object identity."""
        with self._lock:
            if self._provider is None:
                raise RuntimeError("observability is not initialized")
            self._register_span_processors(self._provider, processors)

    def get_tracer(self, name: str) -> Any:
        """Return a tracer bound to this runtime's provider."""
        with self._lock:
            if self._provider is not None:
                return self._provider.get_tracer(name)
            return trace.get_tracer(name)

    def get_config(self) -> ObservabilityConfig | None:
        """Return the active configuration, if initialized."""
        with self._lock:
            return self._config

    def is_initialized(self) -> bool:
        """Return whether this runtime owns an active provider."""
        with self._lock:
            return self._provider is not None

    def force_flush(self, timeout_millis: int = 5000) -> None:
        """Flush all registered processors."""
        with self._lock:
            if self._provider is None:
                return
            try:
                self._provider.force_flush(timeout_millis=timeout_millis)
            except Exception as exc:
                logger.warning("otel: force_flush failed - {}", exc)

    def shutdown(self, timeout_millis: int = 5000) -> None:
        """Flush and shut down the provider, then clear runtime state."""
        with self._lock:
            provider = self._provider
            tracker = self._tracker
            try:
                self._unregister_callbacks()
                if tracker is not None:
                    try:
                        tracker.flush_all_spans(exclude_root_span=False)
                    except Exception as exc:
                        logger.warning("otel: tracker flush failed - {}", exc)
                if provider is not None:
                    try:
                        provider.force_flush(timeout_millis=timeout_millis)
                    except Exception as exc:
                        logger.warning("otel: provider force_flush failed - {}", exc)
                    try:
                        provider.shutdown()
                    except Exception as exc:
                        logger.warning("otel: provider shutdown failed - {}", exc)
            finally:
                self._provider = None
                self._config = None
                self._tracker = None
                self._callback_handler = None
                if get_active_span_tracker() is tracker:
                    set_active_span_tracker(None)
                self._additional_processors.clear()

    def get_tracker(self) -> ActiveSpanTracker | None:
        """Return the tracker owned by this runtime, if initialized."""
        with self._lock:
            return self._tracker

    @staticmethod
    def _callback_pairs(handler: OtelCallbackHandler) -> list[tuple[str, Any]]:
        """Return the framework events handled by the common callback rail."""
        return [
            (LLMCallEvents.LLM_INVOKE_INPUT, handler.on_llm_invoke_input),
            (LLMCallEvents.LLM_STREAM_INPUT, handler.on_llm_stream_input),
            (LLMCallEvents.LLM_STREAM_OUTPUT, handler.on_llm_stream_output),
            (LLMCallEvents.LLM_INVOKE_OUTPUT, handler.on_llm_invoke_output),
            (LLMCallEvents.LLM_OUTPUT, handler.on_llm_output),
            (LLMCallEvents.LLM_CALL_ERROR, handler.on_llm_call_error),
            (ToolCallEvents.TOOL_CALL_STARTED, handler.on_tool_call_started),
            (ToolCallEvents.TOOL_CALL_FINISHED, handler.on_tool_call_finished),
            (ToolCallEvents.TOOL_CALL_ERROR, handler.on_tool_call_error),
            (AgentEvents.AGENT_INVOKE_INPUT, handler.on_agent_invoke_input),
            (AgentEvents.AGENT_INVOKE_OUTPUT, handler.on_agent_invoke_output),
            (AgentEvents.AGENT_STREAM_INPUT, handler.on_agent_stream_input),
            (AgentEvents.AGENT_STREAM_OUTPUT, handler.on_agent_stream_output),
        ]

    def _register_callbacks(
        self,
        callbacks: Sequence[tuple[str, Any]],
    ) -> list[tuple[str, Any]]:
        """Register callback pairs and roll back a partial registration.

        A missing callback framework is a supported optional-runtime mode: a
        warning is emitted and the provider remains initialized.
        """
        with self._lock:
            target = self._resolve_callback_framework()
            if target is None:
                logger.warning("otel: callback framework unavailable; skipping callback registration")
                return []
            pairs: list[tuple[str, Any]] = []
            for event, callback in callbacks:
                is_registered = any(
                    registered_event == event and registered_callback is callback
                    for registered_event, registered_callback in self._registered_callbacks
                )
                if not is_registered:
                    pairs.append((event, callback))
            if not pairs:
                return []
            registered: list[tuple[str, Any]] = []
            try:
                for event, callback in pairs:
                    target.register_sync(
                        event,
                        callback,
                        namespace=self._callback_namespace,
                    )
                    registered.append((event, callback))
            except Exception:
                self._unregister_callback_pairs(target, registered)
                raise
            self._callback_framework = target
            self._registered_callbacks.extend(registered)
            return registered

    def _unregister_callbacks(self) -> None:
        """Unregister callbacks previously registered by this runtime."""
        with self._lock:
            framework = self._callback_framework
            registered = list(self._registered_callbacks)
            self._registered_callbacks.clear()
            self._callback_framework = None
            if framework is None:
                return
            self._unregister_callback_pairs(framework, registered)

    @staticmethod
    def _resolve_callback_framework() -> Any | None:
        try:
            from openjiuwen.core.runner import Runner

            return Runner.callback_framework
        except Exception as exc:
            logger.warning("otel: cannot reach callback framework - {}", exc)
            return None

    @staticmethod
    def _unregister_callback_pairs(framework: Any, registered: Sequence[tuple[str, Any]]) -> None:
        for event, callback in registered:
            try:
                framework.unregister_sync(event, callback)
            except Exception as exc:
                logger.warning("otel: failed to unregister {} - {}", event, exc)

    def _register_span_processors(
        self,
        provider: TracerProvider,
        processors: Sequence[SpanProcessor],
        *,
        tracked_processors: list[SpanProcessor] | None = None,
    ) -> None:
        registered = self._additional_processors if tracked_processors is None else tracked_processors
        for processor in processors:
            if any(existing is processor for existing in registered):
                continue
            provider.add_span_processor(SafeSpanProcessor(processor))
            registered.append(processor)


def build_span_exporter(config: ObservabilityConfig) -> SpanExporter:
    """Construct the exporter selected by the configuration."""
    if config.exporter == "console":
        return ConsoleSpanExporter()
    if config.exporter == "file":
        return TraceFileExporter(
            root_dir=config.traces_dir,
            retention_days=config.file_retention_days,
        )
    if config.exporter == "otlp_grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(
            endpoint=config.endpoint,
            insecure=True,
            headers=build_auth_headers(config),
        )
    if config.exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        return HttpExporter(endpoint=config.endpoint, headers=build_auth_headers(config))
    raise build_error(
        ErrStatusCode.PARAM_INVALID_ERROR,
        msg=f"unsupported observability exporter: {config.exporter}",
    )


def build_auth_headers(config: ObservabilityConfig) -> dict[str, str]:
    """Build Basic authentication headers for a configured OTLP backend."""
    if not config.langfuse_public_key or not config.langfuse_secret_key:
        return {}
    credentials = base64.b64encode(f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()).decode()
    return {"authorization": f"Basic {credentials}"}


__all__ = [
    "ObservabilityRuntime",
    "SafeSpanProcessor",
    "build_auth_headers",
    "build_span_exporter",
]
