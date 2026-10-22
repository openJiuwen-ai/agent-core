# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptKind,
    InterruptRequest,
    ToolCallInterruptRequest,
    interrupt_kind_of,
)
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    INTERRUPTION_KEY,
    RESUME_INTERRUPT_ANSWERS_KEY,
    RESUME_START_ITERATION_KEY,
    RESUME_USER_INPUT_KEY,
    InterruptAnswer,
    ToolInterruptEntry,
    ToolInterruptionState,
    issue_interrupt_id,
    match_answers,
    pending_interrupts,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

_RESUME_USER_INPUT_KEY_METADATA = "resume_user_input_key"

# Kinds answered in the ``{"approved", "feedback", "auto_confirm"}`` shape: only
# their answers can switch on auto-confirm, so a host that sends that shape to
# another kind (a skill evolution approval expects ``{"action": ...}``) cannot.
# Undeclared kinds keep the behaviour every interrupt had before kinds existed.
_AUTO_CONFIRM_KINDS = frozenset({
    InterruptKind.PERMISSION,
    InterruptKind.CONFIRM,
    InterruptKind.TEAM_TOOL_APPROVAL,
    InterruptKind.UNDECLARED,
})

# tool_call_id (inner, or outer for a sub-agent or wrapper call) -> {interrupt_owner: answer}
_CallAnswers = Dict[str, Dict[str, InterruptAnswer]]
# inner tool_call_id -> (interrupt_owner, interrupt_id) of an interrupt a resume left unanswered.
_Unanswered = Dict[str, Tuple[str, str]]


@dataclass
class ResumeContext:
    """handle_resume 函数的上下文参数封装"""
    state: ToolInterruptionState
    user_input: Any
    ctx: AgentCallbackContext
    context: ModelContext
    session: Optional[Session] = None
    invoke_inputs: Optional[InvokeInputs] = None
    # Callback to execute tool calls. Passed from ReActAgent to avoid accessing protected member.
    # This fixes the Codex G.CLS.11 violation (accessing protected member outside class/subclass).
    execute_tool_call: Optional[Callable] = None


