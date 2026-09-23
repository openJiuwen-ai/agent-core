# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The structured-output tool an agent calls to return a schema-conforming result.

The harness has no native structured-output / ``response_format`` mechanism, so
an agent that must produce schema-conforming output is given this tool with its
``ToolCard.input_params`` set to the exact JSON Schema the caller requested. The
LLM is instructed to finish by calling ``structured_output`` with the result
object; ``invoke`` validates the arguments against that schema and captures them
for the caller to read back. A schema violation raises, so the error
tool_message flows back to the model for a same-turn correction
(``StructuredOutputFinishRail`` force-finishes only successful calls).

Used by both swarmflow workers/sessions and tiny agents. One instance is
constructed per call (the schema differs each time), so the captured value is
single-use and lives on the instance.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import jsonschema

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


def describe_schema_requirements(schema_json: dict[str, Any] | None) -> str:
    """Render a compact per-level required-keys summary of a JSON Schema.

    Weak models read ``tools[].parameters`` poorly: the required keys of
    *nested* items (e.g. every element of an array) are exactly what they drop
    (the ``q_id`` failure). This walks the object/array nesting and spells the
    required keys out per level, so the requirement is impossible to miss
    whether it rides the tool description or the turn prompt. Returns ``""``
    when the schema declares no required keys anywhere.
    """
    if not isinstance(schema_json, dict):
        return ""
    lines: list[str] = []

    def walk(node: dict[str, Any], where: str) -> None:
        props = node.get("properties")
        if not isinstance(props, dict):
            return
        required = [r for r in node.get("required", []) if r in props]
        if required:
            lines.append(f"- {where or 'top level'}: required keys: {', '.join(required)}")
        for name, sub in props.items():
            if not isinstance(sub, dict):
                continue
            child = f"{where}.{name}" if where else name
            items = sub.get("items")
            if sub.get("type") == "array" and isinstance(items, dict):
                walk(items, f"{child}[]")
            else:
                walk(sub, child)

    walk(schema_json, "")
    if not lines:
        return ""
    return "Required structure (include every listed key at its level):\n" + "\n".join(lines)


class StructuredOutputTool(Tool):
    """A single-use tool that captures a worker's structured result.

    Follows the team tools' conventions: the description is resolved through the
    shared i18n ``Translator`` (``descs/<lang>/common/structured_output.md``) so it
    honours the worker's language, the requested JSON Schema becomes the tool's
    ``input_params``, and a schema-derived per-level required-keys summary
    (:func:`describe_schema_requirements`) is appended to the description (and
    exposed as ``required_structure`` for backends to repeat in the turn prompt)
    so nested required keys are impossible to miss.

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
        # Schema-derived required-keys summary rides the description so a weak
        # model that skims ``parameters`` still sees which keys are mandatory
        # at every nesting level; backends repeat it in the turn prompt via
        # the ``required_structure`` attribute.
        required_structure = describe_schema_requirements(schema_json)
        description = translator("structured_output")
        if required_structure:
            description = f"{description}\n{required_structure}"
        super().__init__(
            ToolCard(
                id=tool_id,
                name="structured_output",
                description=description,
            )
        )
        self.required_structure: str = required_structure
        self.card.input_params = schema_json or _DEFAULT_SCHEMA
        self.captured: dict[str, Any] | None = None
        self.called: bool = False

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Validate the submission against the schema, then capture it.

        A schema violation raises ``jsonschema.ValidationError`` instead of
        acknowledging: the failed call leaves ``captured``/``called`` unset and
        the error tool_message flows back to the model, which can correct and
        resubmit within the same turn.
        """
        jsonschema.validate(inputs, self.card.input_params)
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
    requests a force-finish as soon as ``structured_output`` is captured (i.e.
    the call succeeded), ending the round deterministically regardless of the
    model. A failed call (e.g. malformed arguments) does NOT force-finish, so
    the error tool_message reaches the model for self-correction. The backend
    reads the result off the :class:`StructuredOutputTool` instance, so the
    force-finish payload itself is irrelevant.
    """

    priority: int = 900

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Force-finish the round only when ``structured_output`` succeeded.

        A failed call (e.g. malformed JSON arguments that fail to parse) must
        NOT force-finish: the error tool_message needs to flow back to the
        model so it can self-correct. Force-finishing on failure would swallow
        the error and end the round with no structured result captured.
        """
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if inputs.tool_name != _STRUCTURED_OUTPUT_NAME:
            return
        if ctx.exception is not None:
            return
        ctx.request_force_finish({"accepted": True})


__all__ = [
    "StructuredOutputTool",
    "StructuredOutputFinishRail",
    "describe_schema_requirements",
]
