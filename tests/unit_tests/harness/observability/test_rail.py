# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The agent-tier span every DeepAgent gets, and what other layers add to it."""

from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest
import openjiuwen.harness.observability.rail as rail_module
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.extensions.observability import span_context as shared_span_context
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.semconv import (
    DA_AGENT_NAME,
    DA_TASK_ITERATION,
    GEN_AI_AGENT_DESCRIPTION,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_AGENT_VERSION,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_ID,
    GEN_AI_TOOL_INPUT,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_OUTPUT,
    LANGFUSE_SESSION_ID,
    AT_SESSION_ID,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    OJ_REQUEST_ID,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_REQUEST_NUMBER,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_SPAN_FORCED_CLOSE,
    OJ_SPAN_FORCED_CLOSE_REASON,
    OJ_INFERENCE_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_TOOL_AUTHORITATIVE,
    OJ_TOOL_RESOURCE_ID,
    OJ_TOOL_TYPE,
    OJ_TRACE_ROOT,
    OJ_TRACE_FORCED_CLOSE,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TURN_ID,
)
from openjiuwen.extensions.observability.tool_outcome import TOOL_REPORTED_FAILURE
from openjiuwen.harness.observability.rail import (
    AgentObservabilityRail,
    AgentSpanDecoration,
)
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.execution_subject import (
    ExecutionSubject,
    execution_subject_scope,
)
from openjiuwen.extensions.observability.span_context import (
    clear_current_session_id,
    set_current_session_id,
)


