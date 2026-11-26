# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FEAT-127: every streaming/invoke end carries a structured termination reason.

Covers the design's acceptance points at unit level: the reason enum and the
request-scoped carrier, the forceFinish declaration path, the AFTER_INVOKE
fallback classification, each terminating branch of ReActAgent.invoke, the
interrupt dict/frame carriers, and the stream terminal-frame guarantee.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest, ToolCallInterruptRequest
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    AgentTerminationReason,
    attach_termination_reason,
    claim_terminal_frame,
    current_termination_reason,
    force_finish_reason,
    record_termination_reason,
    reset_termination_reason,
    restore_termination_scope,
    terminal_frame_reason,
    termination_reason_of,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.agent.react_agent._rig import build_react_agent_rig
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

# ---------------------------------------------------------------- helpers


def _answer(text: str = "final answer") -> AssistantMessage:
    return AssistantMessage(
        content=text,
        finish_reason="stop",
        usage_metadata=UsageMetadata(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _empty() -> AssistantMessage:
    return AssistantMessage(
        content="",
        finish_reason="stop",
        usage_metadata=UsageMetadata(input_tokens=10, output_tokens=0, total_tokens=10),
    )


def _agent(*responses: AssistantMessage, max_iterations: int = 5, on_max: str = "error"):
    return build_react_agent_rig(
        *responses,
        card_name="termination-reason-test",
        max_iterations=max_iterations,
        on_max_iterations=on_max,
    )


def _ctx() -> AgentCallbackContext:
    agent = MagicMock()
    agent.agent_callback_manager.execute = AsyncMock()
    return AgentCallbackContext(agent=agent)


# ---------------------------------------------------------------- carrier


def test_record_first_wins_and_reset() -> None:
    reset_termination_reason()
    assert current_termination_reason() is None
    assert record_termination_reason(AgentTerminationReason.TEXT_TERMINATION) is True
    assert record_termination_reason(AgentTerminationReason.ERROR) is False
    assert current_termination_reason() is AgentTerminationReason.TEXT_TERMINATION
    reset_termination_reason()
    assert current_termination_reason() is None


def test_terminal_frame_claim_is_bound_to_the_result_object() -> None:
    first = {"output": "one", "result_type": "answer"}
    second = {"output": "two", "result_type": "answer"}
    assert claim_terminal_frame(first) is True
    assert claim_terminal_frame(first) is False  # the same run result, again
    assert claim_terminal_frame(second) is True  # a later run in the same task


def test_terminal_frame_reason_maps_cancelled_and_defaults_to_error() -> None:
    assert terminal_frame_reason({"result_type": "cancelled"}) is AgentTerminationReason.CANCELLED
    assert terminal_frame_reason({"result_type": "answer"}) is AgentTerminationReason.TEXT_TERMINATION
    assert terminal_frame_reason({"result_type": "interrupt"}) is AgentTerminationReason.TOOL_INTERRUPT
    assert terminal_frame_reason({}) is AgentTerminationReason.ERROR


def test_force_finish_reason_disambiguation() -> None:
    assert force_finish_reason({"output": "x"}) is AgentTerminationReason.FORCE_FINISH
    assert force_finish_reason({"result_type": "cancelled"}) is AgentTerminationReason.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,expected",
    [
        (asyncio.CancelledError, AgentTerminationReason.CANCELLED),
        (GeneratorExit, AgentTerminationReason.CANCELLED),
        (RuntimeError, AgentTerminationReason.ERROR),
    ],
)
async def test_lifecycle_fallback_classifies_in_flight_exception(exc, expected) -> None:
    ctx = _ctx()
    with pytest.raises(BaseException):
        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            raise exc()
    # the run scope is handed back on exit; the run's own value stays on the context
    assert ctx.termination_reason is expected


@pytest.mark.asyncio
async def test_lifecycle_fallback_does_not_override_recorded_reason() -> None:
    ctx = _ctx()
    with pytest.raises(RuntimeError):
        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            ctx.finish(AgentTerminationReason.MODEL_ERROR)
            raise RuntimeError("boom")
    assert ctx.termination_reason is AgentTerminationReason.MODEL_ERROR


@pytest.mark.asyncio
async def test_before_event_failure_closes_the_scope() -> None:
    """N-1: a guardrail aborting BEFORE_INVOKE must not leave the run scope open."""
    reset_termination_reason()
    record_termination_reason(AgentTerminationReason.TEXT_TERMINATION)
    ctx = _ctx()
    ctx.agent.agent_callback_manager.execute = AsyncMock(side_effect=RuntimeError("guardrail"))
    with pytest.raises(RuntimeError) as excinfo:
        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            pass
    assert current_termination_reason() is AgentTerminationReason.TEXT_TERMINATION
    assert ctx._run_scope_depth == 0
    assert termination_reason_of(excinfo.value) is AgentTerminationReason.ERROR


@pytest.mark.asyncio
async def test_lifecycle_inherits_a_reason_published_on_the_exception() -> None:
    """A nested run's published reason must survive the outer scope's fallback."""
    ctx = _ctx()
    exc = RuntimeError("boom")
    attach_termination_reason(exc, AgentTerminationReason.MODEL_ERROR)
    with pytest.raises(RuntimeError):
        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            raise exc
    assert ctx.termination_reason is AgentTerminationReason.MODEL_ERROR


def test_scope_restores_the_callers_reason() -> None:
    """A nested invoke shares the caller's task, so its record must be scoped."""
    reset_termination_reason()
    record_termination_reason(AgentTerminationReason.FORCE_FINISH)
    scope = reset_termination_reason()
    assert current_termination_reason() is None
    record_termination_reason(AgentTerminationReason.MODEL_ERROR)
    restore_termination_scope(scope)
    assert current_termination_reason() is AgentTerminationReason.FORCE_FINISH


@pytest.mark.asyncio
async def test_normal_run_inside_callers_except_block_is_not_misclassified() -> None:
    """An invoke awaited from a caller's ``except`` block must not inherit it.

    The fallback classification reads the exception that crossed the invoke
    boundary; ``sys.exc_info()`` would report the caller's error instead.
    """
    reset_termination_reason()
    ctx = _ctx()
    try:
        raise ValueError("caller's own error")
    except ValueError as caller_error:
        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            pass
        assert ctx.termination_reason is None
        assert current_termination_reason() is None
        assert termination_reason_of(caller_error) is None


def test_raising_invoke_publishes_its_reason_on_the_exception() -> None:
    exc = RuntimeError("boom")
    assert termination_reason_of(exc) is None
    attach_termination_reason(exc, AgentTerminationReason.MODEL_ERROR)
    assert termination_reason_of(exc) is AgentTerminationReason.MODEL_ERROR


def test_consume_force_finish_records_reason() -> None:
    reset_termination_reason()
    ctx = _ctx()
    ctx.request_force_finish({"output": "intercepted"})
    ctx.consume_force_finish()
    assert current_termination_reason() is AgentTerminationReason.FORCE_FINISH

    reset_termination_reason()
    ctx = _ctx()
    ctx.request_force_finish({"output": "stop", "result_type": "cancelled"})
    ctx.consume_force_finish()
    assert current_termination_reason() is AgentTerminationReason.CANCELLED


# ---------------------------------------------------------------- invoke branches


@pytest.mark.asyncio
async def test_invoke_answer_carries_text_termination() -> None:
    agent, session, model, _ = _agent(_answer())
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["result_type"] == "answer"
    assert result["termination_reason"] == "text_termination"


@pytest.mark.asyncio
async def test_invoke_empty_response_carries_model_error() -> None:
    agent, session, model, _ = _agent(_empty(), _empty(), max_iterations=2)
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["result_type"] == "error"
    assert result["termination_reason"] == "model_error"


@pytest.mark.asyncio
async def test_invoke_max_iterations_keeps_existing_fields() -> None:
    agent, session, model, _ = _agent(max_iterations=1)
    model.invoke = AsyncMock(
        side_effect=[
            create_tool_call_response("lookup", "{}", tool_call_id="c1"),
            create_tool_call_response("lookup", "{}", tool_call_id="c2"),
        ]
    )
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.ability_manager.execute = AsyncMock(return_value=[])
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["result_type"] == "error"
    assert result["termination_reason"] == "max_iterations"
    for key in (
        "iterations_used",
        "truncated_by_max_iterations",
        "max_iterations",
        "finalization_attempted",
        "finalization_succeeded",
    ):
        assert key in result


@pytest.mark.asyncio
async def test_invoke_model_error_records_model_error_and_raises() -> None:
    agent, session, model, _ = _agent()
    # Production model errors are built with build_error, which yields a
    # FrameworkError — not the ModelError subclass. Pin the classifier on that shape.
    model.invoke = AsyncMock(side_effect=build_error(StatusCode.MODEL_CALL_FAILED, error_msg="down"))
    with patch.object(agent, "_get_llm", return_value=model):
        with pytest.raises(BaseError) as excinfo:
            await agent.invoke({"query": "hi"}, session=session)
    # the run's scope is gone; the reason travels on the exception
    assert termination_reason_of(excinfo.value) is AgentTerminationReason.MODEL_ERROR


@pytest.mark.asyncio
async def test_invoke_generic_error_records_error_and_raises() -> None:
    agent, session, model, _ = _agent()
    model.invoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(agent, "_get_llm", return_value=model):
        with pytest.raises(RuntimeError) as excinfo:
            await agent.invoke({"query": "hi"}, session=session)
    assert termination_reason_of(excinfo.value) is AgentTerminationReason.ERROR


@pytest.mark.asyncio
async def test_invoke_non_model_framework_error_is_a_plain_error() -> None:
    """The counterpart of the model-error case: a non-model framework error is `error`."""
    agent, session, model, _ = _agent()
    model.invoke = AsyncMock(side_effect=build_error(StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="ctx"))
    with patch.object(agent, "_get_llm", return_value=model):
        with pytest.raises(BaseError) as excinfo:
            await agent.invoke({"query": "hi"}, session=session)
    assert termination_reason_of(excinfo.value) is AgentTerminationReason.ERROR


@pytest.mark.asyncio
async def test_invoke_force_finish_answer_and_cancelled_declarations() -> None:
    for payload, expected in (
        ({"output": "intercepted", "result_type": "answer"}, "force_finish"),
        ({"output": "stop", "result_type": "cancelled"}, "cancelled"),
    ):

        class _Rail(AgentRail):
            async def before_model_call(self, ctx: AgentCallbackContext) -> None:
                ctx.request_force_finish(payload)

        agent, session, model, _ = _agent(_answer())
        await agent.register_rail(_Rail())
        with patch.object(agent, "_get_llm", return_value=model):
            result = await agent.invoke({"query": "hi"}, session=session)
        assert result["output"] == payload["output"]
        assert result["termination_reason"] == expected


@pytest.mark.asyncio
async def test_non_model_framework_error_records_error_and_writes_no_frame() -> None:
    """J-05: a context-engine style BaseError is a plain error, not a model error."""
    agent, session, model, _ = _agent(_answer(), _answer())
    broken = MagicMock()
    broken.get_messages = MagicMock(return_value=[])
    broken.get_context_window = AsyncMock(side_effect=build_error(StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="ctx"))
    broken.add_messages = AsyncMock()
    agent.context_engine.create_context = AsyncMock(return_value=broken)
    written: list = []

    async def write_stream(schema):
        written.append(schema)

    session.write_stream = AsyncMock(side_effect=write_stream)

    with patch.object(agent, "_get_llm", return_value=model):
        with pytest.raises(BaseError) as excinfo:
            await agent.invoke({"query": "hi"}, session=session)

    assert termination_reason_of(excinfo.value) is AgentTerminationReason.ERROR
    assert not [schema for schema in written if getattr(schema, "type", None) == "answer"]


@pytest.mark.asyncio
async def test_deep_agent_run_records_no_error_and_leaks_no_reason() -> None:
    """S-1: the shared lifecycle must not label a normal DeepAgent run as error."""
    from openjiuwen.core.runner import Runner
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.schema.config import DeepAgentConfig

    reset_termination_reason()
    model = MockLLMModel()
    model.set_responses([create_text_response("deep answer")])
    agent = DeepAgent(AgentCard(id="tr-deep")).configure(DeepAgentConfig(model=model, enable_task_loop=False))
    observed: list[str] = []

    class _Observer(AgentRail):
        async def after_invoke(self, ctx: AgentCallbackContext) -> None:
            reason = ctx.termination_reason
            observed.append(str(reason) if reason is not None else "none")

    if hasattr(agent, "register_rail"):
        await agent.register_rail(_Observer())
    frame_reasons: list[str] = []

    await Runner.start()
    try:
        with patch.object(agent.react_agent, "_get_llm", return_value=model):
            async for chunk in agent.stream({"query": "hi", "conversation_id": "tr-deep"}):
                payload = getattr(chunk, "payload", None) or (chunk.get("payload") if isinstance(chunk, dict) else None)
                if getattr(chunk, "type", None) == "answer" and isinstance(payload, dict):
                    frame_reasons.append(payload.get("termination_reason"))
    finally:
        await Runner.stop()

    assert frame_reasons == ["text_termination"]
    assert current_termination_reason() is None  # nothing leaked into the caller's task
    assert "error" not in observed


@pytest.mark.asyncio
async def test_force_finish_during_finalization_wins_over_max_iterations() -> None:
    """M-2: the exhaustion result is not delivered; the rail's finish is."""
    agent, session, model, _ = _agent(max_iterations=1, on_max="final_answer")
    model.invoke = AsyncMock(
        side_effect=[
            create_tool_call_response("lookup", "{}", tool_call_id="c1"),
        ]
    )
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.ability_manager.execute = AsyncMock(return_value=[])

    class _FinalizingRail(AgentRail):
        async def before_model_call(self, ctx: AgentCallbackContext) -> None:
            if ctx.extra.get("_finalizing_max_iterations"):
                ctx.request_force_finish({"output": "intercepted", "result_type": "answer"})

    await agent.register_rail(_FinalizingRail())
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["output"] == "intercepted"
    assert result["termination_reason"] == "force_finish"


@pytest.mark.asyncio
async def test_finalization_answer_dict_still_records_max_iterations() -> None:
    """J-09: an answer produced by exhaustion finalization keeps max_iterations."""
    agent, session, model, _ = _agent(max_iterations=1, on_max="final_answer")
    model.invoke = AsyncMock(
        side_effect=[
            create_tool_call_response("lookup", "{}", tool_call_id="c1"),
        ]
    )
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.ability_manager.execute = AsyncMock(return_value=[])

    async def dict_answer(*args, **kwargs):  # noqa: ANN002, ANN003
        if dict_answer.calls == 0:
            dict_answer.calls += 1
            return create_tool_call_response("lookup", "{}", tool_call_id="c1")
        return {"output": "final from rail", "result_type": "answer"}

    dict_answer.calls = 0
    model.invoke = AsyncMock(side_effect=dict_answer)
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["output"] == "final from rail"
    assert result["termination_reason"] == "max_iterations"


@pytest.mark.asyncio
async def test_cancel_during_finalization_records_cancelled() -> None:
    """M-2: a cancellation inside finalization is cancelled, not max_iterations."""
    agent, session, model, _ = _agent(max_iterations=1, on_max="final_answer")

    second_call_started = asyncio.Event()

    async def slow_second_call(messages, **kwargs):  # noqa: ANN001, ANN003
        if slow_second_call.calls == 0:
            slow_second_call.calls += 1
            return create_tool_call_response("lookup", "{}", tool_call_id="c1")
        second_call_started.set()
        await asyncio.sleep(30)
        return create_tool_call_response("lookup", "{}", tool_call_id="c2")

    slow_second_call.calls = 0
    model.invoke = AsyncMock(side_effect=slow_second_call)
    agent.ability_manager.list_tool_info = AsyncMock(return_value=[])
    agent.ability_manager.execute = AsyncMock(return_value=[])

    with patch.object(agent, "_get_llm", return_value=model):
        task = asyncio.create_task(agent.invoke({"query": "hi"}, session=session))
        await asyncio.wait_for(second_call_started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await task
    assert termination_reason_of(excinfo.value) is AgentTerminationReason.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_rail_declares_cancelled() -> None:
    """The rail that really emits the cancelled declaration is covered end to end."""
    from openjiuwen.rsi.harness_rsi.auto_harness.rails.cancellation_rail import CancellationRail

    reset_termination_reason()
    rail = CancellationRail()
    rail.bind(SimpleNamespace(should_cancel=True))
    ctx = _ctx()
    await rail.before_tool_call(ctx)
    request = ctx.consume_force_finish()
    assert request is not None
    assert request.result["result_type"] == "cancelled"
    assert current_termination_reason() is AgentTerminationReason.CANCELLED


# ---------------------------------------------------------------- interrupt carriers


def test_hitl_interrupt_dict_and_frame_carry_reason() -> None:
    handler = ToolInterruptHandler(agent=MagicMock())
    result = handler.build_interrupt_result(None)
    assert result["termination_reason"] == "tool_interrupt"

    request = InterruptRequest()
    tool_call = ToolCall(id="t1", type="function", name="read", arguments="{}")
    built = ToolCallInterruptRequest.from_tool_call(request, tool_call)
    assert built.metadata["termination_reason"] == "tool_interrupt"


def test_from_tool_call_does_not_overwrite_existing_metadata() -> None:
    request = InterruptRequest(metadata={"interrupt_kind": "approval", "termination_reason": "custom"})
    tool_call = ToolCall(id="t1", type="function", name="x", arguments="{}")
    built = ToolCallInterruptRequest.from_tool_call(request, tool_call)
    assert built.metadata["interrupt_kind"] == "approval"
    # a reason the caller already declared is kept, not overwritten
    assert built.metadata["termination_reason"] == "custom"


def test_workflow_interrupt_dict_carries_reason() -> None:
    agent = ReActAgent(card=AgentCard(name="workflow-interrupt-test"))
    state = SimpleNamespace(
        pending_workflow_id="wf-1",
        pending_component_id="cmp-1",
        interrupted_workflows={"wf-1": SimpleNamespace(workflow_execution_state=object())},
    )
    result = agent._build_interrupt_result(state)
    assert result["termination_reason"] == "tool_interrupt"


@pytest.mark.asyncio
async def test_non_assistant_model_result_falls_back_to_model_error() -> None:
    """Java parity: a dict the model leg returns maps by result_type, fallback model_error."""
    agent, session, model, _ = _agent()
    model.invoke = AsyncMock(return_value={"output": "rail supplied"})
    with patch.object(agent, "_get_llm", return_value=model):
        result = await agent.invoke({"query": "hi"}, session=session)
    assert result["output"] == "rail supplied"
    assert result["termination_reason"] == "model_error"


# ---------------------------------------------------------------- nesting and concurrency


@pytest.mark.asyncio
async def test_nested_invoke_does_not_leak_reason_into_the_caller() -> None:
    reset_termination_reason()
    sub, sub_session, sub_model, _ = _agent()
    sub_model.invoke = AsyncMock(side_effect=RuntimeError("sub failed"))

    seen: list[str] = []

    class _Nested(AgentRail):
        async def before_model_call(self, ctx: AgentCallbackContext) -> None:
            with pytest.raises(RuntimeError):
                await sub.invoke({"query": "sub"}, session=sub_session)
            # the sub run's MODEL_ERROR/ERROR record must not survive its return
            seen.append(str(current_termination_reason()))

    parent, parent_session, parent_model, _ = _agent(_answer())
    await parent.register_rail(_Nested())
    with patch.object(sub, "_get_llm", return_value=sub_model):
        with patch.object(parent, "_get_llm", return_value=parent_model):
            result = await parent.invoke({"query": "hi"}, session=parent_session)
    assert seen == ["None"]
    assert result["termination_reason"] == "text_termination"


@pytest.mark.asyncio
async def test_concurrent_invokes_keep_their_own_reason() -> None:
    ok, ok_session, ok_model, _ = _agent(_answer())
    bad, bad_session, bad_model, _ = _agent()
    bad_model.invoke = AsyncMock(side_effect=RuntimeError("boom"))

    async def run_ok():
        with patch.object(ok, "_get_llm", return_value=ok_model):
            return await ok.invoke({"query": "hi"}, session=ok_session)

    async def run_bad():
        with patch.object(bad, "_get_llm", return_value=bad_model):
            return await bad.invoke({"query": "hi"}, session=bad_session)

    good, failure = await asyncio.gather(run_ok(), run_bad(), return_exceptions=True)
    assert good["termination_reason"] == "text_termination"
    assert isinstance(failure, RuntimeError)


# ---------------------------------------------------------------- stream frames


async def _collect(agent, query="hi"):
    return [chunk async for chunk in agent.stream({"query": query, "conversation_id": agent.card.id})]


@pytest.mark.asyncio
async def test_stream_writes_single_terminal_frame_with_reason() -> None:
    from openjiuwen.core.runner import Runner

    agent = ReActAgent(card=AgentCard(id="tr-stream-answer"))
    agent.configure(ReActAgentConfig(model_name="fake"))
    model = MockLLMModel()
    model.set_responses([create_text_response("final answer")])
    await Runner.start()
    try:
        with patch.object(agent, "_get_llm", return_value=model):
            chunks = await _collect(agent)
    finally:
        await Runner.stop()
    finals = [c.payload for c in chunks if c.type == "answer"]
    assert len(finals) == 1
    assert finals[0]["termination_reason"] == "text_termination"


@pytest.mark.asyncio
async def test_stream_model_error_writes_frame_then_raises() -> None:
    from openjiuwen.core.runner import Runner

    class _Failing(MockLLMModel):
        async def invoke(self, messages, **kwargs):  # noqa: ANN001, ANN003
            raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="down")

        async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN003
            raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="down")
            yield  # pragma: no cover

    agent = ReActAgent(card=AgentCard(id="tr-stream-error"))
    agent.configure(ReActAgentConfig(model_name="fake"))
    await Runner.start()
    frames = []
    try:
        with patch.object(agent, "_get_llm", return_value=_Failing()):
            with pytest.raises(BaseError):
                async for chunk in agent.stream({"query": "hi", "conversation_id": agent.card.id}):
                    frames.append(chunk)
    finally:
        await Runner.stop()
    finals = [c.payload for c in frames if c.type == "answer"]
    assert len(finals) == 1
    assert finals[0]["termination_reason"] == "model_error"
    assert finals[0]["result_type"] == "error"


