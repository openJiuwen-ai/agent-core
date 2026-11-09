# coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TracerAgentUtils retriever event helpers."""

import pytest

from openjiuwen.core.session.tracer.agent_tracer import TracerAgentUtils
from openjiuwen.core.session.tracer.handler import TraceExtAgentHandler
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry

pytestmark = pytest.mark.asyncio


class RetrieverRecorderHandler(TraceExtAgentHandler):
    """Record every event this handler receives (retriever events asserted)."""

    def __init__(self):
        self.events = []

    async def on_retriever_start(self, **kwargs):
        self.events.append(("on_retriever_start", kwargs))

    async def on_retriever_end(self, **kwargs):
        self.events.append(("on_retriever_end", kwargs))

    async def on_retriever_error(self, **kwargs):
        self.events.append(("on_retriever_error", kwargs))

    # Satisfy the abstract interface; recording keeps the assertions honest
    # about which events the utils actually fire.
    async def on_llm_start(self, **kwargs):
        self.events.append(("on_llm_start", kwargs))

    async def on_llm_request(self, **kwargs):
        self.events.append(("on_llm_request", kwargs))

    async def on_llm_end(self, **kwargs):
        self.events.append(("on_llm_end", kwargs))

    async def on_llm_error(self, **kwargs):
        self.events.append(("on_llm_error", kwargs))

    async def on_plugin_start(self, **kwargs):
        self.events.append(("on_plugin_start", kwargs))

    async def on_plugin_end(self, **kwargs):
        self.events.append(("on_plugin_end", kwargs))

    async def on_plugin_error(self, **kwargs):
        self.events.append(("on_plugin_error", kwargs))

    async def on_prompt_start(self, **kwargs):
        self.events.append(("on_prompt_start", kwargs))

    async def on_prompt_end(self, **kwargs):
        self.events.append(("on_prompt_end", kwargs))

    async def on_prompt_error(self, **kwargs):
        self.events.append(("on_prompt_error", kwargs))

    async def on_chain_start(self, **kwargs):
        self.events.append(("on_chain_start", kwargs))

    async def on_chain_end(self, **kwargs):
        self.events.append(("on_chain_end", kwargs))

    async def on_chain_error(self, **kwargs):
        self.events.append(("on_chain_error", kwargs))

    async def on_evaluator_start(self, **kwargs):
        self.events.append(("on_evaluator_start", kwargs))

    async def on_evaluator_end(self, **kwargs):
        self.events.append(("on_evaluator_end", kwargs))

    async def on_evaluator_error(self, **kwargs):
        self.events.append(("on_evaluator_error", kwargs))

    async def on_workflow_start(self, **kwargs):
        self.events.append(("on_workflow_start", kwargs))

    async def on_workflow_end(self, **kwargs):
        self.events.append(("on_workflow_end", kwargs))

    async def on_workflow_error(self, **kwargs):
        self.events.append(("on_workflow_error", kwargs))


class _FakeSession:
    """Minimal session stand-in: just exposes the tracer."""

    def __init__(self, tracer):
        self._tracer = tracer

    def tracer(self):
        return self._tracer


class TestTracerAgentUtilsRetrieval:
    async def test_retriever_events_reach_registered_handler(self):
        handler = RetrieverRecorderHandler()
        TracerHandlerRegistry.register_handler("recorder_agent", handler)
        try:
            tracer = Tracer(session_id="sess-1")
            tracer.init()
            session = _FakeSession(tracer)

            span = await TracerAgentUtils.trace_retriever_start(
                session, query="什么是九问"
            )
            assert span is not None
            await TracerAgentUtils.trace_retriever_end(
                session, span, documents=["doc-a", "doc-b"]
            )

            assert [name for name, _ in handler.events] == [
                "on_retriever_start",
                "on_retriever_end",
            ]
            start_kwargs = handler.events[0][1]
            assert start_kwargs["inputs"] == {"query": "什么是九问"}
            assert start_kwargs["instance_info"]["type"] == "retriever"
            assert start_kwargs["span"] is span
            done_kwargs = handler.events[1][1]
            assert done_kwargs["outputs"] == {"results": ["doc-a", "doc-b"]}
        finally:
            TracerHandlerRegistry.unregister_handler("recorder_agent")

    async def test_retriever_error_event_carries_error(self):
        handler = RetrieverRecorderHandler()
        TracerHandlerRegistry.register_handler("recorder_agent", handler)
        try:
            tracer = Tracer()
            tracer.init()
            session = _FakeSession(tracer)

            span = await TracerAgentUtils.trace_retriever_start(session)
            error = RuntimeError("kb down")
            await TracerAgentUtils.trace_retriever_error(session, span, error)

            assert handler.events[1][0] == "on_retriever_error"
            assert handler.events[1][1]["error"] is error
        finally:
            TracerHandlerRegistry.unregister_handler("recorder_agent")

    async def test_retriever_start_returns_none_without_handlers(self):
        tracer = Tracer()
        tracer.init()
        session = _FakeSession(tracer)

        span = await TracerAgentUtils.trace_retriever_start(session, query="q")
        assert span is None
        # Done/error on a None span must be silent no-ops.
        await TracerAgentUtils.trace_retriever_end(session, None)
        await TracerAgentUtils.trace_retriever_error(session, None, RuntimeError("x"))

    async def test_retriever_start_returns_none_without_session(self):
        assert await TracerAgentUtils.trace_retriever_start(None, query="q") is None
        await TracerAgentUtils.trace_retriever_end(None, None)
        await TracerAgentUtils.trace_retriever_error(None, None, RuntimeError("x"))
