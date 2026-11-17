# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ReAct model-step integration with the ``turn.step`` event."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.tool_call import TOOL_CALL, ToolCallEventOutput
from openjiuwen.core.events.builtins.event_types import turn as turn_events
from openjiuwen.core.events.builtins.event_types.turn import (
    TURN_STEP,
    TurnStepInput,
    TurnStepOutput,
)
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, InvokeInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent


def _echo_tool() -> LocalFunction:
    card = ToolCard(
        id="echo",
        name="echo",
        description="echo desc",
        input_params={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )

    async def _echo(text: str) -> dict[str, str]:
        return {"tool": "echo", "text": text}

    return LocalFunction(card=card, func=_echo)


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


async def _built_bus_recording_turn_step_core_inputs(
    recorded: list[TurnStepInput],
) -> ExtensionEventBus:
    """Build a bus whose ``turn.step`` CORE handler records ``event.event_input`` before running."""
    real_core = turn_events.turn_step_core_handler

    async def recording_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnStepOutput:
        recorded.append(cast(TurnStepInput, event.event_input))
        return await real_core(event, ops, event_next)

    with patch.object(turn_events, "turn_step_core_handler", recording_core):
        bus = ExtensionEventBus()
        await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _rig(*, step_index: int | None = 0) -> tuple[ReActAgent, AgentCallbackContext, MagicMock]:
    agent = ReActAgent(card=AgentCard(id="step-agent"))
    agent.configure(ReActAgentConfig(model_name="fake", on_max_iterations="final_answer"))
    session = create_agent_session(session_id="step-session", card=agent.card)
    extra = {}
    if step_index is not None:
        extra["_turn_state"] = SimpleNamespace(turn_id="turn-1", step_index=step_index)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="question"),
        session=session,
        extra=extra,
    )
    context = MagicMock()
    context.get_messages.return_value = []
    context.add_messages = AsyncMock()
    ctx.context = context
    return agent, ctx, context


@pytest.mark.asyncio
@pytest.mark.parametrize(("step_index", "expected_index"), [(None, None), (4, 5)])
async def test_call_model_without_active_event_path_executes_directly(
    step_index: int | None,
    expected_index: int | None,
) -> None:
    agent, ctx, context = _rig(step_index=step_index)
    original = AssistantMessage(content="original")

    Runner.set_extension_bus(None)
    with patch.object(agent, "_execute_model_step", AsyncMock(return_value=original)) as execute:
        result = await agent._call_model(ctx, context, None)

    assert result is original
    execute.assert_awaited_once_with(ctx, context, None)
    if expected_index is None:
        assert "_turn_state" not in ctx.extra
    else:
        assert ctx.extra["_turn_state"].step_index == expected_index


@pytest.mark.asyncio
async def test_turn_step_handler_can_invoke_ops_tool_call_before_model_step() -> None:
    """E2E: a ``turn.step`` USER handler runs ``ops.tool.call`` then continues the step chain."""
    agent, ctx, context = _rig()
    session = ctx.session
    tool = _echo_tool()
    agent.ability_manager.add_ability(tool.card, tool)
    deep = MagicMock(spec=DeepAgent)
    deep.react_agent = agent
    deep.find_rails_by_type.return_value = []
    get_agent_runtime().bind(session.get_session_id(), deep)

    prefetch_outputs: list[ToolCallEventOutput] = []

    async def prefetch_via_ops(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnStepOutput:
        assert ops is not None

        @with_session(session)
        async def prefetch() -> ToolCallEventOutput:
            return await ops.tool.call("echo", {"text": "from-turn-step"})

        prefetch_outputs.append(await prefetch())
        output = await event_next(event)
        return dataclasses.replace(output, answer=f"tool+{output.answer}")

    bus = await _built_bus()
    bus.register_event_handler(TURN_STEP, prefetch_via_ops, {}, HandlerLevel.USER)
    model_message = AssistantMessage(content="model-answer", metadata={"private": True})

    await Runner.start()
    Runner.set_extension_bus(bus)
    try:
        with (
            patch.object(bus, "trigger", wraps=bus.trigger) as trigger,
            patch.object(agent, "_execute_model_step", AsyncMock(return_value=model_message)) as execute,
        ):
            result = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)
        agent.ability_manager.teardown_tools()
        await Runner.stop()

    assert len(prefetch_outputs) == 1
    prefetch = prefetch_outputs[0]
    assert prefetch.result == {"tool": "echo", "text": "from-turn-step"}
    assert isinstance(prefetch.text, str) and "from-turn-step" in prefetch.text

    assert result.content == "tool+model-answer"
    assert result.metadata == {"private": True}
    assert ctx.extra["_turn_state"].step_index == 1
    execute.assert_awaited_once_with(ctx, context, None)

    event_types = [call.args[0].event_type for call in trigger.await_args_list]
    assert event_types.count(TURN_STEP) == 1
    assert event_types.count(TOOL_CALL) == 1
    turn_index = event_types.index(TURN_STEP)
    tool_index = event_types.index(TOOL_CALL)
    assert tool_index > turn_index


