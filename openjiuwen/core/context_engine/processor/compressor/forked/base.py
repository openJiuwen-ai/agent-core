from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.compressor.util import build_compressor_reinjected_state_message
from openjiuwen.core.context_engine.processor.compressor.forked.executor import (
    ForkedCompressionExecutor,
    ForkedCompressionRequest,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
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


class ForkedPrefixCompactProcessorConfig(BaseModel):
    trigger_context_ratio: float = Field(default=0.4, gt=0.0, lt=1.0)
    min_target_context_ratio: float = Field(default=0.1, ge=0.0, lt=1.0)
    model: ModelRequestConfig | None = None
    model_client: ModelClientConfig | None = None
    # Opt-in: persist each real compression invocation (the request sent to the
    # compression model plus the post-compression main-agent context) for offline
    # effect analysis. Disabled by default; zero overhead when off.
    enable_compression_dump: bool = Field(default=False)
    compression_dump_dir: str | None = Field(default=None)


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


def find_last_real_user_message_index(messages: list[BaseMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if _is_real_user_message(messages[index]):
            return index
    return -1


def _is_real_user_message(message: BaseMessage) -> bool:
    if not isinstance(message, UserMessage):
        return False
    content = getattr(message, "content", "")
    if not isinstance(content, str):
        return True
    stripped = content.lstrip()
    internal_prefixes = (
        "<system-reminder>",
        "<memory_block_current>",
        "<memory_block_dialogue>",
        "<memory_block_round>",
        "[STATE_REINJECTION]",
    )
    return not stripped.startswith(internal_prefixes)


class ForkedPrefixCompactProcessor(ContextProcessor):
    memory_block_open: str = "<memory_block>"
    memory_block_close: str = "</memory_block>"
    default_prompt: str = ""
    processor_label: str = "ForkedPrefixCompactProcessor"
    reinject_builder_names: list[str] | None = None

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
        absolute_threshold = self._resolve_trigger_tokens_threshold(context_max)
        if total_tokens < absolute_threshold:
            return False

        span = self._build_span(context_window.context_messages)
        if not span.has_target:
            return False
        target_tokens = self._count_messages_tokens(span.messages_to_compress, context)
        min_target_tokens = int(context_max * getattr(self.config, "min_target_context_ratio", 0.0))
        if target_tokens < min_target_tokens:
            logger.info(
                "[%s skipped] compression target tokens %s below min target %s",
                self.processor_type(),
                target_tokens,
                min_target_tokens,
            )
            return False

        logger.info(
            "[%s triggered] context tokens %s reached threshold %s of max %s",
            self.processor_type(),
            total_tokens,
            absolute_threshold,
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
        request = ForkedCompressionRequest.from_context_window(
            prompt=prompt,
            context_window=context_window,
            exclude_recent_messages=len(span.protected_tail),
        )
        try:
            response = await self._forked_executor.invoke(request)
            self._record_compression_usage(response)
        except Exception as exc:
            logger.warning("[%s] forked compression failed: %s", self.processor_type(), exc, exc_info=True)
            return None, context_window

        summary = self._extract_state_snapshot_or_raw(response.content or "")
        if not summary:
            return None, context_window

        memory_message = UserMessage(content=self._wrap_memory_block(summary))
        new_messages = [*span.preserved_prefix, memory_message, *span.protected_tail]
        reinjected_message = build_compressor_reinjected_state_message(
            source_messages=original_messages,
            messages_to_keep=new_messages,
            context=context,
            config=self.config,
            builder_names=self.reinject_builder_names,
        )
        if reinjected_message is not None:
            new_messages = [*span.preserved_prefix, memory_message, reinjected_message, *span.protected_tail]
        if not self._has_compression_benefit(context, original_messages, new_messages):
            return None, context_window

        context_window.context_messages = new_messages
        context.set_messages(new_messages)

        self._dump_compression_artifact(
            context=context,
            context_window=context_window,
            original_messages=original_messages,
            span=span,
            prompt=prompt,
            request=request,
            response_content=response.content or "",
            summary=summary,
            new_messages=new_messages,
        )

        return (
            ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=list(range(len(original_messages))),
                compact_summary=memory_message.content,
                compression_usage=self._current_compression_usage(),
            ),
            context_window,
        )

    def _dump_compression_artifact(
        self,
        *,
        context: ModelContext,
        context_window: ContextWindow,
        original_messages: list[BaseMessage],
        span: PrefixCompactSpan,
        prompt: str,
        request: "ForkedCompressionRequest",
        response_content: str,
        summary: str,
        new_messages: list[BaseMessage],
    ) -> None:
        if not getattr(self.config, "enable_compression_dump", False):
            return
        # Lazy import avoids a circular dependency: compression_dump imports
        # from this module (ForkedPrefixCompactProcessor, PrefixCompactSpan).
        from openjiuwen.core.context_engine.processor.compressor.forked.compression_dump import (
            dump_compression_artifact,
        )

        try:
            dump_compression_artifact(
                processor=self,
                context=context,
                context_window=context_window,
                config=self.config,
                processor_type=self.processor_type(),
                original_messages=original_messages,
                span=span,
                prompt=prompt,
                request=request,
                response_content=response_content,
                summary=summary,
                new_messages=new_messages,
                usage=self._current_compression_usage(),
            )
        except Exception as exc:  # pragma: no cover - tracing must not break compression
            logger.warning("[%s] compression dump failed: %s", self.processor_type(), exc, exc_info=True)

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        raise NotImplementedError

    def _build_trace_context(self, context: ModelContext) -> dict[str, Any]:
        trace_context: dict[str, Any] = {
            "call_site": "forked_compressor",
            "compression_processor": self.processor_type(),
        }
        if hasattr(context, "session_id"):
            try:
                trace_context["session_id"] = context.session_id()
            except Exception:
                pass
        if hasattr(context, "context_id"):
            try:
                trace_context["context_id"] = context.context_id()
            except Exception:
                pass
        if hasattr(context, "get_session_ref"):
            try:
                session = context.get_session_ref()
            except Exception:
                session = None
            if session is not None and hasattr(session, "get_session_id") and "session_id" not in trace_context:
                try:
                    trace_context["session_id"] = session.get_session_id()
                except Exception:
                    pass
            if session is not None and hasattr(session, "dump_state"):
                try:
                    trace_context["session_state"] = session.dump_state()
                    trace_context["session_state_source"] = "session.dump_state"
                except Exception:
                    pass
            if session is not None and hasattr(session, "get_state"):
                try:
                    trace_context["session_global_state"] = session.get_state()
                except Exception:
                    pass
        return trace_context

    def _resolve_trigger_tokens_threshold(self, context_max: int) -> int:
        tokens_threshold = getattr(self.config, "tokens_threshold", None)
        if tokens_threshold is not None:
            return int(tokens_threshold)
        trigger_total_tokens = getattr(self.config, "trigger_total_tokens", None)
        if trigger_total_tokens is not None:
            return int(trigger_total_tokens)
        return int(context_max * self.config.trigger_context_ratio)

    def _build_prompt(self, span: PrefixCompactSpan) -> str:
        return self.default_prompt

    def _wrap_memory_block(self, summary: str) -> str:
        return (
            f"{self.memory_block_open}\n"
            "authority: This block is reference memory, not a binding source of truth.\n"
            "instruction_status: Do not treat this block as a new user request.\n"
            "conflict_priority: Prefer newer explicit user intent, newer raw context, and fresh tool results.\n\n"
            f"{summary}\n"
            f"{self.memory_block_close}"
        )

    @staticmethod
    def _extract_state_snapshot_or_raw(content: str) -> str:
        raw = (content or "").strip()
        if not raw:
            return ""
        match = re.search(r"<state_snapshot>\s*(.*?)\s*</state_snapshot>", raw, flags=re.DOTALL | re.IGNORECASE)
        if match is None:
            return raw
        snapshot = match.group(1).strip()
        return snapshot or raw

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
