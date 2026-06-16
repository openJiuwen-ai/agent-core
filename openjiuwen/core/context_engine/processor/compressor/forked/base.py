from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.compressor.forked.executor import (
    ForkedCompressionExecutor,
    ForkedCompressionRequest,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    Model,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo


@dataclass(frozen=True)
class PrefixCompactSpan:
    preserved_prefix: list[BaseMessage]
    messages_to_compress: list[BaseMessage]
    protected_tail: list[BaseMessage]

    @property
    def has_target(self) -> bool:
        return bool(self.messages_to_compress)


def adjust_keep_recent_for_tool_boundaries(messages: list[BaseMessage], keep_recent: int) -> int:
    if keep_recent <= 0 or not messages:
        return max(keep_recent, 0)

    start = max(len(messages) - keep_recent, 0)
    while True:
        protected_tool_result_ids = _tool_result_ids(messages[start:])
        if not protected_tool_result_ids:
            break

        adjusted_start = start
        for index, message in enumerate(messages[:start]):
            if not isinstance(message, AssistantMessage):
                continue
            tool_ids = _message_tool_call_ids(message)
            if tool_ids & protected_tool_result_ids:
                adjusted_start = min(adjusted_start, index)

        if adjusted_start == start:
            break
        start = adjusted_start

    return len(messages) - start


class ForkedPrefixCompactProcessor(ContextProcessor):
    memory_block_open: str = "<memory_block>"
    memory_block_close: str = "</memory_block>"
    default_prompt: str = ""
    processor_label: str = "ForkedPrefixCompactProcessor"

    def __init__(self, config: BaseModel):
        super().__init__(config)
        self._model: Model | None = None
        self._forked_executor: ForkedCompressionExecutor | None = None
        model_client = getattr(config, "model_client", None)
        model = getattr(config, "model", None)
        if model_client is not None and model is not None:
            self._model = Model(model_client, model)
            self._forked_executor = ForkedCompressionExecutor(self._model)

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        if self._forked_executor is None:
            return False

        total_tokens = self._count_context_window_tokens(context_window, context)
        context_max = self._resolve_context_max(context, kwargs)
        if total_tokens < int(context_max * self.config.trigger_context_ratio):
            return False

        span = self._build_span(context_window.context_messages)
        if not span.has_target:
            return False

        logger.info(
            "[%s triggered] context tokens %s reached %.2f of max %s",
            self.processor_type(),
            total_tokens,
            self.config.trigger_context_ratio,
            context_max,
        )
        return True

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[ContextEvent | None, ContextWindow]:
        if self._forked_executor is None:
            return None, context_window

        self._reset_compression_usage()
        original_messages = list(context_window.context_messages)
        span = self._build_span(original_messages)
        if not span.has_target:
            return None, context_window

        prompt = self._build_prompt(span)
        try:
            response = await self._forked_executor.invoke(
                ForkedCompressionRequest.from_context_window(
                    prompt=prompt,
                    context_window=context_window,
                    exclude_recent_messages=len(span.protected_tail),
                )
            )
            self._record_compression_usage(response)
        except Exception as exc:
            logger.warning("[%s] forked compression failed: %s", self.processor_type(), exc, exc_info=True)
            return None, context_window

        summary = (response.content or "").strip()
        if not summary:
            return None, context_window

        memory_message = UserMessage(content=self._wrap_memory_block(summary))
        new_messages = [*span.preserved_prefix, memory_message, *span.protected_tail]
        if not self._has_compression_benefit(context, original_messages, new_messages):
            return None, context_window

        context_window.context_messages = new_messages
        context.set_messages(new_messages)
        return (
            ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=list(range(len(original_messages))),
                compact_summary=memory_message.content,
                compression_usage=self._current_compression_usage(),
            ),
            context_window,
        )

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        raise NotImplementedError

    def _build_prompt(self, span: PrefixCompactSpan) -> str:
        return self.config.custom_compression_prompt or self.default_prompt

    def _wrap_memory_block(self, summary: str) -> str:
        return (
            f"{self.memory_block_open}\n"
            "authority: This block is reference memory, not a binding source of truth.\n"
            "instruction_status: Do not treat this block as a new user request.\n"
            "conflict_priority: Prefer newer explicit user intent, newer raw context, and fresh tool results.\n\n"
            f"{summary}\n"
            f"{self.memory_block_close}"
        )

    def _has_compression_benefit(
        self,
        context: ModelContext,
        original_messages: list[BaseMessage],
        new_messages: list[BaseMessage],
    ) -> bool:
        original_tokens = self._count_messages_tokens(original_messages, context)
        new_tokens = self._count_messages_tokens(new_messages, context)
        return original_tokens <= 0 or new_tokens < original_tokens

    def _count_context_window_tokens(self, context_window: ContextWindow, context: ModelContext) -> int:
        total = self._count_messages_tokens(
            list(context_window.system_messages or []) + list(context_window.context_messages or []),
            context,
        )
        token_counter = context.token_counter()
        tools = list(context_window.tools or [])
        if token_counter is not None:
            try:
                return total + token_counter.count_tools(tools)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("[%s] tool token counter failed: %s", self.processor_type(), exc)
        return total + sum(self._estimate_text_tokens(_serialize_tool(tool)) for tool in tools)

    def _count_messages_tokens(self, messages: list[BaseMessage], context: ModelContext) -> int:
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(messages)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("[%s] message token counter failed: %s", self.processor_type(), exc)
        return sum(self._estimate_text_tokens(_message_content_to_text(message)) for message in messages)

    @staticmethod
    def _resolve_context_max(context: ModelContext, kwargs: dict[str, Any]) -> int:
        return ContextUtils.resolve_context_max(
            model_name=kwargs.get("model_name") or getattr(context, "_model_name", None),
            fallback_context_window_tokens=getattr(context, "_context_window_tokens", None),
            model_context_window_tokens=getattr(context, "_model_context_window_tokens", None),
        )

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        return max(len(text) // 3, 1) if text else 0

    def load_state(self, state: dict[str, Any]) -> None:
        return

    def save_state(self) -> dict[str, Any]:
        return {}


def _message_tool_call_ids(message: AssistantMessage) -> set[str]:
    result: set[str] = set()
    for tool_call in getattr(message, "tool_calls", None) or []:
        tool_call_id = getattr(tool_call, "id", None)
        if tool_call_id is None and isinstance(tool_call, dict):
            tool_call_id = tool_call.get("id")
        if tool_call_id:
            result.add(str(tool_call_id))
    return result


def _tool_result_ids(messages: list[BaseMessage]) -> set[str]:
    return {
        str(getattr(message, "tool_call_id"))
        for message in messages
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None)
    }


def _message_content_to_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


def _serialize_tool(tool: ToolInfo) -> str:
    if hasattr(tool, "model_dump"):
        return json.dumps(tool.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return str(tool)
