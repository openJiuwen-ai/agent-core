# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TracerProvider lifecycle and callback wiring for observability.

Public entry points:
    - ``init_observability(config)``: stand up the TracerProvider, build
      the configured exporter, and register all callback handlers
      against the global AsyncCallbackFramework.
    - ``shutdown_observability()``: unregister callbacks, flush spans,
      reset module state. Tests rely on this to keep cases isolated.
    - ``attach_to_team_agent(team_agent)``: register the monitor handler
      on a leader TeamAgent. Idempotent per agent.

Tests can pass ``span_exporter_override`` to ``init_observability`` to
bypass the OTLP exporter and feed an InMemorySpanExporter directly.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from openjiuwen.agent_teams.observability.callback_handler import OtelCallbackHandler
from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.monitor_handler import OtelTeamMonitorHandler
from openjiuwen.agent_teams.observability.span_context import (
    ActiveSpanTracker,
    get_active_span_tracker,
    reset_all,
    set_active_span_tracker,
)
from openjiuwen.core.common.exception.codes import StatusCode as ErrStatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.callback.events import (
    AgentEvents,
    LLMCallEvents,
    ToolCallEvents,
)


_NAMESPACE = "agent_teams.observability"
_CALLBACK_TRACER_NAME = "openjiuwen.agent_teams.observability"
_MONITOR_TRACER_NAME = "openjiuwen.agent_teams.observability.monitor"

_provider: Optional[TracerProvider] = None
_callback_handler: Optional[OtelCallbackHandler] = None
_monitor_handler: Optional[OtelTeamMonitorHandler] = None
_config: Optional[ObservabilityConfig] = None
_registered: list[tuple[str, Callable[..., Any]]] = []


def init_observability(
    config: ObservabilityConfig,
    *,
    span_exporter_override: SpanExporter | None = None,
) -> None:
    """Initialize the TracerProvider and register Callback handlers.

    Args:
        config: Effective ObservabilityConfig.
        span_exporter_override: When set, bypass ``config.exporter`` and
            use this exporter directly. Tests pass an InMemorySpanExporter
            here to capture spans without an external collector.
    """
    global _provider, _callback_handler, _monitor_handler, _config

    if not config.enabled:
        team_logger.info("observability disabled by config")
        return
    if _provider is not None:
        team_logger.warning("observability already initialized; skipping re-init")
        return

    _config = config

    resource = Resource.create({"service.name": config.service_name})
    sampler = ParentBased(root=TraceIdRatioBased(config.sample_rate))
    _provider = TracerProvider(resource=resource, sampler=sampler)

    tracker = ActiveSpanTracker()
    _provider.add_span_processor(tracker)
    set_active_span_tracker(tracker)

    exporter = span_exporter_override or _build_exporter(config)
    if span_exporter_override is not None or isinstance(exporter, ConsoleSpanExporter):
        _provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        _provider.add_span_processor(BatchSpanProcessor(exporter))

    try:
        trace.set_tracer_provider(_provider)
    except Exception as exc:
        team_logger.warning("otel: set_tracer_provider failed - {}", exc)

    _callback_handler = OtelCallbackHandler(
        config,
        tracer=_provider.get_tracer(_CALLBACK_TRACER_NAME),
    )
    _monitor_handler = OtelTeamMonitorHandler(
        config,
        tracer=_provider.get_tracer(_MONITOR_TRACER_NAME),
    )
    _wire_callback_handlers(_callback_handler)


def finalize_team_trace(team_name: str) -> None:
    """Close all spans for a specific team when the runner exits."""
    if not team_name:
        return

    team_logger.info("otel: finalize_team_trace for team={}", team_name)

    if _monitor_handler is not None:
        _monitor_handler.close_team_spans(team_name)

    from openjiuwen.agent_teams.observability.span_context import finalize_trace
    finalize_trace(team_name)

    force_flush_provider()


def force_flush_provider(timeout_millis: int = 5000) -> None:
    """Force flush the TracerProvider to ensure spans are exported."""
    if _provider is not None:
        try:
            _provider.force_flush(timeout_millis=timeout_millis)
        except Exception as exc:
            team_logger.warning("otel: force_flush failed - {}", exc)


