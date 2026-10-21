# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-context identity fallback in OTel handlers (issue #2086).

Orchestrated modes (Controller / PlanExecute) raise events without
rail-forwarded identity; the handler must fall back to the session-scoped
context identity bound at Session creation, while ReAct payloads keep
precedence and identity-less contexts stay identity-less.
"""

import pytest

from openjiuwen.core.session.identity import (
    clear_current_agent_identity,
    current_agent_identity,
    set_current_agent_identity,
)
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    OJ_GEN_AI_TRACE_NAME,
)
from tests.conftest_otel import _EXPORTER, _OTEL_TRACER


@pytest.fixture(autouse=True)
def _isolated_identity():
    """Start every test identity-less, restore the incoming value after."""
    original = current_agent_identity()
    clear_current_agent_identity()
    _EXPORTER.clear()
    yield
    if original:
        set_current_agent_identity(
            original.get("agent_id"),
            original.get("agent_name"),
            original.get("agent_description"),
        )
    else:
        clear_current_agent_identity()
    _EXPORTER.clear()


def _finished_attributes() -> dict:
    spans = _EXPORTER.get_finished_spans()
    assert spans, "expected at least one finished span"
    return dict(spans[-1].attributes or {})


def _agent_handler() -> OtelAgentHandler:
    return OtelAgentHandler(_OTEL_TRACER, OtelTracerConfig(exporter_type="console"))


def _workflow_handler() -> OtelWorkflowHandler:
    return OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig(exporter_type="console"))


class TestAgentHandlerFallback:
    def test_empty_payload_falls_back_to_session_identity(self):
        set_current_agent_identity("group-77", "SupportGroup", "Support group")
        span = _OTEL_TRACER.start_span("chain")
        _agent_handler()._set_agent_identity_attrs(span, {})
        span.end()

        attrs = _finished_attributes()
        assert attrs[GEN_AI_AGENT_ID] == "group-77"
        assert attrs[GEN_AI_AGENT_NAME] == "SupportGroup"
        assert attrs[GEN_AI_AGENT_DESCRIPTION] == "Support group"

    def test_studio_style_payload_falls_back_to_session_identity(self):
        """Controller/PlanExecute events carry studio's nested instance_info
        ({class_name, instance_attributes}) — no flat identity keys."""
        set_current_agent_identity("group-77", "SupportGroup")
        span = _OTEL_TRACER.start_span("chain")
        _agent_handler()._set_agent_identity_attrs(span, {"class_name": "Agent", "instance_attributes": {"config": {}}})
        span.end()

        attrs = _finished_attributes()
        assert attrs[GEN_AI_AGENT_ID] == "group-77"
        assert attrs[GEN_AI_AGENT_NAME] == "SupportGroup"

    def test_rail_payload_takes_precedence_over_session_identity(self):
        """ReAct mode forwards identity via the rail; the payload wins."""
        set_current_agent_identity("group-77", "SupportGroup")
        span = _OTEL_TRACER.start_span("chain")
        _agent_handler()._set_agent_identity_attrs(span, {"agent_id": "react-1", "agent_name": "ReactAgent"})
        span.end()

        attrs = _finished_attributes()
        assert attrs[GEN_AI_AGENT_ID] == "react-1"
        assert attrs[GEN_AI_AGENT_NAME] == "ReactAgent"

    def test_no_identity_anywhere_sets_no_identity_attrs(self):
        span = _OTEL_TRACER.start_span("chain")
        _agent_handler()._set_agent_identity_attrs(span, {})
        span.end()

        attrs = _finished_attributes()
        assert GEN_AI_AGENT_ID not in attrs
        assert GEN_AI_AGENT_NAME not in attrs
        assert GEN_AI_AGENT_DESCRIPTION not in attrs

    def test_session_facts_stay_payload_only(self):
        """conversation_id is session data the card cannot supply — the
        context fallback must not fabricate it."""
        set_current_agent_identity("group-77", "SupportGroup")
        span = _OTEL_TRACER.start_span("chain")
        _agent_handler()._set_agent_identity_attrs(span, {})
        span.end()

        assert GEN_AI_CONVERSATION_ID not in _finished_attributes()

    def test_trace_display_name_uses_session_identity(self):
        set_current_agent_identity("group-77", "SupportGroup")
        span_manager = SpanManager("tid-fallback")
        agent_span = span_manager.create_agent_span(None)
        otel_span = _OTEL_TRACER.start_span("tool")
        _agent_handler()._set_non_llm_attrs(otel_span, agent_span, "tool", {})
        otel_span.end()

        assert _finished_attributes()[OJ_GEN_AI_TRACE_NAME] == "SupportGroup"


class TestWorkflowHandlerFallback:
    def test_workflow_span_gets_session_identity(self):
        set_current_agent_identity("group-77", "SupportGroup", "Support group")
        otel_span = _OTEL_TRACER.start_span("workflow")
        _workflow_handler()._set_workflow_attrs(
            otel_span,
            {"workflow_id": "wf-1", "workflow_name": "Main"},
            "invoke-1",
        )
        otel_span.end()

        attrs = _finished_attributes()
        assert attrs[GEN_AI_AGENT_ID] == "group-77"
        assert attrs[GEN_AI_AGENT_NAME] == "SupportGroup"
        assert attrs["openjiuwen.workflow.name"] == "Main"

    def test_identity_stamped_even_without_metadata(self):
        set_current_agent_identity("group-77", "SupportGroup")
        otel_span = _OTEL_TRACER.start_span("workflow")
        _workflow_handler()._set_workflow_attrs(otel_span, None, "invoke-2")
        otel_span.end()

        attrs = _finished_attributes()
        assert attrs[GEN_AI_AGENT_ID] == "group-77"

    def test_standalone_workflow_gets_no_identity(self):
        """No session → no identity: standalone workflows stay anonymous."""
        otel_span = _OTEL_TRACER.start_span("workflow")
        _workflow_handler()._set_workflow_attrs(otel_span, {"workflow_id": "wf-2"}, "invoke-3")
        otel_span.end()

        attrs = _finished_attributes()
        assert GEN_AI_AGENT_ID not in attrs
        assert GEN_AI_AGENT_NAME not in attrs
