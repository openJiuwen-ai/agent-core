# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field

from openjiuwen.core.common.utils.pydantic_compat import RestoreFieldDefaultsOnUnpickle


class InterruptKind:
    """Well-known values of :attr:`InterruptRequest.interrupt_kind`.

    The kind tells a host how to present an interrupt and what shape its
    answer takes. Kinds are plain strings so third-party rails can declare
    their own; the empty string means the raising rail did not declare one.
    Answers are routed by ``interrupt_owner``, never by kind.
    """

    UNDECLARED = ""
    PERMISSION = "permission"
    CONFIRM = "confirm"
    ASK_USER = "ask_user"
    SKILL_EVOLUTION_APPROVAL = "skill_evolution_approval"
    TEAM_TOOL_APPROVAL = "team_tool_approval"


def new_interrupt_id() -> str:
    """Return a fresh, globally unique interrupt id."""
    return f"intr_{uuid.uuid4().hex}"


class InterruptRequest(RestoreFieldDefaultsOnUnpickle, BaseModel):
    """Request for user interruption/confirmation.

    Identity fields, filled by the framework:

    - ``interrupt_id``: unique id of this interrupt, given by the interrupt
      handler when it collects the paused call; hosts answer it with
      ``InteractiveInput.answer(interrupt_id, value)``.
    - ``interrupt_kind``: what kind of question this is (``InterruptKind``),
      for hosts to render it and shape the answer.
    - ``interrupt_owner``: stable key of the rail that raised it; the answer
      is delivered only to that rail.

    The rail sets kind and owner when it raises the interrupt.
    """

    message: str = ""
    payload_schema: dict = Field(default_factory=dict)
    auto_confirm_key: str = ""
    ui_options: list[dict] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    interrupt_id: str = Field(default="", description="Unique id of this interrupt, set by the interrupt handler.")
    interrupt_kind: str = Field(default="", description="Interrupt kind (see InterruptKind); empty when undeclared.")
    interrupt_owner: str = Field(default="", description="Key of the rail that raised the interrupt.")


def interrupt_kind_of(request: InterruptRequest) -> str:
    """Return the kind of ``request``; "" when the raiser declared none.

    Skill-evolution approvals persisted before the field existed carry their
    kind only in ``metadata["interrupt_kind"]``.
    """
    return request.interrupt_kind or request.metadata.get("interrupt_kind", "")


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
            tool_name=tool_call.name if hasattr(tool_call, "name") else str(tool_call),
            tool_call_id=tool_call.id if hasattr(tool_call, "id") else "",
            tool_args=tool_call.arguments if hasattr(tool_call, "arguments") else None,
            index=tool_call.index if hasattr(tool_call, "index") else None,
        )
        return cls.model_validate(base_fields)