@pytest.mark.asyncio
async def test_stream_cancel_records_cancelled_and_keeps_cancelled_error() -> None:
    from openjiuwen.core.runner import Runner

    slow_started = asyncio.Event()

    class _Slow(MockLLMModel):
        async def invoke(self, messages, **kwargs):  # noqa: ANN001, ANN003
            slow_started.set()
            await asyncio.sleep(30)
            return create_text_response("late")

        async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN003
            slow_started.set()
            await asyncio.sleep(30)
            yield AssistantMessageChunk(content="late")

    observed: list[str] = []

    class _Observer(AgentRail):
        async def after_invoke(self, ctx: AgentCallbackContext) -> None:
            reason = ctx.termination_reason
            observed.append(str(reason) if reason is not None else "none")

    agent = ReActAgent(card=AgentCard(id="tr-stream-cancel"))
    agent.configure(ReActAgentConfig(model_name="fake"))
    await agent.register_rail(_Observer())
    written: list[dict] = []
    real_writer = ReActAgent._write_invoke_result_to_stream

    async def _spy(self, result, session):  # noqa: ANN001
        written.append(dict(result))
        await real_writer(self, result, session)

    await Runner.start()
    try:
        with patch.object(agent, "_get_llm", return_value=_Slow()):
            with patch.object(ReActAgent, "_write_invoke_result_to_stream", _spy):
                task = asyncio.create_task(_collect(agent))
                await asyncio.wait_for(slow_started.wait(), timeout=10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    finally:
        await Runner.stop()
    assert observed and observed[-1] == "cancelled"
    # the cancelled run still leaves one frame-shaped result behind
    assert len(written) == 1
    assert written[0]["result_type"] == "cancelled"
    assert written[0]["termination_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_two_sequential_inline_stream_runs_each_write_a_frame() -> None:
    """A workflow session runs the producer inline, on the caller's task.

    The frame guard must not leak from one run into the next: each run has its
    own result object and must get its own terminal frame.
    """
    agent, session, _, _ = _agent()
    model = MockLLMModel()
    model.set_responses([create_text_response("first"), create_text_response("second")])
    written: list = []

    async def write_stream(schema):
        written.append(schema)

    session.write_stream = AsyncMock(side_effect=write_stream)
    # No pre_run/close_stream/commit => not an agent session => inline producer.
    session.pre_run = None
    session.close_stream = None
    session.commit = None

    with patch.object(agent, "_get_llm", return_value=model):
        for _ in range(2):
            async for _chunk in agent.stream("q", session=session):
                pass

    finals = [schema.payload for schema in written if getattr(schema, "type", None) == "answer"]
    assert len(finals) == 2
    assert [payload["termination_reason"] for payload in finals] == ["text_termination", "text_termination"]


@pytest.mark.asyncio
async def test_cancel_during_terminal_write_does_not_add_a_second_frame() -> None:
    """L-2: a cancellation inside the write must not produce a cancelled frame too."""
    agent, session, _, _ = _agent()
    model = MockLLMModel()
    model.set_responses([create_text_response("first")])
    gate = asyncio.Event()
    write_started = asyncio.Event()
    written: list = []

    async def write_stream(schema):
        written.append(schema)
        if getattr(schema, "type", None) == "answer":
            write_started.set()
            await gate.wait()

    session.write_stream = AsyncMock(side_effect=write_stream)
    session.pre_run = None
    session.close_stream = None
    session.commit = None

    async def consume():
        async for _chunk in agent.stream("q", session=session):
            pass

    with patch.object(agent, "_get_llm", return_value=model):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(write_started.wait(), timeout=5)  # write is in flight
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    finals = [schema.payload for schema in written if getattr(schema, "type", None) == "answer"]
    assert len(finals) == 1
    assert finals[0]["termination_reason"] == "text_termination"


def test_write_invoke_result_injects_reason_and_drops_duplicates() -> None:
    reset_termination_reason()
    agent = ReActAgent(card=AgentCard(name="writer-test"))
    session = MagicMock()
    written = []

    async def write_stream(schema):
        written.append(schema)

    session.write_stream = AsyncMock(side_effect=write_stream)
    record_termination_reason(AgentTerminationReason.TEXT_TERMINATION)
    result = {"output": "x", "result_type": "answer"}

    async def _both() -> None:
        await agent._write_invoke_result_to_stream(result, session)
        await agent._write_invoke_result_to_stream(result, session)

    try:
        asyncio.run(_both())
        assert len(written) == 1
        assert written[0].payload["termination_reason"] == "text_termination"
    finally:
        # This test records in the caller's (main-thread) context; clear it so
        # later tests that write a frame without running an invoke see the
        # framework fallback instead of this run's reason.
        reset_termination_reason()
