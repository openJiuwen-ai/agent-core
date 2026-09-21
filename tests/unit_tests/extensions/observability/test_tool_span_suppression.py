# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A tool the caller records itself must not be recorded again here."""

from __future__ import annotations

import asyncio
from typing import Any

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.semconv import GEN_AI_TOOL_NAME
from openjiuwen.extensions.observability.span_context import (
    clear_root_span,
    reset_state,
    set_current_agent_span,
    set_root_span,
    suppressed_tool_spans,
    tool_spans_suppressed,
)
from tests.test_logger import logger

_SESSION_ID = "tool-span-suppression-session"


def _run_tool(handler: OtelCallbackHandler, tool_name: str) -> None:
    """Drive one tool call through the global callbacks."""

    async def call() -> None:
        await handler.on_tool_call_started(tool_name=tool_name, tool_id=f"team.{tool_name}", inputs={"to": "leader"})
        await handler.on_tool_call_finished(tool_name=tool_name, tool_id=f"team.{tool_name}", result="ok")

    asyncio.run(call())


def _tool_names(exporter: InMemorySpanExporter) -> list[Any]:
    return [
        span.attributes[GEN_AI_TOOL_NAME]
        for span in exporter.get_finished_spans()
        if span.attributes.get(GEN_AI_TOOL_NAME)
    ]


def test_a_suppressed_tool_is_not_recorded_in_this_context() -> None:
    exporter = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    config = ObservabilityConfig(enabled=True, service_name="tool-span-suppression-test", sample_rate=1.0)
    runtime.initialize(config, span_exporter_override=exporter)
    tracer = runtime.get_tracer("tool-span-suppression-test")
    root = tracer.start_span("team.demo")
    set_root_span(root, session_id=_SESSION_ID)
    set_current_agent_span(root)
    handler = OtelCallbackHandler(config, tracer=tracer)
    try:
        with suppressed_tool_spans("send_message"):
            _run_tool(handler, "send_message")
            # Only the named tool is covered; work the call dispatches counts.
            _run_tool(handler, "claim_task")
        _run_tool(handler, "send_message")
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id=_SESSION_ID, expected_span=root)
        set_current_agent_span(None)
        runtime.shutdown()
        reset_state()

    recorded = _tool_names(exporter)
    logger.info("recorded tool spans: {}", recorded)
    assert recorded == ["claim_task", "send_message"]


def test_suppression_is_scoped_to_its_context() -> None:
    assert not tool_spans_suppressed("send_message")
    with suppressed_tool_spans("send_message"):
        assert tool_spans_suppressed("send_message")
        assert not tool_spans_suppressed("view_task")
        with suppressed_tool_spans("view_task"):
            assert tool_spans_suppressed("send_message")
            assert tool_spans_suppressed("view_task")
        assert not tool_spans_suppressed("view_task")
    assert not tool_spans_suppressed("send_message")
