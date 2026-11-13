# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Consume business sharing replies without granting or caching host authorization."""

import json
import logging

from pydantic import ValidationError

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.single_agent.interrupt.response import InterruptKind, InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, ToolInterruptionState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..ports import GroupContentApprovalRequired
from ..tools.group_history import GROUP_SHARE_INTERRUPT_OWNER, SEARCH_TOOL_NAME, _failure

_SHARING_TOOLS = frozenset({SEARCH_TOOL_NAME, "bash"})


class PersistentGroupShareRail(BaseInterruptRail):
    """Approval permits another authorized host read; rejection skips only its own call."""

    priority = 85
    interrupt_kind = InterruptKind.CONFIRM

    def __init__(self, execution=None, group_chat=None):
        self._preflight = getattr(group_chat, "prepare_tool_call", None)
        self._execution = execution
        self._tools = _SHARING_TOOLS | (
            {"read_file", "list_files", "list_dir", "share_file"} if self._preflight else set()
        )
        super().__init__(tool_names=self._tools)
        self.interrupt_owner = GROUP_SHARE_INTERRUPT_OWNER
        self._requests = {}

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Read the loaded scene before native moves it out of Session state."""
        state = ctx.session.get_state(INTERRUPTION_KEY) if ctx.session is not None else None
        self._requests = {}
        if not isinstance(state, ToolInterruptionState):
            return
        for entry in state.interrupted_tools.values():
            if entry.tool_call.name not in self._tools:
                continue
            for call_id, request in entry.interrupt_requests.items():
                if request.metadata.get("kind") != "group_share":
                    continue
                if request.interrupt_owner != self.interrupt_owner:
                    continue
                self._requests[call_id] = request

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        call = ctx.inputs.tool_call
        if ctx.inputs.tool_name not in self._tools or call is None or ctx.session is None:
            return
        if (
            ctx.has_force_finish_request
            or ctx.extra.get("_skip_tool")
            or ctx.extra.get("_skip_tool_calls", {}).get(call.id)
        ):
            return
        request = self._requests.get(call.id)
        if request is None:
            await self._prepare(ctx, call)
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
            await self._prepare(ctx, call)
            return
        if call.name == "bash" and request.metadata.get("origin") == "cli_group_read":
            # The host adapter re-enters frozen release, never the raw shell.
            # It must retain current-group hits even when every outside source was denied.
            return
        if request.metadata.get("origin") == "session_artifact" and self._preflight:
            await self._prepare(ctx, call)
            return
        result = _failure("unavailable", "所有者拒绝此次跨群消息分享。")
        ctx.extra.setdefault("_skip_tool_calls", {})[call.id] = True
        ctx.inputs.tool_result = result
        ctx.inputs.tool_msg = ToolMessage(content=json.dumps(result.data, ensure_ascii=False), tool_call_id=call.id)

    async def _prepare(self, ctx, call):
        """Allow a host to guard persisted artifact provenance before any tool policy can bypass it."""
        if self._preflight is None or call.name == SEARCH_TOOL_NAME:
            return
        try:
            arguments = getattr(ctx.inputs, "tool_args", None)
            if not isinstance(arguments, dict):
                arguments = json.loads(call.arguments)
            output = await self._preflight(self._execution, call.name, dict(arguments))
        except GroupContentApprovalRequired as exc:
            self._raise_interrupt(
                call.name,
                call,
                InterruptRequest(
                    message="跨群文件复用需要所有者批准。",
                    payload_schema=ConfirmPayload.to_schema(),
                    metadata={"kind": "group_share", "approval_id": exc.approval_id, "origin": "session_artifact"},
                    interrupt_owner=GROUP_SHARE_INTERRUPT_OWNER,
                    interrupt_kind=InterruptKind.CONFIRM,
                ),
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Sharing preflight unavailable request=%s error_type=%s",
                getattr(self._execution, "request_id", ""),
                type(exc).__name__,
            )
            output = _failure("unavailable", "文件来源与跨群审批暂时无法核验，未执行该读取或分享。")
        if output is None:
            return
        if not isinstance(output, ToolOutput):
            output = _failure("unavailable", "宿主文件来源检查返回无效结果。")
        ctx.extra.setdefault("_skip_tool_calls", {})[call.id] = True
        ctx.inputs.tool_result = output
        ctx.inputs.tool_msg = ToolMessage(content=json.dumps(output.data, ensure_ascii=False), tool_call_id=call.id)
