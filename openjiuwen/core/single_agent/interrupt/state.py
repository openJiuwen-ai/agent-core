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
# 「在途中断」登记键：ToolInterruptException 被逐工具捕获的时点（可能远早于
# 整批工具完成后的 commit_interrupt）即把 {outer_tool_call_id: InterruptRequest}
# 记入 session。批末 commit_interrupt 转正并清键；invoke 异常中止时由
# react_agent 的 finally 清键。仅供 has_pending_interrupt 类准入查询读取，
# ReAct resume 状态机只认 INTERRUPTION_KEY，不读本键。
INTERRUPTION_INFLIGHT_KEY = "__react_agent_interruption_inflight__"
RESUME_USER_INPUT_KEY = "_resume_user_input"
RESUME_ORIGINAL_TOOL_CALLS_KEY = "_resume_original_tool_calls"
INTERRUPT_AUTO_CONFIRM_KEY = "__interrupt_auto_confirm__"
RESUME_START_ITERATION_KEY = "_resume_start_iteration"


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