@pytest.fixture
def tracing():
    """Serve a real tracer over an in-memory exporter, with a run root open."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agent-rail-test")

    shared_span_context.reset_state()
    root = tracer.start_span("run.root")
    root.set_attribute(GEN_AI_CONVERSATION_ID, "conversation")
    root.set_attribute(OJ_SESSION_ID, "session")
    root.set_attribute(OJ_REQUEST_ID, "request")
    root.set_attribute(OJ_RUN_ID, "run")
    root.set_attribute(OJ_TRACE_ROOT, True)
    root.set_attribute(OJ_EXECUTION_SUBJECT_ID, "main")
    root.set_attribute(OJ_EXECUTION_SUBJECT_DISPLAY_NAME, "Main Agent")
    root.set_attribute(OJ_EXECUTION_SUBJECT_KIND, "main_agent")
    root.set_attribute(OJ_EXECUTION_SUBJECT_SESSION_ID, "session")
    shared_span_context.set_root_span(root)
    yield SimpleNamespace(exporter=exporter, tracer=tracer, root=root)
    if root.is_recording():
        root.end()
    shared_span_context.reset_state()


def _agent(name: str = "solo", *, enable_task_loop: bool = True):
    """Build the smallest agent stub the rail reads."""
    return SimpleNamespace(
        member_name=name,
        card=AgentCard(
            id=f"agent-{name}",
            name=f"{name}-display",
            description=f"{name} description",
        ),
        deep_config=SimpleNamespace(enable_task_loop=enable_task_loop),
    )


def _iteration_ctx(agent, *, iteration: int = 1, query: str = "do it"):
    """Build the callback context a task-loop round would pass to the rails."""
    inputs = TaskIterationInputs(iteration=iteration, query=query, loop_event=None)
    return AgentCallbackContext(agent=agent, inputs=inputs)


def _finished(exporter: InMemorySpanExporter, name: str):
    return [span for span in exporter.get_finished_spans() if span.name == name]


@pytest.mark.asyncio
async def test_iteration_span_opens_under_the_run_root_and_carries_generic_attributes(tracing):
    """The agent tier is team-agnostic: no ``agentteam.*`` unless a layer adds it."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent())

    await rail.before_task_iteration(ctx)
    ctx.inputs.result = "the answer"
    await rail.after_task_iteration(ctx)

    spans = _finished(tracing.exporter, "agent.solo.task_iteration.1")
    assert len(spans) == 1
    span = spans[0]
    assert span.parent.span_id == tracing.root.context.span_id
    assert span.attributes[LANGFUSE_OBSERVATION_TYPE] == "agent"
    assert span.attributes[DA_AGENT_NAME] == "solo"
    assert span.attributes[DA_TASK_ITERATION] == 1
    assert span.attributes[GEN_AI_AGENT_NAME] == "solo"
    assert span.attributes[GEN_AI_AGENT_ID] == "agent-solo"
    assert span.attributes[GEN_AI_AGENT_DESCRIPTION] == "solo description"
    assert GEN_AI_AGENT_VERSION not in span.attributes
    assert span.attributes[GEN_AI_CONVERSATION_ID] == "conversation"
    assert span.attributes[GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert span.attributes[OJ_SESSION_ID] == "session"
    assert span.attributes[OJ_REQUEST_ID] == "request"
    assert span.attributes[OJ_RUN_ID] == "run"
    assert OJ_STEP_ID not in span.attributes
    assert OJ_STEP_NUMBER not in span.attributes
    assert span.attributes[OJ_TRACE_SCHEMA_VERSION] == "1"
    assert span.attributes[OJ_TRAJECTORY_RECORD_KIND] == "agent"
    assert span.attributes[LANGFUSE_OBSERVATION_INPUT] == "do it"
    assert span.attributes[LANGFUSE_OBSERVATION_OUTPUT] == "the answer"
    assert not [key for key in span.attributes if key.startswith("agentteam.")]


@pytest.mark.asyncio
async def test_each_llm_request_keeps_identity_parent_and_owning_step(tracing):
    tracing.root.set_attribute(OJ_TURN_ID, "turn-7")
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp", max_attributes=40),
        tracer=tracing.tracer,
    )
    ctx = _iteration_ctx(_agent(), iteration=3)
    await rail.before_task_iteration(ctx)
    outer_span = ctx.extra["_otel_agent_scope"].span
    model_ctx = AgentCallbackContext(
        agent=ctx.agent,
        inputs=ModelCallInputs(react_iteration=3),
        extra=ctx.extra,
    )
    await rail.before_model_call(model_ctx)
    step_span = shared_span_context.get_current_agent_span()
    assert step_span is not None
    assert outer_span.parent.span_id == tracing.root.context.span_id
    assert step_span.parent.span_id == tracing.root.context.span_id

    messages = [
        {"role": "system", "content": "system"},
        *(
            {"role": "user", "content": f"message-{index}"}
            for index in range(12)
        ),
    ]
    for index in range(2):
        await rail.before_model_call(model_ctx)
        request = handler._open_llm_span({"messages": messages, "model": "fake"})
        assert request is not None
        handler._close_llm_span(
            request.otel_llm_state,
            SimpleNamespace(
                content=f"answer-{index}",
                reasoning_content="",
                finish_reason="stop",
                tool_calls=None,
                usage_metadata=None,
            ),
        )

    await rail.after_task_iteration(ctx)

    requests = _finished(tracing.exporter, "llm.call")
    assert len(requests) == 2
    assert [span.attributes[OJ_REQUEST_NUMBER] for span in requests] == [1, 2]
    assert len({span.attributes[OJ_INFERENCE_ID] for span in requests}) == 2
    assert all(
        span.attributes[OJ_INFERENCE_ID] == f"{span.context.span_id:016x}"
        for span in requests
    )
    assert all(span.parent.span_id == step_span.context.span_id for span in requests)
    assert all(span.attributes[OJ_TURN_ID] == "turn-7" for span in requests)
    assert all(span.attributes[OJ_STEP_ID] == f"{step_span.context.span_id:016x}" for span in requests)
    assert all(span.attributes[OJ_STEP_NUMBER] == 3 for span in requests)
    steps = _finished(tracing.exporter, "agent.solo.react_iteration.3")
    assert len(steps) == 1


@pytest.mark.asyncio
async def test_no_agent_span_without_a_run_root(tracing):
    """An orphan agent span would start a trace of its own — skip instead."""
    shared_span_context.reset_state()
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent())

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    assert _finished(tracing.exporter, "agent.solo.task_iteration.1") == []


