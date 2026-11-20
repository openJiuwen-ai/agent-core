# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Client-executed tools.

A client tool is offered to the model like any other tool, but the connected
client (for example a document editor front end) executes it. When the model
calls it and every ``before_tool_call`` rail has let the call through, the
ability manager pauses the call with a ``client_tool`` interrupt carrying a
``ClientToolRequest``. The host runs the call on the client and resumes with
the result (``InteractiveInput.answer(interrupt_id, result)``); the result then
becomes the call's ordinary tool output and goes through ``after_tool_call``.

Declare a client tool with ``ToolCard(execution="client")``, or subclass
:class:`ClientTool` to shape the request (``build_request``) and the output
(``complete``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ValidationError, field_validator

from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.foundation.tool.execution import ToolExecution
from openjiuwen.core.foundation.tool.schema import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.session.agent import Session
    from openjiuwen.core.single_agent.base import BaseAgent
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


class ClientToolStatus(str, Enum):
    """How a client tool call ended, as reported by the host."""

    COMPLETED = "completed"
    ERROR = "error"
    # The host gave up waiting for the client.
    TIMEOUT = "timeout"
    # The client that runs the tool disconnected.
    CLIENT_GONE = "client_gone"
    # The call was cancelled before the client returned a result.
    CANCELLED = "cancelled"


# Statuses after which the model may simply call the tool again.
RETRYABLE_CLIENT_TOOL_STATUSES = frozenset({
    ClientToolStatus.TIMEOUT,
    ClientToolStatus.CLIENT_GONE,
    ClientToolStatus.CANCELLED,
})

ContentBlocks = List[Dict[str, Any]]


class ClientToolResult(BaseModel):
    """The answer a host sends for a paused client tool call.

    Attributes:
        status: How the call ended. Anything but ``completed`` is a failure.
        content: Text, or content blocks: ``{"type": "text", "text": ...}`` and
            images as ``{"type": "image", "data_url": "data:image/png;base64,..."}``
            or ``{"type": "image", "source": {"type": "base64", "media_type":
            "image/png", "data": ...}}``.
        is_error: The client ran the call and it failed.
        data: Structured result for rails and hosts; not shown to the model.
    """

    status: ClientToolStatus = ClientToolStatus.COMPLETED
    content: Union[str, ContentBlocks] = ""
    is_error: bool = False
    data: Any = None

    @field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, dict):
            # A single content block, or a structured value shown as JSON.
            return [value] if value.get("type") in _BLOCK_TYPES else _json_text(value)
        if isinstance(value, list) and not all(isinstance(item, dict) for item in value):
            return _json_text(value)
        return value

    @property
    def failed(self) -> bool:
        """Whether the call did not succeed: an error result or any status other than completed."""
        return self.is_error or self.status != ClientToolStatus.COMPLETED

    @classmethod
    def coerce(cls, value: Any) -> "ClientToolResult":
        """Build a result from whatever the host answered.

        - A ``ClientToolResult`` is used as is.
        - A dict is this envelope when every key is one of ``status``,
          ``content``, ``is_error`` and ``data``, and the values are valid.
        - Any other dict is the result as a whole: the model sees it as JSON
          and ``data`` keeps it. It failed only if ``is_error`` is true or
          ``status`` is a status other than ``completed``.
        - A string is the result text, a list is content blocks, ``None`` is an
          empty result, and any other value is shown as JSON.
        """
        if isinstance(value, ClientToolResult):
            return value
        if value is None or isinstance(value, (str, list)):
            return cls(content=value)
        if not isinstance(value, dict):
            return cls(content=_json_text(value), data=value)
        if set(value) <= _ENVELOPE_KEYS:
            try:
                return cls.model_validate(value)
            except ValidationError:
                pass
        status = next((status for status in ClientToolStatus if status == value.get("status")), None)
        return cls(
            status=status or ClientToolStatus.COMPLETED,
            is_error=value.get("is_error") is True,
            content=_json_text(value),
            data=value,
        )


_BLOCK_TYPES = ("text", "image", "image_url")
_ENVELOPE_KEYS = frozenset({"status", "content", "is_error", "data"})


@dataclass
class ClientToolContext:
    """Where a client tool call happens, passed to ``build_request`` and ``complete``.

    Attributes:
        tool_call: The call, with its final arguments.
        session: The agent session (``get_session_id()``, ``get_state()``,
            ``update_state()``), e.g. to find the document bound to the
            conversation or keep per-call state between the two hooks.
        agent: The agent running the call.
        callback_context: The rail callback context of this call.
    """

    tool_call: "ToolCall"
    session: Optional["Session"]
    agent: "BaseAgent"
    callback_context: "AgentCallbackContext"

    @property
    def session_id(self) -> str:
        """The conversation's session id, or ``""`` without a session."""
        return self.session.get_session_id() if self.session is not None else ""


