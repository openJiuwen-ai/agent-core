# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tracer_otel handlers (OtelAgentHandler, OtelWorkflowHandler).

Uses InMemorySpanExporter to verify span creation, attributes, parent-child
relationships, and error marking.
"""

import pytest

from opentelemetry import trace

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.tracer.data import InvokeType
from openjiuwen.core.session.tracer.span import TraceAgentSpan, SpanManager
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_COMPLETION,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_SYSTEM_VALUE,
    OJ_AGENT_ERROR_MESSAGE,
    OJ_AGENT_INPUTS,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_CHILD_INVOKE_IDS,
    OJ_ELAPSED_TIME,
    OJ_END_TIME,
    OJ_ERROR,
    OJ_INVOKE_ID,
    OJ_META_DATA,
    OJ_PARENT_INVOKE_ID,
    OJ_PARENT_NODE_ID,
    OJ_START_TIME,
    OJ_STATUS,
    OJ_STREAM_INPUTS,
    OJ_STREAM_OUTPUTS,
    OJ_TRACE_ID,
    OJ_WORKFLOW_COMPONENT_ID,
    OJ_WORKFLOW_COMPONENT_TYPE,
    OJ_WORKFLOW_ERROR_MESSAGE,
    OJ_WORKFLOW_ID,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_registry_and_exporter():
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()
    yield
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()


# ---------------------------------------------------------------------------
# OtelAgentHandler tests
# ---------------------------------------------------------------------------


class TestOtelAgentHandler:
    async def test_agent_llm_start_creates_span(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(
            span=agent_span,
            inputs={"prompt": "hello"},
            instance_info={"class_name": "TestModel"},
        )

        # Span not ended yet — still active
        assert len(_EXPORTER.get_finished_spans()) == 0

        # End the span
        await handler.on_llm_end(span=agent_span, outputs={"response": "world"})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        assert s.name == "llm.TestModel"
        assert s.kind == trace.SpanKind.CLIENT
        assert s.attributes[GEN_AI_SYSTEM] == GEN_AI_SYSTEM_VALUE
        assert s.attributes[GEN_AI_REQUEST_MODEL] == "TestModel"
        assert s.attributes[GEN_AI_OPERATION_NAME] == "chat"
        # Span base attributes (field-completion)
        assert OJ_TRACE_ID in s.attributes
        assert OJ_INVOKE_ID in s.attributes
        assert OJ_PARENT_INVOKE_ID in s.attributes
        assert s.status.status_code == trace.StatusCode.OK

    async def test_agent_llm_end_closes_span(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs="hello", instance_info={"class_name": "M"})
        await handler.on_llm_end(span=agent_span, outputs="world")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[GEN_AI_COMPLETION] == "world"

        # End-time base attributes should be present
        s = finished[0]
        assert OJ_STATUS in s.attributes
        assert OJ_INVOKE_ID in s.attributes
        assert OJ_PARENT_INVOKE_ID in s.attributes
        # end_time / elapsed_time / child_invoke_ids only set when span has values
        assert OJ_TRACE_ID in s.attributes

        # Span should be popped from manager
        assert handler._span_manager.get(agent_span.invoke_id) is None

    async def test_agent_llm_error_marks_error(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs=None, instance_info={"class_name": "M"})
        error = RuntimeError("test error")
        await handler.on_llm_error(span=agent_span, error=error)

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        assert s.status.status_code == trace.StatusCode.ERROR
        assert s.attributes[OJ_AGENT_ERROR_MESSAGE] == "test error"
        # Error dict should be present (non-BaseError → fallback error_code)
        assert OJ_ERROR in s.attributes
        assert OJ_STATUS in s.attributes
        assert s.attributes[OJ_STATUS] == "error"

        # Span should be popped from manager
        assert handler._span_manager.get(agent_span.invoke_id) is None

    async def test_agent_parent_child_relation(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        parent_span = span_manager.create_agent_span()
        child_span = span_manager.create_agent_span(parent_span)

        await handler.on_chain_start(
            span=parent_span, inputs=None, instance_info={"class_name": "ParentChain"},
        )
        await handler.on_plugin_start(
            span=child_span, inputs=None, instance_info={"class_name": "ChildTool"},
        )
        await handler.on_plugin_end(span=child_span, outputs="result")
        await handler.on_chain_end(span=parent_span, outputs="done")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 2

        child = next(s for s in finished if s.name == "tool.ChildTool")
        parent = next(s for s in finished if s.name == "chain.ParentChain")

        # Child's parent should point to parent's span_id
        assert child.parent.span_id == parent.context.span_id

    async def test_agent_non_llm_internal_span(self):
        """Plugin, chain, prompt, retriever, evaluator, workflow → INTERNAL span."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()
        # Simulate built-in handler setting invoke_type and name
        agent_span.invoke_type = "plugin"
        agent_span.name = "MyTool"

        await handler.on_plugin_start(
            span=agent_span, inputs=None, instance_info={"class_name": "MyTool"},
        )
        await handler.on_plugin_end(span=agent_span, outputs="ok")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].kind == trace.SpanKind.INTERNAL
        assert finished[0].attributes[OJ_AGENT_INVOKE_TYPE] == "plugin"
        assert finished[0].attributes[OJ_AGENT_NAME] == "MyTool"
        # Base attributes present
        assert OJ_INVOKE_ID in finished[0].attributes
        assert OJ_TRACE_ID in finished[0].attributes

    async def test_agent_llm_prompt_redaction(self):
        """When redaction_enabled=True, inputs are hashed."""
        config = OtelTracerConfig(redaction_enabled=True)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs="secret data", instance_info={"class_name": "M"})
        await handler.on_llm_end(span=agent_span, outputs="secret response")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[GEN_AI_PROMPT].startswith("sha256:")
        assert finished[0].attributes[GEN_AI_COMPLETION].startswith("sha256:")

    async def test_agent_llm_prompt_completion_split_redaction(self):
        """redact_prompts=False, redact_completions=True: prompt not hashed, completion hashed."""
        config = OtelTracerConfig(redaction_enabled=True, redact_prompts=False, redact_completions=True)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs="visible prompt", instance_info={"class_name": "M"})
        await handler.on_llm_end(span=agent_span, outputs="secret response")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        # Prompt not hashed (redact_prompts=False overrides redaction_enabled=True)
        assert not finished[0].attributes[GEN_AI_PROMPT].startswith("sha256:")
        # Completion hashed (redact_completions=True)
        assert finished[0].attributes[GEN_AI_COMPLETION].startswith("sha256:")

    async def test_agent_llm_fields_set_when_span_empty(self):
        """When TraceAgentSpan.invoke_type/name are None, handler sets them itself."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        # Create span WITHOUT setting invoke_type/name (simulates OTel handler running first)
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(
            span=agent_span,
            inputs="hello",
            instance_info={"class_name": "TestLLM"},
        )
        await handler.on_llm_end(span=agent_span, outputs="world")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        # Handler should set invoke_type = "llm" when span.invoke_type is None
        assert s.attributes[OJ_AGENT_INVOKE_TYPE] == InvokeType.LLM.value
        # Handler should set name = instance_info.class_name when span.name is None
        assert s.attributes[OJ_AGENT_NAME] == "TestLLM"
        # start_time, end_time, elapsed_time always set (handler-generated when None)
        assert OJ_START_TIME in s.attributes
        assert OJ_END_TIME in s.attributes
        assert OJ_ELAPSED_TIME in s.attributes
        # meta_data set from instance_info when span.meta_data is None
        assert OJ_META_DATA in s.attributes

    async def test_agent_chain_fields_set_when_span_empty(self):
        """Chain: invoke_type = "chain" when span.invoke_type is None."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_chain_start(
            span=agent_span,
            inputs=None,
            instance_info={"class_name": "TestChain"},
        )
        await handler.on_chain_end(span=agent_span, outputs="done")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        assert s.attributes[OJ_AGENT_INVOKE_TYPE] == InvokeType.CHAIN.value
        assert s.attributes[OJ_AGENT_NAME] == "TestChain"

    async def test_agent_plugin_fields_set_when_span_empty(self):
        """Plugin: invoke_type = "plugin" when span.invoke_type is None."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_plugin_start(
            span=agent_span,
            inputs=None,
            instance_info={"class_name": "TestTool"},
        )
        await handler.on_plugin_end(span=agent_span, outputs="ok")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[OJ_AGENT_INVOKE_TYPE] == InvokeType.PLUGIN.value
        assert finished[0].attributes[OJ_AGENT_NAME] == "TestTool"

    async def test_agent_fields_use_span_value_when_present(self):
        """When span already has invoke_type/name (built-in handler ran first), use those values."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()
        # Simulate built-in handler setting invoke_type/name
        agent_span.invoke_type = "llm"
        agent_span.name = "MyLLMModel"

        await handler.on_llm_start(
            span=agent_span,
            inputs="hello",
            instance_info={"class_name": "TestLLM"},
        )
        await handler.on_llm_end(span=agent_span, outputs="world")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        # Use span.invoke_type = "llm", not the handler-generated value
        assert s.attributes[OJ_AGENT_INVOKE_TYPE] == "llm"
        # Use span.name = "MyLLMModel", not instance_info.class_name
        assert s.attributes[OJ_AGENT_NAME] == "MyLLMModel"


