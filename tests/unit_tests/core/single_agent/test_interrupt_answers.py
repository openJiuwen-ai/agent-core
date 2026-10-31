# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""interrupt_answer builds the answer shape each interrupt kind expects."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.interrupt import InterruptKind, interrupt_answer
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    CountingTool,
    ask_permissions,
    make_deep_agent,
    pending,
    run,
    scripted,
    tool_results_seen,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


@pytest.mark.parametrize(("kind", "kwargs", "expected"), [
    (InterruptKind.PERMISSION, {"approved": True}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.CONFIRM, {"approved": True, "remember": True},
     {"approved": True, "feedback": "", "auto_confirm": True, "persist_allow": False}),
    (InterruptKind.TEAM_TOOL_APPROVAL, {"approved": False, "feedback": "no"},
     {"approved": False, "feedback": "no", "auto_confirm": False}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"approved": True}, {"action": "allow_once"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "always"}, {"action": "allow_always"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "not this one"},
     {"action": "reject", "feedback": "not this one"}),
    (InterruptKind.PERMISSION, {"text": "yes"}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.PERMISSION, {"text": ""}, {"approved": False, "feedback": "", "auto_confirm": False}),
    (InterruptKind.ASK_USER, {"text": "blue"}, "blue"),
    (InterruptKind.UNDECLARED, {"approved": True}, {"approved": True, "feedback": "", "auto_confirm": False}),
    (InterruptKind.UNDECLARED, {"text": "a"},
     {"approved": True, "feedback": "", "auto_confirm": True, "persist_allow": False}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "allow_once"}, {"action": "allow_once"}),
    (InterruptKind.SKILL_EVOLUTION_APPROVAL, {"text": "reject"}, {"action": "reject", "feedback": ""}),
])
def test_answer_shape_per_kind(kind, kwargs, expected):
    assert interrupt_answer(kind, **kwargs) == expected


@pytest.mark.parametrize(("kind", "kwargs"), [
    (InterruptKind.ASK_USER, {"approved": True}),
    (InterruptKind.CLIENT_TOOL, {"approved": False}),
    (InterruptKind.PERMISSION, {}),
])
def test_an_answer_that_does_not_fit_the_kind_is_refused(kind, kwargs):
    with pytest.raises(ValueError):
        interrupt_answer(kind, **kwargs)


@pytest.mark.parametrize("word", ["y", "Yes", "ok", "approve", "true", "1", "allow", "allow_once"])
def test_words_that_approve_once(word):
    assert interrupt_answer(InterruptKind.PERMISSION, text=word) == {
        "approved": True, "feedback": "", "auto_confirm": False,
    }


@pytest.mark.parametrize("word", ["n", "no", "deny", "reject"])
def test_words_that_reject_without_feedback(word):
    assert interrupt_answer(InterruptKind.CONFIRM, text=word) == {
        "approved": False, "feedback": "", "auto_confirm": False,
    }


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


_EXAMPLE = Path(__file__).resolve().parents[4] / "examples" / "client_tool" / "client_tool_demo.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("client_tool_demo", _EXAMPLE)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


async def _run_example_host_loop(agent, query: str, conversation_id: str) -> dict:
    """Run the agent and resume it with the example's answer_pending until nothing is pending."""
    demo = _load_example()
    result = await run(agent, query, conversation_id)
    for _ in range(4):
        if not pending(result):
            break
        result = await run(agent, demo.answer_pending(result), conversation_id)
    return result


@pytest.mark.asyncio
async def test_example_host_loop_approves_a_skill_evolution_change(tmp_path, runner):
    tool = CountingTool("simplify_skill_experiences")
    evolution = EvolutionInterruptRail(
        review_runtime=object(), submission_service=_StubSubmission(), auto_save=False, language="en"
    )
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[evolution])
    args = json.dumps({"subject": {"kind": "skill", "name": "demo"}, "actions": [{"action": "delete"}]})
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("simplify_skill_experiences", args, tool_call_id="call_e"),
        create_text_response("done"),
    ])
    with scripted(llm):
        result = await _run_example_host_loop(agent, "simplify", "example-evo")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 1


@pytest.mark.asyncio
async def test_example_host_loop_runs_a_permission_asked_client_call(tmp_path, runner):
    """The example's client branch: the permission ask is approved, the client runs read_clipboard."""
    demo = _load_example()
    agent = make_deep_agent(tmp_path, tools=[demo.ReadClipboardTool()], permissions=ask_permissions("read_clipboard"))
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("read_clipboard", "{}", tool_call_id="call_x"),
        create_text_response("done"),
    ])
    with scripted(llm):
        result = await _run_example_host_loop(agent, "what did I copy", "example-client")

    assert result["result_type"] == "answer"
    assert tool_results_seen(llm) == [("call_x", "Meeting moved to 3 pm, room 4.12")]