@pytest.mark.asyncio
async def test_a_contributed_decoration_is_applied_on_open_and_at_close(tracing):
    """This is how a layer extends the span without subclassing or re-opening it."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent())
    AgentSpanDecoration(
        attributes={"contrib.team.id": "research"},
        input_attribute_keys=("contrib.agent.input",),
        output_attribute_keys=("contrib.agent.output",),
    ).park(ctx)

    await rail.before_task_iteration(ctx)
    ctx.inputs.result = "the answer"
    await rail.after_task_iteration(ctx)

    span = _finished(tracing.exporter, "agent.solo.task_iteration.1")[0]
    assert span.attributes["contrib.team.id"] == "research"
    assert span.attributes["contrib.agent.input"] == "do it"
    assert span.attributes["contrib.agent.output"] == "the answer"


@pytest.mark.asyncio
async def test_a_decoration_never_leaks_into_another_agents_span(tracing):
    """Contributions are parked per callback context, not on a ContextVar."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    decorated = _iteration_ctx(_agent("decorated"))
    AgentSpanDecoration(attributes={"contrib.team.id": "research"}).park(decorated)
    await rail.before_task_iteration(decorated)
    await rail.after_task_iteration(decorated)

    plain = _iteration_ctx(_agent("plain"))
    await rail.before_task_iteration(plain)
    await rail.after_task_iteration(plain)

    span = _finished(tracing.exporter, "agent.plain.task_iteration.1")[0]
    assert "contrib.team.id" not in span.attributes


@pytest.mark.asyncio
async def test_single_round_agent_gets_an_invoke_span(tracing):
    """Sub-agents never fire iteration events; the invoke hook is their agent tier."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    agent = _agent("explore_agent", enable_task_loop=False)
    ctx = AgentCallbackContext(agent=agent, inputs=SimpleNamespace(query="look", result=None))

    await rail.before_invoke(ctx)
    ctx.inputs.result = "found it"
    await rail.after_invoke(ctx)

    spans = _finished(tracing.exporter, "agent.explore_agent.invoke")
    assert len(spans) == 1
    assert spans[0].attributes[LANGFUSE_OBSERVATION_OUTPUT] == "found it"


@pytest.mark.asyncio
async def test_multi_round_agent_gets_no_invoke_span(tracing):
    """One agent tier per round: the iteration hook owns the multi-round path."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = AgentCallbackContext(
        agent=_agent(enable_task_loop=True), inputs=SimpleNamespace(query="do it", result=None)
    )

    await rail.before_invoke(ctx)
    await rail.after_invoke(ctx)

    assert _finished(tracing.exporter, "agent.solo.invoke") == []


@pytest.mark.asyncio
async def test_subagent_invoke_nests_under_the_dispatching_agent_span(tracing):
    """Otherwise the sub-agent's whole run reads as if the parent had made the calls."""
    parent_rail = AgentObservabilityRail(tracer=tracing.tracer)
    parent_ctx = _iteration_ctx(_agent("leader"))
    await parent_rail.before_task_iteration(parent_ctx)
    parent_span = parent_ctx.extra["_otel_agent_scope"].span

    subagent_rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    subagent_ctx = AgentCallbackContext(
        agent=_agent("explore_agent", enable_task_loop=False),
        inputs=SimpleNamespace(query="look", result=None),
    )
    with execution_subject_scope(ExecutionSubject(
        subject_id="subagent:dispatch-1",
        display_name="Explore Agent",
        kind="subagent",
        parent_subject_id="main",
        session_id="session_sub_explore_1",
    )):
        await subagent_rail.before_invoke(subagent_ctx)
        request = handler._open_llm_span(
            {"messages": [{"role": "user", "content": "look"}], "model": "fake"}
        )
        assert request is not None
        handler._close_llm_span(
            request.otel_llm_state,
            SimpleNamespace(
                content="done",
                reasoning_content="",
                finish_reason="stop",
                tool_calls=None,
                usage_metadata=None,
            ),
        )
        await subagent_rail.after_invoke(subagent_ctx)
    await parent_rail.after_task_iteration(parent_ctx)

    subagent_span = _finished(tracing.exporter, "agent.explore_agent.invoke")[0]
    assert subagent_span.parent.span_id == parent_span.context.span_id
    request_span = _finished(tracing.exporter, "llm.call")[0]
    assert request_span.parent.span_id == subagent_span.context.span_id
    assert request_span.attributes[OJ_EXECUTION_SUBJECT_ID] == "subagent:dispatch-1"
    assert request_span.attributes[OJ_EXECUTION_SUBJECT_PARENT_ID] == "main"


