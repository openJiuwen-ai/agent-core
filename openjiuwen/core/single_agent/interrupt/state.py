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
# Batch-scoped allow keys (P3-2 批次级授权): set[auto_confirm_key] injected into
# ``ctx.extra`` before replaying an interrupted parallel tool-call batch. Rail
# checks it during first-check so sibling calls of the same tool that the user
# already approved (allow_once) skip re-asking. Lifetime is the replay itself
# only — it is popped in the same ``finally`` as RESUME_USER_INPUT_KEY and is
# never written to session state, so a fresh batch in a later iteration asks
# again. Key semantics reuse the auto_confirm key (tool name, or
# ``tool:subcommand`` for simple shell commands).
RESUME_BATCH_ALLOW_KEYS = "_resume_batch_allow_keys"


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
