# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for a resumed delegation reaching the sub-agent that is waiting.

An approved interrupt has to continue the paused sub-agent, not start an
identical one. The sub-agent finds its parked ``ToolInterruptionState`` through
the session state it persists, which is namespaced by session id *and* agent
id, so both have to survive the pause. ``_SubAgentStateStore`` below mirrors
that two-part namespace, which is what the delegation is asserted against.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import _sub_agent_card_for_session
from openjiuwen.harness.tools.subagent.task_tool import TaskTool

_SPEC_CARD_NAME = "general-purpose"
_TASK = "audit the deployment scripts"


def _interrupt_envelope() -> dict:
    return {"result_type": "interrupt", "state": [], "interrupt_ids": ["inner_bash"]}


def _parked_state() -> ToolInterruptionState:
    """The state a sub-agent leaves behind when it stops to ask a question."""
    inner_call = ToolCall(
        id="inner_bash", type="function", name="bash", arguments='{"command": "ls"}'
    )
    state = ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[inner_call]),
        iteration=5,
        original_query=_TASK,
    )
    state.interrupted_tools = {
        "inner_bash": ToolInterruptEntry(tool_call=inner_call, is_sub_agent=False)
    }
    return state


class _SubAgentStateStore:
    """Persisted sub-agent state, namespaced the way AgentStorage namespaces it."""

    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], dict] = {}

    def load(self, session_id: str, agent_id: str) -> dict:
        return self.blobs.setdefault((session_id, agent_id), {})

    def namespaces(self) -> list[tuple[str, str]]:
        return sorted(self.blobs)


class _FakeSubAgent:
    """Sub-agent that resumes from parked state, or starts over when it finds none.

    Stands in for the ReAct loop's own resume dispatch: it looks for an
    interruption under its own namespace and either continues the paused run or
    treats the input as a brand new request.
    """

    def __init__(self, card, store: _SubAgentStateStore, interrupt_again: bool = False) -> None:
        self.card = card
        self._store = store
        self._interrupt_again = interrupt_again
        self.runs: list[dict] = []

    async def invoke(self, inputs: dict) -> dict:
        session_id = inputs["conversation_id"]
        state = self._store.load(session_id, self.card.id)
        parked = state.get(INTERRUPTION_KEY)

        if parked is not None:
            # Resume: the answer is delivered to the tool that was waiting.
            state[INTERRUPTION_KEY] = None
            self.runs.append({"mode": "resume", "query": inputs["query"]})
            if self._interrupt_again:
                state[INTERRUPTION_KEY] = _parked_state()
                return _interrupt_envelope()
            return {"output": "audit complete"}

        # No parked state: a fresh run over whatever arrived as the query.
        self.runs.append({"mode": "start", "query": inputs["query"]})
        state[INTERRUPTION_KEY] = _parked_state()
        return _interrupt_envelope()


class _ParentAgent:
    """Parent whose sub-agent card is derived exactly as create_subagent derives it.

    A new instance stands for the parent being rebuilt, which is what happens
    between a question being asked and its answer arriving.
    """

    def __init__(self, store: _SubAgentStateStore, interrupt_again: bool = False) -> None:
        self.deep_config = SimpleNamespace(model=None, kv_cache_affinity_config=None)
        self._store = store
        self._interrupt_again = interrupt_again
        # Minted per build, exactly as the injected general-purpose spec is.
        self._spec_card = AgentCard(name=_SPEC_CARD_NAME, description="gp")
        self.subagents: list[_FakeSubAgent] = []

    def create_subagent(self, subagent_type: str, subsession_id: str, **_kwargs):
        card = _sub_agent_card_for_session(self._spec_card, subsession_id)
        subagent = _FakeSubAgent(card, self._store, self._interrupt_again)
        self.subagents.append(subagent)
        return subagent


def _tool(parent: _ParentAgent) -> TaskTool:
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent)


def _resume_arguments(user_input) -> dict:
    """The arguments the handler replays a paused delegation with."""
    tool_call = ToolCall(
        id="call_1",
        type="function",
        name="task_tool",
        arguments=f'{{"subagent_type": "{_SPEC_CARD_NAME}", "task_description": "{_TASK}"}}',
    )
    return ToolInterruptHandler._build_sub_agent_resume_tool_call(
        tool_call, user_input
    ).arguments