class ClientToolUnavailable(Exception):
    """Raise from ``ClientTool.build_request`` to fail the call without contacting the client.

    For example: no document is open, the open document cannot run this tool,
    or the input is invalid. The message becomes the call's tool error.
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


def client_tool_unavailable_output(error: ClientToolUnavailable) -> ToolOutput:
    """The tool output of a call that ``build_request`` refused."""
    return ToolOutput(
        success=False,
        error=error.message,
        data={"status": "unavailable", "retryable": error.retryable},
    )


def as_client_tool_output(output: Any, result: ClientToolResult, tool_name: str = "") -> ToolOutput:
    """Make sure a client call ends with a ``ToolOutput``.

    ``ClientTool.complete`` may return plain text or another value; it then
    replaces the text of the default output, which keeps the images, status
    and retry hints of ``result``.
    """
    if isinstance(output, ToolOutput):
        return output
    base = default_client_output(result, tool_name=tool_name)
    text = output if isinstance(output, str) else _json_text(output)
    data = dict(base.data or {})
    data["content"] = text
    if base.success:
        return base.model_copy(update={"data": data})
    return base.model_copy(update={"data": data, "error": text or base.error})


def is_client_tool(card: Optional[ToolCard]) -> bool:
    """Whether calls to ``card`` are executed by the client."""
    return card is not None and card.execution == ToolExecution.CLIENT


def default_client_output(result: ClientToolResult, tool_name: str = "") -> ToolOutput:
    """Turn a client result into the tool's ``ToolOutput``.

    Text becomes ``data["content"]`` (what the model reads); images go to
    ``data["multimodal"]``, which the agent forwards to the model after the
    tool message. A failed call is a normal failed result (``success=False``)
    with ``data["status"]`` and ``data["retryable"]`` for rails and hosts.
    """
    text, images = _split_content(result.content)
    data: Dict[str, Any] = {"content": text}
    if images:
        label = f"{tool_name} result" if tool_name else "client tool result"
        data["multimodal"] = [
            {"type": "image", "data_url": url, "source": "client", "source_path": label} for url in images
        ]
    if result.data is not None:
        data["result"] = result.data
    if not result.failed:
        return ToolOutput(success=True, data=data)
    status = result.status if result.status != ClientToolStatus.COMPLETED else ClientToolStatus.ERROR
    data["status"] = status.value
    data["retryable"] = status in RETRYABLE_CLIENT_TOOL_STATUSES
    return ToolOutput(success=False, error=_failure_text(status, tool_name or "client tool", text), data=data)


class ClientTool(Tool):
    """Base class for tools the client executes.

    ``invoke`` is never called by the agent. Override :meth:`build_request` to
    tell the client what to do (it receives the final arguments, after every
    rail ran) and :meth:`complete` to validate or reshape the client's result.
    Both receive a :class:`ClientToolContext` and may be ``async``.
    """

    def __init__(self, card: ToolCard):
        if not is_client_tool(card):
            # Mark a copy; the caller's card is left untouched.
            card = card.model_copy(update={"execution": ToolExecution.CLIENT})
        super().__init__(card)

    def build_request(self, arguments: Dict[str, Any], context: ClientToolContext) -> Dict[str, Any]:
        """Return what the client needs to run the call (sent as ``client_request``).

        Raise :class:`ClientToolUnavailable` to fail the call with a tool error
        instead of pausing it for the client.
        """
        return {}

    def complete(self, arguments: Dict[str, Any], result: ClientToolResult, context: ClientToolContext) -> Any:
        """Turn the client's result into this call's tool output.

        Return a ``ToolOutput``, or text or another value that replaces the
        text of the default output. May be a coroutine.
        """
        return default_client_output(result, tool_name=self.card.name)

    async def invoke(self, inputs: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"'{self.card.name}' is a client tool; the connected client executes it")

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, **kwargs)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _split_content(content: Union[str, ContentBlocks]) -> Tuple[str, List[str]]:
    if isinstance(content, str):
        return content, []
    texts: List[str] = []
    images: List[str] = []
    for block in content:
        block_type = block.get("type")
        if block_type == "text":
            texts.append(str(block.get("text") or ""))
        elif block_type in ("image", "image_url"):
            url = _image_data_url(block)
            if url:
                images.append(url)
            else:
                texts.append("[image omitted: only base64 data images are supported]")
        else:
            texts.append(_json_text(block))
    return "\n".join(text for text in texts if text), images


def _image_data_url(block: Dict[str, Any]) -> str:
    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        image_url = image_url.get("url")
    for candidate in (block.get("data_url"), image_url):
        if isinstance(candidate, str) and candidate.startswith("data:image/"):
            return candidate
    source = block.get("source")
    if isinstance(source, dict) and source.get("type") == "base64" and source.get("data"):
        media_type = source.get("media_type")
        if isinstance(media_type, str) and media_type.startswith("image/"):
            return f"data:{media_type};base64,{source['data']}"
    return ""


def _failure_text(status: ClientToolStatus, tool_name: str, text: str) -> str:
    if status == ClientToolStatus.TIMEOUT:
        message = (
            f"Client tool '{tool_name}' did not return a result in time. It may or may not have taken "
            "effect; check the current state before calling it again."
        )
    elif status == ClientToolStatus.CLIENT_GONE:
        message = (
            f"Client tool '{tool_name}' could not finish: the client that runs it disconnected. It may or "
            "may not have taken effect; check the current state, then call it again once the client is back."
        )
    elif status == ClientToolStatus.CANCELLED:
        message = (
            f"Client tool '{tool_name}' was cancelled before the client returned a result. It may or may not "
            "have taken effect; check the current state before calling it again."
        )
    else:
        return text or f"Client tool '{tool_name}' failed without an error message."
    return f"{message}\n{text}" if text else message


__all__ = [
    "ClientTool",
    "ClientToolContext",
    "ClientToolUnavailable",
    "client_tool_unavailable_output",
    "ClientToolResult",
    "ClientToolStatus",
    "RETRYABLE_CLIENT_TOOL_STATUSES",
    "as_client_tool_output",
    "default_client_output",
    "is_client_tool",
]
