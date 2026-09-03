# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tracer_otel handlers (OtelAgentHandler, OtelWorkflowHandler).

Uses InMemorySpanExporter to verify span creation, attributes, parent-child
relationships, and error marking.
"""

import json

import pytest

from opentelemetry import trace

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from openjiuwen.core.graph.pregel import GraphInterrupt, Interrupt
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.tracer.data import InvokeType, NodeStatus
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
    GEN_AI_TOOL_NAME,
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
    OJ_SESSION_ID,
    OJ_START_TIME,
    OJ_STATUS,
    OJ_STREAM_INPUTS,
    OJ_STREAM_OUTPUTS,
    OJ_TRACE_ID,
    OJ_WORKFLOW_COMPONENT_ID,
    OJ_WORKFLOW_COMPONENT_TYPE,
    OJ_WORKFLOW_ERROR_MESSAGE,
    OJ_WORKFLOW_ID,
    OJ_WORKFLOW_INVOKE_DATA,
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
        # Optional session IDs stay absent for legacy callers.
        assert OJ_SESSION_ID not in s.attributes
        assert s.status.status_code == trace.StatusCode.OK

    async def test_agent_span_contains_session_id_when_provided(self):
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        tracer = Tracer(session_id="session-123")
        tracer.init()
        agent_span = tracer.tracer_agent_span_manager.create_agent_span()

        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_llm_start",
            span=agent_span,
            inputs={"prompt": "hello"},
            instance_info={"class_name": "TestModel"},
        )
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_llm_end",
            span=agent_span,
            outputs={"response": "world"},
        )

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[OJ_SESSION_ID] == "session-123"

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

    async def test_agent_llm_inputs_normalized_to_dict(self):
        """Message objects are converted to plain dicts via model_dump().

        Without normalization, GEN_AI_PROMPT would contain class repr like
        ``SystemMessage(role='system', ...)``.  After normalization it should
        be standard JSON with ``{"role": "...", "content": "..."}`` entries.
        """
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        inputs = {
            "inputs": [
                SystemMessage(role="system", content="you are helpful"),
                UserMessage(role="user", content="hello"),
            ]
        }
        await handler.on_llm_start(span=agent_span, inputs=inputs, instance_info={"class_name": "M"})
        await handler.on_llm_end(span=agent_span, outputs="world")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        prompt_attr = finished[0].attributes[GEN_AI_PROMPT]

        # Should be valid JSON, not a Python repr
        parsed = json.loads(prompt_attr)
        assert "inputs" in parsed
        assert isinstance(parsed["inputs"], list)
        assert len(parsed["inputs"]) == 2

        # Each message is a plain dict — no class names in the output
        sys_msg = parsed["inputs"][0]
        assert sys_msg["role"] == "system"
        assert sys_msg["content"] == "you are helpful"
        user_msg = parsed["inputs"][1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "hello"
        # No Pydantic class repr leaked into the serialized form
        assert "SystemMessage" not in prompt_attr
        assert "UserMessage" not in prompt_attr

    async def test_agent_llm_outputs_normalized_to_dict(self):
        """AssistantMessage outputs are converted to plain dicts via model_dump()."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        span_manager = SpanManager("test-trace-id")
        agent_span = span_manager.create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs="hi", instance_info={"class_name": "M"})
        outputs = AssistantMessage(role="assistant", content="world")
        await handler.on_llm_end(span=agent_span, outputs=outputs)

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        completion_attr = finished[0].attributes[GEN_AI_COMPLETION]

        parsed = json.loads(completion_attr)
        assert parsed["role"] == "assistant"
        assert parsed["content"] == "world"
        assert "AssistantMessage" not in completion_attr

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

    async def test_agent_plugin_span_contains_session_id_when_provided(self):
        """Tool spans carry the session id stamped on the span by SpanManager."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)

        agent_span = SpanManager("test-trace-id", session_id="tool-session-123").create_agent_span()

        await handler.on_plugin_start(
            span=agent_span,
            inputs={"query": "hello"},
            instance_info={"class_name": "TestTool"},
        )
        await handler.on_plugin_end(span=agent_span, outputs="ok")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[OJ_SESSION_ID] == "tool-session-123"

    async def test_agent_span_session_id_survives_concurrent_handler_reuse(self):
        """A globally registered handler is shared across sessions; the span-carried
        session id must win over the last one injected via set_session_id()."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelAgentHandler(_OTEL_TRACER, config)
        handler.set_session_id("other-session")

        agent_span = SpanManager("test-trace-id", session_id="own-session").create_agent_span()

        await handler.on_llm_start(span=agent_span, inputs="hi", instance_info={"class_name": "M"})
        await handler.on_llm_end(span=agent_span, outputs="ok")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[OJ_SESSION_ID] == "own-session"

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
            session_id="workflow-session-123",
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
        assert s.attributes[OJ_SESSION_ID] == "workflow-session-123"
        assert OJ_INVOKE_ID in s.attributes
        assert OJ_PARENT_NODE_ID in s.attributes
        assert OJ_START_TIME in s.attributes
        # end_time / elapsed_time set by _set_workflow_end_attrs in on_call_done
        assert OJ_END_TIME in s.attributes
        assert OJ_ELAPSED_TIME in s.attributes
        assert OJ_STATUS in s.attributes
        assert s.status.status_code == trace.StatusCode.OK

    async def test_workflow_span_omits_session_id_when_absent(self):
        """Callers that never supply a session id keep the pre-existing attribute set."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1"},
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert OJ_SESSION_ID not in finished[0].attributes

    async def test_workflow_span_falls_back_to_injected_session_id(self):
        """Direct callers that omit the per-event id still get the tracer-injected one."""
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)
        handler.set_session_id("injected-session")

        await handler.on_call_start(
            invoke_id="wf_root",
            metadata={"workflow_id": "wf1"},
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].attributes[OJ_SESSION_ID] == "injected-session"

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

        # LLM component (component_type is type(executor).__name__, e.g. "LLMComponent")
        await handler.on_call_start(
            invoke_id="llm_node",
            metadata={
                "component_id": "llm_node",
                "component_type": "LLMComponent",
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
        # LLM components get gen_ai.operation.name="chat"
        assert llm_span.attributes[GEN_AI_OPERATION_NAME] == "chat"

    async def test_workflow_llm_compound_components_tagged_as_chat(self):
        """IntentDetection / Questioner internally invoke an LLM and are tagged
        with gen_ai.operation.name="chat" so backends can filter all LLM-invoking
        nodes. component_type comes from type(executor).__name__ at runtime.
        Also verifies the "LLM" substring path still matches "LLMComponent".
        """
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        for comp_type, invoke in [
            ("IntentDetectionComponent", "intent_node"),
            ("QuestionerComponent", "questioner_node"),
            ("LLMComponent", "llm_compound_node"),
            ("LLM", "llm_substring_node"),  # substring match path
        ]:
            await handler.on_call_start(
                invoke_id=invoke,
                metadata={
                    "component_id": invoke,
                    "component_type": comp_type,
                    "component_name": invoke,
                    "workflow_id": "wf1",
                },
                parent_node_id="",
            )
            await handler.on_call_done(invoke_id=invoke)

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 4
        for s in finished:
            assert s.kind == trace.SpanKind.CLIENT
            assert s.attributes[GEN_AI_OPERATION_NAME] == "chat"

    async def test_workflow_tool_component_tagged_as_execute_tool(self):
        """ToolExecutable → gen_ai.operation.name="execute_tool" (no gen_ai.tool.name).

        Tool 真名未通过 TracerWorkflowUtils._get_component_metadata 传递下来
        (metadata 中 component_name 实为 node_id)，故不设置 gen_ai.tool.name。
        component_type 用真实运行时值 "ToolExecutable" (type(executor).__name__)。
        """
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="tool_node",
            metadata={
                "component_id": "tool_node",
                "component_type": "ToolExecutable",
                "component_name": "tool_node",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )
        await handler.on_call_done(invoke_id="tool_node")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        # Tool components are not LLM → INTERNAL span kind
        assert s.kind == trace.SpanKind.INTERNAL
        assert s.attributes[GEN_AI_OPERATION_NAME] == "execute_tool"
        # gen_ai.tool.name intentionally not set (real tool name not in metadata)
        assert GEN_AI_TOOL_NAME not in s.attributes

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
        # end_time / elapsed_time set by _set_workflow_end_attrs in error path
        assert OJ_END_TIME in s.attributes
        assert OJ_ELAPSED_TIME in s.attributes

    async def test_workflow_invoke_graph_interrupt_marks_interrupted(self):
        """on_invoke with GraphInterrupt → OJ_STATUS=interrupted, NOT OTel ERROR.

        Mirrors builtin TraceWorkflowHandler: GraphInterrupt is a control-flow
        signal (e.g. QuestionerComponent pending user input), not a failure.
        The span must NOT carry StatusCode.ERROR / OJ_ERROR, but should still
        record the interrupt payload for debuggability and close cleanly.
        """
        config = OtelTracerConfig(redaction_enabled=False)
        handler = OtelWorkflowHandler(_OTEL_TRACER, config)

        await handler.on_call_start(
            invoke_id="questioner",
            metadata={"component_id": "questioner", "component_type": "QuestionerExecutable", "workflow_id": "wf1"},
            parent_node_id="",
        )
        interrupt_exc = GraphInterrupt(Interrupt({"wait_for": "user_input"}))
        await handler.on_invoke(
            invoke_id="questioner",
            exception=interrupt_exc,
        )

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        s = finished[0]
        # Interrupt is not a failure — OTel span status stays default (UNSET), not ERROR
        assert s.status.status_code != trace.StatusCode.ERROR
        # openjiuwen.status reflects interrupt semantics
        assert s.attributes[OJ_STATUS] == NodeStatus.INTERRUPTED.value
        # Must NOT write OJ_ERROR (would imply a real failure)
        assert OJ_ERROR not in s.attributes
        # Interrupt payload still recorded for debugging (non-error semantics)
        assert OJ_WORKFLOW_ERROR_MESSAGE in s.attributes
        # Span still closes cleanly with end attrs
        assert OJ_END_TIME in s.attributes
        assert OJ_ELAPSED_TIME in s.attributes

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
        assert OJ_WORKFLOW_INVOKE_DATA in finished[0].attributes

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
            invoke_id="llm_node",
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
        await handler.on_call_done(invoke_id="llm_node")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 3

        root_wf = next(s for s in finished if s.name == "wf_root")
        host_comp = next(s for s in finished if s.name == "component.llm_node")
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
            invoke_id="sub_wf_node",
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
        await handler.on_call_done(invoke_id="sub_wf_node")
        await handler.on_call_done(invoke_id="wf_root")

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 4

        root_wf = next(s for s in finished if s.name == "wf_root")
        host_comp = next(s for s in finished if s.name == "component.sub_wf_node")
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
# ===========================================================================
# Supplementary tests for issues B/C/D
# ===========================================================================


class TestRefreshInputsAttribute:
    """Test _refresh_inputs_attribute (Problem C: memory variable inputs showing None).

    When transform callbacks (e.g. resolve_global_vars_transform) mutate the
    inputs dict in place after on_pre_invoke, the OTel attribute should reflect
    the post-transform values.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        _EXPORTER.clear()
        self.handler = OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig(redaction_enabled=False))

    async def test_refresh_inputs_after_mutation(self):
        """Inputs mutated after on_pre_invoke should appear in span attributes."""
        invoke_id = "node_1"
        await self.handler.on_call_start(
            invoke_id=invoke_id,
            metadata={
                "component_id": "node_1",
                "component_type": "LLM",
                "workflow_id": "wf1",
            },
        )
        # Simulate on_pre_invoke with initial inputs (memory vars as None)
        initial_inputs = {"query": "hello", "CHAT_HISTORY": None, "selfname": None}
        await self.handler.on_pre_invoke(invoke_id=invoke_id, inputs=initial_inputs, component_metadata={})

        # Transform callback mutates inputs in place (resolves memory vars)
        initial_inputs["CHAT_HISTORY"] = [{"role": "user", "content": "hi"}]
        initial_inputs["selfname"] = "test_user"

        # on_call_done should re-serialize inputs with resolved values
        await self.handler.on_call_done(invoke_id=invoke_id, outputs={"result": "ok"})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        span = finished[0]
        inputs_attr = span.attributes.get("openjiuwen.workflow.inputs")
        assert "CHAT_HISTORY" in inputs_attr
        assert "test_user" in inputs_attr
        assert "None" not in inputs_attr or inputs_attr.count("None") == 0

    async def test_refresh_inputs_with_no_inputs(self):
        """When inputs is None, refresh should be a no-op."""
        invoke_id = "node_2"
        await self.handler.on_call_start(
            invoke_id=invoke_id,
            metadata={
                "component_id": "node_2",
                "component_type": "Start",
                "workflow_id": "wf1",
            },
        )
        # on_call_done without on_pre_invoke (no inputs stored)
        await self.handler.on_call_done(invoke_id=invoke_id, outputs={})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 1
        # Should not crash, span should exist without inputs attribute
        span = finished[0]
        assert span is not None


