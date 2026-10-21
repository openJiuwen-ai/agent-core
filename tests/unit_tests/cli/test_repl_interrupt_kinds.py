# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The CLI REPL renders and answers interrupts by their kind."""

from __future__ import annotations

import io
import json

import pytest
import pytest_asyncio
from rich.console import Console

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptKind,
    InterruptRequest,
    ToolCallInterruptRequest,
)
from openjiuwen.harness.cli.ui.renderer import PendingInteraction, render_stream
from openjiuwen.harness.cli.ui.repl import _collect_interaction_answers, _render_interaction
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    CountingTool,
    UndeclaredConfirmRail,
    make_deep_agent,
    pending,
    run,
    scripted,
    tool_results_seen,
    trip_workflow,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


class TypingUser:
    """Stands in for the prompt_toolkit session: the user types the given lines in order."""

    def __init__(self, *lines: str):
        self.lines = list(lines)
        self.prompts: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.lines.pop(0)


def _payload(kind: str, call_id: str = "call_1", tool_name: str = "demo_tool", **fields) -> ToolCallInterruptRequest:
    request = InterruptRequest(message=fields.pop("message", "m"), interrupt_kind=kind, interrupt_id=f"intr_{call_id}")
    payload = ToolCallInterruptRequest.from_tool_call(
        request, ToolCall(id=call_id, type="function", name=tool_name, arguments="{}")
    )
    for name, value in fields.items():
        setattr(payload, name, value)
    return payload


def _console() -> Console:
    return Console(file=io.StringIO(), width=120)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "typed", "expected"),
    [
        (InterruptKind.PERMISSION, "y", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "ok", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "approve", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "allow_once", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "reject", {"approved": False, "feedback": "", "auto_confirm": False}),
        (InterruptKind.UNDECLARED, "y", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.UNDECLARED, "", {"approved": True, "feedback": "", "auto_confirm": False}),
        (InterruptKind.UNDECLARED, "not now", {"approved": False, "feedback": "not now", "auto_confirm": False}),
        (InterruptKind.PERMISSION, "a", {"approved": True, "feedback": "", "auto_confirm": True,
                                         "persist_allow": False}),
        (InterruptKind.TEAM_TOOL_APPROVAL, "n", {"approved": False, "feedback": "", "auto_confirm": False}),
        (InterruptKind.CONFIRM, "no thanks", {"approved": False, "feedback": "no thanks", "auto_confirm": False}),
        (InterruptKind.SKILL_EVOLUTION_APPROVAL, "y", {"action": "allow_once"}),
        (InterruptKind.SKILL_EVOLUTION_APPROVAL, "a", {"action": "allow_always"}),
        (InterruptKind.SKILL_EVOLUTION_APPROVAL, "n", {"action": "reject", "feedback": ""}),
        (InterruptKind.SKILL_EVOLUTION_APPROVAL, "allow_once", {"action": "allow_once"}),
        (InterruptKind.SKILL_EVOLUTION_APPROVAL, "reject", {"action": "reject", "feedback": ""}),
        ("my_kind", '{"choice": 2}', {"choice": 2}),
        ("my_kind", "free text", "free text"),
    ],
)
async def test_answers_take_the_shape_of_the_interrupt_kind(kind, typed, expected):
    payload = _payload(kind)
    interactive_input = await _collect_interaction_answers(
        [PendingInteraction(interaction_id="call_1", request=payload)], TypingUser(typed), _console()
    )

    assert interactive_input.interrupt_answers == {payload.interrupt_id: expected}
    assert interactive_input.user_inputs == {}


@pytest.mark.asyncio
async def test_ask_user_answers_each_question():
    questions = [{"header": "Color", "question": "Which color?", "options": []}]
    payload = _payload(InterruptKind.ASK_USER, tool_name="ask_user", questions=questions)

    interactive_input = await _collect_interaction_answers(
        [PendingInteraction(interaction_id="call_1", request=payload)], TypingUser("blue"), _console()
    )

    assert interactive_input.interrupt_answers == {payload.interrupt_id: {"answers": {"Which color?": "blue"}}}


