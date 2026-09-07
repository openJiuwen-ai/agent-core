# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end cover for a delegated pause and the answer that resumes it.

The other files in this change each pin one link of the round trip. This one
runs the whole of it against a subagent that exposes ``stream``, which is what
every ``DeepAgent`` subagent exposes and therefore what ``TaskTool`` actually
delegates to: ``_run_subagent_with_observable_stream`` prefers the streaming
path and only falls back to ``invoke`` for an adaptor that has no ``stream``.

That distinction is the whole point of the file. A pause never reaches
``TaskTool.invoke`` as a return value on the streaming path -- ``ReActAgent``
writes the envelope's ``state`` schemas to the stream and emits no terminal
answer -- so a test whose subagent only implements ``invoke`` exercises a path
no delegation takes and cannot see the envelope being dropped.

Every step that shapes the envelope is production code: the agent-side write
(``ReActAgent._write_invoke_result_to_stream``), the tool-side rebuild
(``_run_subagent_with_observable_stream``), the caller-side collection
(``ToolInterruptHandler.build_interrupt_state`` and ``build_interrupt_result``)
and the replay that carries the answer back
(``ToolInterruptHandler._build_sub_agent_resume_tool_call``). Only the stream
transport and the subagent's own turn are doubled.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import ToolCallInterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.subagent.task_tool import (
    TaskTool,
    _run_subagent_with_observable_stream,
)

try:  # pragma: no cover - exercised by whichever tree the test runs on
    from openjiuwen.harness.deep_agent import _sub_agent_card_for_session
except ImportError:  # pragma: no cover
    def _sub_agent_card_for_session(card: AgentCard, subsession_id: str) -> AgentCard:
        """Stand in for the derivation when the tree under test has none.

        Returning the spec's card unchanged is exactly what ``create_subagent``
        does without it, so the test measures the round trip on either tree
        rather than failing to import on one of them.
        """
        return card


_SPEC_CARD_NAME = "general-purpose"
_TASK = "audit the deployment scripts"
_QUESTION = "run `rm -rf build`?"
_INNER_CALL_ID = "inner_bash"
_OUTER_CALL_ID = "call_task_tool_1"
_ANSWER = {"approved": True, "auto_confirm": False, "feedback": ""}


def _inner_tool_call() -> ToolCall:
    return ToolCall(
        id=_INNER_CALL_ID,
        type="function",
        name="bash",
        arguments='{"command": "rm -rf build"}',
    )


def _sub_agent_interrupt_result() -> dict:
    """The envelope a paused ReAct turn returns, built by the real constructor."""
    request = ToolCallInterruptRequest(
        message=_QUESTION,
        tool_call_id=_INNER_CALL_ID,
        tool_name="bash",
        tool_args={"command": "rm -rf build"},
    )
    payload = OutputSchema(
        type=INTERACTION,
        index=0,
        payload=InteractionOutput(id=_INNER_CALL_ID, value=request),
    )
    return ToolInterruptHandler.build_interrupt_result([(_INNER_CALL_ID, payload)])


def _parked_state() -> ToolInterruptionState:
    """The state the subagent leaves in its own session when it pauses."""
    inner_call = _inner_tool_call()
    state = ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[inner_call]),
        iteration=3,
        original_query=_TASK,
    )
    state.interrupted_tools = {
        _INNER_CALL_ID: ToolInterruptEntry(tool_call=inner_call, is_sub_agent=False)
    }
    return state


class _StateStore:
    """Persisted agent state, namespaced by session id *and* agent id.

    Both halves have to survive the pause for the answer to find the parked
    state: the session id is what ``TaskTool`` derives per delegation, the
    agent id is what the subagent's card carries.
    """

    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], dict] = {}

    def load(self, session_id: str, agent_id: str) -> dict:
        return self.blobs.setdefault((session_id, agent_id), {})

    def namespaces(self) -> list[tuple[str, str]]:
        return sorted(self.blobs)


class _RecordingStream:
    """Stands in for the session's stream transport, keeping chunks verbatim.

    ``OutputSchema.payload`` is typed ``Any``, so the real writer hands the
    schema through unchanged; the schemas are what matter here, not the queue.
    """

    def __init__(self) -> None:
        self.chunks: list = []

    async def write_stream(self, data) -> None:
        self.chunks.append(data)


