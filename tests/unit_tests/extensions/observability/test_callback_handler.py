# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import AgentEvents, LLMCallEvents, ToolCallEvents
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from openjiuwen.extensions.observability.span_context import (
    clear_root_span,
    reset_state,
    set_root_span,
)


async def _emit_callback_flow(framework, session) -> None:
    await framework.trigger(
        AgentEvents.AGENT_INVOKE_INPUT,
        {"user_input": "hello"},
        session=session,
    )
    await framework.trigger(
        LLMCallEvents.LLM_INVOKE_INPUT,
        messages=[{"role": "user", "content": "hello"}],
        model="fake",
    )
    await framework.trigger(
        ToolCallEvents.TOOL_CALL_STARTED,
        tool_name="search",
        tool_id="tool-1",
        inputs=((), {"q": "hello"}),
    )
    await framework.trigger(
        ToolCallEvents.TOOL_CALL_FINISHED,
        tool_name="search",
        tool_id="tool-1",
        result={"ok": True},
    )
    await framework.trigger(
        LLMCallEvents.LLM_INVOKE_OUTPUT,
        messages=[{"role": "user", "content": "hello"}],
        result=SimpleNamespace(
            content="done",
            reasoning_content="",
            finish_reason="stop",
            tool_calls=None,
            usage_metadata=None,
        ),
    )
    await framework.trigger(
        AgentEvents.AGENT_INVOKE_OUTPUT,
        {"agent_id": "agent"},
        session=session,
        result="done",
    )


@pytest.mark.asyncio
async def test_runtime_initialize_wires_global_callback_framework() -> None:
    exporters = [InMemorySpanExporter(), InMemorySpanExporter()]
    runtime = ObservabilityRuntime()
    config = ObservabilityConfig(enabled=True, service_name="callback-test", sample_rate=1.0)
    framework = Runner.callback_framework

    try:
        for exporter in exporters:
            runtime.initialize(config, span_exporter_override=exporter)
            root = runtime.get_tracer("callback-test").start_span("agent.root")
            set_root_span(root, session_id="session-1")
            session = SimpleNamespace(get_session_id=lambda: "session-1")
            try:
                await _emit_callback_flow(framework, session)
            finally:
                if root.is_recording():
                    root.end()
                clear_root_span(session_id="session-1", expected_span=root)
                runtime.shutdown()
                runtime.shutdown()
                reset_state()
    finally:
        runtime.shutdown()
        reset_state()

    names = [span.name for exporter in exporters for span in exporter.get_finished_spans()]
    assert names.count("llm.call") == 2
    assert names.count("tool.search") == 2
    assert names.count("agent.root") == 2