def test_render_uses_the_kind():
    console = _console()
    _render_interaction(_payload(InterruptKind.SKILL_EVOLUTION_APPROVAL, message="Approve 1 change?"), console)
    _render_interaction(_payload("my_kind", tool_name="odd_tool", message="Pick one", payload_schema={"x": 1}),
                        console)
    output = console.file.getvalue()
    assert "Approve 1 change?" in output and "a/always=allow for this session" in output
    assert "waiting for an answer (my_kind)" in output and "Pick one" in output


def test_render_asks_an_undeclared_kind_for_approval():
    console = _console()
    _render_interaction(_payload(InterruptKind.UNDECLARED, tool_name="odd_tool"), console)
    output = console.file.getvalue()
    assert "Approve" in output and "n/no=reject" in output


class _StubSubmission:
    async def prepare_simplify_submission(self, subject, actions):
        return None


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_c_cli_approves_a_skill_evolution_change(tmp_path, runner):
    """Bug C: the CLI answered a skill evolution approval with {"approved": ...}; the rail
    needs {"action": ...}, so the approval looped and could never be granted."""
    tool = CountingTool("simplify_skill_experiences")
    agent = make_deep_agent(
        tmp_path,
        tools=[tool],
        rails=[EvolutionInterruptRail(
            review_runtime=object(), submission_service=_StubSubmission(), auto_save=False, language="en"
        )],
    )
    args = json.dumps({"subject": {"kind": "skill", "name": "demo-skill"},
                       "actions": [{"action": "delete", "record_id": "r1", "reason": "stale"}]})
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("simplify_skill_experiences", args, tool_call_id="call_C"),
        create_text_response("done"),
    ])
    with scripted(llm):
        result = await run(agent, "simplify demo-skill experiences", "cli_c")
        interactions = [PendingInteraction(interaction_id=i, request=v) for i, v in pending(result)]
        answers = await _collect_interaction_answers(interactions, TypingUser("y"), _console())
        result = await run(agent, answers, "cli_c")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 1


async def _stream_round(agent, session_id: str, query, console: Console):
    """One REPL round: stream the agent through the CLI renderer, rendering each interaction as it arrives."""
    async def on_interaction(_interaction_id, request):
        _render_interaction(request, console)
        return ""

    stream = Runner.run_agent_streaming(agent, {"query": query}, session=session_id)
    return await render_stream(stream, console, on_interaction=on_interaction)


async def _answer_round(agent, session_id: str, query: str, *typed: str):
    """Ask, answer what the agent asks as typed, and resume, as the REPL loop does."""
    console = _console()
    asked = await _stream_round(agent, session_id, query, console)
    assert asked.pending_interactions, "the agent did not ask anything"
    answers = await _collect_interaction_answers(asked.pending_interactions, TypingUser(*typed), console)
    resumed = await _stream_round(agent, session_id, answers, console)
    return resumed, answers, console.file.getvalue()


@pytest.mark.asyncio
async def test_cli_approves_an_undeclared_kind_with_y(tmp_path, runner):
    """A rail that declares no kind is approved with y, as the CLI did before kinds existed."""
    tool = CountingTool("doc_tool")
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[UndeclaredConfirmRail(tool_names=["doc_tool"])])
    llm = MockLLMModel()
    llm.set_responses([create_tool_call_response("doc_tool", "{}", tool_call_id="call_n"),
                       create_text_response("done")])
    with scripted(llm):
        resumed, _, _ = await _answer_round(agent, "cli_undeclared", "run doc_tool", "y")

    assert resumed.pending_interactions == []
    assert tool.invoke_count == 1


@pytest.mark.asyncio
async def test_cli_asks_a_workflow_interaction_and_answers_it_by_its_own_id(tmp_path, runner):
    """A workflow's session.interact value is not an InterruptRequest: no interrupt id, no kind."""
    agent = make_deep_agent(tmp_path)
    agent.ability_manager.add(trip_workflow())
    llm = MockLLMModel()
    llm.set_responses([create_tool_call_response("plan_trip", "{}", tool_call_id="call_w"),
                       create_text_response("done")])
    with scripted(llm):
        resumed, answers, output = await _answer_round(agent, "cli_workflow", "plan a trip", "Paris")

    assert "Which city?" in output
    assert answers.user_inputs == {"ask_city": "Paris"} and answers.interrupt_answers == {}
    assert resumed.pending_interactions == []
    assert "Paris" in tool_results_seen(llm)[-1][1]