class TestComponentSpansInvokeIdKey:
    """Test _component_spans using invoke_id as key (Problem B part 2).

    Previously _component_spans used component_id as key, causing collisions
    when the same component was invoked multiple times (e.g. in loops).
    Now uses invoke_id to ensure uniqueness.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        _EXPORTER.clear()
        self.handler = OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig())

    async def test_multiple_invocations_same_component_id(self):
        """Same component_id with different invoke_ids should not collide."""
        # First invocation
        await self.handler.on_call_start(
            invoke_id="node_1_inv_1",
            metadata={
                "component_id": "node_1",
                "component_type": "LLM",
                "workflow_id": "wf1",
            },
        )
        await self.handler.on_call_done(invoke_id="node_1_inv_1", outputs={})

        # Second invocation with same component_id but different invoke_id
        await self.handler.on_call_start(
            invoke_id="node_1_inv_2",
            metadata={
                "component_id": "node_1",
                "component_type": "LLM",
                "workflow_id": "wf1",
            },
        )
        await self.handler.on_call_done(invoke_id="node_1_inv_2", outputs={})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 2
        # Both spans should exist independently
        invoke_ids = {s.attributes.get(OJ_INVOKE_ID) for s in finished}
        assert invoke_ids == {"node_1_inv_1", "node_1_inv_2"}


class TestWorkflowIsolation:
    """Test workflow isolation via workflow_id field (Problem B part 3).

    When a new workflow starts with a different workflow_id, stale context
    from the previous workflow should be cleaned.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        _EXPORTER.clear()
        self.handler = OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig())

    async def test_different_workflow_id_cleans_stale_context(self):
        """New workflow_id should clean old _layer_root_spans and _component_spans."""
        # First workflow
        await self.handler.on_call_start(
            invoke_id="wf1_root",
            metadata={"workflow_id": "wf1", "workflow_name": "Workflow 1"},
        )
        await self.handler.on_call_done(invoke_id="wf1_root", outputs={})

        # Second workflow with different workflow_id
        await self.handler.on_call_start(
            invoke_id="wf2_root",
            metadata={"workflow_id": "wf2", "workflow_name": "Workflow 2"},
        )

        # Internal state should be cleaned
        assert "wf1_root" not in self.handler._layer_root_spans
        assert self.handler._component_spans == {}

        await self.handler.on_call_done(invoke_id="wf2_root", outputs={})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 2


