# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.query.trace_call_chain import VALID_DIRECTIONS
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext


class TraceCallPathsTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("trace_call_paths", "TraceCallPathsTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        symbol_id = str(inputs.get("symbol_id") or "").strip()
        if not symbol_id:
            return ToolOutput(success=False, error="symbol_id is required")
        direction = str(inputs.get("direction") or "").strip().lower()
        if not direction:
            return ToolOutput(
                success=False,
                error="direction is required: callers or callees",
            )
        if direction not in VALID_DIRECTIONS:
            return ToolOutput(
                success=False,
                error=f"direction must be one of {list(VALID_DIRECTIONS)}",
            )
        for key in ("max_depth", "max_paths"):
            value = inputs.get(key)
            if value is not None and not isinstance(value, (int, float, str)):
                return ToolOutput(success=False, error=f"{key} must be an integer")
        depth = inputs.get("max_depth")
        paths = inputs.get("max_paths")
        policy = self.policy
        return await self._invoke_service(
            lambda service: service.trace_call_chain(
                symbol_id,
                direction=direction,
                max_depth=policy.depth(None if depth == "" else depth),
                max_paths=policy.paths(None if paths == "" else paths),
                max_nodes=policy.nodes(inputs.get("max_nodes")),
                include_tests=bool(inputs.get("include_tests")),
            )
        )
