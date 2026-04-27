# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


class MicroCompactProcessorConfig(BaseModel):
    """Clear stale tool results while keeping recent ones per tool."""

    trigger_threshold: int = Field(default=5, gt=0)
    compactable_tool_names: list[str] = Field(default=["grep", "glob", "read_file", "web_search", "web_fetch"])
    keep_recent_per_tool: int = Field(default=15, ge=0)
    cleared_marker: str = Field(default="[Old tool result content cleared]")


@ContextEngine.register_processor()
class MicroCompactProcessor(ContextProcessor):
    @property
    def config(self) -> MicroCompactProcessorConfig:
        return self._config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        all_messages = context.get_messages() + messages_to_add
        if not self._api_round(all_messages):
            return False
        return self._has_any_tool_exceed_threshold(all_messages, context)

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + messages_to_add
        write_context_trace(
            "context.processor.micro_compact.before",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "message_count_before": len(all_messages),
                "trigger_threshold": self.config.trigger_threshold,
                "keep_recent_per_tool": self.config.keep_recent_per_tool,
            },
        )
        indices_to_clear = self._collect_flat_indices_for_compact(all_messages, context)

        if not indices_to_clear:
            return None, messages_to_add

        modified_indices: List[int] = []
        for index in indices_to_clear:
            message = all_messages[index]
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            all_messages[index] = message.model_copy(update={"content": self.config.cleared_marker})
            modified_indices.append(index)

        context.set_messages(all_messages)
        write_context_trace(
            "context.processor.micro_compact.after",
            {
                "processor": self.processor_type(),
                "context_id": context.context_id(),
                "session_id": context.session_id(),
                "modified_indices": modified_indices,
                "message_count_after": len(all_messages),
            },
        )
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified_indices), []

    def _collect_compactable_indices_by_tool(
        self,
        messages: List[BaseMessage],
        context=None,
    ) -> dict[str, List[int]]:
        from openjiuwen.core.context_engine.processor._protected import (
            is_protected,
            msg_in_window,
            resolve_active_window_message_ids,
        )
        in_window_ids = resolve_active_window_message_ids(context, messages) if context else set()
        allowed_names = set(self.config.compactable_tool_names)
        result: dict[str, List[int]] = defaultdict(list)

        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            if is_protected(message, in_active_window=msg_in_window(message, in_window_ids)):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(message, messages)
            if tool_name in allowed_names:
                result[tool_name].append(index)

        return dict(result)

    def _has_any_tool_exceed_threshold(
        self,
        messages: List[BaseMessage],
        context=None,
    ) -> bool:
        grouped_indices = self._collect_compactable_indices_by_tool(messages, context)
        return any(
            len(indices) > self._config.trigger_threshold + self._config.keep_recent_per_tool
            for indices in grouped_indices.values()
        )

    def _collect_flat_indices_for_compact(
        self,
        messages: List[BaseMessage],
        context=None,
    ) -> List[int]:
        grouped = self._collect_compactable_indices_by_tool(messages, context)
        keep = self._config.keep_recent_per_tool
        result = []
        for indices in grouped.values():
            if len(indices) > self._config.trigger_threshold + keep:
                result.extend(indices[:-keep] if keep else indices)
        return result

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
