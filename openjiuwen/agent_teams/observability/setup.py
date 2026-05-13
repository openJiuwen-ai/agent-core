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
from openjiuwen.agent_teams.observability.span_context import reset_all
from openjiuwen.core.common.exception.codes import StatusCode
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
_RAIL_TRACER_NAME = "openjiuwen.agent_teams.observability.rail"

_provider: Optional[TracerProvider] = None
_callback_handler: Optional[OtelCallbackHandler] = None
_monitor_handler: Optional[OtelTeamMonitorHandler] = None
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
    global _provider, _callback_handler, _monitor_handler

    if not config.enabled:
        team_logger.info("observability disabled by config")
        return
    if _provider is not None:
        team_logger.warning("observability already initialized; skipping re-init")
        return

    resource = Resource.create({"service.name": config.service_name})
    sampler = ParentBased(root=TraceIdRatioBased(config.sample_rate))
    _provider = TracerProvider(resource=resource, sampler=sampler)

    exporter = span_exporter_override or _build_exporter(config)
    # Use SimpleSpanProcessor when the caller supplies an exporter directly
    # (test path with InMemorySpanExporter) or for ConsoleSpanExporter so that
    # spans become visible without an explicit force_flush. Production
    # exporters go through BatchSpanProcessor for throughput.
    if span_exporter_override is not None or isinstance(exporter, ConsoleSpanExporter):
        _provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        _provider.add_span_processor(BatchSpanProcessor(exporter))

    # Best-effort install as global TracerProvider; OTel refuses overrides
    # (e.g. between tests) but our handlers keep a reference to _provider's
    # tracer directly, so global state is not load-bearing.
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


def shutdown_observability() -> None:
    """Unregister callbacks, flush spans, and reset module state."""
    global _provider, _callback_handler, _monitor_handler

    framework = _runner_callback_framework()
    if framework is not None:
        for event, cb in _registered:
            try:
                framework.unregister_sync(event, cb)
            except Exception as exc:
                team_logger.warning("otel: failed to unregister {} - {}", event, exc)
    _registered.clear()

    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception as exc:
            team_logger.warning("otel: provider shutdown failed - {}", exc)
        _provider = None

    _callback_handler = None
    _monitor_handler = None
    reset_all()


def get_tracer(name: str) -> Any:
    """Return a Tracer bound to the active observability provider.

    Falls back to the global TracerProvider when ``init_observability``
    has not been called. Subsystems (notably ObservabilityRail, whose
    instance is owned by user code) call this helper instead of going
    straight to ``trace.get_tracer`` so they see the per-init provider
    even when the global TracerProvider has been frozen by an earlier
    test run.
    """
    if _provider is not None:
        return _provider.get_tracer(name)
    return trace.get_tracer(name)


def attach_to_team_agent(team_agent: Any) -> None:
    """Register the monitor handler on a leader TeamAgent.

    Args:
        team_agent: A leader TeamAgent instance with ``add_event_listener``.
    """
    if _monitor_handler is None:
        team_logger.warning("attach_to_team_agent called before init_observability")
        return
    team_agent.add_event_listener(_monitor_handler)


def detach_from_team_agent(team_agent: Any) -> None:
    """Reverse of ``attach_to_team_agent``."""
    if _monitor_handler is None:
        return
    team_agent.remove_event_listener(_monitor_handler)


def _build_exporter(config: ObservabilityConfig) -> SpanExporter:
    """Construct the exporter selected by the configuration."""
    if config.exporter == "console":
        return ConsoleSpanExporter()
    if config.exporter == "otlp_grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=config.endpoint, insecure=True)
    if config.exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        return HttpExporter(endpoint=config.endpoint)
    raise build_error(
        StatusCode.PARAM_INVALID_ERROR,
        reason=f"unsupported observability exporter: {config.exporter}",
    )


def _wire_callback_handlers(handler: OtelCallbackHandler) -> None:
    """Register all callback handlers against the framework singleton.

    Stores (event, bound_method) pairs in ``_registered`` so
    ``shutdown_observability`` can unwire them deterministically.
    """
    framework = _runner_callback_framework()
    if framework is None:
        team_logger.warning("otel: Runner.callback_framework unavailable; skipping wiring")
        return

    pairs: list[tuple[str, Callable[..., Any]]] = [
        (LLMCallEvents.LLM_INVOKE_INPUT, handler.on_llm_invoke_input),
        (LLMCallEvents.LLM_STREAM_INPUT, handler.on_llm_stream_input),
        (LLMCallEvents.LLM_STREAM_OUTPUT, handler.on_llm_stream_output),
        (LLMCallEvents.LLM_INVOKE_OUTPUT, handler.on_llm_invoke_output),
        (LLMCallEvents.LLM_CALL_ERROR, handler.on_llm_call_error),
        (ToolCallEvents.TOOL_CALL_STARTED, handler.on_tool_call_started),
        (ToolCallEvents.TOOL_CALL_FINISHED, handler.on_tool_call_finished),
        (ToolCallEvents.TOOL_CALL_ERROR, handler.on_tool_call_error),
        (AgentEvents.AGENT_INVOKE_INPUT, handler.on_agent_invoke_input),
        (AgentEvents.AGENT_INVOKE_OUTPUT, handler.on_agent_invoke_output),
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
