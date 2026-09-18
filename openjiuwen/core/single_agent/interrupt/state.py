# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from dataclasses import field
from typing import Any, Dict
from pydantic import BaseModel

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

INTERRUPTION_KEY = "__react_agent_interruption__"
RESUME_USER_INPUT_KEY = "_resume_user_input"
# Argument key carrying the user's answer back into the delegating tool when a
# sub-agent interrupt is resumed. Underscore-prefixed so it cannot collide with
# a model-supplied argument. Written by ToolInterruptHandler, read by TaskTool;
# both sides import this name so the contract cannot drift.
SUB_AGENT_RESUME_INPUT_KEY = "_sub_agent_resume_input"
INTERRUPT_AUTO_CONFIRM_KEY = "__interrupt_auto_confirm__"
RESUME_START_ITERATION_KEY = "_resume_start_iteration"


def is_interrupt_envelope(result: Any) -> bool:
    """Return True when ``result`` is the dict an agent returns when it pauses.

    The envelope is the shape produced by ``build_interrupt_result``: it carries
    ``result_type == "interrupt"`` together with the ids of the pending
    interrupts. A tool that wraps an agent hands this dict back verbatim, so it
    reaches the caller as an ordinary tool result and has to be told apart from
    a real one.

    A ``(tool_result, tool_message)`` tuple is accepted as well and inspected by
    its first element, so call sites holding either form can share one check.
    """
    if isinstance(result, tuple) and len(result) >= 1:
        result = result[0]

    return (
            isinstance(result, dict)
            and result.get("result_type") == "interrupt"
            and "interrupt_ids" in result
    )


class BaseInterruptionState(BaseModel):
    """Common interruption state fields."""
    ai_message: AssistantMessage
    iteration: int
    original_query: str = ""


class ToolInterruptEntry(BaseModel):
    tool_call: ToolCall
    interrupt_requests: Dict[str, InterruptRequest] = field(default_factory=dict)
    is_sub_agent: bool = False


class ToolInterruptionState(BaseInterruptionState):
    """Tool interruption state for resume support.
    """
    interrupted_tools: Dict[str, ToolInterruptEntry] = field(default_factory=dict)
    auto_confirm_mapping: Dict[str, str] = field(default_factory=dict)
