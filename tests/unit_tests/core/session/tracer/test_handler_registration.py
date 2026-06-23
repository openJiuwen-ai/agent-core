# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session.stream.emitter import StreamEmitter
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.handler import (
    TracerHandlerName,
    TraceExtAgentHandler,
    TraceExtWorkflowHandler,
)
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry

pytestmark = pytest.mark.asyncio


class MockExtAgentHandler(TraceExtAgentHandler):
    """Mock agent handler that records all triggered events."""

    def __init__(self):
        self._calls = []

    async def on_llm_start(self, **kwargs):
        self._calls.append(("on_llm_start", kwargs))

    async def on_llm_request(self, **kwargs):
        self._calls.append(("on_llm_request", kwargs))

    async def on_llm_end(self, **kwargs):
        self._calls.append(("on_llm_end", kwargs))

    async def on_llm_error(self, **kwargs):
        self._calls.append(("on_llm_error", kwargs))

    async def on_plugin_start(self, **kwargs):
        self._calls.append(("on_plugin_start", kwargs))

    async def on_plugin_end(self, **kwargs):
        self._calls.append(("on_plugin_end", kwargs))

    async def on_plugin_error(self, **kwargs):
        self._calls.append(("on_plugin_error", kwargs))

    async def on_prompt_start(self, **kwargs):
        self._calls.append(("on_prompt_start", kwargs))

    async def on_prompt_end(self, **kwargs):
        self._calls.append(("on_prompt_end", kwargs))

    async def on_prompt_error(self, **kwargs):
        self._calls.append(("on_prompt_error", kwargs))

    async def on_chain_start(self, **kwargs):
        self._calls.append(("on_chain_start", kwargs))

    async def on_chain_end(self, **kwargs):
        self._calls.append(("on_chain_end", kwargs))

    async def on_chain_error(self, **kwargs):
        self._calls.append(("on_chain_error", kwargs))

    async def on_retriever_start(self, **kwargs):
        self._calls.append(("on_retriever_start", kwargs))

    async def on_retriever_end(self, **kwargs):
        self._calls.append(("on_retriever_end", kwargs))

    async def on_retriever_error(self, **kwargs):
        self._calls.append(("on_retriever_error", kwargs))

    async def on_evaluator_start(self, **kwargs):
        self._calls.append(("on_evaluator_start", kwargs))

    async def on_evaluator_end(self, **kwargs):
        self._calls.append(("on_evaluator_end", kwargs))

    async def on_evaluator_error(self, **kwargs):
        self._calls.append(("on_evaluator_error", kwargs))

    async def on_workflow_start(self, **kwargs):
        self._calls.append(("on_workflow_start", kwargs))

    async def on_workflow_end(self, **kwargs):
        self._calls.append(("on_workflow_end", kwargs))

    async def on_workflow_error(self, **kwargs):
        self._calls.append(("on_workflow_error", kwargs))


class MockExtWorkflowHandler(TraceExtWorkflowHandler):
    """Mock workflow handler that records all triggered events."""

    def __init__(self):
        self._calls = []

    async def on_call_start(self, **kwargs):
        self._calls.append(("on_call_start", kwargs))

    async def on_call_done(self, **kwargs):
        self._calls.append(("on_call_done", kwargs))

    async def on_pre_invoke(self, **kwargs):
        self._calls.append(("on_pre_invoke", kwargs))

    async def on_pre_stream(self, **kwargs):
        self._calls.append(("on_pre_stream", kwargs))

    async def on_invoke(self, **kwargs):
        self._calls.append(("on_invoke", kwargs))

    async def on_post_invoke(self, **kwargs):
        self._calls.append(("on_post_invoke", kwargs))

    async def on_post_stream(self, **kwargs):
        self._calls.append(("on_post_stream", kwargs))

    async def on_interact(self, **kwargs):
        self._calls.append(("on_interact", kwargs))


