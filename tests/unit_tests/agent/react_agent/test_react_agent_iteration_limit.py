"""Iteration exhaustion is recoverable without executing more tools."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture
def rig():
    agent = ReActAgent(card=AgentCard(name="iteration-limit-test"))
    agent.configure(ReActAgentConfig(model_name="fake", max_iterations=1))
    messages = []
    context = MagicMock()
    context.get_messages.side_effect = lambda **_: list(messages)
    context.add_messages = AsyncMock(side_effect=lambda msg: messages.append(msg))

    async def window(**kwargs):
        return MagicMock(
            get_messages=lambda: kwargs["system_messages"] + list(messages),
            get_tools=lambda: kwargs["tools"],
        )

    context.get_context_window = AsyncMock(side_effect=window)
    engine = MagicMock()
    engine.create_context = AsyncMock(return_value=context)
    engine.save_contexts = AsyncMock()
    agent.context_engine = engine
    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()
    model = MagicMock()
    model.invoke = AsyncMock()
    return agent, context, session, model, messages


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["error", "return_observations", "final_answer"])
async def test_exhaustion_invoke(rig, strategy):
    agent, context, session, model, messages = rig
    agent.config.on_max_iterations = strategy
    tool_call = ToolCall(type="function", id="lookup-1", name="lookup", arguments="{}")
    model.invoke.side_effect = [
        AssistantMessage(content="", tool_calls=[tool_call]),
        AssistantMessage(content="Found row 1; further searching was truncated."),
    ]
    agent.ability_manager.list_tool_info = AsyncMock(
        return_value=[ToolInfo(name="lookup", description="lookup", parameters={})]
    )
    agent.ability_manager.execute = AsyncMock(
        return_value=[("row 1", ToolMessage(content="row 1", tool_call_id="lookup-1"))]
    )
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "find a row"}, session=session)
    assert result["termination_reason"] == "max_iterations"
    assert result["iterations_used"] == 1
    assert result["truncated_by_max_iterations"] is True
    assert agent.ability_manager.execute.await_count == 1
    assert model.invoke.await_count == (2 if strategy == "final_answer" else 1)
    if strategy == "final_answer":
        assert result["result_type"] == "answer"
        assert model.invoke.await_args.kwargs["tools"] is None
        assert any(
            isinstance(m, ToolMessage) and m.content == "row 1" for m in model.invoke.await_args.kwargs["messages"]
        )
    else:
        assert result["result_type"] == "error"
    if strategy != "error":
        assert result["observations"][0]["content"] == "row 1"
        assert result["observations"][0]["name"] == "lookup"
    # Policies that only describe exhaustion must retain the original save count.
    assert agent.context_engine.save_contexts.await_count == (2 if strategy == "final_answer" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        AssistantMessage(content="", tool_calls=[ToolCall(type="function", id="bad", name="lookup", arguments="{}")]),
        AssistantMessage(content=""),
        AssistantMessage(content=" \n\t"),
        AssistantMessage(content="An incomplete answer", finish_reason="length"),
        AssistantMessage(content="An incomplete answer", finish_reason="max_tokens"),
        AssistantMessage(content="Filtered answer", finish_reason="content_filter"),
        RuntimeError("provider unavailable"),
    ],
)
async def test_failed_finalization_keeps_partial_results(rig, answer):
    agent, context, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="find"), session=session)
    ctx.extra.update(_react_iteration=1, _iteration_observations=[{"content": "row 1"}])
    call = AsyncMock(side_effect=answer) if isinstance(answer, Exception) else AsyncMock(return_value=answer)
    with patch.object(agent, "_call_model", call):
        result = await agent._finish_max_iterations(ctx, context, session)
    assert result["result_type"] == "error"
    assert result["observations"] == [{"content": "row 1"}]
    assert result["finalization_error"]
    assert "_finalizing_max_iterations" not in ctx.extra


@pytest.mark.asyncio
async def test_finalization_cancellation_propagates(rig):
    agent, context, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="find"), session=session)
    with patch.object(agent, "_call_model", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await agent._finish_max_iterations(ctx, context, session)
    assert "_finalizing_max_iterations" not in ctx.extra


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        ReActAgentConfig(on_max_iterations="keep_running")


@pytest.mark.asyncio
async def test_final_window_removes_tools_reintroduced_by_rails(rig):
    agent, context, session, _, _ = rig
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        context=context,
        inputs=ModelCallInputs(tools=[ToolInfo(name="lookup", description="lookup", parameters={})]),
        extra={"_finalizing_max_iterations": True},
    )
    await agent._build_model_context_window(ctx)
    assert context.get_context_window.await_args.kwargs["tools"] is None
    assert ctx.inputs.tools is None


@pytest.mark.asyncio
async def test_finalization_preserves_force_finish(rig):
    agent, context, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="find"), session=session)
    expected = {"result_type": "error", "output": "blocked by a rail"}

    async def call(*_):
        ctx.request_force_finish(expected)
        return AssistantMessage(content="do not expose")

    with patch.object(agent, "_call_model", side_effect=call):
        assert await agent._finish_max_iterations(ctx, context, session) == expected


@pytest.mark.asyncio
async def test_streaming_exhaustion_produces_answer_and_reason(rig):
    from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

    agent, _, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    model = MockLLMModel()
    model.set_responses(
        [
            create_tool_call_response("lookup", "{}"),
            create_text_response("Found row 1"),
        ]
    )
    requests = []

    async def stream(**kwargs):
        requests.append(kwargs)
        async for chunk in model.stream(**kwargs):
            yield chunk

    fake = MagicMock()
    fake.stream = stream
    agent.ability_manager.list_tool_info = AsyncMock(
        return_value=[ToolInfo(name="lookup", description="lookup", parameters={})]
    )
    agent.ability_manager.execute = AsyncMock(
        return_value=[
            ("row 1", ToolMessage(content="row 1", tool_call_id="lookup-1", name="lookup")),
        ]
    )
    with patch.object(agent, "_get_llm", return_value=fake):
        result = await agent.invoke({"query": "find"}, session=session, _streaming=True)
    assert result["result_type"] == "answer"
    assert result["termination_reason"] == "max_iterations"
    assert requests[-1]["tools"] is None
    assert agent.ability_manager.execute.await_count == 1
    assert session.write_stream.await_count > 0


@pytest.mark.asyncio
async def test_resume_recovers_evidence_without_previous_turn_leak(rig):
    from openjiuwen.core.foundation.llm import UserMessage

    agent, _, session, model, messages = rig
    agent.config.on_max_iterations = "return_observations"
    messages.extend(
        [
            UserMessage(content="previous turn"),
            ToolMessage(content="old private evidence", tool_call_id="old"),
            UserMessage(content="current turn", metadata={"_openjiuwen_message_source_kind": "query"}),
            AssistantMessage(
                content="", tool_calls=[ToolCall(type="function", id="prior", name="lookup", arguments="{}")]
            ),
            ToolMessage(content="retained before pause", tool_call_id="prior"),
            UserMessage(content="approved", metadata={"_openjiuwen_message_source_kind": "resume"}),
        ]
    )
    model.invoke.return_value = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(type="function", id="new", name="lookup", arguments="{}"),
        ],
    )
    agent.ability_manager.execute = AsyncMock(
        return_value=[
            ("new evidence", ToolMessage(content="new evidence", tool_call_id="new")),
        ]
    )
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "", "_resume_continuation": True}, session=session)
    assert [o["content"] for o in result["observations"]] == ["retained before pause", "new evidence"]
    assert all(o["name"] == "lookup" for o in result["observations"])


@pytest.mark.asyncio
async def test_new_turn_does_not_return_previous_observations(rig):
    from openjiuwen.core.foundation.llm import UserMessage

    agent, _, session, model, messages = rig
    agent.config.on_max_iterations = "return_observations"
    messages.extend([UserMessage(content="old query"), ToolMessage(content="old evidence", tool_call_id="old")])
    model.invoke.return_value = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(type="function", id="new", name="lookup", arguments="{}"),
        ],
    )
    agent.ability_manager.execute = AsyncMock(
        return_value=[
            ("new evidence", ToolMessage(content="new evidence", tool_call_id="new")),
        ]
    )
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "new query"}, session=session)
    assert [o["content"] for o in result["observations"]] == ["new evidence"]


@pytest.mark.asyncio
async def test_finalization_cancellation_not_masked_by_save_failure(rig):
    agent, context, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    agent.context_engine.save_contexts.side_effect = OSError("storage unavailable")
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="find"), session=session)
    with patch.object(agent, "_call_model", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await agent._finish_max_iterations(ctx, context, session)
    agent.context_engine.save_contexts.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["model_continue", "steering"])
async def test_finalization_honors_pending_work(rig, pending):
    agent, context, session, _, _ = rig
    agent.config.on_max_iterations = "final_answer"
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="find"), session=session)
    ctx.extra["_iteration_observations"] = [{"content": "row 1"}]

    async def call(*_):
        if pending == "model_continue":
            # Browser protocol rails request another step when a tool call is missing.
            ctx.request_model_continue()
        else:
            ctx.bind_steering_queue(asyncio.Queue())
            ctx.push_steering("The user has corrected the requested task")
        return AssistantMessage(content="Unverified success")

    with patch.object(agent, "_call_model", side_effect=call) as model_call:
        result = await agent._finish_max_iterations(ctx, context, session)
    assert result["result_type"] == "error"
    assert result["observations"] == [{"content": "row 1"}]
    assert result["finalization_error"]
    model_call.assert_awaited_once()
    assert not any(isinstance(m, AssistantMessage) for m in context.get_messages())


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["error", "return_observations", "final_answer"])
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_public_stream_returns_structured_exhaustion(strategy, finish_reason):
    from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.fixtures.mock_llm import MockLLMModel

    agent = ReActAgent(card=AgentCard(id=f"limit-stream-{strategy}-{finish_reason}"))
    agent.configure(ReActAgentConfig(model_name="fake", max_iterations=1, on_max_iterations=strategy))
    model = MockLLMModel()
    requests = []

    async def stream(**kwargs):
        requests.append(kwargs)
        if kwargs.get("tools"):
            yield AssistantMessageChunk(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(type="function", id="lookup-1", name="lookup", arguments="{}"),
                ],
            )
        else:
            yield AssistantMessageChunk(content="Found row 1", finish_reason=finish_reason)

    model.stream = stream
    agent.ability_manager.list_tool_info = AsyncMock(
        return_value=[ToolInfo(name="lookup", description="lookup", parameters={})]
    )
    agent.ability_manager.execute = AsyncMock(
        return_value=[
            ("row 1", ToolMessage(content="row 1", tool_call_id="lookup-1", name="lookup")),
        ]
    )
    await Runner.start()
    try:
        with patch.object(agent, "_get_llm", return_value=model):
            chunks = [chunk async for chunk in agent.stream({"query": "find", "conversation_id": agent.card.id})]
        final = [chunk.payload for chunk in chunks if chunk.type == "answer"]
        assert len(final) == 1
        assert final[0]["termination_reason"] == "max_iterations"
        assert final[0]["iterations_used"] == 1
        assert final[0]["truncated_by_max_iterations"] is True
        expected = "answer" if strategy == "final_answer" and finish_reason == "stop" else "error"
        assert final[0]["result_type"] == expected
        if strategy != "error":
            assert final[0]["observations"][0]["content"] == "row 1"
        assert len(requests) == (2 if strategy == "final_answer" else 1)
        if strategy == "final_answer":
            assert not requests[-1].get("tools")
        agent.ability_manager.execute.assert_awaited_once()
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_observations_include_workflow_replay_without_duplicates(rig):
    from openjiuwen.core.foundation.llm import UserMessage

    agent, context, session, _, messages = rig
    agent.config.on_max_iterations = "return_observations"
    messages.extend(
        [
            UserMessage(content="current query"),
            ToolMessage(content="first result", tool_call_id="first"),
            ToolMessage(content="workflow replay", tool_call_id="replayed", name="workflow"),
        ]
    )
    ctx = AgentCallbackContext(agent=agent, context=context, session=session)
    ctx.extra["_iteration_observations"] = [{"content": "first result", "tool_call_id": "first"}]
    result = await agent._finish_max_iterations(ctx, context, session)
    assert [o["tool_call_id"] for o in result["observations"]] == ["first", "replayed"]
    assert result["observations"][1]["name"] == "workflow"


@pytest.mark.asyncio
async def test_before_model_rail_can_interrupt_finalization(rig):
    from openjiuwen.core.single_agent.rail.base import AgentRail

    agent, context, session, model, _ = rig
    agent.config.on_max_iterations = "final_answer"
    expected = {"result_type": "interrupt", "output": "approval required"}

    class InterruptFinalizer(AgentRail):
        async def before_model_call(self, ctx):
            ctx.request_force_finish(expected)

    await agent.register_rail(InterruptFinalizer())
    ctx = AgentCallbackContext(agent=agent, context=context, session=session)
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent._finish_max_iterations(ctx, context, session)
    assert result == expected
    model.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalization_context_recovery_retry_stays_tool_free(rig):
    agent, context, session, model, _ = rig
    agent.config.on_max_iterations = "final_answer"
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=True)
    model.invoke.side_effect = [RuntimeError("context window exceeded"), AssistantMessage(content="row 1")]
    ctx = AgentCallbackContext(agent=agent, context=context, session=session)
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent._finish_max_iterations(ctx, context, session)
    assert result["result_type"] == "answer"
    assert result["termination_reason"] == "max_iterations"
    assert model.invoke.await_count == 2
    assert all(call.kwargs["tools"] is None for call in model.invoke.await_args_list)
    agent.context_engine.recover_from_model_exception.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["error", "return_observations", "final_answer"])
async def test_last_iteration_rail_finish_takes_precedence_over_exhaustion(rig, strategy):
    from openjiuwen.core.single_agent.rail.base import AgentRail

    agent, _, session, model, _ = rig
    agent.config.on_max_iterations = strategy
    expected = {"result_type": "error", "output": "Stopped explicitly by the caller"}

    class StopAfterIteration(AgentRail):
        async def after_react_iteration(self, ctx):
            ctx.request_force_finish(expected)

    await agent.register_rail(StopAfterIteration())
    model.invoke.return_value = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(type="function", id="lookup-1", name="lookup", arguments="{}"),
        ],
    )
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.ability_manager.execute = AsyncMock(
        return_value=[
            ("row 1", ToolMessage(content="row 1", tool_call_id="lookup-1", name="lookup")),
        ]
    )
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "find"}, session=session)
    assert result == expected
    model.invoke.assert_awaited_once()
    agent.context_engine.save_contexts.assert_awaited_once()
