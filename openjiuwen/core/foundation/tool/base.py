# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

import inspect
import json
from abc import ABCMeta, abstractmethod
from functools import wraps
from typing import Any, AsyncIterator, Dict, Type
from typing import TypeVar
from pydantic import BaseModel, Field
from pydantic import PrivateAttr

from openjiuwen.core.common import BaseCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.exposure import ToolExposure
from openjiuwen.core.foundation.tool.schema import ToolInfo, ToolOutput

Input = TypeVar('Input', contravariant=True)
Output = TypeVar('Output', contravariant=True)

EMPTY_SUCCESS_TEXT = "Tool succeeded with empty output."
EMPTY_FAILURE_TEXT = "Tool failed without an error message."


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def render_payload_text(data: Any) -> str:
    """Render a ``ToolOutput.data`` payload as model-facing text.

    A string payload is used as-is. A dict carrying a ``content`` key renders
    only that value, so sibling keys stay program-only (e.g. ``multimodal``
    image items delivered by a separate message). Any other payload, or a
    non-string ``content``, is serialized as JSON: a readable last resort for
    tools that have not declared their own rendering.

    Args:
        data: The ``ToolOutput.data`` value.

    Returns:
        The rendered text; empty when there is nothing to show.
    """
    if data is None:
        return ""
    if isinstance(data, dict) and "content" in data:
        data = data["content"]
        if data is None:
            return ""
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, default=_json_default)


def render_tool_output(output: ToolOutput) -> str:
    """Render a ``ToolOutput`` with the default model-facing rules.

    Success renders ``data["content"]``; failure renders ``error``, falling
    back to the payload when the error is empty. An empty rendering becomes a
    short placeholder so the model never receives a blank tool result. Tools
    that customize ``Tool.render_for_llm`` delegate here for the cases they
    do not special-case; function-backed tools without a subclass reuse it.

    Args:
        output: The structured tool result.

    Returns:
        The model-facing text, never empty.
    """
    if output.success:
        return render_payload_text(output.data) or EMPTY_SUCCESS_TEXT
    return output.error or render_payload_text(output.data) or EMPTY_FAILURE_TEXT


class ToolCard(BaseCard):
    # Registration policy bookkeeping. These private attributes never enter
    # the model-facing ToolInfo/schema.
    _exposure_declared: bool | None = PrivateAttr(default=None)

    exposure: ToolExposure = Field(
        default=ToolExposure.DIRECT,
        description=(
            "Whether the tool is exposed directly to the model or deferred "
            "until it is discovered by tool_search."
        ),
    )
    input_params: Dict[str, Any] | Type[BaseModel] = Field(default_factory=dict)
    properties: Dict[str, Any] = Field(default_factory=dict)
    parallel_safe: bool = Field(
        default=True,
        description=(
            "Whether this tool can safely execute concurrently with other tool "
            "calls emitted in the same assistant turn. Tools that mutate shared "
            "state or external resources should set this to False."
        ),
    )
    stateless: bool = Field(
        default=False,
        description=(
            "Whether the tool holds no per-agent/session state. Stateless tools "
            "(module-level singletons) are shared across agents under their bare "
            "id; stateful tools are owned exclusively by one agent and get an "
            "agent-qualified id at registration."
        ),
    )
    idempotent: bool = Field(
        default=False,
        description=(
            "Whether repeated invocations with the same inputs have no additional "
            "side effects. Non-idempotent tools (write, shell, spawn) are exempt "
            "from outer call-level timeouts and are never retried by resilience rails. "
            "Defaults to False for secure-by-default: new tools must explicitly opt-in "
            "to idempotency."
        ),
    )

    def tool_info(self):
        return ToolInfo(name=self.name, description=self.description, parameters=self.input_params)

    def get_exposure_declared(self) -> bool | None:
        """Return whether registration already resolved this card's exposure."""
        return self._exposure_declared

    def set_exposure_declared(self, value: bool | None) -> None:
        """Record that the registration policy has inspected this card."""
        self._exposure_declared = value


