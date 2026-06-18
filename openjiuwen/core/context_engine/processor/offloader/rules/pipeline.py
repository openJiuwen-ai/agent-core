from __future__ import annotations

import time
from typing import Callable

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.offloader.rules.query_terms import extract_query_terms
from openjiuwen.core.context_engine.processor.offloader.rules.router import RuleContentRouter
from openjiuwen.core.context_engine.processor.offloader.rules.types import RuleContext
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, UserMessage


CHARACTERS_PER_TOKEN = 3
ADD_MESSAGE_RATIO = 0.2
TTL_MESSAGE_RATIO = 0.1


class RuleCompressionPipeline:
    """Run deterministic compression using fixed context-relative character budgets."""

    def __init__(
        self,
        router: RuleContentRouter | None = None,
        time_func: Callable[[], float] = time.time,
    ) -> None:
        self._router = router or RuleContentRouter()
        self._time_func = time_func

    def current_time(self) -> float:
        return self._time_func()

    @staticmethod
    def context_character_capacity(context: ModelContext) -> int:
        context_tokens = context.context_window_tokens()
        return max(int(context_tokens or 0) * CHARACTERS_PER_TOKEN, 1)

    def add_message_threshold(self, context: ModelContext) -> int:
        return max(int(self.context_character_capacity(context) * ADD_MESSAGE_RATIO), 1)

    def ttl_message_budget(self, context: ModelContext) -> int:
        return max(int(self.context_character_capacity(context) * TTL_MESSAGE_RATIO), 1)

    def has_candidate(self, messages: list[BaseMessage], context: ModelContext) -> bool:
        threshold = self.add_message_threshold(context)
        return any(
            isinstance(message, ToolMessage)
            and isinstance(getattr(message, "content", None), str)
            and len(message.content) > threshold
            for message in messages
        )

    def compress(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        pass_name: str,
        max_chars: int,
        force: bool = False,
    ) -> BaseMessage:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            return message
        if not force and len(message.content) <= max_chars:
            return message

        original = message.content
        result = self._router.compress(
            original,
            RuleContext(
                max_tokens=max(max_chars // CHARACTERS_PER_TOKEN, 1),
                head_tokens=max(max_chars // (CHARACTERS_PER_TOKEN * 2), 1),
                tail_tokens=max(max_chars // (CHARACTERS_PER_TOKEN * 2), 1),
                count_tokens=lambda text: max(len(text) // CHARACTERS_PER_TOKEN, 1),
                query_terms=self._query_terms_for_message(message, context),
            ),
        )
        content = result.content
        if content == original:
            return message

        metadata = dict(getattr(message, "metadata", None) or {})
        metadata.update(
            {
                "rule_compressed_at": self.current_time(),
                "rule_compression_type": result.content_type.value,
                "rule_compression_pass": pass_name,
            }
        )
        logger.info(
            "[RuleCompression] applied tool_call_id=%s pass=%s type=%s chars=%s->%s",
            getattr(message, "tool_call_id", "unknown"),
            pass_name,
            result.content_type.value,
            len(original),
            len(content),
        )
        return message.model_copy(update={"content": content, "metadata": metadata})

    def _query_terms_for_message(self, message: BaseMessage, context: ModelContext) -> frozenset[str]:
        context_messages = context.get_messages()
        latest_user_content = ""
        for context_message in reversed(context_messages):
            if isinstance(context_message, UserMessage) and isinstance(context_message.content, str):
                latest_user_content = context_message.content
                break

        tool_name = None
        tool_arguments = None
        if isinstance(message, ToolMessage):
            tool_call = ContextUtils.resolve_tool_call_from_message(message, context_messages)
            if tool_call is not None:
                tool_name = ContextUtils.extract_tool_name(tool_call)
                tool_arguments = self._extract_tool_arguments(tool_call)

        return extract_query_terms(latest_user_content, tool_name, tool_arguments)

    @staticmethod
    def _extract_tool_arguments(tool_call: object) -> object:
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            return arguments if arguments is not None else tool_call.get("arguments")

        function = getattr(tool_call, "function", None)
        if function is not None:
            arguments = getattr(function, "arguments", None)
            if arguments is not None:
                return arguments
        return getattr(tool_call, "arguments", None)
