# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Base abstractions shared by all team tools."""

import json
from abc import ABC
from typing import Any, AsyncIterator

from pydantic import PrivateAttr

from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class MappedToolOutput(ToolOutput):
    """ToolOutput with custom string representation for LLM consumption.

    The ability_manager converts tool results to LLM messages via str(result).
    This subclass overrides __str__ to return model-optimized text instead of
    Pydantic's default representation.
    """

    _mapped_content: str = PrivateAttr(default="")

    @classmethod
    def from_output(cls, output: ToolOutput, mapped_content: str) -> "MappedToolOutput":
        """Create a MappedToolOutput from an existing ToolOutput."""
        obj = cls(success=output.success, data=output.data, error=output.error)
        obj._mapped_content = mapped_content
        return obj

    def __str__(self) -> str:
        return self._mapped_content


class TeamTool(Tool, ABC):
    """Base class for team tools with model-facing result mapping.

    Subclasses override map_result() to control what the LLM sees.
    Default implementation returns JSON for success, error text for failure.
    """

    def map_result(self, output: ToolOutput) -> str:
        """Map tool output to model-facing text.

        Override in subclasses for custom formatting. The returned string
        becomes the ToolMessage.content that the LLM receives.
        """
        if not output.success:
            return output.error or "Operation failed"
        if output.data is None:
            return "OK"
        return json.dumps(output.data, ensure_ascii=False)

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError("TeamTool does not support streaming")
