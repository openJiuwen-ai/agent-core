from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.foundation.llm import BaseMessage, SystemMessage, UserMessage


@dataclass(frozen=True)
class ForkedCompressionRequest:
    prompt: str
    context_messages: list[BaseMessage]
    system_messages: list[BaseMessage] = field(default_factory=list)
    tools: list[Any] | None = None
    exclude_recent_messages: int = 0
    output_parser: Any = None


@dataclass(frozen=True)
class ForkedCompressionResult:
    """Normalized compaction response while preserving raw model response access."""

    response: Any
    usage: Any = None
    error: Exception | None = None

    @property
    def content(self) -> str:
        return getattr(self.response, "content", "") or ""

    def __getattr__(self, item: str) -> Any:
        return getattr(self.response, item)


class ForkedCompressionExecutor:
    """Shared model invocation wrapper for compaction calls using main-agent prefix context."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self._last_response: Any = None

    @property
    def last_response(self) -> Any:
        return self._last_response

    async def invoke(self, request: ForkedCompressionRequest) -> ForkedCompressionResult:
        messages = self.build_messages(request)
        kwargs: dict[str, Any] = {"messages": messages, "tools": request.tools}
        if request.output_parser is not None:
            kwargs["output_parser"] = request.output_parser
        response = await self._model.invoke(**kwargs)
        self._last_response = response
        return ForkedCompressionResult(
            response=response,
            usage=getattr(response, "usage_metadata", None) or getattr(response, "usage", None),
        )

    @staticmethod
    def build_messages(request: ForkedCompressionRequest) -> list[BaseMessage]:
        context_messages = list(request.context_messages)
        if request.exclude_recent_messages > 0:
            context_messages = context_messages[: -request.exclude_recent_messages]
        return [
            *list(request.system_messages or []),
            *context_messages,
            UserMessage(content=request.prompt),
        ]
