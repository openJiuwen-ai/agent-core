from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.content_sanitize import sanitize_value_for_text
from openjiuwen.core.context_engine.processor.base import _invoke_via_stream
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage

# Summaries must describe images as text: the compression model receives no
# base64 pixels. A single screenshot priced as text would balloon the summary
# request by tens of thousands of tokens of noise the summarizer cannot use.
_SUMMARY_IMAGE_NOTE = (
    "[Image attached: its visual payload is excluded from this summary request. "
    "Summarize it from the surrounding text and any image reference lines.]"
)

_IMAGE_BLOCK_TYPES = ("image", "image_url", "input_image")


def _is_image_content_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = str(block.get("type") or "").strip().lower()
    if block_type in _IMAGE_BLOCK_TYPES:
        return True
    return "image_url" in block or "image" in block


def _image_block_summary_text(block: dict) -> str:
    """Write an image block as text, keeping stamped dimensions when present."""

    width = block.get("width")
    height = block.get("height")
    if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
        return (
            f"[Image attached ({width}x{height} px): its visual payload is excluded "
            "from this summary request. Summarize it from the surrounding text and "
            "any image reference lines.]"
        )
    return _SUMMARY_IMAGE_NOTE


def _content_images_as_text(content: Any) -> tuple[Any, bool]:
    """Return content with image blocks/data URLs swapped for text, plus a changed flag."""

    if isinstance(content, str):
        # Fast bail: a data URL cannot hide in a string without "base64".
        if "base64" not in content.lower():
            return content, False
        sanitized = sanitize_value_for_text(content)
        return sanitized, sanitized != content
    if not isinstance(content, list):
        return content, False
    parts: list[Any] = []
    changed = False
    for block in content:
        if _is_image_content_block(block):
            parts.append({"type": "text", "text": _image_block_summary_text(block)})
            changed = True
        else:
            parts.append(block)
    if not changed:
        return content, False
    return parts, True


def _message_images_as_text(message: BaseMessage) -> BaseMessage:
    """Copy *message* with its images written as text; the original stays untouched.

    History keeps its base64 blocks (they are re-used by later vision-capable
    requests); only the summary request carries the text stand-in.
    """

    if isinstance(message, dict):
        new_content, changed = _content_images_as_text(message.get("content"))
        if not changed:
            return message
        clone = dict(message)
        clone["content"] = new_content
        return clone
    new_content, changed = _content_images_as_text(getattr(message, "content", None))
    if not changed:
        return message
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"content": new_content})
    clone = message.__class__.__new__(message.__class__)
    clone.__dict__.update(message.__dict__)
    clone.content = new_content
    return clone


class CompressionErrorKind(str, Enum):
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    SERVER_UNSTABLE = "server_unstable"
    API_REQUEST_ERROR = "api_request_error"
    UNKNOWN = "unknown"


class CompressionError(Exception):
    """Structured error raised when a compression model call fails."""

    def __init__(
        self,
        *,
        kind: CompressionErrorKind,
        message: str,
        original_error: BaseException,
    ) -> None:
        self.kind = kind
        self.message = message
        self.original_error = original_error
        super().__init__(message)

    @property
    def is_context_overflow(self) -> bool:
        return self.kind == CompressionErrorKind.CONTEXT_OVERFLOW


_CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "max context",
    "token limit",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "request too large",
    "413",
)

_RATE_LIMIT_MARKERS = ("rate_limit", "rate limit", "too many requests", "429")
_AUTHENTICATION_MARKERS = ("authentication", "unauthorized", "invalid api key", "api key invalid", "401", "403")
_TIMEOUT_MARKERS = ("timeout", "timed out", "read timeout", "connect timeout")
_SERVER_UNSTABLE_MARKERS = (
    "internal server error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "temporarily unavailable",
    "500",
    "502",
    "503",
    "504",
)
_API_REQUEST_MARKERS = ("bad request", "invalid request", "400")


@dataclass(frozen=True)
class CompressionRequest:
    prompt: str
    context_messages: list[BaseMessage] = field(default_factory=list)
    system_messages: list[BaseMessage] = field(default_factory=list)
    tools: list[Any] | None = None
    exclude_recent_messages: int = 0
    output_parser: Any = None

    @classmethod
    def from_context_window(
        cls,
        *,
        prompt: str,
        context_window: ContextWindow,
        exclude_recent_messages: int = 0,
        output_parser: Any = None,
    ) -> "CompressionRequest":
        return cls(
            prompt=prompt,
            system_messages=list(context_window.system_messages or []),
            context_messages=list(context_window.context_messages or []),
            tools=list(context_window.tools or []),
            exclude_recent_messages=exclude_recent_messages,
            output_parser=output_parser,
        )