class _StreamingSubAgent:
    """A subagent shaped like ``DeepAgent``: it has ``stream``, so ``TaskTool`` uses it.

    Its turn either pauses -- parking state under its own namespace and
    returning an interrupt envelope -- or resumes the parked turn. Whichever it
    is, the result leaves through the real ``ReActAgent`` stream writer.
    """

    def __init__(self, card: AgentCard, store: _StateStore) -> None:
        self.card = card
        self._store = store
        self.runs: list[dict] = []

    async def _turn(self, inputs: dict) -> dict:
        blob = self._store.load(inputs["conversation_id"], self.card.id)
        if blob.get(INTERRUPTION_KEY) is not None:
            blob[INTERRUPTION_KEY] = None
            self.runs.append({"mode": "resume", "query": inputs["query"]})
            return {"output": "build directory removed", "result_type": "answer"}

        self.runs.append({"mode": "start", "query": inputs["query"]})
        blob[INTERRUPTION_KEY] = _parked_state()
        return _sub_agent_interrupt_result()

    async def invoke(self, inputs: dict) -> dict:
        return await self._turn(inputs)

    async def stream(self, inputs: dict):
        result = await self._turn(inputs)
        recorder = _RecordingStream()
        writer = ReActAgent.__new__(ReActAgent)
        writer._hitl_handler = ToolInterruptHandler(writer)
        await writer._write_invoke_result_to_stream(result, recorder)
        for chunk in recorder.chunks:
            yield chunk


class _ParentAgent:
    """A parent build. A second instance stands for the parent being rebuilt.

    The spec's card is minted fresh per build, exactly as the injected
    general-purpose spec's is, so a card id that is not derived from the
    sub-session cannot survive the rebuild that delivers the answer.
    """

    def __init__(self, store: _StateStore) -> None:
        self.deep_config = SimpleNamespace(model=None, kv_cache_affinity_config=None)
        self._store = store
        self._spec_card = AgentCard(name=_SPEC_CARD_NAME, description="gp")
        self.subagents: list[_StreamingSubAgent] = []

    def create_subagent(self, subagent_type: str, subsession_id: str, **_kwargs):
        subagent = _StreamingSubAgent(
            _sub_agent_card_for_session(self._spec_card, subsession_id), self._store
        )
        self.subagents.append(subagent)
        return subagent


def _tool(parent: _ParentAgent) -> TaskTool:
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent)


def _delegating_tool_call() -> ToolCall:
    return ToolCall(
        id=_OUTER_CALL_ID,
        type="function",
        name="task_tool",
        arguments=(
            f'{{"subagent_type": "{_SPEC_CARD_NAME}", "task_description": "{_TASK}"}}'
        ),
    )


@dataclass
class _RoundTrip:
    """What each stage of the round trip produced, so a test can assert on it.

    ``caller_interrupt`` and everything after it are ``None`` when the pause
    never reached the caller: there is then no delegating call to replay and no
    answer to route back, which is the defect this file exists to catch rather
    than a reason to stop the run.
    """

    asking_parent: _ParentAgent
    tool_result: Any
    caller_interrupt: Optional[dict] = None
    parked_state: Optional[ToolInterruptionState] = None
    answering_parent: Optional[_ParentAgent] = None
    final: Any = None


async def _round_trip(store: _StateStore) -> _RoundTrip:
    """Delegate, surface the pause to the caller, answer it, resume the subagent."""
    session = Session(session_id="parent_session")
    delegating_call = _delegating_tool_call()

    # 1. The parent delegates and the subagent stops to ask.
    asking_parent = _ParentAgent(store)
    tool_result = await _tool(asking_parent).invoke(
        {"subagent_type": _SPEC_CARD_NAME, "task_description": _TASK},
        session=session,
    )
    trip = _RoundTrip(asking_parent=asking_parent, tool_result=tool_result)

    # 2. The caller's ReAct loop collects that result and commits an interrupt.
    handler = ToolInterruptHandler(SimpleNamespace())
    parked_state, payloads = handler.build_interrupt_state(
        results=[(tool_result, None)],
        tool_calls=[delegating_call],
        ai_message=AssistantMessage(content="", tool_calls=[delegating_call]),
        iteration=1,
    )
    if parked_state is None:
        return trip
    trip.parked_state = parked_state
    trip.caller_interrupt = ToolInterruptHandler.build_interrupt_result(payloads)

    # 3. The user answers. The handler replays the delegating call it parked.
    replayed = ToolInterruptHandler._build_sub_agent_resume_tool_call(
        parked_state.interrupted_tools[_OUTER_CALL_ID].tool_call, _ANSWER
    )

    # 4. The answer arrives on a later turn, against a rebuilt parent.
    trip.answering_parent = _ParentAgent(store)
    trip.final = await _tool(trip.answering_parent).invoke(
        replayed.arguments, session=session
    )
    return trip


