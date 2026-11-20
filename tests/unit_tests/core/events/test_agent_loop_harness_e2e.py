# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process Harness E2E for AgentLoop events on the published ExtensionEventBus."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Callable

import pytest

from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.tool_call import TOOL_CALL
from openjiuwen.core.events.builtins.event_types.tool_check import TOOL_CHECK, ToolCheckInput, ToolCheckOutput
from openjiuwen.core.events.builtins.event_types.turn import (
    TURN_COMPLETE,
    TURN_START,
    TURN_STEP,
    TurnCompleteInput,
    TurnCompleteOutput,
    TurnStartInput,
    TurnStepInput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import HandlerLevel
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from tests.system_tests.agent.react_agent.interrupt.test_base import assert_answer_result
from tests.unit_tests.core.events.conftest import (
    PROBE_ECHO_TOOL_NAME,
    build_extension_bus,
    default_agent_options,
    fake_model,
    harness_runner,
    patch_mock_llm,
    probe_echo_tool,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


@dataclass
class TurnLifecycleTrace:
    """Turn.start / turn.complete timeline for text-only rounds."""

    lines: list[str] = field(default_factory=list)
    starts: list[TurnStartInput] = field(default_factory=list)
    completes: list[TurnCompleteInput] = field(default_factory=list)


@dataclass
class FullLoopTrace:
    """Full AgentLoop event timeline including turn.step and tool events."""

    lines: list[str] = field(default_factory=list)
    starts: list[TurnStartInput] = field(default_factory=list)
    steps: list[TurnStepInput] = field(default_factory=list)
    completes: list[TurnCompleteInput] = field(default_factory=list)


def _turn_tracing_handler(event_type: str, trace: TurnLifecycleTrace) -> Callable:
    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        trace.lines.append(f"{event_type}:before")
        if event_type == TURN_START and isinstance(event.event_input, TurnStartInput):
            trace.starts.append(event.event_input)
        if event_type == TURN_COMPLETE and isinstance(event.event_input, TurnCompleteInput):
            trace.completes.append(event.event_input)
        output = await event_next(event)
        if event_type == TURN_COMPLETE and isinstance(event.event_input, TurnCompleteInput):
            trace.lines.append(f"{event_type}:after:{event.event_input.reason}")
        else:
            trace.lines.append(f"{event_type}:after")
        return output

    return handler


def _full_loop_tracing_handler(event_type: str, trace: FullLoopTrace) -> Callable:
    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        trace.lines.append(f"{event_type}:before")
        if event_type == TURN_START and isinstance(event.event_input, TurnStartInput):
            trace.starts.append(event.event_input)
        if event_type == TURN_STEP and isinstance(event.event_input, TurnStepInput):
            trace.steps.append(event.event_input)
        if event_type == TURN_COMPLETE and isinstance(event.event_input, TurnCompleteInput):
            trace.completes.append(event.event_input)
        output = await event_next(event)
        if event_type == TOOL_CHECK and isinstance(output, ToolCheckOutput):
            trace.lines.append(f"{event_type}:after:{output.decision}")
        elif event_type == TURN_COMPLETE and isinstance(event.event_input, TurnCompleteInput):
            trace.lines.append(f"{event_type}:after:{event.event_input.reason}")
        else:
            trace.lines.append(f"{event_type}:after")
        return output

    return handler


async def _bus_with_turn_tracers(trace: TurnLifecycleTrace) -> ExtensionEventBus:
    bus = await build_extension_bus()
    bus.register_event_handler(TURN_START, _turn_tracing_handler(TURN_START, trace), {}, HandlerLevel.USER)
    bus.register_event_handler(TURN_COMPLETE, _turn_tracing_handler(TURN_COMPLETE, trace), {}, HandlerLevel.USER)
    return bus


async def _bus_with_full_loop_tracers(trace: FullLoopTrace) -> ExtensionEventBus:
    bus = await build_extension_bus()
    for event_type in (TURN_START, TURN_STEP, TOOL_CALL, TOOL_CHECK, TURN_COMPLETE):
        bus.register_event_handler(event_type, _full_loop_tracing_handler(event_type, trace), {}, HandlerLevel.USER)
    return bus


def _assert_turn_lifecycle_order(trace: TurnLifecycleTrace, *, reason: str) -> None:
    start_before = trace.lines.index(f"{TURN_START}:before")
    start_after = trace.lines.index(f"{TURN_START}:after", start_before + 1)
    complete_before = trace.lines.index(f"{TURN_COMPLETE}:before", start_after + 1)
    complete_after = trace.lines.index(f"{TURN_COMPLETE}:after:{reason}", complete_before + 1)
    assert start_before < start_after < complete_before < complete_after


def _assert_full_tool_round_order(trace: FullLoopTrace) -> None:
    start_before = trace.lines.index(f"{TURN_START}:before")
    start_after = trace.lines.index(f"{TURN_START}:after", start_before + 1)
    first_step_before = trace.lines.index(f"{TURN_STEP}:before", start_after + 1)
    first_step_after = trace.lines.index(f"{TURN_STEP}:after", first_step_before + 1)
    call_before = trace.lines.index(f"{TOOL_CALL}:before", first_step_after + 1)
    check_before = trace.lines.index(f"{TOOL_CHECK}:before", call_before + 1)
    check_after = trace.lines.index(f"{TOOL_CHECK}:after:allow", check_before + 1)
    call_after = trace.lines.index(f"{TOOL_CALL}:after", check_after + 1)
    second_step_before = trace.lines.index(f"{TURN_STEP}:before", call_after + 1)
    second_step_after = trace.lines.index(f"{TURN_STEP}:after", second_step_before + 1)
    complete_before = trace.lines.index(f"{TURN_COMPLETE}:before", second_step_after + 1)
    complete_after = trace.lines.index(f"{TURN_COMPLETE}:after:completed", complete_before + 1)
    assert start_before < complete_after


@pytest.mark.asyncio
async def test_text_invoke_traces_turn_start_then_turn_complete(tmp_path) -> None:
    """One mock text round: turn.start precedes turn.complete with matching turn_id."""
    query = "hello turn events"
    llm_answer = "mock answer for turn e2e"
    trace = TurnLifecycleTrace()
    bus = await _bus_with_turn_tracers(trace)
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response(llm_answer)])

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"turn_evt_{uuid.uuid4().hex[:10]}", name="TurnEventE2E"),
            max_iterations=3,
            system_prompt="Answer briefly.",
            **default_agent_options(tmp_path),
        )
        with patch_mock_llm(mock_llm):
            result = await Runner.run_agent(
                agent=agent,
                inputs={"query": query, "conversation_id": f"turn_evt_{uuid.uuid4().hex[:8]}"},
            )

    assert_answer_result(result)
    assert result.get("output") == llm_answer
    assert trace.lines.count(f"{TURN_START}:before") == 1
    assert trace.lines.count(f"{TURN_COMPLETE}:before") == 1
    assert len(trace.starts) == 1
    assert len(trace.completes) == 1
    assert trace.starts[0].text == query
    assert trace.completes[0].turn_id == trace.starts[0].turn_id
    assert trace.completes[0].reason == "completed"
    assert trace.completes[0].answer == llm_answer
    _assert_turn_lifecycle_order(trace, reason="completed")


