# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class InterruptRequest(BaseModel):
    """Request for user interruption/confirmation."""
    message: str = ""
    payload_schema: dict = Field(default_factory=dict)
    auto_confirm_key: str = ""


class ToolCallInterruptRequest(InterruptRequest):
    """Interrupt request with tool call context.

    Inherits from InterruptRequest and adds tool call context fields.
    Used for serializing interrupt info to user output.

    When the input request is a subclass of InterruptRequest (e.g. AskUserRequest),
    the subclass's extra fields are preserved via model_dump().
    """
    model_config = {"extra": "allow"}

    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: Any = None
    index: Optional[int] = None

    @classmethod
    def from_tool_call(
            cls,
            request: InterruptRequest,
            tool_call: Any,
    ) -> "ToolCallInterruptRequest":
        """Create ToolCallInterruptRequest from InterruptRequest and ToolCall.

        Preserves all fields from the request, including any extra fields
        defined in subclasses (e.g. AskUserRequest.questions).
        """
        base_fields = request.model_dump()
        base_fields.update(
            tool_name=tool_call.name if hasattr(tool_call, 'name') else str(tool_call),
            tool_call_id=tool_call.id if hasattr(tool_call, 'id') else "",
            tool_args=tool_call.arguments if hasattr(tool_call, 'arguments') else None,
            index=tool_call.index if hasattr(tool_call, 'index') else None,
        )
        return cls.model_validate(base_fields)
