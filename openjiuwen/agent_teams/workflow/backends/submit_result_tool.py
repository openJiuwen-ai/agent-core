# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The structured-output tool a swarmflow worker calls to return its result.

The harness has no native structured-output / ``response_format`` mechanism, so
a worker that must produce schema-conforming output is given this tool with its
``ToolCard.input_params`` set to the exact JSON Schema the engine requested. The
LLM is instructed to finish by calling ``submit_result`` with the result object;
the call's arguments — validated against the schema by the model's tool-use
machinery — are captured here for the worker backend to read back.

One instance is constructed per ``agent()`` call (the schema differs each time),
so the captured value is single-use and lives on the instance.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput

# Generic fallback schema used when a caller constructs the tool without one.
# A worker that needs free text never gets this tool at all — the backend only
# attaches it when the engine passed a real schema — but keeping a valid default
# means the ToolCard is always well-formed.
_DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"result": {"type": "string", "description": "The final result."}},
    "required": ["result"],
}

_DESCRIPTION = (
    "Submit your final structured result. Call this EXACTLY ONCE when the task "
    "is complete, passing the result object that conforms to this tool's input "
    "schema. Do not emit the result as plain text — it is only captured through "
    "this tool call. After calling it, stop."
)


class SubmitResultTool(Tool):
    """A single-use tool that captures a worker's structured result.

    Args:
        schema_json: The JSON Schema the engine requested for this ``agent()``
            call. Becomes the tool's ``input_params`` so the model's tool-use
            layer constrains the arguments to the schema. ``None`` falls back to
            a generic ``{"result": str}`` schema.
        tool_id: Resource-manager id for the tool. Defaults to a stable id; the
            worker backend qualifies it per call to avoid collisions when many
            workers run concurrently in one process.
    """

    def __init__(
        self,
        schema_json: dict[str, Any] | None,
        *,
        tool_id: str = "swarmflow.submit_result",
    ) -> None:
        super().__init__(
            ToolCard(
                id=tool_id,
                name="submit_result",
                description=_DESCRIPTION,
            )
        )
        self.card.input_params = schema_json or _DEFAULT_SCHEMA
        self.captured: dict[str, Any] | None = None
        self.called: bool = False

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Capture the structured result and acknowledge the submission."""
        self.captured = inputs
        self.called = True
        return ToolOutput(success=True, data={"accepted": True})

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        """Streaming is not supported; workers call ``invoke`` once."""
        raise NotImplementedError("SubmitResultTool does not support streaming")


__all__ = ["SubmitResultTool"]
