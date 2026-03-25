# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Set

from pydantic import BaseModel

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptRequest,
)
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)

UserInput = Any


class InterruptDecision(BaseModel):
    """Base decision type for interrupt resume."""


class ApproveResult(InterruptDecision):
    """Decision to continue tool execution."""
    new_args: Optional[str] = None


class RejectResult(InterruptDecision):
    """Decision to reject tool execution."""
    tool_result: object = None
    tool_message: Optional[ToolMessage] = None


class InterruptResult(InterruptDecision):
    """Decision to interrupt and wait for user input."""
    request: InterruptRequest


class ToolSkipResult(InterruptDecision):
    tool_result: object
    tool_message: ToolMessage


class BaseInterruptRail(AgentRail):
    """Base rail for interrupt and resume handling.

    Provides:
    - Tool name registration
    - User input extraction from ctx.extra
    - Decision application (approve/reject/interrupt)
    - Nested agent interrupt handling

    Subclasses must implement resolve_interrupt() to define
    the specific interruption logic.
    """
    priority: int = 90

    def __init__(
            self,
            tool_names: Optional[Iterable[str]] = None,
    ):
        self._tool_names: Set[str] = set(tool_names or [])

    def approve(self, new_args: Optional[str] = None) -> ApproveResult:
        """Create an approve decision to continue execution."""
        return ApproveResult(new_args=new_args)

    def reject(self, tool_result: object = None) -> RejectResult:
        """Create a reject decision to skip tool execution."""
        return RejectResult(tool_result=tool_result)

    def interrupt(self, request: InterruptRequest) -> InterruptResult:
        """Create an interrupt decision to wait for user input."""
        return InterruptResult(request=request)

    def add_tool(self, tool_name: str) -> None:
        """Register a tool name to intercept."""
        self._tool_names.add(tool_name)

    def add_tools(self, tool_names: Iterable[str]) -> None:
        """Register multiple tool names to intercept."""
        self._tool_names.update(tool_names)

    def add_policy(self, tool_name: str, _policy: Optional[BaseModel] = None) -> None:
        """Deprecated alias of add_tool (policy ignored)."""
        self.add_tool(tool_name)

    def get_tools(self) -> Set[str]:
        """Get all registered tool names."""
        return set(self._tool_names)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Intercept tool call and handle resume/interrupt."""
        tool_call: Optional[ToolCall] = ctx.inputs.tool_call
        tool_name = ctx.inputs.tool_name

        if tool_name not in self._tool_names:
            return

        tool_call_id = self._resolve_tool_call_id(tool_call)
        user_input = self._get_user_input(ctx, tool_call_id)

        decision = await self.resolve_interrupt(ctx, tool_call, user_input)

        self._apply_decision(ctx, tool_call, tool_name, decision)

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
    ) -> InterruptDecision:
        """Override to handle resume and return decision.

        Args:
            ctx: Agent callback context
            tool_call: The tool call being intercepted
            user_input: User input from resume (None if first time)

        Returns:
            InterruptDecision: ApproveResult, RejectResult, or InterruptResponse
        """
        raise NotImplementedError

    def _apply_decision(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            tool_name: str,
            decision: InterruptDecision,
    ) -> None:
        if isinstance(decision, ApproveResult):
            if decision.new_args is not None:
                ctx.inputs.tool_args = decision.new_args
            return

        if isinstance(decision, RejectResult):
            self._skip_tool(ctx, tool_call, decision.tool_result, decision.tool_message)
            return

        if isinstance(decision, InterruptResult):
            self._raise_interrupt(tool_name, tool_call, decision.request)
            return

    def _raise_interrupt(
            self,
            tool_name: str,
            tool_call: Optional[ToolCall],
            request: InterruptRequest,
    ) -> None:
        raise AbortError(
            reason=f"Tool execution interrupted: {tool_name}",
            cause=ToolInterruptException(request=request, tool_call=tool_call)
        )

    def _skip_tool(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            tool_result: object,
            tool_message: Optional[ToolMessage] = None,
    ) -> None:
        tool_call_id = tool_call.id if tool_call is not None else ""
        msg = tool_message or ToolMessage(
            content=str(tool_result),
            tool_call_id=tool_call_id,
        )
        ctx.extra["_skip_tool"] = ToolSkipResult(
            tool_result=tool_result,
            tool_message=msg,
        )

    def _resolve_tool_call_id(self, tool_call: Optional[ToolCall]) -> str:
        if tool_call is None:
            return ""
        return tool_call.id if hasattr(tool_call, "id") else ""

    def _get_user_input(self, ctx: AgentCallbackContext, tool_call_id: str) -> Optional[Any]:
        """Get user input from ctx.extra (passed by handler) or session global state.
        
        For subagent internal rail: tool_call_id is inner_id, can directly match user_inputs key.
        No alias mapping needed - rail only cares if current tool_call has corresponding user input.
        """
        raw_input = ctx.extra.get(RESUME_USER_INPUT_KEY)
        if raw_input is None:
            return None

        if isinstance(raw_input, InteractiveInput):
            if tool_call_id in raw_input.user_inputs:
                return raw_input.user_inputs[tool_call_id]
            return None

        if isinstance(raw_input, Mapping):
            if tool_call_id in raw_input:
                return raw_input[tool_call_id]
            return raw_input

        return raw_input
