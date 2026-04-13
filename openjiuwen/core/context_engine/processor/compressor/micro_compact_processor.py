# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage


class MicroCompactProcessorConfig(BaseModel):
    """Clear stale tool results while keeping recent ones per tool."""

    trigger_threshold: int = Field(default=5, gt=0)
    compactable_tool_names: list[str] = Field(
        default=["grep", "glob", "read_file", "web_search", "web_fetch"]
    )
    keep_recent_per_tool: int = Field(default=5, ge=0)
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
        candidates = self._collect_compactable_indices(all_messages)
        return len(candidates) >= self.config.trigger_threshold

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        old_messages = context.get_messages()
        all_messages = old_messages + messages_to_add
        indices_to_clear = self._collect_indices_to_clear(all_messages)

        if not indices_to_clear:
            return None, messages_to_add

        modified_indices: List[int] = []
        for index in indices_to_clear:
            if index >= len(old_messages):
                continue
            message = old_messages[index]
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            old_messages[index] = message.model_copy(update={"content": self.config.cleared_marker})
            modified_indices.append(index)

        context.set_messages(old_messages)
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=modified_indices), messages_to_add

    def _collect_indices_to_clear(self, messages: List[BaseMessage]) -> List[int]:
        grouped: dict[str, List[int]] = {}
        for index in self._collect_compactable_indices(messages):
            tool_name = self._resolve_tool_name_from_message(messages[index], messages)
            if not tool_name:
                continue
            grouped.setdefault(tool_name, []).append(index)

        indices_to_clear: List[int] = []
        keep_recent = self.config.keep_recent_per_tool
        for indices in grouped.values():
            if keep_recent <= 0:
                indices_to_clear.extend(indices)
            elif len(indices) > keep_recent:
                indices_to_clear.extend(indices[:-keep_recent])
        return sorted(indices_to_clear)

    def _collect_compactable_indices(self, messages: List[BaseMessage]) -> List[int]:
        allowed_names = set(self.config.compactable_tool_names)
        result: List[int] = []
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if message.content == self.config.cleared_marker:
                continue
            tool_name = self._resolve_tool_name_from_message(message, messages)
            if tool_name in allowed_names:
                result.append(index)
        return result

    def _resolve_tool_name_from_message(
        self,
        message: BaseMessage,
        context_messages: List[BaseMessage],
    ) -> Optional[str]:
        if not isinstance(message, ToolMessage):
            return None
        tool_call_id = getattr(message, "tool_call_id", None)
        if not tool_call_id:
            return None
        for context_message in reversed(context_messages):
            if not isinstance(context_message, AssistantMessage):
                continue
            tool_calls = getattr(context_message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if ContextUtils.tool_call_matches_id(tool_call, tool_call_id):
                    return ContextUtils.extract_tool_name(tool_call)
        return None

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