@pytest.mark.asyncio
async def test_an_orphan_span_from_the_same_agent_is_drained_not_left_open(tracing):
    """A round that never closed would otherwise swallow the next round's children."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    first = _iteration_ctx(_agent(), iteration=1)
    await rail.before_task_iteration(first)
    orphan = first.extra["_otel_agent_scope"].span

    second = _iteration_ctx(_agent(), iteration=2)
    await rail.before_task_iteration(second)
    await rail.after_task_iteration(second)

    assert not orphan.is_recording()
    orphan_record = _finished(tracing.exporter, "agent.solo.task_iteration.1")[0]
    assert orphan_record.status.status_code is StatusCode.UNSET
    assert orphan_record.attributes[OJ_SPAN_FORCED_CLOSE] is True
    assert orphan_record.attributes[OJ_SPAN_FORCED_CLOSE_REASON] == (
        "missing_agent_terminal_callback"
    )
    assert tracing.root.attributes[OJ_TRACE_FORCED_CLOSE] is True
    assert _finished(tracing.exporter, "agent.solo.task_iteration.2")[0].parent.span_id == (
        tracing.root.context.span_id
    )
    assert tracing.root.is_recording()
    tracing.root.end()
    assert tracing.exporter.get_finished_spans()[-1].name == "run.root"


@pytest.mark.asyncio
async def test_another_agents_inherited_span_is_left_alone(tracing):
    """A ContextVar snapshot from another agent's task must not be ended here."""
    other_rail = AgentObservabilityRail(tracer=tracing.tracer)
    other_ctx = _iteration_ctx(_agent("teammate"))
    await other_rail.before_task_iteration(other_ctx)
    other_span = other_ctx.extra["_otel_agent_scope"].span

    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent("leader"))
    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    assert other_span.is_recording()


@pytest.mark.asyncio
async def test_the_run_root_is_ambient_again_after_a_round_closes(tracing):
    """Work that follows a round must not hang off the span that just ended."""
    from opentelemetry import trace as otel_trace

    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent())

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    assert otel_trace.get_current_span() is tracing.root


@pytest.mark.asyncio
async def test_a_concurrent_session_of_the_same_agent_is_never_drained(tracing):
    """Overlapping runs share an agent name — only the trace tells them apart.

    A process serves several chats at once, so the same agent name is live in
    all of them, and an inherited ContextVar snapshot can put another session's
    span in front of this round. Ending it would leave that run's remaining
    llm/tool spans parentless and break its trace mid-run.
    """
    other_root = tracing.tracer.start_span("run.root.other")
    shared_span_context.set_root_span(other_root)
    other_rail = AgentObservabilityRail(tracer=tracing.tracer)
    other_ctx = _iteration_ctx(_agent("coder"))
    await other_rail.before_task_iteration(other_ctx)
    other_span = other_ctx.extra["_otel_agent_scope"].span

    # This session's round starts while the other session's span is the one
    # left in the inherited context.
    shared_span_context.set_root_span(tracing.root)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(_agent("coder"))
    await rail.before_task_iteration(ctx)
    mine = ctx.extra["_otel_agent_scope"].span
    await rail.after_task_iteration(ctx)

    assert other_span.is_recording(), "a concurrent session's span was ended"
    assert mine.context.trace_id == tracing.root.context.trace_id
    assert mine.parent.span_id == tracing.root.context.span_id
    other_span.end()
    other_root.end()


