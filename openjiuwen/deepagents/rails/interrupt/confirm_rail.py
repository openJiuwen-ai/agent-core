# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import Any, Iterable, Optional
from pydantic import BaseModel, Field
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.rails.interrupt.interrupt_base import BaseInterruptRail, InterruptDecision


class ConfirmPayload(BaseModel):
    """Payload for user confirmation response."""
    approved: bool
    feedback: str = Field(default="")
    auto_confirm: bool = Field(default=False)

    @classmethod
    def to_schema(cls) -> dict:
        return cls.model_json_schema()


class ConfirmRequest(BaseModel):
    """Confirmation request configuration for a tool."""
    message: str = Field(default="Please approve or reject?", description="Message shown to the user.")
    payload_schema: dict = Field(default_factory=lambda: ConfirmPayload.to_schema())


class ConfirmInterruptRail(BaseInterruptRail):
    """Confirm rail: only proceeds when ConfirmPayload.approved is True.
    
    Usage:
        rail = ConfirmInterruptRail(tool_names=["read", "delete"])
        await agent.register_rail(rail)
    """

    def __init__(
            self,
            tool_names: Optional[Iterable[str]] = None,
    ):
        super().__init__(tool_names=tool_names)
        self.request = ConfirmRequest()

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
    ) -> InterruptDecision:
        tool_name = tool_call.name if tool_call is not None else ""

        # Check auto-confirm on first call (user_input is None)
        if user_input is None:
            if self._is_auto_confirmed(ctx, tool_name):
                return self.approve()
            return self.interrupt(InterruptRequest(
                message=self.request.message,
                payload_schema=self.request.payload_schema,
            ))

        try:
            if isinstance(user_input, ConfirmPayload):
                payload = user_input
            elif isinstance(user_input, dict):
                payload = ConfirmPayload.model_validate(user_input)
            else:
                return self.interrupt(InterruptRequest(
                    message=self.request.message,
                    payload_schema=self.request.payload_schema,
                ))
        except Exception:
            return self.interrupt(InterruptRequest(
                message=self.request.message,
                payload_schema=self.request.payload_schema,
            ))

        if payload.auto_confirm:
            if ctx.session is not None and tool_name:
                config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
                config[tool_name] = True
                ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})

        if payload.approved:
            return self.approve()

        return self.reject(tool_result=payload.feedback or "User feedback: rejected the action")

    @staticmethod
    def _is_auto_confirmed(ctx: AgentCallbackContext, tool_name: str) -> bool:
        """Check if tool is auto-confirmed in session state."""
        session = ctx.session
        if session is None:
            return False

        config = session.get_state(INTERRUPT_AUTO_CONFIRM_KEY)
        if config is None:
            return False

        return config.get(tool_name, False)