class TestMultiRoundConversationTraceContinuity:
    """Test multi-round conversation trace continuity (Problem B core fix).

    When a conversation has multiple rounds, the second round should pick up
    the first round's context so sub-workflows can find their parent.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        _EXPORTER.clear()
        self.handler = OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig())

    async def test_second_round_picks_up_first_round_context(self):
        """Second round should use first round's component span as parent."""
        # Round 1: root workflow with a component
        await self.handler.on_call_start(
            invoke_id="round1_root",
            metadata={"workflow_id": "wf1", "workflow_name": "Round 1"},
        )
        await self.handler.on_call_start(
            invoke_id="round1_comp",
            metadata={
                "component_id": "comp1",
                "component_type": "LLM",
                "workflow_id": "wf1",
            },
            parent_node_id="",
        )
        await self.handler.on_call_done(invoke_id="round1_comp", outputs={})
        # End round1_root so its span is exported (preserved in _layer_root_spans)
        await self.handler.on_call_done(invoke_id="round1_root", outputs={})

        # Round 2: same workflow_id, new conversation round
        await self.handler.on_call_start(
            invoke_id="round2_root",
            metadata={"workflow_id": "wf1", "workflow_name": "Round 2"},
        )

        # After round2 on_call_start, _layer_root_spans[""] is updated to round2_root
        # Key assertion: _layer_root_spans was NOT cleared (multi-round preservation)
        assert "" in self.handler._layer_root_spans
        assert self.handler._layer_root_spans[""].invoke_id == "round2_root"

        await self.handler.on_call_done(invoke_id="round2_root", outputs={})

        finished = _EXPORTER.get_finished_spans()
        assert len(finished) == 3

        # Verify round2_root has a parent from round1 (trace continuity)
        round2_spans = [s for s in finished if s.attributes.get("openjiuwen.invoke_id") == "round2_root"]
        assert len(round2_spans) == 1
        assert round2_spans[0].parent is not None, "round2_root should have a parent span from round1"


