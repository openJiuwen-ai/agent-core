# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AgentLoop tool batch baseline shared by direct and event-bus paths."""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.tool_call import TOOL_CALL, ToolCallEventOutput
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _make_tool(name: str) -> LocalFunction:
    card = ToolCard(
        id=name,
        name=name,
        description=f"{name} desc",
        input_params={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )

    async def _func(text: str) -> dict[str, str]:
        return {"tool": name, "text": text.upper() if name == "upper" else text}

    return LocalFunction(card=card, func=_func)


def _tool_calls() -> list[ToolCall]:
    return [
        ToolCall(id="call-1", type="function", name="echo", arguments=json.dumps({"text": "a"})),
        ToolCall(id="call-2", type="function", name="upper", arguments=json.dumps({"text": "b"})),
        ToolCall(id="call-3", type="function", name="echo", arguments=json.dumps({"text": "c"})),
    ]


async def _run_tool_batch(parallel: bool) -> tuple[AgentCallbackContext, list, list[Any]]:
    """Run one deterministic tool batch through ``_execute_tool_call``."""
    agent = ReActAgent(card=AgentCard(id=f"tool-batch-{parallel}"))
    agent.configure(
        ReActAgentConfig(model_name="fake", parallel_tool_calls=parallel, on_max_iterations="return_observations")
    )
    for name in ("echo", "upper"):
        tool = _make_tool(name)
        agent.ability_manager.add_ability(tool.card, tool)

    messages: list[Any] = []
    context = MagicMock()
    context.add_messages = AsyncMock(side_effect=messages.append)
    session = create_agent_session(session_id=f"tool-batch-session-{parallel}")
    ctx = AgentCallbackContext(agent=agent, inputs=InvokeInputs(query="q"), session=session)
    try:
        results = await agent._execute_tool_call(ctx, _tool_calls(), session, context)
    finally:
        agent.ability_manager.teardown_tools()
    return ctx, results, messages


def _snapshot(ctx: AgentCallbackContext, results: list, messages: list[Any]) -> dict[str, Any]:
    """Observable AgentLoop state that must match between direct and event-bus paths."""
    return {
        "results": [(result, type(message).__name__, message.tool_call_id) for result, message in results],
        "messages": [(type(message).__name__, message.model_dump(mode="json")) for message in messages],
        "observations": ctx.extra.get("_iteration_observations"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [True, False])
async def test_direct_path_executes_batch_once_and_commits_results(parallel: bool) -> None:
    original_execute = AbilityManager.execute
    returned: list[list] = []

    async def _spy(self, **kwargs):
        result = await original_execute(self, **kwargs)
        returned.append(result)
        return result

    assert Runner.get_extension_bus() is None
    await Runner.start()
    try:
        with patch.object(AbilityManager, "execute", autospec=True, side_effect=_spy) as execute:
            ctx, results, messages = await _run_tool_batch(parallel)
    finally:
        await Runner.stop()

    assert execute.await_count == 1
    call_kwargs = execute.await_args.kwargs
    assert call_kwargs["ctx"] is ctx
    assert [tc.id for tc in call_kwargs["tool_call"]] == ["call-1", "call-2", "call-3"]
    assert call_kwargs["session"] is ctx.session
    assert call_kwargs["parallel_tool_calls"] is parallel
    assert results is returned[0]

    snapshot = _snapshot(ctx, results, messages)
    assert snapshot["results"] == [
        ({"tool": "echo", "text": "a"}, "ToolMessage", "call-1"),
        ({"tool": "upper", "text": "B"}, "ToolMessage", "call-2"),
        ({"tool": "echo", "text": "c"}, "ToolMessage", "call-3"),
    ]
    assert all(message is tool_message for message, (_, tool_message) in zip(messages, results))
    assert [(item["tool_call_id"], item["name"]) for item in snapshot["observations"]] == [
        ("call-1", "echo"),
        ("call-2", "upper"),
        ("call-3", "echo"),
    ]


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [True, False])
async def test_empty_extension_chain_matches_direct_path(parallel: bool) -> None:
    await Runner.start()
    try:
        direct = _snapshot(*await _run_tool_batch(parallel))

        bus = await _built_bus()
        Runner.set_extension_bus(bus)
        with (
            patch.object(bus, "trigger", wraps=bus.trigger) as trigger,
            patch.object(AbilityManager, "execute", autospec=True, side_effect=AbilityManager.execute) as execute,
        ):
            via_bus = _snapshot(*await _run_tool_batch(parallel))
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()

    assert trigger.await_count == 1
    assert trigger.await_args.args[0].event_type == TOOL_CALL
    assert execute.await_count == 1
    assert execute.await_args.kwargs["parallel_tool_calls"] is parallel
    assert via_bus == direct


@pytest.mark.asyncio
async def test_agent_loop_rejects_short_circuit_output_for_another_batch() -> None:
    async def forge(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops, event_next
        return tuple(ToolCallEventOutput(tool_call_id=f"forged-{item.tool_call_id}") for item in event.event_input)

    await Runner.start()
    try:
        bus = await _built_bus()
        bus.register_event_handler(TOOL_CALL, forge, {}, HandlerLevel.USER)
        Runner.set_extension_bus(bus)
        with patch.object(AbilityManager, "execute", autospec=True) as execute:
            with pytest.raises(BaseError):
                await _run_tool_batch(True)
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()

    assert execute.await_count == 0


@pytest.mark.asyncio
async def test_handler_output_is_applied_onto_raw_results() -> None:
    async def redact(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        outputs = await event_next(event)
        return tuple(
            dataclasses.replace(output, result="R", text="redacted") if output.tool_call_id == "call-2" else output
            for output in outputs
        )

    await Runner.start()
    try:
        bus = await _built_bus()
        bus.register_event_handler(TOOL_CALL, redact, {}, HandlerLevel.USER)
        Runner.set_extension_bus(bus)
        _, results, messages = await _run_tool_batch(True)
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()

    assert results[0][0] == {"tool": "echo", "text": "a"}
    assert results[1][0] == "R"
    message = results[1][1]
    assert (message.role, message.tool_call_id, message.content) == ("tool", "call-2", "redacted")
    assert [m.tool_call_id for m in messages] == ["call-1", "call-2", "call-3"]
    assert messages[1] is message


@pytest.mark.asyncio
async def test_short_circuit_output_builds_results_without_execute() -> None:
    async def answer(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops, event_next
        return tuple(
            ToolCallEventOutput(tool_call_id=item.tool_call_id, result="cached", text="cached text")
            for item in event.event_input
        )

    await Runner.start()
    try:
        bus = await _built_bus()
        bus.register_event_handler(TOOL_CALL, answer, {}, HandlerLevel.USER)
        Runner.set_extension_bus(bus)
        with patch.object(AbilityManager, "execute", autospec=True) as execute:
            _, results, messages = await _run_tool_batch(True)
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()

    assert execute.await_count == 0
    assert [(result, m.tool_call_id, m.content) for result, m in results] == [
        ("cached", "call-1", "cached text"),
        ("cached", "call-2", "cached text"),
        ("cached", "call-3", "cached text"),
    ]
    assert [m.tool_call_id for m in messages] == ["call-1", "call-2", "call-3"]


@pytest.mark.asyncio
async def test_event_path_failure_propagates_without_direct_replay() -> None:
    await Runner.start()
    try:
        Runner.set_extension_bus(await _built_bus())
        with patch.object(AbilityManager, "execute", autospec=True, side_effect=RuntimeError("boom")) as execute:
            with pytest.raises(RuntimeError, match="boom"):
                await _run_tool_batch(True)
    finally:
        Runner.set_extension_bus(None)
        await Runner.stop()

    assert execute.await_count == 1
