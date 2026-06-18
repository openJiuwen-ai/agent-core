# coding: utf-8

import fnmatch
import json
import os
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.offloader.rules import RuleCompressionPipeline
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage

OMIT_STRING = "..."


class MessageOffloaderConfig(BaseModel):
    """Minimal configuration for context-relative tool-result offloading."""

    model_config = ConfigDict(extra="forbid")

    add_message_threshold_ratio: float = Field(default=0.2, gt=0)
    """Context-capacity ratio above which a newly added tool message is processed."""

    ttl_seconds: int = Field(default=300, ge=0)
    """Idle time between LLM context-window requests before TTL processing is eligible."""

    ttl_context_occupancy_ratio: float = Field(
        default=0.5,
        gt=0,
    )
    """Context-capacity ratio above which TTL processing is eligible."""

    ttl_message_threshold_ratio: float = Field(
        default=0.1,
        gt=0,
    )
    """Context-capacity ratio above which one TTL tool message is processed."""

    offload_preview_head_tail_chars: int = Field(
        default=2000,
        ge=0,
    )
    """Characters kept from both head and tail in direct/offload-reuse previews."""

    enable_rule_compression: bool = True
    """Whether deterministic rule compression runs before offload fallback."""

    protected_tool_names: list[str] = Field(
        default_factory=lambda: ["reload_original_context_messages"]
    )
    """Tool names, or ``tool:argument-pattern`` entries, that must remain inline."""