@pytest.mark.asyncio
async def test_extension_turn_complete_rewrites_terminal_output(tmp_path) -> None:
    """USER handler may normalize terminal text via turn.complete output."""

    async def normalize_complete(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> TurnCompleteOutput:
        del ops
        output = await event_next(event)
        assert isinstance(output, TurnCompleteOutput)
        return TurnCompleteOutput(text=f"normalized:{output.text}", usage={"total_tokens": 7})

    llm_answer = "raw terminal"
    bus = await build_extension_bus()
    bus.register_event_handler(TURN_COMPLETE, normalize_complete, {}, HandlerLevel.USER)
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response(llm_answer)])

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"turn_evt_norm_{uuid.uuid4().hex[:10]}", name="TurnEventNormalize"),
            max_iterations=3,
            system_prompt="Answer briefly.",
            **default_agent_options(tmp_path),
        )
        with patch_mock_llm(mock_llm):
            result = await Runner.run_agent(
                agent=agent,
                inputs={"query": "normalize me", "conversation_id": f"turn_evt_norm_{uuid.uuid4().hex[:8]}"},
            )

    assert_answer_result(result)
    assert result.get("output") == f"normalized:{llm_answer}"
    assert result.get("usage") == {"total_tokens": 7}


@pytest.mark.asyncio
async def test_tool_round_traces_full_agent_loop_event_order(tmp_path, invoke_counter: list[int]) -> None:
    """One tool round: start, two steps, check inside call, then complete."""
    trace = FullLoopTrace()
    bus = await _bus_with_full_loop_tracers(trace)
    llm_answer = "done"
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response(
                PROBE_ECHO_TOOL_NAME,
                json.dumps({"payload": "ping"}),
                tool_call_id="tc_probe_loop",
            ),
            create_text_response(llm_answer),
        ]
    )

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"loop_evt_{uuid.uuid4().hex[:10]}", name="AgentLoopEventE2E"),
            tools=[probe_echo_tool(invoke_counter)],
            max_iterations=5,
            system_prompt="Execute mock tool calls.",
            **default_agent_options(tmp_path),
        )
        with patch_mock_llm(mock_llm):
            result = await Runner.run_agent(
                agent=agent,
                inputs={"query": "call probe", "conversation_id": f"loop_evt_{uuid.uuid4().hex[:8]}"},
            )

    assert_answer_result(result)
    assert result.get("output") == llm_answer
    assert invoke_counter[0] == 1
    assert len(trace.starts) == 1
    assert len(trace.steps) == 2
    assert [step.index for step in trace.steps] == [0, 1]
    assert len(trace.completes) == 1
    turn_id = trace.starts[0].turn_id
    assert trace.starts[0].text == "call probe"
    assert all(step.turn_id == turn_id for step in trace.steps)
    assert trace.completes[0].turn_id == turn_id
    assert trace.completes[0].reason == "completed"
    assert trace.completes[0].answer == llm_answer
    assert trace.lines.count(f"{TOOL_CALL}:before") == 1
    assert trace.lines.count(f"{TOOL_CHECK}:before") == 1
    _assert_full_tool_round_order(trace)