@pytest.mark.asyncio
async def test_an_own_orphan_in_the_same_run_is_still_drained(tracing):
    """The orphan sweep must keep working inside one run — that is its job."""
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    first = _iteration_ctx(_agent("coder"), iteration=1)
    await rail.before_task_iteration(first)
    orphan = first.extra["_otel_agent_scope"].span

    second = _iteration_ctx(_agent("coder"), iteration=2)
    await rail.before_task_iteration(second)
    await rail.after_task_iteration(second)

    assert not orphan.is_recording()


@pytest.mark.asyncio
async def test_subagent_invoke_nests_under_the_tool_span_that_dispatched_it(tracing):
    """A task-tool sub-agent belongs *inside* the tool call that launched it.

    Parenting it to the dispatching agent instead leaves the ``task_tool`` span
    empty and the sub-agent's work sitting beside it, which is what made a
    dispatched run read as flat rather than layered.
    """
    from opentelemetry.trace import set_span_in_context

    parent_rail = AgentObservabilityRail(tracer=tracing.tracer)
    parent_ctx = _iteration_ctx(_agent("leader"))
    await parent_rail.before_task_iteration(parent_ctx)
    parent_span = parent_ctx.extra["_otel_agent_scope"].span

    tool_span = tracing.tracer.start_span(
        "tool.task_tool",
        context=set_span_in_context(parent_span),
    )
    shared_span_context.push_tool_span("task_tool", tool_span)

    subagent_rail = AgentObservabilityRail(tracer=tracing.tracer)
    subagent_ctx = AgentCallbackContext(
        agent=_agent("explore_agent", enable_task_loop=False),
        inputs=SimpleNamespace(query="look", result=None),
    )
    with execution_subject_scope(ExecutionSubject(
        subject_id="subagent:dispatch-1",
        display_name="Explore Agent",
        kind="subagent",
        parent_subject_id="main",
        session_id="session_sub_explore_1",
    )):
        await subagent_rail.before_invoke(subagent_ctx)
        model_ctx = AgentCallbackContext(
            agent=subagent_ctx.agent,
            inputs=ModelCallInputs(react_iteration=1),
            extra=subagent_ctx.extra,
        )
        await subagent_rail.before_model_call(model_ctx)
        await subagent_rail.after_react_iteration(model_ctx)
        assert tool_span.is_recording()
        await subagent_rail.after_invoke(subagent_ctx)
    tool_span.end()
    await parent_rail.after_task_iteration(parent_ctx)

    subagent_span = _finished(tracing.exporter, "agent.explore_agent.invoke")[0]
    assert subagent_span.parent.span_id == tool_span.context.span_id
    assert subagent_span.attributes[OJ_EXECUTION_SUBJECT_ID] == "subagent:dispatch-1"
    assert subagent_span.attributes[OJ_EXECUTION_SUBJECT_DISPLAY_NAME] == "Explore Agent"
    assert subagent_span.attributes[OJ_EXECUTION_SUBJECT_KIND] == "subagent"
    assert subagent_span.attributes[OJ_EXECUTION_SUBJECT_PARENT_ID] == "main"
    assert subagent_span.attributes[OJ_EXECUTION_SUBJECT_SESSION_ID] == "session_sub_explore_1"
    assert OJ_SPAN_FORCED_CLOSE not in _finished(
        tracing.exporter,
        "tool.task_tool",
    )[0].attributes


def _tool_ctx(agent, *, call_id: str, shared_extra=None, tool_name: str = "search"):
    return AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=ToolCall(
                id=call_id,
                type="function",
                name=tool_name,
                arguments='{"q":"hello"}',
            ),
            tool_name=tool_name,
            tool_args='{"q":"hello"}',
        ),
        extra=shared_extra if shared_extra is not None else {},
    )


