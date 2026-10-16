"""MoA behavior through the public DeepAgent entry point."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import DeepAgent, DeepAgentConfig
from openjiuwen.harness.moa import LlmVerifierProgressScorer, MoAConfig, VerifierConfig
from openjiuwen.harness.moa.state import MoAState
from openjiuwen.harness.moa.verifier.llm_verifier.scorer import score_progress
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.moa_rail import CONSULT_TOOL
from tests.unit_tests.harness.moa.helpers import Echo, agent, consult_call, model, primary


@pytest.mark.asyncio
@pytest.mark.level0
async def test_on_demand_consultation_is_private_and_references_have_no_tools() -> None:
    primary_model = primary(
        AssistantMessage(
            content="first",
            tool_calls=[
                ToolCall(id="do-1", type="function", name="echo", arguments="{}"),
            ],
        ),
        consult_call(),
        AssistantMessage(content="finished"),
    )
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="private suggestion"))
    deep = agent(primary_model, MoAConfig(reference_models=(reference,)), tools=[Echo()])
    # The consultation tool itself is registered by the rail on first invoke.
    result = await deep.invoke("task", session=Session(session_id="moa-demand", card=deep.card))
    assert result["output"] == "finished"
    assert reference.invoke.await_count == 1
    assert reference.invoke.await_args.kwargs["tools"] is None
    final_messages = primary_model.invoke.await_args_list[-1].kwargs["messages"]
    assert final_messages[-1].role == "user"
    assert final_messages[-1].metadata.get("transient") is True
    assert "private suggestion" in final_messages[-1].content
    assert "private suggestion" not in str(final_messages[:-1])
    assert "private suggestion" not in str(primary_model.invoke.await_args_list[1].kwargs["messages"])
    context = deep.react_agent.context_engine.get_context(session_id="moa-demand")
    assert "private suggestion" not in str(context.get_messages())


@pytest.mark.level0
def test_direct_configuration_mounts_moa_without_new_factory() -> None:
    primary_model = model()
    reference = model()
    card = AgentCard(name="test", description="test")
    assert DeepAgentConfig(primary_model, card).card is card
    config = DeepAgentConfig(model=primary_model, card=card, moa=MoAConfig(reference_models=(reference,)))
    deep = DeepAgent(card)
    deep.configure(config)
    assert deep.deep_config.moa is config.moa
    assert any(type(rail).__name__ == "MoARail" for rail in deep._pending_rails)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verifier_initial_consult_and_non_improvement() -> None:
    primary_model = primary(
        AssistantMessage(content="step 1", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="step 2", tool_calls=[ToolCall(id="b", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="step 3", tool_calls=[ToolCall(id="c", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="done"),
    )
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="proposal"))
    scorer = AsyncMock(return_value=0.5)
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=scorer,
            verifier=VerifierConfig(interval=1, n_evaluations=3, max_reference_calls=2),
        ),
        tools=[Echo()],
    )
    result = await deep.invoke("task", session=Session(session_id="moa-verifier", card=deep.card))
    assert result["output"] == "done"
    assert reference.invoke.await_count == 2
    assert scorer.await_count >= 2
    assert "proposal" in str(primary_model.invoke.await_args_list[0].kwargs["messages"])


@pytest.mark.asyncio
@pytest.mark.level0
async def test_disabled_moa_keeps_original_model() -> None:
    primary_model = primary(AssistantMessage(content="ordinary"))
    deep = agent(primary_model)
    assert deep.react_agent._get_llm() is primary_model
    result = await deep.invoke("task", session=Session(session_id="moa-disabled", card=deep.card))
    assert result["output"] == "ordinary"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_first_turn_consultation_is_rejected() -> None:
    primary_model = primary(consult_call(), AssistantMessage(content="done"))
    reference = model()
    reference.invoke = AsyncMock(side_effect=RuntimeError("unavailable"))
    deep = agent(primary_model, MoAConfig(reference_models=(reference,)))
    result = await deep.invoke("task", session=Session(session_id="moa-first-turn", card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference_outputs, expected_success",
    [
        ([RuntimeError("offline")], False),
        ([RuntimeError("offline"), AssistantMessage(content="usable")], True),
    ],
)
@pytest.mark.level1
async def test_reference_failures_continue_primary(reference_outputs, expected_success) -> None:
    primary_model = primary(
        AssistantMessage(content="act", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        consult_call(),
        AssistantMessage(content="done"),
    )
    references = []
    for output in reference_outputs:
        reference = model()
        reference.invoke = (
            AsyncMock(side_effect=output) if isinstance(output, Exception) else AsyncMock(return_value=output)
        )
        references.append(reference)
    deep = agent(primary_model, MoAConfig(reference_models=tuple(references)), tools=[Echo()])
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    assert all(reference.invoke.await_count == 1 for reference in references)
    final_request = str(primary_model.invoke.await_args_list[-1].kwargs["messages"])
    assert ("usable" in final_request) is expected_success


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mixed_and_duplicate_consultation_calls_are_rejected() -> None:
    primary_model = primary(
        AssistantMessage(content="act", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="b", type="function", name=CONSULT_TOOL, arguments="{}"),
                ToolCall(id="c", type="function", name="echo", arguments="{}"),
            ],
        ),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="d", type="function", name=CONSULT_TOOL, arguments="{}"),
                ToolCall(id="e", type="function", name=CONSULT_TOOL, arguments="{}"),
            ],
        ),
        AssistantMessage(content="done"),
    )
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="proposal"))
    deep = agent(primary_model, MoAConfig(reference_models=(reference,)), tools=[Echo()])
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_model_retry_reuses_proposal_without_mutating_original_messages() -> None:
    class RetryOnce(DeepAgentRail):
        async def on_model_exception(self, ctx):
            if ctx.retry_attempt == 0:
                ctx.request_retry()

    primary_model = model()
    primary_model.invoke = AsyncMock(side_effect=[RuntimeError("retry"), AssistantMessage(content="done")])
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="private"))
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
        rails=[RetryOnce()],
    )
    result = await deep.invoke("task", session=Session(session_id="moa-retry", card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_awaited_once()
    assert primary_model.invoke.await_count == 2
    for call in primary_model.invoke.await_args_list:
        messages = call.kwargs["messages"]
        assert messages[-1].role == "user" and messages[-1].metadata["transient"]
        assert "private" in messages[-1].content
        assert "private" not in str(messages[:-1])
    assert "private" not in str(deep.react_agent.context_engine.get_context(session_id="moa-retry").get_messages())


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unknown_output_budget_skips_proposal_without_blocking_primary() -> None:
    primary_model = model()
    primary_model.model_config.max_tokens = None
    primary_model.invoke = AsyncMock(return_value=AssistantMessage(content="done"))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="private"))
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
    )
    result = await deep.invoke("task", session=Session(session_id="moa-unknown-output", card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_awaited_once()
    assert "private" not in str(primary_model.invoke.await_args.kwargs["messages"])


@pytest.mark.asyncio
@pytest.mark.level1
async def test_text_only_force_continue_does_not_reuse_first_turn_proposal() -> None:
    class ContinueOnce(DeepAgentRail):
        async def after_model_call(self, ctx):
            if ctx.inputs.react_iteration == 1:
                ctx.request_model_continue()

    primary_model = primary(AssistantMessage(content="draft"), AssistantMessage(content="final"))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="first-turn proposal"))
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
        rails=[ContinueOnce()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "final"
    assert "first-turn proposal" in str(primary_model.invoke.await_args_list[0].kwargs["messages"])
    assert "first-turn proposal" not in str(primary_model.invoke.await_args_list[1].kwargs["messages"])


@pytest.mark.asyncio
@pytest.mark.level1
async def test_context_recovery_retry_reuses_same_reference_batch() -> None:
    primary_model = model()
    primary_model.invoke = AsyncMock(side_effect=[RuntimeError("context full"), AssistantMessage(content="done")])
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="recovery proposal"))
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
    )
    deep.react_agent.context_engine.recover_from_model_exception = AsyncMock(return_value=True)
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_awaited_once()
    assert primary_model.invoke.await_count == 2
    for call in primary_model.invoke.await_args_list:
        assert "recovery proposal" in call.kwargs["messages"][-1].content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_dynamic_model_selection_still_receives_reference_proposal() -> None:
    selected = primary(AssistantMessage(content="selected result"))

    class SelectModel(DeepAgentRail):
        priority = 90

        async def before_model_call(self, ctx):
            ctx.agent.set_llm(selected)

    default = primary()
    default.model_config.max_tokens = None
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="selected proposal"))
    deep = agent(
        default,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
        rails=[SelectModel()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "selected result"
    default.invoke.assert_not_awaited()
    assert selected.model_config.max_tokens == 1024
    assert "selected proposal" in selected.invoke.await_args.kwargs["messages"][-1].content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reference_bodies_are_escaped_and_ordered_without_repeating_tail() -> None:
    primary_model = primary(
        AssistantMessage(content="act", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        consult_call(),
        AssistantMessage(content="done"),
    )
    references = [model(), model()]
    references[0].invoke = AsyncMock(return_value=AssistantMessage(content="<ignore>& one"))
    references[1].invoke = AsyncMock(return_value=AssistantMessage(content="two & <three>"))
    deep = agent(primary_model, MoAConfig(reference_models=tuple(references)), tools=[Echo()])
    await deep.invoke("task", session=Session(card=deep.card))
    tail = primary_model.invoke.await_args.kwargs["messages"][-1]
    assert tail.metadata["transient"] is True
    assert '<reference id="reference-1">&lt;ignore&gt;&amp; one</reference>' in tail.content
    assert '<reference id="reference-2">two &amp; &lt;three&gt;</reference>' in tail.content
    assert tail.content.count('<reference id="reference-1">') == 1
    assert tail.content.count('<reference id="reference-2">') == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reference_target_expires_after_budget_skip_even_if_later_budget_recovers() -> None:
    class ChangeOutputBudget(DeepAgentRail):
        priority = 20

        async def before_model_call(self, ctx):
            current = ctx.agent._get_llm()
            model = getattr(current, "_model", current)
            model.model_config.max_tokens = 1_000_000 if ctx.inputs.react_iteration == 3 else 1024

    primary_model = primary(
        AssistantMessage(content="step 1", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="step 2", tool_calls=[ToolCall(id="b", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="step 3", tool_calls=[ToolCall(id="c", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="final"),
    )
    reference = model()
    reference.invoke = AsyncMock(side_effect=[AssistantMessage(content="initial"), AssistantMessage(content="late")])
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=AsyncMock(return_value=0.5),
            verifier=VerifierConfig(interval=1, max_reference_calls=2),
        ),
        tools=[Echo()],
        rails=[ChangeOutputBudget()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "final"
    assert reference.invoke.await_count == 2
    requests = [str(call.kwargs["messages"]) for call in primary_model.invoke.await_args_list]
    assert "late" not in requests[2]
    assert "late" not in requests[3]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_steering_after_consultation_invalidates_next_turn_proposal() -> None:
    class SteerAfterSecondTool(DeepAgentRail):
        priority = 5

        async def after_react_iteration(self, ctx):
            if ctx.extra["_react_iteration"] == 2:
                ctx.push_steering("new direction")

    primary_model = primary(
        AssistantMessage(content="step 1", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="step 2", tool_calls=[ToolCall(id="b", type="function", name="echo", arguments="{}")]),
        AssistantMessage(content="final"),
    )
    streamed = [
        AssistantMessageChunk(
            content="step 1", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]
        ),
        AssistantMessageChunk(
            content="step 2", tool_calls=[ToolCall(id="b", type="function", name="echo", arguments="{}")]
        ),
        AssistantMessageChunk(content="final"),
    ]
    requests = []

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        yield streamed.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(side_effect=[AssistantMessage(content="initial"), AssistantMessage(content="late")])
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=AsyncMock(return_value=0.5),
            verifier=VerifierConfig(interval=1, max_reference_calls=2),
        ),
        tools=[Echo()],
        rails=[SteerAfterSecondTool()],
        enable_task_loop=True,
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "final"
    assert reference.invoke.await_count == 2
    third = requests[2]
    assert "new direction" in str(third)
    assert "late" not in str(third)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_loop_new_inner_iteration_number_does_not_reuse_old_proposal() -> None:
    class FollowUpOnce(DeepAgentRail):
        priority = 5

        async def after_task_iteration(self, ctx):
            if ctx.inputs.iteration == 1:
                await ctx.agent.follow_up("continue", session=ctx.session)

    primary_model = model()
    requests = []
    outputs = [AssistantMessageChunk(content="first"), AssistantMessageChunk(content="second")]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        yield outputs.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="only-first-inner"))
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
        rails=[FollowUpOnce()],
        enable_task_loop=True,
    )
    await deep.invoke("task", session=Session(card=deep.card))
    assert len(requests) == 2
    reference.invoke.assert_awaited_once()
    assert "only-first-inner" in str(requests[0])
    assert "only-first-inner" not in str(requests[1])


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stream_verifier_uses_private_proposal() -> None:
    primary_model = model()
    requests = []

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        yield AssistantMessageChunk(content="done")

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="private stream proposal"))
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=lambda *_: 0.5,
        ),
    )
    chunks = [
        chunk
        async for chunk in deep.stream(
            "task",
            session=Session(session_id="moa-stream", card=deep.card),
        )
    ]
    assert chunks
    assert "private stream proposal" in str(requests[0])
    assert "private stream proposal" not in str(
        deep.react_agent.context_engine.get_context(session_id="moa-stream").get_messages()
    )


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_stream_does_not_carry_proposal_to_next_invoke() -> None:
    primary_model = primary(AssistantMessage(content="fresh answer"))
    streamed_requests = []

    async def broken_stream(messages, **kwargs):
        del kwargs
        streamed_requests.append(messages)
        yield AssistantMessageChunk(content="partial")
        raise RuntimeError("stream broken")

    primary_model.stream = broken_stream
    reference = model()
    reference.invoke = AsyncMock(
        side_effect=[AssistantMessage(content="old proposal"), AssistantMessage(content="fresh proposal")]
    )
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
    )
    chunks = [chunk async for chunk in deep.stream("first", session=Session(session_id="moa-broken", card=deep.card))]
    assert chunks and streamed_requests
    result = await deep.invoke("second", session=Session(session_id="moa-fresh-after-stream", card=deep.card))
    assert result["output"] == "fresh answer"
    assert "old proposal" in str(streamed_requests[0])
    final = str(primary_model.invoke.await_args.kwargs["messages"])
    assert "fresh proposal" in final and "old proposal" not in final


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cancelled_stream_does_not_carry_proposal_to_next_invoke() -> None:
    primary_model = primary(AssistantMessage(content="fresh answer"))
    started = asyncio.Event()

    async def waiting_stream(messages, **kwargs):
        del messages, kwargs
        started.set()
        await asyncio.sleep(3600)
        yield AssistantMessageChunk(content="never")

    primary_model.stream = waiting_stream
    reference = model()
    reference.invoke = AsyncMock(
        side_effect=[AssistantMessage(content="old proposal"), AssistantMessage(content="fresh proposal")]
    )
    deep = agent(
        primary_model,
        MoAConfig(mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5),
    )

    async def consume():
        async for _ in deep.stream("first", session=Session(session_id="moa-stream-cancel", card=deep.card)):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await deep.invoke("second", session=Session(session_id="moa-stream-fresh", card=deep.card))
    assert result["output"] == "fresh answer"
    final = str(primary_model.invoke.await_args.kwargs["messages"])
    assert "fresh proposal" in final and "old proposal" not in final


@pytest.mark.asyncio
@pytest.mark.level1
async def test_moa_state_does_not_cross_invocations_or_hot_reload() -> None:
    primary_model = primary(AssistantMessage(content="one"), AssistantMessage(content="two"))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="proposal"))
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=lambda *_: 0.5,
        ),
    )
    await deep.invoke("first", session=Session(session_id="moa-a", card=deep.card))
    config = replace(deep.deep_config, system_prompt="changed")
    deep.configure(config)
    await deep.invoke("second", session=Session(session_id="moa-b", card=deep.card))
    assert reference.invoke.await_count == 2
    assert sum(type(rail).__name__ == "MoARail" for rail in deep.configured_rails()) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_loop_delivers_on_demand_proposal_to_next_react_turn() -> None:
    primary_model = primary(
        AssistantMessage(content="act", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]),
        consult_call(),
        AssistantMessage(content="done"),
    )
    streamed = [
        AssistantMessageChunk(
            content="act", tool_calls=[ToolCall(id="a", type="function", name="echo", arguments="{}")]
        ),
        AssistantMessageChunk(
            content="", tool_calls=[ToolCall(id="b", type="function", name=CONSULT_TOOL, arguments="{}")]
        ),
        AssistantMessageChunk(content="done"),
    ]
    requests = []

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        yield streamed.pop(0)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="task-loop proposal"))
    deep = agent(primary_model, MoAConfig(reference_models=(reference,)), tools=[Echo()], enable_task_loop=True)
    result = await deep.invoke("task", session=Session(session_id="moa-loop", card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_awaited_once()
    assert "task-loop proposal" in str(requests[-1])


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_verifier_score_preserves_last_valid_baseline() -> None:
    primary_model = primary(
        *[
            AssistantMessage(
                content=f"step {i}",
                tool_calls=[
                    ToolCall(id=str(i), type="function", name="echo", arguments="{}"),
                ],
            )
            for i in range(3)
        ],
        AssistantMessage(content="done"),
    )
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="proposal"))
    scorer = AsyncMock(side_effect=[0.8, RuntimeError("bad score"), 0.8])
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=scorer,
            verifier=VerifierConfig(interval=1, n_evaluations=1, max_reference_calls=2),
        ),
        tools=[Echo()],
    )
    await deep.invoke("task", session=Session(card=deep.card))
    assert scorer.await_count == 3
    assert reference.invoke.await_count == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_two_simultaneous_sessions_keep_proposal_separate() -> None:
    primary_model = model()
    requests = []

    async def answer(messages, **kwargs):
        del kwargs
        requests.append(messages)
        await asyncio.sleep(0)
        return AssistantMessage(content="done")

    primary_model.invoke = answer
    reference = model()

    async def advise(messages, **kwargs):
        del kwargs
        text = str(messages[-1].content)
        await asyncio.sleep(0)
        return AssistantMessage(content="alpha proposal" if "alpha task" in text else "beta proposal")

    reference.invoke = advise
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=lambda *_: 0.5,
        ),
    )
    await asyncio.gather(
        deep.invoke("alpha task", session=Session(session_id="moa-alpha", card=deep.card)),
        deep.invoke("beta task", session=Session(session_id="moa-beta", card=deep.card)),
    )
    assert any("alpha proposal" in str(request) and "beta proposal" not in str(request) for request in requests)
    assert any("beta proposal" in str(request) and "alpha proposal" not in str(request) for request in requests)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reference_cancellation_does_not_leak_into_next_invoke() -> None:
    primary_model = primary(AssistantMessage(content="done"))
    reference = model()
    started = asyncio.Event()

    async def pending(**kwargs):
        del kwargs
        started.set()
        await asyncio.sleep(3600)

    reference.invoke = pending
    deep = agent(
        primary_model,
        MoAConfig(
            mode="verifier_routed",
            reference_models=(reference,),
            scorer=lambda *_: 0.5,
        ),
    )
    task = asyncio.create_task(deep.invoke("cancel", session=Session(session_id="moa-cancel", card=deep.card)))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="fresh proposal"))
    result = await deep.invoke("fresh", session=Session(session_id="moa-fresh", card=deep.card))
    assert result["output"] == "done"
    assert "fresh proposal" in str(primary_model.invoke.await_args.kwargs["messages"])


@pytest.mark.asyncio
@pytest.mark.level1
async def test_verifier_factory_uses_routing_repeats_and_rejects_all_invalid() -> None:
    kwargs_seen = {}

    class Tracker:
        def update(self, step):
            del step
            return 0.5

        def result(self):
            return type("Result", (), {"per_rep_scores": [[None], [None]]})()

    def factory(problem, **kwargs):
        del problem
        kwargs_seen.update(kwargs)
        return Tracker()

    scorer = LlmVerifierProgressScorer(tracker_factory=factory)
    invoke_scorer = scorer.for_invoke("task", n_evaluations=2)
    with pytest.raises(BaseError, match="no valid"):
        await invoke_scorer("task", ("step",), 2)
    assert kwargs_seen["n_evaluations"] == 2


@pytest.mark.level1
def test_builtin_verifier_requires_explicit_model_without_external_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "llm_verifier", None)
    with pytest.raises(BaseError, match="jiuwen Model"):
        LlmVerifierProgressScorer()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_verifier_factory_receives_redacted_problem() -> None:
    problems = []

    class Scorer:
        def for_invoke(self, problem):
            problems.append(problem)
            return self

        def __call__(self, problem, steps, n_evaluations):
            problems.append(problem)
            return 0.5

    config = MoAConfig(reference_models=(model(),), scorer=Scorer())
    state = MoAState(problem="Investigate token=secret123")
    assert await score_progress(config, state) == 0.5
    assert problems == ["Investigate [redacted]"] * 2
