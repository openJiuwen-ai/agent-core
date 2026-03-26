# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

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
    InterruptRequest,
    ToolCallInterruptRequest,
)
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.interrupt.state import (
    ToolInterruptEntry,
    ToolInterruptionState,
    RESUME_START_ITERATION_KEY, INTERRUPT_AUTO_CONFIRM_KEY,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs


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

        interrupted_tools, payloads, tool_name_mapping = self._collect_interrupts(
            results, tool_calls
        )

        if not interrupted_tools:
            return None, []

        state = ToolInterruptionState(
            ai_message=ai_message,
            iteration=iteration,
            interrupted_tools=interrupted_tools,
            original_query=original_query,
            tool_name_mapping=tool_name_mapping,
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
            tool_name_mapping: Dict[str, str],
    ) -> None:
        tc = tool_result.tool_call or tool_call
        outer_id = tc.id
        inner_id = outer_id

        interrupted_tools[outer_id] = ToolInterruptEntry(
            tool_call=tc,
            interrupt_requests={inner_id: tool_result.request},
        )

        payload = ToolCallInterruptRequest.from_tool_call(
            request=tool_result.request,
            tool_call=tc,
        )
        payloads.append((inner_id, payload))

        tool_name_mapping[inner_id] = tc.name

    @staticmethod
    def _handle_sub_agent_interrupt(
            tool_result: Any,
            tool_call: ToolCall,
            interrupted_tools: Dict[str, ToolInterruptEntry],
            payloads: list,
            tool_name_mapping: Dict[str, str],
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
                if payload_obj.tool_name:
                    tool_name_mapping[inner_id] = payload_obj.tool_name

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
    ) -> tuple[Dict[str, ToolInterruptEntry], list, Dict[str, str]]:
        """Collect tool interrupts and sub-agent interrupts.

        Returns:
            interrupted_tools: outer_id -> ToolInterruptEntry
            payloads: List[Tuple[inner_id, payload]]
            tool_name_mapping: inner_id -> tool_name
        """
        interrupted_tools: Dict[str, ToolInterruptEntry] = {}
        payloads: list = []
        tool_name_mapping: Dict[str, str] = {}

        for i, (tool_result, tool_msg) in enumerate(results):
            tool_call = tool_calls[i]

            if isinstance(tool_result, ToolInterruptException):
                self._handle_tool_interrupt_exception(
                    tool_result, tool_call, interrupted_tools, payloads, tool_name_mapping
                )
            elif self._is_sub_agent_interrupt(tool_result):
                self._handle_sub_agent_interrupt(
                    tool_result, tool_call, interrupted_tools, payloads, tool_name_mapping
                )

        return interrupted_tools, payloads, tool_name_mapping

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

        ctx.extra[RESUME_USER_INPUT_KEY] = user_input

        tools_to_execute = []
        for outer_id, entry in state.interrupted_tools.items():
            tc = copy.deepcopy(entry.tool_call)
            if entry.is_sub_agent:
                tc = self._build_sub_agent_resume_tool_call(tc, user_input, session, state)
            tools_to_execute.append(tc)

        if tools_to_execute:
            execute_tool_call = resume_ctx.execute_tool_call
            results = await execute_tool_call(ctx, tools_to_execute, session, context)
        else:
            results = []

        ctx.extra.pop(RESUME_USER_INPUT_KEY, None)

        new_interrupted_tools, sub_agent_outputs, _ = self._collect_interrupts(
            results, tools_to_execute
        )

        state.interrupted_tools = new_interrupted_tools

        if new_interrupted_tools:
            return await self.commit_interrupt(state, context, session, invoke_inputs, sub_agent_outputs)

        ctx.extra[RESUME_START_ITERATION_KEY] = resume_iteration + 1
        return None

    @staticmethod
    def _build_sub_agent_resume_tool_call(
            tool_call: ToolCall,
            user_input: UserInput,
            session: Optional[Session] = None,
            state: Optional[ToolInterruptionState] = None,
    ) -> ToolCall:
        """Build tool call for sub-agent resume with proper user input.
        
        Also propagates auto_confirm state to sub-agent for multi-layer scenarios.
        """
        try:
            args = json.loads(tool_call.arguments) if isinstance(tool_call.arguments, str) else tool_call.arguments
        except (json.JSONDecodeError, TypeError):
            args = {}

        args["query"] = user_input

        if isinstance(user_input, InteractiveInput) and state is not None:
            for inner_id, tool_name in state.tool_name_mapping.items():
                if inner_id in user_input.user_inputs:
                    user_value = user_input.user_inputs[inner_id]
                    if isinstance(user_value, dict) and user_value.get("auto_confirm"):
                        if session is not None:
                            session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: {tool_name: True}})
                        break

        tool_call.arguments = args
        return tool_call