@pytest.mark.asyncio
async def test_ability_tool_span_is_authoritative_and_carries_old_and_new_fields(tracing):
    card = ToolCard(id="resource-search", name="search", description="Search documents")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    agent_span = iteration_ctx.extra["_otel_agent_scope"].span
    model_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(react_iteration=2),
        extra=iteration_ctx.extra,
    )
    await rail.before_model_call(model_ctx)
    step_span = shared_span_context.get_current_agent_span()
    assert step_span is not None

    ctx = _tool_ctx(agent, call_id="call-1")
    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = {"answer": 42}
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert agent_span.parent.span_id == tracing.root.context.span_id
    assert step_span.parent.span_id == tracing.root.context.span_id
    assert span.parent.span_id == step_span.context.span_id
    assert span.attributes[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert span.attributes[GEN_AI_TOOL_NAME] == "search"
    assert span.attributes[GEN_AI_TOOL_ID] == "resource-search"
    assert span.attributes[GEN_AI_TOOL_CALL_ID] == "call-1"
    assert span.attributes[GEN_AI_TOOL_INPUT] == '{"q":"hello"}'
    assert span.attributes[GEN_AI_TOOL_CALL_ARGUMENTS] == '{"q":"hello"}'
    assert span.attributes[GEN_AI_TOOL_CALL_RESULT] == '{"answer": 42}'
    assert span.attributes[GEN_AI_AGENT_ID] == "agent-solo"
    assert span.attributes[GEN_AI_AGENT_DESCRIPTION] == "solo description"
    assert span.attributes[OJ_TOOL_AUTHORITATIVE] is True
    assert span.attributes[OJ_TOOL_RESOURCE_ID] == "resource-search"
    assert span.attributes[OJ_TOOL_TYPE] == "tool"
    assert span.attributes[OJ_STEP_ID] == f"{step_span.context.span_id:016x}"
    assert span.attributes[OJ_STEP_NUMBER] == 2


@pytest.mark.asyncio
async def test_iteration_and_tool_publish_live_snapshots_before_they_end(
    tracing,
    monkeypatch,
) -> None:
    published: list[tuple[object, str, bool]] = []
    monkeypatch.setattr(
        rail_module,
        "publish_span_snapshot",
        lambda span, kind: published.append((span, kind, span.is_recording())),
    )
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)

    await rail.before_task_iteration(iteration_ctx)
    tool_ctx = _tool_ctx(agent, call_id="call-live")
    await rail.before_tool_call(tool_ctx)

    assert [(span.name, kind, recording) for span, kind, recording in published] == [
        ("agent.solo.task_iteration.1", "attributes", True),
        ("tool.search", "attributes", True),
    ]
    tool_ctx.inputs.tool_result = "done"
    await rail.after_tool_call(tool_ctx)
    await rail.after_task_iteration(iteration_ctx)


@pytest.mark.asyncio
async def test_explicit_compatible_card_version_is_emitted_without_guessing(tracing):
    agent = _agent()
    agent.card = SimpleNamespace(
        id="versioned-agent",
        name="Versioned Agent",
        description="Versioned description",
        version="2.1.0",
    )
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    ctx = _iteration_ctx(agent)

    await rail.before_task_iteration(ctx)
    await rail.after_task_iteration(ctx)

    span = _finished(tracing.exporter, "agent.solo.task_iteration.1")[0]
    assert span.attributes[GEN_AI_AGENT_ID] == "versioned-agent"
    assert span.attributes[GEN_AI_AGENT_DESCRIPTION] == "Versioned description"
    assert span.attributes[GEN_AI_AGENT_VERSION] == "2.1.0"


@pytest.mark.asyncio
async def test_llm_child_context_propagation_inherits_agent_card_identity(tracing):
    agent = _agent()
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)

    child = tracing.tracer.start_span("llm.child")
    handler._propagate_session_context(child)
    child.end()
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "llm.child")[0]
    assert span.attributes[GEN_AI_AGENT_ID] == "agent-solo"
    assert span.attributes[GEN_AI_AGENT_NAME] == "solo"
    assert span.attributes[GEN_AI_AGENT_DESCRIPTION] == "solo description"
    assert GEN_AI_AGENT_VERSION not in span.attributes