class _ToolMeta(ABCMeta):
    def __call__(cls, *args, **kwargs):
        instance = super().__call__(*args, **kwargs)
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.runner.callback.events import ToolCallEvents
        _fw = Runner.callback_framework

        _original_invoke = instance.invoke

        @wraps(_original_invoke)
        async def _lifecycle_invoke(*a, **kw):
            await _fw.trigger(ToolCallEvents.TOOL_CALL_STARTED,
                              tool_name=instance.card.name,
                              tool_id=instance.card.id,
                              inputs=(a, kw))
            try:
                result = await _original_invoke(*a, **kw)
                await _fw.trigger(ToolCallEvents.TOOL_CALL_FINISHED,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  inputs=(a, kw),
                                  result=result)
                return result
            except Exception as e:
                await _fw.trigger(ToolCallEvents.TOOL_CALL_ERROR,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  error=e)
                raise

        instance.invoke = _lifecycle_invoke

        _original_stream = instance.stream

        if inspect.isasyncgenfunction(_original_stream):
            @wraps(_original_stream)
            async def _lifecycle_stream(*a, **kw):
                await _fw.trigger(ToolCallEvents.TOOL_CALL_STARTED,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  inputs=(a, kw))
                try:
                    async for chunk in _original_stream(*a, **kw):
                        await _fw.trigger(ToolCallEvents.TOOL_RESULT_RECEIVED,
                                          tool_name=instance.card.name,
                                          tool_id=instance.card.id,
                                          inputs=(a, kw),
                                          result=chunk)
                        yield chunk
                    await _fw.trigger(ToolCallEvents.TOOL_CALL_FINISHED,
                                      tool_name=instance.card.name,
                                      tool_id=instance.card.id)
                except Exception as e:
                    await _fw.trigger(ToolCallEvents.TOOL_CALL_ERROR,
                                      tool_name=instance.card.name,
                                      tool_id=instance.card.id,
                                      error=e)
                    raise

            instance.stream = _lifecycle_stream
        _extra = {
            "tool_name": instance.card.name,
            "tool_info": instance.card.tool_info(),
        }
        fn = instance.invoke
        fn = _fw.emit_before(ToolCallEvents.TOOL_INVOKE_INPUT, extra_kwargs=_extra)(fn)
        fn = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_INVOKE_INPUT,
            output_event=ToolCallEvents.TOOL_INVOKE_OUTPUT,
        )(fn)
        fn = _fw.emit_after(ToolCallEvents.TOOL_INVOKE_OUTPUT, extra_kwargs=_extra)(fn)
        instance.invoke = fn

        fn = instance.stream
        fn = _fw.emit_before(ToolCallEvents.TOOL_STREAM_INPUT, extra_kwargs=_extra)(fn)
        fn = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_STREAM_INPUT,
            output_event=ToolCallEvents.TOOL_STREAM_OUTPUT,
        )(fn)
        fn = _fw.emit_after(ToolCallEvents.TOOL_STREAM_OUTPUT, item_key="result", extra_kwargs=_extra)(fn)
        instance.stream = fn
        return instance


class Tool(metaclass=_ToolMeta):
    """tool class that defined the data types and content for LLM modules"""

    def __init__(self, card: ToolCard):
        """Constructs a new tool instance with the given configuration.

        Args:
            card: ToolCard configuration defining tool behavior and parameters

        Note:
            The tool card is stored internally and used for validation and
            metadata purposes throughout the tool's lifecycle.
        """
        if card is None:
            raise build_error(StatusCode.TOOL_CARD_INVALID, card=card, reason="card is None")
        if not card.id:
            raise build_error(StatusCode.TOOL_CARD_INVALID, card=card, reason="card is is None or empty")
        self._card = card

    @property
    def card(self) -> ToolCard:
        return self._card

    def is_parallel_safe(self) -> bool:
        """Return whether this tool may run concurrently with sibling tool calls."""
        return bool(getattr(self.card, "parallel_safe", True))

    # An override hook: subclasses render from their own state, and callers
    # dispatch it on the instance.
    # pylint: disable-next=add-staticmethod-or-classmethod-decorator
    def render_for_llm(self, output: Any) -> str:
        """Render an ``invoke`` result into the plain text the model reads.

        The ability layer calls this once per tool call when it builds the
        tool-result message; the structured result itself is kept intact for
        rails, events and logs. Override it to control the model-facing text.

        The default renders a ``ToolOutput`` through ``render_tool_output``:
        ``data["content"]`` on success and ``error`` on failure. Results that
        are not a ``ToolOutput`` (plain functions, REST APIs) render as
        ``str(output)``.

        Args:
            output: The value returned by ``invoke``.

        Returns:
            The model-facing text of the tool result.
        """
        if not isinstance(output, ToolOutput):
            return str(output)
        return render_tool_output(output)

    @abstractmethod
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Execute the tool with provided inputs and return final result.

        This method performs complete tool execution in a single call,
        processing all inputs and returning the final output when the
        operation is fully completed.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters such as timeout,
                     retry policies, or tool-specific options

        Returns:
            Output: The complete result of tool execution

        """
        pass

    @abstractmethod
    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Execute the tool and stream incremental results.

        This method supports long-running operations by yielding partial
        results as they become available, enabling real-time processing
        and progress tracking.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters for streaming behavior

        Yields:
            Output: Incremental results during tool execution

        """
        pass