@dataclass(frozen=True)
class CompressionResult:
    """Normalized compaction response while preserving raw model response access."""

    response: Any
    usage: Any = None
    error: Exception | None = None

    @property
    def content(self) -> str:
        return getattr(self.response, "content", "") or ""

    def __getattr__(self, item: str) -> Any:
        return getattr(self.response, item)


class CompressionExecutor:
    """Shared model invocation wrapper for compaction calls using main-agent prefix context."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self._last_response: Any = None

    @property
    def last_response(self) -> Any:
        return self._last_response

    async def invoke(self, request: CompressionRequest) -> CompressionResult:
        messages = self.build_messages(request)
        kwargs: dict[str, Any] = {"messages": messages, "tools": request.tools}
        if request.output_parser is not None:
            kwargs["output_parser"] = request.output_parser
        try:
            response = await _invoke_via_stream(self._model, **kwargs)
        except Exception as exc:
            raise classify_compression_error(exc) from exc
        self._last_response = response
        return CompressionResult(
            response=response,
            usage=getattr(response, "usage_metadata", None) or getattr(response, "usage", None),
        )

    @staticmethod
    def build_context_messages(request: CompressionRequest) -> list[BaseMessage]:
        """Build the seed context used by forked-agent executors.

        ``build_messages`` appends the compression prompt as a user message,
        which is correct for a direct model call.  A ReAct agent receives the
        prompt through ``agent.invoke`` instead, so agent executors need the
        system/context prefix without duplicating the prompt.

        Images are written as text: the summary request carries no base64
        pixels (bloat for the compression model, unusable as summary input).
        Message copies are returned; persisted history keeps its image blocks.
        """
        context_messages = list(request.context_messages)
        if request.exclude_recent_messages > 0:
            keep_count = max(len(context_messages) - request.exclude_recent_messages, 0)
            context_messages = context_messages[:keep_count]
        return [
            _message_images_as_text(message)
            for message in [*list(request.system_messages or []), *context_messages]
        ]

    @staticmethod
    def build_messages(request: CompressionRequest) -> list[BaseMessage]:
        return [
            *CompressionExecutor.build_context_messages(request),
            UserMessage(content=request.prompt),
        ]


def classify_compression_error(error: BaseException) -> CompressionError:
    message = _error_text(error)
    return CompressionError(
        kind=_classify_compression_error_kind(error, message),
        message=message or error.__class__.__name__,
        original_error=error,
    )


def _classify_compression_error_kind(error: BaseException, message: str) -> CompressionErrorKind:
    normalized = message.lower()
    status_code = _extract_status_code(error)
    if _contains_any(normalized, _CONTEXT_OVERFLOW_MARKERS):
        return CompressionErrorKind.CONTEXT_OVERFLOW
    if status_code == 413:
        return CompressionErrorKind.CONTEXT_OVERFLOW
    if _contains_any(normalized, _RATE_LIMIT_MARKERS) or status_code == 429:
        return CompressionErrorKind.RATE_LIMIT
    if _contains_any(normalized, _AUTHENTICATION_MARKERS) or status_code in {401, 403}:
        return CompressionErrorKind.AUTHENTICATION
    if isinstance(error, TimeoutError) or _contains_any(normalized, _TIMEOUT_MARKERS):
        return CompressionErrorKind.TIMEOUT
    if _contains_any(normalized, _SERVER_UNSTABLE_MARKERS) or status_code in {500, 502, 503, 504}:
        return CompressionErrorKind.SERVER_UNSTABLE
    if _contains_any(normalized, _API_REQUEST_MARKERS) or status_code == 400:
        return CompressionErrorKind.API_REQUEST_ERROR
    return CompressionErrorKind.UNKNOWN


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _error_text(error: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        status = getattr(current, "status", None)
        if status is not None:
            parts.append(str(status))
            parts.append(str(getattr(status, "name", "")))
            parts.append(str(getattr(status, "errmsg", "")))
        for attr in ("code", "status_code", "request_id", "type"):
            value = getattr(current, attr, None)
            if value is not None:
                parts.append(str(value))
        for attr in ("params", "details"):
            value = getattr(current, attr, None)
            if value:
                parts.append(str(value))
        current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
    return " ".join(part for part in parts if part).strip()


def _extract_status_code(error: BaseException) -> int | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("status_code", "status"):
            value = getattr(current, attr, None)
            code = _coerce_status_code(value)
            if code is not None:
                return code
        response = getattr(current, "response", None)
        if response is not None:
            code = _coerce_status_code(getattr(response, "status_code", None))
            if code is not None:
                return code
        current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
    return None


def _coerce_status_code(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
