from __future__ import annotations

import time
from typing import Any, Callable

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.processor.offloader.rules.router import RuleContentRouter, RuleContext
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


class RuleCompressionPipeline:
    """Deterministic rule-compression pipeline for oversized tool messages."""

    def __init__(
        self,
        router: RuleContentRouter | None = None,
        time_func: Callable[[], float] = time.time,
    ) -> None:
        self._router = router or RuleContentRouter()
        self._time_func = time_func

    def current_time(self) -> float:
        return self._time_func()

    def has_candidate(self, messages: list[BaseMessage], context: ModelContext, config: Any) -> bool:
        threshold = self.threshold(config)
        for message in messages:
            if not isinstance(message, ToolMessage) or not isinstance(getattr(message, "content", None), str):
                continue
            if self.estimate_text_tokens(message.content, context) > threshold:
                return True
        return False

    def compress_if_needed(
        self,
        message: BaseMessage,
        context: ModelContext,
        config: Any,
        *,
        threshold_ratio: float | None = None,
        force: bool = False,
        pass_name: str = "add",
        truncate_head_tokens: int | None = None,
        truncate_tail_tokens: int | None = None,
    ) -> BaseMessage:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            return message

        now = self.current_time()
        metadata = dict(getattr(message, "metadata", None) or {})
        last_processed = float(metadata.get("rule_compressed_at") or 0)
        if not force and last_processed and getattr(config, "rule_compression_ttl_seconds", 0):
            if now - last_processed < config.rule_compression_ttl_seconds:
                return message

        threshold = self.threshold(config, threshold_ratio=threshold_ratio)
        if not force and self.estimate_text_tokens(message.content, context) <= threshold:
            return message

        result = self._router.compress(
            message.content,
            RuleContext(
                max_tokens=threshold,
                head_tokens=config.rule_truncate_head_tokens,
                tail_tokens=config.rule_truncate_tail_tokens,
            ),
        )
        content = result.content
        modified = result.modified
        if self.estimate_text_tokens(content, context) > threshold:
            content = self.truncate_head_tail_by_tokens(
                content,
                truncate_head_tokens if truncate_head_tokens is not None else config.rule_truncate_head_tokens,
                truncate_tail_tokens if truncate_tail_tokens is not None else config.rule_truncate_tail_tokens,
                context,
            )
            modified = content != message.content
        if not modified:
            return message

        metadata.update(
            {
                "rule_compressed_at": now,
                "rule_compression_type": result.content_type.value,
                "rule_compression_modified": modified,
                "rule_compression_threshold": threshold,
                "rule_compression_pass": pass_name,
            }
        )
        return message.model_copy(update={"content": content, "metadata": metadata})

    @staticmethod
    def threshold(config: Any, threshold_ratio: float | None = None) -> int:
        window_tokens = config.rule_compression_context_window_tokens or config.tokens_threshold
        ratio = config.rule_compression_ratio if threshold_ratio is None else threshold_ratio
        return max(int(window_tokens * ratio), 1)

    @staticmethod
    def estimate_text_tokens(content: str, context: ModelContext) -> int:
        token_counter = context.token_counter()
        message = ToolMessage(content=content, tool_call_id="rule-token-estimate")
        if token_counter is not None:
            try:
                return token_counter.count_messages([message])
            except Exception:
                pass
        return max(len(content) // 3, 1)

    def truncate_head_tail_by_tokens(
        self,
        content: str,
        head_tokens: int,
        tail_tokens: int,
        context: ModelContext,
    ) -> str:
        head_chars = head_tokens * 3
        tail_chars = tail_tokens * 3
        if self.estimate_text_tokens(content, context) <= head_tokens + tail_tokens:
            return content
        head = content[:head_chars]
        tail = content[-tail_chars:] if tail_chars > 0 else ""
        omitted = max(len(content) - len(head) - len(tail), 0)
        return f"{head}\n...[RULE_TRUNCATED {omitted} chars omitted]...\n{tail}"