def _create_tracer():
    """Create a Tracer instance with StreamWriterManager.

    init() picks up globally registered handlers from TracerHandlerRegistry.
    When stream_writer_manager has a trace writer, built-in TraceSchema handlers
    are also registered automatically.
    """
    tracer = Tracer()
    tracer.init(StreamWriterManager(StreamEmitter()))
    return tracer


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clean the global registry before and after each test."""
    TracerHandlerRegistry.clear()
    yield
    TracerHandlerRegistry.clear()


# ------------------------------------------------------------------
# Global registry tests
# ------------------------------------------------------------------

class TestTracerHandlerRegistry:

    def test_register_agent_handler_globally(self):
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        assert "otel_agent" in TracerHandlerRegistry.get_agent_handlers()
        assert TracerHandlerRegistry.get_agent_handlers()["otel_agent"] is handler

    def test_register_workflow_handler_globally(self):
        handler = MockExtWorkflowHandler()
        TracerHandlerRegistry.register_handler("otel_workflow", handler)
        assert "otel_workflow" in TracerHandlerRegistry.get_workflow_handlers()
        assert TracerHandlerRegistry.get_workflow_handlers()["otel_workflow"] is handler

    def test_duplicate_handler_name_raises(self):
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        with pytest.raises(ValueError, match="already registered"):
            TracerHandlerRegistry.register_handler("otel_agent", MockExtAgentHandler())

    def test_duplicate_name_cross_type_raises(self):
        """A name registered as agent cannot be reused for workflow."""
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("my_handler", handler)
        with pytest.raises(ValueError, match="already registered"):
            TracerHandlerRegistry.register_handler("my_handler", MockExtWorkflowHandler())

    def test_unregister_handler(self):
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        assert TracerHandlerRegistry.unregister_handler("otel_agent") is True
        assert "otel_agent" not in TracerHandlerRegistry.get_agent_handlers()

    def test_unregister_nonexistent_handler(self):
        assert TracerHandlerRegistry.unregister_handler("nonexistent") is False

    def test_clear_registry(self):
        TracerHandlerRegistry.register_handler("otel_agent", MockExtAgentHandler())
        TracerHandlerRegistry.register_handler("otel_workflow", MockExtWorkflowHandler())
        TracerHandlerRegistry.clear()
        assert len(TracerHandlerRegistry.get_agent_handlers()) == 0
        assert len(TracerHandlerRegistry.get_workflow_handlers()) == 0

    def test_reserved_name_tracer_agent_raises(self):
        """Cannot register handler with reserved name 'tracer_agent'."""
        with pytest.raises(ValueError, match="reserved name"):
            TracerHandlerRegistry.register_handler("tracer_agent", MockExtAgentHandler())

    def test_reserved_name_tracer_workflow_raises(self):
        """Cannot register handler with reserved name 'tracer_workflow'."""
        with pytest.raises(ValueError, match="reserved name"):
            TracerHandlerRegistry.register_handler("tracer_workflow", MockExtWorkflowHandler())


# ------------------------------------------------------------------
# Global → Tracer.init() propagation tests
# ------------------------------------------------------------------

class TestGlobalRegistryPropagation:

    async def test_tracer_init_picks_up_registered_agent_handler(self):
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        tracer = _create_tracer()

        span = tracer.tracer_agent_span_manager.create_agent_span()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_start",
            span=span, inputs={"test": 1}, instance_info={"class_name": "TestModel"},
        )

        assert len(handler._calls) == 1
        assert handler._calls[0][0] == "on_llm_start"

    async def test_tracer_init_picks_up_registered_workflow_handler(self):
        handler = MockExtWorkflowHandler()
        TracerHandlerRegistry.register_handler("otel_workflow", handler)
        tracer = _create_tracer()

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="test_node", parent_node_id="",
            metadata={"component_id": "test"}, inputs={"test": 1},
        )

        assert len(handler._calls) == 1
        assert handler._calls[0][0] == "on_call_start"

    async def test_multiple_agent_handlers_registered_globally(self):
        handler1 = MockExtAgentHandler()
        handler2 = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent_1", handler1)
        TracerHandlerRegistry.register_handler("otel_agent_2", handler2)
        tracer = _create_tracer()

        span = tracer.tracer_agent_span_manager.create_agent_span()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_chain_start",
            span=span, inputs={"test": 1}, instance_info={"class_name": "TestChain"},
        )

        assert len(handler1._calls) == 1
        assert len(handler2._calls) == 1

    async def test_multiple_workflow_handlers_registered_globally(self):
        handler1 = MockExtWorkflowHandler()
        handler2 = MockExtWorkflowHandler()
        TracerHandlerRegistry.register_handler("otel_workflow_1", handler1)
        TracerHandlerRegistry.register_handler("otel_workflow_2", handler2)
        tracer = _create_tracer()

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="test_node", parent_node_id="",
            metadata={"component_id": "test"}, inputs=None,
        )

        assert len(handler1._calls) == 1
        assert len(handler2._calls) == 1

    async def test_handler_registered_after_init_not_picked_up(self):
        """Handler registered AFTER Tracer.init() should NOT appear in that tracer."""
        tracer = _create_tracer()
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("late_agent", handler)

        assert "late_agent" not in tracer._agent_handlers

    async def test_second_tracer_picks_up_handlers_registered_between_inits(self):
        """A second Tracer should pick up handlers registered after the first Tracer."""
        tracer1 = _create_tracer()

        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)

        tracer2 = _create_tracer()
        assert "otel_agent" in tracer2._agent_handlers


# ------------------------------------------------------------------
# Trigger iteration tests
# ------------------------------------------------------------------

class TestTriggerIteratesAllHandlers:

    async def test_trigger_iterates_all_agent_handlers_including_builtin(self):
        handler = MockExtAgentHandler()
        TracerHandlerRegistry.register_handler("otel_agent", handler)
        tracer = _create_tracer()

        span = tracer.tracer_agent_span_manager.create_agent_span()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_start",
            span=span, inputs={"test": 1}, instance_info={"class_name": "TestModel"},
        )

        assert len(handler._calls) == 1

    async def test_trigger_iterates_workflow_handlers_with_parent_node_id_matching(self):
        handler = MockExtWorkflowHandler()
        TracerHandlerRegistry.register_handler("otel_workflow", handler)
        tracer = _create_tracer()

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="start", parent_node_id="",
            metadata={"component_id": "start"},
        )

        assert len(handler._calls) == 1

    async def test_workflow_handler_with_subworkflow(self):
        handler = MockExtWorkflowHandler()
        TracerHandlerRegistry.register_handler("otel_workflow", handler)
        tracer = _create_tracer()
        tracer.register_workflow_span_manager("sub_node")

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="start", parent_node_id="",
            metadata={"component_id": "start"},
        )
        assert len(handler._calls) == 1

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="sub_start", parent_node_id="sub_node",
            metadata={"component_id": "sub_start"},
        )
        assert len(handler._calls) == 2


# ------------------------------------------------------------------
# WorkflowSpanManager integration
# ------------------------------------------------------------------

class TestWorkflowSpanManagerIntegration:

    async def test_register_workflow_span_manager_adds_to_dict(self):
        tracer = _create_tracer()
        tracer.register_workflow_span_manager("node_a")
        # The subworkflow handler should be stored in the dict keyed by concatenated name
        wf_handler_dict = tracer._workflow_handlers[TracerHandlerName.TRACER_WORKFLOW.value]
        concat_key = TracerHandlerName.TRACER_WORKFLOW.value + ".node_a"
        assert concat_key in wf_handler_dict
        assert wf_handler_dict[concat_key]._span_manager._parent_node_id == "node_a"

    async def test_register_multiple_workflow_span_managers(self):
        tracer = _create_tracer()
        tracer.register_workflow_span_manager("node_a")
        tracer.register_workflow_span_manager("node_b")
        wf_handler_dict = tracer._workflow_handlers[TracerHandlerName.TRACER_WORKFLOW.value]
        assert len(wf_handler_dict) == 3  # tracer_workflow (root) + tracer_workflow.node_a + tracer_workflow.node_b


# ------------------------------------------------------------------
# Backward compatibility
# ------------------------------------------------------------------

class TestBackwardCompatibility:

    async def test_trigger_with_parent_node_id_matching(self):
        """Trigger with TRACER_WORKFLOW + parent_node_id finds correct handler in list."""
        tracer = _create_tracer()
        tracer.register_workflow_span_manager("node_a")

        span_manager = tracer.tracer_workflow_span_manager_dict.get("node_a")
        span = span_manager.create_workflow_span("invoke_1")
        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
            invoke_id="invoke_1", parent_node_id="node_a",
            metadata={"component_id": "invoke_1"},
        )

        updated_span = span_manager.get_span("invoke_1")
        assert updated_span is not None
        assert updated_span.start_time is not None


# ------------------------------------------------------------------
# Unknown handler_class_name fallback
# ------------------------------------------------------------------

class TestTriggerUnknownHandlerClassName:

    async def test_trigger_with_unknown_handler_class_name_logs_warning(self):
        """Trigger with unknown handler_class_name should log warning and not raise."""
        tracer = _create_tracer()
        # No exception should be raised for unknown handler_class_name
        await tracer.trigger("unknown_handler", "on_llm_start")