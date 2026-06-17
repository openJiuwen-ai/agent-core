# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail, InterruptDecision


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

    def _get_auto_confirm_key(self, tool_call: ToolCall) -> str:
        return tool_call.name

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
            auto_confirm_config: Optional[dict] = None,
    ) -> InterruptDecision:
        auto_confirm_key = self._get_auto_confirm_key(tool_call)

        # Check auto-confirm on first call (user_input is None)
        if user_input is None:
            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                return self.approve()
            return self.interrupt(InterruptRequest(
                message=self.request.message,
                payload_schema=self.request.payload_schema,
                auto_confirm_key=auto_confirm_key,
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
                    auto_confirm_key=auto_confirm_key,
                ))
        except Exception:
            return self.interrupt(InterruptRequest(
                message=self.request.message,
                payload_schema=self.request.payload_schema,
                auto_confirm_key=auto_confirm_key,
            ))

        if payload.approved:
            return self.approve()

        return self.reject(tool_result=payload.feedback or "User feedback: rejected\n action")

    @staticmethod
    def _is_auto_confirmed(config: Optional[dict], key: str) -> bool:
        """Check if key is auto-confirmed in config."""
        if config is None:
            return False
        return config.get(key, False)