@pytest.mark.asyncio
async def test_turn_step_core_receives_user_forwarded_input_after_ops_prefetch() -> None:
    """``event_next(modified)`` reaches CORE; AgentLoop still rejects non-host ``TurnStepInput``."""
    agent, ctx, context = _rig()
    session = ctx.session
    tool = _echo_tool()
    agent.ability_manager.add_ability(tool.card, tool)
    deep = MagicMock(spec=DeepAgent)
    deep.react_agent = agent
    deep.find_rails_by_type.return_value = []
    get_agent_runtime().bind(session.get_session_id(), deep)

    core_inputs: list[TurnStepInput] = []
    forwarded_model = "seen-by-core-after-prefetch"

    async def prefetch_then_forward_modified_input(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnStepOutput:
        assert ops is not None

        @with_session(session)
        async def prefetch() -> ToolCallEventOutput:
            return await ops.tool.call("echo", {"text": "prefetch-before-forward"})

        await prefetch()
        modified = dataclasses.replace(event.event_input, model=forwarded_model)
        return await event_next(Event(TURN_STEP, modified))

    bus = await _built_bus_recording_turn_step_core_inputs(core_inputs)
    bus.register_event_handler(TURN_STEP, prefetch_then_forward_modified_input, {}, HandlerLevel.USER)
    execute = AsyncMock(return_value=AssistantMessage(content="must not run"))

    await Runner.start()
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", execute):
            with pytest.raises(BaseError, match="turn.step input cannot be modified"):
                await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)
        agent.ability_manager.teardown_tools()
        await Runner.stop()

    assert len(core_inputs) == 1
    assert core_inputs[0] == TurnStepInput(
        turn_id="turn-1",
        index=0,
        model=forwarded_model,
        agent_id="step-agent",
    )
    execute.assert_not_awaited()
    # ``_next_turn_step`` bumps the counter before ``trigger``; a failed step does not roll it back.
    assert ctx.extra["_turn_state"].step_index == 1


@pytest.mark.asyncio
async def test_empty_step_chain_preserves_message_identity_and_increments_index_once() -> None:
    agent, ctx, context = _rig(step_index=2)
    original = AssistantMessage(content="original", metadata={"private": True})
    bus = await _built_bus()

    Runner.set_extension_bus(bus)
    try:
        with (
            patch.object(bus, "trigger", wraps=bus.trigger) as trigger,
            patch.object(agent, "_execute_model_step", AsyncMock(return_value=original)) as execute,
        ):
            result = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    assert result is original
    assert ctx.inputs.response is original
    assert ctx.extra["_turn_state"].step_index == 3
    execute.assert_awaited_once_with(ctx, context, None)
    step_calls = [call for call in trigger.await_args_list if call.args[0].event_type == TURN_STEP]
    assert len(step_calls) == 1
    assert step_calls[0].args[0].event_input == TurnStepInput(
        turn_id="turn-1",
        index=2,
        model="fake",
        agent_id="step-agent",
    )


@pytest.mark.asyncio
async def test_step_handler_can_short_circuit_or_rewrite_final_answer() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()
    mode = "short_circuit"

    async def handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnStepOutput:
        del ops
        if mode == "short_circuit":
            step_input = event.event_input
            return TurnStepOutput(
                turn_id=step_input.turn_id,
                index=step_input.index,
                answer="cached",
                tool_uses=(),
                stop_reason="stop",
            )
        output = await event_next(event)
        return dataclasses.replace(output, answer="rewritten")

    bus.register_event_handler(TURN_STEP, handler, {}, HandlerLevel.USER)
    execute = AsyncMock(return_value=AssistantMessage(content="provider", metadata={"private": True}))
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", execute):
            short_circuit = await agent._call_model(ctx, context, None)
            mode = "rewrite"
            rewritten = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    assert short_circuit.content == "cached"
    assert rewritten.content == "rewritten"
    assert rewritten.metadata == {"private": True}
    assert execute.await_count == 1
    assert ctx.extra["_turn_state"].step_index == 2


