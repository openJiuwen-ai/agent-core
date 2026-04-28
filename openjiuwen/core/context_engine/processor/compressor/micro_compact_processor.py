# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.observability import write_context_trace
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage


class ToolCompactOverride(BaseModel):
    """Per-tool override for MicroCompactProcessor knobs.

    Any field left as ``None`` falls back to the global value on
    :class:`MicroCompactProcessorConfig`. Keeping each field optional lets
    callers diff-override only the dimension that needs to differ for a
    specific tool (e.g. tighten ``keep_recent_per_tool`` for a tool whose
    output repeats across calls), without restating the whole config.
    """

    trigger_threshold: Optional[int] = Field(default=None, ge=0)
    keep_recent_per_tool: Optional[int] = Field(default=None, ge=0)
    cleared_marker: Optional[str] = Field(default=None)


class MicroCompactProcessorConfig(BaseModel):
    """Clear stale tool results while keeping recent ones per tool.

    ``per_tool_overrides`` allows differentiated compaction policy per
    tool name — e.g. a tool whose result repeats most of the prior plan
    on every call (skill_step) can be configured with smaller
    ``trigger_threshold``/``keep_recent_per_tool`` than scan tools like
    ``grep`` that rarely repeat. Only tools that also appear in
    ``compactable_tool_names`` are subject to compaction; an override for
    a tool not in the whitelist has no effect.
    """

    trigger_threshold: int = Field(default=5, ge=0)
    compactable_tool_names: list[str] = Field(default=["grep", "glob", "read_file", "web_search", "web_fetch"])
    keep_recent_per_tool: int = Field(default=15, ge=0)
    cleared_marker: str = Field(default="[Old tool result content cleared]")
    per_tool_overrides: dict[str, ToolCompactOverride] = Field(default_factory=dict)


# Tools whose output structurally repeats most of the prior plan/result on every
# call (so the global policy lets them pile up). They are always compacted with a
# fixed (trigger=0, keep=1) policy regardless of user-supplied config — listing
# or omitting the tool in ``compactable_tool_names`` / ``per_tool_overrides``
# cannot disable this. Use ``cleared_marker=None`` so the global marker still applies.
_FORCED_TOOL_OVERRIDES: dict[str, ToolCompactOverride] = {
    "skill_step": ToolCompactOverride(trigger_threshold=0, keep_recent_per_tool=1),
}


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
                "per_tool_override_count": len(self.config.per_tool_overrides),
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
            tool_name = ContextUtils.resolve_tool_name_from_message(message, all_messages)
            _, _, marker = self._resolve_for(tool_name)
            if message.content == marker:
                continue
            all_messages[index] = message.model_copy(update={"content": marker})
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

    def _resolve_for(self, tool_name: str | None) -> Tuple[int, int, str]:
        """Return (trigger_threshold, keep_recent_per_tool, cleared_marker) for ``tool_name``.

        Lookup order: ``_FORCED_TOOL_OVERRIDES`` (non-overridable), then
        ``per_tool_overrides`` from user config (field-level, missing fields
        fall back to globals), then global defaults.
        """
        ov = _FORCED_TOOL_OVERRIDES.get(tool_name) if tool_name else None
        if ov is None:
            ov = self._config.per_tool_overrides.get(tool_name) if tool_name else None
        if ov is None:
            return (
                self._config.trigger_threshold,
                self._config.keep_recent_per_tool,
                self._config.cleared_marker,
            )
        return (
            ov.trigger_threshold if ov.trigger_threshold is not None else self._config.trigger_threshold,
            ov.keep_recent_per_tool if ov.keep_recent_per_tool is not None else self._config.keep_recent_per_tool,
            ov.cleared_marker if ov.cleared_marker is not None else self._config.cleared_marker,
        )

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
        # Forced-override tools are always compactable regardless of user config.
        allowed_names = set(self.config.compactable_tool_names) | set(_FORCED_TOOL_OVERRIDES)
        result: dict[str, List[int]] = defaultdict(list)

        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if is_protected(message, in_active_window=msg_in_window(message, in_window_ids)):
                continue
            tool_name = ContextUtils.resolve_tool_name_from_message(message, messages)
            if tool_name not in allowed_names:
                continue
            _, _, marker = self._resolve_for(tool_name)
            if message.content == marker:
                continue
            result[tool_name].append(index)

        return dict(result)

    def _has_any_tool_exceed_threshold(
        self,
        messages: List[BaseMessage],
        context=None,
    ) -> bool:
        grouped_indices = self._collect_compactable_indices_by_tool(messages, context)
        for tool_name, indices in grouped_indices.items():
            trigger, keep, _ = self._resolve_for(tool_name)
            if len(indices) > trigger + keep:
                return True
        return False

    def _collect_flat_indices_for_compact(
        self,
        messages: List[BaseMessage],
        context=None,
    ) -> List[int]:
        grouped = self._collect_compactable_indices_by_tool(messages, context)
        result: List[int] = []
        for tool_name, indices in grouped.items():
            trigger, keep, _ = self._resolve_for(tool_name)
            if len(indices) > trigger + keep:
                result.extend(indices[:-keep] if keep else indices)
        return result

    def load_state(self, state: Dict[str, Any]) -> None:
        pass

    def save_state(self) -> Dict[str, Any]:
        return {}
