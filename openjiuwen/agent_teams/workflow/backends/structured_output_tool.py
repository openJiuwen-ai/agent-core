# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The structured-output tool a swarmflow worker calls to return its result.

The harness has no native structured-output / ``response_format`` mechanism, so
a worker that must produce schema-conforming output is given this tool with its
``ToolCard.input_params`` set to the exact JSON Schema the engine requested. The
LLM is instructed to finish by calling ``structured_output`` with the result
object; the call's arguments — validated against the schema by the model's
tool-use machinery — are captured here for the worker backend to read back.

One instance is constructed per ``agent()`` call (the schema differs each time),
so the captured value is single-use and lives on the instance.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.agent_teams.tools.locales import Translator, make_translator
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


class StructuredOutputTool(Tool):
    """A single-use tool that captures a worker's structured result.

    Follows the team tools' conventions: the description is resolved through the
    shared i18n ``Translator`` (``descs/<lang>/structured_output.md``) so it
    honours the worker's language, and the requested JSON Schema becomes the
    tool's ``input_params``.

    Args:
        schema_json: The JSON Schema the engine requested for this ``agent()``
            call. Becomes the tool's ``input_params`` so the model's tool-use
            layer constrains the arguments to the schema. ``None`` falls back to
            a generic ``{"result": str}`` schema.
        t: The language-bound translator used to resolve the description. When
            omitted a default (``cn``) translator is created.
        tool_id: Resource-manager id for the tool. Defaults to a stable id; when
            mounted on a harness the ability manager re-qualifies it per owner
            (``structured_output_{owner_id}``), so concurrent workers never
            collide — no per-call id is needed.
    """

    def __init__(
        self,
        schema_json: dict[str, Any] | None,
        t: Translator | None = None,
        *,
        tool_id: str = "swarmflow.structured_output",
    ) -> None:
        translator = t if t is not None else make_translator("cn")
        super().__init__(
            ToolCard(
                id=tool_id,
                name="structured_output",
                description=translator("structured_output"),
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
        raise NotImplementedError("StructuredOutputTool does not support streaming")


__all__ = ["StructuredOutputTool"]
