# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interrupt ids, answer attribution and compatibility of ToolInterruptHandler.

The resume rounds run real interrupt rails through the handler's public
``build_interrupt_state`` / ``handle_resume`` path; the executor below plays
the ability manager's part of firing ``before_tool_call`` per call.
"""

from __future__ import annotations

import pickle
from typing import Any, List, Optional

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt import PendingInterrupt, match_answers, pending_interrupts
from openjiuwen.core.single_agent.interrupt.handler import ResumeContext, ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptKind,
    InterruptRequest,
    ToolCallInterruptRequest,
    interrupt_kind_of,
)
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry, ToolInterruptionState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

APPROVE = {"approved": True, "feedback": "", "auto_confirm": False}


def _call(call_id: str, name: str = "demo_tool") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


class RecordingRail(BaseInterruptRail):
    """Undeclared rail: interrupts until it receives any answer, records what it received."""

    priority = 40

    def __init__(self, tool_names):
        super().__init__(tool_names=tool_names)
        self.seen: List[Any] = []

    async def resolve_interrupt(self, ctx, tool_call, user_input, auto_confirm_config=None):
        self.seen.append(user_input)
        if user_input is None:
            return self.interrupt(InterruptRequest(message="recording rail"))
        return self.approve()


class _Agent:
    class context_engine:  # noqa: N801 - mimics the attribute the handler reads
        @staticmethod
        async def save_contexts(_session):
            return None


class Session:
    def __init__(self) -> None:
        self.state: dict = {}

    def get_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_state(self, values: dict) -> None:
        self.state.update(values)


class Rounds:
    """Pause and resume calls guarded by real rails, as the agent loop does."""

    def __init__(self, *rails: BaseInterruptRail, session: Optional[Session] = None):
        self.rails = sorted(rails, key=lambda rail: -rail.priority)
        self.handler = ToolInterruptHandler(agent=_Agent())
        self.session = session
        self.ran: List[str] = []

    async def _execute(self, extra: dict, calls: List[ToolCall]) -> list:
        results = []
        for call in calls:
            ctx = AgentCallbackContext(
                agent=None,
                inputs=ToolCallInputs(tool_call=call, tool_name=call.name, tool_args=call.arguments),
                session=self.session,
                extra=dict(extra),
            )
            try:
                for rail in self.rails:
                    await rail.before_tool_call(ctx)
            except AbortError as error:
                results.append((error.cause, None))
                continue
            if ctx.extra.get("_skip_tool"):
                results.append((ctx.inputs.tool_result, ctx.inputs.tool_msg))
                continue
            self.ran.append(call.id)
            results.append(("ran", ToolMessage(content="ran", tool_call_id=call.id)))
        return results

    async def start(self, *calls: ToolCall):
        results = await self._execute({}, list(calls))
        state, payloads = self.handler.build_interrupt_state(
            results, list(calls), AssistantMessage(content="", tool_calls=list(calls)), iteration=0
        )
        return state, {call_id: payload for call_id, payload in payloads}

    async def resume(self, state: ToolInterruptionState, user_input: Any) -> dict:
        ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())

        async def _execute(ctx_arg, calls, _session, _context):
            return await self._execute(ctx_arg.extra, calls)

        result = await self.handler.handle_resume(ResumeContext(
            state=state,
            user_input=user_input,
            ctx=ctx,
            context=None,
            session=self.session,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute,
        ))
        if result is None:
            return {}
        return {item.payload.id: item.payload.value for item in result["state"]}


def _answer(interrupt_id: str, value: Any) -> InteractiveInput:
    user_input = InteractiveInput()
    user_input.answer(interrupt_id, value)
    return user_input


def _update(call_id: str, value: Any) -> InteractiveInput:
    user_input = InteractiveInput()
    user_input.update(call_id, value)
    return user_input


def test_request_identity_fields_and_payloads():
    request = InterruptRequest(message="x")
    assert (request.interrupt_id, request.interrupt_kind, request.interrupt_owner) == ("", "", "")
    payload = ToolCallInterruptRequest.from_tool_call(
        InterruptRequest(message="x", interrupt_kind="confirm", interrupt_id="intr_1", interrupt_owner="o"),
        _call("c1"),
    )
    assert (payload.interrupt_id, payload.interrupt_kind, payload.tool_call_id) == ("intr_1", "confirm", "c1")
    assert "kind" not in payload.model_dump(), "the kind lives under interrupt_kind, apart from host payload keys"
    assert interrupt_kind_of(InterruptRequest(metadata={"interrupt_kind": "skill_evolution_approval"})) == (
        "skill_evolution_approval"
    )


def test_answer_requires_an_interrupt_id():
    with pytest.raises(BaseError):
        InteractiveInput().answer("", {"approved": True})


@pytest.mark.asyncio
async def test_each_interrupt_gets_a_unique_id_and_a_later_one_on_the_call_a_new_id():
    first, second = ConfirmInterruptRail(tool_names=["demo_tool"]), RecordingRail(["demo_tool"])
    rounds = Rounds(first, second)
    state, payloads = await rounds.start(_call("call_a"), _call("call_b"))
    ids = {payloads["call_a"].interrupt_id, payloads["call_b"].interrupt_id}
    assert len(ids) == 2 and all(i.startswith("intr_") for i in ids)
    assert payloads["call_a"].interrupt_kind == InterruptKind.CONFIRM

    answers = InteractiveInput()
    answers.answer(payloads["call_a"].interrupt_id, APPROVE)
    answers.answer(payloads["call_b"].interrupt_id, APPROVE)
    paused = await rounds.resume(state, answers)

    assert set(paused) == {"call_a", "call_b"}
    assert {paused["call_a"].interrupt_id, paused["call_b"].interrupt_id}.isdisjoint(ids)
    assert second.seen == [None, None], "the second rail must not receive the confirm approvals"


@pytest.mark.asyncio
async def test_update_by_call_id_answers_the_interrupt_pending_on_that_call():
    confirm, recorder = ConfirmInterruptRail(tool_names=["demo_tool"]), RecordingRail(["demo_tool"])
    rounds = Rounds(confirm, recorder)
    state, _ = await rounds.start(_call("call_a"))

    paused = await rounds.resume(state, _update("call_a", APPROVE))
    assert recorder.seen == [None]
    assert paused["call_a"].interrupt_owner == recorder.interrupt_owner
    assert await rounds.resume(state, _update("call_a", "client done")) == {}
    assert recorder.seen == [None, "client done"]
    assert rounds.ran == ["call_a"]


@pytest.mark.asyncio
async def test_answer_to_an_unknown_interrupt_id_is_ignored():
    rounds = Rounds(ConfirmInterruptRail(tool_names=["demo_tool"]))
    state, payloads = await rounds.start(_call("call_a"))

    paused = await rounds.resume(state, _answer("intr_stale", APPROVE))

    assert set(paused) == {"call_a"}
    assert paused["call_a"].interrupt_id == payloads["call_a"].interrupt_id
    assert rounds.ran == []


@pytest.mark.asyncio
async def test_rejected_answer_is_asked_again_with_a_new_id():
    rounds = Rounds(ConfirmInterruptRail(tool_names=["demo_tool"]))
    state, payloads = await rounds.start(_call("call_a"))

    paused = await rounds.resume(state, _answer(payloads["call_a"].interrupt_id, {"not": "an approval"}))

    assert paused["call_a"].interrupt_id != payloads["call_a"].interrupt_id
    assert await rounds.resume(state, _answer(paused["call_a"].interrupt_id, APPROVE)) == {}
    assert rounds.ran == ["call_a"]


@pytest.mark.asyncio
async def test_plain_text_resume_goes_to_the_interrupt_pending_on_each_call():
    confirm, recorder = ConfirmInterruptRail(tool_names=["demo_tool"]), RecordingRail(["demo_tool"])
    rounds = Rounds(confirm, recorder)
    state, payloads = await rounds.start(_call("call_a"))
    await rounds.resume(state, _answer(payloads["call_a"].interrupt_id, APPROVE))

    assert await rounds.resume(state, "plain text") == {}
    assert recorder.seen == [None, "plain text"]


@pytest.mark.asyncio
async def test_plain_dict_resume_keyed_by_call_id_gives_each_call_its_value():
    recorder = RecordingRail(["demo_tool"])
    rounds = Rounds(recorder)
    state, _ = await rounds.start(_call("call_a"), _call("call_b"))

    assert await rounds.resume(state, {"call_a": "for a", "call_b": "for b"}) == {}
    assert recorder.seen == [None, None, "for a", "for b"]


@pytest.mark.asyncio
async def test_plain_dict_keyed_by_some_call_ids_answers_only_those_calls():
    recorder = RecordingRail(["demo_tool"])
    rounds = Rounds(recorder)
    state, payloads = await rounds.start(_call("call_a"), _call("call_b"))

    paused = await rounds.resume(state, {"call_a": "for a"})

    assert set(paused) == {"call_b"}
    assert paused["call_b"].interrupt_id == payloads["call_b"].interrupt_id
    assert recorder.seen == [None, None, "for a", None]


@pytest.mark.asyncio
async def test_auto_confirm_is_saved_only_from_confirmation_answers():
    class AskRail(RecordingRail):
        interrupt_kind = InterruptKind.ASK_USER

        async def resolve_interrupt(self, ctx, tool_call, user_input, auto_confirm_config=None):
            if user_input is None:
                return self.interrupt(InterruptRequest(message="?", auto_confirm_key="ask_key"))
            return self.reject(tool_result="answered")

    session = Session()
    rounds = Rounds(ConfirmInterruptRail(tool_names=["confirm_tool"]), AskRail(["ask_tool"]), session=session)
    state, payloads = await rounds.start(_call("call_a", "confirm_tool"), _call("call_b", "ask_tool"))
    answers = InteractiveInput()
    answers.answer(payloads["call_a"].interrupt_id, {"approved": True, "auto_confirm": True})
    answers.answer(payloads["call_b"].interrupt_id, {"answer": "x", "auto_confirm": True})

    await rounds.resume(state, answers)

    assert session.state["__interrupt_auto_confirm__"] == {"confirm_tool": True}


def _without(model, *names: str):
    """Simulate an instance pickled before ``names`` were added to its class."""
    for name in names:
        model.__dict__.pop(name, None)
    return pickle.loads(pickle.dumps(model))


def test_objects_pickled_before_the_new_fields_unpickle_with_defaults():
    request = _without(
        InterruptRequest(message="old", interrupt_kind="confirm", interrupt_id="intr_1", interrupt_owner="o"),
        "interrupt_kind", "interrupt_id", "interrupt_owner",
    )
    assert (request.interrupt_kind, request.interrupt_id, request.interrupt_owner, request.message) == (
        "", "", "", "old"
    )

    entry = _without(
        ToolInterruptEntry(tool_call=_call("c"), interrupt_requests={"c": InterruptRequest()}), "carried_answers"
    )
    assert entry.carried_answers == {}

    user_input = InteractiveInput()
    user_input.update("c", {"approved": True})
    user_input = _without(user_input, "interrupt_answers")
    assert user_input.interrupt_answers == {}
    user_input.answer("intr_1", "x")
    assert user_input.interrupt_answers == {"intr_1": "x"}


@pytest.mark.asyncio
async def test_session_paused_before_the_upgrade_resumes():
    """A state pickled by an older version: no ids, kinds, owners or carried answers.

    Loading it gives the pending request an id derived from its call, so an
    answer by call id (an old client) reaches it like any other answer. Who
    raised it is unknown, so its rail asks once more before the call runs."""
    call = _call("call_old")
    old_request = InterruptRequest(message="Approve?")
    for name in ("interrupt_kind", "interrupt_id", "interrupt_owner"):
        old_request.__dict__.pop(name)
    entry = ToolInterruptEntry(tool_call=call, interrupt_requests={"call_old": old_request})
    entry.__dict__.pop("carried_answers")
    old_state = ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[call]),
        iteration=0,
        interrupted_tools={"call_old": entry},
    )
    old_input = InteractiveInput()
    old_input.update("call_old", APPROVE)
    old_input.__dict__.pop("interrupt_answers")

    loaded = pickle.loads(pickle.dumps(old_state))
    assert pending_interrupts(loaded) == [PendingInterrupt("intr_call_old", "call_old", "")]
    assert pending_interrupts(pickle.loads(pickle.dumps(old_state))) == pending_interrupts(loaded)

    confirm = ConfirmInterruptRail(tool_names=["demo_tool"])
    rounds = Rounds(confirm)
    paused = await rounds.resume(loaded, pickle.loads(pickle.dumps(old_input)))
    assert paused["call_old"].interrupt_owner == confirm.interrupt_owner
    assert rounds.ran == []

    assert await rounds.resume(loaded, _answer(paused["call_old"].interrupt_id, APPROVE)) == {}
    assert rounds.ran == ["call_old"]


@pytest.mark.asyncio
async def test_pending_interrupts_and_matched_answers():
    rounds = Rounds(ConfirmInterruptRail(tool_names=["demo_tool"]))
    state, payloads = await rounds.start(_call("call_a"), _call("call_b"), _call("call_c"))
    ids = {call_id: payload.interrupt_id for call_id, payload in payloads.items()}
    pending = pending_interrupts(state)
    assert pending == [PendingInterrupt(ids[c], c, InterruptKind.CONFIRM) for c in ("call_a", "call_b", "call_c")]

    user_input = InteractiveInput()
    user_input.answer(ids["call_a"], APPROVE)
    user_input.update("call_c", {"approved": False})

    matched = match_answers(user_input, pending)
    assert matched.answers == {ids["call_a"]: APPROVE, ids["call_c"]: {"approved": False}}
    assert matched.unmatched == []
    user_input.answer("intr_gone", APPROVE)
    user_input.update("call_gone", APPROVE)
    assert match_answers(user_input, pending).unmatched == ["call_gone", "intr_gone"]


def test_interactive_input_without_interrupt_answers_serializes_as_before():
    legacy = InteractiveInput("hello")
    assert legacy.model_dump() == {"user_inputs": {}, "raw_inputs": "hello"}
    assert "interrupt_answers" not in legacy.model_dump_json()

    answered = InteractiveInput()
    answered.update("call_a", APPROVE)
    answered.answer("intr_1", {"content": "done"})
    dumped = answered.model_dump()
    assert dumped["interrupt_answers"] == {"intr_1": {"content": "done"}}
    restored = InteractiveInput.model_validate(dumped)
    assert restored.interrupt_answers == answered.interrupt_answers
    assert restored.user_inputs == answered.user_inputs
    assert InteractiveInput.model_validate_json(answered.model_dump_json()).interrupt_answers == {
        "intr_1": {"content": "done"}
    }