def shutdown_observability() -> None:
    """Unregister callbacks, flush, and reset module state."""
    global _provider, _callback_handler, _monitor_handler, _config

    framework = _runner_callback_framework()
    if framework is not None:
        for event, cb in _registered:
            try:
                framework.unregister_sync(event, cb)
            except Exception as exc:
                team_logger.warning("otel: failed to unregister {} - {}", event, exc)
    _registered.clear()

    if _monitor_handler is not None:
        _monitor_handler.close_all_spans()

    tracker = get_active_span_tracker()
    if tracker is not None:
        tracker.flush_all_spans(exclude_team_span=False)
        set_active_span_tracker(None)

    if _provider is not None:
        try:
            _provider.force_flush(timeout_millis=5000)
        except Exception as exc:
            team_logger.warning("otel: provider force_flush failed - {}", exc)
        try:
            _provider.shutdown()
        except Exception as exc:
            team_logger.warning("otel: provider shutdown failed - {}", exc)
        _provider = None

    _callback_handler = None
    _monitor_handler = None
    _config = None
    reset_all()


def get_tracer(name: str) -> Any:
    """Return a Tracer bound to the active observability provider."""
    if _provider is not None:
        return _provider.get_tracer(name)
    return trace.get_tracer(name)


def get_config() -> ObservabilityConfig | None:
    """Return the active ObservabilityConfig, or None if not initialized."""
    return _config


def is_initialized() -> bool:
    """Return True if ``init_observability()`` has been called and not yet shut down."""
    return _provider is not None


def attach_to_team_agent(team_agent: Any) -> None:
    """Register the monitor handler on a leader TeamAgent.

    Idempotent — silently skips when the handler is already registered.
    """
    if _monitor_handler is None:
        team_logger.warning("attach_to_team_agent called before init_observability")
        return
    listeners = getattr(team_agent, "_state", None)
    if listeners is not None:
        listener_list = getattr(listeners, "event_listeners", None)
        if listener_list is not None and _monitor_handler in listener_list:
            return
    team_agent.add_event_listener(_monitor_handler)


def _build_exporter(config: ObservabilityConfig) -> SpanExporter:
    """Construct the exporter selected by the configuration."""
    if config.exporter == "console":
        return ConsoleSpanExporter()
    if config.exporter == "otlp_grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        headers = _build_auth_headers(config)
        return OTLPSpanExporter(endpoint=config.endpoint, insecure=True, headers=headers)
    if config.exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        headers = _build_auth_headers(config)
        return HttpExporter(endpoint=config.endpoint, headers=headers)
    raise build_error(
        ErrStatusCode.PARAM_INVALID_ERROR,
        msg=f"unsupported observability exporter: {config.exporter}",
    )


def _build_auth_headers(config: ObservabilityConfig) -> dict[str, str]:
    """Build authentication headers for OTLP export."""
    import base64

    headers: dict[str, str] = {}
    if config.langfuse_public_key and config.langfuse_secret_key:
        credentials = base64.b64encode(
            f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
        ).decode()
        headers["authorization"] = f"Basic {credentials}"
    return headers


def _wire_callback_handlers(handler: OtelCallbackHandler) -> None:
    """Register all callback handlers against the framework singleton."""
    framework = _runner_callback_framework()
    if framework is None:
        team_logger.warning("otel: Runner.callback_framework unavailable; skipping wiring")
        return

    pairs: list[tuple[str, Callable[..., Any]]] = [
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
    for event, cb in pairs:
        framework.register_sync(event, cb, namespace=_NAMESPACE)
        _registered.append((event, cb))


def _runner_callback_framework() -> Any:
    """Lazy lookup of Runner.callback_framework to avoid bootstrap cycles."""
    try:
        from openjiuwen.core.runner import Runner

        return Runner.callback_framework
    except Exception as exc:
        team_logger.warning("otel: cannot reach Runner.callback_framework - {}", exc)
        return None