@pytest.mark.asyncio
async def test_subagent_ambient_session_does_not_replace_trajectory_owner(tracing):
    agent = _agent("explore_agent", enable_task_loop=False)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=SimpleNamespace(query="look", result=None),
    )

    with execution_subject_scope(ExecutionSubject(
        subject_id="subagent:dispatch-1",
        display_name="Explore Agent",
        kind="subagent",
        parent_subject_id="main",
        session_id="session_sub_explore_1",
    )):
        await rail.before_invoke(ctx)
        set_current_session_id("session_sub_explore_1")
        try:
            child = tracing.tracer.start_span("llm.child")
            handler._propagate_session_context(child)
            child.end()
        finally:
            clear_current_session_id()
        await rail.after_invoke(ctx)

    span = _finished(tracing.exporter, "llm.child")[0]
    assert span.attributes[OJ_SESSION_ID] == "session"
    assert span.attributes[GEN_AI_CONVERSATION_ID] == "session"
    assert span.attributes[LANGFUSE_SESSION_ID] == "session"
    assert span.attributes[AT_SESSION_ID] == "session"
    assert span.attributes[OJ_EXECUTION_SUBJECT_SESSION_ID] == "session_sub_explore_1"


@pytest.mark.asyncio
async def test_parallel_same_name_tools_keep_distinct_scopes(tracing):
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    shared_extra = {}

    async def execute(call_id: str) -> None:
        ctx = _tool_ctx(agent, call_id=call_id, shared_extra=shared_extra)
        await rail.before_tool_call(ctx)
        await asyncio.sleep(0)
        ctx.inputs.tool_result = call_id
        await rail.after_tool_call(ctx)

    await asyncio.gather(execute("call-a"), execute("call-b"))
    await rail.after_task_iteration(iteration_ctx)

    spans = _finished(tracing.exporter, "tool.search")
    assert len(spans) == 2
    assert {span.attributes[GEN_AI_TOOL_CALL_ID] for span in spans} == {
        "call-a",
        "call-b",
    }
    assert {span.attributes[GEN_AI_TOOL_CALL_RESULT] for span in spans} == {
        "call-a",
        "call-b",
    }


@pytest.mark.asyncio
async def test_tool_exception_closes_authoritative_span_with_error(tracing):
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-error")

    await rail.before_tool_call(ctx)
    ctx.exception = ValueError("bad tool")
    await rail.on_tool_exception(ctx)
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "ValueError"


@pytest.mark.asyncio
async def test_concrete_tool_global_callbacks_enrich_without_duplicate_span(tracing):
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-global")

    await rail.before_tool_call(ctx)
    await handler.on_tool_call_started(
        tool_name="search",
        tool_id="resource-search",
        inputs=(({"q": "hello"},), {}),
    )
    await handler.on_tool_call_finished(tool_name="search", result={"answer": 42})
    ctx.inputs.tool_result = {"answer": 42}
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    spans = _finished(tracing.exporter, "tool.search")
    assert len(spans) == 1
    assert spans[0].attributes[GEN_AI_TOOL_CALL_ID] == "call-global"
    assert spans[0].attributes[GEN_AI_TOOL_ID] == "resource-search"


@pytest.mark.asyncio
async def test_mcp_raw_lifecycle_name_enriches_model_facing_authoritative_span(tracing):
    resource_id = "playwright.playwright-official.browser_navigate"
    model_name = "mcp_playwright-official_browser_navigate"
    card = ToolCard(id=resource_id, name=model_name)
    agent = _agent()
    agent.ability_manager = SimpleNamespace(
        get=lambda name: card,
        _resolve_mcp_tool_scope=lambda name: ("playwright", "browser_navigate"),
    )
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-mcp", tool_name=model_name)

    await rail.before_tool_call(ctx)
    await handler.on_tool_call_started(
        tool_name="browser_navigate",
        tool_id=resource_id,
        inputs=(({"url": "https://example.test"},), {}),
    )
    await handler.on_tool_call_finished(
        tool_name="browser_navigate",
        tool_id=resource_id,
        result={"ok": True},
    )
    ctx.inputs.tool_result = {"ok": True}
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    assert len(_finished(tracing.exporter, f"tool.{model_name}")) == 1
    assert _finished(tracing.exporter, "tool.browser_navigate") == []


