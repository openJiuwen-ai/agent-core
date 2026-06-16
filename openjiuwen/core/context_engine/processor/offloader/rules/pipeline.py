from __future__ import annotations

import time
from typing import Callable

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.processor.offloader.rules.router import RuleContentRouter
from openjiuwen.core.context_engine.processor.offloader.rules.types import RuleContext
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


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
