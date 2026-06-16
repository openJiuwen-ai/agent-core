# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for evolution tool adapters."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput


class BaseEvolutionTool(Tool):
    """Common behavior for agent-facing evolution tools."""

    tool_name = ""
    tool_id = ""
    failure_message = "evolution tool failed"

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[ToolOutput]:
        if False:
            yield ToolOutput(success=False)

    @staticmethod
    def inputs_dict(inputs: Any) -> dict[str, Any]:
        return dict(inputs or {}) if isinstance(inputs, dict) else {}

    def to_output(self, result: dict[str, Any]) -> ToolOutput:
        success = bool(result.get("success", True))
        if success:
            return ToolOutput(success=True, data=result)
        errors = result.get("errors") or []
        error = "; ".join(str(error) for error in errors) or self.failure_message
        return ToolOutput(success=False, data=result, error=error)

    @staticmethod
    def failure(exc: Exception) -> ToolOutput:
        return ToolOutput(success=False, error=str(exc))


__all__ = ["BaseEvolutionTool"]
