# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interrupt ownership shared by the rails that pause tool calls.

Every interrupt records the key of the rail that raised it
(``InterruptRequest.interrupt_owner``); on resume the handler attributes each
answer to that interrupt, and a rail reads only answers to interrupts it owns.
The key defaults to the rail's class path, so it survives pickling and the
rail being rebuilt between pause and resume. Two rails of one class on one
agent must be given distinct keys (``rail.interrupt_owner = "..."``), or each
would also read the other's answers. Answers are keyed by call and owner, so
a rail keeps at most one interrupt pending per call.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.core.single_agent.interrupt.response import InterruptKind, InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import RESUME_INTERRUPT_ANSWERS_KEY, InterruptAnswer


class InterruptOwnerMixin:
    """Identity, stamping and answer lookup for rails that raise interrupts."""

    #: Kind stamped on requests this rail raises (for hosts); see ``InterruptKind``.
    interrupt_kind: str = InterruptKind.UNDECLARED

    @property
    def interrupt_owner(self) -> str:
        """Stable key that routes answers back to this rail."""
        override = self.__dict__.get("_interrupt_owner")
        if override:
            return override
        cls = type(self)
        return f"{cls.__module__}.{cls.__qualname__}"

    @interrupt_owner.setter
    def interrupt_owner(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("interrupt_owner must be a non-empty string")
        self.__dict__["_interrupt_owner"] = value

    def stamp_interrupt(self, request: InterruptRequest) -> None:
        """Mark ``request`` as raised by this rail: its owner, and its kind unless it declares one.

        The interrupt handler gives the request its id when it collects the paused call.
        """
        request.interrupt_owner = self.interrupt_owner
        if not request.interrupt_kind:
            request.interrupt_kind = self.interrupt_kind

    def find_own_answer(self, extra: Dict[str, Any], tool_call_id: str) -> Optional[InterruptAnswer]:
        """Return the answer to this rail's interrupt on one call in the current resume, if any."""
        return extra.get(RESUME_INTERRUPT_ANSWERS_KEY, {}).get(tool_call_id, {}).get(self.interrupt_owner)

    def is_carried_answer(self, extra: Dict[str, Any], tool_call_id: str) -> bool:
        """Whether this rail's answer on the call was given in an earlier resume round.

        Such an answer was already acted on: accept it again without repeating
        external side effects, such as persisting rules.
        """
        answer = self.find_own_answer(extra, tool_call_id)
        return answer is not None and answer.carried


__all__ = ["InterruptOwnerMixin"]
