# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent-only control tool that queues a PA-11 window handoff."""

from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import ValidationError

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput

from ..execution.control import ExecutionControl
from ..prompts import WINDOW_SWITCH_GUIDANCE
from ..schema.window import WindowSeed, WindowSwitchRequest

NEW_CONTEXT_TOOL_NAME = "new_context"
_ACCEPTED = "已接收换窗请求；系统会在安全边界检查并尝试切换，接收不代表已切换。"
_INVALID = "换窗信息不符合要求，请检查必填目标及字段长度。"
_DUPLICATE = "本轮已经有一个换窗请求，本次没有覆盖之前的请求。"


class PersistentNewContextTool(Tool):
    """Model-facing control action; it does not write storage or switch context itself."""

    def __init__(self, control: ExecutionControl, *, trigger_kind: Literal["agent", "request_entry"] = "agent") -> None:
        if trigger_kind not in ("agent", "request_entry"):
            raise ValueError("new_context trigger must be bound by the host")
        super().__init__(
            ToolCard(
                id=NEW_CONTEXT_TOOL_NAME,
                name=NEW_CONTEXT_TOOL_NAME,
                description=(
                    "开始一个新的上下文窗口，继续当前任务，不重置工作空间、文件或已执行的操作。\n\n"
                    + WINDOW_SWITCH_GUIDANCE["cn"]
                    + "\n\n"
                    "提交简明的任务目标、已完成事项、下一步、关键约束和未确认事项，保留必要的证据引用。"
                    "旧窗口会归档，需要原始细节时可使用 search_session_history 检索。\n\n"
                    "调用只提交换窗请求；入口判断在正式回答前、执行中在本轮工具全部完成后，由系统"
                    "校验并尝试切换。不要把请求已接收当作"
                    "换窗已成功，也不要重复执行已完成的操作。"
                ),
                input_params=WindowSeed.model_json_schema(),
                parallel_safe=False,
                stateless=False,
            )
        )
        self._control = control
        self._trigger_kind = trigger_kind

    async def invoke(self, inputs: Any, **kwargs: Any) -> ToolOutput:
        try:
            seed = WindowSeed.model_validate(inputs)
        except (ValidationError, TypeError, ValueError):
            return ToolOutput(success=False, data={"status": "invalid_arguments"}, error=_INVALID)
        request = WindowSwitchRequest(trigger_kind=self._trigger_kind, seed=seed)
        if not self._control.queue_window_switch(request):
            return ToolOutput(success=False, data={"status": "already_requested"}, error=_DUPLICATE)
        return ToolOutput(success=True, data={"status": "accepted", "content": _ACCEPTED})

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)
