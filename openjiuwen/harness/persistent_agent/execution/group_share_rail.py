# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Consume business sharing replies without granting or caching host authorization."""

import json

from pydantic import ValidationError

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.interrupt.response import InterruptKind
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, ToolInterruptionState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..tools.group_history import GROUP_SHARE_INTERRUPT_OWNER, SEARCH_TOOL_NAME, _failure


class PersistentGroupShareRail(BaseInterruptRail):
    """Approval permits another authorized host read; rejection skips only its own call."""

    priority = 85
    interrupt_kind = InterruptKind.CONFIRM

    def __init__(self):
        super().__init__(tool_names=[SEARCH_TOOL_NAME])
        self.interrupt_owner = GROUP_SHARE_INTERRUPT_OWNER
        self._requests = {}

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Read the loaded scene before native moves it out of Session state."""
        state = ctx.session.get_state(INTERRUPTION_KEY) if ctx.session is not None else None
        self._requests = {}
        if not isinstance(state, ToolInterruptionState):
            return
        for entry in state.interrupted_tools.values():
            if entry.tool_call.name != SEARCH_TOOL_NAME:
                continue
            for call_id, request in entry.interrupt_requests.items():
                if request.metadata.get("kind") != "group_share":
                    continue
                if request.interrupt_owner != self.interrupt_owner:
                    continue
                self._requests[call_id] = request

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        call = ctx.inputs.tool_call
        if ctx.inputs.tool_name != SEARCH_TOOL_NAME or call is None or ctx.session is None:
            return
        if (
            ctx.has_force_finish_request
            or ctx.extra.get("_skip_tool")
            or ctx.extra.get("_skip_tool_calls", {}).get(call.id)
        ):
            return
        request = self._requests.get(call.id)
        if request is None:
            return
        answer = self.find_own_answer(ctx.extra, call.id)
        if answer is None or answer.request.interrupt_id != request.interrupt_id:
            self._raise_interrupt(call.name, call, request)
            return
        try:
            reply = ConfirmPayload.model_validate(answer.value)
        except ValidationError:
            self._raise_interrupt(call.name, call, request)
            return
        if reply.approved:
            return
        result = _failure("unavailable", "所有者拒绝此次跨群消息分享。")
        ctx.extra.setdefault("_skip_tool_calls", {})[call.id] = True
        ctx.inputs.tool_result = result
        ctx.inputs.tool_msg = ToolMessage(content=json.dumps(result.data, ensure_ascii=False), tool_call_id=call.id)