@pytest.mark.asyncio
async def test_non_mcp_name_mismatch_does_not_match_by_resource_id(tracing):
    resource_id = "resource-wrapper"
    card = ToolCard(id=resource_id, name="wrapper")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-wrapper", tool_name="wrapper")

    await rail.before_tool_call(ctx)
    await handler.on_tool_call_started(
        tool_name="nested",
        tool_id=resource_id,
        inputs=((), {}),
    )
    await handler.on_tool_call_finished(
        tool_name="nested",
        tool_id=resource_id,
        result="done",
    )
    ctx.inputs.tool_result = "done"
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    assert len(_finished(tracing.exporter, "tool.wrapper")) == 1
    assert len(_finished(tracing.exporter, "tool.nested")) == 1


@pytest.mark.asyncio
async def test_a_raised_tool_call_still_records_the_result_the_model_saw(tracing):
    """A raised call is handed back to the model as a tool result, so the span keeps one."""
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-raised")

    await rail.before_tool_call(ctx)
    ctx.exception = ValueError("bad tool")
    await rail.on_tool_exception(ctx)
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "ValueError"
    assert span.attributes[GEN_AI_TOOL_OUTPUT] == "Ability execution error: bad tool"
    assert span.attributes[GEN_AI_TOOL_CALL_RESULT] == "Ability execution error: bad tool"
    assert span.attributes[LANGFUSE_OBSERVATION_OUTPUT] == "Ability execution error: bad tool"


@pytest.mark.asyncio
async def test_a_result_reporting_failure_closes_the_span_as_an_error(tracing):
    """``ToolOutput(success=False)`` never raises; the status has to come from the result."""
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-failed")

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = ToolOutput(success=False, error="exit code 1")
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.status.description == "exit code 1"
    assert span.attributes["error.type"] == TOOL_REPORTED_FAILURE
    assert "exit code 1" in span.attributes[GEN_AI_TOOL_OUTPUT]


@pytest.mark.asyncio
async def test_a_failing_result_without_an_error_message_still_reports_an_error(tracing):
    """The reason is optional; ``success=False`` alone is enough to fail the span."""
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-bare-failure")

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = ToolOutput(success=False)
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == TOOL_REPORTED_FAILURE


@pytest.mark.asyncio
async def test_a_result_without_a_success_field_is_left_alone(tracing):
    """Workflow outputs and raw payloads carry no ``success``; they must stay OK."""
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-plain")

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = {"answer": 42}
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "OK"
    assert "error.type" not in span.attributes


@pytest.mark.asyncio
async def test_a_succeeding_result_still_closes_the_span_as_ok(tracing):
    """The common path must not regress into an error just because it is read now."""
    card = ToolCard(id="resource-search", name="search")
    agent = _agent()
    agent.ability_manager = SimpleNamespace(get=lambda name: card)
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)
    ctx = _tool_ctx(agent, call_id="call-ok")

    await rail.before_tool_call(ctx)
    ctx.inputs.tool_result = ToolOutput(success=True, data={"answer": 42})
    await rail.after_tool_call(ctx)
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "OK"
    assert "error.type" not in span.attributes


@pytest.mark.asyncio
async def test_the_global_tool_callbacks_also_read_failure_from_the_result(tracing):
    """Team mode owns the span in the callback handler; it needs the same reading."""
    agent = _agent()
    rail = AgentObservabilityRail(tracer=tracing.tracer)
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, backend="otlp"),
        tracer=tracing.tracer,
    )
    iteration_ctx = _iteration_ctx(agent)
    await rail.before_task_iteration(iteration_ctx)

    await handler.on_tool_call_started(
        tool_name="search",
        tool_id="resource-search",
        inputs=(({"q": "hello"},), {}),
    )
    await handler.on_tool_call_finished(
        tool_name="search",
        result=ToolOutput(success=False, error="exit code 1"),
    )
    await rail.after_task_iteration(iteration_ctx)

    span = _finished(tracing.exporter, "tool.search")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.status.description == "exit code 1"
    assert span.attributes["error.type"] == TOOL_REPORTED_FAILURE