@pytest.mark.asyncio
async def test_step_rejects_forwarded_input_mutation_before_model_execution() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()

    async def mutate_input(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        changed = dataclasses.replace(event.event_input, model="forged")
        return await event_next(Event(TURN_STEP, changed))

    bus.register_event_handler(TURN_STEP, mutate_input, {}, HandlerLevel.USER)
    execute = AsyncMock(return_value=AssistantMessage(content="must not run"))
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", execute):
            with pytest.raises(BaseError):
                await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_rejects_output_identity_mutation() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()

    async def forge_output(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnStepOutput:
        del ops, event_next
        step_input = event.event_input
        return TurnStepOutput(
            turn_id="forged",
            index=step_input.index,
            answer="cached",
            tool_uses=(),
            stop_reason="stop",
        )

    bus.register_event_handler(TURN_STEP, forge_output, {}, HandlerLevel.USER)
    execute = AsyncMock(return_value=AssistantMessage(content="must not run"))
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", execute):
            with pytest.raises(BaseError):
                await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_preserves_non_message_control_result() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()
    expected = {"result_type": "error", "output": "stopped by rail"}

    async def rewrite_empty_projection(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        output = await event_next(event)
        return dataclasses.replace(output, answer="must be ignored")

    bus.register_event_handler(TURN_STEP, rewrite_empty_projection, {}, HandlerLevel.USER)
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", AsyncMock(return_value=expected)):
            result = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    assert result is expected
    assert ctx.extra["_turn_state"].step_index == 1


@pytest.mark.asyncio
async def test_context_recovery_retry_remains_one_turn_step() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()
    observed: list[TurnStepInput] = []

    async def observe(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        observed.append(event.event_input)
        return await event_next(event)

    bus.register_event_handler(TURN_STEP, observe, {}, HandlerLevel.USER)
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=True)
    model_call = AsyncMock(
        side_effect=[
            RuntimeError("context window exceeded"),
            AssistantMessage(content="recovered"),
        ]
    )
    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_railed_model_call", model_call):
            result = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    assert result.content == "recovered"
    assert len(observed) == 1
    assert observed[0].index == 0
    assert ctx.extra["_turn_state"].step_index == 1
    assert model_call.await_count == 2
    agent.context_engine.recover_from_model_exception.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_rail_retry_remains_one_turn_step() -> None:
    agent, ctx, context = _rig()
    bus = await _built_bus()
    observed: list[TurnStepInput] = []

    class RetryOnceRail(AgentRail):
        async def on_model_exception(self, callback_ctx: AgentCallbackContext) -> None:
            if callback_ctx.retry_attempt < 1:
                callback_ctx.request_retry()

    async def observe(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        observed.append(event.event_input)
        return await event_next(event)

    window = MagicMock()
    window.get_messages.return_value = []
    window.get_tools.return_value = []
    context.get_context_window = AsyncMock(return_value=window)
    model = MagicMock()
    model.invoke = AsyncMock(
        side_effect=[
            RuntimeError("retryable provider failure"),
            AssistantMessage(content="retried"),
        ]
    )
    await agent.register_rail(RetryOnceRail())
    bus.register_event_handler(TURN_STEP, observe, {}, HandlerLevel.USER)

    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_get_llm", return_value=model):
            result = await agent._call_model(ctx, context, None)
    finally:
        Runner.set_extension_bus(None)

    assert result.content == "retried"
    assert len(observed) == 1
    assert ctx.extra["_turn_state"].step_index == 1
    assert model.invoke.await_count == 2


@pytest.mark.asyncio
async def test_max_iteration_finalization_uses_next_turn_step() -> None:
    agent, ctx, context = _rig(step_index=7)
    ctx.extra["_react_iteration"] = 1
    bus = await _built_bus()
    execute = AsyncMock(return_value=AssistantMessage(content="final answer", finish_reason="stop"))

    Runner.set_extension_bus(bus)
    try:
        with patch.object(agent, "_execute_model_step", execute):
            result = await agent._finish_max_iterations(ctx, context, ctx.session)
    finally:
        Runner.set_extension_bus(None)

    assert result["result_type"] == "answer"
    assert result["output"] == "final answer"
    assert ctx.extra["_turn_state"].step_index == 8
    execute.assert_awaited_once_with(ctx, context, None)
