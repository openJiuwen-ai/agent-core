# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Base classes for harness security rails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    RESUME_USER_INPUT_KEY,
)
from openjiuwen.core.single_agent.rail.base import (
    EVENT_METHOD_MAP,
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)


@dataclass
class SecurityCheckContext:
    """Context passed to custom security checks."""

    callback_ctx: AgentCallbackContext
    event: AgentCallbackEvent
    user_input: Any | None = None
    auto_confirm_config: dict[str, Any] | None = None
    subject_id: str = ""


class SecurityDecision:
    """Base class for security decisions."""


@dataclass
class SecurityAllow(SecurityDecision):
    """Allow the guarded operation to continue."""

    new_args: Optional[str] = None


@dataclass
class SecurityReject(SecurityDecision):
    """Reject the guarded operation."""

    message: str = ""
    result: Any = None
    tool_message: Optional[ToolMessage] = None


@dataclass
class SecurityInterrupt(SecurityDecision):
    """Interrupt the guarded operation and wait for user input."""

    request: InterruptRequest
    subject_id: str = ""


class BaseSecurityRail(AgentRail):
    """Base rail for security checks.

    Subclasses implement ``run_security_check`` and return a SecurityDecision.
    This base class handles hook registration, resume input extraction, session
    auto-confirm state lookup, and applying decisions for supported events.
    """

    priority: int = 90
    supported_events: Set[AgentCallbackEvent] = set()

    def __init__(self, tool_names: Optional[Iterable[str]] = None) -> None:
        self._tool_names: Set[str] = set(tool_names or [])

    def allow(self, new_args: Optional[str] = None) -> SecurityAllow:
        return SecurityAllow(new_args=new_args)

    def approve(self, new_args: Optional[str] = None) -> SecurityAllow:
        """Compatibility helper for rails migrated from interrupt rails."""
        return self.allow(new_args=new_args)

    def reject(
        self,
        message: str = "",
        *,
        result: Any = None,
        tool_result: Any = None,
        tool_message: Optional[ToolMessage] = None,
    ) -> SecurityReject:
        if result is None and tool_result is not None:
            result = tool_result
        if not message and result is not None:
            message = str(result)
        return SecurityReject(message=message, result=result, tool_message=tool_message)

    def interrupt(
        self,
        request: InterruptRequest,
        *,
        subject_id: str = "",
    ) -> SecurityInterrupt:
        return SecurityInterrupt(request=request, subject_id=subject_id)

    def add_tool(self, tool_name: str) -> None:
        """Register a tool name tag for display or subclass use."""
        self._tool_names.add(tool_name)

    def add_tools(self, tool_names: Iterable[str]) -> None:
        """Register multiple tool name tags."""
        self._tool_names.update(tool_names)

    def add_policy(self, tool_name: str, _policy=None) -> None:
        """Deprecated alias of add_tool (policy ignored)."""
        self.add_tool(tool_name)

    def get_tools(self) -> Set[str]:
        """Get registered tool name tags."""
        return set(self._tool_names)

    def get_callbacks(self):  # type: ignore[override]
        callbacks = {}
        for event in self.supported_events:
            method_name = EVENT_METHOD_MAP.get(event)
            if method_name is None:
                continue
            method = getattr(self, method_name, None)
            if method is not None:
                callbacks[event] = method
        return callbacks

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.BEFORE_INVOKE)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.AFTER_INVOKE)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.BEFORE_TOOL_CALL)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.AFTER_TOOL_CALL)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.BEFORE_MODEL_CALL)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.AFTER_MODEL_CALL)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.ON_MODEL_EXCEPTION)

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.ON_TOOL_EXCEPTION)

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.BEFORE_TASK_ITERATION)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        await self._run_and_apply(ctx, AgentCallbackEvent.AFTER_TASK_ITERATION)

    async def run_security_check(
        self,
        security_ctx: SecurityCheckContext,
    ) -> SecurityDecision:
        """Override to implement a concrete security check."""
        raise NotImplementedError

    async def _run_and_apply(
        self,
        ctx: AgentCallbackContext,
        event: AgentCallbackEvent,
    ) -> None:
        subject_id = self._resolve_subject_id(ctx, event)
        user_input = self._get_user_input(ctx, subject_id)
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=event,
            user_input=user_input,
            auto_confirm_config=self._get_auto_confirm_config(ctx),
            subject_id=subject_id,
        )
        decision = await self.run_security_check(security_ctx)
        ctx.extra["_interrupt_decision"] = decision
        await self.apply_security_decision(security_ctx, decision)

    def _get_auto_confirm_config(self, ctx: AgentCallbackContext) -> dict[str, Any]:
        if ctx.session is None:
            return {}
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY)
        return config if isinstance(config, dict) else {}

    def _resolve_subject_id(
        self,
        ctx: AgentCallbackContext,
        event: AgentCallbackEvent,
    ) -> str:
        if event == AgentCallbackEvent.BEFORE_TOOL_CALL:
            return self._resolve_tool_call_id(ctx.inputs.tool_call)
        return f"{self.__class__.__name__}:{event.value}"

    def _resolve_tool_call_id(self, tool_call: Optional[ToolCall]) -> str:
        if tool_call is None:
            return ""
        return tool_call.id if hasattr(tool_call, "id") else ""

    def _get_user_input(
        self,
        ctx: AgentCallbackContext,
        subject_id: str,
    ) -> Optional[Any]:
        raw_input = ctx.extra.get(RESUME_USER_INPUT_KEY)
        logger.info(
            "[BaseSecurityRail] get_user_input subject_id=%r raw_input_type=%s",
            subject_id,
            type(raw_input).__name__ if raw_input is not None else "None",
        )
        if raw_input is None:
            return None

        if isinstance(raw_input, InteractiveInput):
            if subject_id in raw_input.user_inputs:
                return raw_input.user_inputs[subject_id]
            return None

        if isinstance(raw_input, dict):
            if subject_id in raw_input:
                return raw_input[subject_id]
            return raw_input

        return raw_input

    async def apply_security_decision(
        self,
        security_ctx: SecurityCheckContext,
        decision: SecurityDecision,
    ) -> None:
        """Apply a concrete security decision.

        The base class only knows that allow means "continue". Subclasses own
        the event-specific semantics for reject and interrupt.
        """
        if isinstance(decision, SecurityAllow):
            return
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement apply_security_decision "
            f"for {type(decision).__name__} on {security_ctx.event.value}."
        )

    @staticmethod
    def _build_force_finish_result(decision: SecurityReject) -> dict[str, Any]:
        if isinstance(decision.result, dict):
            return decision.result
        return {
            "output": decision.message or str(decision.result or "Rejected by security rail."),
            "result_type": "error",
        }

    def _raise_tool_interrupt(
        self,
        tool_name: str,
        tool_call: Optional[ToolCall],
        request: InterruptRequest,
    ) -> None:
        raise AbortError(
            reason=f"Tool execution interrupted: {tool_name}",
            cause=ToolInterruptException(request=request, tool_call=tool_call),
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
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = tool_result
        ctx.inputs.tool_msg = msg


__all__ = [
    "BaseSecurityRail",
    "SecurityAllow",
    "SecurityCheckContext",
    "SecurityDecision",
    "SecurityInterrupt",
    "SecurityReject",
]
