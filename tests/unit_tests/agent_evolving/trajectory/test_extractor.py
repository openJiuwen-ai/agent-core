# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TracerTrajectoryExtractor - trajectory extraction from session tracer."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.trajectory import ExecutionSpec, TracerTrajectoryExtractor


def make_span(invoke_type="llm", invoke_id="inv1", inputs=None, outputs=None, error=None, **kwargs):
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
    for key, value in kwargs.items():
        setattr(span, key, value)
    return span


def make_execution_spec(case_id="case1", exec_id="exec1"):
    """Create execution spec."""
    return ExecutionSpec(case_id=case_id, execution_id=exec_id)


def make_session_with_tracer(agent_spans=None, workflow_spans=None):
    """Create mock session with tracer configured."""
    tracer = MagicMock()
    tracer.tracer_agent_span_manager.get_all_spans.return_value = agent_spans or []
    tracer.tracer_workflow_span_manager_dict = workflow_spans or None

    session = MagicMock()
    session.tracer.return_value = tracer
    return session


class TestTracerTrajectoryExtractor:
    """Test TracerTrajectoryExtractor.extract method via public API."""

    @staticmethod
    def test_extract_no_tracer():
        """Handles session without tracer."""
        extractor = TracerTrajectoryExtractor()
        session = MagicMock()
        session.tracer = MagicMock(return_value=None)

        result = extractor.extract(session, make_execution_spec())

        assert result.case_id == "case1"
        assert result.steps == []

    @staticmethod
    def test_extract_tracer_no_agent_manager():
        """Handles tracer without agent span manager."""
        extractor = TracerTrajectoryExtractor()
        session = make_session_with_tracer()
        session.tracer.return_value.tracer_agent_span_manager = None

        result = extractor.extract(session, make_execution_spec())

        assert result.case_id == "case1"
        assert result.steps == []

    @staticmethod
    def test_extract_llm_span():
        """Extracts LLM span correctly."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].kind == "llm"
        assert result.steps[0].operator_id == "test_op"

    @staticmethod
    def test_extract_plugin_span_as_tool():
        """Plugin invoke type maps to tool kind."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="plugin", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].kind == "tool"

    @staticmethod
    def test_extract_workflow_span():
        """Extracts workflow span."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        wf_tracer = MagicMock()
        wf_tracer.get_all_spans.return_value = []
        session = make_session_with_tracer(agent_spans=[span], workflow_spans={"wf1": wf_tracer})

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1

    @staticmethod
    def test_extract_chain_span_as_agent():
        """Chain invoke type maps to agent kind."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="chain", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].kind == "agent"

    @staticmethod
    def test_extract_memory_span():
        """Memory invoke type."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="memory", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].kind == "memory"

    @staticmethod
    def test_extract_workflow_invoke_type():
        """Workflow invoke type."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="workflow", invoke_id="inv1")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].kind == "workflow"

    @staticmethod
    def test_extract_with_edge_parent_invoke():
        """Creates edge based on parent_invoke_id."""
        extractor = TracerTrajectoryExtractor()
        parent_span = make_span(invoke_type="llm", invoke_id="parent1")
        child_span = make_span(invoke_type="tool", invoke_id="child1", parent_invoke_id="parent1")
        session = make_session_with_tracer(agent_spans=[parent_span, child_span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 2
        assert result.edges is not None
        assert len(result.edges) >= 1

    @staticmethod
    def test_extract_with_child_invokes():
        """Creates edge based on child_invokes_id."""
        extractor = TracerTrajectoryExtractor()
        parent_span = make_span(invoke_type="llm", invoke_id="parent1", child_invokes_id=["child1"])
        child_span = make_span(invoke_type="tool", invoke_id="child1")
        session = make_session_with_tracer(agent_spans=[parent_span, child_span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 2
        assert result.edges is not None

    @staticmethod
    def test_extract_with_error():
        """Captures error from span."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1", error="Test error")
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].error == "Test error"

    @staticmethod
    def test_extract_nested_inputs_outputs():
        """Handles nested inputs/outputs with 'inputs'/'outputs' wrapper."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.inputs = {"inputs": {"query": "nested"}}
        span.outputs = {"outputs": {"response": "nested"}}
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].inputs == {"query": "nested"}

    @staticmethod
    def test_extract_uses_llm_call_id_as_operator_id():
        """Uses llm_call_id when operator_id is missing."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.operator_id = None
        span.llm_call_id = "llm_call_1"
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].operator_id == "llm_call_1"

    @staticmethod
    def test_extract_with_meta_node_id():
        """Extracts node_id from meta."""
        extractor = TracerTrajectoryExtractor()
        span = make_span(invoke_type="llm", invoke_id="inv1")
        span.meta_data = {"node_id": "node_123"}
        span.operator_id = None
        session = make_session_with_tracer(agent_spans=[span])

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 1
        assert result.steps[0].node_id == "node_123"

    @staticmethod
    def test_extract_workflow_span_with_metadata():
        """Extracts workflow span with full metadata."""
        extractor = TracerTrajectoryExtractor()
        agent_span = make_span(invoke_type="llm", invoke_id="inv1")

        wf_span = MagicMock()
        wf_span.invoke_type = None
        wf_span.inputs = {}
        wf_span.outputs = {}
        wf_span.error = None
        wf_span.start_time = datetime.now(tz=timezone.utc)
        wf_span.end_time = datetime.now(tz=timezone.utc)
        wf_span.workflow_id = "wf_123"
        wf_span.workflow_name = "test_workflow"
        wf_span.component_id = "comp_1"
        wf_span.component_name = "TestComponent"
        wf_span.component_type = "action"
        wf_span.loop_node_id = None
        wf_span.loop_index = None
        wf_span.parent_node_id = None
        wf_span.meta_data = {}

        wf_tracer = MagicMock()
        wf_tracer.get_all_spans.return_value = [wf_span]

        session = make_session_with_tracer(agent_spans=[agent_span], workflow_spans={"wf1": wf_tracer})

        result = extractor.extract(session, make_execution_spec())

        assert len(result.steps) == 2
        workflow_step = [s for s in result.steps if s.kind == "workflow"][0]
        assert workflow_step is not None
        assert workflow_step.node_id == "comp_1"


class TestDtToMs:
    """Test _dt_to_ms helper function via public API."""

    @staticmethod
    def test_dt_to_ms_none():
        """None input returns None."""
        from openjiuwen.agent_evolving.trajectory.operation import _dt_to_ms

        result = _dt_to_ms(None)
        assert result is None

    @staticmethod
    def test_dt_to_ms_valid_datetime():
        """Converts datetime to milliseconds."""
        from openjiuwen.agent_evolving.trajectory.operation import _dt_to_ms

        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = _dt_to_ms(dt)
        expected = int(dt.timestamp() * 1000)
        assert result == expected