@pytest.mark.asyncio
async def test_streamed_pause_reaches_the_caller() -> None:
    """Out: the question the subagent raised is the one the caller surfaces."""
    store = _StateStore()
    trip = await _round_trip(store)

    assert trip.caller_interrupt is not None, (
        "the caller collected no interrupt from the delegation: the pause was "
        f"flattened into an ordinary result on the way out ({trip.tool_result!r})"
    )
    assert ToolInterruptHandler._is_sub_agent_interrupt(trip.caller_interrupt) is True
    assert trip.caller_interrupt["interrupt_ids"] == [_INNER_CALL_ID]

    (surfaced,) = trip.caller_interrupt["state"]
    assert surfaced.payload.value.message == _QUESTION


@pytest.mark.asyncio
async def test_caller_parks_the_delegating_call_as_a_sub_agent_interrupt() -> None:
    """The caller records which of its own tool calls is waiting, and for what."""
    store = _StateStore()
    trip = await _round_trip(store)

    assert trip.parked_state is not None, (
        "no interruption state was parked for the delegating call, so nothing "
        "would be replayed when the user answers"
    )
    entry = trip.parked_state.interrupted_tools[_OUTER_CALL_ID]
    assert entry.is_sub_agent is True
    assert entry.tool_call.name == "task_tool"
    assert list(entry.interrupt_requests) == [_INNER_CALL_ID]


@pytest.mark.asyncio
async def test_the_answer_reaches_the_sub_agent_that_asked() -> None:
    """Back: the resumed run is the parked one, and it carries the answer."""
    store = _StateStore()
    trip = await _round_trip(store)

    assert trip.answering_parent is not None, (
        "the pause never reached the caller, so no answer was routed back into "
        "the subagent that asked"
    )
    assert len(store.namespaces()) == 1, (
        "the replay addressed a different (session id, agent id) namespace than "
        f"the pause did: {store.namespaces()}"
    )
    assert (
        trip.asking_parent.subagents[0].card.id
        == trip.answering_parent.subagents[0].card.id
    )

    resumed = trip.answering_parent.subagents[0].runs[0]
    assert resumed["mode"] == "resume", (
        "the replay started a fresh run instead of continuing the paused one"
    )
    assert resumed["query"] == _ANSWER


@pytest.mark.asyncio
async def test_the_resumed_delegation_reports_the_completed_work() -> None:
    """The round trip ends as an ordinary success once the answer lands."""
    store = _StateStore()
    trip = await _round_trip(store)

    assert trip.final is not None, "the round trip stopped before the resume"
    assert trip.final.success is True
    assert trip.final.data["output"] == "build directory removed"


@pytest.mark.asyncio
async def test_the_pause_leaves_the_sub_agent_state_parked_until_answered() -> None:
    """Nothing consumes the parked state before the answer arrives."""
    store = _StateStore()
    session = Session(session_id="parent_session")
    parent = _ParentAgent(store)

    await _tool(parent).invoke(
        {"subagent_type": _SPEC_CARD_NAME, "task_description": _TASK},
        session=session,
    )

    (namespace,) = store.namespaces()
    assert store.blobs[namespace][INTERRUPTION_KEY] is not None


class _ChunkSubAgent:
    """Subagent whose stream yields exactly the chunks it is given."""

    def __init__(self, chunks: list) -> None:
        self.card = AgentCard(name=_SPEC_CARD_NAME, description="gp")
        self._chunks = chunks

    async def invoke(self, inputs: dict) -> dict:
        raise AssertionError("invoke must not be used when stream is available")

    async def stream(self, inputs: dict):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_a_streamed_answer_is_still_an_answer() -> None:
    """The completion path is unchanged: an answer chunk still wins."""
    subagent = _ChunkSubAgent(
        [
            OutputSchema(type="llm_output", index=0, payload={"content": "working"}),
            OutputSchema(
                type="answer",
                index=1,
                payload={"output": "done", "result_type": "answer"},
            ),
        ]
    )

    result = await _run_subagent_with_observable_stream(
        subagent, {"query": _TASK, "conversation_id": "sub"}
    )

    assert result == {"output": "done", "result_type": "answer"}
    assert ToolInterruptHandler._is_sub_agent_interrupt(result) is False


@pytest.mark.asyncio
async def test_an_interaction_that_is_not_an_interrupt_request_is_not_a_pause() -> None:
    """A workflow's own interaction chunk keeps the behaviour it has today.

    Only the HITL interrupt protocol is rebuilt here. An interaction carrying
    something else belongs to the workflow interrupt path, which returns a
    differently shaped envelope and is not part of this round trip.
    """
    subagent = _ChunkSubAgent(
        [
            OutputSchema(
                type=INTERACTION,
                index=0,
                payload=InteractionOutput(id="node_1", value={"form": "fill me in"}),
            )
        ]
    )

    result = await _run_subagent_with_observable_stream(
        subagent, {"query": _TASK, "conversation_id": "sub"}
    )

    assert ToolInterruptHandler._is_sub_agent_interrupt(result) is False
