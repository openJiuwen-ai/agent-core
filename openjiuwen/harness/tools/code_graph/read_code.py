# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext


class ReadCodeTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("read_code", "ReadCodeTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        path = str(inputs.get("path") or inputs.get("file") or "").strip()
        if not path:
            return ToolOutput(success=False, error="path is required")
        start_line = inputs.get("start_line")
        end_line = inputs.get("end_line")
        return await self._invoke_service(
            lambda service: service.read_code(
                path,
                start_line=int(start_line or 1),
                end_line=None if end_line is None else int(end_line),
            )
        )
