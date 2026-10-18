# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent-only control tool that queues a PA-11 window handoff."""

from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput

from ..execution.control import ExecutionControl
from ..schema.window import WindowSeed, WindowSwitchRequest

NEW_CONTEXT_TOOL_NAME = "new_context"
_ACCEPTED = "已接收换窗请求；系统会在本轮所有工具完成后检查并切换。"
_INVALID = "换窗信息不符合要求，请检查必填目标及字段长度。"
_DUPLICATE = "本轮已经有一个换窗请求，本次没有覆盖之前的请求。"


class PersistentNewContextTool(Tool):
    """Model-facing control action; it does not write storage or switch context itself."""

    def __init__(self, control: ExecutionControl) -> None:
        super().__init__(
            ToolCard(
                id=NEW_CONTEXT_TOOL_NAME,
                name=NEW_CONTEXT_TOOL_NAME,
                description=(
                    "当当前阶段适合整理任务并在新上下文继续时调用。填写目标、已完成事项、下一步、"
                    "约束与决定、待确认问题，以及尚未确认的副作用和证据引用。"
                    "这只是请求；当前批次的其他工具会先完成，系统随后在安全边界校验并尝试换窗。"
                    "换窗要点不是副作用成功的证明。"
                ),
                input_params=WindowSeed.model_json_schema(),
                parallel_safe=False,
                stateless=False,
            )
        )
        self._control = control

    async def invoke(self, inputs: Any, **kwargs: Any) -> ToolOutput:
        try:
            seed = WindowSeed.model_validate(inputs)
        except (ValidationError, TypeError, ValueError):
            return ToolOutput(success=False, data={"status": "invalid_arguments"}, error=_INVALID)
        request = WindowSwitchRequest(trigger_kind="agent", seed=seed)
        if not self._control.queue_window_switch(request):
            return ToolOutput(success=False, data={"status": "already_requested"}, error=_DUPLICATE)
        return ToolOutput(success=True, data={"status": "accepted", "content": _ACCEPTED})

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)
