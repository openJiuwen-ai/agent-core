# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TrajectoryExtractor - trajectory extraction from session tracer."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.trajectory import TrajectoryExtractor
from openjiuwen.agent_evolving.trajectory.types import LLMCallDetail, ToolCallDetail


def make_span(
    invoke_type="llm",
    invoke_id="inv1",
    inputs=None,
    outputs=None,
    error=None,
    **kwargs
):
    """Factory for creating mock span."""
    span = MagicMock()
    span.invoke_type = invoke_type
    span.invoke_id = invoke_id
    span.inputs = inputs or {"inputs": {"query": "test"}}
    span.outputs = outputs or {"outputs": {"response": "test"}}
    span.error = error
    span.start_time = datetime.now(tz=timezone.utc)
    span.end_time = datetime.now(tz=timezone.utc)
    span.meta_data = {}
    span.operator_id = "test_op"
    span.llm_call_id = None
    span.name = "test_span"
    span.parent_invoke_id = None
    span.child_invokes_id = None
    span.agent_id = None
    for key, value in kwargs.items():
        setattr(span, key, value)
    return span


def make_session_with_tracer(agent_spans=None, workflow_spans=None):
    """Create mock session with tracer configured."""
    tracer = MagicMock()
    tracer.tracer_agent_span_manager.get_all_spans.return_value = (
        agent_spans or []
    )
    tracer.tracer_workflow_span_manager_dict = workflow_spans or None

    session = MagicMock()
    session.tracer.return_value = tracer
    return session


class TestTrajectoryExtractor:
    """Test TrajectoryExtractor.extract method via public API."""

    @staticmethod
    def test_extract_no_tracer():
        """Handles session without tracer."""
        extractor = TrajectoryExtractor()
        session = MagicMock()
        session.tracer = MagicMock(return_value=None)

        result = extractor.extract(session, case_id="case1")

        assert result.case_id == "case1"
        assert result.steps == []
        assert not hasattr(result, "trace_id")
        assert not hasattr(result, "edges")

    @staticmethod
    def test_extract_tracer_no_agent_manager():
        """Handles tracer without agent span manager."""
        extractor = TrajectoryExtractor()
        session = make_session_with_tracer()
        session.tracer.return_value.tracer_agent_span_manager = None

        result = extractor.extract(session, case_id="case1")

        assert result.case_id == "case1"
        assert result.steps == []

    @staticmethod
    def test_extract_llm_span():
        """Extracts LLM span correctly."""
        extractor = TrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        step = result.steps[0]
        assert step.kind == "llm"
        assert step.meta.get("operator_id") == "test_op"

    @staticmethod
    def test_extract_plugin_span_as_tool():
        """Plugin invoke type maps to tool kind."""
        extractor = TrajectoryExtractor()
        span = make_span(invoke_type="plugin", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        assert result.steps[0].kind == "tool"

    @staticmethod
    def test_extract_with_error():
        """Captures error from span."""
        extractor = TrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1", error="Test error")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        assert result.steps[0].error == "Test error"

    @staticmethod
    def test_extract_nested_inputs_outputs():
        """Handles nested inputs/outputs with 'inputs'/'outputs' wrapper."""
        extractor = TrajectoryExtractor()
        span = make_span(
            invoke_type="llm",
            invoke_id="inv1",
            on_invoke_data=[{
                "llm_params": {
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "nested"}],
                }
            }],
        )
        span.inputs = {"inputs": {"query": "nested"}}
        span.outputs = {"outputs": {"response": "nested"}}
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        step = result.steps[0]
        assert step.detail is not None
        assert isinstance(step.detail, LLMCallDetail)
        assert step.detail.messages == [{"role": "user", "content": "nested"}]

    @staticmethod
    def test_extract_uses_llm_call_id_as_operator_id():
        """Uses llm_call_id when operator_id is missing."""
        extractor = TrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.operator_id = None
        span.llm_call_id = "llm_call_1"
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        assert result.steps[0].meta.get("operator_id") == "llm_call_1"

    @staticmethod
    def test_extract_tool_span_with_detail():
        """Extracts Tool span with detail containing call_args/call_result."""
        extractor = TrajectoryExtractor()
        span = make_span(
            invoke_type="plugin",
            invoke_id="inv1",
            name="test_tool",
            inputs={"inputs": {"arg": "value"}},
            outputs={"outputs": {"result": "success"}},
        )
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        step = result.steps[0]
        assert step.kind == "tool"
        assert step.detail is not None
        assert isinstance(step.detail, ToolCallDetail)
        assert step.detail.tool_name == "test_tool"
        assert step.detail.call_args == {"arg": "value"}
        assert step.detail.call_result == {"result": "success"}

    @staticmethod
    def test_extract_llm_span_with_meta_backup():
        """Non-LLM/Tool steps have I/O in meta as backup."""
        extractor = TrajectoryExtractor()
        span = make_span(
            invoke_type="memory",
            invoke_id="inv1",
            inputs={"inputs": {"key": "value"}},
            outputs={"outputs": {"result": "data"}},
        )
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        step = result.steps[0]
        assert step.kind == "memory"
        assert step.meta.get("inputs") == {"key": "value"}
        assert step.meta.get("outputs") == {"result": "data"}


class TestDtToMs:
    """Test _dt_to_ms helper function via public API."""

    @staticmethod
    def test_dt_to_ms_none():
        """None input returns None via public API."""
        extractor = TrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.start_time = None
        span.end_time = None
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        assert result.steps[0].start_time_ms is None
        assert result.steps[0].end_time_ms is None

    @staticmethod
    def test_dt_to_ms_valid_datetime():
        """Valid datetime converts to milliseconds via public API."""
        extractor = TrajectoryExtractor()
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.start_time = dt
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, case_id="case1")

        assert len(result.steps) == 1
        expected = int(dt.timestamp() * 1000)
        assert result.steps[0].start_time_ms == expected
