# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.utils.pydantic_compat import RestoreFieldDefaultsOnUnpickle
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptRequest,
    interrupt_kind_of,
    new_interrupt_id,
)

if TYPE_CHECKING:
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

INTERRUPTION_KEY = "__react_agent_interruption__"
RESUME_USER_INPUT_KEY = "_resume_user_input"
INTERRUPT_AUTO_CONFIRM_KEY = "__interrupt_auto_confirm__"
RESUME_START_ITERATION_KEY = "_resume_start_iteration"
# ctx.extra key holding ``{tool_call_id: {interrupt_owner: InterruptAnswer}}`` during a resume.
RESUME_INTERRUPT_ANSWERS_KEY = "_resume_interrupt_answers"


class InterruptAnswer(BaseModel):
    """One answer, attributed by the interrupt handler to the interrupt it answers.

    Attributes:
        owner: ``interrupt_owner`` of the answered interrupt; only that rail
            receives the answer.
        kind: ``interrupt_kind`` of the answered interrupt.
        value: The answer as the host sent it.
        request: The answered request.
        carried: True when the answer was given, and acted on, in an earlier
            resume round of the same, still paused call.
    """

    owner: str
    kind: str
    value: Any
    request: InterruptRequest
    carried: bool = False


class BaseInterruptionState(BaseModel):
    """Common interruption state fields."""
    ai_message: AssistantMessage
    iteration: int
    original_query: str = ""


class ToolInterruptEntry(RestoreFieldDefaultsOnUnpickle, BaseModel):
    tool_call: ToolCall
    interrupt_requests: Dict[str, InterruptRequest] = Field(default_factory=dict)
    is_sub_agent: bool = False
    # inner tool_call_id -> {interrupt_owner: answer} already given for this
    # still-paused call; they stay visible to their rails in later resume rounds.
    carried_answers: Dict[str, Dict[str, InterruptAnswer]] = Field(default_factory=dict)

    def __setstate__(self, state: Dict[Any, Any]) -> None:
        super().__setstate__(state)
        # A request persisted before interrupt ids existed gets one derived from
        # its call, so every loader (and a sub-agent's copy of it) agrees on it.
        for call_id, request in self.interrupt_requests.items():
            if not request.interrupt_id:
                request.interrupt_id = f"intr_{call_id}"


class ToolInterruptionState(BaseInterruptionState):
    """Tool interruption state for resume support.
    """
    interrupted_tools: Dict[str, ToolInterruptEntry] = Field(default_factory=dict)


def issue_interrupt_id(
    request: InterruptRequest,
    tool_call_id: str,
    unanswered: Optional[Mapping[str, Tuple[str, str]]],
) -> None:
    """Give an interrupt raised on ``tool_call_id`` its id (in place).

    The interrupt handler calls this for every interrupt it collects from a
    paused call. ``unanswered`` maps each call to the ``(interrupt_owner,
    interrupt_id)`` of the interrupt the current resume left unanswered on it.
    An interrupt raised again only because the call's previous interrupt from
    the same owner was left unanswered keeps that id, so a host answering
    parallel calls in batches keeps using the ids it has, even if the rail now
    asks a different question. Every other interrupt gets a new id.
    """
    previous = (unanswered or {}).get(tool_call_id)
    if previous is not None and previous[0] == request.interrupt_owner:
        request.interrupt_id = previous[1]
    else:
        request.interrupt_id = new_interrupt_id()


class PendingInterrupt(NamedTuple):
    """A paused tool-call interrupt as a host addresses it."""

    interrupt_id: str
    tool_call_id: str
    kind: str


def pending_interrupts(state: Optional[ToolInterruptionState]) -> List[PendingInterrupt]:
    """List the interrupts pending in a session's ``ToolInterruptionState`` (including those inside sub-agents).

    Anything else, ``None`` included, has no pending interrupts.
    """
    if not isinstance(state, ToolInterruptionState):
        return []
    return [
        PendingInterrupt(request.interrupt_id, call_id, interrupt_kind_of(request))
        for entry in state.interrupted_tools.values()
        for call_id, request in entry.interrupt_requests.items()
    ]


class MatchedAnswers(NamedTuple):
    """What a resume input answers among the pending interrupts.

    Attributes:
        answers: ``{interrupt_id: value}`` for every pending interrupt the
            input answers.
        unmatched: Keys of the input (interrupt ids or tool call ids) that
            address no pending interrupt, e.g. stale answers.
    """

    answers: Dict[str, Any]
    unmatched: List[str]


def match_answers(user_input: "InteractiveInput", pending: Iterable[PendingInterrupt]) -> MatchedAnswers:
    """Translate a resume input into answers keyed by interrupt id.

    ``InteractiveInput.answer(interrupt_id, value)`` answers that interrupt;
    ``InteractiveInput.update(tool_call_id, value)`` answers the interrupt
    pending on that call (an answer by interrupt id wins). The interrupt
    handler resumes with exactly this translation, so hosts can use it to
    check an input before sending it.
    """
    pending = list(pending)
    by_interrupt_id = user_input.interrupt_answers
    by_call_id = user_input.user_inputs
    answers: Dict[str, Any] = {}
    for interrupt in pending:
        if interrupt.interrupt_id in by_interrupt_id:
            answers[interrupt.interrupt_id] = by_interrupt_id[interrupt.interrupt_id]
        elif interrupt.tool_call_id in by_call_id:
            answers[interrupt.interrupt_id] = by_call_id[interrupt.tool_call_id]
    call_ids = {interrupt.tool_call_id for interrupt in pending}
    interrupt_ids = {interrupt.interrupt_id for interrupt in pending}
    unmatched = [key for key in by_call_id if key not in call_ids]
    unmatched += [key for key in by_interrupt_id if key not in interrupt_ids]
    return MatchedAnswers(answers, unmatched)
