# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The structured-output tool an agent calls to return a schema-conforming result.

The harness has no native structured-output / ``response_format`` mechanism, so
an agent that must produce schema-conforming output is given this tool with its
``ToolCard.input_params`` set to the exact JSON Schema the caller requested. The
LLM is instructed to finish by calling ``structured_output`` with the result
object; the call's arguments — validated against the schema by the model's
tool-use machinery — are captured here for the caller to read back.

Used by both swarmflow workers/sessions and tiny agents. One instance is
constructed per call (the schema differs each time), so the captured value is
single-use and lives on the instance.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

# The tool name the model calls; the ability manager re-qualifies the resource id
# per owner but the card name (what the LLM emits in a tool call) stays this.
_STRUCTURED_OUTPUT_NAME = "structured_output"

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


class StructuredOutputFinishRail(AgentRail):
    """End the ReAct round the moment ``structured_output`` is captured.

    A swarmflow turn is "do the work, submit the result via ``structured_output``,
    done". The submission tool's acknowledgement (``{"accepted": True}``) carries
    no "stop now" signal, and the schema-turn prompt forbids a plain-text final
    answer — so a weak model keeps re-emitting the same ``structured_output`` call
    until it happens to stop, burning iterations and tokens.

    This rail makes the terminal action terminal: an ``after_tool_call`` hook
    requests a force-finish as soon as ``structured_output`` is invoked, ending
    the round deterministically regardless of the model. The backend reads the
    result off the :class:`StructuredOutputTool` instance, so the force-finish
    payload itself is irrelevant.
    """

    priority: int = 900

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Force-finish the round when the captured tool was ``structured_output``."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if inputs.tool_name != _STRUCTURED_OUTPUT_NAME:
            return
        ctx.request_force_finish({"accepted": True})


__all__ = ["StructuredOutputTool", "StructuredOutputFinishRail"]
