# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public turn event contracts and default CORE behavior."""

from __future__ import annotations

import dataclasses

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.turn import (
    TURN_COMPLETE,
    TURN_START,
    TURN_STEP,
    TurnCompleteInput,
    TurnCompleteOutput,
    TurnStartInput,
    TurnStartOutput,
    TurnStepInput,
    TurnStepOutput,
    TurnStepToolUse,
    TurnStepTriggerContext,
    apply_turn_step_output,
    build_turn_step_message,
)
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "event_input", "expected_output"),
    [
        (
            TURN_START,
            TurnStartInput(turn_id="turn-1", text="hello"),
            TurnStartOutput(turn_id="turn-1"),
        ),
        (
            TURN_COMPLETE,
            TurnCompleteInput(
                turn_id="turn-1",
                reason="completed",
                answer="done",
                usage={"input_tokens": 2},
            ),
            TurnCompleteOutput(text="done", usage={"input_tokens": 2}),
        ),
        (
            TURN_STEP,
            TurnStepInput(turn_id="turn-1", index=0, model="fake", agent_id="agent-1"),
            TurnStepOutput(
                turn_id="turn-1",
                index=0,
                answer="done",
                tool_uses=(),
                stop_reason="stop",
                usage={"input_tokens": 2},
            ),
        ),
    ],
)
async def test_turn_event_core_contract_and_validators(
    event_type: str,
    event_input: object,
    expected_output: object,
) -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    if event_type == TURN_STEP:
        original = AssistantMessage(
            content="done",
            finish_reason="stop",
            usage_metadata=UsageMetadata(input_tokens=2),
        )
        execute_calls = 0

        async def execute_step(final_input: TurnStepInput) -> object:
            nonlocal execute_calls
            execute_calls += 1
            assert final_input is event_input
            return original

        trigger_context = TurnStepTriggerContext(execute_step=execute_step)
        assert (
            await bus.trigger(Event(event_type, event_input), trigger_context=trigger_context) == expected_output
        )
        assert execute_calls == 1
        assert trigger_context.original_result is original
    else:
        assert await bus.trigger(Event(event_type, event_input)) == expected_output

    with pytest.raises(BaseError):
        await bus.trigger(Event(event_type, object()))

    async def invalid_output(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del event, ops, event_next
        return object()

    bus.register_event_handler(event_type, invalid_output, {}, HandlerLevel.USER)

    if event_type == TURN_STEP:
        async def execute_step(final_input: TurnStepInput) -> object:
            del final_input
            return AssistantMessage(
                content="done",
                finish_reason="stop",
                usage_metadata=UsageMetadata(input_tokens=2),
            )

        assert (
            await bus.trigger(
                Event(event_type, event_input),
                trigger_context=TurnStepTriggerContext(execute_step=execute_step),
            )
            == expected_output
        )
    else:
        assert await bus.trigger(Event(event_type, event_input)) == expected_output


def _original_step_message() -> AssistantMessage:
    return AssistantMessage(
        content="original",
        metadata={"trace": "keep"},
        tool_calls=[
            ToolCall(
                id="call-1",
                type="provider_function",
                name="lookup",
                arguments='{"query":"old"}',
                index=3,
                response_item_id="item-1",
            ),
        ],
        usage_metadata=UsageMetadata(input_tokens=1),
        finish_reason="tool_calls",
        reasoning_content="private",
        prompt_token_ids=[1],
        completion_token_ids=[2],
        logprobs={"token": -0.1},
        provider_metadata={"provider": "keep"},
    )


@pytest.mark.parametrize("field", ["answer", "tool_uses", "stop_reason", "usage"])
def test_apply_turn_step_output_only_rewrites_selected_public_field(field: str) -> None:
    original = _original_step_message()
    projected = TurnStepOutput(
        turn_id="turn-1",
        index=0,
        answer=original.content,
        tool_uses=(
            TurnStepToolUse(
                tool_call_id="call-1",
                tool_name="lookup",
                arguments='{"query":"old"}',
            ),
        ),
        stop_reason="tool_calls",
        usage={"input_tokens": 1},
    )
    updates = {
        "answer": ["rewritten", {"type": "text", "text": "answer"}],
        "tool_uses": (
            TurnStepToolUse(
                tool_call_id="call-1",
                tool_name="lookup",
                arguments={"query": "new"},
            ),
        ),
        "stop_reason": "stop",
        "usage": {"input_tokens": 7, "output_tokens": 2},
    }

    result = apply_turn_step_output(
        original,
        dataclasses.replace(projected, **{field: updates[field]}),
        turn_id="turn-1",
        index=0,
    )

    assert result is not original
    assert result.metadata is original.metadata
    assert result.reasoning_content == "private"
    assert result.prompt_token_ids is original.prompt_token_ids
    assert result.completion_token_ids is original.completion_token_ids
    assert result.logprobs is original.logprobs
    assert result.provider_metadata is original.provider_metadata
    if field != "tool_uses":
        assert result.tool_calls[0] is original.tool_calls[0]
    else:
        assert result.tool_calls[0].type == "provider_function"
        assert result.tool_calls[0].index == 3
        assert result.tool_calls[0].response_item_id == "item-1"


def test_turn_step_equal_output_reuses_original_and_short_circuit_builds_message() -> None:
    original = _original_step_message()
    output = TurnStepOutput(
        turn_id="turn-1",
        index=2,
        answer=original.content,
        tool_uses=(
            TurnStepToolUse(
                tool_call_id="call-1",
                tool_name="lookup",
                arguments='{"query":"old"}',
            ),
        ),
        stop_reason="tool_calls",
        usage={"input_tokens": 1},
    )

    assert apply_turn_step_output(original, output, turn_id="turn-1", index=2) is original

    built = build_turn_step_message(output, turn_id="turn-1", index=2)
    assert built.content == "original"
    assert built.finish_reason == "tool_calls"
    assert built.tool_calls[0].model_dump() == {
        "id": "call-1",
        "type": "function",
        "name": "lookup",
        "arguments": '{"query":"old"}',
        "index": None,
        "response_item_id": None,
    }
    assert built.usage_metadata.input_tokens == 1
