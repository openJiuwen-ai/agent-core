# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""An AFTER_TOOL_CALL evolution trigger sees the tool call it follows.

Callbacks run highest priority first. The observability rail ends the tool
span in its own after_tool_call at priority 10, while every evolution rail
(priority 60 and up) drained its subscription before that -- so the drain
never found the tool span and the trigger never fired.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import iter_spans, read_tool_call
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability import span_context
from openjiuwen.harness.observability.rail import AgentObservabilityRail
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint


class _RecordingEvolutionRail(EvolutionRail):
    def __init__(self, processor: TrajectorySpanProcessor) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.AFTER_TOOL_CALL,
            async_evolution=False,
            trajectory_span_processor=processor,
        )
        self.prepared = []

    async def run_evolution(self, prepared) -> None:
        self.prepared.append(prepared)


@pytest.mark.asyncio
async def test_after_tool_call_trigger_fires_with_the_ended_tool_span() -> None:
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("after-tool-call-trigger")
    manager = AgentCallbackManager(agent_id=f"after-tool-{uuid.uuid4().hex}")
    agent = SimpleNamespace(agent_callback_manager=manager, ability_manager=None, card=None)
    observability = AgentObservabilityRail(tracer=tracer)
    evolution = _RecordingEvolutionRail(processor)
    await manager.register_rail(observability, agent)
    await manager.register_rail(evolution, agent)

    session_id = "after-tool-session"
    root = tracer.start_span(
        "agent.run",
        attributes={
            semconv.OJ_TRACE_ROOT: True,
            semconv.GEN_AI_CONVERSATION_ID: session_id,
        },
    )
    span_context.set_root_span(root, session_id=session_id)
    try:
        await evolution.before_invoke(
            AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="q", conversation_id=session_id))
        )
        ctx = AgentCallbackContext(
            agent=agent,
            inputs=ToolCallInputs(
                tool_call=SimpleNamespace(id="call-1"),
                tool_name="search",
                tool_args={"q": "x"},
            ),
        )
        await ctx.fire(AgentCallbackEvent.BEFORE_TOOL_CALL)
        ctx.inputs.tool_result = {"answer": 42}
        await ctx.fire(AgentCallbackEvent.AFTER_TOOL_CALL)
    finally:
        await manager.unregister_rail(observability, agent)
        await manager.unregister_rail(evolution, agent)
        root.end()
        span_context.clear_root_span(session_id=session_id, expected_span=root)
        provider.shutdown()
        span_context.reset_state()

    assert len(evolution.prepared) == 1
    tools = [read_tool_call(span) for span in iter_spans(evolution.prepared[0].trajectory)]
    assert tools == [
        {"name": "search", "id": "call-1", "input": {"q": "x"}, "output": {"answer": 42}},
    ]
