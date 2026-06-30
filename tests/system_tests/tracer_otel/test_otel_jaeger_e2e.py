# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Jaeger OTLP integration test — pytest version.

Validates that OTel spans from agent and workflow invocations are exported
to Jaeger via OTLP and can be queried through the Jaeger HTTP API.

Two separate test classes:
- TestJaegerAgentIntegration: agent LLM + plugin spans via tracer.trigger()
- TestJaegerWorkflowIntegration: workflow root + component spans via real wf.invoke()

Prerequisites:
- Jaeger all-in-one running at localhost:4317 (gRPC) and localhost:16686 (HTTP)
- opentelemetry-exporter-otlp-proto-grpc installed
"""

import asyncio

import pytest
import requests

from tests.conftest_otel import _EXPORTER, _OTEL_TRACER, jaeger_is_available
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode, Node1
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.core.session.workflow import Session, create_workflow_session
from openjiuwen.core.workflow import Workflow
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_registry_and_exporter():
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()
    yield
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()


def _register_otel_handlers():
    """Register OtelAgentHandler + OtelWorkflowHandler to global registry."""
    config = OtelTracerConfig(redaction_enabled=False)
    TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))
    TracerHandlerRegistry.register_handler("otel_workflow", OtelWorkflowHandler(_OTEL_TRACER, config))


def _query_jaeger_traces(service_name: str) -> list[str]:
    """Query Jaeger HTTP API for span names of a given service."""
    resp = requests.get(
        "http://localhost:16686/api/traces",
        params={"service": service_name, "limit": 10},
        timeout=10,
    )
    data = resp.json()
    traces = data.get("data", [])
    all_span_names = []
    for trace_data in traces:
        for span in trace_data.get("spans", []):
            all_span_names.append(span.get("operationName", ""))
    return all_span_names


# ---------------------------------------------------------------------------
# Jaeger integration: Agent spans
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not jaeger_is_available(),
    reason="Jaeger not available at localhost:16686",
)
class TestJaegerAgentIntegration:
    """Agent spans exported to Jaeger via OTLP and verified through HTTP API."""

    async def test_agent_llm_span_in_jaeger(self):
        """LLM span is visible in Jaeger after export."""
        config = OtelTracerConfig(redaction_enabled=False)
        TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))

        tracer = Tracer()
        tracer.init()

        # --- LLM span ---
        llm_span = tracer.tracer_agent_span_manager.create_agent_span()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_start",
            span=llm_span, inputs="jaeger agent test",
            instance_info={"class_name": "JaegerAgentLLM"},
        )
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_llm_end",
            span=llm_span, outputs="jaeger agent response",
        )

        # Wait for BatchSpanProcessor to export
        await asyncio.sleep(6)

        span_names = _query_jaeger_traces("openjiuwen")
        assert "llm.JaegerAgentLLM" in span_names, (
            f"llm.JaegerAgentLLM not in Jaeger. Found: {span_names}"
        )

    async def test_agent_plugin_span_in_jaeger(self):
        """Plugin (tool) span is visible in Jaeger after export."""
        config = OtelTracerConfig(redaction_enabled=False)
        TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(_OTEL_TRACER, config))

        tracer = Tracer()
        tracer.init()

        # --- Plugin span ---
        plugin_span = tracer.tracer_agent_span_manager.create_agent_span()
        plugin_span.invoke_type = "plugin"
        plugin_span.name = "JaegerSearchTool"
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_plugin_start",
            span=plugin_span, inputs={"query": "agent search"},
            instance_info={"class_name": "JaegerSearchTool"},
        )
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value, "on_plugin_end",
            span=plugin_span, outputs={"result": "agent found"},
        )

        # Wait for BatchSpanProcessor to export
        await asyncio.sleep(6)

        span_names = _query_jaeger_traces("openjiuwen")
        assert "tool.JaegerSearchTool" in span_names, (
            f"tool.JaegerSearchTool not in Jaeger. Found: {span_names}"
        )


# ---------------------------------------------------------------------------
# Jaeger integration: Workflow spans (real wf.invoke)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not jaeger_is_available(),
    reason="Jaeger not available at localhost:16686",
)
class TestJaegerWorkflowIntegration:
    """Workflow spans exported to Jaeger via OTLP — uses real Workflow.invoke()."""

    async def test_workflow_root_span_in_jaeger(self):
        """Workflow root span is visible in Jaeger after real workflow invoke."""
        _register_otel_handlers()

        wf = Workflow()
        wf.set_start_comp("start", MockStartNode("start"),
                          inputs_schema={"a": "${a}"})
        wf.add_workflow_comp("node1", Node1("node1"),
                             inputs_schema={"aa": "${start.a}"})
        wf.set_end_comp("end", MockEndNode("end"),
                        inputs_schema={"result": "${node1.aa}"})
        wf.add_connection("start", "node1")
        wf.add_connection("node1", "end")

        session = create_workflow_session()
        result = await wf.invoke({"a": 99}, session)

        # Verify workflow completed
        assert result.state.value == "COMPLETED"

        # Wait for BatchSpanProcessor to export
        await asyncio.sleep(6)

        span_names = _query_jaeger_traces("openjiuwen")
        # Expect workflow root span and at least 3 component spans
        assert len(span_names) >= 4, (
            f"Expected at least 4 spans in Jaeger, found: {span_names}"
        )

        # Verify component spans exist
        component_spans = [s for s in span_names if s.startswith("component.")]
        assert len(component_spans) >= 3, (
            f"Expected at least 3 component spans, found: {component_spans}"
        )

    async def test_workflow_component_span_in_jaeger(self):
        """Workflow component spans (start, node1, end) visible in Jaeger."""
        _register_otel_handlers()

        wf = Workflow()
        wf.set_start_comp("start", MockStartNode("start"),
                          inputs_schema={"a": "${a}"})
        wf.add_workflow_comp("node1", Node1("node1"),
                             inputs_schema={"aa": "${start.a}"})
        wf.set_end_comp("end", MockEndNode("end"),
                        inputs_schema={"result": "${node1.aa}"})
        wf.add_connection("start", "node1")
        wf.add_connection("node1", "end")

        session = create_workflow_session()
        await wf.invoke({"a": 100}, session)

        # Wait for BatchSpanProcessor to export
        await asyncio.sleep(6)

        span_names = _query_jaeger_traces("openjiuwen")
        component_spans = [s for s in span_names if s.startswith("component.")]
        assert len(component_spans) >= 3, (
            f"Expected at least 3 component spans. Found: {component_spans}"
        )