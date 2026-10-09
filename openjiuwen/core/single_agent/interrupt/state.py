# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from dataclasses import field
from typing import Dict
from pydantic import BaseModel

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

INTERRUPTION_KEY = "__react_agent_interruption__"
RESUME_USER_INPUT_KEY = "_resume_user_input"
INTERRUPT_AUTO_CONFIRM_KEY = "__interrupt_auto_confirm__"
RESUME_START_ITERATION_KEY = "_resume_start_iteration"


class BaseInterruptionState(BaseModel):
    """Common interruption state fields.

    Persisted via the agent checkpointer. To prevent stale interrupts from
    leaking across process restarts (bug #4756), each interrupt records the
    ``invocation_id`` of the agent cycle that produced it. When the next
    ``invoke()`` loads this state from disk it compares the persisted
    ``trigger_invocation_id`` against the current cycle's ``invocation_id``;
    mismatched cycles are treated as stale and dropped via ``is_stale``.
    """

    ai_message: AssistantMessage
    iteration: int
    original_query: str = ""
    # invocation_id of the agent cycle that produced this interrupt. Empty
    # for legacy / pre-fix state — those entries are treated as fresh to
    # avoid breaking older serialized payloads on disk.
    trigger_invocation_id: str = ""

    def is_stale(self, current_invocation_id: str) -> bool:
        """Return True if this interrupt belongs to a previous invoke cycle.

        Empty ``trigger_invocation_id`` is treated as legacy / unknown and
        returns False so existing on-disk payloads keep their old resume
        semantics. A non-empty value that does not match
        ``current_invocation_id`` means the interrupt was committed in a
        prior process and must be discarded instead of resumed — this is
        the bug #4756 hot path.
        """
        if not self.trigger_invocation_id:
            return False
        return self.trigger_invocation_id != (current_invocation_id or "")


class ToolInterruptEntry(BaseModel):
    tool_call: ToolCall
    interrupt_requests: Dict[str, InterruptRequest] = field(default_factory=dict)
    is_sub_agent: bool = False


class ToolInterruptionState(BaseInterruptionState):
    """Tool interruption state for resume support.
    """
    interrupted_tools: Dict[str, ToolInterruptEntry] = field(default_factory=dict)
    auto_confirm_mapping: Dict[str, str] = field(default_factory=dict)