class ToolInterruptHandler:

    def __init__(self, agent: 'ReActAgent'):
        self._agent = agent
        self._key = INTERRUPTION_KEY

    def build_interrupt_state(
            self,
            results: list,
            tool_calls: list,
            ai_message: AssistantMessage,
            iteration: int,
            original_query: str = "",
    ) -> tuple[Optional[ToolInterruptionState], list]:

        interrupted_tools, payloads = self._collect_interrupts(results, tool_calls)

        if not interrupted_tools:
            return None, []

        state = ToolInterruptionState(
            ai_message=ai_message,
            iteration=iteration,
            interrupted_tools=interrupted_tools,
            original_query=original_query,
        )

        return state, payloads

    @staticmethod
    def _is_sub_agent_interrupt(result: Any) -> bool:
        """Check if result is a sub-agent interrupt dict."""
        if isinstance(result, tuple) and len(result) >= 1:
            tool_result = result[0]
        else:
            tool_result = result

        return (
                isinstance(tool_result, dict)
                and tool_result.get("result_type") == "interrupt"
                and "interrupt_ids" in tool_result
        )

    @staticmethod
    def _process_sub_agent_interrupt(
            tool_result: dict,
            tool_call,
            interrupt_entries: Dict[str, ToolInterruptEntry],
            id_mappings: Dict[str, str],
            sub_agent_outputs: list,
    ) -> None:
        """Process sub-agent interrupt result."""
        sub_ids = tool_result.get("interrupt_ids", [])
        sub_state = tool_result.get("state", [])

        interrupt_requests: Dict[str, InterruptRequest] = {}
        for output in sub_state:
            inner_id = output.payload.id
            request_value = output.payload.value
            if isinstance(request_value, InterruptRequest):
                interrupt_requests[inner_id] = request_value

        interrupt_entries[tool_call.id] = ToolInterruptEntry(
            tool_call=tool_call,
            interrupt_requests=interrupt_requests,
        )
        for inner_id in sub_ids:
            id_mappings[inner_id] = tool_call.id

        sub_agent_outputs.extend(sub_state)

    def save(self, state: ToolInterruptionState, session: Optional[Session]) -> None:
        """Save tool interruption state to session."""
        if session:
            session.update_state({self._key: state})

    def load(self, session: Optional[Session]) -> Optional[ToolInterruptionState]:
        """Load tool interruption state from session."""
        if session:
            return session.get_state(self._key)
        return None

    def clear(self, session: Optional[Session]) -> None:
        """Clear tool interruption state from session."""
        if session:
            session.update_state({self._key: None})

    @staticmethod
    def _handle_tool_interrupt_exception(
            tool_result: ToolInterruptException,
            tool_call: ToolCall,
            interrupted_tools: Dict[str, ToolInterruptEntry],
            payloads: list,
    ) -> None:
        # Approval identifies the nested target, but resume must replay the
        # model-visible outer call so wrappers can reestablish authorization.
        inner_tool_call = tool_result.tool_call or tool_call
        outer_id = tool_call.id
        inner_id = inner_tool_call.id

        interrupted_tools[outer_id] = ToolInterruptEntry(
            tool_call=tool_call,
            interrupt_requests={inner_id: tool_result.request},
        )

        payload = ToolCallInterruptRequest.from_tool_call(
            request=tool_result.request,
            tool_call=inner_tool_call,
        )
        payloads.append((inner_id, payload))

    @staticmethod
    def _handle_sub_agent_interrupt(
            tool_result: Any,
            tool_call: ToolCall,
            interrupted_tools: Dict[str, ToolInterruptEntry],
            payloads: list,
    ) -> None:
        outer_id = tool_call.id

        if isinstance(tool_result, tuple) and len(tool_result) >= 1:
            actual_tool_result = tool_result[0]
        else:
            actual_tool_result = tool_result

        sub_state = actual_tool_result.get("state", [])
        interrupt_requests: Dict[str, InterruptRequest] = {}

        for output in sub_state:
            if not isinstance(output, OutputSchema):
                continue
            payload = output.payload
            if not isinstance(payload, InteractionOutput):
                continue

            inner_id = payload.id
            payload_obj = payload.value

            if isinstance(payload_obj, ToolCallInterruptRequest):
                interrupt_requests[inner_id] = payload_obj
                payloads.append((inner_id, output))

        if outer_id not in interrupted_tools:
            interrupted_tools[outer_id] = ToolInterruptEntry(
                tool_call=tool_call,
                interrupt_requests=interrupt_requests,
                is_sub_agent=True,
            )

    def _collect_interrupts(
            self,
            results: list,
            tool_calls: list,
            unanswered: Optional[_Unanswered] = None,
    ) -> tuple[Dict[str, ToolInterruptEntry], list]:
        """Collect tool interrupts and sub-agent interrupts.

        Each tool interrupt gets its id here (``issue_interrupt_id``; ids the
        resume left ``unanswered`` are kept). A sub-agent's interrupts keep the
        ids its own handler gave them.

        Returns:
            interrupted_tools: outer_id -> ToolInterruptEntry
            payloads: List[Tuple[inner_id, payload]]
        """
        interrupted_tools: Dict[str, ToolInterruptEntry] = {}
        payloads: list = []

        for i, (tool_result, tool_msg) in enumerate(results):
            tool_call = tool_calls[i]

            if isinstance(tool_result, ToolInterruptException):
                issue_interrupt_id(tool_result.request, (tool_result.tool_call or tool_call).id, unanswered)
                self._handle_tool_interrupt_exception(tool_result, tool_call, interrupted_tools, payloads)
            elif self._is_sub_agent_interrupt(tool_result):
                self._handle_sub_agent_interrupt(tool_result, tool_call, interrupted_tools, payloads)

        return interrupted_tools, payloads

    @staticmethod
    def build_interrupt_result(
            payloads: list = None,
    ) -> Dict[str, object]:
        """Build interrupt result from payloads.

        Args:
            payloads: List[(inner_id, payload)], payload is OutputSchema
        """

        interrupt_ids = []
        state_outputs = []

        if payloads:
            for idx, (inner_id, payload) in enumerate(payloads):
                interrupt_ids.append(inner_id)
                if isinstance(payload, OutputSchema):
                    state_outputs.append(payload)
                else:
                    state_outputs.append(
                        OutputSchema(
                            type=INTERACTION,
                            index=idx,
                            payload=InteractionOutput(
                                id=inner_id,
                                value=payload
                            )
                        )
                    )

        return {
            "result_type": "interrupt",
            "state": state_outputs,
            "interrupt_ids": interrupt_ids,
        }

    async def commit_interrupt(
            self,
            state: ToolInterruptionState,
            context: ModelContext,
            session: Optional[Session],
            invoke_inputs: InvokeInputs,
            sub_agent_outputs: list = None,
    ) -> Dict[str, object]:
        """Persist tool interruption state and return interrupt dict."""
        await self._agent.context_engine.save_contexts(session)
        self.save(state, session)
        result = self.build_interrupt_result(sub_agent_outputs)
        invoke_inputs.result = result
        return result

    @staticmethod
    async def write_interrupt_to_stream(
            result: Dict[str, Any],
            session: Session,
    ) -> None:
        """Write tool interrupt result to session stream.

        Emit all OutputSchema from state list to stream.
        """
        schemas = result.get("state", [])
        if not isinstance(schemas, list):
            schemas = []

        for schema in schemas:
            await session.write_stream(schema)

    async def handle_resume(
            self,
            resume_ctx: ResumeContext,
    ) -> Optional[Dict[str, object]]:
        """Process resume step.

        Execute once per outer_id (tool/subagent), not per inner_id.
        Returns interrupt dict if still waiting, or None to continue ReAct loop.
        """
        state = resume_ctx.state
        user_input = resume_ctx.user_input
        ctx = resume_ctx.ctx
        context = resume_ctx.context
        session = resume_ctx.session
        invoke_inputs = resume_ctx.invoke_inputs

        resume_iteration = state.iteration
        logger.info(f"Resuming tool interrupt from iteration {resume_iteration + 1}")

        fresh_answers = self._attribute_answers(state, user_input)
        if isinstance(user_input, InteractiveInput):
            self._save_auto_confirm(fresh_answers, session)
        call_answers = self._call_answers(state, fresh_answers)
        unanswered = self._unanswered_interrupts(state, fresh_answers)

        resume_user_input_keys = self._resume_user_input_keys_for_state(state)
        ctx.extra.update({key: user_input for key in resume_user_input_keys})
        ctx.extra[RESUME_INTERRUPT_ANSWERS_KEY] = call_answers

        tools_to_execute = []
        for outer_id, entry in state.interrupted_tools.items():
            tc = copy.deepcopy(entry.tool_call)
            if entry.is_sub_agent:
                tc = self._build_sub_agent_resume_tool_call(tc, user_input)
            tools_to_execute.append(tc)

        try:
            if tools_to_execute:
                execute_tool_call = resume_ctx.execute_tool_call
                results = await execute_tool_call(ctx, tools_to_execute, session, context)
            else:
                results = []
        finally:
            for resume_user_input_key in resume_user_input_keys:
                ctx.extra.pop(resume_user_input_key, None)
            ctx.extra.pop(RESUME_INTERRUPT_ANSWERS_KEY, None)

        new_interrupted_tools, sub_agent_outputs = self._collect_interrupts(results, tools_to_execute, unanswered)
        self._carry_answers(new_interrupted_tools, call_answers)

        state.interrupted_tools = new_interrupted_tools

        if new_interrupted_tools:
            return await self.commit_interrupt(state, context, session, invoke_inputs, sub_agent_outputs)

        ctx.extra[RESUME_START_ITERATION_KEY] = resume_iteration + 1
        return None

    @staticmethod
    def _resume_user_input_keys_for_state(state: ToolInterruptionState) -> set[str]:
        """Return all ctx.extra keys that should expose the current resume input."""
        resume_keys: set[str] = set()
        needs_generic_key = False
        for entry in state.interrupted_tools.values():
            for request in entry.interrupt_requests.values():
                metadata = request.metadata if isinstance(request.metadata, dict) else {}
                resume_user_input_key = metadata.get(_RESUME_USER_INPUT_KEY_METADATA)
                if not isinstance(resume_user_input_key, str) or not resume_user_input_key:
                    needs_generic_key = True
                    continue

                resume_keys.add(resume_user_input_key)

        if needs_generic_key or not resume_keys:
            resume_keys.add(RESUME_USER_INPUT_KEY)
        return resume_keys

    @staticmethod
    def _attribute_answers(
            state: ToolInterruptionState,
            user_input: Any,
    ) -> Dict[str, InterruptAnswer]:
        """Translate this resume's input into answers keyed by interrupt id.

        This is the single translation point for the answers rails receive
        (``find_own_answer``); the raw input stays exposed under the resume
        user input keys for hosts' own rails.

        - ``InteractiveInput``: as :func:`match_answers` translates it
          (``answer(interrupt_id, v)``, or ``update(tool_call_id, v)`` for the
          interrupt pending on that call).
        - A dict whose keys include pending call ids answers only those calls,
          each with its own value.
        - Any other input (plain text, another dict) is offered to the
          interrupt pending on every call, as it always was.
        """
        if user_input is None:
            return {}
        pending = pending_interrupts(state)
        requests = {
            request.interrupt_id: request
            for entry in state.interrupted_tools.values()
            for request in entry.interrupt_requests.values()
        }
        if isinstance(user_input, InteractiveInput):
            matched = match_answers(user_input, pending)
            if matched.unmatched:
                logger.info("Ignoring answers that address no pending interrupt: %s", matched.unmatched)
            values = matched.answers
        elif isinstance(user_input, dict) and any(interrupt.tool_call_id in user_input for interrupt in pending):
            values = {
                interrupt.interrupt_id: user_input[interrupt.tool_call_id]
                for interrupt in pending
                if interrupt.tool_call_id in user_input
            }
        else:
            values = {interrupt.interrupt_id: user_input for interrupt in pending}
        return {
            interrupt_id: InterruptAnswer(
                owner=requests[interrupt_id].interrupt_owner,
                kind=interrupt_kind_of(requests[interrupt_id]),
                value=value,
                request=requests[interrupt_id],
            )
            for interrupt_id, value in values.items()
        }

    @staticmethod
    def _call_answers(state: ToolInterruptionState, fresh_answers: Dict[str, InterruptAnswer]) -> _CallAnswers:
        """Answers rails read during the replay: earlier rounds' answers, overridden by this round's.

        A sub-agent call exposes only the answers carried under its own
        (outer) id, for the rails gating the sub-agent call itself; the
        sub-agent attributes the resume input to its own interrupts.
        """
        call_answers: _CallAnswers = {}
        for entry in state.interrupted_tools.values():
            for call_id, answers in entry.carried_answers.items():
                call_answers.setdefault(call_id, {}).update(answers)
            if entry.is_sub_agent:
                continue
            for call_id, request in entry.interrupt_requests.items():
                answer = fresh_answers.get(request.interrupt_id)
                if answer is not None:
                    call_answers.setdefault(call_id, {})[answer.owner] = answer
        return call_answers

    @staticmethod
    def _unanswered_interrupts(state: ToolInterruptionState, fresh_answers: Dict[str, InterruptAnswer]) -> _Unanswered:
        """The interrupts this resume leaves unanswered, whose ids ``issue_interrupt_id`` keeps.

        Sub-agent entries are left out: the sub-agent issues its own ids.
        """
        unanswered: _Unanswered = {}
        for entry in state.interrupted_tools.values():
            if entry.is_sub_agent:
                continue
            for call_id, request in entry.interrupt_requests.items():
                if request.interrupt_id not in fresh_answers:
                    unanswered[call_id] = (request.interrupt_owner, request.interrupt_id)
        return unanswered

    @staticmethod
    def _carry_answers(interrupted_tools: Dict[str, ToolInterruptEntry], call_answers: _CallAnswers) -> None:
        """Keep the answers of calls that paused again for the next resume round.

        An answer whose rail is asking again on the same call is dropped:
        being asked again means it was not accepted. A call that now pauses
        inside a sub-agent keeps the answers given to its own (outer) id.
        """
        for outer_id, entry in interrupted_tools.items():
            asking: Dict[str, str] = {}
            call_ids = {outer_id}
            if not entry.is_sub_agent:
                asking = {call_id: request.interrupt_owner for call_id, request in entry.interrupt_requests.items()}
                call_ids.update(entry.interrupt_requests)
            for call_id in call_ids:
                kept = {
                    owner: answer.model_copy(update={"carried": True})
                    for owner, answer in call_answers.get(call_id, {}).items()
                    if owner != asking.get(call_id)
                }
                if kept:
                    entry.carried_answers[call_id] = kept

    @staticmethod
    def _save_auto_confirm(answers: Dict[str, InterruptAnswer], session: Optional[Session]) -> None:
        """Remember approvals answered with ``auto_confirm`` for the session."""
        if session is None:
            return
        config = session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
        for answer in answers.values():
            if answer.kind not in _AUTO_CONFIRM_KINDS:
                continue
            if isinstance(answer.value, dict) and answer.value.get("auto_confirm") and answer.request.auto_confirm_key:
                config[answer.request.auto_confirm_key] = True
        session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})

    @staticmethod
    def _build_sub_agent_resume_tool_call(tool_call: ToolCall, user_input: Any) -> ToolCall:
        """Build tool call for sub-agent resume with proper user input."""
        try:
            args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
        except (json.JSONDecodeError, TypeError):
            args = {}

        args["query"] = user_input

        tool_call.arguments = args
        return tool_call
