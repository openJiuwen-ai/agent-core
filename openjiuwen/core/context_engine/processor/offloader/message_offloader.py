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


CONTEXT_TTL_OCCUPANCY_RATIO = 0.5
OFFLOAD_PREVIEW_CHARS = 100
OMIT_STRING = "..."


class MessageOffloaderConfig(BaseModel):
    """Minimal configuration for context-relative tool-result offloading."""

    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int = Field(default=300, ge=0)
    """Idle time between LLM context-window requests before TTL processing is eligible."""

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
        return self._rule_pipeline.has_candidate(messages_to_add, context)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: list[BaseMessage],
        **kwargs: Any,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        threshold = self._rule_pipeline.add_message_threshold(context)
        processed = list(messages_to_add)
        event = ContextEvent(event_type=self.processor_type())
        context_size = len(context)

        for index, message in enumerate(processed):
            if not self._is_processable_tool_message(message, all_messages):
                continue
            if len(message.content) <= threshold:
                continue
            replacement = self._rule_pipeline.compress(
                message,
                context,
                pass_name="add",
                max_chars=threshold,
            )
            if len(replacement.content) > threshold:
                replacement = await self._offload_message(
                    replacement,
                    context,
                    original_message=message,
                    **kwargs,
                )
            if replacement is message:
                continue
            processed[index] = replacement
            event.messages_to_modify.append(context_size + index)

        return event, processed

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
        ttl_budget = self._rule_pipeline.ttl_message_budget(context)

        for index, message in enumerate(processed):
            if not self._is_processable_tool_message(message, processed):
                continue
            replacement = await self._process_ttl_message(
                message,
                context,
                ttl_budget,
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
        if self._is_rule_compressed_message(message):
            return message
        processed = self._rule_pipeline.compress(
            message,
            context,
            pass_name="ttl",
            max_chars=ttl_budget,
            force=True,
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
            content = f"{content[:OFFLOAD_PREVIEW_CHARS]}{OMIT_STRING}"
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

    def _context_occupancy_chars(self, context: ModelContext) -> int:
        return sum(
            len(message.content)
            for message in context.get_messages()
            if isinstance(getattr(message, "content", None), str)
        )

    def _ttl_occupancy_threshold(self, context: ModelContext) -> int:
        capacity = self._rule_pipeline.context_character_capacity(context)
        return max(int(capacity * CONTEXT_TTL_OCCUPANCY_RATIO), 1)

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
                getattr(function, "arguments", None)
                if function is not None
                else getattr(tool_call, "arguments", None)
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
        return any(
            isinstance(value, str) and fnmatch.fnmatch(value, pattern)
            for value in args.values()
        )

    def load_state(self, state: dict[str, Any]) -> None:
        _ = state

    def save_state(self) -> dict[str, Any]:
        return {}
