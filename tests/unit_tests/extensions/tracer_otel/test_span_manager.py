# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tracer_otel span manager."""

import pytest

from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    OJ_TRACE_ID,
)
from openjiuwen.extensions.tracer_otel.span_manager import (
    OtelAgentSpanManager,
    OtelSpanState,
    OtelWorkflowSpanManager,
)


class _MockSpan:
    """Minimal mock for opentelemetry.trace.Span."""

    def __init__(self, name: str = "mock"):
        self.name = name
        self._ended = False

    def end(self):
        self._ended = True

    def is_recording(self) -> bool:
        return True


class _MockToken:
    """Minimal mock for otel context attach token."""

    pass


class TestOtelAgentSpanManager:
    def test_push_and_get(self):
        mgr = OtelAgentSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        assert mgr.get("inv1") is state

    def test_pop_removes_mapping(self):
        mgr = OtelAgentSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        popped = mgr.pop("inv1")
        assert popped is state
        assert mgr.get("inv1") is None

    def test_pop_nonexistent_returns_none(self):
        mgr = OtelAgentSpanManager()
        assert mgr.pop("nonexistent") is None

    def test_get_nonexistent_returns_none(self):
        mgr = OtelAgentSpanManager()
        assert mgr.get("nonexistent") is None

    def test_parent_context_resolution(self):
        """Verify that parent invoke_id lookup works for parent-child span linkage."""
        mgr = OtelAgentSpanManager()
        parent_state = OtelSpanState(span=_MockSpan("parent"), context_token=_MockToken(), invoke_id="parent_inv")
        mgr.push("parent_inv", parent_state)
        assert mgr.get("parent_inv") is parent_state

        # After parent is popped, child can no longer find parent
        mgr.pop("parent_inv")
        assert mgr.get("parent_inv") is None

    def test_has_open_tool_span_matches_rail_flag_without_call_id(self):
        """Id-less tool calls (no ToolCall object / empty id): the rail span
        carries no gen_ai.tool.call.id, so identification must rely on the
        from_tool_rail flag — attribute presence would silently skip the
        dedupe and re-emit the duplicate span (bug #2046)."""
        mgr = OtelAgentSpanManager()

        def _push(invoke_id: str, attrs: dict, from_rail: bool) -> None:
            span = _MockSpan()
            span.attributes = attrs
            state = OtelSpanState(span=span, context_token=_MockToken(), invoke_id=invoke_id)
            state.from_tool_rail = from_rail
            mgr.push(invoke_id, state)

        # Rail span WITHOUT gen_ai.tool.call.id — still matched.
        _push("inv-rail", {GEN_AI_TOOL_NAME: "get_weather", OJ_TRACE_ID: "trace-1"}, True)
        assert mgr.has_open_tool_span("get_weather", "trace-1") is True
        # Different tool name or trace id does not match.
        assert mgr.has_open_tool_span("other_tool", "trace-1") is False
        assert mgr.has_open_tool_span("get_weather", "trace-2") is False
        # Unflagged span (legacy decorator path) never matches, even with an id.
        _push(
            "inv-deco",
            {GEN_AI_TOOL_NAME: "get_weather", OJ_TRACE_ID: "trace-1", GEN_AI_TOOL_CALL_ID: "call-1"},
            False,
        )
        mgr.pop("inv-rail")
        assert mgr.has_open_tool_span("get_weather", "trace-1") is False


class TestOtelWorkflowSpanManager:
    def test_push_and_get(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        assert mgr.get("inv1") is state

    def test_pop_removes_mapping_and_buffers(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        mgr.append_on_invoke_data("inv1", {"step": 1})
        mgr.append_stream_input("inv1", {"chunk": "a"})
        mgr.append_stream_output("inv1", {"chunk": "b"})

        popped = mgr.pop("inv1")
        assert popped is state
        assert mgr.get("inv1") is None
        assert mgr.get_on_invoke_data("inv1") == []
        assert mgr.get_stream_inputs("inv1") == []
        assert mgr.get_stream_outputs("inv1") == []

    def test_pop_nonexistent_returns_none(self):
        mgr = OtelWorkflowSpanManager()
        assert mgr.pop("nonexistent") is None

    # --- Incremental data buffers ---

    def test_append_on_invoke_data(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        mgr.append_on_invoke_data("inv1", {"step": 1})
        mgr.append_on_invoke_data("inv1", {"step": 2})
        data = mgr.get_on_invoke_data("inv1")
        assert data == [{"step": 1}, {"step": 2}]

    def test_get_on_invoke_data_nonexistent_returns_empty(self):
        mgr = OtelWorkflowSpanManager()
        assert mgr.get_on_invoke_data("nonexistent") == []

    def test_append_stream_input(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        mgr.append_stream_input("inv1", {"text": "hello"})
        mgr.append_stream_input("inv1", {"text": "world"})
        assert mgr.get_stream_inputs("inv1") == [{"text": "hello"}, {"text": "world"}]

    def test_append_stream_output(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        mgr.append_stream_output("inv1", {"result": "ok"})
        assert mgr.get_stream_outputs("inv1") == [{"result": "ok"}]

    def test_buffer_is_empty_after_push(self):
        mgr = OtelWorkflowSpanManager()
        state = OtelSpanState(span=_MockSpan(), context_token=_MockToken(), invoke_id="inv1")
        mgr.push("inv1", state)
        assert mgr.get_on_invoke_data("inv1") == []
        assert mgr.get_stream_inputs("inv1") == []
        assert mgr.get_stream_outputs("inv1") == []