@ContextEngine.register_processor()
class MessageOffloader(ContextProcessor):
    def __init__(self, config: MessageOffloaderConfig):
        super().__init__(config)
        self._rule_pipeline = RuleCompressionPipeline()

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: list[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        _ = kwargs
        threshold = self._add_message_threshold(context)
        return any(
            isinstance(message, ToolMessage)
            and isinstance(getattr(message, "content", None), str)
            and len(message.content) > threshold
            for message in messages_to_add
        )

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: list[BaseMessage],
        **kwargs: Any,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        threshold = self._add_message_threshold(context)
        processed = list(messages_to_add)
        event = ContextEvent(event_type=self.processor_type())
        context_size = len(context)

        for index, message in enumerate(messages_to_add):
            replacement = await self._process_added_message(
                message,
                context,
                all_messages,
                threshold,
                **kwargs,
            )
            if replacement is message:
                continue
            processed[index] = replacement
            event.messages_to_modify.append(context_size + index)

        return event, processed

    async def _process_added_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        context_messages: list[BaseMessage],
        threshold: int,
        **kwargs: Any,
    ) -> BaseMessage:
        if not self._is_processable_tool_message(message, context_messages):
            return message
        if len(message.content) <= threshold:
            return message

        if self._enable_rule_compression():
            compressed = self._rule_pipeline.compress(
                message,
                context,
                pass_name="add",
                max_chars=threshold,
                context_messages=context_messages,
            )
            if self._is_rule_compressed_message(compressed):
                return await self._finalize_rule_compressed_message(
                    compressed,
                    context,
                    original_message=message,
                    max_chars=threshold,
                    offload_original=True,
                    truncate_offloaded_preview=True,
                    **kwargs,
                )

        return await self._offload_message(
            message,
            context,
            original_message=message,
            **kwargs,
        )

    async def _finalize_rule_compressed_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        original_message: BaseMessage,
        max_chars: int,
        offload_original: bool,
        truncate_offloaded_preview: bool,
        **kwargs: Any,
    ) -> BaseMessage:
        if not offload_original and len(message.content) <= max_chars:
            return message

        offloaded = await self._offload_message(
            message,
            context,
            original_message=original_message,
            **kwargs,
        )
        if not truncate_offloaded_preview:
            return offloaded
        if self._should_keep_full_rule_compressed_preview(message):
            return offloaded
        if len(offloaded.content) <= max_chars:
            return offloaded
        return offloaded.model_copy(
            update={
                "content": self._truncate_preserving_offload_marker(
                    offloaded.content,
                    max_chars,
                )
            }
        )

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        _ = context_window, kwargs
        now = self._rule_pipeline.current_time()
        previous_access = context.last_context_window_access_at()
        context.set_last_context_window_access_at(now)
        if self.config.ttl_seconds <= 0 or previous_access is None:
            return False
        if now - previous_access < self.config.ttl_seconds:
            return False
        return self._context_occupancy_chars(context) >= self._ttl_occupancy_threshold(context)

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[ContextEvent, ContextWindow]:
        messages = context.get_messages()
        processed = list(messages)
        event = ContextEvent(event_type=self.processor_type())
        ttl_message_threshold = self._ttl_message_threshold(context)

        for index, message in enumerate(processed):
            if not self._is_processable_tool_message(message, processed):
                continue
            if len(message.content) <= ttl_message_threshold:
                continue
            replacement = await self._process_ttl_message(
                message,
                context,
                ttl_message_threshold,
                **kwargs,
            )
            if replacement is message:
                continue
            processed[index] = replacement
            event.messages_to_modify.append(index)

        if event.messages_to_modify:
            context.set_messages(processed)
            self._replace_window_messages(context_window, processed)
        return event, context_window

    async def _process_ttl_message(
        self,
        message: ToolMessage,
        context: ModelContext,
        ttl_budget: int,
        **kwargs: Any,
    ) -> BaseMessage:
        if not self._enable_rule_compression():
            return await self._offload_message(
                message,
                context,
                original_message=message,
                **kwargs,
            )
        if self._is_rule_compressed_message(message):
            return message
        processed = self._rule_pipeline.compress(
            message,
            context,
            pass_name="ttl",
            max_chars=ttl_budget,
            force=True,
            context_messages=context.get_messages(),
        )
        if self._is_rule_compressed_message(processed):
            return await self._finalize_rule_compressed_message(
                processed,
                context,
                original_message=message,
                max_chars=ttl_budget,
                offload_original=True,
                truncate_offloaded_preview=True,
                **kwargs,
            )
        if len(processed.content) <= ttl_budget:
            return processed
        return await self._offload_message(
            processed,
            context,
            original_message=message,
            **kwargs,
        )

    async def _offload_message(
        self,
        message: BaseMessage,
        context: ModelContext,
        *,
        original_message: BaseMessage,
        **kwargs: Any,
    ) -> BaseMessage:
        content = message.content
        if not self._is_rule_compressed_message(message):
            content = self._head_tail_preview(
                content,
                description="Content truncated and offloaded. Load the OFFLOAD marker for the full original content.",
            )
        elif self._rule_compression_requests_original_offload(message):
            content = (
                f"{content}\n"
                "[Original content offloaded. Retrieve full diff with the OFFLOAD marker below.]"
            )
        extra_fields = message.model_dump()
        extra_fields.pop("role", None)
        extra_fields.pop("content", None)
        offload_handle, offload_path = self._new_offload_handle_and_path(context)
        offloaded = await self.offload_messages(
            role=message.role,
            content=content,
            messages=[original_message],
            context=context,
            offload_handle=offload_handle,
            offload_path=offload_path,
            **extra_fields,
            **kwargs,
        )
        return offloaded or message

    def _is_processable_tool_message(
        self,
        message: BaseMessage,
        context_messages: list[BaseMessage],
    ) -> bool:
        return (
            isinstance(message, ToolMessage)
            and isinstance(message.content, str)
            and not isinstance(message, OffloadMixin)
            and not self._is_protected_tool_message(message, context_messages)
        )

    @staticmethod
    def _is_rule_compressed_message(message: BaseMessage) -> bool:
        return bool((getattr(message, "metadata", None) or {}).get("rule_compressed_at"))

    @staticmethod
    def _truncate_preserving_offload_marker(content: str, max_chars: int) -> str:
        marker_start = content.rfind("[[OFFLOAD:")
        if marker_start < 0:
            return content[:max_chars]

        marker = content[marker_start:].strip()
        body = content[:marker_start].rstrip()
        marker_separator = "\n...\n"
        body_budget = max_chars - len(marker) - len(marker_separator)
        if body_budget <= 0:
            return marker
        if len(body) <= body_budget:
            return f"{body}{marker_separator}{marker}"

        body_separator = "\n...\n"
        tail_budget = body_budget // 2
        head_budget = body_budget - len(body_separator) - tail_budget
        if head_budget <= 0:
            return f"{body[-body_budget:].lstrip()}{marker_separator}{marker}"
        return (
            f"{body[:head_budget].rstrip()}"
            f"{body_separator}"
            f"{body[-tail_budget:].lstrip()}"
            f"{marker_separator}"
            f"{marker}"
        )

    @staticmethod
    def _rule_compression_requests_original_offload(message: BaseMessage) -> bool:
        metadata = getattr(message, "metadata", None) or {}
        details = metadata.get("rule_compression_details") or {}
        return (
            metadata.get("rule_compression_type") == "GIT_DIFF"
            and bool(details.get("should_offload_original"))
            and not isinstance(message, OffloadMixin)
        )

    def _should_keep_full_rule_compressed_preview(self, message: BaseMessage) -> bool:
        return self._rule_compression_requests_original_offload(message)

    def _context_occupancy_chars(self, context: ModelContext) -> int:
        return sum(
            len(message.content)
            for message in context.get_messages()
            if isinstance(getattr(message, "content", None), str)
        )

    def _ttl_occupancy_threshold(self, context: ModelContext) -> int:
        capacity = self._rule_pipeline.context_character_capacity(context)
        return max(int(capacity * self._ttl_context_occupancy_ratio()), 1)

    def _add_message_threshold(self, context: ModelContext) -> int:
        capacity = self._rule_pipeline.context_character_capacity(context)
        return max(int(capacity * self._add_message_threshold_ratio()), 1)

    def _ttl_message_threshold(self, context: ModelContext) -> int:
        capacity = self._rule_pipeline.context_character_capacity(context)
        return max(int(capacity * self._ttl_message_threshold_ratio()), 1)

    def _head_tail_preview(self, content: str, *, description: str) -> str:
        keep_chars = self._offload_preview_head_tail_chars()
        if keep_chars <= 0:
            return f"[{description}]"
        if len(content) <= keep_chars * 2:
            return content
        return (
            f"{content[:keep_chars]}"
            f"\n{OMIT_STRING} [{description}] {OMIT_STRING}\n"
            f"{content[-keep_chars:]}"
        )

    def _enable_rule_compression(self) -> bool:
        return bool(getattr(self.config, "enable_rule_compression", True))

    def _add_message_threshold_ratio(self) -> float:
        return float(getattr(self.config, "add_message_threshold_ratio", 0.2))

    def _ttl_context_occupancy_ratio(self) -> float:
        return float(getattr(self.config, "ttl_context_occupancy_ratio", 0.5))

    def _ttl_message_threshold_ratio(self) -> float:
        return float(getattr(self.config, "ttl_message_threshold_ratio", 0.1))

    def _offload_preview_head_tail_chars(self) -> int:
        return int(getattr(self.config, "offload_preview_head_tail_chars", 2000))

    @staticmethod
    def _replace_window_messages(
        context_window: ContextWindow,
        processed_messages: list[BaseMessage],
    ) -> None:
        replacements = {
            MessageOffloader._message_id(message): message
            for message in processed_messages
            if MessageOffloader._message_id(message)
        }
        context_window.context_messages = [
            replacements.get(MessageOffloader._message_id(message), message)
            for message in context_window.context_messages
        ]

    @staticmethod
    def _message_id(message: BaseMessage) -> str | None:
        return (getattr(message, "metadata", None) or {}).get("context_message_id")

    def _new_offload_handle_and_path(self, context: ModelContext) -> tuple[str, str | None]:
        offload_handle = uuid.uuid4().hex
        workspace_dir = context.workspace_dir()
        if not workspace_dir:
            return offload_handle, None
        file_name = f"{self.processor_type()}_{offload_handle}.json"
        return (
            offload_handle,
            os.path.join(
                workspace_dir,
                "context",
                f"{context.session_id()}_context",
                "offload",
                file_name,
            ),
        )

    def _is_protected_tool_message(
        self,
        message: BaseMessage,
        context_messages: list[BaseMessage],
    ) -> bool:
        if not isinstance(message, ToolMessage):
            return False
        tool_call = ContextUtils.resolve_tool_call_from_message(message, context_messages)
        if not tool_call:
            return False
        tool_name = ContextUtils.extract_tool_name(tool_call)
        tool_args = self._extract_tool_args(tool_call)
        for protected in self.config.protected_tool_names:
            if ":" not in protected:
                if tool_name == protected:
                    return True
                continue
            protected_tool, protected_pattern = protected.split(":", 1)
            if tool_name == protected_tool and self._match_pattern(tool_args, protected_pattern):
                return True
        return False

    @staticmethod
    def _extract_tool_args(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            arguments = arguments if arguments is not None else tool_call.get("arguments")
        else:
            function = getattr(tool_call, "function", None)
            arguments = (
                getattr(function, "arguments", None) if function is not None else getattr(tool_call, "arguments", None)
            )
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _match_pattern(args: dict[str, Any], pattern: str) -> bool:
        return any(isinstance(value, str) and fnmatch.fnmatch(value, pattern) for value in args.values())

    def load_state(self, state: dict[str, Any]) -> None:
        _ = state

    def save_state(self) -> dict[str, Any]:
        return {}