async def _ask_then_answer(store, answer, interrupt_again: bool = False):
    """Run the delegation, then resume it from a rebuilt parent."""
    session = Session(session_id="parent_session")

    asking_parent = _ParentAgent(store)
    first = await _tool(asking_parent).invoke(
        {"subagent_type": _SPEC_CARD_NAME, "task_description": _TASK}, session=session
    )

    # The answer arrives on a new turn, against a freshly built parent.
    answering_parent = _ParentAgent(store, interrupt_again=interrupt_again)
    second = await _tool(answering_parent).invoke(_resume_arguments(answer), session=session)

    return asking_parent, answering_parent, first, second


@pytest.mark.asyncio
async def test_the_first_run_parks_its_state_and_surfaces_the_question() -> None:
    """The behaviour being protected: the interrupt still reaches the caller."""
    store = _SubAgentStateStore()
    asking_parent, _, first, _ = await _ask_then_answer(store, {"approved": True})

    assert ToolInterruptHandler._is_sub_agent_interrupt(first) is True
    assert asking_parent.subagents[0].runs[0]["mode"] == "start"


@pytest.mark.asyncio
async def test_resumed_delegation_reaches_the_same_sub_session() -> None:
    """Both runs address one session, so one parked state is in play."""
    store = _SubAgentStateStore()
    asking_parent, answering_parent, _, _ = await _ask_then_answer(store, {"approved": True})

    assert len(store.namespaces()) == 1


@pytest.mark.asyncio
async def test_resumed_delegation_reaches_the_same_sub_agent_identity() -> None:
    """The rebuilt parent must address the identity holding the parked state."""
    store = _SubAgentStateStore()
    asking_parent, answering_parent, _, _ = await _ask_then_answer(store, {"approved": True})

    assert asking_parent.subagents[0].card.id == answering_parent.subagents[0].card.id


@pytest.mark.asyncio
async def test_resumed_delegation_resumes_instead_of_restarting() -> None:
    """An approval continues the paused work rather than repeating it."""
    store = _SubAgentStateStore()
    _, answering_parent, _, _ = await _ask_then_answer(store, {"approved": True})

    resumed_run = answering_parent.subagents[0].runs[0]
    assert resumed_run["mode"] == "resume"
    # Re-running the task from scratch loses the work already done, and is
    # worse than pausing, because the approval appears to have been honoured.
    assert resumed_run["query"] != _TASK


@pytest.mark.asyncio
async def test_resume_consumes_the_parked_interruption_state() -> None:
    """The answer clears the state, so nothing stays parked behind the turn."""
    store = _SubAgentStateStore()
    _, answering_parent, _, _ = await _ask_then_answer(store, {"approved": True})

    (namespace,) = store.namespaces()
    assert store.blobs[namespace][INTERRUPTION_KEY] is None


@pytest.mark.asyncio
async def test_resume_delivers_the_answer_to_the_waiting_sub_agent() -> None:
    """The user's decision, not the original task, is what the resume carries."""
    store = _SubAgentStateStore()
    answer = {"approved": True, "auto_confirm": False, "feedback": ""}
    _, answering_parent, _, _ = await _ask_then_answer(store, answer)

    assert answering_parent.subagents[0].runs[0]["query"] == answer


@pytest.mark.asyncio
async def test_completed_resume_returns_the_sub_agent_result() -> None:
    """A resumed run that finishes reports its output to the parent."""
    store = _SubAgentStateStore()
    _, _, _, second = await _ask_then_answer(store, {"approved": True})

    assert second.success is True
    assert second.data["output"] == "audit complete"


@pytest.mark.asyncio
async def test_second_interrupt_in_the_resumed_run_still_surfaces() -> None:
    """A resumed run that pauses again asks again, rather than going quiet."""
    store = _SubAgentStateStore()
    _, answering_parent, _, second = await _ask_then_answer(
        store, {"approved": True}, interrupt_again=True
    )

    # The question has to come from the continued run, not from a restart that
    # happens to stop at the same tool: a restart asks about work already
    # approved once.
    assert answering_parent.subagents[0].runs[0]["mode"] == "resume"
    assert ToolInterruptHandler._is_sub_agent_interrupt(second) is True
    assert second["interrupt_ids"] == ["inner_bash"]