# ---------------------------------------------------------------------------
# OtelWorkflowHandler tests
# ---------------------------------------------------------------------------


class TestOtelWorkflowHandler:
    async def test_workflow_call_start_creates_span(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        # Root workflow root span
        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "MyWorkflow", "workflow_version": "1.0"},
            inputs={"input": "test"},
            parent_node_id="",
        )

        # Span not finished yet
        assert len(_EXPORTER.get_finished_spans()) == 0

        await handler.on_call_done(invoke_id="wf_root", outputs={"output": "done"})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        assert s.name == "wf_root"
        assert s.attributes[GEN_AI_SYSTEM] == GEN_AI_SYSTEM_VALUE
        assert s.attributes[OJ_WORKFLOW_ID] == "wf1"
        # Base attributes (field-completion)
        assert OJ_TRACE_ID in s.attributes
        assert OJ_INVOKE_ID in s.attributes
        assert OJ_PARENT_NODE_ID in s.attributes
        assert OJ_START_TIME in s.attributes
        assert OJ_STATUS in s.attributes
        assert s.status.status_code == trace.StatusCode.OK

    async def test_workflow_call_done_closes_span(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "WF"},
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1

        # Span should be popped from manager
        assert handler._span_manager.get("wf_root") is None

    async def test_workflow_component_type_mapping(self):
        """LLM component → CLIENT span kind, other → INTERNAL."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        # Root workflow span
        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "WF"},
            parent_node_id="",
        )

        # LLM component
        await handler.on_call_start(
            invoke_id="llm_node",
            metadata={
                "component_id": "llm_node",
                "component_type": "LLM",
                "component_name": "llm",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="llm_node")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        llm_span = next(s for s in finished if s.name == "component.llm_node")
        assert llm_span.kind == trace.SpanKind.CLIENT

    async def test_workflow_non_llm_internal_span(self):
        """Non-LLM component → INTERNAL span kind."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler2 = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler2.on_call_start(
            invoke_id="wf_root2",
            metadata={"workflow_id": "wf2"},
            parent_node_id="",
        )
        await handler2.on_call_start(
            invoke_id="start_node",
            metadata={
                "component_id": "start_node",
                "component_type": "Start",
                "component_name": "start",
                "workflow_id": "wf2",
            },
            parent_node_id="",
        )
        await handler2.on_call_done(invoke_id="start_node")
        await handler2.on_call_done(invoke_id="wf_root2")

        finished = _EXPORTER.get_finished_spans()
        start_span = next(s for s in finished if s.name == "component.start_node")
        assert start_span.kind == trace.SpanKind.INTERNAL

    async def test_workflow_attributes_set(self):
        """Workflow component attributes are correctly set on the span."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "MyWF", "workflow_version": "2.0"},
            parent_node_id="",
        )
        await handler.on_call_start(
            invoke_id="comp1",
            metadata={
                "component_id": "comp1",
                "component_type": "Tool",
                "component_name": "tool1",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="comp1")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        comp_span = next(s for s in finished if s.name == "component.comp1")
        assert comp_span.attributes[OJ_WORKFLOW_COMPONENT_ID] == "comp1"
        assert comp_span.attributes[OJ_WORKFLOW_COMPONENT_TYPE] == "Tool"

    async def test_workflow_invoke_error_marks_error(self):
        """on_invoke with exception → ERROR status and span end."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1"},
            parent_node_id="",
        )
        await handler.on_invoke(
            invoke_id="wf_root",
            exception=RuntimeError("wf failed"),
        )

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        assert s.status.status_code == trace.StatusCode.ERROR
        assert s.attributes[OJ_WORKFLOW_ERROR_MESSAGE] == "wf failed"
        # Error dict and status
        assert OJ_ERROR in s.attributes
        assert s.attributes[OJ_STATUS] == "error"

    async def test_workflow_invoke_buffers_data(self):
        """on_invoke_data is buffered and flushed as span attribute on on_call_done."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="comp1",
            metadata={"component_id": "comp1", "component_type": "Tool", "workflow_id": "wf1"},
            parent_node_id="",
        )
        await handler.on_invoke(invoke_id="comp1", on_invoke_data={"step": 1})
        await handler.on_invoke(invoke_id="comp1", on_invoke_data={"step": 2})
        await handler.on_call_done(invoke_id="comp1")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert OJ_AGENT_INPUTS in finished[0].attributes

    async def test_workflow_stream_buffers_flushed(self):
        """stream_inputs and stream_outputs are buffered and flushed as span attributes."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="comp1",
            metadata={"component_id": "comp1", "component_type": "LLM", "workflow_id": "wf1"},
            parent_node_id="",
        )
        await handler.on_pre_stream(invoke_id="comp1", chunk={"text": "hello"})
        await handler.on_pre_stream(invoke_id="comp1", chunk={"text": "world"})
        await handler.on_post_stream(invoke_id="comp1", chunk={"text": "response"})
        await handler.on_call_done(invoke_id="comp1")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert OJ_STREAM_INPUTS in finished[0].attributes
        assert OJ_STREAM_OUTPUTS in finished[0].attributes

    async def test_exception_safety(self):
        """Handler exceptions do not propagate to business flow."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        # on_llm_end with a span that was never started should be silently handled
        await handler.on_llm_end(span=agent_span, outputs="test")
        # No exception raised, test passes

    async def test_workflow_sub_workflow_parent_child(self):
        """Sub-workflow root span's parent = host component span."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        # 1. Root workflow root span
        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "RootWF"},
            parent_node_id="",
        )

        # 2. Host component (llm_node) under root workflow
        await handler.on_call_start(
            invoke_id="llm_node_exec",
            metadata={
                "component_id": "llm_node",
                "component_type": "LLM",
                "component_name": "llm",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )

        # 3. Sub-workflow root under llm_node (parent_node_id = llm_node's node_id)
        await handler.on_call_start(
            invoke_id="sub_wf_root",
            metadata={"workflow_id": "wf2", "workflow_name": "SubWF"},
            parent_node_id="llm_node",
        )

        # 4. End all spans
        await handler.on_call_done(invoke_id="sub_wf_root")
        await handler.on_call_done(invoke_id="llm_node_exec")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 3

        root_wf = next(s for s in finished if s.name == "wf_root")
        host_comp = next(s for s in finished if s.name == "component.llm_node_exec")
        sub_wf = next(s for s in finished if s.name == "sub_wf_root")

        # root_wf has no parent (or zero span_id parent)
        assert root_wf.parent is None or root_wf.parent.span_id == 0

        # host_comp's parent = root_wf
        assert host_comp.parent.span_id == root_wf.context.span_id

        # sub_wf's parent = host_comp (via _component_spans lookup)
        assert sub_wf.parent.span_id == host_comp.context.span_id

    async def test_workflow_component_inside_sub_workflow_parent(self):
        """Component inside sub-workflow resolves parent to host component span
        via _component_spans fallback (sub-workflows don't trigger trace_workflow_start)."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        # 1. Root workflow root span
        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1", "workflow_name": "RootWF"},
            parent_node_id="",
        )

        # 2. Host component (sub_wf_node) under root workflow
        await handler.on_call_start(
            invoke_id="sub_wf_node_exec",
            metadata={
                "component_id": "sub_wf_node",
                "component_type": "sub_workflow",
                "component_name": "sub_wf",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )

        # 3. Start node inside sub-workflow
        #    parent_node_id = "sub_wf_node" (SubWorkflowSession.executable_id())
        #    This is the exact scenario where _resolve_parent_context used to return None
        #    because _layer_root_spans has no key "sub_wf_node".
        #    Now it falls back to _component_spans["sub_wf_node"].
        await handler.on_call_start(
            invoke_id="sub_wf_node.start",
            metadata={
                "component_id": "start",
                "component_type": "Start",
                "component_name": "start",
                "workflow_id": "wf2",
            },
            parent_node_id="sub_wf_node",
        )

        # 4. LLM node inside sub-workflow (same parent_node_id)
        await handler.on_call_start(
            invoke_id="sub_wf_node.llm",
            metadata={
                "component_id": "llm",
                "component_type": "LLM",
                "component_name": "llm",
                "workflow_id": "wf2",
            },
            parent_node_id="sub_wf_node",
        )

        # 5. End all spans in reverse order
        await handler.on_call_done(invoke_id="sub_wf_node.llm")
        await handler.on_call_done(invoke_id="sub_wf_node.start")
        await handler.on_call_done(invoke_id="sub_wf_node_exec")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 4

        root_wf = next(s for s in finished if s.name == "wf_root")
        host_comp = next(s for s in finished if s.name == "component.sub_wf_node_exec")
        start_node = next(s for s in finished if s.name == "component.sub_wf_node.start")
        llm_node = next(s for s in finished if s.name == "component.sub_wf_node.llm")

        # root_wf — top level
        assert root_wf.parent is None or root_wf.parent.span_id == 0

        # host_comp's parent = root_wf
        assert host_comp.parent.span_id == root_wf.context.span_id

        # start_node and llm_node inside sub-workflow → parent = host_comp
        # (via _component_spans fallback, not _layer_root_spans)
        assert start_node.parent.span_id == host_comp.context.span_id
        assert llm_node.parent.span_id == host_comp.context.span_id