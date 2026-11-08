# -*- coding: UTF-8 -*-
"""Tool decoration through real workflow node sessions.

React agents embedded in workflow components resolve tools via
``Runner.resource_mgr.get_tool(tool_id, session=<workflow session>)``.
``ToolMgr.get_tool`` decorates with ``decorate_tool_with_trace``, whose
``_should_decorate`` gate requires the ``span`` protocol. The existing
``test_decorate_tool`` covers the agent-session shape with MagicMock sessions
— MagicMock satisfies ``hasattr`` for anything, so the gate has never been
exercised against a real workflow ``NodeSession``. These tests use real
session classes: before NodeSession grew its ``span()`` stub the gate
rejected every workflow-session fetch and tool calls inside workflows
(tool / plugin / agent nodes) emitted no execute_tool span.
"""

from typing import Any, AsyncIterator, List

import pytest

from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard, ToolInfo
from openjiuwen.core.session import NodeSession, WorkflowSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.tracer import Tracer, TracerHandlerRegistry, decorate_tool_with_trace
from openjiuwen.core.session.tracer.handler import TraceExtAgentHandler

pytestmark = pytest.mark.asyncio


class _RecordingAgentHandler(TraceExtAgentHandler):
    def __init__(self):
        self.events: List[tuple] = []

    async def on_plugin_start(self, span, inputs: Any, instance_info: dict, **kwargs):
        self.events.append(("on_plugin_start", span, inputs, instance_info))

    async def on_plugin_end(self, span, outputs, **kwargs):
        self.events.append(("on_plugin_end", span, outputs))


# Only the plugin pair is observed here; Tracer.trigger dispatches by
# ``hasattr(handler, event_name)``, so the untouched abstract surface is
# never called. Clearing __abstractmethods__ keeps the fixture to the two
# methods under test instead of twenty no-ops.
_RecordingAgentHandler.__abstractmethods__ = frozenset()


class MockNodeTool(Tool):
    def __init__(self, card: ToolCard):
        super().__init__(card)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        return {"ok": True, "inputs": inputs}

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield {}

    async def astream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield {"chunk": 1}
        yield {"chunk": 2}

    def get_tool_info(self) -> ToolInfo:
        return ToolInfo(name=self.card.name, description=self.card.description)


def _make_node_session(tracer: Tracer = None) -> Session:
    workflow_session = WorkflowSession(workflow_id="wf")
    if tracer is not None:
        workflow_session.set_tracer(tracer)
    return Session(NodeSession(workflow_session, "tool_node"))


@pytest.fixture()
def recording_handler():
    handler = _RecordingAgentHandler()
    TracerHandlerRegistry.register_handler("recording_agent_test", handler)
    yield handler
    TracerHandlerRegistry.unregister_handler("recording_agent_test")


async def test_node_session_exposes_span_stub():
    session = _make_node_session()
    assert session._inner.span() is None


async def test_tool_decorated_through_node_session_fires_plugin_events(recording_handler):
    tracer = Tracer()
    tracer.init()
    session = _make_node_session(tracer)

    raw_tool = MockNodeTool(card=ToolCard(name="doc_generate", description="doc tool"))
    wrapped = decorate_tool_with_trace(raw_tool, session)
    assert wrapped is not raw_tool, "workflow node session must pass the decoration gate"

    outputs = await wrapped.invoke({"query": "hello"})
    assert outputs["ok"] is True

    assert [e[0] for e in recording_handler.events] == ["on_plugin_start", "on_plugin_end"]
    _, start_span, start_inputs, instance_info = recording_handler.events[0]
    assert instance_info["class_name"] == "doc_generate"
    assert instance_info["type"] == "tool"
    assert start_inputs == {"inputs": {"query": "hello"}}
    # NodeSession.span() is a stub: the TraceAgentSpan is parentless and the
    # OTel handler falls back to the ambient (node span) context.
    assert start_span.parent_invoke_id is None
    assert recording_handler.events[1][2] == {"outputs": outputs}


async def test_tool_left_undecorated_without_tracer():
    session = _make_node_session()

    raw_tool = MockNodeTool(card=ToolCard(name="doc_generate", description="doc tool"))
    assert decorate_tool_with_trace(raw_tool, session) is raw_tool


async def test_tool_astream_decorated_through_node_session_fires_plugin_events(recording_handler):
    """Streaming tools get their astream wrapped, not just invoke.

    Studio's workflow stream paths (FlowApi/FlowCustomApi.stream) call
    ``tool.astream`` on the decorated proxy; without this wrapper those
    executions bypass execute_tool spans entirely.
    """
    tracer = Tracer()
    tracer.init()
    session = _make_node_session(tracer)

    raw_tool = MockNodeTool(card=ToolCard(name="doc_generate", description="doc tool"))
    wrapped = decorate_tool_with_trace(raw_tool, session)
    assert wrapped is not raw_tool

    chunks = [chunk async for chunk in wrapped.astream({"query": "hello"})]
    assert chunks == [{"chunk": 1}, {"chunk": 2}]

    assert [e[0] for e in recording_handler.events] == ["on_plugin_start", "on_plugin_end"]
    _, start_span, start_inputs, instance_info = recording_handler.events[0]
    assert instance_info["class_name"] == "doc_generate"
    assert instance_info["type"] == "tool"
    assert start_span.parent_invoke_id is None


async def test_tool_without_astream_keeps_invoke_only_decoration(recording_handler):
    """Tools lacking astream stay decorated on invoke alone.

    The proxy must not manufacture an astream attribute for them.
    """
    tracer = Tracer()
    tracer.init()
    session = _make_node_session(tracer)

    class _InvokeOnlyTool(MockNodeTool):
        astream = None

    raw_tool = _InvokeOnlyTool(card=ToolCard(name="doc_generate", description="doc tool"))
    wrapped = decorate_tool_with_trace(raw_tool, session)
    assert "astream" not in wrapped.__dict__, "no astream wrapper for invoke-only tools"