@pytest.mark.asyncio
async def test_extension_tool_check_deny_skips_probe_tool(tmp_path, invoke_counter: list[int]) -> None:
    """USER handler short-circuits tool.check with deny; probe tool is not executed."""

    async def deny_probe(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> ToolCheckOutput:
        del ops, event_next
        check_input = event.event_input
        if isinstance(check_input, ToolCheckInput) and check_input.tool_name == PROBE_ECHO_TOOL_NAME:
            return ToolCheckOutput(decision="deny", reason="e2e blocked")
        raise AssertionError("unexpected tool.check input")

    bus = await build_extension_bus()
    bus.register_event_handler(
        TOOL_CHECK,
        deny_probe,
        {"tool_name": PROBE_ECHO_TOOL_NAME},
        HandlerLevel.USER,
    )
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response(
                PROBE_ECHO_TOOL_NAME,
                json.dumps({"payload": "blocked"}),
                tool_call_id="tc_probe_deny",
            ),
            create_text_response("after deny"),
        ]
    )

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"tool_evt_deny_{uuid.uuid4().hex[:10]}", name="ToolEventDeny"),
            tools=[probe_echo_tool(invoke_counter)],
            max_iterations=5,
            system_prompt="Execute mock tool calls.",
            **default_agent_options(tmp_path),
        )
        with patch_mock_llm(mock_llm):
            result = await Runner.run_agent(
                agent=agent,
                inputs={"query": "call probe", "conversation_id": f"tool_evt_deny_{uuid.uuid4().hex[:8]}"},
            )

    assert_answer_result(result)
    assert invoke_counter[0] == 0
