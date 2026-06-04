# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Base classes for harness security rails."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Optional, Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
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

_MODEL_EVENTS: Set[AgentCallbackEvent] = {
    AgentCallbackEvent.BEFORE_MODEL_CALL,
    AgentCallbackEvent.AFTER_MODEL_CALL,
}


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


@dataclass
class SecurityAlert(SecurityDecision):
    """Allow execution but alert user with a message.

    Unlike SecurityReject which blocks execution, SecurityAlert
    allows the operation to continue while notifying the user.
    """

    message: str
    alert_type: str = "security"
    message_id: Optional[str] = None


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

    def alert(
        self,
        message: str,
        alert_type: str = "security",
    ) -> SecurityAlert:
        """Create a SecurityAlert decision.

        Args:
            message: Alert message to display
            alert_type: Category for filtering (e.g., "security", "pii", "compliance")

        Returns:
            SecurityAlert decision that allows execution but alerts user.
        """
        return SecurityAlert(
            message=message,
            alert_type=alert_type,
        )

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

        if isinstance(decision, SecurityInterrupt) and event in _MODEL_EVENTS:
            logger.warning(
                "[BaseSecurityRail] SecurityInterrupt not supported for model events (%s). "
                "Auto-rejecting with interrupt message.",
                event.value,
            )
            decision = SecurityReject(
                message=decision.request.message or "Security interrupt not allowed on model events",
            )

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
        if event == AgentCallbackEvent.AFTER_TOOL_CALL:
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
        """Apply security decision with default behaviors.

        Default behaviors:
        - Allow: return (continue execution)
        - Alert: log + stream to frontend, then continue execution
        - Reject:
          - MODEL events: request_force_finish
          - TOOL events (both BEFORE and AFTER): _skip_tool (agent continues)
        - Interrupt:
          - MODEL events: auto-converted to Reject in _run_and_apply
          - TOOL events: _raise_tool_interrupt

        Subclasses can override to customize behavior.
        """
        if isinstance(decision, SecurityAllow):
            return

        if isinstance(decision, SecurityAlert):
            await self._apply_alert(security_ctx, decision)
            return

        if isinstance(decision, SecurityReject):
            await self._apply_reject(security_ctx, decision)
            return

        if isinstance(decision, SecurityInterrupt):
            self._apply_interrupt(security_ctx, decision)
            return

        raise NotImplementedError(
            f"{self.__class__.__name__} received unknown decision type: "
            f"{type(decision).__name__}"
        )

    async def _apply_reject(
        self,
        security_ctx: SecurityCheckContext,
        decision: SecurityReject,
    ) -> None:
        """Default reject behavior based on event type.

        MODEL events: send retract + force_finish
        TOOL events (both BEFORE and AFTER): skip_tool (agent continues)
        """
        ctx = security_ctx.callback_ctx
        event = security_ctx.event

        error_msg = decision.message or "blocked for security reason"

        if event in _MODEL_EVENTS:
            retract_message = decision.message or "内容已因安全原因撤回"
            await self._send_retract_event(ctx, retract_message)
            result = self._build_force_finish_result(decision)
            ctx.request_force_finish(result)
            return

        inputs = ctx.inputs
        tool_call = getattr(inputs, "tool_call", None)
        tool_call_id = tool_call.id if tool_call else ""

        if event in (AgentCallbackEvent.BEFORE_TOOL_CALL, AgentCallbackEvent.AFTER_TOOL_CALL):
            self._skip_tool(
                ctx,
                tool_call,
                tool_result=error_msg,
                tool_message=ToolMessage(
                    content=error_msg,
                    tool_call_id=tool_call_id,
                ),
            )
            return

    def _apply_interrupt(
        self,
        security_ctx: SecurityCheckContext,
        decision: SecurityInterrupt,
    ) -> None:
        """Default interrupt behavior for TOOL events.

        MODEL events: Should not reach here (converted to Reject in _run_and_apply)
        TOOL events: Raise ToolInterruptException if user_input is None
        """
        ctx = security_ctx.callback_ctx
        event = security_ctx.event

        if event in _MODEL_EVENTS:
            logger.warning(
                "[BaseSecurityRail] SecurityInterrupt on MODEL event should be "
                "converted to Reject. This should not happen."
            )
            return

        if security_ctx.user_input is None:
            self._raise_tool_interrupt(
                tool_name=getattr(ctx.inputs, "tool_name", ""),
                tool_call=getattr(ctx.inputs, "tool_call", None),
                request=decision.request,
            )

    async def _apply_alert(
        self,
        security_ctx: SecurityCheckContext,
        decision: SecurityAlert,
    ) -> None:
        """Apply SecurityAlert: log and stream to frontend, then allow execution.

        Logs alert at warning level and streams OutputSchema to frontend.
        Uses type="security.alert" for frontend to handle specifically.
        """
        logger.warning(
            "[SecurityAlert] rail=%s message=%s alert_type=%s",
            self.__class__.__name__,
            decision.message,
            decision.alert_type,
        )

        ctx = security_ctx.callback_ctx
        if ctx.session is not None:
            try:
                session_id = getattr(ctx.session, "session_id", "") or ""
                tool_name = ""
                tool_call_id = ""
                message_id = ""
                if hasattr(ctx.inputs, "tool_name"):
                    tool_name = ctx.inputs.tool_name or ""
                if hasattr(ctx.inputs, "tool_call") and ctx.inputs.tool_call:
                    tool_call_id = getattr(ctx.inputs.tool_call, "id", "") or ""

                if ctx.context is not None:
                    messages = ctx.context.get_messages(size=1, with_history=False)
                    if messages:
                        last_msg = messages[0]
                        metadata = getattr(last_msg, "metadata", None)
                        if isinstance(metadata, dict):
                            message_id = metadata.get("context_message_id", "") or ""

                await ctx.session.write_stream(
                    OutputSchema(
                        type="security.alert",
                        index=0,
                        payload={
                            "session_id": session_id,
                            "message": decision.message,
                            "alert_type": decision.alert_type,
                            "tool_name": tool_name,
                            "tool_call_id": tool_call_id,
                            "message_id": message_id,
                        },
                    )
                )
            except Exception:
                logger.debug(
                    "[BaseSecurityRail] Failed to stream alert: %s",
                    exc_info=True,
                )

    async def _send_retract_event(
        self,
        ctx: AgentCallbackContext,
        message: str,
    ) -> None:
        """Send chat.retract event to notify frontend to retract content."""
        if ctx.session is None:
            return
        try:
            message_id = ""
            if ctx.context is not None:
                messages = ctx.context.get_messages(size=1, with_history=False)
                if messages:
                    last_msg = messages[0]
                    metadata = getattr(last_msg, "metadata", None)
                    if isinstance(metadata, dict):
                        message_id = metadata.get("context_message_id", "") or ""
            
            if not message_id:
                import uuid
                message_id = str(uuid.uuid4())
            
            await ctx.session.write_stream(
                OutputSchema(
                    type="chat.retract",
                    index=0,
                    payload={
                        "session_id": getattr(ctx.session, 'session_id', "") or "",
                        "message": message,
                        "message_id": message_id,
                    },
                )
            )
        except Exception:
            logger.debug(
                "[BaseSecurityRail] Failed to send retract event: %s",
                exc_info=True,
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

    @staticmethod
    def _is_auto_confirmed(
        auto_confirm_config: Optional[dict[str, Any]],
        auto_confirm_key: str,
    ) -> bool:
        """Check if auto_confirm_key is in the auto-confirm config.

        Args:
            auto_confirm_config: Auto-confirm config dict from session state
            auto_confirm_key: Key to check (should be stable per rail/tool)

        Returns:
            True if key exists and is True in config
        """
        if auto_confirm_config is None:
            return False
        return bool(auto_confirm_config.get(auto_confirm_key, False))

    @staticmethod
    def _store_auto_confirm(
        ctx: AgentCallbackContext,
        auto_confirm_key: str,
    ) -> None:
        """Store auto_confirm_key in session state for future auto-approval.

        Args:
            ctx: Agent callback context (must have session)
            auto_confirm_key: Key to store (should be stable per rail/tool)
        """
        if ctx.session is None:
            return
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
        config[auto_confirm_key] = True
        ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
        logger.info(
            "[BaseSecurityRail] auto_confirm.store key=%s",
            auto_confirm_key,
        )

    def _replace_messages(
        self,
        ctx: AgentCallbackContext,
        messages: List[BaseMessage],
        with_history: bool = True,
    ) -> None:
        """Replace the entire message list with the provided one.

        Subclass handles message filtering/modification itself,
        then passes the complete modified list here for replacement.
        Gracefully returns if ctx.context is None.

        Args:
            ctx: Agent callback context with ModelContext
            messages: Complete modified message list to set
            with_history: If True, replace context + history messages;
                          if False, replace only history messages
        """
        if ctx.context is None:
            return
        ctx.context.set_messages(messages, with_history=with_history)

    def _pop_last_user_message(
        self,
        ctx: AgentCallbackContext,
        with_history: bool = False,
    ) -> List[Any]:
        """Pop the last user message from context.

        Args:
            ctx: Agent callback context with ModelContext
            with_history: If True, pop from full history; if False, only current turn

        Returns:
            List containing the popped user message (empty if not found)
        """
        if ctx.context is None:
            return []
        messages = ctx.context.get_messages(with_history=with_history)
        for i in range(len(messages) - 1, -1, -1):
            if getattr(messages[i], "role", None) == "user":
                popped = messages[i]
                ctx.context.set_messages(messages[:i] + messages[i + 1:], with_history=with_history)
                return [popped]
        return []

    def _pop_last_assistant_message(
        self,
        ctx: AgentCallbackContext,
        with_history: bool = False,
    ) -> List[Any]:
        """Pop the last assistant message from context.

        Args:
            ctx: Agent callback context with ModelContext
            with_history: If True, pop from full history; if False, only current turn

        Returns:
            List containing the popped assistant message (empty if not found)
        """
        if ctx.context is None:
            return []
        messages = ctx.context.get_messages(with_history=with_history)
        for i in range(len(messages) - 1, -1, -1):
            if getattr(messages[i], "role", None) == "assistant":
                popped = messages[i]
                ctx.context.set_messages(messages[:i] + messages[i + 1:], with_history=with_history)
                return [popped]
        return []

    def _pop_last_tool_message(
        self,
        ctx: AgentCallbackContext,
        with_history: bool = False,
    ) -> List[Any]:
        """Pop the last tool message (tool result) from context.

        Args:
            ctx: Agent callback context with ModelContext
            with_history: If True, pop from full history; if False, only current turn

        Returns:
            List containing the popped tool message (empty if not found)
        """
        if ctx.context is None:
            return []
        messages = ctx.context.get_messages(with_history=with_history)
        for i in range(len(messages) - 1, -1, -1):
            role = getattr(messages[i], "role", None)
            if role == "tool":
                popped = messages[i]
                ctx.context.set_messages(messages[:i] + messages[i + 1:], with_history=with_history)
                return [popped]
        return []

    def _handle_interrupt_resume(
        self,
        security_ctx: SecurityCheckContext,
        auto_confirm_key: str,
    ) -> Optional[SecurityDecision]:
        """Handle interrupt resume with auto-confirm and user_input parsing.

        Extracted helper for common interrupt flow pattern:
        1. Check auto_confirm_config for pre-approved key
        2. Parse user_input for approval/rejection
        3. Store auto_confirm if user requested "remember"

        Args:
            security_ctx: Security check context with callback_ctx, user_input, auto_confirm_config
            auto_confirm_key: Stable key for auto-confirm lookup (e.g., "rail_name:tool_name")

        Returns:
            SecurityAllow if approved (auto-confirm stored if requested)
            SecurityReject if rejected by user
            None if no user_input (first call, should interrupt)
        """
        auto_confirm_config = security_ctx.auto_confirm_config
        
        if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
            return self.allow()
        
        user_input = security_ctx.user_input
        if user_input is None:
            return None
        
        approved = False
        auto_confirm = False
        if isinstance(user_input, dict):
            approved = user_input.get("approved", False)
            auto_confirm = user_input.get("auto_confirm", False)
        elif hasattr(user_input, "approved"):
            approved = user_input.approved
            auto_confirm = getattr(user_input, "auto_confirm", False)
        
        if approved:
            if auto_confirm:
                self._store_auto_confirm(security_ctx.callback_ctx, auto_confirm_key)
            return self.allow()
        
        return self.reject(message="")


__all__ = [
    "BaseSecurityRail",
    "SecurityAlert",
    "SecurityAllow",
    "SecurityCheckContext",
    "SecurityDecision",
    "SecurityInterrupt",
    "SecurityReject",
]
