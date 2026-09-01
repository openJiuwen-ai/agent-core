# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the resume-input contract between the interrupt handler and TaskTool."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.state import SUB_AGENT_RESUME_INPUT_KEY
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


class _FakeSubAgent:
    """Subagent recording the inputs it is invoked with."""

    def __init__(self) -> None:
        self.card = AgentCard(id="sub", name="sub", description="sub")
        self.inputs: list[dict] = []

    async def invoke(self, inputs: dict) -> dict:
        self.inputs.append(dict(inputs))
        return {"output": "done"}


def _make_tool(subagent: _FakeSubAgent) -> TaskTool:
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(model=None, kv_cache_affinity_config=None),
        create_subagent=lambda *_args, **_kwargs: subagent,
    )
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent)


def _resume_tool_call(user_input) -> ToolCall:
    tool_call = ToolCall(
        id="call_1",
        type="function",
        name="task_tool",
        arguments='{"subagent_type": "code", "task_description": "run task"}',
    )
    return ToolInterruptHandler._build_sub_agent_resume_tool_call(tool_call, user_input)


def test_handler_writes_the_answer_under_the_shared_key() -> None:
    """The handler hands the answer over under the agreed argument name."""
    user_input = {"action": "allow_once"}

    resumed = _resume_tool_call(user_input)

    assert resumed.arguments[SUB_AGENT_RESUME_INPUT_KEY] == user_input


def test_handler_still_answers_agent_abilities_through_query() -> None:
    """An agent registered as an ability keeps reading the answer from "query"."""
    user_input = {"action": "allow_once"}

    resumed = _resume_tool_call(user_input)

    assert resumed.arguments["query"] == user_input


def test_handler_preserves_the_original_arguments() -> None:
    """The replayed call still identifies which delegation it is resuming."""
    resumed = _resume_tool_call({"action": "allow_once"})

    assert resumed.arguments["subagent_type"] == "code"
    assert resumed.arguments["task_description"] == "run task"


@pytest.mark.asyncio
async def test_resume_key_round_trips_into_the_subagent_query() -> None:
    """What the handler writes is what TaskTool forwards to the subagent."""
    subagent = _FakeSubAgent()
    user_input = {"action": "allow_once"}
    resumed = _resume_tool_call(user_input)

    await _make_tool(subagent).invoke(
        resumed.arguments, session=Session(session_id="parent_session")
    )

    # The answer replaces the task description, so the subagent resumes its
    # parked turn instead of starting the original task over.
    assert subagent.inputs[0]["query"] == user_input


@pytest.mark.asyncio
async def test_call_without_resume_input_still_sends_the_task_description() -> None:
    """The first, uninterrupted call is unaffected by the resume contract."""
    subagent = _FakeSubAgent()

    await _make_tool(subagent).invoke(
        {"subagent_type": "code", "task_description": "run task"},
        session=Session(session_id="parent_session"),
    )

    assert subagent.inputs[0]["query"] == "run task"


@pytest.mark.asyncio
async def test_resumed_call_reaches_the_interrupted_sub_session() -> None:
    """The resumed delegation targets the session holding the parked state."""
    subagent = _FakeSubAgent()
    tool = _make_tool(subagent)
    session = Session(session_id="parent_session")

    await tool.invoke(
        {"subagent_type": "code", "task_description": "run task"}, session=session
    )
    await tool.invoke(_resume_tool_call({"action": "allow_once"}).arguments, session=session)

    first, second = subagent.inputs
    assert first["conversation_id"] == second["conversation_id"]
